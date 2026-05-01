"""Renderer adapters — facades over backends (cutout, Manim, Remotion, etc.).

The Renderer Protocol and registry live in `_base`. Concrete backends are
imported here so they self-register on package import.
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

# Import the cutout subpackage to trigger its self-registration.
# Other backends (manim, remotion, whiteboard) land in Phase 6 and will be
# imported the same way once they exist.
from an.adapters import cutout  # noqa: F401

__all__ = [
    "Renderer",
    "RendererRegistry",
    "RenderContext",
    "RenderResult",
    "register_renderer",
    "get_renderer",
    "list_renderers",
    "cutout",
]
