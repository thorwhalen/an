"""WhiteboardRenderer — stub for hand-drawn / chalkboard-style animation.

Phase 6 ships the registry entry only. The real implementation will likely
be a thin wrapper around the cutout backend with hand-drawn-styled assets
(rough strokes, paper texture), or a Manim subclass with a chalkboard theme.
A spike during v0.1 picks the direction.
"""

from __future__ import annotations

from an.adapters._base import RenderContext, RenderResult
from an.ir.schema import Shot


class WhiteboardRenderError(RuntimeError):
    """Raised by the whiteboard stub."""


class WhiteboardRenderer:
    """Whiteboard-style renderer (stub)."""

    name: str = "whiteboard"
    supported_renderers: tuple[str, ...] = ("whiteboard",)

    def can_render(self, shot: Shot) -> bool:
        return shot.renderer == "whiteboard"

    def render(self, shot: Shot, ctx: RenderContext) -> RenderResult:
        raise WhiteboardRenderError(
            "WhiteboardRenderer is a stub in Phase 6. Decision spike during "
            "v0.1: probably hand-drawn-styled cutout (rough strokes, paper "
            "texture) or a Manim chalkboard theme. Use the cutout backend "
            "for v0.1 dialogue cartoons."
        )
