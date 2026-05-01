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

import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

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

        job = _stage_job(ctx.work_dir, shot.id, scene_json)

        # Drive Chromium → screenshot frames.
        with sync_playwright() as p:
            browser = p.chromium.launch(args=["--no-sandbox"])
            try:
                page = browser.new_page(
                    viewport={"width": ctx.resolution[0], "height": ctx.resolution[1]}
                )
                page.goto(job.runtime_dir.joinpath("index.html").as_uri())

                # Wait for runtime + PixiJS to load.
                page.wait_for_function(
                    "() => window.anLoadScene && window.PIXI",
                    timeout=DEFAULT_RUNTIME_LOAD_TIMEOUT_MS,
                )

                scene_dict = to_dict(scene_json)
                page.evaluate("(s) => window.anLoadScene(s)", scene_dict)

                if not page.evaluate("() => window.anCanvasReady()"):
                    raise CutoutRenderError(
                        "JS runtime did not initialize PixiJS app after anLoadScene"
                    )

                total_frames = max(1, int(round(shot.duration * ctx.fps)))
                _capture_frames(page, total_frames, ctx.fps, job.frames_dir)
            finally:
                browser.close()

        # Mux to mp4.
        _ffmpeg_mux(job.frames_dir, ctx.fps, job.output_mp4)

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
            },
        )


# -----------------------------------------------------------------------------
# Internals
# -----------------------------------------------------------------------------


def _ensure_ffmpeg_available() -> None:
    if shutil.which("ffmpeg") is None:
        raise CutoutRenderError(
            "ffmpeg not found on PATH. Install with: brew install ffmpeg "
            "(macOS) or apt install ffmpeg (Linux)."
        )


def _stage_job(work_dir: Path, shot_id: str, scene_json: Any) -> _RenderJob:
    """Lay out per-shot directories + copy the runtime files."""
    base = Path(work_dir) / f"shot_{shot_id}"
    runtime_target = base / "runtime"
    frames_dir = base / "frames"
    base.mkdir(parents=True, exist_ok=True)
    frames_dir.mkdir(parents=True, exist_ok=True)
    if runtime_target.exists():
        shutil.rmtree(runtime_target)
    shutil.copytree(runtime_dir(), runtime_target)
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
