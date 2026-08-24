"""Resolve a ``play`` against a character descriptor — the renderer-free half (an#7).

A :class:`~an.ir.schema.PlayAction` names a descriptor animation. Its tracks
speak the DESCRIPTOR's vocabulary — bones, slots, attachment names, view-box
units, degrees — while the renderer's channels speak the SCENE's: node paths,
swap-set keys, scene pixels, radians. This module does everything on the
descriptor side of that line and knows no renderer, so that ``an validate``
and the cutout compiler share ONE verdict on whether a play can resolve. The
compiler used to decide alone, and validate passed plays that compile then
refused — four measured cases: an unknown bone property, a bone with no slot
of its own, a frame naming art that is not on disk, and a slot suppressed by
``face_overlay=false`` (an#7 review).

Every rule mirrors a rig-builder fact, and the builder imports the shared
helpers rather than restating them, so the two cannot drift:

- A bone track animates the node of the bone's **primary slot** — the slot
  named like the bone (:func:`primary_slot_per_bone`); ``bone:root.*``
  animates the entity container. A bone with no primary slot is a resolution
  error that *says so*: the old message ("no node of that name was built")
  named the symptom and left the rule for the author to guess.
- A slot track resolves to exactly **one** swap set: the set whose keys name
  every frame's attachment. Resolving frame-by-frame used to split a track
  across two channels; the runtime applies a pose's properties in name order,
  so ``blink`` never closed once a second set that sorted before ``eyelid``
  also named ``open``. Two candidates is an error naming both.
- Art is consulted when the caller can consult it (``art_exists``): a frame
  whose attachment is declared but not on disk is reported as exactly that,
  not as "no set resolves it".

>>> from an.characters.schema import CharacterDescriptor
>>> desc = CharacterDescriptor(name="maya")
>>> resolved = resolve_play(desc, "blink")
>>> [(t.slot, t.set_name) for t in resolved.tracks]
[('left_eye', 'eyelid'), ('right_eye', 'eyelid')]
>>> play_problems(desc, "walk")
["the descriptor declares no animation 'walk' (it has: ['blink', 'idle_breath'])"]
"""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Union

from an.characters.idle import evaluate_track
from an.characters.schema import (
    AnimationTrack,
    Attachment,
    CharacterDescriptor,
    IdleAnimation,
    Skin,
    Slot,
)

#: Descriptor bone-track properties → ``(runtime property, unit factor)``.
#: The descriptor speaks degrees for rotation; the runtime is radians.
BONE_TRACK_PROPERTIES: dict[str, tuple[str, float]] = {
    "x": ("x", 1.0),
    "y": ("y", 1.0),
    "rotation_deg": ("rotation", math.pi / 180.0),
    "scale_x": ("scale_x", 1.0),
    "scale_y": ("scale_y", 1.0),
}

#: Bone-track properties whose values are view-box LENGTHS, so a renderer
#: scales them by the rig's view-box → scene-pixel factor. Scales and angles
#: are dimensionless.
RIG_SCALED_PROPERTIES: frozenset[str] = frozenset({"x", "y"})

#: The bone that stands for the whole rig: a track on it animates the entity's
#: container node rather than any slot.
ROOT_BONE = "root"

#: The bone whose primary slot's nested slots are the FACE — what
#: ``face_overlay=false`` suppresses.
HEAD_BONE = "head"


class PlayResolutionError(ValueError):
    """A ``play`` that cannot resolve; ``problems`` lists every reason found."""

    def __init__(self, animation: str, problems: list[str]) -> None:
        self.animation = animation
        self.problems = list(problems)
        super().__init__(f"play of {animation!r}: " + "; ".join(self.problems))


@dataclass(frozen=True)
class BoneTrack:
    """A resolved ``bone:<name>.<prop>`` track.

    ``slot`` is the primary slot whose node carries the bone, or ``None`` for
    the entity container (``bone:root``). ``property`` is the RUNTIME name;
    values are ``rest + deviation * unit`` (times the rig's pixel factor when
    ``rig_scaled``).
    """

    track: AnimationTrack
    slot: str | None
    property: str
    unit: float
    rig_scaled: bool


@dataclass(frozen=True)
class SlotTrack:
    """A resolved ``slot:<name>.attachment`` track: one set, frames as KEYS."""

    track: AnimationTrack
    slot: str
    set_name: str
    frames: tuple[tuple[float, str], ...]


ResolvedTrack = Union[BoneTrack, SlotTrack]


@dataclass(frozen=True)
class ResolvedPlay:
    animation: IdleAnimation
    tracks: tuple[ResolvedTrack, ...]


# ----------------------------------------------------------------- rig facts


def primary_slot_per_bone(desc: CharacterDescriptor) -> dict[str, str]:
    """``{bone name: the slot that IS that bone}``, when one exists.

    Used for node nesting, which is deliberately **not** the bone hierarchy.
    The rigs here are flat by design — arms are siblings of the torso, not
    children (CLAUDE.md pillar 4) — so bone parentage decides *position* only.
    A slot nests under the primary slot of its bone when it is not that slot
    itself, which is what puts eyes and mouth under ``head`` and leaves every
    limb a direct child of the entity.

    >>> primary_slot_per_bone(CharacterDescriptor(name="m"))["head"]
    'head'
    """
    return {s.bone: s.name for s in desc.slots if s.name == s.bone}


def slot_parent(desc: CharacterDescriptor, slot: Slot) -> str | None:
    """The slot ``slot`` nests under, or ``None`` when it is a direct child."""
    parent = primary_slot_per_bone(desc).get(slot.bone)
    return parent if parent is not None and parent != slot.name else None


def slot_node_path(desc: CharacterDescriptor, slot_name: str) -> str:
    """The node path of a slot RELATIVE to its entity (``head/left_eye``,
    ``torso``) — the rig builder's nesting rule, stated once.

    >>> slot_node_path(CharacterDescriptor(name="m"), "left_eye")
    'head/left_eye'
    >>> slot_node_path(CharacterDescriptor(name="m"), "torso")
    'torso'
    """
    slot = _slot_named(desc, slot_name)
    if slot is None:
        raise KeyError(slot_name)
    parent = slot_parent(desc, slot)
    return f"{parent}/{slot_name}" if parent else slot_name


def suppressed_slots(desc: CharacterDescriptor) -> frozenset[str]:
    """Slots the rig builder never builds: with the face baked into the head
    art (``face_overlay=false``), every slot nested under the HEAD BONE's
    primary slot — keyed on the bone, not on a slot named "head".

    >>> sorted(suppressed_slots(CharacterDescriptor(name="m", face_overlay=False)))
    ['left_brow', 'left_eye', 'mouth', 'right_brow', 'right_eye']
    >>> suppressed_slots(CharacterDescriptor(name="m"))
    frozenset()
    """
    if desc.face_overlay:
        return frozenset()
    head_slot = primary_slot_per_bone(desc).get(HEAD_BONE)
    if head_slot is None:
        return frozenset()
    return frozenset(s.name for s in desc.slots if slot_parent(desc, s) == head_slot)


def active_skin(desc: CharacterDescriptor) -> Skin:
    """The skin the rig draws: ``default``, else the first declared, else empty."""
    return desc.skins.get("default") or next(iter(desc.skins.values()), Skin())


def drawn_attachment(
    desc: CharacterDescriptor, skin: Skin, slot: Slot
) -> tuple[str, Attachment] | None:
    """The ``(name, attachment)`` a slot draws by default, or ``None``."""
    available = skin.slots.get(slot.name) or {}
    if not available:
        return None
    name = slot.attachment if slot.attachment in available else next(iter(available))
    return name, available[name]


def art_exists_for(characters_store: Mapping, ref: str) -> Callable[[str], bool] | None:
    """``rel_path -> is the art on disk``, for a character in a filesystem
    store; ``None`` when the store has no root to look under (a dict, a
    fake) — a store that can answer nothing must assume presence, not absence,
    exactly as the rig builder's part probe does.
    """
    root = getattr(characters_store, "_root", None)
    if root is None:
        return None
    base = Path(root) / ref

    def exists(rel_path: str) -> bool:
        return (base / rel_path).is_file()

    return exists


# ----------------------------------------------------------------- resolution


def play_problems(
    desc: CharacterDescriptor,
    animation: str,
    *,
    art_exists: Callable[[str], bool] | None = None,
) -> list[str]:
    """Every reason ``play(<entity>, animation)`` cannot resolve — empty when
    it can. The validate-facing spelling of :func:`resolve_play`.
    """
    try:
        resolve_play(desc, animation, art_exists=art_exists)
    except PlayResolutionError as e:
        return list(e.problems)
    return []


def resolve_play(
    desc: CharacterDescriptor,
    animation: str,
    *,
    art_exists: Callable[[str], bool] | None = None,
) -> ResolvedPlay:
    """Resolve ``animation`` of ``desc`` into renderer-ready tracks, or raise
    :class:`PlayResolutionError` listing every problem found.

    ``art_exists(rel_path)`` answers whether a skin attachment's art is on
    disk; pass ``None`` when the caller cannot know, and every declared
    attachment is assumed present (the rig builder's own rule for a store
    without a filesystem root).
    """
    anim = desc.animations.get(animation)
    if anim is None:
        raise PlayResolutionError(
            animation,
            [
                f"the descriptor declares no animation {animation!r} "
                f"(it has: {sorted(desc.animations)})"
            ],
        )
    ctx = _RigFacts.of(desc, art_exists)
    problems: list[str] = []
    tracks: list[ResolvedTrack] = []
    for track in anim.tracks:
        kind, _, rest = track.target.partition(":")
        name, _, prop = rest.partition(".")
        if kind == "bone":
            out = _resolve_bone_track(track, name, prop, ctx, problems)
        elif kind == "slot" and prop == "attachment":
            out = _resolve_slot_track(track, name, ctx, problems)
        else:
            problems.append(
                f"track {track.target!r} has an unsupported target "
                "(bone:<name>.<prop> or slot:<name>.attachment)"
            )
            out = None
        if out is not None:
            tracks.append(out)
    if problems:
        raise PlayResolutionError(animation, problems)
    return ResolvedPlay(animation=anim, tracks=tuple(tracks))


@dataclass(frozen=True)
class _RigFacts:
    """What the rig builder would build from ``desc`` — derived once per resolve."""

    desc: CharacterDescriptor
    skin: Skin
    primary: dict[str, str]
    suppressed: frozenset[str]
    art_exists: Callable[[str], bool] | None

    @classmethod
    def of(
        cls, desc: CharacterDescriptor, art_exists: Callable[[str], bool] | None
    ) -> "_RigFacts":
        return cls(
            desc=desc,
            skin=active_skin(desc),
            primary=primary_slot_per_bone(desc),
            suppressed=suppressed_slots(desc),
            art_exists=art_exists,
        )

    def has_art(self, attachment: Attachment) -> bool:
        return self.art_exists is None or self.art_exists(attachment.path)

    def unbuilt_reason(self, slot_name: str) -> str | None:
        """Why the rig builder would NOT build ``slot_name``'s node, or None."""
        slot = _slot_named(self.desc, slot_name)
        if slot is None:
            return (
                f"the descriptor declares no slot {slot_name!r} "
                f"(slots: {sorted(s.name for s in self.desc.slots)})"
            )
        if slot_name in self.suppressed:
            return (
                f"slot {slot_name!r} is suppressed: face_overlay=false bakes "
                "the face into the head art, so its node is never built"
            )
        drawn = drawn_attachment(self.desc, self.skin, slot)
        if drawn is None:
            return (
                f"slot {slot_name!r} has no attachment in the skin, so it draws nothing"
            )
        drawn_name, attachment = drawn
        if not self.has_art(attachment):
            return (
                f"slot {slot_name!r} draws nothing: its attachment "
                f"{drawn_name!r} art {attachment.path!r} is not on disk"
            )
        parent = slot_parent(self.desc, slot)
        if parent is not None:
            why = self.unbuilt_reason(parent)
            if why is not None:
                return f"slot {slot_name!r} nests under an unbuilt slot: {why}"
        return None


def _slot_named(desc: CharacterDescriptor, name: str) -> Slot | None:
    return next((s for s in desc.slots if s.name == name), None)


def _resolve_bone_track(
    track: AnimationTrack,
    bone: str,
    prop: str,
    ctx: _RigFacts,
    problems: list[str],
) -> BoneTrack | None:
    ok = True
    if prop not in BONE_TRACK_PROPERTIES:
        problems.append(
            f"track {track.target!r}: bone property {prop!r} is not animatable "
            f"(known: {sorted(BONE_TRACK_PROPERTIES)})"
        )
        ok = False
    slot = ctx.primary.get(bone)
    if slot is not None:
        why = ctx.unbuilt_reason(slot)
        if why is not None:
            problems.append(
                f"track {track.target!r}: bone {bone!r} animates its primary "
                f"slot {slot!r}, which is not built — {why}"
            )
            ok = False
    elif bone == ROOT_BONE:
        slot = None
    else:
        declared = {b.name for b in ctx.desc.bones}
        if bone in declared:
            owned = sorted(s.name for s in ctx.desc.slots if s.bone == bone)
            problems.append(
                f"track {track.target!r}: bone {bone!r} has no primary slot "
                f"(a slot named {bone!r}) whose node could carry it; its slots "
                f"are {owned} — a bone track moves the node of its same-named "
                "slot, or the entity container for 'root'"
            )
        else:
            problems.append(
                f"track {track.target!r}: the descriptor declares no bone "
                f"{bone!r} (bones: {sorted(declared)})"
            )
        ok = False
    if track.type in ("step", "linear"):
        for ft, fv in track.frames:
            if isinstance(fv, bool) or not isinstance(fv, (int, float)):
                problems.append(
                    f"track {track.target!r}: frame at t={ft} has value {fv!r}; "
                    "a bone track's values must be numbers"
                )
                ok = False
                break
    if not ok:
        return None
    runtime_prop, unit = BONE_TRACK_PROPERTIES[prop]
    return BoneTrack(
        track=track,
        slot=slot,
        property=runtime_prop,
        unit=unit,
        rig_scaled=prop in RIG_SCALED_PROPERTIES,
    )


def _resolve_slot_track(
    track: AnimationTrack, slot_name: str, ctx: _RigFacts, problems: list[str]
) -> SlotTrack | None:
    if track.type not in ("step", "linear"):
        problems.append(
            f"track {track.target!r}: an attachment track must be step or "
            f"linear frames naming attachments, not {track.type!r}"
        )
        return None
    why = ctx.unbuilt_reason(slot_name)
    if why is not None:
        problems.append(f"track {track.target!r}: {why}")
        return None
    inventory = ctx.skin.slots.get(slot_name) or {}
    wanted: list[str] = []
    for ft, attachment in track.frames:
        if not isinstance(attachment, str):
            problems.append(
                f"track {track.target!r}: frame at t={ft} has value "
                f"{attachment!r}; an attachment track's values name attachments"
            )
            return None
        if attachment not in wanted:
            wanted.append(attachment)
    if not wanted:
        problems.append(f"track {track.target!r} has no frames")
        return None
    # Sets that name EVERY wanted attachment — the projection the rig builder
    # stamps on this slot's node covers the whole track, or the track does
    # not resolve to that set at all.
    sets_naming = {
        name: sorted(k for k, att in key_map.items() if att in wanted)
        for name, key_map in ctx.desc.asset_sets.items()
    }
    covering = sorted(
        name
        for name, key_map in ctx.desc.asset_sets.items()
        if all(att in key_map.values() for att in wanted)
    )
    for attachment in wanted:
        if attachment not in inventory:
            problems.append(
                f"track {track.target!r}: names attachment {attachment!r}, which "
                f"the skin's slot {slot_name!r} does not carry "
                f"(it has: {sorted(inventory)})"
            )
            return None
        if not ctx.has_art(inventory[attachment]):
            named_by = sorted(n for n, keys in sets_naming.items() if keys)
            problems.append(
                f"track {track.target!r}: attachment {attachment!r} of slot "
                f"{slot_name!r} is declared (by set(s) {named_by}) but its art "
                f"{inventory[attachment].path!r} is not on disk"
            )
            return None
    on_slot = sorted(
        name
        for name, key_map in ctx.desc.asset_sets.items()
        if any(att in inventory for att in key_map.values())
    )
    if not covering:
        problems.append(
            f"track {track.target!r}: no declared asset set maps a key to "
            f"every attachment it names ({wanted}); sets projecting onto "
            f"{slot_name!r}: {on_slot}"
        )
        return None
    if len(covering) > 1:
        problems.append(
            f"track {track.target!r}: attachments {wanted} are named by more "
            f"than one asset set ({covering}); a track resolves to exactly one "
            "set — give each set its own attachment names, or split the track"
        )
        return None
    (set_name,) = covering
    key_map = ctx.desc.asset_sets[set_name]
    key_of = {att: min(k for k, a in key_map.items() if a == att) for att in wanted}
    frames = tuple((float(ft), key_of[att]) for ft, att in track.frames)
    return SlotTrack(track=track, slot=slot_name, set_name=set_name, frames=frames)


def sine_sample_times(duration: float, fps: int) -> list[float]:
    """Frame-rate sample times for a sine track, ALWAYS ending at ``duration``.

    ``ceil`` rather than ``round``: with ``round``, a 0.18 s track at 24 fps
    got samples up to 0.1667 s and then held that value to the clip end, so
    the cycle-closing sample (equal to the first) was never emitted and the
    clip wrapped with a jump (an#7 review).

    >>> sine_sample_times(0.19, 24)[-2:]
    [0.16666666666666666, 0.19]
    >>> len(sine_sample_times(6.0, 24))
    145
    """
    duration = max(0.0, float(duration))
    n = max(1, math.ceil(duration * fps - 1e-9))
    return [min(duration, i / fps) for i in range(n + 1)]


def sampled_deviations(
    track: AnimationTrack, duration: float, fps: int
) -> list[tuple[float, float]]:
    """``(time, deviation)`` pairs for a sine bone track at the frame rate —
    :func:`an.characters.idle.evaluate_track`'s formula, sampled, so the
    descriptor's own evaluator stays the one definition of a sine track."""
    return [
        (t, float(evaluate_track(track, t, duration)))
        for t in sine_sample_times(duration, fps)
    ]


__all__ = [
    "BONE_TRACK_PROPERTIES",
    "BoneTrack",
    "HEAD_BONE",
    "PlayResolutionError",
    "RIG_SCALED_PROPERTIES",
    "ROOT_BONE",
    "ResolvedPlay",
    "SlotTrack",
    "active_skin",
    "art_exists_for",
    "drawn_attachment",
    "play_problems",
    "primary_slot_per_bone",
    "resolve_play",
    "sampled_deviations",
    "sine_sample_times",
    "slot_node_path",
    "slot_parent",
    "suppressed_slots",
]
