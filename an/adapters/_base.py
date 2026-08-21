"""Renderer protocol, render context, render result, and the renderer registry.

Every backend implements ``Renderer`` and registers itself by name. The
orchestrator (or `RenderRouter` in non-agent contexts) picks a renderer per
shot by inspecting ``shot.style`` and asking each registered renderer's
``can_render``.

>>> from an.adapters import Renderer
>>> hasattr(Renderer, '__call__') or True  # Protocol attribute access works
True
"""

from __future__ import annotations

from collections.abc import Mapping, MutableMapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Protocol, runtime_checkable

from an.base import DEFAULT_FPS, DEFAULT_RESOLUTION
from an.ir.schema import Shot


@dataclass(slots=True)
class RenderContext:
    """Everything a renderer needs that isn't on the Shot itself.

    ``mall`` carries the project's stores so the renderer can resolve assets
    by reference. ``work_dir`` is a scratch space; the renderer must clean up
    after itself or treat it as ephemeral.
    """

    mall: Mapping[str, MutableMapping]
    work_dir: Path
    fps: int = DEFAULT_FPS
    resolution: tuple[int, int] = DEFAULT_RESOLUTION
    #: Refuse to draw a stand-in for a declared asset that the stores do not
    #: supply. Off by default so an asset-less project still renders; on for
    #: anything that measures pixels, where a stand-in is a different picture
    #: that looks like a successful render (an#33).
    strict_assets: bool = False
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class RenderResult:
    """Outcome of a single shot render."""

    mp4_path: Path
    duration: float  # actual rendered duration in seconds
    frame_manifest: list[Path] = field(default_factory=list)
    log: str = ""
    provenance: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class Renderer(Protocol):
    """Backend renderer interface.

    Implementations should be cheap to construct and stateless across renders;
    state belongs in the ``RenderContext`` or the project mall.
    """

    name: str
    supported_styles: tuple[str, ...]

    def can_render(self, shot: Shot) -> bool:
        """Return True if this renderer can render ``shot``."""

    def render(self, shot: Shot, ctx: RenderContext) -> RenderResult:
        """Render a single shot to mp4. Idempotent given identical inputs."""


# -----------------------------------------------------------------------------
# Registry
# -----------------------------------------------------------------------------


class RendererRegistry:
    """Name-keyed registry of renderers.

    A module-level instance is exposed via ``register_renderer`` /
    ``get_renderer`` / ``list_renderers``; callers needing isolation (tests,
    multi-tenant servers) can construct their own.
    """

    def __init__(self) -> None:
        self._by_name: dict[str, Renderer] = {}

    def register(self, renderer: Renderer) -> None:
        if not getattr(renderer, "name", None):
            raise ValueError("renderer must have a non-empty 'name' attribute")
        self._by_name[renderer.name] = renderer

    def get(self, name: str) -> Renderer:
        if name not in self._by_name:
            raise KeyError(f"no renderer registered with name {name!r}")
        return self._by_name[name]

    def find_for(self, shot: Shot) -> Renderer | None:
        """Return the first registered renderer that ``can_render(shot)``."""
        for r in self._by_name.values():
            if r.can_render(shot):
                return r
        return None

    def names(self) -> Iterable[str]:
        return list(self._by_name.keys())


_DEFAULT_REGISTRY = RendererRegistry()


def register_renderer(renderer: Renderer) -> None:
    """Register a renderer in the default registry."""
    _DEFAULT_REGISTRY.register(renderer)


def get_renderer(name: str) -> Renderer:
    """Look up a renderer by name in the default registry."""
    return _DEFAULT_REGISTRY.get(name)


def list_renderers() -> list[str]:
    """Names of all renderers registered in the default registry."""
    return list(_DEFAULT_REGISTRY.names())
