"""Project-level rendering: per-shot mp4 → final composited mp4 via ffmpeg concat.

The orchestrator picks a renderer per shot from the registry (matched on
``shot.renderer``) and renders each shot in isolation, then concatenates the
per-shot outputs into one final mp4 written to ``project.mall["output"]``.

Phase 2D ships the cutout path; later phases register Manim / Remotion / etc.
adapters and the same flow handles them.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Iterable

from an.adapters._base import RenderContext, RenderResult
from an.adapters.cutout.compile import style_pack_for
from an.adapters._base import _DEFAULT_REGISTRY
from an.base import (
    DEFAULT_FPS,
    DEFAULT_RESOLUTION,
    DEFAULT_SUPERSAMPLE,
    MP4_FASTSTART_ARGS,
)
from an.ir.schema import Shot
from an.project import Project, load


# Default cap so a 20-shot scene doesn't try to spawn 20 Chromiums; the user
# can always pass a higher number explicitly.
DEFAULT_PARALLEL_CAP: int = 4


class RenderError(RuntimeError):
    """Raised on render-pipeline failures with actionable detail."""


def _scene_has_pending_dialogue(scene) -> bool:
    """Return True if any dialogue line lacks a viseme_track or timing."""
    for shot in scene.timeline:
        for line in shot.dialogue:
            if line.viseme_track is None or line.duration is None:
                return True
    return False


def _has_any_audio_content(scene) -> bool:
    """True if any shot carries something the audio pipeline should look at.

    Narration counts even though the pipeline cannot synthesise it yet, and that
    is the point: this predicate is the ONLY thing that decides whether
    ``produce_audio_for_scene`` is called at all, so gating it on dialogue alone
    made the narration guard unreachable for exactly the scenes it names — a
    narration-only project skipped the pipeline entirely and rendered a silent
    mp4 with no diagnostic anywhere.
    """
    return any(shot.dialogue or shot.narration for shot in scene.timeline)


def render_project(
    project_dir: str | Path,
    *,
    output_name: str = "main",
    fps: int | None = None,
    resolution: tuple[int, int] | None = None,
    tts: str | object = "offline",
    lipsync: str | object = "offline",
    parallel: int | str | None = None,
    strict_assets: bool = False,
    supersample: int = DEFAULT_SUPERSAMPLE,
    pix_fmt: str | None = None,
    step_hz: float | None = None,
    language: str = "en",
) -> Path:
    """Render every shot in ``project_dir``'s scene and concatenate to one mp4.

    ``tts`` and ``lipsync`` may be provider name strings (``"offline"``,
    ``"elevenlabs"``, ``"rhubarb"``) or provider instances. Defaults are
    offline so no API keys are required. Switching providers triggers a
    re-synthesis on dialogue lines whose stamped audio_ref / viseme_ref
    no longer match the current configuration.

    ``parallel`` controls per-shot concurrency:

    - ``None`` or ``1`` (default): render shots serially.
    - ``"auto"``: ``min(n_shots, cpu_count(), DEFAULT_PARALLEL_CAP)``.
    - integer ≥ 2: cap the thread pool at that size.

    Each shot's renderer runs in its own thread (the cutout backend
    spawns a Chromium + http.server per shot, so threads release the
    GIL during the slow parts).

    ``supersample`` renders at N times the declared resolution and resolves back
    with an exact block mean. **Opt-in, and 1 is free** — at 1 nothing is
    decoded and Chromium's own bytes reach disk. See :func:`render`.

    ``step_hz`` overrides the scene's ``meta.step_hz`` for this render (a shot's
    own ``step_hz`` still wins): authored tweens are resampled onto a pose grid
    of that many updates per second — 15 at 30 fps is "on twos". ``None`` uses
    the scene's declaration, which is itself ``None`` (smooth) by default.

    ``language`` (BCP-47) reaches lip-sync providers that select behaviour by
    it when ``lipsync`` is a provider *name* — Rhubarb's recognizer (an#96). A
    provider *instance* carries its own.

    Returns the absolute path of the final output file (under ``output/``).
    """
    project: Project = load(project_dir)
    return render(
        project,
        output_name=output_name,
        fps=fps,
        resolution=resolution,
        tts=tts,
        lipsync=lipsync,
        parallel=parallel,
        strict_assets=strict_assets,
        supersample=supersample,
        pix_fmt=pix_fmt,
        step_hz=step_hz,
        language=language,
    )


def render(
    project: Project,
    *,
    output_name: str = "main",
    fps: int | None = None,
    resolution: tuple[int, int] | None = None,
    auto_audio: bool = True,
    tts: str | object = "offline",
    lipsync: str | object = "offline",
    parallel: int | str | None = None,
    strict_assets: bool = False,
    supersample: int = DEFAULT_SUPERSAMPLE,
    pix_fmt: str | None = None,
    step_hz: float | None = None,
    language: str = "en",
) -> Path:
    """Lower-level: render a loaded ``Project`` to mp4.

    ``supersample`` renders at N times the declared resolution and resolves back
    with an exact N x N block mean, in the frame stage, before anything else
    reads the frames. **Opt-in, and 1 costs nothing**: at 1 Chromium writes
    straight to disk and no pixel is decoded.

    **Measured on the shipped path, not on the render alone** — the distinction
    matters, because the two differ by 1.6x. `single_character` forced to
    1920x1080, 60 frames, this machine:

    ========  =============  ==========
    factor    ms/frame       vs k=1
    ========  =============  ==========
    1         125.5          1.00x
    2         508.6          **4.05x**
    ========  =============  ==========

    `misc/docs/wave3_research.md` §3b reports 2.54x for k=2; that is the
    **render only**, measured with a patched runtime and no Python-side resolve,
    and quoting it here would understate what a caller pays by 1.6x. The
    difference is the decode + block mean + re-encode per frame.

    **What it buys, and where it does not.** Research §3a renders each corpus
    scene at rising k and lets `edge_transition_width` converge: k=2 travels 57%
    to 112% of the way to that ceiling, and k=3 reaches it on every scene that
    has one, at twice k=2's cost. `promote_demo` — the descriptor path — is the
    scene it helps most (-34.8% edge width, because the SVG sprite rasterises AT
    2x instead of being stretched up from a 1x texture). `aa_probe` has no
    ceiling at all: its diagonals land the block-mean grid differently at every
    k, so it oscillates +/-5-8% with no settling.

    **The corpus cannot inform the factor and must not be used to.** At 320x240
    the same ladder reads 1.0x / 1.08x, because fixed costs dominate.

    When ``auto_audio`` is True (the default) and any shot has dialogue,
    the audio pipeline is run first so visemes + audio are available to
    the renderer. Re-synthesis is triggered on provider changes (the
    pipeline's idempotency check compares against the current providers'
    expected content hashes).

    ``strict_assets=True`` refuses to draw a stand-in for a declared asset the
    stores do not supply — the placeholder rig for a missing character
    descriptor, the default backdrop for an unknown environment ref. Use it for
    anything that measures pixels: a stand-in renders happily and is a
    different picture (an#33).
    """
    scene = project.scene
    if not scene.timeline:
        raise RenderError("scene has no shots to render")

    if auto_audio and _has_any_audio_content(scene):
        # Lazy import to keep render.py importable without audio extras.
        from an.audio.pipeline import produce_audio_for_scene
        from an.audio.providers import make_lipsync, make_tts

        tts_provider = make_tts(tts) if isinstance(tts, str) else tts
        lipsync_provider = (
            make_lipsync(lipsync, language=language)
            if isinstance(lipsync, str)
            else lipsync
        )

        produce_audio_for_scene(
            scene,
            project.mall,
            tts=tts_provider,
            lipsync=lipsync_provider,
        )
        # Persist the now-stamped scene back to disk so subsequent loads see it.
        project.mall["scenes"]["main"] = scene

    work_dir = project.root / ".an" / "render_work"
    work_dir.mkdir(parents=True, exist_ok=True)

    effective_fps = fps if fps is not None else scene.meta.fps or DEFAULT_FPS
    effective_res = (
        resolution
        if resolution is not None
        else (
            scene.meta.resolution.width or DEFAULT_RESOLUTION[0],
            scene.meta.resolution.height or DEFAULT_RESOLUTION[1],
        )
    )

    ctx = RenderContext(
        mall=project.mall,
        work_dir=work_dir,
        fps=effective_fps,
        resolution=effective_res,
        strict_assets=strict_assets,
        supersample=supersample,
        pix_fmt=pix_fmt,
        step_hz=step_hz if step_hz is not None else scene.meta.step_hz,
        # Resolved here, once, so a missing pack fails before the first browser
        # launch rather than per shot — and so every shot in a scene is drawn
        # under the same art direction by construction.
        style_pack=style_pack_for(scene.meta, project.mall.get("styles") or {}),
    )

    shots = list(scene.timeline)
    pool_size = _resolve_parallel(parallel, n_shots=len(shots))

    # Resolve renderers up front so a missing one fails fast (before we spawn
    # workers).
    shot_renderers = []
    for shot in shots:
        r = _DEFAULT_REGISTRY.find_for(shot)
        if r is None:
            raise RenderError(
                f"no renderer registered for shot {shot.id!r} "
                f"(renderer={shot.renderer!r}); registered: "
                f"{list(_DEFAULT_REGISTRY.names())}"
            )
        shot_renderers.append((shot, r))

    if pool_size <= 1:
        shot_results = [
            _render_one(shot, renderer, ctx, project)
            for shot, renderer in shot_renderers
        ]
    else:
        results_by_id: dict[str, RenderResult] = {}
        with ThreadPoolExecutor(max_workers=pool_size) as ex:
            futures = {
                ex.submit(_render_one, shot, renderer, ctx, project): shot.id
                for shot, renderer in shot_renderers
            }
            for fut in as_completed(futures):
                shot_id = futures[fut]
                results_by_id[shot_id] = fut.result()
        # Preserve scene-timeline order for ffmpeg concat.
        shot_results = [results_by_id[s.id] for s in shots]

    # Concatenate per-shot mp4s.
    output_path = (project.root / "output" / f"{output_name}.mp4").resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    _ffmpeg_concat([r.mp4_path for r in shot_results], output_path)

    # Also write to the output store for parity with other artifacts.
    with open(output_path, "rb") as f:
        project.mall["output"][output_name] = f.read()

    return output_path


def _render_one(
    shot: Shot,
    renderer,
    ctx: RenderContext,
    project: Project,
) -> RenderResult:
    """Render one shot and persist its mp4 into ``project.mall["shots"]``.

    Each call is self-contained: the cutout renderer creates a per-shot
    work directory, its own Chromium instance, its own http server. This
    is what makes the call thread-safe.
    """
    result = renderer.render(shot, ctx)
    with open(result.mp4_path, "rb") as f:
        project.mall["shots"][shot.id] = f.read()
    return result


def _resolve_parallel(parallel: int | str | None, *, n_shots: int) -> int:
    """Resolve ``parallel`` to an integer worker count.

    >>> _resolve_parallel(None, n_shots=5)
    1
    >>> _resolve_parallel(1, n_shots=5)
    1
    >>> _resolve_parallel(3, n_shots=5)
    3
    >>> _resolve_parallel(8, n_shots=2)  # capped to n_shots
    2
    """
    if parallel is None or parallel == 1 or parallel == 0 or parallel == "":
        return 1
    if parallel == "auto":
        cap = DEFAULT_PARALLEL_CAP
        cpu = os.cpu_count() or 1
        return max(1, min(n_shots, cpu, cap))
    try:
        n = int(parallel)
    except (TypeError, ValueError):
        return 1
    return max(1, min(n, n_shots))


# -----------------------------------------------------------------------------
# ffmpeg concat
# -----------------------------------------------------------------------------


def _ffmpeg_concat(inputs: Iterable[Path], output: Path) -> None:
    """Concatenate mp4 files using ffmpeg's concat demuxer."""
    if shutil.which("ffmpeg") is None:
        raise RenderError(
            "ffmpeg not found on PATH. Install with: brew install ffmpeg "
            "(macOS) or apt install ffmpeg (Linux)."
        )
    inputs = list(inputs)
    if len(inputs) == 1:
        # Single shot: just copy. This branch can therefore fix nothing about
        # the container -- it is sound only because the per-shot mp4 is
        # already faststart (`_ffmpeg_add_audio`), which is why the flag has
        # to be on the shot mux and not only on the concat below. Five of the
        # six bench corpus scenes take this path (only `multi_shot` has two
        # shots), so a concat-only fix would leave the corpus untouched.
        shutil.copy(inputs[0], output)
        return

    # Build a concat list file: ffmpeg's concat demuxer wants a `file '<path>'\n` list.
    list_path = output.with_suffix(".concat.txt")
    list_path.write_text(
        "\n".join(f"file '{p.resolve()}'" for p in inputs) + "\n",
        encoding="utf-8",
    )
    cmd = [
        "ffmpeg",
        "-y",
        "-loglevel",
        "error",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(list_path),
        "-c",
        "copy",
        # an#57's open question, answered by experiment on ffmpeg 8.1
        # (Homebrew, macOS arm64): `-f concat -c copy -movflags +faststart`
        # is a REMUX, not a transcode. The concatenated elementary stream is
        # sha256-identical to the two inputs' streams appended
        # (a4be46f7...218e == cat a.h264 b.h264), the video packet total is
        # unchanged, the decoded YUV is sha256-identical, the file size is
        # unchanged (moov is the same 1062 bytes, it just moves), and the
        # wall time is the same 0.02 s. So it does not create the double
        # encode epic #9 wrongly describes.
        *MP4_FASTSTART_ARGS,
        str(output),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if result.returncode != 0 or not output.exists():
        raise RenderError(
            f"ffmpeg concat failed (rc={result.returncode}):\n{result.stderr}"
        )
    list_path.unlink(missing_ok=True)
