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
...     timeline=[Shot(id="s1", renderer="cutout", duration=45.0)],
... )
>>> from an.base import SCHEMA_VERSION
>>> scene.version == SCHEMA_VERSION
True
>>> scene.kind
'SceneIR'
>>> scene.timeline[0].renderer
'cutout'
"""

from __future__ import annotations

from typing import Annotated, Any, Literal, Union

from pydantic import model_serializer, BaseModel, ConfigDict, Field, model_validator

from an.base import (
    COMPATIBLE_VERSION,
    DEFAULT_DURATION,
    DEFAULT_FPS,
    DEFAULT_RESOLUTION,
    SCHEMA_VERSION,
    EasingSpec,
    PathStr,
    Seconds,
    RendererName,
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


class CameraKey(_IRModel):
    """One camera pose at one time — the explicit door behind the named moves.

    >>> CameraKey(at=1.0, x=-160.0, zoom=1.25).x
    -160.0

    **Sign convention**, stated because every surveyed tool disagrees: `+x`
    moves the CAMERA right, which moves the content left. `zoom` is on-screen
    magnification, so `1.25` means "everything 25% bigger", and it composes
    through the pivot — a push-in during a pan zooms toward what the camera is
    looking at rather than toward a fixed frame centre. `rotation` is camera
    roll in radians.

    `easing` defaults to **`None`, not `"ease_in_out"`**, and that is not a
    style choice: today's emitter puts `"ease_in_out"` on the first keyframe
    and `null` on the terminal one, so a per-key default of `"ease_in_out"`
    would put it on both and move every camera scene's contract hash. The
    named moves supply the easing they have always supplied.
    """

    at: Seconds = 0.0
    #: Camera position in scene pixels. `+x` moves the camera right.
    x: float = Field(default=0.0, allow_inf_nan=False)
    y: float = Field(default=0.0, allow_inf_nan=False)
    #: On-screen magnification. Must be > 0 — a zero or negative zoom is not a
    #: camera, and the compiler would emit a degenerate root scale.
    zoom: float = Field(default=1.0, gt=0, allow_inf_nan=False)
    #: Camera roll, radians.
    rotation: float = Field(default=0.0, allow_inf_nan=False)
    easing: EasingSpec | None = None


class Camera(_IRModel):
    """Camera state for a shot: a named move, or explicit keys.

    >>> Camera(move="push_in").move
    'push_in'
    >>> Camera(keys=[CameraKey(at=0.0), CameraKey(at=2.0, x=-200.0)]).keys[1].x
    -200.0

    **One code path, two front doors** — the shape the dialogue `[emotion]`
    sugar already uses. A named `move` desugars to a key list; `keys` is that
    list written out. Setting both raises, because a scene that says two
    things about the same camera has no reading that is not a guess.

    `keys` defaults to `None`, not `[]`, and that too is load-bearing: the
    markdown writer dumps the camera with `exclude_none=True`, which KEEPS
    empty lists — an empty default would write `keys: []` into every camera
    block it regenerates.

    `position`, `target` and `focal_length` were removed in an#109. They were
    written into every `scene.md` this package ever generated and read by
    nothing; a registered migration drops them.
    """

    #: A named preset — sugar for `keys`. The cutout renderer's vocabulary is
    #: `an.adapters.cutout.compile.CAMERA_MOVES`; validate and the compiler are
    #: pinned to the same table by test, because a move that validates and then
    #: raises is the failure `_check_renderable` exists to prevent.
    move: str | None = None
    #: The explicit door. `None` = use `move`.
    keys: list[CameraKey] | None = None


# -----------------------------------------------------------------------------
# Asset references
# -----------------------------------------------------------------------------


class StagePlacement(_IRModel):
    """Where an entity stands on the stage, and how big it is.

    >>> StagePlacement(at=(120.0, -40.0), scale=0.5).at
    (120.0, -40.0)
    >>> StagePlacement().at is None
    True

    `at` is in SCENE pixels relative to the stage centre — the same space the
    compiler already places characters in — so an author reads it off the same
    ruler as a camera pivot. `scale` multiplies the rig's own uniform scale.

    Deliberately only two fields today. #108 sketches `depth` and `after` as
    well; both belong to the stage vocabulary that arrives with the translating
    camera (#109) and plane environments (#110), and shipping either now would
    put a knob in the schema that nothing reads — which is worse than an absent
    one, because a scene that sets it renders identically and says nothing.
    """

    #: ``(x, y)`` in scene pixels from the stage centre. ``None`` = default layout.
    #:
    #: `allow_inf_nan=False` on both fields, and it is not pedantry: pydantic
    #: serializes `inf` and `nan` to JSON **null**, and re-validating that JSON
    #: raises — so a scene file written with either is corrupt one way, and the
    #: author finds out on the next load rather than at the edit that did it
    #: (an#108 review, M-1). `gt=0` already refuses `scale=0` and `scale=-1`; it
    #: does not refuse `inf`.
    at: (
        tuple[
            Annotated[float, Field(allow_inf_nan=False)],
            Annotated[float, Field(allow_inf_nan=False)],
        ]
        | None
    ) = None
    #: Uniform scale multiplier on the built rig. ``1.0`` = the rig's own size.
    scale: float = Field(default=1.0, gt=0, allow_inf_nan=False)


class AssetRef(_IRModel):
    """Reference to an entry in a project store.

    The IR never inlines large assets. Instead it references them by store
    name + key, so the same character/voice/environment is reusable across
    scenes. ``overrides`` lets a single shot tweak presentation without
    forking the asset.

    >>> AssetRef(kind="character", id="maya", store="characters", ref="maya-v1").id
    'maya'
    """

    #: ``"style"`` was retired in an#106: it selected nothing (the compiler
    #: skipped it, nothing read the styles store) and the name belonged to the
    #: renderer selector. Art direction arrives as a StylePack (#112).
    kind: Literal["character", "environment", "voice", "prop"]
    id: str
    store: str  # which store in the project mall
    ref: str  # key inside that store
    overrides: dict[str, Any] | None = None

    #: Where on the stage this entity stands. ``None`` — the default and what
    #: every existing document has — means "wherever the layout puts it",
    #: which for characters is the evenly-spaced row the compiler computes.
    #:
    #: **Additive by construction, and hash-free by construction**: the
    #: contract hashes the COMPILED document, and an `AssetRef` never reaches
    #: it. So this field can grow without retiring a single ledger row.
    stage: StagePlacement | None = None

    @model_serializer(mode="wrap")
    def _omit_unset_stage(self, handler):
        """Serialize ``stage: null`` out of existence when it is unset.

        The precedent is `serialize._omit_unset_step_hz`, and the reason is
        the same one scaled down: every committed `ir/scene.json` in this repo
        — corpus fixtures, examples, and whatever a user has on disk — was
        written before this field existed. A defaulted `null` on every
        `AssetRef` would rewrite all of them on the next `an sync`, and
        `test_every_speaking_corpus_scene_ir_is_reproducible_from_its_md`
        would be red until each was regenerated. A field nobody set should
        leave no trace (an#108).
        """
        data = handler(self)
        if self.stage is None:
            data.pop("stage", None)
        return data


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


#: Default ramp in/out of an expression, seconds (0 = cut). The dialogue
#: `[emotion]` sugar uses its own in `an.expression.provider`.
DFLT_EXPRESSION_BLEND_S: float = 0.15


class ExpressionAction(_ActionBase):
    """Hold a facial expression on an entity (an#98, epic #9 Wave 6).

    ``preset`` names one of :data:`an.expression.presets.PRESETS`; ``axes``
    are per-axis overrides layered on it (axis units, see
    :mod:`an.expression.axes`); ``None`` + no axes is a cheap "return to
    rest". ``duration=None`` runs to the shot end (the looping-play rule) and
    is **zero-width in a sequence**, like ``play``. ``blend`` ramps the
    intensity in and out; two overlapping expressions cross-fade because the
    face solver sums offsets. The dialogue ``speaker [emotion]: …`` bracket is
    sugar for one of these over the line, desugared in memory only.

    A leaf action, flattened like ``play``: the compiler resolves it in the
    face solver (one channel per ``(node, property)``), never per action.

    The ramp is a min over the two ends, so a span shorter than ``2·blend``
    never reaches full intensity (a 0.2 s expression at the default 0.15 s
    blend peaks at 0.67) and a ``duration=0`` expression shows only where a
    frame lands on it with ``blend=0`` — cut the blend for a flash.
    """

    kind: Literal["expression"] = "expression"
    target: PathStr  # the ENTITY; the binding picks the nodes
    preset: str | None = None
    axes: dict[str, float] = Field(default_factory=dict)
    intensity: float = Field(default=1.0, ge=0.0, le=1.0)
    duration: Seconds | None = Field(default=None, ge=0.0)  # None = to the shot end
    blend: Seconds = Field(default=DFLT_EXPRESSION_BLEND_S, ge=0.0)


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
        ExpressionAction,
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


class WordTimingIR(_IRModel):
    """One word of a line and when it was spoken, in seconds from the line's
    start (like :class:`VisemeKeyframe`, never absolute). Stamped by the audio
    pipeline from the provider's word timings when it has them (an#96); JSON
    only — ``scene.md`` never carries it, the way it never carries visemes."""

    text: str
    start: Seconds
    end: Seconds

    @model_validator(mode="after")
    def _ordered(self) -> "WordTimingIR":
        if self.start < 0 or self.end < self.start:
            raise ValueError(
                f"word timing {self.text!r} must satisfy 0 <= start <= end; "
                f"got start={self.start}, end={self.end}"
            )
        return self


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
    #: The provider's word timings, line-relative; ``None`` when the provider
    #: has none (offline, Rhubarb) or the line was stamped before an#96.
    word_timings: list[WordTimingIR] | None = None
    audio_ref: str | None = None  # mall["audio"] key (content-hash of TTS input)
    viseme_ref: str | None = None  # mall["visemes"] key (content-hash of lipsync input)


class Narration(_IRModel):
    """Off-screen narration. Same shape as Dialogue minus the speaker pin."""

    text: str
    voice_ref: str | None = None
    start: Seconds | None = None
    duration: Seconds | None = None
    viseme_track: VisemeTrack | None = None
    word_timings: list[WordTimingIR] | None = None
    audio_ref: str | None = None
    viseme_ref: str | None = None


# -----------------------------------------------------------------------------
# Shot
# -----------------------------------------------------------------------------


class Shot(_IRModel):
    """A single rendered unit. A scene is a sequence of shots.

    A shot's ``renderer`` selects the backend that draws it. Every renderer must accept the
    same Shot fields; renderer-specific options go under ``options``.
    """

    id: str
    #: Which RENDERER draws this shot — not art direction. The field was
    #: called `style` until an#106, colliding with the styles store (which
    #: holds art direction) and with `AssetRef(kind="style")`; one word for two
    #: meanings is how a scene came to declare a "style" that selected a
    #: renderer while the thing that actually styles it went unread.
    renderer: RendererName = "cutout"
    duration: Seconds = DEFAULT_DURATION
    camera: Camera | None = None
    entities: list[AssetRef] = Field(default_factory=list)
    actions: list[Action] = Field(default_factory=list)
    dialogue: list[Dialogue] = Field(default_factory=list)
    narration: list[Narration] = Field(default_factory=list)
    options: dict[str, Any] = Field(default_factory=dict)
    #: Per-shot override of :attr:`Meta.step_hz` (``None`` = inherit).
    step_hz: float | None = Field(default=None, gt=0)


def resolve_step_hz(shot: "Shot", scene_step_hz: float | None) -> float | None:
    """The stepped-timing policy ``shot`` renders under: its own ``step_hz``
    when it declares one, else the scene's, else ``None`` (smooth). The ONE
    statement of the shot-over-scene rule — the cutout renderer, the preview
    and the project renderer all call it (an#89 review: three copies).

    >>> resolve_step_hz(Shot(id="s", step_hz=10.0), 15.0)
    10.0
    >>> resolve_step_hz(Shot(id="s"), 15.0)
    15.0
    >>> resolve_step_hz(Shot(id="s"), None) is None
    True
    """
    return shot.step_hz if shot.step_hz is not None else scene_step_hz


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
    default_renderer: RendererName = "cutout"
    notes: str = ""
    #: Stepped timing for AUTHORED TWEENS, in pose updates per second; ``None``
    #: (the default) leaves every tween smooth. At 30 fps, ``15`` is "on twos"
    #: and ``10`` "on threes" — the character-animation practice Spider-Verse
    #: made famous (characters on twos, simulation on ones). The camera is
    #: exempt by construction, as are swap channels (already stepped by
    #: format), compiled blinks and ``play`` clips: only `tween` curves are
    #: resampled — sample-and-hold of the eased curve on a SHOT-wide grid
    #: (every tween in a shot shares it; it restarts at a cut), not a retiming
    #: into holds and fast transitions. A tween's own START and END are always
    #: pose changes too, so a tween that begins or ends off-grid changes pose
    #: on that frame as well as on the grid (a ``set`` at an off-grid ``at``
    #: likewise lands where it was authored). A shot's own ``step_hz``
    #: overrides this. Must be positive (schema) and ``<= fps`` (validate +
    #: compile), an#89.
    step_hz: float | None = Field(default=None, gt=0)

    #: The `StylePack` in the project's `styles` store this scene is drawn
    #: under, by key. ``None`` — the default and what every existing document
    #: has — leaves every colour exactly where it is, which is why adding this
    #: moved no corpus hash (an#112).
    #:
    #: A pack changes what the COMPILER decides: the character palette, the
    #: leg and pupil colours, the environment presets' sky and ground. It does
    #: NOT recolour SVG art — that would need role tagging the descriptor does
    #: not have, and inferring a role from a pixel is what produced an#99's
    #: wrong-tone lid. A rig whose art a pack cannot reach is WARNED about by
    #: name at compile.
    style_pack: str | None = None

    @model_serializer(mode="wrap")
    def _omit_unset_style_pack(self, handler):
        """Serialize ``style_pack: null`` out of existence when it is unset.

        The same rule as `AssetRef._omit_unset_stage`, and the same reason:
        every committed `ir/scene.json` in this repo — and whatever a user has
        on disk — predates the field, so a defaulted `null` on every scene's
        meta rewrites all of them on the next `an sync`. A field nobody set
        should leave no trace (an#112).
        """
        data = handler(self)
        if isinstance(data, dict) and data.get("style_pack") is None:
            data.pop("style_pack", None)
        return data


class SceneIR(_IRModel):
    """Top-level Scene IR document. The SSOT.

    A document is portable, diffable, and renderer-agnostic. Persisted as JSON
    at ``ir/scene.json`` inside an an project.

    >>> from an.base import SCHEMA_VERSION
    >>> doc = SceneIR(meta=Meta(title="Hello"))
    >>> doc.version == SCHEMA_VERSION
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
