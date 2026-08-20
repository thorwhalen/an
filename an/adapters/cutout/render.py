"""Headless cutout rendering: Playwright drives the JS runtime, ffmpeg muxes.

The flow per shot:

1. Compile the shot to a `CutoutSceneJSON` via `compile_shot`.
2. Stage a copy of the JS runtime in a per-shot work directory and write the
   JSON beside it.
3. Launch headless Chromium via Playwright; load `index.html`; inject the
   scene via ``window.anLoadScene``.
4. For each frame ``f`` in ``[0, total_frames)``: call ``window.anSetTime(f/fps)``
   and screenshot the canvas to a PNG.
5. Mux the PNG sequence to mp4 with ffmpeg.

Failures are reported with concrete remediation: missing ffmpeg, missing
Chromium, runtime load timeout, etc. Subprocess errors are wrapped at the
facade boundary.
"""

from __future__ import annotations

import http.server
import json
import shutil
import socketserver
import subprocess
import threading
import warnings
from collections.abc import Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

from an.adapters._base import RenderContext, RenderResult
from an.adapters.cutout.compile import compile_shot
from an.adapters.cutout.runtime_files import runtime_dir
from an.adapters.cutout.serialize import to_dict
from an.ir.schema import Shot


# Tunables — exposed as module constants per the no-magic-numbers rule.
DEFAULT_RUNTIME_LOAD_TIMEOUT_MS: int = 15_000
DEFAULT_FRAME_PNG_PATTERN: str = "frame_%06d.png"


class CutoutRenderError(RuntimeError):
    """Raised when a cutout render fails. Carries actionable detail."""


@dataclass(slots=True)
class _RenderJob:
    """Per-shot scratch area + the scene that's about to render."""

    work_dir: Path
    runtime_dir: Path
    json_path: Path
    frames_dir: Path
    output_mp4: Path


class CutoutRenderer:
    """Headless cutout renderer: Playwright + ffmpeg.

    >>> r = CutoutRenderer()
    >>> r.name
    'cutout'
    >>> r.supported_styles
    ('cutout',)
    """

    name: str = "cutout"
    supported_styles: tuple[str, ...] = ("cutout",)

    def can_render(self, shot: Shot) -> bool:
        return shot.style == "cutout"

    def render(self, shot: Shot, ctx: RenderContext) -> RenderResult:
        """Render ``shot`` to mp4 using ``ctx`` for paths + parameters."""
        _ensure_ffmpeg_available()
        from playwright.sync_api import sync_playwright  # local: optional dep

        scene_json = compile_shot(
            shot,
            mall=ctx.mall,
            fps=ctx.fps,
            width=ctx.resolution[0],
            height=ctx.resolution[1],
        )

        job = _stage_job(ctx.work_dir, shot.id, scene_json, mall=ctx.mall)

        # Drive Chromium → screenshot frames.
        # Phase 11b: serve runtime via local HTTP because PIXI.Assets.fetch()
        # can't load file:// URLs in headless Chromium. Same effect as a
        # static deployment, isolated to this render.
        with _serve_dir(job.runtime_dir) as base_url, sync_playwright() as p:
            browser = p.chromium.launch(args=["--no-sandbox"])
            try:
                page = browser.new_page(
                    viewport={"width": ctx.resolution[0], "height": ctx.resolution[1]}
                )
                page.goto(f"{base_url}/index.html")

                # Wait for runtime + PixiJS to load.
                page.wait_for_function(
                    "() => window.anLoadScene && window.PIXI",
                    timeout=DEFAULT_RUNTIME_LOAD_TIMEOUT_MS,
                )

                scene_dict = to_dict(scene_json)
                # anLoadScene is async (Phase 11b: it awaits Assets.load).
                # Playwright awaits returned Promises automatically.
                page.evaluate(
                    "async (s) => { await window.anLoadScene(s); }", scene_dict
                )

                if not page.evaluate("() => window.anCanvasReady()"):
                    raise CutoutRenderError(
                        "JS runtime did not initialize PixiJS app after anLoadScene"
                    )

                total_frames = max(1, int(round(shot.duration * ctx.fps)))
                _capture_frames(page, total_frames, ctx.fps, job.frames_dir)
            finally:
                browser.close()

        # Mux frames → silent video, then layer (silence base + dialogue) audio on top.
        # Every shot mp4 carries an AAC stream (silent if no dialogue) so the
        # final ffmpeg concat across heterogeneous shots works without surprises.
        silent_mp4 = job.work_dir / "silent.mp4"
        _ffmpeg_mux(job.frames_dir, ctx.fps, silent_mp4)
        audio_inputs = _stage_audio_inputs(shot, ctx, job.work_dir)
        _ffmpeg_add_audio(silent_mp4, audio_inputs, job.output_mp4, shot.duration)

        return RenderResult(
            mp4_path=job.output_mp4,
            duration=shot.duration,
            frame_manifest=sorted(job.frames_dir.glob("*.png")),
            log="",
            provenance={
                "shot_id": shot.id,
                "fps": ctx.fps,
                "resolution": ctx.resolution,
                "frame_count": total_frames,
                "audio_tracks": len(audio_inputs),
            },
        )


# -----------------------------------------------------------------------------
# Internals
# -----------------------------------------------------------------------------


@contextmanager
def _serve_dir(directory: Path) -> Iterator[str]:
    """Run a tiny HTTP server on a free port serving ``directory``.

    Yields the base URL (``http://127.0.0.1:<port>``). Tears the server
    down on context exit. Used by the cutout renderer so PIXI.Assets can
    fetch SVG textures (file:// URLs don't work in headless Chromium).
    """

    class _Handler(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *a: Any, **kw: Any) -> None:
            super().__init__(*a, directory=str(directory), **kw)

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002, ARG002
            return  # silence access logs

    class _Server(socketserver.ThreadingTCPServer):
        allow_reuse_address = True
        daemon_threads = True

    server = _Server(("127.0.0.1", 0), _Handler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.shutdown()
        server.server_close()


def _ensure_ffmpeg_available() -> None:
    if shutil.which("ffmpeg") is None:
        raise CutoutRenderError(
            "ffmpeg not found on PATH. Install with: brew install ffmpeg "
            "(macOS) or apt install ffmpeg (Linux)."
        )


def _stage_job(
    work_dir: Path,
    shot_id: str,
    scene_json: Any,
    *,
    mall: Mapping[str, Any] | None = None,
) -> _RenderJob:
    """Lay out per-shot directories + copy the runtime files + SVG assets."""
    base = Path(work_dir) / f"shot_{shot_id}"
    runtime_target = base / "runtime"
    frames_dir = base / "frames"
    base.mkdir(parents=True, exist_ok=True)
    frames_dir.mkdir(parents=True, exist_ok=True)
    if runtime_target.exists():
        shutil.rmtree(runtime_target)
    shutil.copytree(runtime_dir(), runtime_target)

    # Phase 11b: stage SVG character textures into the runtime dir at the
    # paths declared in scene.assets.textures, so Pixi can load them by
    # relative URL from index.html.
    if mall is not None:
        _stage_scene_assets(scene_json, mall, runtime_target)

    json_path = runtime_target / "scene.json"
    json_path.write_text(
        json.dumps(to_dict(scene_json), sort_keys=True), encoding="utf-8"
    )
    return _RenderJob(
        work_dir=base,
        runtime_dir=runtime_target,
        json_path=json_path,
        frames_dir=frames_dir,
        output_mp4=base / f"{shot_id}.mp4",
    )


class CutoutAssetWarning(UserWarning):
    """A declared texture could not be staged into the runtime directory.

    Deliberately a warning and not an error, for now: an art package that is
    still being assembled is a real state, and refusing to render it would be
    worse than rendering it incompletely. But it must be *audible* — the
    renderer's fallback for a missing texture is a plain white rectangle, which
    is indistinguishable from art, so a silent skip surfaces to the user as
    "the animation looks wrong" rather than as an error.
    """


#: Texture ``src`` prefix → the mall store that resolves the rest of the path.
#:
#: A ``src`` reads ``<prefix>/<ref>/parts/head.svg`` and resolves to
#: ``mall[store]._root/<ref>/parts/head.svg``. Only ``characters/`` is emitted
#: by the compiler today; the other two are here because environments, styles
#: and props all route through this same staging step as they land, and the
#: previous hardcoded ``characters/`` test silently dropped everything else.
ASSET_SRC_PREFIX_TO_STORE: dict[str, str] = {
    "characters/": "characters",
    "environments/": "environments",
    "styles/": "styles",
}


def _stage_scene_assets(
    scene_json: Any,
    mall: Mapping[str, Any],
    runtime_target: Path,
) -> None:
    """Copy every ``assets.textures`` entry from its mall store into the runtime dir.

    Each texture's ``src`` is resolved through
    :data:`ASSET_SRC_PREFIX_TO_STORE`. Anything that cannot be resolved — an
    unknown prefix, an absent store, an in-memory store, or a file that is not
    on disk — emits a :class:`CutoutAssetWarning` naming the alias, the declared
    ``src`` and where it was looked for, rather than being skipped in silence.
    """
    textures = getattr(scene_json.assets, "textures", {}) if scene_json.assets else {}
    for alias, asset in textures.items():
        src_rel = getattr(asset, "src", None) or (
            asset.get("src") if isinstance(asset, dict) else None
        )
        if not src_rel:
            warnings.warn(
                f"texture {alias!r} declares no src; nothing to stage. "
                "The runtime will draw a white rectangle in its place.",
                CutoutAssetWarning,
                stacklevel=2,
            )
            continue

        prefix = next(
            (p for p in ASSET_SRC_PREFIX_TO_STORE if src_rel.startswith(p)), None
        )
        if prefix is None:
            warnings.warn(
                f"texture {alias!r} has src {src_rel!r}, whose prefix is not one of "
                f"{sorted(ASSET_SRC_PREFIX_TO_STORE)}. It cannot be resolved to a "
                "store and will render as a white rectangle.",
                CutoutAssetWarning,
                stacklevel=2,
            )
            continue

        store_name = ASSET_SRC_PREFIX_TO_STORE[prefix]
        store = mall.get(store_name)
        root = getattr(store, "_root", None) if store is not None else None
        if root is None:
            # An in-memory store is legitimate (tests do it) and has nothing on
            # disk to copy — but a scene that *declared* the texture still will
            # not get it, so say so.
            warnings.warn(
                f"texture {alias!r} resolves to the {store_name!r} store, which has "
                "no filesystem root (absent or in-memory); it cannot be staged.",
                CutoutAssetWarning,
                stacklevel=2,
            )
            continue

        source = Path(root) / src_rel[len(prefix) :]
        if not source.exists():
            warnings.warn(
                f"texture {alias!r} declared as {src_rel!r} was not found at "
                f"{source}. The runtime will draw a white rectangle in its place.",
                CutoutAssetWarning,
                stacklevel=2,
            )
            continue

        target = runtime_target / src_rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)


def _capture_frames(page: Any, total_frames: int, fps: int, frames_dir: Path) -> None:
    """Step the JS runtime through ``total_frames`` and screenshot the canvas each time."""
    for i in range(total_frames):
        t = i / float(fps)
        page.evaluate("(t) => window.anSetTime(t)", t)
        # Screenshot only the canvas element (no surrounding chrome).
        canvas = page.locator("#stage")
        out_path = frames_dir / (DEFAULT_FRAME_PNG_PATTERN % i)
        canvas.screenshot(path=str(out_path), omit_background=False)


def _ffmpeg_mux(frames_dir: Path, fps: int, output_mp4: Path) -> None:
    """Mux a PNG sequence to H.264 mp4."""
    pattern = str(frames_dir / DEFAULT_FRAME_PNG_PATTERN)
    cmd = [
        "ffmpeg",
        "-y",
        "-loglevel",
        "error",
        "-framerate",
        str(fps),
        "-i",
        pattern,
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-movflags",
        "+faststart",
        str(output_mp4),
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    except OSError as e:
        raise CutoutRenderError(f"ffmpeg failed to launch: {e}") from e
    if result.returncode != 0 or not output_mp4.exists():
        raise CutoutRenderError(
            "ffmpeg mux failed (rc=%d):\n%s" % (result.returncode, result.stderr)
        )


def _stage_audio_inputs(
    shot: Shot, ctx: RenderContext, work_dir: Path
) -> list[tuple[Path, float]]:
    """Write the shot's per-dialogue audio bytes to disk and return (path, delay)s.

    Looks up each ``dialogue.audio_ref`` in ``mall["audio"]``. Lines without
    an audio_ref or duration are skipped silently. Returns ``[]`` when no
    audio is available, so the caller can use a video-only path.
    """
    audio_store = ctx.mall.get("audio") if ctx.mall else None
    if not audio_store:
        return []
    out: list[tuple[Path, float]] = []
    for i, line in enumerate(shot.dialogue):
        if not line.audio_ref or line.start is None:
            continue
        try:
            audio_bytes = audio_store[line.audio_ref]
        except KeyError:
            continue
        # Sniff format: WAV starts with 'RIFF', mp3 with 'ID3' or 0xFFFB.
        if audio_bytes[:4] == b"RIFF":
            ext = "wav"
        elif audio_bytes[:3] == b"ID3" or audio_bytes[:1] == b"\xff":
            ext = "mp3"
        else:
            ext = "wav"
        path = work_dir / f"audio_{i}_{line.audio_ref[:8]}.{ext}"
        path.write_bytes(audio_bytes)
        out.append((path, float(line.start)))
    return out


def _ffmpeg_add_audio(
    video_path: Path,
    audio_inputs: list[tuple[Path, float]],
    output_path: Path,
    duration_s: float,
) -> None:
    """Mux a silence base + ``audio_inputs`` (path, delay_s) onto ``video_path``.

    Always emits an audio stream. ``anullsrc`` provides the silent base track
    of length ``duration_s`` so concat across shots is safe; dialogue lines
    are overlaid via ``adelay`` + ``amix``.
    """
    sr = 44100
    cmd = [
        "ffmpeg",
        "-y",
        "-loglevel",
        "error",
        "-i",
        str(video_path),
        # Silent base (input #1)
        "-f",
        "lavfi",
        "-t",
        f"{duration_s:.3f}",
        "-i",
        f"anullsrc=channel_layout=mono:sample_rate={sr}",
    ]
    for audio_path, _delay in audio_inputs:
        cmd += ["-i", str(audio_path)]

    filter_parts: list[str] = []
    # Resample silence base to ensure consistent format with dialogue inputs.
    filter_parts.append(
        f"[1:a]aformat=sample_fmts=fltp:sample_rates={sr}:channel_layouts=mono[base]"
    )
    overlays: list[str] = []
    for i, (_path, delay_s) in enumerate(audio_inputs):
        delay_ms = max(0, int(round(delay_s * 1000)))
        # Index in command: 0=video, 1=anullsrc, 2..=user audio
        cmd_idx = i + 2
        label = f"a{i}"
        filter_parts.append(
            f"[{cmd_idx}:a]aformat=sample_fmts=fltp:sample_rates={sr}:channel_layouts=mono,"
            f"adelay={delay_ms}|{delay_ms}[{label}]"
        )
        overlays.append(f"[{label}]")

    # Mix [base] + all overlays (always at least 1 input for amix).
    inputs_count = 1 + len(overlays)
    mix_in = "[base]" + "".join(overlays)
    filter_parts.append(
        f"{mix_in}amix=inputs={inputs_count}:dropout_transition=0:normalize=0[aout]"
    )

    cmd += [
        "-filter_complex",
        ";".join(filter_parts),
        "-map",
        "0:v",
        "-map",
        "[aout]",
        "-c:v",
        "copy",
        "-c:a",
        "aac",
        "-b:a",
        "128k",
        "-ar",
        str(sr),
        "-ac",
        "1",
        "-t",
        f"{duration_s:.3f}",
        str(output_path),
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    except OSError as e:
        raise CutoutRenderError(f"ffmpeg audio mux failed to launch: {e}") from e
    if result.returncode != 0 or not output_path.exists():
        raise CutoutRenderError(
            "ffmpeg audio mux failed (rc=%d):\n%s" % (result.returncode, result.stderr)
        )
