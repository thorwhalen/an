"""The face solver at the compiler, and everything an `expression` touches on
the way there: the six enumerations, the md round trip, validate, the mouth
set per line, the baked-face refusal, and JSON identity for untouched scenes
(an#98, epic #9 Wave 6).
"""

from __future__ import annotations

import json
import re
import tempfile
import warnings
from pathlib import Path

import pytest

from an.adapters.cutout import compile as compile_mod
from an.adapters.cutout.compile import CutoutCompileError, CutoutCompileWarning, compile_shot
from an.adapters.cutout.serialize import to_dict
from an.characters import new_character
from an.ir.compose import expression, flatten, sequence, delay, tween
from an.ir.schema import AssetRef, Dialogue, ExpressionAction, Shot, VisemeKeyframe, VisemeTrack
from an.ir.sync import ir_to_markdown, markdown_to_ir
from an.ir.validate import validate_semantic
from an.project import init, load

from an.adapters.cutout.timeline import evaluate_timeline, timeline_from_scene

ROOT = Path(__file__).resolve().parents[1]
#: Corpus scenes whose contract hash is allowed to move in the PR that ADDS
#: them — a scene with no committed ledger row has nothing to be compared to.
#: `expressions` was Wave 6's own scene and carried this exemption while
#: an#103 landed; it has had a row since, so the set is empty and the guard
#: covers all eight. Keep it empty unless a PR is adding a scene, and empty it
#: again in the PR that first blesses that scene's row (an#108 review, H-1:
#: the exemption outlived its wave, and three later PRs claimed "all eight,
#: no exemption" while the only guard checked seven).
NEW_IN_WAVE: set[str] = set()


@pytest.fixture(scope="module")
def mall():
    with tempfile.TemporaryDirectory() as d:
        root = init(Path(d) / "p")
        # `gaze=False`: these rigs stand for the pre-Wave-6 shape (no pupils), so
        # the untouched-entity identity holds; PR-D's rigs are in test_gaze.py.
        new_character(root / "assets" / "characters", name="c", seed="c", use_dicebear=False, overwrite=True, gaze=False)
        new_character(root / "assets" / "characters", name="plain", seed="plain", use_dicebear=False, overwrite=True, mouth_variants={}, gaze=False)
        yield load(root).mall


def _track(*codes):
    return VisemeTrack(keyframes=[VisemeKeyframe(time=0.2 * i, viseme=c) for i, c in enumerate(codes)])


def _shot(actions=(), dialogue=(), duration=2.0, ref="c", entity="c"):
    return Shot(
        id="s", renderer="cutout", duration=duration,
        entities=[AssetRef(kind="character", id=entity, store="characters", ref=ref)],
        actions=list(actions), dialogue=list(dialogue),
    )


def _face_channels(js):
    d = to_dict(js)
    out = {}
    for k, a in d["animations"].items():
        if k.startswith("__face__"):
            for ch in a["channels"]:
                out[(ch["target"], ch["property"])] = ch["keyframes"]
    return out


# ---------------------------------------------------------------- the solver


def test_every_generated_face_channel_has_a_distinct_target_property(mall):
    js = compile_shot(_shot(actions=[expression("c", "angry")]), mall=mall, fps=24, strict_assets=True)
    d = to_dict(js)
    seen = []
    for k, a in d["animations"].items():
        if k.startswith("__face__") or k.startswith("__blink__"):
            seen += [(ch["target"], ch["property"]) for ch in a["channels"]]
    assert len(seen) == len(set(seen)), seen
    assert not [k for k in d["animations"] if k.startswith("__blink__")], "an expressed-on entity's blinks live in the face clip"


def test_emotion_and_an_override_compose_in_one_pose_order_independently(mall):
    """Both offsets present at t=1.0, and the pose is identical with the
    contributors fed in reverse — commutativity, which override lacks."""
    a = expression("c", "angry", duration=2.0, blend=0.0)
    b = expression("c", None, axes={"brow_height_l": 0.5}, duration=2.0, blend=0.0)
    poses = []
    for order in ((a, b), (b, a)):
        js = compile_shot(_shot(actions=list(order)), mall=mall, fps=24, strict_assets=True)
        poses.append(evaluate_timeline(timeline_from_scene(js), 1.0))
    assert poses[0] == poses[1]
    pose = poses[0]
    neutral = evaluate_timeline(timeline_from_scene(compile_shot(_shot(), mall=mall, fps=24, strict_assets=True)), 1.0)
    rest_rot = neutral.get(("c/head/left_brow", "rotation"), 0.0)
    assert pose[("c/head/left_brow", "rotation")] == pytest.approx(rest_rot + 0.8 * 0.35), "angry's furrow"
    assert pose[("c/head/right_brow", "rotation")] == pytest.approx(-(pose[("c/head/left_brow", "rotation")]))
    # brow_height: angry -0.6 plus the +0.5 override on the LEFT only.
    left_dy = pose[("c/head/left_brow", "y")] - neutral[("c/head/left_brow", "y")] if ("c/head/left_brow", "y") in neutral else None
    js_rest = next(c for c in js.scene.children if c.name == "c")
    head = next(c for c in js_rest.children if c.name == "head")
    lb = next(c for c in head.children if c.name == "left_brow").transform.y
    rb = next(c for c in head.children if c.name == "right_brow").transform.y
    left_off = pose[("c/head/left_brow", "y")] - lb
    right_off = pose[("c/head/right_brow", "y")] - rb
    assert left_off == pytest.approx(right_off * (0.1 / 0.6), rel=1e-6), (left_off, right_off)
    assert right_off > 0, "angry lowers the brows (+y is down)"


def test_a_pose_at_t_carries_the_blink_inside_the_lid_state(mall):
    """The lid is min(expression, blink): a sleepy `half`-less rig still closes."""
    shot = _shot(actions=[expression("c", None, axes={"lid_open_l": -0.5, "lid_open_r": -0.5}, blend=0.0)], duration=6.0)
    js = compile_shot(shot, mall=mall, fps=24, strict_assets=True)
    ch = _face_channels(js)[("c/head/left_eye", "eyelid")]
    values = {k["value"] for k in ch}
    assert "CLOSED" in values, "blinks still close the eye under a lid offset"
    assert "HALF" not in values, "no half art on the synthesized rig: -0.5 stays OPEN"


def test_untouched_entities_get_their_blink_clips_verbatim(mall):
    """JSON identity: the face solver must not change one byte of a scene
    nothing expresses on — that is what keeps every corpus contract hash."""
    shot = _shot(duration=12.0)
    with_solver = to_dict(compile_shot(shot, mall=mall, fps=24, strict_assets=True))
    # The reference: the an#88 blink emitter alone, on a copy of the animations.
    anims, tracks = {}, []
    placed = compile_mod._blink_placements(shot, "c", anims, vocab=compile_mod._swap_vocabulary(compile_mod._build_scene_root(shot, mall, textures={}, resolutions=[]), shot, mall), fps=24)
    assert placed, "a 12 s shot blinks at least once"
    blink_ids = [k for k in with_solver["animations"] if k.startswith("__blink__")]
    assert sorted(blink_ids) == sorted(anims)
    for k in blink_ids:
        assert with_solver["animations"][k] == json.loads(anims[k].model_dump_json())
    assert not [k for k in with_solver["animations"] if k.startswith("__face")]


def test_every_corpus_contract_hash_equals_the_committed_ledger_row():
    """The measurement ledger's evidence stays valid across the solver: the
    seven corpus scenes' contract hashes are the row's. Not just pixels —
    `bench-compare` refuses rows whose hash moved."""
    from an.bench import contract
    from an.bench.capture import stage_copy
    from an.bench.corpus import DFLT_FIXTURES

    # The newest CLEAN row — by its own timestamp, not its filename (a stale
    # `-dirty` row sorts after the commit-named ones).
    rows = [json.load(open(p)) for p in ROOT.glob("misc/bench/ledger/*.json") if "dirty" not in p.name]
    row = max(rows, key=lambda r: r["generated_at"])
    checked = 0
    for name, fx in DFLT_FIXTURES.items():
        if name not in row["scenes"] or name in NEW_IN_WAVE:
            continue  # only a scene the newest clean row has never measured
        checked += 1
        with tempfile.TemporaryDirectory() as tmp:
            work = stage_copy(ROOT / fx.path, Path(tmp))
            if fx.prepare:
                fx.prepare(work)
            proj = load(work)
            scene = proj.scene
            docs = [
                to_dict(compile_shot(s, mall=proj.mall, fps=scene.meta.fps, width=scene.meta.resolution.width, height=scene.meta.resolution.height, strict_assets=True))
                for s in scene.timeline
            ]
        assert contract.scenes_contract_sha256(docs) == row["scenes"][name]["provenance"]["scene_contract_sha256"], name
    # Every fixture, not "at least most of them". A floor below the corpus size
    # lets a scene fall out of the guard — by an exemption, or by dropping out
    # of the ledger row — while the count still passes.
    #
    # And the two ways of falling out are asserted SEPARATELY from the count,
    # because a count derived from `NEW_IN_WAVE` moves with it: growing the
    # exemption shrinks both sides and the equality holds. Only comparing the
    # exemption against the row can catch that (an#108 review, H-1).
    unmeasured = set(DFLT_FIXTURES) - set(row["scenes"])
    assert unmeasured == NEW_IN_WAVE, (
        f"scenes with no row in {row.get('commit', '?')}: {sorted(unmeasured)}; "
        f"declared new: {sorted(NEW_IN_WAVE)}"
    )
    assert checked == len(DFLT_FIXTURES) - len(NEW_IN_WAVE), (
        sorted(DFLT_FIXTURES), sorted(row["scenes"]), checked
    )


def test_no_scene_stays_exempt_from_the_hash_guard_once_it_has_a_row():
    """An exemption is legitimate for exactly one thing: a scene the newest
    committed row has never measured, because there is nothing to compare to.

    It is not legitimate as a standing waiver, and it does not expire on its
    own — `NEW_IN_WAVE_6 = {"expressions"}` outlived Wave 6 by four PRs, three
    of which stated "all eight corpus hashes, no exemption" while the only
    guard in the repo checked seven (an#108 review, H-1). The guard above
    cannot notice: its expected count is derived from the same set.
    """
    rows = [json.load(open(p)) for p in ROOT.glob("misc/bench/ledger/*.json") if "dirty" not in p.name]
    row = max(rows, key=lambda r: r["generated_at"])
    stale = sorted(n for n in NEW_IN_WAVE if n in row["scenes"])
    assert not stale, (
        f"{stale} have committed ledger rows, so their hashes are comparable "
        "and the exemption is now a hole. Remove them from NEW_IN_WAVE."
    )


def test_authored_wins_over_the_expression_with_a_warning(mall):
    shot = _shot(actions=[expression("c", "angry", blend=0.0), tween("c/head/left_brow", "rotation", to=1.0, duration=2.0)])
    with pytest.warns(CutoutCompileWarning, match="authored channel on 'c/head/left_brow':'rotation'"):
        js = compile_shot(shot, mall=mall, fps=24, strict_assets=True)
    pose = evaluate_timeline(timeline_from_scene(js), 2.0)
    assert pose[("c/head/left_brow", "rotation")] == pytest.approx(1.0)
    # The face clip sits at the FRONT of the track, like blinks.
    d = to_dict(js)
    (track,) = [t for t in d["timeline"]["tracks"] if t["target_root"] == "c"]
    assert track["clips"][0]["animation_id"].startswith("__face__")


def test_dialogue_emotion_desugars_in_memory_only(mall):
    line = Dialogue(speaker="c", text="hi", emotion="happy", start=0.5, duration=0.6, viseme_track=_track("X", "D", "X"))
    shot = _shot(dialogue=[line])
    js = compile_shot(shot, mall=mall, fps=24, strict_assets=True)
    assert ("c/head/left_brow", "y") in _face_channels(js)
    assert shot.actions == [] and not any(isinstance(a, ExpressionAction) for a in shot.actions)
    scene_md = ir_to_markdown(__import__("an.ir.schema", fromlist=["SceneIR"]).SceneIR(meta={"title": "t", "duration": 2.0}, timeline=[shot]))
    assert "kind: expression" not in scene_md and "[happy]" in scene_md


def test_an_unknown_preset_refuses_at_compile_and_at_validate(mall):
    with pytest.raises(CutoutCompileError, match="joyful"):
        compile_shot(_shot(actions=[expression("c", "joyful")]), mall=mall, fps=24, strict_assets=True)
    from an.ir.schema import Meta, SceneIR

    scene = SceneIR(meta=Meta(title="t", duration=2.0), timeline=[_shot(actions=[expression("c", "joyful")], dialogue=[Dialogue(speaker="c", text="x", emotion="gleeful")])])
    report = validate_semantic(scene, available_characters=mall["characters"])
    errors = [f.description for f in report.findings if f.severity == "error"]
    assert any("joyful" in e for e in errors) and any("gleeful" in e for e in errors), errors


def test_a_baked_face_refuses_an_authored_expression_and_warns_on_sugar(mall):
    desc = json.loads(json.dumps(mall["characters"]["c"]))
    desc["face_overlay"] = False
    baked_mall = {"characters": {"c": mall["characters"]["c"], "baked": desc}}
    with pytest.raises(CutoutCompileError, match="baked into the head art"):
        compile_shot(_shot(actions=[expression("b", "happy")], ref="baked", entity="b"), mall=baked_mall, fps=24, strict_assets=True)
    line = Dialogue(speaker="b", text="hi", emotion="happy", start=0.2, duration=0.5, viseme_track=_track("X", "D", "X"))
    with pytest.warns(CutoutCompileWarning, match="moves nothing"):
        js = compile_shot(_shot(dialogue=[line], ref="baked", entity="b"), mall=baked_mall, fps=24, strict_assets=True)
    assert not _face_channels(js)
    from an.ir.schema import Meta, SceneIR

    report = validate_semantic(SceneIR(meta=Meta(title="t", duration=2.0), timeline=[_shot(actions=[expression("b", "happy")], ref="baked", entity="b")]), available_characters=baked_mall["characters"])
    assert any("baked" in f.description for f in report.findings if f.severity == "error")


def test_a_procedural_rig_takes_an_expression_as_a_no_op_with_a_warning():
    shot = Shot(id="s", renderer="cutout", duration=1.0, entities=[AssetRef(kind="character", id="p", store="characters", ref="p-v1")], actions=[expression("p", "happy")])
    with pytest.warns(CutoutCompileWarning, match="no descriptor"):
        js = compile_shot(shot, mall={"characters": {}}, fps=24)
    assert not _face_channels(js)


# ---------------------------------------------------------------- the mouth set per line


def test_a_line_under_a_preset_uses_the_variant_set(mall):
    line = Dialogue(speaker="c", text="hi", start=0.2, duration=0.6, viseme_track=_track("X", "D", "X"))
    js = compile_shot(_shot(actions=[expression("c", "happy", blend=0.0)], dialogue=[line]), mall=mall, fps=24, strict_assets=True)
    d = to_dict(js)
    (clip,) = [a for k, a in d["animations"].items() if k.startswith("__viseme__")]
    assert clip["channels"][0]["property"] == "viseme@happy"
    pose = evaluate_timeline(timeline_from_scene(js), 0.5)
    assert pose[("c/head/mouth", "viseme@happy")] == "D"
    assert ("c/head/mouth", "viseme") not in pose, "one mouth swap property live per instant"


def test_a_silent_expression_holds_the_variants_rest_outside_lines(mall):
    line = Dialogue(speaker="c", text="hi", start=1.0, duration=0.5, viseme_track=_track("X", "D", "X"))
    js = compile_shot(_shot(actions=[expression("c", "happy", blend=0.0)], dialogue=[line]), mall=mall, fps=24, strict_assets=True)
    tl = timeline_from_scene(js)
    assert evaluate_timeline(tl, 0.5)[("c/head/mouth", "viseme@happy")] == "X"
    assert evaluate_timeline(tl, 1.2)[("c/head/mouth", "viseme@happy")] == "D"
    assert evaluate_timeline(tl, 1.9)[("c/head/mouth", "viseme@happy")] == "X"
    holds = sorted(k for k in to_dict(js)["animations"] if k.startswith("__face_mouth__"))
    assert len(holds) == 4, holds  # variant rest before/after the line, neutral rest before/after
    assert sum("_neutral_" in k for k in holds) == 2


def test_a_missing_variant_falls_back_to_the_neutral_set_with_a_warning(mall):
    line = Dialogue(speaker="plain", text="hi", start=0.2, duration=0.6, viseme_track=_track("X", "D", "X"))
    with pytest.warns(UserWarning, match="viseme@happy"):
        js = compile_shot(_shot(actions=[expression("plain", "happy", blend=0.0)], dialogue=[line], ref="plain", entity="plain"), mall=mall, fps=24, strict_assets=True)
    assert evaluate_timeline(timeline_from_scene(js), 0.5)[("plain/head/mouth", "viseme")] == "D"


# ---------------------------------------------------------------- the six enumerations


def test_expression_round_trips_through_scene_md():
    md = """# X

```yaml meta
title: X
duration: 2
```

## Shot s1 (cutout)

```yaml shot
duration: 2
```

```yaml entities
- kind: character
  id: c
  store: characters
  ref: c
```

```yaml actions
- kind: expression
  target: c
  preset: angry
  axes:
    brow_height_l: 0.5
  intensity: 0.8
  duration: 1.0
  blend: 0.0
  start: 0.5
```
"""
    scene = markdown_to_ir(md)
    (flat,) = [f for f in flatten(scene.timeline[0].actions[0]) if isinstance(f.action, ExpressionAction)]
    assert (flat.start, flat.action.preset, flat.action.axes, flat.action.intensity, flat.action.duration, flat.action.blend) == (0.5, "angry", {"brow_height_l": 0.5}, 0.8, 1.0, 0.0)
    back = markdown_to_ir(ir_to_markdown(scene))
    assert back.timeline[0].actions == scene.timeline[0].actions
    md2 = ir_to_markdown(scene)
    assert "kind: expression" in md2 and "start: 0.5" in md2


def test_the_writer_omits_defaults_and_the_parser_refuses_bad_axes():
    from an.ir.schema import Meta, SceneIR

    scene = SceneIR(meta=Meta(title="t", duration=1.0), timeline=[_shot(actions=[expression("c", "sad")])])
    md = ir_to_markdown(scene)
    block = md[md.index("kind: expression"):]
    assert "blend" not in block and "intensity" not in block and "duration" not in block
    with pytest.raises(ValueError, match="axes must be a mapping"):
        markdown_to_ir(md.replace("preset: sad", "preset: sad\n  axes: 3"))


def test_flatten_and_duration_of_treat_expression_like_play():
    from an.ir.compose import duration_of

    assert duration_of(expression("c", "happy")) == 0.0
    assert duration_of(expression("c", "happy", duration=1.5)) == 1.5
    [f.start for f in flatten(sequence(expression("c", "happy"), delay(1.0), expression("c", "sad")))] == [0.0, 1.0]


def test_iterate_grammar_names_every_preset_and_the_action():
    from an.expression import known_presets
    from an.iterate import _SYSTEM_PROMPT

    assert "kind: expression" in _SYSTEM_PROMPT
    for name in known_presets():
        assert name in _SYSTEM_PROMPT, name


def test_the_brow_table_is_gone_everywhere():
    """`rg _EMOTION_BROWS an/ .claude/ misc/docs/ README.md misc/demos` is empty (an#98)."""
    hits = []
    for base in ("an", ".claude", "misc/docs", "misc/demos", "README.md"):
        p = ROOT / base
        files = [p] if p.is_file() else [f for f in p.rglob("*") if f.suffix in (".py", ".md") and f.is_file()]
        for f in files:
            if "_EMOTION_BROWS" in f.read_text(encoding="utf-8", errors="ignore") and "wave6_research.md" not in f.name:
                hits.append(str(f.relative_to(ROOT)))
    assert not hits, hits


def test_compile_actions_never_sees_an_expression_leaf(mall):
    """An `expression` reaching `_compile_one` would raise TypeError; it is
    filtered into the solver instead — inside compositions too."""
    js = compile_shot(_shot(actions=[sequence(delay(0.2), expression("c", "happy", duration=0.5))]), mall=mall, fps=24, strict_assets=True)
    assert not [k for k in to_dict(js)["animations"] if k.startswith("__tween__") or k.startswith("__play__")]


# ---------------------------------------------------------------- factory + character validate


def test_the_factory_declares_the_variant_sets_and_their_art(mall):
    desc = mall["characters"]["c"]
    assert set(desc["asset_sets"]) >= {"viseme", "eyelid", "viseme@happy", "viseme@sad"}
    assert desc["asset_sets"]["viseme@happy"]["A"] == "mouth_a_happy"
    mouth = desc["skins"]["default"]["slots"]["mouth"]
    assert mouth["mouth_a_happy"]["path"] == "parts/mouth/mouth_a_happy.svg"
    assert (mouth["mouth_a_happy"]["x"], mouth["mouth_a_happy"]["y"]) == (mouth["mouth_a"]["x"], mouth["mouth_a"]["y"])
    assert "viseme@happy" not in mall["characters"]["plain"]["asset_sets"]


def test_character_validate_rules_for_variants(tmp_path):
    from an.characters.validate import validate_character

    new_character(tmp_path, name="v", seed="v", use_dicebear=False, overwrite=True)
    report = validate_character(tmp_path / "v", name="v")
    assert report.passed, [f.description for f in report.findings]
    desc_path = tmp_path / "v" / "character.json"
    raw = json.loads(desc_path.read_text(encoding="utf-8"))
    raw["asset_sets"]["viseme@bored"] = dict(raw["asset_sets"]["viseme@happy"])
    del raw["asset_sets"]["viseme@sad"]["D"]
    desc_path.write_text(json.dumps(raw), encoding="utf-8")
    report = validate_character(tmp_path / "v", name="v")
    descs = [(f.severity, f.description) for f in report.findings]
    assert any(s == "error" and "viseme@bored" in d for s, d in descs), descs
    assert any(s == "warning" and "viseme@sad" in d and "['D']" in d for s, d in descs), descs
    del raw["asset_sets"]["viseme"]
    desc_path.write_text(json.dumps(raw), encoding="utf-8")
    report = validate_character(tmp_path / "v", name="v")
    assert any(f.severity == "error" and "no neutral" in f.description for f in report.findings)


def test_the_mouth_variant_art_is_a_corner_move_only():
    from an.characters.mouth_set import generate_default_mouths

    neutral = generate_default_mouths(shapes=["d"])["mouth_d"]
    happy = generate_default_mouths(shapes=["d"], smile=0.35, form="happy")["mouth_d_happy"]
    assert neutral != happy
    # Same canvas, same opening: only the corner control points differ.
    assert re.search(r'viewBox="[^"]+"', neutral).group(0) == re.search(r'viewBox="[^"]+"', happy).group(0)


# ---------------------------------------------------------------- an#98 review: the defects, pinned


def _pose(js, t):
    return evaluate_timeline(timeline_from_scene(js), t)


def test_the_mouth_returns_to_neutral_after_a_variant_span(mall):
    """The runtime keeps the last texture a property set; with nothing
    re-asserting `viseme` the mouth stayed on `X_happy` for the rest of the
    shot after a happy span (review #2). A neutral rest hold now runs the
    whole shot under any variant, and name order lets the variant win where
    it is live."""
    js = compile_shot(_shot(actions=[expression("c", "happy", duration=1.0, blend=0.0)]), mall=mall, fps=24, strict_assets=True)
    assert _pose(js, 0.5)[("c/head/mouth", "viseme@happy")] == "X"
    late = _pose(js, 1.5)
    assert late[("c/head/mouth", "viseme")] == "X" and ("c/head/mouth", "viseme@happy") not in late
    # The dialogue sugar too: after a [happy] line on the variant set, neutral.
    line = Dialogue(speaker="c", text="hi", emotion="happy", start=0.2, duration=0.5, viseme_track=_track("X", "D", "X"))
    js = compile_shot(_shot(dialogue=[line]), mall=mall, fps=24, strict_assets=True)
    assert _pose(js, 0.4)[("c/head/mouth", "viseme@happy")] == "D"
    assert _pose(js, 1.5)[("c/head/mouth", "viseme")] == "X"


def test_no_frame_carries_a_hold_and_the_lines_own_key(mall):
    """Review #12: the hold's frame-ceiled end used to land on the line's first
    frame and the next hold on the line clip's last frame; the runtime
    resolves two mouth properties by NAME order, so `X_happy` flicked over a
    neutral line's first key. Holds now stop one frame short on both sides."""
    line = Dialogue(speaker="c", text="hi", start=0.5, duration=0.5, viseme_track=_track("X", "D", "X"))
    js = compile_shot(_shot(actions=[expression("c", "happy", blend=0.0)]), mall=mall, fps=24, strict_assets=True)
    js = compile_shot(_shot(actions=[expression("c", "sad", blend=0.0)], dialogue=[line]), mall=mall, fps=24, strict_assets=True)
    # The line resolves to viseme@sad (declared by default); the holds are on
    # viseme@sad and viseme. At every frame, at most one hold-or-line key per
    # PROPERTY is live, and the line's frames carry the line's key alone.
    tl = timeline_from_scene(js)
    for f in range(0, 49):
        t = f / 24
        pose = evaluate_timeline(tl, t)
        mouth = {k[1]: v for k, v in pose.items() if k[0] == "c/head/mouth"}
        if 0.5 <= t <= 1.0 + 1e-9:
            assert mouth.get("viseme@sad") in ("X", "D"), (t, mouth)
    d = to_dict(js)
    (track,) = [tr for tr in d["timeline"]["tracks"] if tr["target_root"] == "c"]
    holds = [c for c in track["clips"] if "__face_mouth__" in c["animation_id"]]
    line_clip = next(c for c in track["clips"] if "__viseme__" in c["animation_id"])
    for h in holds:
        h_end = h["start_time"] + h["duration"]
        assert not (h["start_time"] <= line_clip["start_time"] <= h_end + 1e-9), (h, line_clip)
        assert not (h["start_time"] - 1e-9 <= line_clip["start_time"] + line_clip["duration"] <= h_end), (h, line_clip)


def test_a_wide_lid_is_reachable_when_the_rig_has_the_art(mall, tmp_path):
    """Review #1: the blink term returned 0 outside a blink, so any positive
    lid offset was clamped and the `WIDE` rung could never show."""
    import json as _json
    import shutil

    src = Path(mall["characters"].rootdir if hasattr(mall["characters"], "rootdir") else "") if False else None
    # Build a rig with WIDE art: copy `c`, add eye_l_wide/eye_r_wide, declare WIDE.
    from an.characters import new_character
    from an.project import init, load

    root = init(tmp_path / "p")
    new_character(root / "assets" / "characters", name="w", seed="w", use_dicebear=False, overwrite=True)
    cdir = root / "assets" / "characters" / "w"
    for side in ("l", "r"):
        shutil.copy(cdir / "parts" / f"eye_{side}_open.svg", cdir / "parts" / f"eye_{side}_wide.svg")
    raw = _json.loads((cdir / "character.json").read_text(encoding="utf-8"))
    raw["asset_sets"]["eyelid"]["WIDE"] = "wide"
    for slot, stem in (("left_eye", "eye_l"), ("right_eye", "eye_r")):
        open_att = raw["skins"]["default"]["slots"][slot]["open"]
        raw["skins"]["default"]["slots"][slot]["wide"] = {**open_att, "path": f"parts/{stem}_wide.svg"}
    (cdir / "character.json").write_text(_json.dumps(raw), encoding="utf-8")
    wmall = load(root).mall
    js = compile_shot(_shot(actions=[expression("w", "surprised", blend=0.0)], ref="w", entity="w"), mall=wmall, fps=24, strict_assets=True)
    assert _pose(js, 0.5)[("w/head/left_eye", "eyelid")] == "WIDE"
    # and a blink inside a wide expression still closes (12 s to catch one)
    js = compile_shot(_shot(actions=[expression("w", "surprised", blend=0.0)], ref="w", entity="w", duration=12.0), mall=wmall, fps=24, strict_assets=True)
    values = {k["value"] for k in _face_channels(js)[("w/head/left_eye", "eyelid")]}
    assert values >= {"WIDE", "CLOSED"}, values


def test_a_variant_without_the_rest_key_is_not_selected(mall):
    """Review #4: coverage ignored the terminal rest, so a variant lacking `X`
    was chosen and the line then had no rest key — no lip-sync at all."""
    import copy

    desc = copy.deepcopy(mall["characters"]["c"])
    del desc["asset_sets"]["viseme@happy"]["X"]
    m = {"characters": {"c": mall["characters"]["c"], "norest": desc}}
    line = Dialogue(speaker="n", text="hi", start=0.2, duration=0.6, viseme_track=_track("A", "D"))
    with pytest.warns(UserWarning, match=r"\['X'\]"):
        js = compile_shot(_shot(actions=[expression("n", "happy", blend=0.0)], dialogue=[line], ref="norest", entity="n"), mall=m, fps=24, strict_assets=True)
    assert _pose(js, 0.5)[("n/head/mouth", "viseme")] == "D"


def test_a_variant_key_missing_on_disk_falls_back_to_neutral(tmp_path):
    """Review #9: declared is not resolved — a variant whose art is missing on
    disk dropped the key with a warning and held the previous shape through it."""
    from an.characters import new_character
    from an.project import init, load

    root = init(tmp_path / "p")
    new_character(root / "assets" / "characters", name="g", seed="g", use_dicebear=False, overwrite=True)
    (root / "assets" / "characters" / "g" / "parts" / "mouth" / "mouth_d_happy.svg").unlink()
    m = load(root).mall
    line = Dialogue(speaker="g", text="hi", start=0.2, duration=0.6, viseme_track=_track("X", "D", "X"))
    with pytest.warns(CutoutCompileWarning, match="art missing on disk"):
        js = compile_shot(_shot(actions=[expression("g", "happy", blend=0.0)], dialogue=[line], ref="g", entity="g"), mall=m, fps=24)
    assert _pose(js, 0.5)[("g/head/mouth", "viseme")] == "D"


def test_axis_ranges_hold_on_what_reaches_the_rig():
    """Review #5: `preset_axes` clamped after scaling while the provider clamped
    before; overlapping spans could sum past an axis's range; the schema took
    negative durations and intensities above 1."""
    from pydantic import ValidationError

    from an.expression import DefaultExpressionProvider, preset_axes

    assert preset_axes("neutral", axes={"brow_height_l": 5.0}, intensity=0.5)["brow_height_l"] == 0.5
    shot = Shot(id="s", renderer="cutout", duration=1.0, entities=[AssetRef(kind="character", id="c", store="characters", ref="c")],
                actions=[expression("c", "surprised", blend=0.0), expression("c", "surprised", blend=0.0)])
    curves = {c.axis: c.samples for c in DefaultExpressionProvider().curves(shot, "c", fps=4)}
    assert max(curves["brow_height_l"]) == 1.0
    for bad in (dict(intensity=2.0), dict(intensity=-1.0), dict(duration=-1.0), dict(blend=-0.1)):
        with pytest.raises(ValidationError):
            ExpressionAction(target="c", preset="happy", **bad)


def test_validate_refuses_an_expression_on_an_unknown_entity(mall):
    from an.ir.schema import Meta, SceneIR

    scene = SceneIR(meta=Meta(title="t", duration=1.0), timeline=[_shot(actions=[expression("ghost", "happy")])])
    report = validate_semantic(scene, available_characters=mall["characters"])
    assert any("ghost" in f.description and f.severity == "error" for f in report.findings)


def test_a_declared_binding_naming_a_missing_slot_is_a_resolution_error(mall):
    import copy

    from an.expression import ExpressionResolutionError, binding_for, expression_problems
    from an.characters.schema import CharacterDescriptor

    raw = copy.deepcopy(mall["characters"]["c"])
    raw["expression_binding"] = [{"axis": "brow_height_l", "slot": "nose_ring", "property": "y", "gain": 1.0}]
    desc = CharacterDescriptor.model_validate(raw)
    with pytest.raises(ExpressionResolutionError, match="nose_ring"):
        binding_for(desc)
    assert any("nose_ring" in p for p in expression_problems(desc, preset="happy", who="c"))
    m = {"characters": {"c": mall["characters"]["c"], "bad": raw}}
    with pytest.raises(CutoutCompileError, match="nose_ring"):
        compile_shot(_shot(actions=[expression("b", "happy")], ref="bad", entity="b"), mall=m, fps=24, strict_assets=True)


def test_variants_derive_their_art_from_the_key_not_the_attachment_name():
    """Review #8: a promoted hand rig maps `X` to `mouth_shut`; only
    `mouth_x_<form>.svg` is ever drawn, so the declaration must say that."""
    from an.characters.factory import declare_mouth_variants
    from an.characters.schema import CharacterDescriptor

    desc = CharacterDescriptor(name="h")
    desc.asset_sets["viseme"]["X"] = "mouth_shut"
    desc.skins["default"].slots["mouth"]["mouth_shut"] = desc.skins["default"].slots["mouth"]["mouth_x"]
    declare_mouth_variants(desc, {"happy": 0.3})
    assert desc.asset_sets["viseme@happy"]["X"] == "mouth_x_happy"
    assert desc.skins["default"].slots["mouth"]["mouth_x_happy"].path == "parts/mouth/mouth_x_happy.svg"


def test_an_empty_expression_is_not_a_contributor(mall):
    """Review #16: `preset: None` with no axes, or `intensity: 0`, used to take
    the solver path and emit a rest-valued face clip — the compiled document
    must be identical to no expression at all."""
    plain = to_dict(compile_shot(_shot(), mall=mall, fps=24, strict_assets=True))
    for empty in (expression("c", None), expression("c", "angry", intensity=0.0)):
        assert to_dict(compile_shot(_shot(actions=[empty]), mall=mall, fps=24, strict_assets=True)) == plain


def test_the_squash_floor_holds_through_a_blink(mall):
    """Review #13: the floor was applied before the blink squash."""
    import copy

    from an.expression.binding import LID_SQUASH_GAIN

    raw = copy.deepcopy(mall["characters"]["c"])
    del raw["skins"]["default"]["slots"]["left_eye"]["closed"]
    del raw["skins"]["default"]["slots"]["right_eye"]["closed"]
    m = {"characters": {"c": mall["characters"]["c"], "sq": raw}}
    js = compile_shot(_shot(actions=[expression("q", None, axes={"lid_open_l": -1.0, "lid_open_r": -1.0}, blend=0.0)], ref="sq", entity="q", duration=12.0), mall=m, fps=24, strict_assets=False)
    ch = _face_channels(js)[("q/head/left_eye", "scale_y")]
    lo = min(k["value"] for k in ch)
    assert lo >= 0.05 * 1.0 - 1e-9 and lo < 1.0 - LID_SQUASH_GAIN + 1e-9, lo


def test_subtract_intervals_normalises_inverted_holes():
    assert compile_mod._subtract_intervals((0.0, 10.0), [(6.0, 4.0)]) == [(0.0, 4.0), (6.0, 10.0)]


def test_two_axes_on_one_key_sum(mall):
    """Mutant 1b: the solver's accumulation (`+=`) — the default binding never
    shares a (slot, property), so a declared binding that does is the guard."""
    import copy

    from an.expression.binding import BROW_HEIGHT_TRAVEL

    raw = copy.deepcopy(mall["characters"]["c"])
    raw["expression_binding"] = [
        {"axis": "brow_height_l", "slot": "left_brow", "property": "y", "gain": -1.0, "rig_scaled": False},
        {"axis": "brow_height_r", "slot": "left_brow", "property": "y", "gain": -1.0, "rig_scaled": False},
    ]
    m = {"characters": {"c": mall["characters"]["c"], "two": raw}}
    js = compile_shot(_shot(actions=[expression("t", None, axes={"brow_height_l": 0.5, "brow_height_r": 0.25}, blend=0.0)], ref="two", entity="t"), mall=m, fps=24, strict_assets=True)
    ent = next(e for e in js.scene.children if e.name == "t")
    rest = next(c for c in next(c for c in ent.children if c.name == "head").children if c.name == "left_brow").transform.y
    assert _pose(js, 0.5)[("t/head/left_brow", "y")] == pytest.approx(rest - 0.75)


def test_pinned_frames_min_pairwise_is_a_minimum_over_distinct_frames(tmp_path):
    """Mutant 13 and review #11, on synthetic frames."""
    from types import SimpleNamespace as NS

    import numpy as np

    from an.bench.png import write_png
    from an.bench.run import pinned_frames_min_pairwise_changed_px

    frames = tmp_path / "frames"
    frames.mkdir()
    base = np.zeros((4, 4, 3), dtype=np.uint8)
    for i, n_changed in enumerate((0, 1, 3, 10)):
        img = base.copy()
        img.reshape(-1, 3)[:n_changed] = 255
        write_png(frames / f"frame_{i:06d}.png", img)
    capture = NS(fps=4, shots=[NS(shot_id="s", frames_dir=frames, frame_count=4)])
    v = pinned_frames_min_pairwise_changed_px(capture, [0.0, 0.25, 0.5, 0.75])
    assert v.state == "measured" and v.value == 1, v  # frames 0 vs 1
    assert v.extra["closest_pair"] == ["f0000", "f0001"]
    v = pinned_frames_min_pairwise_changed_px(capture, [0.0, 0.05, 0.1])  # all one frame
    assert v.state == "unavailable"


def test_the_distinguishability_floor_is_positive_and_below_the_measured_minimum():
    """Mutant 14: the threshold is a fraction of what the goldens actually show, never 0."""
    from itertools import combinations

    from tests.test_expression_goldens import FACE_CROP, MIN_PAIRWISE_CHANGED_PX, _goldens

    goldens = _goldens()
    if len(goldens) < 8:
        pytest.skip("goldens not blessed")
    r0, r1, c0, c1 = FACE_CROP
    actual = min(int((goldens[a].astype(int) != goldens[b].astype(int)).any(axis=-1)[r0:r1, c0:c1].sum()) for a, b in combinations(sorted(goldens), 2))
    assert 0 < MIN_PAIRWISE_CHANGED_PX <= actual // 2, (MIN_PAIRWISE_CHANGED_PX, actual)


def test_variant_forms_parse_case_insensitively():
    from an.characters.cli import _parse_variants

    assert _parse_variants("HAPPY, Sad") == {"happy": 0.35, "sad": -0.35}
