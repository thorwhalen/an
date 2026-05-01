"""Renderer adapters — facades over backends (cutout, Manim, Remotion, whiteboard).

The Renderer Protocol and registry live in `_base`. Concrete backends are
imported here so they self-register on package import. Backends with missing
system deps still register but their ``render()`` raises a clear error;
``can_render(shot)`` continues to work for routing decisions.
"""

from an.adapters._base import (
    Renderer,
    RendererRegistry,
    RenderContext,
    RenderResult,
    register_renderer,
    get_renderer,
    list_renderers,
)

from an.adapters import cutout  # noqa: F401

# Phase 6: register the other backends. They're skeleton-implementations in
# v0.1 — render() raises clearly if the backend isn't usable yet.
from an.adapters.manim_adapter import ManimRenderer
from an.adapters.remotion_adapter import RemotionRenderer
from an.adapters.whiteboard import WhiteboardRenderer

register_renderer(ManimRenderer())
register_renderer(RemotionRenderer())
register_renderer(WhiteboardRenderer())

__all__ = [
    "Renderer",
    "RendererRegistry",
    "RenderContext",
    "RenderResult",
    "register_renderer",
    "get_renderer",
    "list_renderers",
    "cutout",
    "ManimRenderer",
    "RemotionRenderer",
    "WhiteboardRenderer",
]
