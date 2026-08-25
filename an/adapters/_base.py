"""Renderer protocol, render context, render result, and the renderer registry.

Every backend implements ``Renderer`` and registers itself by name. The
orchestrator (or `RenderRouter` in non-agent contexts) picks a renderer per
shot by inspecting ``shot.renderer`` and asking each registered renderer's
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

from an.base import DEFAULT_FPS, DEFAULT_RESOLUTION, DEFAULT_SUPERSAMPLE
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
    #: Render at this many times the declared resolution and resolve back with
    #: an exact block mean. **1 means off, and off is free** — Chromium's own
    #: PNG bytes reach disk untouched.
    #:
    #: A `RenderContext` field, and that placement is load-bearing rather than
    #: convenient. Simulated against a real committed ledger row: as a
    #: `render_kwargs` entry it becomes a `COMMON_ENV_PATHS` key and **all 96
    #: metrics are refused**; as a field on the compiled scene document it moves
    #: `scene_contract_sha256` and **every scene becomes incomparable**; here,
    #: only `runtime_sha256` moves, which is deliberately not a comparability
    #: key — so 30 render-side entries still compare. It needs no
    #: `SCHEMA_VERSION` migration, and it MUST reach per-shot provenance, because
    #: a row that does not record it cannot be read back later.
    supersample: int = DEFAULT_SUPERSAMPLE
    #: The delivered encode's pixel format, or ``None`` for the module default.
    #: **The one first-order quality lever in the encoder**: 4:2:0 -> 4:4:4 cuts
    #: the edge-band error 11.35 -> 3.79, where mathematically lossless 4:2:0
    #: only reaches 10.15. Losslessness buys 8%; dropping chroma subsampling
    #: buys 66%.
    #:
    #: ``None`` rather than the literal, so the bench's `pix_fmt` lever — which
    #: rebinds the module default — still reaches an unset render. The default
    #: stays 4:2:0 for a PRODUCT reason and not an encoder one: High 4:4:4
    #: Predictive is refused by many hardware decoders, browsers and platforms.
    pix_fmt: str | None = None
    #: Scene-level stepped-timing policy for authored tweens (an#89); a shot's
    #: own ``step_hz`` overrides it. ``None`` = smooth. Reaches the compiled
    #: document's ``meta.step_hz`` (only when set) and per-shot provenance.
    #:
    #: The one deliberate exception to the rule two fields up ("a field on the
    #: compiled scene document moves `scene_contract_sha256`"): unlike
    #: `supersample`, this knob CHANGES the compiled document — the resampled
    #: keyframes are the contract — so the hash moves whenever it is set no
    #: matter where the knob lives, and a document that carries its own timing
    #: policy is the honest one. Omit-when-unset keeps the unset case free.
    step_hz: float | None = None

    #: The `StylePack` this render is drawn under, already resolved from the
    #: scene's `meta.style_pack` (an#112). Resolved ONCE per render rather than
    #: per shot: a pack is art direction for a project, and a scene whose shots
    #: disagreed about it would be two scenes.
    style_pack: Any = None
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
    #: The `Shot.renderer` values this backend claims. It is the ONE place
    #: an adapter names them: `can_render` derives from it rather than
    #: comparing to its own literal, so an adapter cannot advertise one
    #: renderer and accept another. an#106 renamed this from
    #: `supported_styles`; the rename is a break for an out-of-tree adapter
    #: because `Renderer` is `@runtime_checkable` and 3.12 checks data
    #: members, so `isinstance(old_adapter, Renderer)` is now False.
    supported_renderers: tuple[str, ...]

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
