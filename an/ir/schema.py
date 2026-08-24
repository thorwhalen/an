"""Pydantic v2 models for the Scene IR.

Design principles (locked in from the architectural plan):

- **Renderer-agnostic.** No cutout-specific or Manim-specific fields here. Backend
  adapters compile shots into their own internal formats. This module only knows
  what every shot has in common.
- **Versioned envelope.** Every IR document carries `version` and
  `compatible_version`. Migrations live in `an.ir.migrate`.
- **Forward-compatible reads.** Top-level model has ``extra="allow"`` so a future
  field doesn't crash an older reader.
- **Discriminated `Action` union.** All authoring-time and flattened actions
  carry a `kind` literal so Pydantic dispatches to the right validator.
- **Time in seconds (float).** Always.

Doctest:

>>> from an.ir.schema import SceneIR, Meta, Shot
>>> scene = SceneIR(
...     meta=Meta(title="Park Bench", duration=45.0),
...     timeline=[Shot(id="s1", style="cutout", duration=45.0)],
... )
>>> scene.version
'0.1.0'
>>> scene.kind
'SceneIR'
>>> scene.timeline[0].style
'cutout'
"""

from __future__ import annotations

from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, ConfigDict, Field

from an.base import (
    COMPATIBLE_VERSION,
    DEFAULT_DURATION,
    DEFAULT_FPS,
    DEFAULT_RESOLUTION,
    SCHEMA_VERSION,
    EasingSpec,
    PathStr,
    Seconds,
    StyleName,
)


# -----------------------------------------------------------------------------
# Inbound-friendly base: forward-compat reads, strict-ish writes.
# -----------------------------------------------------------------------------


class _IRModel(BaseModel):
    """Common config: allow unknown fields on read so newer documents survive."""

    model_config = ConfigDict(extra="allow", populate_by_name=True)


# -----------------------------------------------------------------------------
# Leaf / value types
# -----------------------------------------------------------------------------


class Resolution(_IRModel):
    """Pixel dimensions of the rendered output."""

    width: int = DEFAULT_RESOLUTION[0]
    height: int = DEFAULT_RESOLUTION[1]


class Camera(_IRModel):
    """Camera state for a shot. Minimal placeholder; expanded in P2."""

    position: tuple[float, float, float] = (0.0, 0.0, 0.0)
    target: tuple[float, float, float] = (0.0, 0.0, 0.0)
    focal_length: float = 50.0
    # Interpreted by the adapter. The cutout renderer implements
    # hold | push_in | pull_out | zoom_in | zoom_out and RAISES on anything
    # else. It deliberately does not list "pan_left": the camera is a scale
    # tween on the scene root, so it cannot translate at all, and advertising
    # a move nothing implements is how it came to be documented here and dead
    # in the compiler. A translating camera lands in Wave 7 of #9.
    move: str | None = None


# -----------------------------------------------------------------------------
# Asset references
# -----------------------------------------------------------------------------


class AssetRef(_IRModel):
    """Reference to an entry in a project store.

    The IR never inlines large assets. Instead it references them by store
    name + key, so the same character/voice/environment is reusable across
    scenes. ``overrides`` lets a single shot tweak presentation without
    forking the asset.

    >>> AssetRef(kind="character", id="maya", store="characters", ref="maya-v1").id
    'maya'
    """

    kind: Literal["character", "environment", "voice", "style", "prop"]
    id: str
    store: str  # which store in the project mall
    ref: str  # key inside that store
    overrides: dict[str, Any] | None = None


# -----------------------------------------------------------------------------
# Action union — authoring atoms + composition nodes.
# All authoring DSL output, all flattened forms, all serialized actions are
# instances of this discriminated union.
# -----------------------------------------------------------------------------


class _ActionBase(_IRModel):
    """Common fields for all action variants."""

    # Name optional; helpful for editing/debugging but not required.
    name: str | None = None


class SetAction(_ActionBase):
    """Set a property to a value at a specific time. Discrete, no tween."""

    kind: Literal["set"] = "set"
    target: PathStr
    property: str
    value: Any
    at: Seconds = 0.0


class TweenAction(_ActionBase):
    """Animate a property from a start value to an end value over a duration."""

    kind: Literal["tween"] = "tween"
    target: PathStr
    property: str
    to_value: Any
    from_value: Any | None = None
    duration: Seconds = 1.0
    easing: EasingSpec | None = "ease_in_out"


class PlayAction(_ActionBase):
    """Play a named animation of the target entity's descriptor (an#7).

    ``animation`` names an entry of ``CharacterDescriptor.animations`` (the
    seeded ``idle_breath`` and ``blink``, or anything an author adds); the
    compiler resolves its tracks into channels on the entity's nodes.
    ``duration`` widens/narrows the placement window; ``None`` means the
    animation's own duration — or, when the resolved ``loop`` is true, the
    rest of the shot, because a loop bounded by its own natural duration
    never loops. ``loop`` overrides the animation's declared ``loop``
    (``None`` = use the descriptor's). Inside a ``sequence`` a play with
    ``duration=None`` has ZERO width (:func:`an.ir.compose.duration_of`):
    the next sibling starts at the same instant.
    """

    kind: Literal["play"] = "play"
    target: PathStr
    animation: str  # a key of the entity descriptor's `animations`
    duration: Seconds | None = None  # None = the animation's natural duration
    speed: float = 1.0
    loop: bool | None = None  # None = the descriptor animation's own `loop`


class SequenceAction(_ActionBase):
    """Composition: run children one after the other."""

    kind: Literal["sequence"] = "sequence"
    children: list["Action"] = Field(default_factory=list)


class ParallelAction(_ActionBase):
    """Composition: run all children simultaneously starting at the same time."""

    kind: Literal["parallel"] = "parallel"
    children: list["Action"] = Field(default_factory=list)


class DelayAction(_ActionBase):
    """Composition: an empty span that consumes time."""

    kind: Literal["delay"] = "delay"
    duration: Seconds


class LoopAction(_ActionBase):
    """Composition: repeat ``child`` ``count`` times."""

    kind: Literal["loop"] = "loop"
    child: "Action"
    count: int = 1


#: Discriminated union of every action variant. Pydantic dispatches on `kind`.
Action = Annotated[
    Union[
        SetAction,
        TweenAction,
        PlayAction,
        SequenceAction,
        ParallelAction,
        DelayAction,
        LoopAction,
    ],
    Field(discriminator="kind"),
]


# Resolve forward refs for self-referential composition nodes.
SequenceAction.model_rebuild()
ParallelAction.model_rebuild()
LoopAction.model_rebuild()


# -----------------------------------------------------------------------------
# Dialogue & narration
# -----------------------------------------------------------------------------


class VisemeKeyframe(_IRModel):
    """A single mouth-shape keyframe in a viseme track."""

    time: Seconds
    viseme: str  # Rhubarb letter A-H/X, MPEG-4 viseme number, or Azure name


class VisemeTrack(_IRModel):
    """Aligned viseme track produced by the lip-sync stage. Optional in P1."""

    fps_hint: float | None = None
    keyframes: list[VisemeKeyframe] = Field(default_factory=list)


class Dialogue(_IRModel):
    """One line of spoken dialogue.

    ``timing`` is None until the audio pipeline runs (TTS gives us a real
    duration); the orchestrator fills it in then.
    """

    speaker: str  # the entity id this line belongs to
    text: str
    voice_ref: str | None = None  # key in the voices store; None = default
    start: Seconds | None = None
    duration: Seconds | None = None
    emotion: str | None = None
    viseme_track: VisemeTrack | None = None
    audio_ref: str | None = None  # mall["audio"] key (content-hash of TTS input)
    viseme_ref: str | None = None  # mall["visemes"] key (content-hash of lipsync input)


class Narration(_IRModel):
    """Off-screen narration. Same shape as Dialogue minus the speaker pin."""

    text: str
    voice_ref: str | None = None
    start: Seconds | None = None
    duration: Seconds | None = None
    viseme_track: VisemeTrack | None = None
    audio_ref: str | None = None
    viseme_ref: str | None = None


# -----------------------------------------------------------------------------
# Shot
# -----------------------------------------------------------------------------


class Shot(_IRModel):
    """A single rendered unit. A scene is a sequence of shots.

    A shot's ``style`` selects the renderer. Every renderer must accept the
    same Shot fields; renderer-specific options go under ``options``.
    """

    id: str
    style: StyleName = "cutout"
    duration: Seconds = DEFAULT_DURATION
    camera: Camera | None = None
    entities: list[AssetRef] = Field(default_factory=list)
    actions: list[Action] = Field(default_factory=list)
    dialogue: list[Dialogue] = Field(default_factory=list)
    narration: list[Narration] = Field(default_factory=list)
    options: dict[str, Any] = Field(default_factory=dict)


# -----------------------------------------------------------------------------
# Top-level Scene IR
# -----------------------------------------------------------------------------


class Meta(_IRModel):
    """Scene metadata."""

    title: str = ""
    author: str = ""
    duration: Seconds = 0.0
    fps: int = DEFAULT_FPS
    resolution: Resolution = Field(default_factory=Resolution)
    default_style: StyleName = "cutout"
    notes: str = ""


class SceneIR(_IRModel):
    """Top-level Scene IR document. The SSOT.

    A document is portable, diffable, and renderer-agnostic. Persisted as JSON
    at ``ir/scene.json`` inside an an project.

    >>> doc = SceneIR(meta=Meta(title="Hello"))
    >>> doc.version == '0.1.0'
    True
    >>> round_tripped = SceneIR.model_validate_json(doc.model_dump_json())
    >>> round_tripped.meta.title
    'Hello'
    """

    version: str = SCHEMA_VERSION
    compatible_version: str = COMPATIBLE_VERSION
    kind: Literal["SceneIR"] = "SceneIR"
    meta: Meta = Field(default_factory=Meta)
    assets: list[AssetRef] = Field(default_factory=list)
    timeline: list[Shot] = Field(default_factory=list)
