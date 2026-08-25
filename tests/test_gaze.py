"""Gaze (an#99, epic #9 Wave 6): the eye stack, `add-gaze`, the seeded saccades,
authored gaze through the face solver, and the no-op on a rig without pupils.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from an.adapters.cutout.compile import CutoutCompileError, _blink_windows, compile_shot
from an.adapters.cutout.gaze import (
    BLINK_COUPLING_WINDOW_S,
    FIXATION_MAX_S,
    FIXATION_MIN_S,
    gaze_seed,
    saccade_track,
)
from an.adapters.cutout.serialize import to_dict
from an.characters import new_character
from an.characters.factory import GAZE_PARTS, add_gaze, gaze_travel_for
from an.characters.validate import validate_character
from an.ir.compose import expression
from an.ir.schema import AssetRef, Shot
from an.project import init, load

from .test_swap_channels import _evaluate, _python_timeline


@pytest.fixture(scope="module")
def rigs():
    with tempfile.TemporaryDirectory() as d:
        root = init(Path(d) / "p")
        chars = root / "assets" / "characters"
        new_character(chars, name="g", seed="g", use_dicebear=False)  # the eye stack, by default
        new_character(chars, name="old", seed="old", use_dicebear=False, gaze=False)
        yield root, load(root).mall


def _shot(entity, ref, actions=(), duration=2.0):
    return Shot(id="s", renderer="cutout", duration=duration, entities=[AssetRef(kind="character", id=entity, store="characters", ref=ref)], actions=list(actions))


def _pupil(js, t, entity="g", prop="x", side="left"):
    return _evaluate(_python_timeline(js), t)[(f"{entity}/head/{side}_pupil", prop)]


def _rest(js, entity, node, prop="x"):
    head = next(c for c in next(e for e in js.scene.children if e.name == entity).children if c.name == "head")
    return float(getattr(next(c for c in head.children if c.name == node).transform, prop))


# ---------------------------------------------------------------- the generator


def test_saccades_are_deterministic_per_entity_and_snapped_to_frames():
    a = saccade_track("gale", duration=4.0, fps=24)
    assert a == saccade_track("gale", duration=4.0, fps=24)
    assert a != saccade_track("nora", duration=4.0, fps=24)
    assert gaze_seed("gale") != gaze_seed("nora")
    assert a[0].time == 0.0 and all(0.0 <= s.time < 4.0 for s in a)
    assert all(abs(s.time * 24 - round(s.time * 24)) < 1e-9 for s in a), "step keyframes on frame times"
    assert all(-1.0 <= s.x <= 1.0 and -1.0 <= s.y <= 1.0 for s in a)
    gaps = [b.time - a_.time for a_, b in zip(a, a[1:])]
    assert gaps and min(gaps) >= FIXATION_MIN_S - 1.0 / 24 - 1e-9 and max(gaps) <= FIXATION_MAX_S + BLINK_COUPLING_WINDOW_S + 1.0 / 24 + 1e-9


def test_a_large_jump_moves_into_a_nearby_blink():
    """The coupling rule: a jump at or above the threshold within reach of a
    blink window lands on that window's centre (snapped to a frame)."""
    windows = [(1.0, 1.14)]
    coupled = saccade_track("gale", duration=6.0, fps=24, blink_windows=windows)
    free = saccade_track("gale", duration=6.0, fps=24, blink_windows=None)
    # Same seed → same targets; only the timing of qualifying jumps may differ.
    assert [(s.x, s.y) for s in coupled] == [(s.x, s.y) for s in free]
    moved = [(f.time, c.time) for f, c in zip(free, coupled) if f.time != c.time]
    for f_t, c_t in moved:
        assert abs(c_t - 1.07) <= 1.0 / 24 + 1e-9 and abs(f_t - 1.07) <= BLINK_COUPLING_WINDOW_S + 1.0 / 24


def test_amplitude_zero_holds_centre():
    assert all(s.x == 0.0 and s.y == 0.0 for s in saccade_track("gale", duration=3.0, fps=24, amplitude=0.0))


# ---------------------------------------------------------------- the eye stack


def test_a_new_character_has_the_stack_and_the_clamp(rigs):
    root, mall = rigs
    desc = mall["characters"]["g"]
    order = {s["name"]: s["draw_order"] for s in desc["slots"]}
    assert order["left_sclera"] < order["left_pupil"] < order["left_eye"], order
    assert desc["gaze_travel"] == gaze_travel_for()
    parts = root / "assets" / "characters" / "g" / "parts"
    assert all((parts / f"{p}.svg").is_file() for p in GAZE_PARTS)
    closed = (parts / "eye_l_closed.svg").read_text(encoding="utf-8")
    assert 'fill="none"' not in closed.split("<path")[0] and "<ellipse" in closed, "the closed lid is FILLED"
    open_ = (parts / "eye_l_open.svg").read_text(encoding="utf-8")
    assert 'fill="none"' in open_ and "<circle" not in open_, "the open eye is outline-only"


def test_add_gaze_is_the_expand_step_and_is_idempotent(rigs):
    import shutil

    root, _ = rigs
    old = root / "assets" / "characters" / "old2"
    shutil.copytree(root / "assets" / "characters" / "old", old)
    report = validate_character(old, name="old")
    assert any(f.severity == "info" and "add-gaze" in (f.suggested_fix or "") for f in report.findings)
    before = json.loads((old / "character.json").read_text(encoding="utf-8"))
    assert "left_pupil" not in {s["name"] for s in before["slots"]}
    add_gaze(old)
    once = (old / "character.json").read_text(encoding="utf-8")
    add_gaze(old)
    assert (old / "character.json").read_text(encoding="utf-8") == once
    after = json.loads(once)
    order = {s["name"]: s["draw_order"] for s in after["slots"]}
    assert order["left_sclera"] < order["left_pupil"] < order["left_eye"] and order["left_eye"] < order["left_brow"]
    assert not any(f.severity == "info" and "add-gaze" in (f.suggested_fix or "") for f in validate_character(old, name="old").findings)
    assert validate_character(old, name="old").passed


# ---------------------------------------------------------------- the solver


def test_authored_gaze_moves_the_pupils_by_the_declared_travel(rigs):
    """`gaze_x = ±1` alone pins the pupil at exactly ±MARGIN·travel·k at t=0
    (the generator's first step is zero), mirrored, yoked on both sides; a
    diagonal ask lands ON the inner ellipse (radius MARGIN in axis units), not
    at the box corner; and every frame of an ambient shot stays inside."""
    from an.adapters.cutout.compile import GAZE_ELLIPSE_MARGIN as M

    root, mall = rigs
    travel = mall["characters"]["g"]["gaze_travel"]
    plus = compile_shot(_shot("g", "g", [expression("g", None, axes={"gaze_x": 1.0}, blend=0.0)]), mall=mall, fps=24, strict_assets=True)
    minus = compile_shot(_shot("g", "g", [expression("g", None, axes={"gaze_x": -1.0}, blend=0.0)]), mall=mall, fps=24, strict_assets=True)
    rest_x, rest_y = _rest(plus, "g", "left_pupil", "x"), _rest(plus, "g", "left_pupil", "y")
    dx_plus = _pupil(plus, 0.0, "g", "x") - rest_x
    dx_minus = _pupil(minus, 0.0, "g", "x") - rest_x
    assert dx_plus > 0 and dx_minus == pytest.approx(-dx_plus), (dx_plus, dx_minus)
    assert _pupil(plus, 0.0, "g", "x", "right") - _rest(plus, "g", "right_pupil", "x") == pytest.approx(dx_plus), "yoked"
    k = dx_plus / (M * travel["x"])  # the rig factor, implied
    assert 0 < k < 1
    diag = compile_shot(_shot("g", "g", [expression("g", None, axes={"gaze_x": 1.0, "gaze_y": 1.0}, blend=0.0)]), mall=mall, fps=24, strict_assets=True)
    ux = (_pupil(diag, 0.0, "g", "x") - rest_x) / (travel["x"] * k)
    uy = (_pupil(diag, 0.0, "g", "y") - rest_y) / (travel["y"] * k)
    assert (ux, uy) == pytest.approx((M / 2 ** 0.5, M / 2 ** 0.5)), "on the inner ellipse, not at the box corner"
    ambient = compile_shot(_shot("g", "g", duration=8.0), mall=mall, fps=24, strict_assets=True)
    tl = _python_timeline(ambient)
    for f in range(0, 8 * 24 + 1):
        pose = _evaluate(tl, f / 24)
        ex = (pose[("g/head/left_pupil", "x")] - rest_x) / (travel["x"] * k)
        ey = (pose[("g/head/left_pupil", "y")] - rest_y) / (travel["y"] * k)
        assert ex * ex + ey * ey <= M * M + 1e-6


def test_the_clamp_holds_when_the_jitter_pushes_the_same_way(rigs):
    """Mutant 8: with `gaze_x = 1` authored, a frame where the saccade jitter is
    positive must still read exactly MARGIN·travel·k — the sum is clamped."""
    from an.adapters.cutout.compile import GAZE_ELLIPSE_MARGIN as M
    from an.adapters.cutout.gaze import saccade_track

    root, mall = rigs
    steps = saccade_track("g", duration=8.0, fps=24, blink_windows=_blink_windows("g", 8.0))
    positive = [s for s in steps if s.x > 0]
    assert positive, "the seeded track has a rightward jitter somewhere in 8 s"
    js = compile_shot(_shot("g", "g", [expression("g", None, axes={"gaze_x": 1.0}, blend=0.0)], duration=8.0), mall=mall, fps=24, strict_assets=True)
    rest_x, rest_y = _rest(js, "g", "left_pupil", "x"), _rest(js, "g", "left_pupil", "y")
    travel = mall["characters"]["g"]["gaze_travel"]
    k = (_pupil(js, 0.0, "g", "x") - rest_x) / (M * travel["x"])
    for s in positive[:3]:
        ux = (_pupil(js, s.time, "g", "x") - rest_x) / (travel["x"] * k)
        uy = (_pupil(js, s.time, "g", "y") - rest_y) / (travel["y"] * k)
        # 1 + jitter would exceed the circle: the sum sits exactly ON it.
        assert (ux * ux + uy * uy) ** 0.5 == pytest.approx(M, abs=1e-6), s
        assert ux > 0


def test_saccades_are_ambient_deterministic_and_stamped(rigs):
    root, mall = rigs
    a = to_dict(compile_shot(_shot("g", "g", duration=6.0), mall=mall, fps=24, strict_assets=True))
    b = to_dict(compile_shot(_shot("g", "g", duration=6.0), mall=mall, fps=24, strict_assets=True))
    assert a == b, "the same shot compiles to the same JSON twice"
    assert a["meta"]["gaze_seeds"] == {"g": gaze_seed("g")}
    face = next(v for k, v in a["animations"].items() if k.startswith("__face__"))
    props = {(c["target"], c["property"]) for c in face["channels"]}
    assert ("g/head/left_pupil", "x") in props and ("g/head/right_pupil", "y") in props
    xs = [k["value"] for c in face["channels"] if c["target"] == "g/head/left_pupil" and c["property"] == "x" for k in c["keyframes"]]
    assert len(set(xs)) > 1, "the pupils move on their own over 6 s"
    assert all(k["easing"] == "linear" for c in face["channels"] if "pupil" in c["target"] for k in c["keyframes"])


def test_a_rig_without_pupils_takes_gaze_as_a_no_op_with_no_stamp(rigs):
    root, mall = rigs
    plain = to_dict(compile_shot(_shot("old", "old"), mall=mall, fps=24, strict_assets=True))
    assert "gaze_seeds" not in plain["meta"], "a pre-Wave-6 rig's document must not move"
    gazed = to_dict(compile_shot(_shot("old", "old", [expression("old", None, axes={"gaze_x": 1.0}, blend=0.0)]), mall=mall, fps=24, strict_assets=True))
    assert not [k for k in gazed["animations"] if k.startswith("__face__")]
    assert gazed == plain


def test_emotion_and_gaze_compose_in_one_pose_over_real_pupils(rigs):
    """The PR-C assertion, now over pupils (#98's remaining item): both offsets
    present at t, order-independent."""
    root, mall = rigs
    a = expression("g", "angry", blend=0.0)
    b = expression("g", None, axes={"gaze_x": 1.0}, blend=0.0)
    poses = []
    for order in ((a, b), (b, a)):
        js = compile_shot(_shot("g", "g", list(order)), mall=mall, fps=24, strict_assets=True)
        poses.append(_evaluate(_python_timeline(js), 1.0))
    assert poses[0] == poses[1]
    pose = poses[0]
    rest_rot = _rest(js, "g", "left_brow", "rotation")
    assert pose[("g/head/left_brow", "rotation")] == pytest.approx(rest_rot + 0.8 * 0.35)
    assert pose[("g/head/left_pupil", "x")] > _rest(js, "g", "left_pupil", "x")


def test_a_baked_face_refuses_gaze_naming_add_gaze(rigs):
    root, mall = rigs
    baked = json.loads(json.dumps(mall["characters"]["g"]))
    baked["face_overlay"] = False
    m = {"characters": {"g": mall["characters"]["g"], "b": baked}}
    with pytest.raises(CutoutCompileError, match="add-gaze"):
        compile_shot(_shot("x", "b", [expression("x", None, axes={"gaze_x": 0.5})]), mall=m, fps=24, strict_assets=True)


def test_blink_windows_feed_the_coupling(rigs, monkeypatch):
    """The solver hands the generator the entity's real blink windows."""
    from an.adapters.cutout import compile as cm

    seen = {}
    real = cm.saccade_track

    def spy(entity_id, **kw):
        seen[entity_id] = kw.get("blink_windows")
        return real(entity_id, **kw)

    monkeypatch.setattr(cm, "saccade_track", spy)
    root, mall = rigs
    compile_shot(_shot("g", "g", duration=12.0), mall=mall, fps=24, strict_assets=True)
    assert seen["g"] == _blink_windows("g", 12.0) and seen["g"], "12 s blinks at least once"


def test_the_closed_lid_takes_the_heads_skin_not_a_reseeded_palette(tmp_path):
    """Review B2: a rig built with `--seed` ≠ name got a lid of another tone,
    because `add_gaze` re-derived the palette from a seed the descriptor did
    not carry. The fill now comes off the head art."""
    import re

    new_character(tmp_path, name="lidtest", seed="someone-else", use_dicebear=False, gaze=False)
    raw = json.loads((tmp_path / "lidtest" / "character.json").read_text(encoding="utf-8"))
    raw["metadata"].pop("seed", None)
    (tmp_path / "lidtest" / "character.json").write_text(json.dumps(raw), encoding="utf-8")
    add_gaze(tmp_path / "lidtest")
    head = (tmp_path / "lidtest" / "parts" / "head.svg").read_text(encoding="utf-8")
    skin = re.search(r'<circle[^>]*fill="(#[0-9a-fA-F]{6})"', head).group(1)
    lid = (tmp_path / "lidtest" / "parts" / "eye_l_closed.svg").read_text(encoding="utf-8")
    assert f'fill="{skin}"' in lid, (skin, lid)


def test_add_gaze_refuses_a_baked_face(tmp_path):
    new_character(tmp_path, name="b", seed="b", use_dicebear=False, gaze=False)
    p = tmp_path / "b" / "character.json"
    raw = json.loads(p.read_text(encoding="utf-8")); raw["face_overlay"] = False
    p.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(ValueError, match="baked into the head art"):
        add_gaze(tmp_path / "b")


def test_pupils_require_closed_lid_art(rigs):
    """Review A11: `closed` art is mandatory once pupils exist — enforced by
    validate, not only by construction."""
    import shutil

    root, _ = rigs
    src = root / "assets" / "characters" / "g"
    dst = root / "assets" / "characters" / "nolid"
    shutil.copytree(src, dst)
    raw = json.loads((dst / "character.json").read_text(encoding="utf-8"))
    del raw["skins"]["default"]["slots"]["left_eye"]["closed"]
    (dst / "character.json").write_text(json.dumps(raw), encoding="utf-8")
    report = validate_character(dst, name="nolid")
    assert any(f.severity == "error" and "closed-lid" in f.description for f in report.findings)


def test_a_procedural_rig_warns_for_a_preset_without_a_mouth_form():
    """Review B20: the bound-axis filter must not swallow the 'no descriptor'
    warning for `skeptical`/`thinking`/`neutral` (no mouth form)."""
    from an.adapters.cutout.compile import CutoutCompileWarning

    shot = Shot(id="s", renderer="cutout", duration=1.0, entities=[AssetRef(kind="character", id="p", store="characters", ref="p-v1")], actions=[expression("p", "skeptical")])
    with pytest.warns(CutoutCompileWarning, match="no descriptor"):
        compile_shot(shot, mall={"characters": {}}, fps=24)


def test_the_saccade_seed_is_not_the_blink_seed():
    """Mutant 2: the salt keeps saccades and blinks from sharing a seed."""
    from an.adapters.cutout.compile import _js_string_hash

    for name in ("gale", "face", "maya"):
        assert gaze_seed(name) != _js_string_hash(name)


def test_a_coupled_jump_lands_on_the_blink_centre_and_none_is_lost():
    """Mutant 4a and review D4: with a blink window right where a large jump is
    due, the jump lands ON the window's centre; and coupling never drops a
    jump (the step count matches the uncoupled track)."""
    free = saccade_track("ent313", duration=6.0, fps=24)
    large = [(a, b) for a, b in zip(free, free[1:]) if ((b.x - a.x) ** 2 + (b.y - a.y) ** 2) ** 0.5 >= 0.6]
    assert large, "the seeded track has a large jump in 6 s"
    _, jump = large[0]
    window = (jump.time - 0.07, jump.time + 0.07)  # centred on the jump, within reach
    coupled = saccade_track("ent313", duration=6.0, fps=24, blink_windows=[window])
    assert len(coupled) == len(free), "coupling delays, never drops"
    assert any(abs(s.time - jump.time) < 1e-9 and (s.x, s.y) == (jump.x, jump.y) for s in coupled)
    # A window whose centre is EARLIER than the previous step: the jump keeps its own time.
    early = (free[0].time - 0.3, free[0].time - 0.1)
    kept = saccade_track("ent313", duration=6.0, fps=24, blink_windows=[early])
    assert len(kept) == len(free)


def test_add_gaze_refuses_to_overwrite_hand_drawn_eyes(tmp_path):
    """Review D1: a promoted rig's eyes are not the factory's to redraw."""
    new_character(tmp_path, name="h", seed="h", use_dicebear=False, gaze=False)
    (tmp_path / "h" / "parts" / "eye_l_open.svg").write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 200 120"><g id="illustrator_eye"><path d="M 0 0 L 200 120"/></g></svg>', encoding="utf-8"
    )
    with pytest.raises(ValueError, match="eye_l_open.svg"):
        add_gaze(tmp_path / "h")
    assert not (tmp_path / "h" / "parts" / "sclera_l.svg").exists(), "nothing written before the refusal"
    add_gaze(tmp_path / "h", overwrite_eyes=True)
    assert (tmp_path / "h" / "parts" / "sclera_l.svg").exists()


def test_a_new_character_lid_matches_its_head(tmp_path):
    """Review D2: `new_character(gaze=True)` used to hand `add_gaze` a palette
    skin while the head was painted from another table."""
    import re

    new_character(tmp_path, name="n", seed="n", use_dicebear=False)
    head = (tmp_path / "n" / "parts" / "head.svg").read_text(encoding="utf-8")
    skin = re.search(r'<circle[^>]*fill="(#[0-9a-fA-F]{6})"', head).group(1)
    for side in ("l", "r"):
        lid = (tmp_path / "n" / "parts" / f"eye_{side}_closed.svg").read_text(encoding="utf-8")
        assert f'fill="{skin}"' in lid


def test_a_malformed_binding_does_not_break_an_expression_free_compile(rigs):
    """Review D3: the binding is only consulted when something expresses on the
    entity; with nothing to bind it must compile as before, and with an
    expression the error is a CutoutCompileError."""
    root, mall = rigs
    # A pupil-less rig: the binding is consulted only when something expresses.
    raw = json.loads(json.dumps(mall["characters"]["old"]))
    raw["expression_binding"] = [{"axis": "bogus", "slot": "left_brow", "property": "y", "gain": 1.0}]
    m = {"characters": {"old": mall["characters"]["old"], "bad": raw}}
    compile_shot(_shot("b", "bad"), mall=m, fps=24, strict_assets=True)
    with pytest.raises(CutoutCompileError, match="bogus"):
        compile_shot(_shot("b", "bad", [expression("b", "happy")]), mall=m, fps=24, strict_assets=True)
    # A pupil rig is always solved (its saccades need the binding), so a
    # broken binding is a compile error there — the typed one, not a raw raise.
    rawg = json.loads(json.dumps(mall["characters"]["g"]))
    rawg["expression_binding"] = raw["expression_binding"]
    with pytest.raises(CutoutCompileError, match="bogus"):
        compile_shot(_shot("p", "badg"), mall={"characters": {"badg": rawg}}, fps=24, strict_assets=True)


def test_gaze_travel_must_be_positive(rigs):
    root, mall = rigs
    raw = json.loads(json.dumps(mall["characters"]["g"]))
    raw["gaze_travel"] = {"x": -3.0, "y": 5.0}
    m = {"characters": {"g": mall["characters"]["g"], "neg": raw}}
    with pytest.raises(CutoutCompileError, match="gaze_travel"):
        compile_shot(_shot("q", "neg", [expression("q", None, axes={"gaze_x": 1.0})]), mall=m, fps=24, strict_assets=True)


def test_one_pupil_is_a_blocking_finding(rigs):
    import shutil

    root, _ = rigs
    src = root / "assets" / "characters" / "g"
    dst = root / "assets" / "characters" / "onepupil"
    shutil.copytree(src, dst)
    raw = json.loads((dst / "character.json").read_text(encoding="utf-8"))
    raw["slots"] = [s for s in raw["slots"] if s["name"] != "right_pupil"]
    (dst / "character.json").write_text(json.dumps(raw), encoding="utf-8")
    report = validate_character(dst, name="onepupil")
    assert any(f.severity == "error" and "one pupil" in f.description for f in report.findings)
