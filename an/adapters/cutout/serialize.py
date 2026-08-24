"""JSON contract between the Python compiler and the (future) JS runtime.

These Pydantic models describe **exactly** the JSON shape the PixiJS runtime
consumes — nothing more. ``compile_shot`` produces these objects; the runtime
reads them. The schema is deliberately separate from the cutout-internal
Python evaluation types (`clip.Clip`, `channel.Channel`) so the Python side
can evolve internally without breaking the runtime contract.

This file used to carry a sketched Spine-flavoured swap vocabulary
(``SkinJSON``, ``RigJSON``, ``CutoutSceneJSON.rigs``, ``SlotJSON``/
``NodeJSON.slots``, ``current_attachment``) that nothing populated and nothing
read — deleted in an#86 so the *real* swap mechanism (``VisualJSON``'s
per-node asset maps driven by step channels) is the only one the contract
describes. A few declared-but-unwired scalars remain (``PlacedClipJSON.blend_in``
/ ``blend_out`` — recorded, never applied; ``VisualJSON.texture_id``): the same
debt class at smaller scale, kept only because they are field-shaped
placeholders rather than a parallel *vocabulary* for a capability that shipped
elsewhere. Do not add more; a new field needs its producer and its consumer in
the same change.

>>> j = CutoutSceneJSON(
...     meta={"fps": 30, "width": 1920, "height": 1080, "duration": 5.0},
...     scene=NodeJSON(name="root"),
...     animations={},
...     timeline=TimelineJSON(duration=5.0, tracks=[]),
...     assets=AssetsJSON(textures={}, audio={}),
... )
>>> from_dict(to_dict(j)) == j
True
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class _JSONModel(BaseModel):
    """Base for serialized models — forward-compat reads, normalized aliases."""

    model_config = ConfigDict(extra="allow", populate_by_name=True)


# -----------------------------------------------------------------------------
# Scene tree
# -----------------------------------------------------------------------------


class TransformJSON(_JSONModel):
    """Local transform of a scene-graph node (authoring form).

    ``alpha`` is not geometry, but it lives here for the same reason the rest
    does: this is the per-node property bag the runtime applies, and it is
    animatable through the same channel machinery.

    It is set on the node's *container*, so it **cascades** — fading a character
    fades every part of it. Note that this is per-part compositing, not a
    flattened group fade: where two parts of the same character overlap, the
    seam is visible mid-fade. That is the standard behaviour of a 2D scene graph
    and the right default; a true group fade needs the subtree rendered to a
    texture first, which costs a render pass per node per frame.

    **This class's field defaults are the single source of truth for a
    property's rest value** — see ``compile.py``'s ``_PROPERTY_REST_VALUES``,
    which is derived from them rather than restated.
    """

    x: float = 0.0
    y: float = 0.0
    rotation: float = 0.0  # radians
    scale_x: float = 1.0
    scale_y: float = 1.0
    skew_x: float = 0.0
    skew_y: float = 0.0
    pivot_x: float = 0.0
    pivot_y: float = 0.0
    alpha: float = 1.0


class VisualJSON(_JSONModel):
    """Drawable content attached to a node.

    ``kind="svg_sprite"`` is the Phase 11b path: the runtime instantiates a
    ``PIXI.Sprite`` from a pre-loaded SVG texture identified by ``asset_id``.

    ``asset_sets`` carries this node's swap vocabulary —
    ``{set_name: {KEY: asset_id}}``, the compiler's per-slot **projection** of
    the descriptor's ``asset_sets`` onto the slot this visual draws (an#87).
    A channel whose property names one of these sets swaps the sprite's
    texture by key; ``viseme`` is just the conventional set name lip-sync
    uses. Same field name as ``CharacterDescriptor.asset_sets`` on purpose:
    one vocabulary, two layers (descriptor = declared, wire = resolved to
    texture aliases). Replaces the mouth-only ``viseme_assets``.

    ``width``/``height`` are the box the art is fitted **into**, not the size it
    is forced to. Under ``fit="contain"`` the art keeps its own aspect ratio and
    may leave slack on one axis; that slack is the correct rendering, not a bug.
    """

    kind: Literal["sprite", "rect", "ellipse", "mouth", "eye", "svg_sprite"] = "rect"
    #: How the art is fitted to ``width``/``height``.
    #:
    #: ``"contain"`` scales uniformly so the art keeps the shape it was drawn
    #: with — the invariant of an#74. ``"stretch"`` sizes each axis
    #: independently, which is what every sprite did before that issue and what
    #: distorted `arm_l` by 3.929x on the repo's own art.
    #:
    #: Additive with a ``"stretch"`` default so no stored scene changes meaning;
    #: the compiler emits ``"contain"`` for every sprite it builds.
    fit: Literal["stretch", "contain"] = "stretch"
    texture_id: str | None = None
    asset_id: str | None = None
    asset_sets: dict[str, dict[str, str]] | None = None
    width: float = 50.0
    height: float = 50.0
    anchor_x: float = 0.5
    anchor_y: float = 0.5
    color: str = "#888888"


class NodeJSON(_JSONModel):
    """One node in the scene tree."""

    name: str
    transform: TransformJSON = Field(default_factory=TransformJSON)
    visual: VisualJSON | None = None
    children: list["NodeJSON"] = Field(default_factory=list)


# -----------------------------------------------------------------------------
# Animations
# -----------------------------------------------------------------------------


class KeyframeJSON(_JSONModel):
    """Single keyframe in an animation channel."""

    time: float
    value: Any
    easing: str | list[float] | None = None


class ChannelJSON(_JSONModel):
    """One animated property of one target."""

    target: str  # path
    property: str
    keyframes: list[KeyframeJSON] = Field(default_factory=list)


class AnimationClipJSON(_JSONModel):
    """A named, reusable animation clip."""

    name: str
    duration: float
    loop_mode: Literal["once", "loop", "ping_pong"] = "once"
    channels: list[ChannelJSON] = Field(default_factory=list)


# -----------------------------------------------------------------------------
# Timeline
# -----------------------------------------------------------------------------


class PlacedClipJSON(_JSONModel):
    """An animation placed on a track at a specific time."""

    animation_id: str  # name lookup into AnimationClipJSON map
    start_time: float = 0.0
    duration: float | None = None  # override; None = clip's natural duration
    speed: float = 1.0
    blend_in: float = 0.0
    blend_out: float = 0.0


class TrackJSON(_JSONModel):
    """A sequence of placed clips with optional target-prefix metadata."""

    target_root: str = ""
    clips: list[PlacedClipJSON] = Field(default_factory=list)


class TimelineJSON(_JSONModel):
    """Top-level timeline: total duration + tracks."""

    duration: float
    tracks: list[TrackJSON] = Field(default_factory=list)


# -----------------------------------------------------------------------------
# Assets
# -----------------------------------------------------------------------------


class AssetJSON(_JSONModel):
    """A single asset (texture / audio file)."""

    src: str  # path or URL relative to the project root
    width: float | None = None
    height: float | None = None
    duration: float | None = None  # for audio


class AssetsJSON(_JSONModel):
    """Map of asset id → AssetJSON, split by kind."""

    textures: dict[str, AssetJSON] = Field(default_factory=dict)
    audio: dict[str, AssetJSON] = Field(default_factory=dict)


# -----------------------------------------------------------------------------
# Asset resolution — what the declared refs actually became
# -----------------------------------------------------------------------------


class AssetResolutionJSON(_JSONModel):
    """How one scene entity's store reference actually resolved at compile time.

    The IR declares every drawable entity as ``store`` + ``ref``. What the
    compiler *builds* from that pair is not recoverable from the scene tree
    afterwards: a character whose descriptor is missing and a character that
    never had one produce byte-identical procedural rigs. That ambiguity is
    an#33 — three CI runners once agreed perfectly about a picture that was not
    the picture, and the agreement read as a clean positive result.

    So the compiler records what it did, per entity, in the artifact the
    browser actually loads. ``fallback`` is the load-bearing bit: True means
    the declared ref supplied nothing and a stand-in was drawn in its place.

    >>> AssetResolutionJSON(
    ...     id="maya", kind="character", store="characters", ref="maya-v1",
    ...     resolved="descriptor", fallback=False,
    ... ).fallback
    False
    """

    id: str
    kind: str  # "character" | "environment"
    store: str
    ref: str
    #: What was built. Characters: "descriptor" | "parts" | "placeholder".
    #: Environments: "store" | "preset" | "default".
    resolved: str
    #: True when the declared ref supplied nothing and a stand-in was drawn.
    fallback: bool = False
    #: One human sentence saying why, when ``fallback`` is True.
    detail: str = ""


# -----------------------------------------------------------------------------
# Top-level scene
# -----------------------------------------------------------------------------


class CutoutSceneMetaJSON(_JSONModel):
    """Per-shot metadata."""

    fps: int = 30
    width: int = 1920
    height: int = 1080
    duration: float = 0.0
    background: str = "#ffffff"
    #: Per-entity blink phase in [0, 1), a pure function of the entity NAME
    #: (an#88). Stamped by the compiler — which now emits blinks as channels —
    #: and inert to the runtime. It is recorded because renaming a corpus
    #: character silently re-phases every blink and moves every pixel metric;
    #: a stamped phase turns that into a visible diff instead of an
    #: unexplained metric shift. (The runtime's determinism probe used to
    #: carry this; the fact moved with the mechanism.)
    blink_phases: dict[str, float] = Field(default_factory=dict)


class CutoutSceneJSON(_JSONModel):
    """Top-level cutout scene JSON — the JS runtime's input contract.

    Versioned so the runtime can refuse incompatible inputs.
    """

    version: str = "0.1.0"
    meta: CutoutSceneMetaJSON = Field(default_factory=CutoutSceneMetaJSON)
    scene: NodeJSON
    animations: dict[str, AnimationClipJSON] = Field(default_factory=dict)
    timeline: TimelineJSON
    assets: AssetsJSON = Field(default_factory=AssetsJSON)
    #: One entry per drawable entity, in scene order — see
    #: :class:`AssetResolutionJSON`. Inert to the runtime; read by the bench
    #: harness and the golden-corpus bless to assert WHICH render path ran.
    asset_resolution: list[AssetResolutionJSON] = Field(default_factory=list)


# Resolve forward refs for nested NodeJSON.children
NodeJSON.model_rebuild()


# -----------------------------------------------------------------------------
# Round-trip helpers
# -----------------------------------------------------------------------------


def to_dict(scene: CutoutSceneJSON) -> dict[str, Any]:
    """Dump a scene to a plain-dict representation (no None pruning)."""
    return scene.model_dump(mode="json")


def from_dict(d: dict[str, Any]) -> CutoutSceneJSON:
    """Rebuild a scene from a plain-dict representation."""
    return CutoutSceneJSON.model_validate(d)
