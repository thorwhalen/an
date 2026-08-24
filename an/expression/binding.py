"""How the axes reach a character: the binding and the mouth-set resolver (an#98).

Renderer-free, like :mod:`an.characters.play` — ``an validate``,
``an character validate`` and the cutout face solver all call the same
functions here, so the three cannot disagree about whether an expression can
resolve on a character.

- A **channel binding** maps a numeric axis onto ``(slot, property, gain)``:
  the solver emits ``rest + Σ axis·gain`` on that slot's node. The brow angle's
  per-side sign lives in the gain — the two sides rotate in opposite screen
  directions for one axis sign.
- A **set binding** maps a lid axis onto a slot's swap set (``eyelid``); the
  solver reads a key off the ladder in :mod:`an.expression.axes`.
- ``resolve_mouth_set`` is the ONE chain for "which mouth set does this line
  use": ``viseme@<form>`` if declared **and** it covers the keys the line
  uses, else ``viseme`` with a warning naming the missing keys, else an
  :class:`ExpressionResolutionError` (a speaking overlay face with no neutral
  mouth set).

>>> from an.characters.schema import CharacterDescriptor
>>> desc = CharacterDescriptor(name="m")
>>> sorted({b.axis for b in default_binding(desc)})
['brow_angle_l', 'brow_angle_r', 'brow_height_l', 'brow_height_r', 'lid_open_l', 'lid_open_r']
>>> resolve_mouth_set(desc, None, keys_used=["A", "X"])
'viseme'
>>> import warnings
>>> with warnings.catch_warnings(record=True) as w:
...     warnings.simplefilter("always")
...     resolve_mouth_set(desc, "happy", keys_used=["A", "X"])
'viseme'
>>> "viseme@happy" in str(w[0].message)
True
"""

from __future__ import annotations

import warnings
from collections.abc import Collection, Iterable
from dataclasses import dataclass
from typing import Union

from an.characters.schema import EYELID_CHANNEL, VISEME_CHANNEL, CharacterDescriptor
from an.expression.axes import AXES, GAZE_AXES
from an.expression.presets import PRESETS, known_presets, mouth_form_of, preset_axes

#: Brow travel per unit of `brow_height_*`, in the rig's view-box units
#: (scaled to scene pixels by the entity's rig factor). Art direction; about
#: the synthesized eye's half-height.
BROW_HEIGHT_TRAVEL: float = 10.0
#: Brow rotation per unit of `brow_angle_*`, radians. Art direction.
BROW_ANGLE_TRAVEL: float = 0.35
#: Pupil travel per unit of `gaze_*`, in view-box units — the default when a
#: descriptor declares no travel of its own (PR-D wires the pupil layer).
GAZE_TRAVEL: float = 6.0
#: On a rig whose eye squashes instead of swapping art, a lid offset scales
#: the eye by this much per unit.
LID_SQUASH_GAIN: float = 0.5

#: Slot names the default binding looks for. A rig that lacks one simply has
#: no binding for that axis — the axis is a no-op on it, which is what lets a
#: pre-Wave-6 descriptor keep rendering unchanged.
LEFT_BROW_SLOT, RIGHT_BROW_SLOT = "left_brow", "right_brow"
LEFT_EYE_SLOT, RIGHT_EYE_SLOT = "left_eye", "right_eye"
LEFT_PUPIL_SLOT, RIGHT_PUPIL_SLOT = "left_pupil", "right_pupil"


class ExpressionResolutionError(ValueError):
    """An expression that cannot resolve on a character; ``problems`` says why."""

    def __init__(self, who: str, problems: Iterable[str]) -> None:
        self.who = who
        self.problems = list(problems)
        super().__init__(f"expression on {who!r}: " + "; ".join(self.problems))


@dataclass(frozen=True)
class ChannelBinding:
    """A numeric axis driving one transform property of one slot's node."""

    axis: str
    slot: str
    property: str
    gain: float
    #: Whether the gain is a view-box length (scaled by the rig factor).
    rig_scaled: bool = False


@dataclass(frozen=True)
class SetBinding:
    """A lid axis driving one slot's swap set through the ladder."""

    axis: str
    slot: str
    set_family: str = EYELID_CHANNEL


Binding = Union[ChannelBinding, SetBinding]


def default_binding(desc: CharacterDescriptor) -> list[Binding]:
    """The binding the default rig implies, from the slots it actually has.

    The brow angle's screen sign: PixiJS rotation is clockwise-positive with y
    down, so on the LEFT brow (screen-left) a clockwise turn drops the inner
    end while on the RIGHT brow it lifts it — the axis says "+ = inner end
    up", hence ``-travel`` on the left and ``+travel`` on the right.
    """
    slots = {s.name for s in desc.slots}
    out: list[Binding] = []
    for axis, slot, sign in (
        ("brow_height_l", LEFT_BROW_SLOT, -1.0),  # up is -y
        ("brow_height_r", RIGHT_BROW_SLOT, -1.0),
    ):
        if slot in slots:
            out.append(ChannelBinding(axis, slot, "y", sign * BROW_HEIGHT_TRAVEL, rig_scaled=True))
    for axis, slot, sign in (
        ("brow_angle_l", LEFT_BROW_SLOT, -1.0),
        ("brow_angle_r", RIGHT_BROW_SLOT, 1.0),
    ):
        if slot in slots:
            out.append(ChannelBinding(axis, slot, "rotation", sign * BROW_ANGLE_TRAVEL))
    for axis, slot in (("lid_open_l", LEFT_EYE_SLOT), ("lid_open_r", RIGHT_EYE_SLOT)):
        if slot in slots:
            out.append(SetBinding(axis, slot, EYELID_CHANNEL))
    for slot in (LEFT_PUPIL_SLOT, RIGHT_PUPIL_SLOT):
        if slot in slots:
            out.append(ChannelBinding("gaze_x", slot, "x", GAZE_TRAVEL, rig_scaled=True))
            out.append(ChannelBinding("gaze_y", slot, "y", GAZE_TRAVEL, rig_scaled=True))
    return out


def binding_for(desc: CharacterDescriptor) -> list[Binding]:
    """The descriptor's declared ``expression_binding`` (additive field), else the default.

    A declared binding is a list of dicts in the two dataclasses' shapes
    (``{"axis", "slot", "property", "gain"[, "rig_scaled"]}`` or
    ``{"axis", "slot", "set_family"}``). An unknown axis in it is an error.
    """
    declared = getattr(desc, "expression_binding", None)
    if not declared:
        return default_binding(desc)
    slots = {s.name for s in desc.slots}
    out: list[Binding] = []
    for raw in declared:
        axis = raw.get("axis")
        if axis not in AXES:
            raise ExpressionResolutionError(
                desc.name, [f"expression_binding names unknown axis {axis!r}"]
            )
        if raw.get("slot") not in slots:
            raise ExpressionResolutionError(
                desc.name,
                [f"expression_binding maps {axis!r} onto slot {raw.get('slot')!r}, "
                 f"which the rig does not declare (slots: {sorted(slots)})"],
            )
        if "set_family" in raw:
            out.append(SetBinding(axis, str(raw["slot"]), str(raw["set_family"])))
        else:
            out.append(
                ChannelBinding(
                    axis,
                    str(raw["slot"]),
                    str(raw["property"]),
                    float(raw["gain"]),
                    rig_scaled=bool(raw.get("rig_scaled", False)),
                )
            )
    return out


def variant_set_name(form: str) -> str:
    """The swap-set name for a mouth form (``@`` is a legal set-name character).

    >>> variant_set_name("happy")
    'viseme@happy'
    """
    return f"{VISEME_CHANNEL}@{form}"


def declared_mouth_variants(desc: CharacterDescriptor) -> dict[str, str]:
    """``{form: set name}`` for every ``viseme@<form>`` set the descriptor declares.

    >>> declared_mouth_variants(CharacterDescriptor(name="m"))
    {}
    """
    prefix = VISEME_CHANNEL + "@"
    return {
        name[len(prefix):]: name
        for name in desc.asset_sets
        if name.startswith(prefix) and len(name) > len(prefix)
    }


def resolve_mouth_set(
    desc: CharacterDescriptor,
    preset: str | None,
    *,
    keys_used: Collection[str],
    who: str | None = None,
) -> str:
    """Which mouth set a line under ``preset`` uses — the one chain, shared.

    ``viseme@<form>`` if the preset prefers a form the descriptor declares and
    that set covers ``keys_used``; else ``viseme`` with a warning naming what
    was missing; else :class:`ExpressionResolutionError`. A descriptor with no
    ``viseme`` set and no covering variant cannot speak at all — that is the
    error, not a fallback.
    """
    who = who or desc.name
    form = mouth_form_of(preset)
    keys = {str(k).upper() for k in keys_used}
    if form is not None:
        variant = variant_set_name(form)
        declared = desc.asset_sets.get(variant)
        if declared is None:
            reason = f"declares no {variant!r} set"
        else:
            missing = sorted(keys - {str(k).upper() for k in declared})
            if not missing:
                return variant
            reason = f"{variant!r} lacks the keys {missing} this line uses"
        if VISEME_CHANNEL in desc.asset_sets:
            warnings.warn(
                f"{who} {reason}; the neutral {VISEME_CHANNEL!r} set is used under "
                f"the {preset!r} expression instead (give the character a "
                f"{variant!r} mouth set to change that: `an character mouths "
                f"--variants {form}`)",
                stacklevel=2,
            )
            return VISEME_CHANNEL
        raise ExpressionResolutionError(
            who, [f"{reason}, and there is no {VISEME_CHANNEL!r} set to fall back on"]
        )
    if VISEME_CHANNEL not in desc.asset_sets:
        raise ExpressionResolutionError(
            who, [f"declares no {VISEME_CHANNEL!r} mouth set, so it cannot speak"]
        )
    return VISEME_CHANNEL


def expression_problems(
    desc: CharacterDescriptor | None,
    *,
    preset: str | None,
    axes: Collection[str] = (),
    who: str,
) -> list[str]:
    """Every reason an expression cannot resolve on ``desc`` — empty means it can.

    Shared by ``an validate`` (each becomes an error Finding) and the compiler
    (which raises :class:`ExpressionResolutionError` with the same list).

    >>> expression_problems(CharacterDescriptor(name="m"), preset="joyful", who="m")
    ["unknown expression preset 'joyful' (known: neutral, happy, sad, angry, surprised, afraid, disgusted, thinking, skeptical, amused)"]
    >>> expression_problems(CharacterDescriptor(name="m", face_overlay=False), preset="happy", who="m")[0].startswith("'m' has its face baked")
    True
    """
    problems: list[str] = []
    if preset is not None and preset not in PRESETS:
        problems.append(
            f"unknown expression preset {preset!r} (known: {', '.join(known_presets())})"
        )
    for axis in axes:
        if axis not in AXES:
            problems.append(
                f"unknown expression axis {axis!r} (known: {', '.join(sorted(AXES))})"
            )
    if desc is not None:
        try:
            binding_for(desc)
        except ExpressionResolutionError as e:
            problems.extend(e.problems)
    if desc is not None and not desc.face_overlay:
        problems.append(
            f"{who!r} has its face baked into the head art (face_overlay: false), so "
            "there is no brow, lid or pupil node for an expression to move. The "
            "exit: `an character promote` a hand-drawn rig with overlay face parts "
            "(or `an character new --offline`, whose synthesized face is overlay art)."
        )
    return problems


def touches_gaze(axes: Collection[str]) -> bool:
    """Whether any of ``axes`` is a gaze axis (a no-op on a rig without pupils).

    >>> touches_gaze(["gaze_x"]), touches_gaze(["brow_angle_l"])
    (True, False)
    """
    return any(a in GAZE_AXES for a in axes)


__all__ = [
    "BROW_ANGLE_TRAVEL",
    "BROW_HEIGHT_TRAVEL",
    "Binding",
    "ChannelBinding",
    "ExpressionResolutionError",
    "GAZE_TRAVEL",
    "LID_SQUASH_GAIN",
    "SetBinding",
    "binding_for",
    "declared_mouth_variants",
    "default_binding",
    "expression_problems",
    "preset_axes",
    "resolve_mouth_set",
    "touches_gaze",
    "variant_set_name",
]
