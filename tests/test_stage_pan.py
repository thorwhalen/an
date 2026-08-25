"""Measuring the pan (an#111) — and the trap the measurement is shaped around.

"The planes moved at different rates" is satisfied by a scene with **no
parallax whatsoever**, because a centre-anchored zoom already gives unequal
per-plane displacements. The honest null hypothesis is "the camera moved the
whole stage as one rigid image", and a zoom has to be *excluded* rather than
assumed away.

Two instruments, deliberately different:

- **JSON**, free on every PR, probing at scene-space `x = 0` — the one column
  where the zoom term cancels exactly, so a zoom cannot masquerade as depth.
- **Pixels**, on the labelled lane, as per-plane centroids over exact-colour
  masks. The `x = 0` cancellation does NOT reach this half — a centroid sits at
  the plane's own offset — so the fixture holds zoom constant instead.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from an.adapters.cutout.compile import compile_shot
from an.bench.stage import (
    ClippedPlane,
    PanMeasurement,
    PlaneTrack,
    measure_pan_json,
    measure_pan_pixels,
    min_ratio_gap,
    plane_centroids,
)
from an.environments import EnvironmentDescriptor, Plane, PlaneArt
from an.ir.schema import AssetRef, Camera, CameraKey, Shot

W, H = 320, 240
DEPTHS = {"far": 0.25, "mid": 1.0, "near": 2.0}
COLOURS = {"far": 0x204080, "mid": 0x40A060, "near": 0xC04020}


def _env(depths=DEPTHS) -> dict:
    return json.loads(
        EnvironmentDescriptor(
            name="depths",
            planes=[
                Plane(
                    name=name,
                    art=PlaneArt(kind="fill", color=f"#{COLOURS[name]:06x}"),
                    depth=depth,
                    offset=(60.0, y),
                    size=(40.0, 50.0),
                )
                for (name, depth), y in zip(depths.items(), (-70.0, 0.0, 70.0))
            ],
        ).model_dump_json()
    )


def _compiled(camera, *, depths=DEPTHS, duration=0.5):
    shot = Shot(
        id="pan",
        renderer="cutout",
        duration=duration,
        camera=camera,
        entities=[AssetRef(kind="environment", id="depths", store="environments", ref="depths")],
    )
    return compile_shot(
        shot, mall={"environments": {"depths": _env(depths)}}, fps=24, width=W, height=H
    )


def _measure(camera, **kw):
    return measure_pan_json(
        _compiled(camera, **kw),
        [f"depths/{n}" for n in DEPTHS],
        (0.0, kw.get("duration", 0.5)),
        depths=DEPTHS,
    )


PAN = Camera(keys=[CameraKey(at=0.0, easing="linear"), CameraKey(at=0.5, x=60.0)])


# --- the trap ----------------------------------------------------------------


def test_a_zoom_alone_produces_unequal_displacements_and_is_NOT_parallax():
    """The false positive the epic's own sentence admits.

    A centre-anchored zoom moves every plane by a different amount — because
    each sits at a different offset from the centre — so "the displacements
    differ" is true of a stage with no depth at all. This is the measurement's
    null hypothesis, written as a test so it cannot be forgotten.
    """
    import json

    from an.adapters.cutout.timeline import (
        evaluate_timeline,
        screen_position,
        timeline_from_scene,
    )
    from an.bench.stage import measure_pan_json

    # Planes at DIFFERENT offsets, all at the character depth: no parallax
    # anywhere in this stage.
    env = json.loads(
        EnvironmentDescriptor(
            name="d",
            planes=[
                Plane(name=n, art=PlaneArt(color=c), depth=1.0, offset=(x, y), size=(30.0, 30.0))
                for n, c, x, y in (
                    ("a", "#204080", -100.0, -60.0),
                    ("b", "#40a060", 0.0, 0.0),
                    ("c", "#c04020", 120.0, 60.0),
                )
            ],
        ).model_dump_json()
    )
    shot = Shot(
        id="s",
        renderer="cutout",
        duration=0.5,
        camera=Camera(keys=[CameraKey(at=0.0, easing="linear"), CameraKey(at=0.5, zoom=1.5)]),
        entities=[AssetRef(kind="environment", id="d", store="environments", ref="d")],
    )
    scene = compile_shot(shot, mall={"environments": {"d": env}}, fps=24, width=W, height=H)

    # Probed at each plane's OWN origin — the obvious choice — a pure zoom
    # moves them by different amounts, because each sits at a different offset
    # from the centre. That is the false positive: no depth, unequal
    # displacements.
    tl = timeline_from_scene(scene)
    naive = {
        n: screen_position(scene, f"d/{n}", pose=evaluate_timeline(tl, 0.5))[0]
        - screen_position(scene, f"d/{n}", pose=evaluate_timeline(tl, 0.0))[0]
        for n in ("a", "b", "c")
    }
    assert len({round(v, 6) for v in naive.values()}) > 1, naive

    # Probed at scene-space x = 0 it does not: the zoom term cancels exactly,
    # every plane reads the same displacement, and the stage is correctly
    # called rigid.
    m = measure_pan_json(scene, ["d/a", "d/b", "d/c"], (0.0, 0.5))
    assert m.is_rigid, {t.name: t.dx for t in m.tracks}


def test_a_real_pan_gives_the_declared_depths_back():
    m = _measure(PAN)
    # Ratios are taken against the LARGEST mover, always — the pixel half
    # cannot see a `depth`, so a depth-aware reference would make the two
    # halves report different numbers for the same stage.
    assert m.reference == "near"
    assert m.ratios == pytest.approx({"far": 0.125, "mid": 0.5, "near": 1.0})
    assert not m.is_rigid
    # …and the ratios are still the depths, up to that normalisation.
    assert {k: v * 2.0 for k, v in m.ratios.items()} == pytest.approx(DEPTHS)


def test_a_zoom_free_pan_moves_nothing_vertically():
    """`Δy ≈ 0` distinguishes a pan from a zoom — for a ZOOM-FREE pan only. A
    pan+zoom shot measures a non-zero Δy by design, because zoom composes
    through the pivot, which is the correct default."""
    assert all(t.dy == pytest.approx(0.0) for t in _measure(PAN).tracks)


def test_the_ordering_is_far_slowest_and_near_fastest():
    """Wrong-order parallax is a real bug a bare inequality passes. A BONUS
    check, not a second gate: the zoom false positive satisfies the ordering
    too, so only the x = 0 probe excludes a zoom."""
    by_name = {t.name: abs(t.dx) for t in _measure(PAN).tracks}
    assert by_name["far"] < by_name["mid"] < by_name["near"]


def test_a_flattened_stage_reads_rigid_and_the_gap_goes_to_zero():
    """What a regression looks like: every plane at the character depth."""
    flat = _measure(PAN, depths={"far": 1.0, "mid": 1.0, "near": 1.0})
    assert flat.is_rigid
    assert min_ratio_gap(flat.ratios) == pytest.approx(0.0)


# --- the two instruments agree ------------------------------------------------


def test_the_pixel_and_json_measurements_give_the_same_ratios():
    """Different instruments, same number. The JSON half reads the compiled
    document; the pixel half reads decoded frames — synthesized here so the
    test stays free and offline, with the real render covered on the lane."""
    frames = []
    for cam_x in (0.0, 40.0):
        frame = np.full((H, W, 3), 255, np.uint8)
        for i, (name, depth) in enumerate(DEPTHS.items()):
            colour = COLOURS[name]
            x0 = int(round(W / 2 + 60 - 20 - depth * cam_x))
            y0 = int(round(H / 2 + (-70, 0, 70)[i] - 25))
            frame[y0 : y0 + 50, x0 : x0 + 40] = (
                (colour >> 16) & 0xFF, (colour >> 8) & 0xFF, colour & 0xFF
            )
        frames.append(frame)
    pixels = measure_pan_pixels(tuple(frames), COLOURS, depths=DEPTHS)
    assert pixels.ratios == pytest.approx({"far": 0.125, "mid": 0.5, "near": 1.0})
    assert _measure(PAN).ratios == pytest.approx(pixels.ratios)


def test_a_plane_that_changes_SIZE_between_frames_is_refused():
    """A centroid measured against a different shape is not a displacement.

    Measured in the research: a plane panning partly off-canvas read a
    `depth = 2` ratio as 1.975 — close enough to look right, and wrong enough
    to set a tripwire's floor against a number nobody meant. It is refused
    rather than reported, because a tripwire that fires on its own instrument
    failing cries wolf.
    """
    a = np.full((H, W, 3), 255, np.uint8)
    b = a.copy()
    a[10:60, 10:50] = (0x20, 0x40, 0x80)
    b[10:60, 0:30] = (0x20, 0x40, 0x80)  # clipped by the left edge
    a[80:130, 10:50] = (0x40, 0xA0, 0x60)
    b[80:130, 10:50] = (0x40, 0xA0, 0x60)
    with pytest.raises(ClippedPlane, match="different shape"):
        measure_pan_pixels((a, b), {"far": 0x204080, "mid": 0x40A060})


def test_a_colour_that_is_absent_is_refused_rather_than_read_as_zero():
    a = np.full((H, W, 3), 255, np.uint8)
    with pytest.raises(ClippedPlane, match="absent"):
        measure_pan_pixels((a, a.copy()), {"ghost": 0x123456, "other": 0x654321})


def test_the_masks_are_exact_colours_not_nearest_matches():
    """One integer per colour, compared exactly — so a plane's mask cannot
    pick up an anti-aliased edge pixel from its neighbour."""
    frame = np.full((4, 4, 3), 255, np.uint8)
    frame[0, 0] = (0x20, 0x40, 0x80)
    frame[0, 1] = (0x21, 0x40, 0x80)  # one bit off
    got = plane_centroids(frame, {"far": 0x204080})
    assert got["far"][2] == 1


# --- the reported number ------------------------------------------------------


def test_the_gap_is_the_smallest_separation_not_the_largest():
    """`stage_min_plane_ratio_gap` reports the CLOSEST pair, so a stage whose
    two nearest planes collapse moves the row even if a third is far away."""
    assert min_ratio_gap({"far": 0.125, "mid": 0.5, "near": 1.0}) == pytest.approx(0.375)
    assert min_ratio_gap({"a": 1.0, "b": 1.0, "c": 5.0}) == pytest.approx(0.0)


def test_the_reference_plane_is_the_largest_mover():
    """Not the character plane: the pixel half cannot see a `depth`, so a
    depth-aware reference makes the two halves report different numbers for
    the same stage — measured, 0.75 against `depth == 1` versus 0.375 against
    the largest mover, for one measurement."""
    from an.bench.stage import _reference

    tracks = (PlaneTrack("z", 8.0, 0.0, 4.0), PlaneTrack("mid", 2.0, 0.0, 1.0))
    assert _reference(tracks) == "z", "the largest mover, not the character plane"
    assert PanMeasurement(tracks, "z").ratios == pytest.approx({"z": 1.0, "mid": 0.25})


def test_the_registry_declares_both_rows_and_the_tripwire_counts_zero():
    from an.bench.registry import METRICS

    metric = METRICS["stage_min_plane_ratio_gap"]
    tripwire = METRICS["stage_planes_parallaxed"]
    assert metric.family == "B" and metric.role == "diagnostic"
    assert tripwire.family == "B" and tripwire.role == "tripwire"
    # Gated, not predicted, under both edge levers: an edge-quality lever moves
    # the exact-colour mask's boundary, and this measurement is defined on
    # exact colours — the outcome is "the instrument declines", which is
    # neither better nor worse.
    for lever in ("disabled_aa", "supersample"):
        assert metric.predictions[lever].expect is None, lever
        assert metric.predictions[lever].gate


# --- the flat-pan warning -----------------------------------------------------


def test_a_pan_over_a_stage_with_no_depth_warns():
    """The null hypothesis, told to the author at validate time. A warning:
    the render is correct, the whole picture slides — but it is also exactly
    what a flattened parallax looks like."""
    from an.ir.schema import Meta, SceneIR
    from an.ir.validate import validate_semantic

    shot = Shot(
        id="s1",
        renderer="cutout",
        duration=1.0,
        camera=Camera(move="pan_right"),
        entities=[AssetRef(kind="environment", id="e", store="environments", ref="park")],
    )
    scene = SceneIR(meta=Meta(), timeline=[shot])
    report = validate_semantic(scene, available_environments={})
    assert report.passed
    assert any("flattened parallax" in f.description for f in report.findings)

    # …silent for a real multiplane stage…
    good = validate_semantic(scene, available_environments={"park": _env()})
    assert not [f for f in good.findings if "flattened" in f.description]

    # …silent when the store was not supplied, because "no store" and "no
    # planes" are different facts.
    assert not validate_semantic(scene).findings


def test_a_zoom_does_not_trigger_the_flat_pan_warning():
    """It fires on a TRANSLATION. A zoom has nothing to parallax against —
    depth compensates translation only — so warning about it would be noise
    on every push-in in the corpus."""
    from an.ir.schema import Meta, SceneIR
    from an.ir.validate import validate_semantic

    shot = Shot(
        id="s1",
        renderer="cutout",
        duration=1.0,
        camera=Camera(move="push_in"),
        entities=[AssetRef(kind="environment", id="e", store="environments", ref="park")],
    )
    report = validate_semantic(SceneIR(meta=Meta(), timeline=[shot]), available_environments={})
    assert not [f for f in report.findings if "flattened" in f.description]
