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

from .test_swap_channels import _evaluate, _python_timeline

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def mall():
    with tempfile.TemporaryDirectory() as d:
        root = init(Path(d) / "p")
        new_character(root / "assets" / "characters", name="c", seed="c", use_dicebear=False, overwrite=True)
        new_character(root / "assets" / "characters", name="plain", seed="plain", use_dicebear=False, overwrite=True, mouth_variants={})
        yield load(root).mall


def _track(*codes):
    return VisemeTrack(keyframes=[VisemeKeyframe(time=0.2 * i, viseme=c) for i, c in enumerate(codes)])


def _shot(actions=(), dialogue=(), duration=2.0, ref="c", entity="c"):
    return Shot(
        id="s", style="cutout", duration=duration,
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
        poses.append(_evaluate(_python_timeline(js), 1.0))
    assert poses[0] == poses[1]
    pose = poses[0]
    neutral = _evaluate(_python_timeline(compile_shot(_shot(), mall=mall, fps=24, strict_assets=True)), 1.0)
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
        if name not in row["scenes"]:
            continue  # a fixture added after the row (`expressions` itself, at first)
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
    assert checked >= 7, checked


def test_authored_wins_over_the_expression_with_a_warning(mall):
    shot = _shot(actions=[expression("c", "angry", blend=0.0), tween("c/head/left_brow", "rotation", to=1.0, duration=2.0)])
    with pytest.warns(CutoutCompileWarning, match="authored channel on 'c/head/left_brow':'rotation'"):
        js = compile_shot(shot, mall=mall, fps=24, strict_assets=True)
    pose = _evaluate(_python_timeline(js), 2.0)
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
    shot = Shot(id="s", style="cutout", duration=1.0, entities=[AssetRef(kind="character", id="p", store="characters", ref="p-v1")], actions=[expression("p", "happy")])
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
    pose = _evaluate(_python_timeline(js), 0.5)
    assert pose[("c/head/mouth", "viseme@happy")] == "D"
    assert ("c/head/mouth", "viseme") not in pose, "one mouth swap property live per instant"


def test_a_silent_expression_holds_the_variants_rest_outside_lines(mall):
    line = Dialogue(speaker="c", text="hi", start=1.0, duration=0.5, viseme_track=_track("X", "D", "X"))
    js = compile_shot(_shot(actions=[expression("c", "happy", blend=0.0)], dialogue=[line]), mall=mall, fps=24, strict_assets=True)
    tl = _python_timeline(js)
    assert _evaluate(tl, 0.5)[("c/head/mouth", "viseme@happy")] == "X"
    assert _evaluate(tl, 1.2)[("c/head/mouth", "viseme@happy")] == "D"
    assert _evaluate(tl, 1.9)[("c/head/mouth", "viseme@happy")] == "X"
    holds = [k for k in to_dict(js)["animations"] if k.startswith("__face_mouth__")]
    assert len(holds) == 2, "one hold before the line, one after"


def test_a_missing_variant_falls_back_to_the_neutral_set_with_a_warning(mall):
    line = Dialogue(speaker="plain", text="hi", start=0.2, duration=0.6, viseme_track=_track("X", "D", "X"))
    with pytest.warns(UserWarning, match="viseme@happy"):
        js = compile_shot(_shot(actions=[expression("plain", "happy", blend=0.0)], dialogue=[line], ref="plain", entity="plain"), mall=mall, fps=24, strict_assets=True)
    assert _evaluate(_python_timeline(js), 0.5)[("plain/head/mouth", "viseme")] == "D"


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
