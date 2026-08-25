"""Measuring a pan: does the stage move as planes, or as one rigid image?

**The trap the epic's own sentence sets.** "The planes moved at different
rates" is satisfied by a scene with *no parallax whatsoever*, because a
centre-anchored zoom already produces unequal per-plane displacements. The
honest null hypothesis is **"the camera moved the whole stage as one rigid
image"**, and a zoom has to be excluded rather than assumed away.

**The quantity.** Probe each plane at scene-space ``x = 0`` — the canvas
centre column. There the zoom term vanishes *exactly*:

    Δ_i = s₁ · p_i · D₁          ratio_ij = Δ_i / Δ_j = p_i / p_j   (any zoom)

Under a rigid pan every ratio is exactly 1, so the measurement can tell a
parallaxing stage from a zooming one. Two additions the epic does not require:

- assert ``Δy ≈ 0``, so a pan is distinguishable from a zoom — but only for a
  ZOOM-FREE pan: §4 endorses zoom composing through the pivot, and a pan+zoom
  shot measures a non-zero Δy by design;
- assert the ORDERING ``p_far < p_mid < p_near``, because wrong-order parallax
  is a real bug that a bare inequality passes. It is a **bonus, not a second
  gate**: the zoom false positive satisfies the ordering too. Only the x = 0
  probe excludes a zoom.

Two measurements, deliberately different instruments:

**(a) JSON** — free, on every PR, from the compiled document through
:func:`an.adapters.cutout.timeline.screen_position`. Composed screen space,
not local channel values: a rigid pan on ``root`` leaves every plane's local
``Δx`` at zero.

**(b) Pixels** — on a labelled PR, per-plane centroids over exact-colour
masks. The ``x = 0`` cancellation does **not** reach (b): a centroid sits at
the plane's own offset, not at ``x = 0``, so the fixture holds zoom constant.
And each mask's pixel COUNT is asserted unchanged between frames, because a
plane panning partly off-canvas biases its centroid — measured, that read a
``depth = 2`` plane as 1.975.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

__all__ = [
    "RotatingCamera",
    "PlaneTrack",
    "PanMeasurement",
    "measure_pan_json",
    "plane_centroids",
    "measure_pan_pixels",
    "min_ratio_gap",
]

#: The scene-space column every plane is probed at — see :func:`_probe_point`
#: for why it is this one and not the plane's own origin. Not a tunable: it is
#: the one x where the zoom term cancels exactly, which is the whole reason the
#: measurement can tell a pan from a zoom.
PROBE_X: float = 0.0

#: How near two ratios may be before the tripwire calls the stage flat. A
#: rigid stage puts every ratio at exactly 1.0, so any real separation is far
#: above this; it exists to absorb float noise, not to soften the verdict.
RATIO_EPS: float = 1e-9


@dataclass(frozen=True, slots=True)
class PlaneTrack:
    """One plane's displacement between the two probed times."""

    name: str
    dx: float
    dy: float
    #: The declared parallax factor, when the caller knows it.
    depth: float | None = None


@dataclass(frozen=True, slots=True)
class PanMeasurement:
    """What a pan did, plane by plane."""

    tracks: tuple[PlaneTrack, ...]
    #: The plane every ratio is taken against: always the largest mover, so
    #: the JSON and pixel halves agree and the number does not depend on what
    #: a descriptor declares (:func:`_reference`).
    reference: str

    @property
    def ratios(self) -> dict[str, float]:
        """``{plane: Δ_i / Δ_ref}``. A rigid stage gives 1.0 for every plane.

        >>> m = PanMeasurement((PlaneTrack("a", 10.0, 0.0), PlaneTrack("b", 40.0, 0.0)), "b")
        >>> m.ratios
        {'a': 0.25, 'b': 1.0}
        """
        ref = next(t.dx for t in self.tracks if t.name == self.reference)
        if ref == 0:
            return {t.name: float("nan") for t in self.tracks}
        return {t.name: t.dx / ref for t in self.tracks}

    @property
    def is_rigid(self) -> bool:
        """True when every plane moved by the same amount — the null
        hypothesis, and what a stage with no parallax looks like.

        Computed from the DISPLACEMENTS, not from the ratios, because a stage
        that did not move at all has no ratios (the reference is zero, so every
        ratio is NaN) and is nonetheless as rigid as a stage can be. Reading it
        off the ratios made a pure zoom — which cancels to zero displacement at
        the probe column, exactly as intended — report as parallaxing.

        >>> PanMeasurement((PlaneTrack("a", 10.0, 0.0), PlaneTrack("b", 10.0, 0.0)), "b").is_rigid
        True
        >>> PanMeasurement((PlaneTrack("a", 0.0, 0.0), PlaneTrack("b", 0.0, 0.0)), "b").is_rigid
        True
        >>> PanMeasurement((PlaneTrack("a", 5.0, 0.0), PlaneTrack("b", 10.0, 0.0)), "b").is_rigid
        False
        """
        spread = [t.dx for t in self.tracks]
        return max(spread) - min(spread) <= RATIO_EPS


def min_ratio_gap(ratios: Mapping[str, float]) -> float:
    """The smallest gap between any two planes' ratios.

    This is the number the ledger row reports and the tripwire guards. Zero
    means two planes moved together, which on a stage that declares distinct
    depths means the parallax flattened.

    >>> min_ratio_gap({"far": 0.25, "mid": 1.0, "near": 2.0})
    0.75
    >>> min_ratio_gap({"far": 1.0, "mid": 1.0})
    0.0
    """
    values = sorted(ratios.values())
    if len(values) < 2:
        return 0.0
    return min(b - a for a, b in zip(values, values[1:]))


def measure_pan_json(
    scene: Any,
    plane_paths: Sequence[str],
    times: tuple[float, float],
    *,
    depths: Mapping[str, float] | None = None,
) -> PanMeasurement:
    """Measurement (a): composed screen displacement, probed at ``x = 0``.

    ``plane_paths`` are full node paths (``"depths/far"``); the returned
    tracks are keyed by the LAST segment, which is the plane's own name.
    """
    from an.adapters.cutout.timeline import (
        evaluate_timeline,
        screen_position,
        timeline_from_scene,
    )

    tl = timeline_from_scene(scene)
    t0, t1 = times
    poses = (evaluate_timeline(tl, t0), evaluate_timeline(tl, t1))
    _refuse_a_rotating_camera(poses)
    tracks = []
    for path in plane_paths:
        name = path.rsplit("/", 1)[-1]
        probe = _probe_point(scene, path)
        (x0, y0), (x1, y1) = (
            screen_position(scene, path, pose=p, point=probe) for p in poses
        )
        tracks.append(PlaneTrack(name, x1 - x0, y1 - y0, (depths or {}).get(name)))
    return PanMeasurement(tuple(tracks), _reference(tracks))


class RotatingCamera(RuntimeError):
    """The camera rolls, so a per-axis ratio is not a depth ratio.

    Under a rotation the composed x-displacement is ``−fx·A + fy·B`` with
    plane-independent ``A``/``B``, so the axes mix and ``Δ_i/Δ_j`` stops being
    ``f_i/f_j``. Measured with `rotation = 0.6` and per-axis factors: one plane
    reported a NEGATIVE ratio and the resulting gap was *larger* than the
    honest one, so the tripwire passed on a number that meant nothing
    (an#111 review, M2).

    Refused rather than reported. The measurement's whole job is to exclude a
    camera move that mimics depth; silently reporting one is the failure it
    exists to prevent, pointed the other way.
    """


def _refuse_a_rotating_camera(poses) -> None:
    angles = {float(p.get(("root", "rotation"), 0.0) or 0.0) for p in poses}
    if angles - {0.0}:
        raise RotatingCamera(
            f"the camera rolls (root rotation {sorted(angles)}), which mixes the "
            "x and y displacements, so a plane's ratio is no longer its depth "
            "ratio. Measure a pan without roll, or measure the axes separately "
            "against a camera that moves on one of them."
        )


def _probe_point(scene: Any, path: str) -> tuple[float, float]:
    """The plane-local point whose SCENE-space position is the origin.

    This is the whole trick, and probing a plane's own local origin instead is
    the mistake it replaces. Composing gives

        screen = W/2 + S·(x0 + local − f·cam)

    so choosing ``local = −x0`` leaves ``screen = W/2 − S·f·cam``, whose
    displacement between two times is ``−f·(S₁·cam₁ − S₀·cam₀)`` — proportional
    to ``f`` and to nothing else. The ratio of two planes is then ``f_i/f_j``
    for **any** zoom, which is what lets the measurement tell a parallaxing
    stage from a zooming one.

    Probed at the plane's own origin instead, ``x0`` survives into the
    displacement, a zoom moves planes at different offsets by different amounts,
    and a scene with no depth reads as parallax.

    Computed by INVERTING the whole chain, not by negating the last node's
    ``x``/``y``. The first version did the latter and its docstring claimed the
    former: an ancestor with an offset, or a plane with its own pivot or scale,
    left a residual ``a`` in the displacement, whose ``a·(S₁ − S₀)`` term is
    exactly the zoom that was supposed to cancel (an#111 review, M1). No
    compiler path produces either shape today — both environment containers sit
    at identity — so it was a guarantee resting on an unstated invariant.
    """
    from an.adapters.cutout.timeline import _node_chain, transform_of

    at = (0.0, 0.0)
    # Root first, downward, undoing each transform in turn — and skipping the
    # document root, which the runtime does not apply (see `screen_position`).
    for node, _ in _node_chain(scene.scene, path)[1:]:
        at = transform_of(node).unapply(at)
    return at


def _reference(tracks: Sequence[PlaneTrack]) -> str:
    """The plane ratios are taken against: **always the largest mover.**

    Not the character plane, and not the first declared. The pixel half of the
    measurement cannot see a `depth`, so a depth-aware reference makes the two
    halves report different numbers for the same stage — measured while
    writing this: the JSON half read a gap of 0.75 against the `depth == 1`
    plane while the ledger row read 0.375 against the largest mover, and the
    two are the same measurement.

    The largest mover is derivable from the frames alone, so both halves agree
    and the reported gap does not depend on what the descriptor happens to
    declare. Ties break on name, so declaration order cannot move the number
    either.
    """
    return min(tracks, key=lambda t: (-abs(t.dx), t.name)).name


def plane_centroids(frame: Any, colours: Mapping[str, int]) -> dict[str, tuple[float, float, int]]:
    """``{plane: (cx, cy, pixel count)}`` for exact-colour masks.

    `an/bench/masks.py` is deliberately not reused: it has no colour
    selection. The primitive is `metrics.pack_rgb` plus equality — one integer
    per colour, compared exactly, so a plane's mask cannot pick up an
    anti-aliased edge pixel from its neighbour.
    """
    import numpy as np

    from an.bench.metrics import pack_rgb

    packed = pack_rgb(frame[None, ...])[0]
    out: dict[str, tuple[float, float, int]] = {}
    for name, colour in colours.items():
        ys, xs = np.nonzero(packed == colour)
        count = int(xs.size)
        if count == 0:
            out[name] = (float("nan"), float("nan"), 0)
        else:
            out[name] = (float(xs.mean()), float(ys.mean()), count)
    return out


class ClippedPlane(RuntimeError):
    """A plane's mask changed size between the two frames.

    Its centroid is then measured against a different shape, which is not a
    displacement. Measured in the research: a plane panning partly off-canvas
    read a `depth = 2` ratio as 1.975 — close enough to look right and wrong
    enough to set a tripwire's floor against a number nobody meant.
    """


def measure_pan_pixels(
    frames: tuple[Any, Any],
    colours: Mapping[str, int],
    *,
    depths: Mapping[str, float] | None = None,
) -> PanMeasurement:
    """Measurement (b): per-plane centroid displacement between two frames."""
    first, second = (plane_centroids(f, colours) for f in frames)
    tracks = []
    for name in colours:
        (x0, y0, n0), (x1, y1, n1) = first[name], second[name]
        if n0 == 0 or n1 == 0:
            raise ClippedPlane(
                f"plane {name!r} has {n0} pixels in the first frame and {n1} in the "
                "second; a colour that is absent was either never drawn or fully "
                "clipped, and neither is a displacement."
            )
        if n0 != n1:
            raise ClippedPlane(
                f"plane {name!r} covers {n0} pixels in the first frame and {n1} in "
                "the second, so its centroid is measured against a different shape. "
                "A plane must stay fully on canvas across the pan."
            )
        tracks.append(PlaneTrack(name, x1 - x0, y1 - y0, (depths or {}).get(name)))
    return PanMeasurement(tuple(tracks), _reference(tracks))
