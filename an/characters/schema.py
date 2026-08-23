"""Character descriptor schema (Spine-shaped, Pydantic v2).

A character on disk lives at::

    characters/<name>/
        <name>.svg              # optional canonical layered SVG
        character.json          # CharacterDescriptor as JSON
        parts/
            head.svg
            torso.svg
            arm_l.svg, arm_r.svg
            leg_l.svg, leg_r.svg
            eye_l_open.svg, eye_l_closed.svg, eye_r_open.svg, eye_r_closed.svg
            brow_l.svg, brow_r.svg
            mouth/mouth_a.svg … mouth_h.svg, mouth_x.svg

The descriptor borrows Spine's separation of concerns:

- **bones** — where things attach. Local transforms relative to a parent.
- **slots** — what is drawn at each bone (one attachment active at a time).
- **skins** — for each slot, the named attachments and their SVG paths.
- **viseme_map** — Rhubarb shape letter → attachment name on the ``mouth`` slot.
- **animations** — built-in idle loops (breath, blink) keyed by name.

>>> char = CharacterDescriptor(name="maya")
>>> char.viseme_map["A"]
'mouth_a'
>>> char.viseme_map["X"]
'mouth_x'
>>> char.view_box
(0, 0, 1024, 1024)
>>> sorted(char.skins["default"].slots.keys())[:3]
['arm_l', 'arm_r', 'brow_l']
"""

from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

from an.ir.assets import AssetSource
from an.ir.migrate import DocumentKind, register_kind


CHARACTER_SCHEMA_VERSION = "0.1.0"

#: The descriptor is a schema-versioned document in its own right, with its own
#: version field. Registered here rather than in :mod:`an.ir.migrate` because
#: this module already imports from :mod:`an.ir.assets` — registering from the
#: other direction would close an import cycle, and because the package that
#: owns a schema is the one that knows its version field.
CHARACTER_DOCUMENT_KIND: DocumentKind = register_kind(
    DocumentKind(
        name="CharacterDescriptor",
        version_field="schema_version",
        current_version=CHARACTER_SCHEMA_VERSION,
    )
)

#: Rhubarb mouth shapes. A-F are mandatory in Rhubarb's basic set; G/H/X
#: are emitted when ``--extendedShapes GHX`` is on (Rhubarb's default).
#: We always ship all 9 so the renderer never has to fall back.
MOUTH_SHAPES: tuple[str, ...] = ("a", "b", "c", "d", "e", "f", "g", "h", "x")

#: Default Rhubarb-letter → mouth-attachment-name mapping. Uppercase keys
#: because Rhubarb emits A-X; lowercase attachment names by convention.
DEFAULT_VISEME_MAP: dict[str, str] = {s.upper(): f"mouth_{s}" for s in MOUTH_SHAPES}

#: Required body parts. A character missing any of these can't be rendered
#: as a full puppet; ``validate_character`` flags the gap.
REQUIRED_PARTS: tuple[str, ...] = (
    "head",
    "torso",
    "arm_l",
    "arm_r",
    "leg_l",
    "leg_r",
    "eye_l_open",
    "eye_l_closed",
    "eye_r_open",
    "eye_r_closed",
    "brow_l",
    "brow_r",
)

#: Canonical character viewBox: 1024x1024 with feet near y≈980. All parts
#: inherit this viewBox at export so PixiJS can use the SVG's intrinsic
#: viewBox without a calibration step.
DEFAULT_VIEW_BOX: tuple[int, int, int, int] = (0, 0, 1024, 1024)


class _CharModel(BaseModel):
    """Common config: forward-compatible reads, strict writes."""

    model_config = ConfigDict(extra="allow", populate_by_name=True)


class Bone(_CharModel):
    """A skeleton joint with a local transform relative to its parent.

    >>> b = Bone(name="head", parent="torso", x=0, y=-260, pivot="neck")
    >>> b.parent
    'torso'
    """

    name: str
    parent: Optional[str] = None
    x: float = 0.0
    y: float = 0.0
    rotation_deg: float = 0.0
    scale_x: float = 1.0
    scale_y: float = 1.0
    #: Optional pivot name — must match a circle in the SVG ``skeleton`` group.
    pivot: Optional[str] = None


class Slot(_CharModel):
    """A draw-order slot bound to a bone, displaying one attachment at a time.

    >>> s = Slot(name="mouth", bone="head", draw_order=7, attachment="mouth_x")
    >>> s.attachment
    'mouth_x'
    """

    name: str
    bone: str
    draw_order: int = 0
    #: Default attachment name; the active attachment can change at runtime
    #: via animation tracks targeting ``slot:<name>.attachment``.
    attachment: Optional[str] = None


class Attachment(_CharModel):
    """A drawable: an SVG path + anchor point (in 0..1 per-axis units).

    >>> a = Attachment(path="parts/head.svg", anchor=(0.5, 0.78))
    >>> a.anchor
    (0.5, 0.78)
    """

    path: str
    #: Anchor in 0..1 per-axis units (Pixi's Sprite.anchor convention).
    anchor: tuple[float, float] = (0.5, 0.5)
    #: Optional explicit bounding box override in the part's local viewBox.
    width: Optional[float] = None
    height: Optional[float] = None


class Skin(_CharModel):
    """A named outfit/variant: maps slot → {attachment_name → Attachment}.

    >>> skin = Skin(name="default", slots={"mouth": {"mouth_a": Attachment(path="parts/mouth/mouth_a.svg")}})
    >>> skin.slots["mouth"]["mouth_a"].path
    'parts/mouth/mouth_a.svg'
    """

    name: str = "default"
    slots: dict[str, dict[str, Attachment]] = Field(default_factory=dict)


_TrackType = Literal["sine", "step", "linear"]


class AnimationTrack(_CharModel):
    """A single channel inside an idle animation.

    The ``target`` is a path-string per the architecture pillar:

    - ``bone:<name>.<prop>`` for bone transforms (``x``, ``y``, ``rotation_deg``,
      ``scale_x``, ``scale_y``).
    - ``slot:<name>.attachment`` for swap animations (eyes blinking, mouth visemes).

    For ``type="sine"``: ``amplitude`` is the peak deviation; ``phase`` is in
    cycles (0..1). For ``type="step"`` / ``type="linear"``: ``frames`` is a
    list of ``[time_s, value]`` pairs evaluated in order.

    >>> t = AnimationTrack(target="bone:torso.y", type="sine", amplitude=2.0)
    >>> t.amplitude
    2.0
    """

    target: str
    type: _TrackType = "sine"
    # sine fields
    amplitude: float = 0.0
    phase: float = 0.0
    # step / linear fields
    frames: list[tuple[float, Any]] = Field(default_factory=list)

    @field_validator("target")
    @classmethod
    def _check_target(cls, v: str) -> str:
        if not (v.startswith("bone:") or v.startswith("slot:")):
            raise ValueError(f"target must start with 'bone:' or 'slot:'; got {v!r}")
        return v


class IdleAnimation(_CharModel):
    """A named idle loop (e.g., breath, blink).

    >>> a = IdleAnimation(name="idle_breath", duration=4.0)
    >>> a.loop
    True
    """

    name: str
    duration: float = 1.0
    loop: bool = True
    tracks: list[AnimationTrack] = Field(default_factory=list)


class CharacterDescriptor(_CharModel):
    """The on-disk character schema. Saved as ``character.json``.

    The descriptor is the SSOT for a character's identity, body part inventory,
    pivot geometry, viseme map, and built-in idle behaviors. Binary art lives
    as SVG sidecars referenced by ``Attachment.path`` (relative to the
    descriptor file).

    >>> c = CharacterDescriptor(name="maya")
    >>> c.schema_version == CHARACTER_SCHEMA_VERSION
    True
    >>> # all 9 mouths are wired into the default skin
    >>> sorted(c.skins["default"].slots["mouth"].keys()) == [
    ...     'mouth_a', 'mouth_b', 'mouth_c', 'mouth_d',
    ...     'mouth_e', 'mouth_f', 'mouth_g', 'mouth_h', 'mouth_x',
    ... ]
    True
    >>> # round-trip
    >>> raw = c.model_dump_json()
    >>> back = CharacterDescriptor.model_validate_json(raw)
    >>> back.name == c.name
    True
    """

    schema_version: str = CHARACTER_SCHEMA_VERSION
    kind: Literal["CharacterDescriptor"] = "CharacterDescriptor"

    name: str
    display_name: Optional[str] = None
    view_box: tuple[int, int, int, int] = DEFAULT_VIEW_BOX

    #: Voice-store id or path used by the audio pipeline. Optional; the scene
    #: can override per shot.
    voice_ref: Optional[str] = None

    #: Optional source SVG (relative path) that the parts/ folder was
    #: extracted from. Useful for re-slicing.
    source_svg: Optional[str] = None

    bones: list[Bone] = Field(default_factory=list)
    slots: list[Slot] = Field(default_factory=list)
    skins: dict[str, Skin] = Field(default_factory=dict)
    viseme_map: dict[str, str] = Field(default_factory=lambda: dict(DEFAULT_VISEME_MAP))
    animations: dict[str, IdleAnimation] = Field(default_factory=dict)

    #: Where this character's art came from, and what its licence obliges.
    #:
    #: ``None`` means "we made this" — not "unknown". Anything acquired should
    #: carry one, because a licence defect is the only failure that reaches
    #: BACKWARDS through completed work: a video shipped with an unattributed
    #: CC BY asset cannot be un-shipped.
    #:
    #: Field names match ``illustration.ImageResult`` exactly, so an adapter is a
    #: dict copy rather than a rename table — and a rename table is where a field
    #: quietly stops being carried. Pinned by test.
    source: AssetSource | None = None

    #: Free-form metadata (dicebear style/seed, etc.). Schema-evolution
    #: friendly: anything an external tool wants to record can land here.
    #:
    #: This comment used to say "art license, etc." — an invitation nothing ever
    #: took up. Rights live in ``source`` now, typed, so they can be found.
    metadata: dict[str, Any] = Field(default_factory=dict)

    def model_post_init(self, __context: Any) -> None:
        # If the caller didn't seed bones/slots/skins, fill in a sensible default
        # rig so a freshly-constructed CharacterDescriptor is immediately usable.
        if not self.bones:
            self.bones = list(_default_bones())
        if not self.slots:
            self.slots = list(_default_slots())
        if not self.skins:
            self.skins = {"default": _default_skin()}
        if not self.animations:
            # Resolved lazily to avoid a circular import with idle.py.
            from an.characters.idle import breath_animation, blink_animation

            self.animations = {
                "idle_breath": breath_animation(),
                "blink": blink_animation(),
            }


# -----------------------------------------------------------------------------
# Default rig builders
# -----------------------------------------------------------------------------


def _default_bones() -> list[Bone]:
    """The 7-bone default rig: root, torso, head, two arms, two legs.

    Coordinates assume a 1024x1024 viewBox with feet near y≈980.
    """
    return [
        Bone(name="root", parent=None, x=512, y=980),
        Bone(name="torso", parent="root", x=0, y=-300),
        Bone(name="head", parent="torso", x=0, y=-260, pivot="neck"),
        Bone(name="arm_l", parent="torso", x=-90, y=-240, pivot="shoulder_l"),
        Bone(name="arm_r", parent="torso", x=90, y=-240, pivot="shoulder_r"),
        Bone(name="leg_l", parent="root", x=-50, y=-10),
        Bone(name="leg_r", parent="root", x=50, y=-10),
    ]


def _default_slots() -> list[Slot]:
    """The 11-slot default draw stack: legs behind, arms in front, face on top."""
    return [
        Slot(name="leg_l", bone="leg_l", draw_order=0, attachment="leg_l"),
        Slot(name="leg_r", bone="leg_r", draw_order=0, attachment="leg_r"),
        Slot(name="torso", bone="torso", draw_order=1, attachment="torso"),
        Slot(name="arm_l", bone="arm_l", draw_order=2, attachment="arm_l"),
        Slot(name="arm_r", bone="arm_r", draw_order=2, attachment="arm_r"),
        Slot(name="head", bone="head", draw_order=4, attachment="head"),
        Slot(name="eye_l", bone="head", draw_order=6, attachment="eye_l_open"),
        Slot(name="eye_r", bone="head", draw_order=6, attachment="eye_r_open"),
        Slot(name="mouth", bone="head", draw_order=7, attachment="mouth_x"),
        Slot(name="brow_l", bone="head", draw_order=8, attachment="brow_l"),
        Slot(name="brow_r", bone="head", draw_order=8, attachment="brow_r"),
    ]


def _default_skin() -> Skin:
    """Default skin wiring slot names → attachment dicts → SVG paths.

    Paths are relative to the descriptor file. Slicing a real source SVG can
    overwrite/extend these; here we declare the canonical inventory so the
    descriptor is internally consistent even before parts exist on disk.
    """
    slots: dict[str, dict[str, Attachment]] = {}

    # Single-attachment body slots
    for slot_name, anchor in (
        ("torso", (0.5, 0.0)),
        ("head", (0.5, 0.78)),
        ("arm_l", (0.5, 0.0)),
        ("arm_r", (0.5, 0.0)),
        ("leg_l", (0.5, 0.0)),
        ("leg_r", (0.5, 0.0)),
        ("brow_l", (0.5, 0.5)),
        ("brow_r", (0.5, 0.5)),
    ):
        slots[slot_name] = {
            slot_name: Attachment(path=f"parts/{slot_name}.svg", anchor=anchor)
        }

    # Eye slots have two attachments (open + closed).
    slots["eye_l"] = {
        "eye_l_open": Attachment(path="parts/eye_l_open.svg", anchor=(0.5, 0.5)),
        "eye_l_closed": Attachment(path="parts/eye_l_closed.svg", anchor=(0.5, 0.5)),
    }
    slots["eye_r"] = {
        "eye_r_open": Attachment(path="parts/eye_r_open.svg", anchor=(0.5, 0.5)),
        "eye_r_closed": Attachment(path="parts/eye_r_closed.svg", anchor=(0.5, 0.5)),
    }

    # Mouth slot has 9 attachments (the viseme set).
    slots["mouth"] = {
        f"mouth_{s}": Attachment(path=f"parts/mouth/mouth_{s}.svg", anchor=(0.5, 0.5))
        for s in MOUTH_SHAPES
    }

    return Skin(name="default", slots=slots)
