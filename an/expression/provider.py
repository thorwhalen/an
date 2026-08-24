"""The expression provider: authored leaves + dialogue sugar → per-axis curves (an#98).

The face solver in the cutout compiler sums contributors per ``(node,
property)`` at compile time. It gets those contributors from an
:class:`ExpressionProvider` — the seam an audio- or vision-driven source
plugs into later. The default provider composes, for one entity of one shot:

- every ``expression`` leaf action (flattened out of the shot's composition
  tree with its absolute times);
- the **dialogue sugar**: a line's ``[emotion]`` becomes an expression over
  the line, **in memory only** — never written into ``shot.actions`` or the
  scenes store, or the md writer would emit the emotion twice.

Each span ramps its intensity in and out over ``blend`` seconds (0 = cut);
two overlapping spans cross-fade because the sum is additive. Curves are
sampled per frame, so the solver and this module agree on time by
construction.

>>> from an.ir.schema import AssetRef, Dialogue, Shot
>>> from an.ir.compose import expression
>>> shot = Shot(id="s", style="cutout", duration=1.0,
...             entities=[AssetRef(kind="character", id="c", store="characters", ref="c")],
...             actions=[expression("c", "angry", blend=0.0)])
>>> [s.preset for s in expression_spans(shot, "c")]
['angry']
>>> curves = {c.axis: c for c in DefaultExpressionProvider().curves(shot, "c", fps=4)}
>>> curves["brow_angle_l"].samples
(-0.8, -0.8, -0.8, -0.8, -0.8)
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Protocol

from an.expression.presets import mouth_form_of, preset_axes
from an.ir.compose import flatten
from an.ir.schema import ExpressionAction, Shot


@dataclass(frozen=True)
class ExpressionSpan:
    """One expression contributor on one entity, in absolute shot time."""

    start: float
    end: float
    preset: str | None
    axes: dict[str, float] = field(default_factory=dict)
    intensity: float = 1.0
    blend: float = 0.0
    #: ``"action"`` for an authored leaf, ``"dialogue"`` for the `[emotion]` sugar.
    source: str = "action"

    def weight_at(self, t: float) -> float:
        """The ramped intensity at ``t``: 0 outside, ramping over ``blend`` at each end."""
        if t < self.start or t > self.end:
            return 0.0
        w = 1.0
        if self.blend > 0:
            w = min(w, (t - self.start) / self.blend, (self.end - t) / self.blend)
        return max(0.0, min(1.0, w)) * self.intensity

    def offsets(self) -> dict[str, float]:
        """The unscaled axis offsets this span asks for."""
        return preset_axes(self.preset, axes=self.axes)

    @property
    def mouth_form(self) -> str | None:
        return mouth_form_of(self.preset)


def expression_spans(shot: Shot, entity_id: str) -> list[ExpressionSpan]:
    """Every expression contributor on ``entity_id``: authored leaves, then
    dialogue sugar. ``duration=None`` runs to the shot end (the looping-play
    rule); a span never extends past the shot."""
    spans: list[ExpressionSpan] = []
    for flat in flatten_expressions(shot):
        action = flat.action
        if action.target.split("/", 1)[0] != entity_id:
            continue
        end = flat.start + action.duration if action.duration is not None else float(shot.duration)
        spans.append(
            ExpressionSpan(
                start=float(flat.start),
                end=float(min(end, shot.duration)),
                preset=action.preset,
                axes=dict(action.axes),
                intensity=float(action.intensity),
                blend=float(action.blend),
                source="action",
            )
        )
    for line in shot.dialogue:
        emotion = (line.emotion or "").strip().lower()
        if not emotion or line.speaker != entity_id:
            continue
        if line.start is None or line.duration is None:
            continue
        spans.append(
            ExpressionSpan(
                start=float(line.start),
                end=float(min(line.start + line.duration, shot.duration)),
                preset=emotion,
                blend=DIALOGUE_EMOTION_BLEND_S,
                source="dialogue",
            )
        )
    return spans


#: The `[emotion]` sugar ramps in and out over this; it is a comment on the
#: line, not a cut.
DIALOGUE_EMOTION_BLEND_S: float = 0.15


def flatten_expressions(shot: Shot):
    """The shot's ``expression`` leaves with absolute times (other leaves dropped)."""
    out = []
    for action in shot.actions:
        for flat in flatten(action):
            if isinstance(flat.action, ExpressionAction):
                out.append(flat)
    return out


@dataclass(frozen=True)
class AxisCurve:
    """One axis sampled at the frame times ``0, 1/fps, …, n/fps`` (offline, deterministic)."""

    axis: str
    samples: tuple[float, ...]


class ExpressionProvider(Protocol):
    """The seam: whatever produces per-axis curves for one entity of one shot."""

    def curves(self, shot: Shot, entity_id: str, *, fps: int) -> Iterable[AxisCurve]: ...


class DefaultExpressionProvider:
    """Sum of the shot's expression spans on the entity, ramped, per frame."""

    def spans(self, shot: Shot, entity_id: str) -> list[ExpressionSpan]:
        return expression_spans(shot, entity_id)

    def curves(self, shot: Shot, entity_id: str, *, fps: int) -> list[AxisCurve]:
        spans = self.spans(shot, entity_id)
        if not spans:
            return []
        n = int(math.ceil(float(shot.duration) * fps - 1e-9))
        times = [f / fps for f in range(n + 1)]
        per_axis: dict[str, list[float]] = {}
        for span in spans:
            offsets = span.offsets()
            if not offsets:
                continue
            for axis, value in offsets.items():
                acc = per_axis.setdefault(axis, [0.0] * len(times))
                for i, t in enumerate(times):
                    w = span.weight_at(t)
                    if w:
                        acc[i] += value * w
        return [AxisCurve(axis, tuple(samples)) for axis, samples in per_axis.items()]

    def mouth_preset_at(self, shot: Shot, entity_id: str, t: float) -> str | None:
        """The preset whose mouth form is in force at ``t``: the heaviest span
        at ``t`` that prefers a form, or ``None`` (the neutral set).

        Whole-line by construction when called at a line's start — the solver
        asks once per line, never per frame, so at most one mouth swap
        property is live per instant.
        """
        best: tuple[float, str] | None = None
        for span in self.spans(shot, entity_id):
            if span.preset is None or span.mouth_form is None:
                continue
            if not (span.start <= t <= span.end):
                continue
            w = span.intensity
            if w > 0 and (best is None or w >= best[0]):
                best = (w, span.preset)
        return best[1] if best else None


__all__ = [
    "AxisCurve",
    "DIALOGUE_EMOTION_BLEND_S",
    "DefaultExpressionProvider",
    "ExpressionProvider",
    "ExpressionSpan",
    "expression_spans",
    "flatten_expressions",
]
