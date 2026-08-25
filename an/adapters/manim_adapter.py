"""ManimRenderer — generate a minimal Manim scene + invoke `manim` as subprocess.

Phase 6 ships the wiring + a placeholder scene. Real shot-to-Manim translation
is a Phase 7+ effort: it needs careful mapping from anima's renderer-agnostic
IR onto Manim's mobject grammar (Text / VGroup / Animation / etc).

For now: every cutout-style shot rendered through this adapter produces a
minimal "title card" Manim scene of the right duration. Useful as a pipeline
sanity check; not a real animation.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from textwrap import dedent

from an.adapters._base import RenderContext, RenderResult
from an.ir.schema import Shot


_DEFAULT_QUALITY: str = "low_quality"  # -ql; matches `manim` shorthand


class ManimRenderError(RuntimeError):
    """Raised when a Manim render fails. Carries actionable detail."""


class ManimRenderer:
    """Manim Community Edition renderer (skeleton).

    Implements the ``Renderer`` Protocol. ``can_render`` is True for shots
    whose ``style`` is ``"manim"``.
    """

    name: str = "manim"
    supported_styles: tuple[str, ...] = ("manim",)

    def can_render(self, shot: Shot) -> bool:
        return shot.renderer == "manim"

    def render(self, shot: Shot, ctx: RenderContext) -> RenderResult:
        if shutil.which("manim") is None:
            raise ManimRenderError(
                "manim CLI not found on PATH. Install with: "
                "pip install manim (plus cairo/pango system deps)."
            )

        work_dir = Path(ctx.work_dir) / f"manim_shot_{shot.id}"
        work_dir.mkdir(parents=True, exist_ok=True)
        script_path = work_dir / "scene.py"
        script_path.write_text(_render_script(shot), encoding="utf-8")
        out_dir = work_dir / "output"
        out_dir.mkdir(exist_ok=True)
        out_mp4 = out_dir / f"{shot.id}.mp4"

        cmd = [
            "manim",
            "-ql",  # low quality for speed
            "--media_dir",
            str(out_dir / "media"),
            "-o",
            out_mp4.name,
            str(script_path),
            "GeneratedScene",
        ]
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, check=False)
        except OSError as e:
            raise ManimRenderError(f"manim failed to launch: {e}") from e

        # Manim writes outputs into nested media/videos/<script_stem>/<quality>/<name>.mp4
        candidate = next(
            (out_dir / "media" / "videos" / "scene" / "480p15").glob("*.mp4"),
            None,
        )
        if result.returncode != 0 or candidate is None or not candidate.exists():
            raise ManimRenderError(
                f"manim render failed (rc={result.returncode}):\n{result.stderr}"
            )

        return RenderResult(
            mp4_path=candidate,
            duration=shot.duration,
            log=result.stdout,
            provenance={"shot_id": shot.id, "renderer": "manim"},
        )


def _render_script(shot: Shot) -> str:
    """Produce a minimal Manim scene that holds the right duration."""
    title = (shot.options.get("title") if shot.options else None) or shot.id
    return (
        dedent(
            f"""
        from manim import Scene, Text

        class GeneratedScene(Scene):
            def construct(self):
                title = Text({title!r}, font_size=48)
                self.add(title)
                self.wait({shot.duration})
        """
        ).strip()
        + "\n"
    )
