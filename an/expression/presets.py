"""Expression presets: our art direction on the axes (an#98).

Every name the compiler's retired brow-tilt table accepted is a preset here
(``amused`` included — live content authors it), plus the two the research
added (``afraid``, ``disgusted``). The FACS action-unit numbers in each ``anchor`` are cross-reference
comments, not sources: no emotion table was transcribed (research
``misc/docs/wave6_research.md`` §3, §8). Gaze is absent from every preset so
the two sources stay independent — "thinking looks up and away" is a gaze
action, not a preset value.

A preset's ``mouth_form`` names the ``viseme@<form>`` set its mouth prefers;
a character that declares none falls back to ``viseme`` with a warning
(:func:`an.expression.binding.resolve_mouth_set`).

>>> preset_axes("happy")["brow_height_l"]
0.2
>>> preset_axes("happy", intensity=0.5)["brow_height_l"]
0.1
>>> preset_axes("happy", axes={"brow_height_l": -1.0})["brow_height_l"]
-1.0
>>> preset_axes(None) == {}
True
>>> PRESETS["skeptical"].mouth_form is None
True
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from an.expression.axes import AXES, clamp_axes


@dataclass(frozen=True)
class Preset:
    """A named expression: axis offsets, the mouth form it prefers, its anchor."""

    name: str
    axes: Mapping[str, float] = field(default_factory=dict)
    #: The `viseme@<form>` set this preset's mouth prefers; ``None`` = `viseme`.
    mouth_form: str | None = None
    #: FACS AU cross-reference (a comment, never a source).
    anchor: str = ""


def _p(name: str, *, height: tuple[float, float], angle: tuple[float, float], lid: tuple[float, float], form: str | None, anchor: str) -> Preset:
    return Preset(
        name,
        axes={
            "brow_height_l": height[0],
            "brow_height_r": height[1],
            "brow_angle_l": angle[0],
            "brow_angle_r": angle[1],
            "lid_open_l": lid[0],
            "lid_open_r": lid[1],
        },
        mouth_form=form,
        anchor=anchor,
    )


PRESETS: dict[str, Preset] = {
    p.name: p
    for p in (
        Preset("neutral", axes={}, mouth_form=None, anchor=""),
        _p("happy", height=(0.2, 0.2), angle=(0.1, 0.1), lid=(-0.2, -0.2), form="happy", anchor="6+12"),
        _p("sad", height=(0.3, 0.3), angle=(0.6, 0.6), lid=(-0.3, -0.3), form="sad", anchor="1+4+15"),
        _p("angry", height=(-0.6, -0.6), angle=(-0.8, -0.8), lid=(0.1, 0.1), form="angry", anchor="4+5+7+23"),
        _p("surprised", height=(1.0, 1.0), angle=(0.0, 0.0), lid=(0.4, 0.4), form="surprised", anchor="1+2+5+26"),
        _p("afraid", height=(0.7, 0.7), angle=(0.5, 0.5), lid=(0.5, 0.5), form="afraid", anchor="1+2+4+5+7+20+26"),
        _p("disgusted", height=(-0.3, -0.3), angle=(-0.3, -0.3), lid=(-0.4, -0.4), form="disgusted", anchor="9+15+17"),
        _p("thinking", height=(0.5, -0.2), angle=(0.3, -0.1), lid=(-0.1, -0.1), form=None, anchor="cartoon convention"),
        _p("skeptical", height=(0.6, -0.3), angle=(0.0, -0.2), lid=(0.0, -0.2), form=None, anchor="cartoon convention"),
        _p("amused", height=(0.1, 0.1), angle=(0.05, 0.05), lid=(-0.1, -0.1), form="happy", anchor="happy at ~0.6"),
    )
}


def known_presets() -> tuple[str, ...]:
    """The preset names, in declaration order.

    >>> known_presets()[:3]
    ('neutral', 'happy', 'sad')
    """
    return tuple(PRESETS)


def preset_axes(
    preset: str | None,
    *,
    axes: Mapping[str, float] | None = None,
    intensity: float = 1.0,
) -> dict[str, float]:
    """The numeric axis offsets an expression asks for: the preset's, with
    ``axes`` layered over them, scaled by ``intensity`` and clamped. Only
    non-zero offsets are returned, so a neutral expression is ``{}``.

    An unknown preset or axis is a ``ValueError`` — validate reports it as an
    error, the compiler refuses it.
    """
    if preset is not None and preset not in PRESETS:
        raise ValueError(
            f"unknown expression preset {preset!r} (known: {', '.join(known_presets())})"
        )
    merged: dict[str, float] = dict(PRESETS[preset].axes) if preset is not None else {}
    for name, value in (axes or {}).items():
        if name not in AXES:
            raise ValueError(
                f"unknown expression axis {name!r} (known: {', '.join(sorted(AXES))})"
            )
        merged[name] = float(value)
    # Clamp BEFORE scaling — the same order the provider uses — so an
    # out-of-range override at half intensity is half the range, not the
    # whole of it (an#98 review).
    clamped = clamp_axes(merged)
    return {k: v for k, v in ((k, v * float(intensity)) for k, v in clamped.items()) if v != 0.0}


def mouth_form_of(preset: str | None) -> str | None:
    """The `viseme@<form>` a preset prefers, or ``None`` for the neutral set.

    >>> mouth_form_of("amused"), mouth_form_of("thinking"), mouth_form_of(None)
    ('happy', None, None)
    """
    if preset is None:
        return None
    return PRESETS[preset].mouth_form
