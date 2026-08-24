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
- **asset_sets** — ``{channel: {key: attachment_name}}``. What a swap key
  *selects*, layered over ``skins``, which says what art *exists*. The
  ``viseme`` channel is Rhubarb's shape letter → an attachment on the ``mouth``
  slot. (Replaced ``viseme_map`` in schema 0.2.0.)
- **animations** — built-in idle loops (breath, blink) keyed by name.

A slot's name **is** its scene-graph node name, which is why the face slots read
``left_eye`` rather than ``eye_l``; attachment names are a separate, per-slot
namespace — file-derived for single-attachment slots, and shared key-like names
(``open``/``closed`` on both eye slots, 0.3.0) where one swap set must drive
several slots.

>>> char = CharacterDescriptor(name="maya")
>>> char.asset_sets["viseme"]["A"]
'mouth_a'
>>> char.asset_sets["viseme"]["X"]
'mouth_x'
>>> char.view_box
(0, 0, 1024, 1024)
>>> sorted(char.skins["default"].slots.keys())[:3]
['arm_l', 'arm_r', 'head']
"""

from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

from an.ir.assets import AssetSource
from an.ir.migrate import DocumentKind, register_kind, register_migration


CHARACTER_SCHEMA_VERSION = "0.3.0"

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

#: The swap channel lip-sync drives. `viseme` is a conventional set name, not
#: a special case in control flow (an#87): the compiler projects EVERY
#: `asset_sets` channel onto the slots whose attachments its keys name, and
#: the runtime applies any projected channel the same way.
VISEME_CHANNEL: str = "viseme"

#: The swap channel blinks drive. One set serves BOTH eye slots because the
#: eye slots share per-slot attachment names (`open` / `closed`) — the 0.3.0
#: migration renamed them from the file-derived `eye_l_open` spelling for
#: exactly this: a set's keys are looked up per slot, so slots that a single
#: channel must drive together need attachment names in common.
EYELID_CHANNEL: str = "eyelid"

#: Default eyelid-state → attachment-name mapping, shared by both eye slots.
DEFAULT_EYELID_MAP: dict[str, str] = {"OPEN": "open", "CLOSED": "closed"}


def default_asset_sets() -> dict[str, dict[str, str]]:
    """``{channel: {key: attachment_name}}`` for a freshly-built character."""
    return {
        VISEME_CHANNEL: dict(DEFAULT_VISEME_MAP),
        EYELID_CHANNEL: dict(DEFAULT_EYELID_MAP),
    }


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

    #: Offset from the slot's bone, in view_box units.
    #:
    #: **This is where a part's position lives**, and it is the reference data
    #: model's answer, not an invention: DragonBones puts it in
    #: ``display.transform``, Spine in the region attachment's ``{x, y}``, and
    #: in both the *slot* carries no transform at all. It is what lets five face
    #: parts share one ``head`` bone and still land in different places — before
    #: this field they all stacked on the bone, because the descriptor had no
    #: way to say otherwise and the compiler used hardcoded literals instead.
    x: float = 0.0
    y: float = 0.0
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
    #: ``{channel: {key: attachment_name}}`` — what a swap key SELECTS, layered
    #: over ``skins``, which is the SSOT for what art EXISTS. The indirection is
    #: deliberate: a channel key is not an attachment name. Today's viseme map
    #: happens to be one-to-one (9 keys, 9 attachments), but real mouth charts
    #: are many-to-one — ~10 drawings carrying ~40 phonemes — and collapsing the
    #: two namespaces makes the first shared drawing a schema change instead of
    #: a data change. Replaces ``viseme_map`` (schema 0.2.0).
    asset_sets: dict[str, dict[str, str]] = Field(default_factory=default_asset_sets)
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

    #: Whether this character's face is drawn as separate overlay parts
    #: (eyes, brows, mouth as their own slots — the default) or baked into the
    #: head art (DiceBear / external avatars). ``False`` suppresses the face
    #: overlay slots at rig build AND the viseme/emotion channels at dialogue
    #: compile — a baked face has no overlay mouth to drive.
    #:
    #: This is a **declared fact**, replacing the old vendor-name check on
    #: ``metadata.art_provenance`` (an#87): provenance says where art came
    #: from; this says what the art IS. The 0.2.0 → 0.3.0 migration derives it
    #: from the provenance string once, and ``art_provenance`` reverts to pure
    #: provenance/licensing metadata.
    face_overlay: bool = True

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


#: Slot renames carried by the 0.1.0 -> 0.2.0 migration: a slot's name is now
#: its scene-graph node name, so the four face slots take the names the scene
#: already addressed them by.
_SLOT_RENAMES_0_2_0: dict[str, str] = {
    "eye_l": "left_eye",
    "eye_r": "right_eye",
    "brow_l": "left_brow",
    "brow_r": "right_brow",
}


@register_migration(CHARACTER_DOCUMENT_KIND.name, "0.1.0", "0.2.0")
def _character_0_1_0_to_0_2_0(doc: dict[str, Any]) -> dict[str, Any]:
    """`viseme_map` -> `asset_sets["viseme"]`, and slot names become node names.

    Both changes ride one migration because splitting them would break the
    descriptor schema twice and ship two migrations where one does.

    `viseme_map` is popped, not copied: leaving it would let a stale map sit
    beside the live one indefinitely, and every descriptor model sets
    `extra="allow"`, so nothing would ever complain.

    >>> out = _character_0_1_0_to_0_2_0(
    ...     {"schema_version": "0.1.0", "viseme_map": {"A": "mouth_a"},
    ...      "slots": [{"name": "eye_l", "bone": "head"}]}
    ... )
    >>> out["asset_sets"]["viseme"], "viseme_map" in out
    ({'A': 'mouth_a'}, False)
    >>> out["slots"][0]["name"]
    'left_eye'
    """
    viseme_map = doc.pop("viseme_map", None)
    if viseme_map is not None:
        doc.setdefault("asset_sets", {})[VISEME_CHANNEL] = viseme_map

    for slot in doc.get("slots") or ():
        if isinstance(slot, dict) and slot.get("name") in _SLOT_RENAMES_0_2_0:
            slot["name"] = _SLOT_RENAMES_0_2_0[slot["name"]]

    for skin in (doc.get("skins") or {}).values():
        slots = skin.get("slots") if isinstance(skin, dict) else None
        if not isinstance(slots, dict):
            continue
        for old, new in _SLOT_RENAMES_0_2_0.items():
            if old in slots:
                slots[new] = slots.pop(old)

    # Face offsets move from code into data. Before 0.2.0 the five face parts
    # had no way to say where they sat, so the compiler hardcoded four literal
    # pairs; a descriptor migrated from 0.1.0 therefore has the information
    # nowhere else. Seeding only when the attachment has not been given one
    # keeps a hand-authored offset authoritative.
    for skin in (doc.get("skins") or {}).values():
        slots = skin.get("slots") if isinstance(skin, dict) else None
        if not isinstance(slots, dict):
            continue
        for slot_name, offset in FACE_OFFSETS.items():
            for attachment in (slots.get(slot_name) or {}).values():
                if not isinstance(attachment, dict):
                    continue
                attachment.setdefault("x", offset[0])
                attachment.setdefault("y", offset[1])

    doc["schema_version"] = "0.2.0"
    return doc


#: Eye attachment-name renames carried by 0.2.0 -> 0.3.0: both eye slots take
#: the shared per-slot keys `open`/`closed` so ONE `eyelid` set can project
#: onto both. Keyed per slot because the old names were per-side.
_EYE_ATTACHMENT_RENAMES_0_3_0: dict[str, dict[str, str]] = {
    "left_eye": {"eye_l_open": "open", "eye_l_closed": "closed"},
    "right_eye": {"eye_r_open": "open", "eye_r_closed": "closed"},
}

#: `metadata.art_provenance` values that mean the face is baked into the head
#: art. Consumed ONLY by the 0.3.0 migration below — live code reads the
#: declared `face_overlay` field instead (an#87). `external_avatar` never had
#: a writer; it is kept here so any hand-authored descriptor carrying it
#: migrates the way the old special case treated it.
_FACE_BAKED_PROVENANCES_0_3_0: tuple[str, ...] = ("dicebear", "external_avatar")


@register_migration(CHARACTER_DOCUMENT_KIND.name, "0.2.0", "0.3.0")
def _character_0_2_0_to_0_3_0(doc: dict[str, Any]) -> dict[str, Any]:
    """Four coherent changes, one migration (an#87).

    (a) ``face_overlay`` becomes a declared fact, derived once from the old
    ``metadata.art_provenance`` vendor-name check; (b) eye attachment names
    become the shared per-slot keys ``open``/``closed`` (paths unchanged);
    (c) ``asset_sets`` gains the ``eyelid`` channel; (d) stored idle-animation
    tracks are repaired — the 0.2.0 migration renamed slots in ``slots`` and
    ``skins`` but never touched ``animations``, so every stored descriptor
    carried stale ``slot:eye_l.attachment`` targets (latent only because
    nothing consumed the field; PlayAction resolution makes it live).

    >>> out = _character_0_2_0_to_0_3_0(
    ...     {"schema_version": "0.2.0",
    ...      "metadata": {"art_provenance": "dicebear"},
    ...      "skins": {"default": {"slots": {"left_eye": {"eye_l_open": {"path": "parts/eye_l_open.svg"}}}}},
    ...      "slots": [{"name": "left_eye", "bone": "head", "attachment": "eye_l_open"}],
    ...      "animations": {"blink": {"name": "blink", "tracks": [
    ...          {"target": "slot:eye_l.attachment", "type": "step",
    ...           "frames": [[0.0, "eye_l_open"], [0.05, "eye_l_closed"]]}]}}}
    ... )
    >>> out["face_overlay"], out["schema_version"]
    (False, '0.3.0')
    >>> list(out["skins"]["default"]["slots"]["left_eye"])
    ['open']
    >>> out["slots"][0]["attachment"]
    'open'
    >>> out["animations"]["blink"]["tracks"][0]["target"]
    'slot:left_eye.attachment'
    >>> [f[1] for f in out["animations"]["blink"]["tracks"][0]["frames"]]
    ['open', 'closed']
    >>> out["asset_sets"]["eyelid"]
    {'OPEN': 'open', 'CLOSED': 'closed'}
    """
    # (a) the declared face fact, from the retired vendor-name check.
    provenance = (doc.get("metadata") or {}).get("art_provenance")
    doc.setdefault("face_overlay", provenance not in _FACE_BAKED_PROVENANCES_0_3_0)

    # (b) per-slot eye attachment keys, in skins and slot defaults.
    flat_renames = {
        old: new
        for per_slot in _EYE_ATTACHMENT_RENAMES_0_3_0.values()
        for old, new in per_slot.items()
    }
    for skin in (doc.get("skins") or {}).values():
        slots = skin.get("slots") if isinstance(skin, dict) else None
        if not isinstance(slots, dict):
            continue
        for slot_name, renames in _EYE_ATTACHMENT_RENAMES_0_3_0.items():
            attachments = slots.get(slot_name)
            if not isinstance(attachments, dict):
                continue
            for old, new in renames.items():
                if old in attachments:
                    attachments[new] = attachments.pop(old)
    for slot in doc.get("slots") or ():
        if isinstance(slot, dict) and slot.get("attachment") in flat_renames:
            slot["attachment"] = flat_renames[slot["attachment"]]

    # (c) the eyelid set, only where absent — a hand-authored one wins.
    asset_sets = doc.setdefault("asset_sets", {})
    if isinstance(asset_sets, dict):
        asset_sets.setdefault(EYELID_CHANNEL, dict(DEFAULT_EYELID_MAP))

    # (d) repair stored animation tracks: the 0.2.0 slot renames, applied to
    # the targets 0.2.0 missed, plus the (b) attachment renames in frames.
    for anim in (doc.get("animations") or {}).values():
        tracks = anim.get("tracks") if isinstance(anim, dict) else None
        for track in tracks or ():
            if not isinstance(track, dict):
                continue
            target = track.get("target")
            if isinstance(target, str) and target.startswith("slot:"):
                rest = target[len("slot:") :]
                slot_name, _, prop = rest.partition(".")
                if slot_name in _SLOT_RENAMES_0_2_0:
                    track["target"] = f"slot:{_SLOT_RENAMES_0_2_0[slot_name]}.{prop}"
                track["frames"] = [
                    [t, flat_renames.get(v, v) if isinstance(v, str) else v]
                    for t, v in (track.get("frames") or ())
                ]

    doc["schema_version"] = "0.3.0"
    return doc


def _default_bones() -> list[Bone]:
    """The 7-bone default rig: root, torso, head, two arms, two legs.

    Coordinates assume a 1024x1024 viewBox with feet near y≈980.
    """
    return [
        Bone(name="root", parent=None, x=512, y=980, pivot="root"),
        Bone(name="torso", parent="root", x=0, y=-300, pivot="hip"),
        Bone(name="head", parent="torso", x=0, y=-260, pivot="neck"),
        Bone(name="arm_l", parent="torso", x=-90, y=-240, pivot="shoulder_l"),
        Bone(name="arm_r", parent="torso", x=90, y=-240, pivot="shoulder_r"),
        Bone(name="leg_l", parent="root", x=-50, y=-10, pivot="hip_l"),
        Bone(name="leg_r", parent="root", x=50, y=-10, pivot="hip_r"),
    ]


#: Where each face part sits relative to the ``head`` bone, in view_box units.
#:
#: All five share one bone, so without a per-attachment offset they stack on it.
#: These are the compiler's four deleted hardcoded pairs converted at
#: k = 345/1024 — i.e. the same picture, now expressed where an illustrator can
#: change it.
FACE_OFFSETS: dict[str, tuple[float, float]] = {
    "left_eye": (-41.6, -17.8),
    "right_eye": (41.6, -17.8),
    "left_brow": (-41.6, -53.4),
    "right_brow": (41.6, -53.4),
    "mouth": (0.0, 41.6),
}


def _default_slots() -> list[Slot]:
    """The 11-slot default draw stack: legs behind, arms in front, face on top.

    **A slot's name IS its scene-graph node name.** The face slots read
    ``left_eye`` rather than ``eye_l`` for that reason and no other: node paths
    are the authoring surface (``scene.md`` targets ``charlie/left_eye:...``, and
    the doc-targeting test addresses them), so the alternative was a
    slot-to-node rename table — and a rename table is where a field quietly
    stops being carried. Attachment names are a *separate*, per-slot namespace:
    single-attachment slots keep the file-derived spelling (``brow_l``), while
    slots that one swap channel must drive **together** share key-like names —
    both eye slots carry ``open``/``closed`` (0.3.0) so the single ``eyelid``
    set projects onto each. Paths keep the file spelling either way.
    """
    return [
        Slot(name="leg_l", bone="leg_l", draw_order=0, attachment="leg_l"),
        Slot(name="leg_r", bone="leg_r", draw_order=0, attachment="leg_r"),
        Slot(name="torso", bone="torso", draw_order=1, attachment="torso"),
        Slot(name="arm_l", bone="arm_l", draw_order=2, attachment="arm_l"),
        Slot(name="arm_r", bone="arm_r", draw_order=2, attachment="arm_r"),
        Slot(name="head", bone="head", draw_order=4, attachment="head"),
        Slot(name="left_eye", bone="head", draw_order=6, attachment="open"),
        Slot(name="right_eye", bone="head", draw_order=6, attachment="open"),
        Slot(name="mouth", bone="head", draw_order=7, attachment="mouth_x"),
        Slot(name="left_brow", bone="head", draw_order=8, attachment="brow_l"),
        Slot(name="right_brow", bone="head", draw_order=8, attachment="brow_r"),
    ]


def bones_from_pivots(
    pivots: Mapping[str, tuple[float, float]],
    *,
    bones: list[Bone] | None = None,
) -> list[Bone]:
    """Re-place a bone rig onto an illustrator's own joint coordinates.

    Each :class:`Bone` already declares the joint it stands for
    (``head`` -> ``neck``, ``arm_l`` -> ``shoulder_l``, ...), and
    :func:`~an.characters.svg_utils.extract_pivots` already returns those joints
    as ``{name: (cx, cy)}``. Nothing connected the two: `promote` computed the
    pivots and stored **only their names**, so the coordinates an artist drew
    were discarded and every character got the generic rig (an#75).

    Bones a drawing has no joint for keep their default placement, so a partial
    skeleton improves a rig rather than breaking it.

    Positions are stored parent-relative, so an absolute joint is converted
    against its parent's resolved absolute position — and parents are resolved
    first, which is why this walks in declaration order rather than by index.

    >>> bones = bones_from_pivots({"neck": (500.0, 300.0), "root": (500.0, 900.0)})
    >>> head = next(b for b in bones if b.name == "head")
    >>> root = next(b for b in bones if b.name == "root")
    >>> root.x, root.y
    (500.0, 900.0)
    >>> torso = next(b for b in bones if b.name == "torso")
    >>> round(head.y + torso.y + root.y)          # absolute, back to the neck
    300
    """
    rig = [b.model_copy(deep=True) for b in (bones or _default_bones())]
    by_name = {b.name: b for b in rig}

    def absolute(bone: Bone) -> tuple[float, float]:
        x = y = 0.0
        seen: set[str] = set()
        cursor: Bone | None = bone
        while cursor is not None and cursor.name not in seen:
            seen.add(cursor.name)
            x += cursor.x
            y += cursor.y
            cursor = by_name.get(cursor.parent) if cursor.parent else None
        return x, y

    for bone in rig:  # declaration order: a parent is always placed first
        target = pivots.get(bone.pivot) if bone.pivot else None
        if target is None:
            continue
        parent = by_name.get(bone.parent) if bone.parent else None
        base = absolute(parent) if parent is not None else (0.0, 0.0)
        bone.x = target[0] - base[0]
        bone.y = target[1] - base[1]
    return rig


def _default_skin() -> Skin:
    """Default skin wiring slot names → attachment dicts → SVG paths.

    Paths are relative to the descriptor file. Slicing a real source SVG can
    overwrite/extend these; here we declare the canonical inventory so the
    descriptor is internally consistent even before parts exist on disk.
    """
    slots: dict[str, dict[str, Attachment]] = {}

    # Single-attachment body slots
    for slot_name, anchor in (
        # Anchors are stated relative to each slot's BONE. The torso's bone is
        # the hip, so the torso hangs UPWARD from it (anchor at its bottom
        # edge); the limbs' bones are shoulders and hips, so they hang downward
        # (anchor at their top edge). Before the compiler read any of this the
        # anchors were inert and the torso's read (0.5, 0.0) — which, once the
        # bone became the hip, drew the body below the waist and over the legs.
        ("torso", (0.5, 1.0)),
        ("head", (0.5, 0.78)),
        ("arm_l", (0.5, 0.0)),
        ("arm_r", (0.5, 0.0)),
        ("leg_l", (0.5, 0.0)),
        ("leg_r", (0.5, 0.0)),
    ):
        slots[slot_name] = {
            slot_name: Attachment(path=f"parts/{slot_name}.svg", anchor=anchor)
        }

    # Brows: slot name is the node name, attachment name is the file stem.
    for slot_name, attachment in (("left_brow", "brow_l"), ("right_brow", "brow_r")):
        x, y = FACE_OFFSETS[slot_name]
        slots[slot_name] = {
            attachment: Attachment(
                path=f"parts/{attachment}.svg", anchor=(0.5, 0.5), x=x, y=y
            )
        }

    # Eye slots have two attachments. Their names are the shared per-slot
    # keys `open`/`closed` (NOT the file stems) so the one `eyelid` set can
    # project onto both slots; the paths keep the file spelling.
    for slot_name, stem in (("left_eye", "eye_l"), ("right_eye", "eye_r")):
        x, y = FACE_OFFSETS[slot_name]
        slots[slot_name] = {
            state: Attachment(
                path=f"parts/{stem}_{state}.svg", anchor=(0.5, 0.5), x=x, y=y
            )
            for state in ("open", "closed")
        }

    # Mouth slot has 9 attachments (the viseme set).
    mouth_x, mouth_y = FACE_OFFSETS["mouth"]
    slots["mouth"] = {
        f"mouth_{s}": Attachment(
            path=f"parts/mouth/mouth_{s}.svg", anchor=(0.5, 0.5), x=mouth_x, y=mouth_y
        )
        for s in MOUTH_SHAPES
    }

    return Skin(name="default", slots=slots)
