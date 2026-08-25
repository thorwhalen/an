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


# --- the ledger half, which had no coverage at all (an#111 review, H2) -------


def _fake_capture(scene_json: dict):
    """A `SceneCapture`-shaped object with one shot, for the walk under test."""
    from types import SimpleNamespace

    return SimpleNamespace(shots=[SimpleNamespace(scene_json=scene_json)])


def _staged(children, resolution):
    return {"scene": {"name": "root", "children": children}, "asset_resolution": resolution}


def _rect(name, colour):
    return {"name": name, "visual": {"kind": "rect", "color": colour}}


def test_a_character_s_parts_are_not_planes():
    """The walk is scoped by what the document SAYS, not by depth.

    The first version called every `rect` two levels down a plane — which is
    also a procedural character's parts. `single_character` and `dialogue`
    reported three "planes" (`torso`, `left_arm`, `right_arm`, all one colour),
    so the tripwire would have called a scene with no stage a flattened stage,
    with the detail text saying the stage panned rigidly (an#111 review, H1).
    """
    from an.bench.run import _plane_colours

    doc = _staged(
        [{"name": "charlie", "children": [
            _rect("torso", "#5b394a"), _rect("left_arm", "#5b394a"), _rect("right_arm", "#5b394a")]}],
        [{"id": "charlie", "kind": "character", "resolved": "placeholder"}],
    )
    assert _plane_colours(_fake_capture(doc)) == {}


def test_a_preset_backdrops_sky_and_ground_are_not_planes():
    """They are rects under an environment node — the right shape and the
    wrong thing. The scoping is `resolved == "planes"`, which only a
    descriptor-built stage records."""
    from an.bench.run import _plane_colours

    doc = _staged(
        [{"name": "park", "children": [_rect("sky", "#cfe9ff"), _rect("ground", "#7cba6f")]}],
        [{"id": "park", "kind": "environment", "resolved": "preset"}],
    )
    assert _plane_colours(_fake_capture(doc)) == {}


def test_declared_planes_are_found_including_the_foreground_container():
    """…and keyed by full PATH, so two environments each with a `sky` cannot
    overwrite each other."""
    from an.bench.run import _plane_colours

    doc = _staged(
        [
            {"name": "street", "children": [_rect("sky", "#204080"), _rect("road", "#40a060")]},
            {"name": "street__front", "children": [_rect("rail", "#c04020")]},
        ],
        [{"id": "street", "kind": "environment", "resolved": "planes"}],
    )
    assert _plane_colours(_fake_capture(doc)) == {
        "street/sky": 0x204080,
        "street/road": 0x40A060,
        "street__front/rail": 0xC04020,
    }


def test_the_tripwire_floor_sits_below_what_the_fixture_measures():
    """The floor is "half the first bless's measured minimum", and both halves
    of that sentence are load-bearing: a floor at or above the measured value
    fires on the very stage it was calibrated against, and a floor of zero can
    never fire at all. Both mutants survived the first round (an#111 review,
    H2) because nothing compared the constant to anything.
    """
    from an.bench.run import STAGE_MIN_RATIO_GAP

    measured_at_bless = 0.375  # depths 0.25 / 1.0 / 2.0, normalised to the largest mover
    assert 0.0 < STAGE_MIN_RATIO_GAP < measured_at_bless
    assert STAGE_MIN_RATIO_GAP == pytest.approx(measured_at_bless / 2)
    # …and a flattened stage lands under it, which is the point.
    assert min_ratio_gap({"far": 1.0, "mid": 1.0, "near": 1.0}) < STAGE_MIN_RATIO_GAP


def test_an_instrument_failure_is_unavailable_and_never_a_measured_zero():
    """A tripwire that fires on its own instrument failing cries wolf.

    Stated in a comment and enforced nowhere until this test: the mutant that
    turns the clipped-plane branch into `measured(0.0), measured(False)` — a
    red tripwire on a scene nobody could measure — survived (an#111 review, H2).
    """
    from an.bench.run import _stage_pan_values

    class _Capture:
        shots = [
            type("S", (), {"scene_json": _staged(
                [{"name": "e", "children": [_rect("a", "#204080"), _rect("b", "#40a060")]}],
                [{"id": "e", "kind": "environment", "resolved": "planes"}],
            )})()
        ]

    # No frames resolve, so the instrument cannot run.
    import an.bench.golden as G

    original = G.resolve_frames
    G.resolve_frames = lambda *a, **k: []
    try:
        metric, tripwire = _stage_pan_values(_Capture(), (0.0, 1.0))
    finally:
        G.resolve_frames = original
    assert metric.state == "unavailable" and tripwire.state == "unavailable"
    assert metric.detail


def test_a_scene_with_no_planes_is_unavailable_for_a_STRUCTURAL_reason():
    """`MetricSpec.requires` says a null here is structural rather than a blind
    panel. That claim is only true if the walk actually finds nothing — which
    it did not, before the scoping was fixed."""
    from an.bench.registry import METRICS
    from an.bench.run import _stage_pan_values

    doc = _staged(
        [{"name": "charlie", "children": [_rect("torso", "#5b394a")]}],
        [{"id": "charlie", "kind": "character", "resolved": "placeholder"}],
    )
    metric, tripwire = _stage_pan_values(_fake_capture(doc), (0.0, 1.0))
    assert metric.state == "unavailable"
    assert "plane" in metric.detail
    assert METRICS["stage_min_plane_ratio_gap"].requires


# --- the limits the review found ---------------------------------------------


def test_a_rolling_camera_is_refused_rather_than_measured():
    """Under a rotation the composed x-displacement is `−fx·A + fy·B`, so the
    axes mix and a ratio stops being a depth ratio. Measured with rotation 0.6
    and per-axis factors: one plane read NEGATIVE, and the resulting gap was
    LARGER than the honest one — so the tripwire passed on a number that meant
    nothing (an#111 review, M2)."""
    from an.bench.stage import RotatingCamera

    rolled = Camera(keys=[CameraKey(at=0.0, easing="linear"), CameraKey(at=0.5, x=60.0, rotation=0.6)])
    with pytest.raises(RotatingCamera, match="rolls"):
        _measure(rolled)


def test_the_probe_point_inverts_the_WHOLE_chain():
    """An ancestor with an offset, or a plane with its own pivot or scale, left
    a residual in the displacement — and that residual's `a·(S₁−S₀)` term is
    exactly the zoom that was supposed to cancel (an#111 review, M1)."""
    from an.adapters.cutout.serialize import (
        CutoutSceneJSON,
        NodeJSON,
        TimelineJSON,
        TransformJSON,
    )
    from an.adapters.cutout.timeline import screen_position
    from an.bench.stage import _probe_point

    scene = CutoutSceneJSON(
        scene=NodeJSON(name="root", children=[
            NodeJSON(name="street", transform=TransformJSON(x=37.0, y=11.0), children=[
                NodeJSON(name="hills", transform=TransformJSON(
                    x=-40.0, y=55.0, pivot_x=5.0, scale_x=2.0))])]),
        timeline=TimelineJSON(duration=1.0),
    )
    scene.meta.width, scene.meta.height = W, H
    probe = _probe_point(scene, "street/hills")
    # The probe lands on the SCENE origin, which is the canvas centre.
    assert screen_position(scene, "street/hills", point=probe) == pytest.approx((W / 2, H / 2))


def test_a_CLIPPED_plane_is_unavailable_through_the_ledger_path_too():
    """The clipped-plane branch specifically, not just "no frames".

    `measure_pan_pixels` raising is one thing; `_stage_pan_values` turning that
    into a measured zero and a red tripwire is another, and only the second is
    the failure the comment warns about (an#111 review, H2/N3).
    """
    import an.bench.golden as G
    from an.bench.run import _stage_pan_values

    a = np.full((H, W, 3), 255, np.uint8)
    b = a.copy()
    a[10:60, 10:50] = (0x20, 0x40, 0x80)
    b[10:60, 10:40] = (0x20, 0x40, 0x80)  # a narrower mask: clipped
    a[80:130, 10:50] = (0x40, 0xA0, 0x60)
    b[80:130, 10:50] = (0x40, 0xA0, 0x60)

    doc = _staged(
        [{"name": "e", "children": [_rect("far", "#204080"), _rect("mid", "#40a060")]}],
        [{"id": "e", "kind": "environment", "resolved": "planes"}],
    )
    frames = iter((a, b))
    originals = (G.resolve_frames, G.frame_png_path)
    G.resolve_frames = lambda *args, **kw: [object(), object()]
    G.frame_png_path = lambda *args, **kw: None
    import an.bench.png as P

    original_read = P.read_png
    P.read_png = lambda _p: next(frames)
    try:
        metric, tripwire = _stage_pan_values(_fake_capture(doc), (0.0, 1.0))
    finally:
        G.resolve_frames, G.frame_png_path = originals
        P.read_png = original_read
    assert metric.state == "unavailable" and tripwire.state == "unavailable"
    assert "different shape" in metric.detail


# --- the flat-pan warning's four cases ---------------------------------------


def _warns_flat(camera, planes):
    import json as _json

    from an.ir.schema import Meta, SceneIR
    from an.ir.validate import validate_semantic

    store = (
        {"p": _json.loads(EnvironmentDescriptor(name="e", planes=planes).model_dump_json())}
        if planes is not None
        else {}
    )
    shot = Shot(
        id="s",
        renderer="cutout",
        duration=1.0,
        camera=camera,
        entities=[AssetRef(kind="environment", id="e", store="environments", ref="p")],
    )
    report = validate_semantic(SceneIR(meta=Meta(), timeline=[shot]), available_environments=store)
    return any("flattened parallax" in f.description for f in report.findings)


def test_a_pure_zoom_held_off_centre_does_not_warn():
    """The guard asks whether the camera TRAVELS, not whether it sits off
    centre. Asking the latter warned on a scene with no translation at all
    (an#111 review, M3a)."""
    held = Camera(keys=[CameraKey(at=0.0, x=100.0), CameraKey(at=1.0, x=100.0, zoom=2.0)])
    assert not _warns_flat(held, [Plane(name="a")])


def test_a_stage_that_parallaxes_by_OVERRIDE_does_not_warn():
    """`parallax` overrides `depth`, so reading the scalar warns on a stage
    that is genuinely multiplane (an#111 review, M3b)."""
    assert not _warns_flat(
        Camera(move="pan_right"),
        [Plane(name="a", parallax=(0.2, 0.2)), Plane(name="b", parallax=(2.0, 2.0))],
    )


def test_a_stage_that_is_FLAT_by_override_does_warn():
    """…and the mirror image: distinct depths, both overridden to the same
    factor, is exactly the case the warning exists to catch and the one the
    scalar read missed (an#111 review, M3c)."""
    assert _warns_flat(
        Camera(move="pan_right"),
        [
            Plane(name="a", depth=0.25, parallax=(1.0, 1.0)),
            Plane(name="b", depth=2.0, parallax=(1.0, 1.0)),
        ],
    )


def test_a_pan_over_a_stage_with_no_planes_warns():
    assert _warns_flat(Camera(move="pan_right"), [])
