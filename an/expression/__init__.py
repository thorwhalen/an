"""Facial expression for the cutout face (an#98, epic #9 Wave 6).

The vocabulary (:mod:`~an.expression.axes`), our presets
(:mod:`~an.expression.presets`), how they reach a character's slots and which
mouth set a line uses (:mod:`~an.expression.binding`), the provider seam that
turns authored leaves and dialogue sugar into per-frame curves
(:mod:`~an.expression.provider`), and the 52-coefficient import/export
mapping (:mod:`~an.expression.blendshapes`). Renderer-free throughout: the
cutout compiler's face solver consumes these; ``an validate`` and
``an character validate`` share the same resolution.
"""

from an.expression.axes import AXES, BROW_AXES, GAZE_AXES, LID_AXES, Axis, clamp_axes, lid_key
from an.expression.binding import (
    Binding,
    ChannelBinding,
    ExpressionResolutionError,
    SetBinding,
    binding_for,
    declared_mouth_variants,
    default_binding,
    expression_problems,
    resolve_mouth_set,
    variant_set_name,
)
from an.expression.blendshapes import BLENDSHAPE_V2_NAMES, from_blendshapes
from an.expression.presets import PRESETS, Preset, known_presets, mouth_form_of, preset_axes
from an.expression.provider import (
    AxisCurve,
    DefaultExpressionProvider,
    ExpressionProvider,
    ExpressionSpan,
    expression_spans,
)

__all__ = [
    "AXES",
    "BLENDSHAPE_V2_NAMES",
    "BROW_AXES",
    "GAZE_AXES",
    "LID_AXES",
    "PRESETS",
    "Axis",
    "AxisCurve",
    "Binding",
    "ChannelBinding",
    "DefaultExpressionProvider",
    "ExpressionProvider",
    "ExpressionResolutionError",
    "ExpressionSpan",
    "Preset",
    "SetBinding",
    "binding_for",
    "clamp_axes",
    "declared_mouth_variants",
    "default_binding",
    "expression_problems",
    "expression_spans",
    "from_blendshapes",
    "known_presets",
    "lid_key",
    "mouth_form_of",
    "preset_axes",
    "resolve_mouth_set",
    "variant_set_name",
]
