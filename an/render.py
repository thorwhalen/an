"""Project-level rendering: per-shot mp4 → final composited mp4 via ffmpeg concat.

The orchestrator picks a renderer per shot from the registry (matched on
``shot.style``) and renders each shot in isolation, then concatenates the
per-shot outputs into one final mp4 written to ``project.mall["output"]``.

Phase 2D ships the cutout path; later phases register Manim / Remotion / etc.
adapters and the same flow handles them.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Iterable

from an.adapters._base import RenderContext, RenderResult
from an.adapters._base import _DEFAULT_REGISTRY
from an.base import DEFAULT_FPS, DEFAULT_RESOLUTION
from an.ir.schema import Shot
from an.project import Project, load


class RenderError(RuntimeError):
    """Raised on render-pipeline failures with actionable detail."""


def render_project(
    project_dir: str | Path,
    *,
    output_name: str = "main",
    fps: int | None = None,
    resolution: tuple[int, int] | None = None,
) -> Path:
    """Render every shot in ``project_dir``'s scene and concatenate to one mp4.

    Returns the absolute path of the final output file (under ``output/``).
    """
    project: Project = load(project_dir)
    return render(
        project,
        output_name=output_name,
        fps=fps,
        resolution=resolution,
    )


def render(
    project: Project,
    *,
    output_name: str = "main",
    fps: int | None = None,
    resolution: tuple[int, int] | None = None,
) -> Path:
    """Lower-level: render a loaded ``Project`` to mp4."""
    scene = project.scene
    if not scene.timeline:
        raise RenderError("scene has no shots to render")

    work_dir = project.root / ".an" / "render_work"
    work_dir.mkdir(parents=True, exist_ok=True)

    effective_fps = fps if fps is not None else scene.meta.fps or DEFAULT_FPS
    effective_res = resolution if resolution is not None else (
        scene.meta.resolution.width or DEFAULT_RESOLUTION[0],
        scene.meta.resolution.height or DEFAULT_RESOLUTION[1],
    )

    ctx = RenderContext(
        mall=project.mall,
        work_dir=work_dir,
        fps=effective_fps,
        resolution=effective_res,
    )

    shot_results: list[RenderResult] = []
    for shot in scene.timeline:
        renderer = _DEFAULT_REGISTRY.find_for(shot)
        if renderer is None:
            raise RenderError(
                f"no renderer registered for shot {shot.id!r} (style={shot.style!r}); "
                f"registered: {list(_DEFAULT_REGISTRY.names())}"
            )
        result = renderer.render(shot, ctx)
        # Persist per-shot mp4 in the artifact store.
        with open(result.mp4_path, "rb") as f:
            project.mall["shots"][shot.id] = f.read()
        shot_results.append(result)

    # Concatenate per-shot mp4s.
    output_path = (project.root / "output" / f"{output_name}.mp4").resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    _ffmpeg_concat([r.mp4_path for r in shot_results], output_path)

    # Also write to the output store for parity with other artifacts.
    with open(output_path, "rb") as f:
        project.mall["output"][output_name] = f.read()

    return output_path


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
