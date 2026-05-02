"""Project-level rendering: per-shot mp4 → final composited mp4 via ffmpeg concat.

The orchestrator picks a renderer per shot from the registry (matched on
``shot.style``) and renders each shot in isolation, then concatenates the
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
from an.adapters._base import _DEFAULT_REGISTRY
from an.base import DEFAULT_FPS, DEFAULT_RESOLUTION
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


def _has_any_dialogue(scene) -> bool:
    """Return True if there's at least one dialogue line in the scene."""
    return any(shot.dialogue for shot in scene.timeline)


def render_project(
    project_dir: str | Path,
    *,
    output_name: str = "main",
    fps: int | None = None,
    resolution: tuple[int, int] | None = None,
    tts: str | object = "offline",
    lipsync: str | object = "offline",
    parallel: int | str | None = None,
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
) -> Path:
    """Lower-level: render a loaded ``Project`` to mp4.

    When ``auto_audio`` is True (the default) and any shot has dialogue,
    the audio pipeline is run first so visemes + audio are available to
    the renderer. Re-synthesis is triggered on provider changes (the
    pipeline's idempotency check compares against the current providers'
    expected content hashes).
    """
    scene = project.scene
    if not scene.timeline:
        raise RenderError("scene has no shots to render")

    if auto_audio and _has_any_dialogue(scene):
        # Lazy import to keep render.py importable without audio extras.
        from an.audio.pipeline import produce_audio_for_scene
        from an.audio.providers import make_lipsync, make_tts

        tts_provider = make_tts(tts) if isinstance(tts, str) else tts
        lipsync_provider = (
            make_lipsync(lipsync) if isinstance(lipsync, str) else lipsync
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
                f"(style={shot.style!r}); registered: "
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
        # Single shot: just copy.
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
        str(output),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if result.returncode != 0 or not output.exists():
        raise RenderError(
            f"ffmpeg concat failed (rc={result.returncode}):\n{result.stderr}"
        )
    list_path.unlink(missing_ok=True)
