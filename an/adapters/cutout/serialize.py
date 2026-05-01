"""JSON contract between the Python compiler and the (future) JS runtime.

These Pydantic models describe **exactly** the JSON shape the Phase 2B
PixiJS runtime will consume. Phase 2A produces these objects via
``compile_shot``; Phase 2B reads them. The schema is deliberately separate
from the cutout-internal Python types (`scene.Node`, `clip.Clip`, etc.) so
the Python side can evolve internally without breaking the runtime contract.

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
    """Local transform of a scene-graph node (authoring form)."""

    x: float = 0.0
    y: float = 0.0
    rotation: float = 0.0  # radians
    scale_x: float = 1.0
    scale_y: float = 1.0
    skew_x: float = 0.0
    skew_y: float = 0.0
    pivot_x: float = 0.0
    pivot_y: float = 0.0


class VisualJSON(_JSONModel):
    """Drawable content attached to a node."""

    kind: Literal["sprite", "rect"] = "rect"
    texture_id: str | None = None
    width: float = 50.0
    height: float = 50.0
    anchor_x: float = 0.5
    anchor_y: float = 0.5
    color: str = "#888888"
    current_attachment: str | None = None


class SlotJSON(_JSONModel):
    """Attachment point on a node."""

    name: str
    x: float = 0.0
    y: float = 0.0
    rotation: float = 0.0
    current_attachment: str | None = None


class NodeJSON(_JSONModel):
    """One node in the scene tree."""

    name: str
    transform: TransformJSON = Field(default_factory=TransformJSON)
    visual: VisualJSON | None = None
    slots: dict[str, SlotJSON] = Field(default_factory=dict)
    children: list["NodeJSON"] = Field(default_factory=list)


# -----------------------------------------------------------------------------
# Rigs (per-character skin definitions; mostly a 2B+ concern, sketched here)
# -----------------------------------------------------------------------------


class SkinJSON(_JSONModel):
    """A named skin: slot path → attachment dict."""

    name: str
    attachments: dict[str, dict[str, Any]] = Field(default_factory=dict)


class RigJSON(_JSONModel):
    """Rig definition for one character: root node path + skins."""

    root_node: str
    skins: dict[str, SkinJSON] = Field(default_factory=dict)


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
# Top-level scene
# -----------------------------------------------------------------------------


class CutoutSceneMetaJSON(_JSONModel):
    """Per-shot metadata."""

    fps: int = 30
    width: int = 1920
    height: int = 1080
    duration: float = 0.0
    background: str = "#ffffff"


class CutoutSceneJSON(_JSONModel):
    """Top-level cutout scene JSON — the JS runtime's input contract.

    Versioned so the runtime can refuse incompatible inputs.
    """

    version: str = "0.1.0"
    meta: CutoutSceneMetaJSON = Field(default_factory=CutoutSceneMetaJSON)
    scene: NodeJSON
    rigs: dict[str, RigJSON] = Field(default_factory=dict)
    animations: dict[str, AnimationClipJSON] = Field(default_factory=dict)
    timeline: TimelineJSON
    assets: AssetsJSON = Field(default_factory=AssetsJSON)


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
