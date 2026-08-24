"""The 52-coefficient blendshape vocabulary, as an import/export mapping (an#98).

Reproduced only from the MediaPipe "Blendshape V2" model card (Apache-2.0,
https://storage.googleapis.com/mediapipe-assets/Model%20Card%20Blendshape%20V2.pdf,
sha256 ``c8e9cf60a39998f4b341740623917590e050d1c97004e2de4568d84e026445ae``,
appendix "List of predicted blendshapes", transcribed from the rendered page —
the text layer drops the "ft" ligature). Compatible with the ARKit-style
52-coefficient convention; no identifier here carries that name, by rule
(research ``misc/docs/wave6_research.md`` §3).

These are **not rig channels**: most have no cutout meaning. They exist so a
tracked or imported face can be mapped onto the axes in
:mod:`an.expression.axes` and back, unipolar in ``[0, 1]`` with rest 0 and
left/right split — the card's contract.

>>> len(BLENDSHAPE_V2_NAMES)
52
>>> from_blendshapes({"browInnerUp": 1.0, "eyeBlinkLeft": 1.0})
{'brow_height_l': 1.0, 'brow_height_r': 1.0, 'brow_angle_l': 1.0, 'brow_angle_r': 1.0, 'lid_open_l': -1.0}
"""

from __future__ import annotations

from collections.abc import Mapping

from an.expression.axes import clamp_axes

BLENDSHAPE_V2_NAMES: tuple[str, ...] = (
    "browDownLeft",
    "browDownRight",
    "browInnerUp",
    "browOuterUpLeft",
    "browOuterUpRight",
    "cheekPuff",
    "cheekSquintLeft",
    "cheekSquintRight",
    "eyeBlinkLeft",
    "eyeBlinkRight",
    "eyeLookDownLeft",
    "eyeLookDownRight",
    "eyeLookInLeft",
    "eyeLookInRight",
    "eyeLookOutLeft",
    "eyeLookOutRight",
    "eyeLookUpLeft",
    "eyeLookUpRight",
    "eyeSquintLeft",
    "eyeSquintRight",
    "eyeWideLeft",
    "eyeWideRight",
    "jawForward",
    "jawLeft",
    "jawOpen",
    "jawRight",
    "mouthClose",
    "mouthDimpleLeft",
    "mouthDimpleRight",
    "mouthFrownLeft",
    "mouthFrownRight",
    "mouthFunnel",
    "mouthLeft",
    "mouthLowerDownLeft",
    "mouthLowerDownRight",
    "mouthPressLeft",
    "mouthPressRight",
    "mouthPucker",
    "mouthRight",
    "mouthRollLower",
    "mouthRollUpper",
    "mouthShrugLower",
    "mouthShrugUpper",
    "mouthSmileLeft",
    "mouthSmileRight",
    "mouthStretchLeft",
    "mouthStretchRight",
    "mouthUpperUpLeft",
    "mouthUpperUpRight",
    "noseSneerLeft",
    "noseSneerRight",
    "tongueOut",
)

#: coefficient name → (axis, sign). Several coefficients fold onto one axis
#: (`browInnerUp` raises both brows and turns both inner ends up); the rest of
#: the vocabulary has no cutout meaning and maps to nothing.
_TO_AXES: dict[str, tuple[tuple[str, float], ...]] = {
    "browInnerUp": (
        ("brow_height_l", 1.0),
        ("brow_height_r", 1.0),
        ("brow_angle_l", 1.0),
        ("brow_angle_r", 1.0),
    ),
    "browOuterUpLeft": (("brow_height_l", 1.0), ("brow_angle_l", -1.0)),
    "browOuterUpRight": (("brow_height_r", 1.0), ("brow_angle_r", -1.0)),
    "browDownLeft": (("brow_height_l", -1.0), ("brow_angle_l", -1.0)),
    "browDownRight": (("brow_height_r", -1.0), ("brow_angle_r", -1.0)),
    "eyeBlinkLeft": (("lid_open_l", -1.0),),
    "eyeBlinkRight": (("lid_open_r", -1.0),),
    "eyeSquintLeft": (("lid_open_l", -0.5),),
    "eyeSquintRight": (("lid_open_r", -0.5),),
    "eyeWideLeft": (("lid_open_l", 0.5),),
    "eyeWideRight": (("lid_open_r", 0.5),),
    "eyeLookInLeft": (("gaze_x", 1.0),),
    "eyeLookOutLeft": (("gaze_x", -1.0),),
    "eyeLookInRight": (("gaze_x", -1.0),),
    "eyeLookOutRight": (("gaze_x", 1.0),),
    "eyeLookUpLeft": (("gaze_y", -1.0),),
    "eyeLookUpRight": (("gaze_y", -1.0),),
    "eyeLookDownLeft": (("gaze_y", 1.0),),
    "eyeLookDownRight": (("gaze_y", 1.0),),
}


def from_blendshapes(coefficients: Mapping[str, float]) -> dict[str, float]:
    """Fold unipolar coefficients onto the axes (summed, then clamped).

    Unknown names raise — a misspelt coefficient must not vanish quietly.
    """
    out: dict[str, float] = {}
    for name, value in coefficients.items():
        if name not in BLENDSHAPE_V2_NAMES:
            raise ValueError(f"unknown blendshape coefficient {name!r}")
        for axis, sign in _TO_AXES.get(name, ()):
            out[axis] = out.get(axis, 0.0) + sign * float(value)
    return clamp_axes(out)
