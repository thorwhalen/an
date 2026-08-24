"""The swap-channel generalisation (an#87), proven against a real art package.

The fixture is `tests/fixtures/characters/gale/` — a committed, hand-authored
character carrying a multi-key `hands` set (its own `left_hand` slot on its
own bone) and a `body_facing` set (three torso drawings). Neither set name
appears anywhere in the renderer or the compiler: they animate purely because
the descriptor declares them and the skin carries their art. That is the
wave's done-when, and `test_the_renderer_knows_nothing_about_the_fixture_sets`
pins it structurally.

The two mutation-tested traps from the epic brief live here too:

- **(a)** a swap channel is always step-interpolated — an authored non-step
  easing on a swap tween is FORCED to step with a warning;
- **(b)** an unknown swap key is LOUD — the compiler refuses an undeclared
  key outright, and the runtime (exercised via node on the real extracted
  functions) throws naming node, set, and known keys, where the old viseme
  path silently kept the previous texture.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from an.adapters.cutout.compile import (
    CutoutCompileError,
    CutoutCompileWarning,
    compile_shot,
)
from an.ir.schema import AssetRef, SetAction, Shot, TweenAction
from an.stores.characters import CharactersStore

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "characters"
RUNTIME_JS = (
    Path(__file__).resolve().parents[1] / "an" / "data" / "cutout_runtime" / "runtime.js"
)


@pytest.fixture()
def gale_store(tmp_path):
    """The committed gale package, copied where a CharactersStore can see it."""
    shutil.copytree(FIXTURES / "gale", tmp_path / "gale")
    return CharactersStore(tmp_path)


def _shot(actions=(), *, duration=2.0):
    return Shot(
        id="s1",
        style="cutout",
        duration=duration,
        entities=[
            AssetRef(kind="character", id="gale", store="characters", ref="gale")
        ],
        actions=list(actions),
    )


def _python_timeline(scene):
    """The compiled scene as the Python spec's Timeline, for evaluating poses."""
    from an.adapters.cutout.channel import Channel, Keyframe
    from an.adapters.cutout.clip import Clip
    from an.adapters.cutout.timeline import PlacedClip, Timeline, Track

    clips = {
        aid: Clip(
            aid,
            duration=a.duration,
            channels=[
                Channel(
                    ch.target,
                    ch.property,
                    [
                        Keyframe(
                            k.time,
                            k.value,
                            tuple(k.easing) if isinstance(k.easing, list) else k.easing,
                        )
                        for k in ch.keyframes
                    ],
                )
                for ch in a.channels
            ],
        )
        for aid, a in scene.animations.items()
    }
    return Timeline(
        duration=scene.timeline.duration,
        tracks=[
            Track(
                t.target_root,
                [
                    PlacedClip(clips[p.animation_id], p.start_time, p.duration, p.speed)
                    for p in t.clips
                ],
            )
            for t in scene.timeline.tracks
        ],
    )


def _evaluate(tl, t):
    from an.adapters.cutout.timeline import evaluate_timeline

    return evaluate_timeline(tl, t)


def _code_only(text: str, *, lang: str) -> str:
    """Source with comments and string/docstring literals removed, so a set
    name in PROSE ("hands off") is not mistaken for control flow."""
    if lang == "js":
        text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
        return re.sub(r"//[^\n]*", "", text)
    import io
    import tokenize

    out = []
    for tok in tokenize.generate_tokens(io.StringIO(text).readline):
        if tok.type in (tokenize.COMMENT, tokenize.STRING):
            continue
        out.append(tok.string)
    return " ".join(out)


def _channels(scene, prop):
    return [
        ch
        for anim in scene.animations.values()
        for ch in anim.channels
        if ch.property == prop
    ]


# ------------------------------------------------------------- the projection


def test_every_declared_set_projects_onto_the_slot_that_carries_its_art(gale_store):
    scene = compile_shot(_shot(), mall={"characters": gale_store})
    gale = scene.scene.children[0]
    by_name = {c.name: c for c in gale.children}
    hand = by_name["left_hand"]
    assert hand.visual.asset_sets["hands"] == {
        "fist": "gale.left_hand.fist",
        "palm": "gale.left_hand.palm",
        "point": "gale.left_hand.point",
    }
    torso = by_name["torso"]
    assert torso.visual.asset_sets["body_facing"] == {
        "front": "gale.torso.torso",
        "left": "gale.torso.torso_left",
        "right": "gale.torso.torso_right",
    }
    # ONE eyelid set projects onto BOTH eye slots — the reason eye attachment
    # names are the shared per-slot keys `open`/`closed` since 0.3.0.
    head = by_name["head"]
    eyes = {c.name: c for c in head.children}
    assert eyes["left_eye"].visual.asset_sets["eyelid"]["CLOSED"] == (
        "gale.left_eye.closed"
    )
    assert eyes["right_eye"].visual.asset_sets["eyelid"]["CLOSED"] == (
        "gale.right_eye.closed"
    )
    # Every projected alias is a registered, staged texture.
    for node in (hand, torso):
        for key_map in node.visual.asset_sets.values():
            for alias in key_map.values():
                assert alias in scene.assets.textures


def test_the_renderer_knows_nothing_about_the_fixture_sets():
    """The done-when, structurally: ZERO renderer change per new set.

    `hands`, `body_facing`, and `gale` must not appear in the runtime or the
    compiler — the sets animate purely as descriptor data through the one
    generic swap path. If this fails, someone special-cased a set name, which
    is the exact defect class an#87 removed for `viseme`.
    """
    runtime = _code_only(RUNTIME_JS.read_text(encoding="utf-8"), lang="js")
    compiler = _code_only(
        (
            Path(__file__).resolve().parents[1]
            / "an"
            / "adapters"
            / "cutout"
            / "compile.py"
        ).read_text(encoding="utf-8"),
        lang="py",
    )
    # Comments and docstrings are stripped first: "hands" is an English word,
    # and a set name in prose is not control flow. Code tokens only.
    for name in ("hands", "body_facing", "gale"):
        assert not re.search(rf"\b{name}\b", runtime), (
            f"runtime.js mentions {name!r} — a set name became control flow"
        )
        assert not re.search(rf"\b{name}\b", compiler), (
            f"compile.py mentions {name!r} — a set name became control flow"
        )


def test_viseme_appears_in_the_runtime_only_as_a_set_name():
    """The other half of the done-when's `rg viseme` clause.

    After an#87 the runtime's only `viseme`-flavoured control flow is the
    procedural mouth declaring `{ viseme: drawMouthShape }` in its OWN
    `_anDrawSets` — a data declaration on the object, exactly how a sprite
    declares its sets. `applyProperty` must carry no viseme case and
    `setVisemeOnMouth` must not exist.
    """
    runtime = RUNTIME_JS.read_text(encoding="utf-8")
    assert "setVisemeOnMouth" not in runtime
    switch = re.search(
        r"function applyProperty\([^)]*\)\s*\{.*?\n    \}", runtime, re.S
    ).group(0)
    assert not re.search(r"case\s+['\"]viseme['\"]", switch)


# ------------------------------------------------------ authoring via actions


def test_an_authored_swap_compiles_to_a_hold_channel(gale_store):
    scene = compile_shot(
        _shot(
            [
                SetAction(target="gale/left_hand", property="hands", value="fist", at=0.0),
                SetAction(target="gale/left_hand", property="hands", value="point", at=1.2),
            ]
        ),
        mall={"characters": gale_store},
    )
    (ch,) = _channels(scene, "hands")
    assert ch.target == "gale/left_hand"
    assert [(k.time, k.value, k.easing) for k in ch.keyframes] == [
        (0.0, "fist", "step"),
        (1.2, "point", "step"),
    ]
    # The channel HOLDS to the shot end — one clip spanning from the first
    # set, not a 0.001s window whose firing depended on frame alignment.
    placed = [
        p
        for t in scene.timeline.tracks
        for p in t.clips
        if p.animation_id in {
            aid for aid, a in scene.animations.items() if a.channels and a.channels[0].property == "hands"
        }
    ]
    assert placed[0].start_time == 0.0
    assert placed[0].duration == pytest.approx(2.0)


def test_a_non_frame_aligned_numeric_set_still_fires(gale_store):
    """The pre-existing defect the hold shape fixes, pinned on a numeric set:
    `at=1.02` at 24/30fps has no frame inside a 1ms window, so the old
    compilation silently never applied it."""
    scene = compile_shot(
        _shot([SetAction(target="gale/torso", property="scale_x", value=2.0, at=1.02)]),
        mall={"characters": gale_store},
    )
    (ch,) = _channels(scene, "scale_x")
    # By id, not position: blink clips sit first on the track (an#88).
    placed = next(
        p
        for t in scene.timeline.tracks
        for p in t.clips
        if p.animation_id.startswith("__set__")
    )
    assert placed.start_time == pytest.approx(1.02)
    assert placed.duration == pytest.approx(2.0 - 1.02)
    assert ch.keyframes[0].value == 2.0
    # "Still fires" as EVIDENCE, not inference: through the spec evaluator the
    # pose carries the value on every frame from the first sample after `at`
    # — measured, the old 0.001s window hit 0 of 49 frames at 24 fps.
    tl = _python_timeline(scene)
    frames_with_value = [
        i for i in range(49) if _evaluate(tl, i / 24.0).get(("gale/torso", "scale_x")) == 2.0
    ]
    assert frames_with_value and frames_with_value[0] == 25


def test_trap_a_non_step_easing_on_a_swap_tween_is_forced_to_step(gale_store):
    """Epic trap (a), as shipped: swap channels are stepped by FORMAT."""
    with pytest.warns(CutoutCompileWarning, match="always step-interpolated"):
        scene = compile_shot(
            _shot(
                [
                    TweenAction(
                        target="gale/torso",
                        property="body_facing",
                        from_value="front",
                        to_value="left",
                        duration=1.0,
                        easing="ease_in_out",
                    )
                ]
            ),
            mall={"characters": gale_store},
        )
    (ch,) = _channels(scene, "body_facing")
    assert ch.keyframes[0].easing == "step"


def test_trap_b_an_undeclared_key_is_refused_naming_the_declared_ones(gale_store):
    with pytest.raises(CutoutCompileError, match=r"fist.*palm.*point"):
        compile_shot(
            _shot(
                [SetAction(target="gale/left_hand", property="hands", value="FISTT")]
            ),
            mall={"characters": gale_store},
        )


def test_an_undeclared_set_is_refused_naming_the_declared_ones(gale_store):
    with pytest.raises(CutoutCompileError, match="body_facing"):
        compile_shot(
            _shot([SetAction(target="gale/left_hand", property="handz", value="fist")]),
            mall={"characters": gale_store},
        )


def test_a_swap_on_the_wrong_node_names_the_right_one(gale_store):
    with pytest.raises(CutoutCompileError, match="gale/left_hand"):
        compile_shot(
            _shot([SetAction(target="gale/torso", property="hands", value="fist")]),
            mall={"characters": gale_store},
        )


def test_a_used_key_with_missing_art_escalates_under_strict_assets(
    gale_store, tmp_path
):
    """Usage-aware escalation: an INVENTORY gap stays non-fatal (the package
    still compiles under strict_assets when nothing references the gap), but
    a key the timeline USES turns fatal — a wrong picture wearing a right
    one's clothes."""
    (tmp_path / "gale" / "parts" / "hand_point.svg").unlink()
    shot = _shot([SetAction(target="gale/left_hand", property="hands", value="fist")])
    compile_shot(shot, mall={"characters": gale_store}, strict_assets=True)

    using = _shot([SetAction(target="gale/left_hand", property="hands", value="point")])
    with pytest.raises(CutoutCompileError, match="point"):
        compile_shot(using, mall={"characters": gale_store}, strict_assets=True)
    # Non-strict: audible, and the channel is dropped rather than compiled
    # into a runtime crash.
    with pytest.warns(CutoutCompileWarning, match="point"):
        scene = compile_shot(using, mall={"characters": gale_store})
    assert not _channels(scene, "hands")


# ----------------------------------------------------- the runtime, extracted


def _run_node(script: str) -> str:
    if shutil.which("node") is None:
        pytest.skip("node not installed")
    proc = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, timeout=30
    )
    assert proc.returncode == 0, f"node failed: {proc.stderr}"
    return proc.stdout.strip()


def _extract(name: str) -> str:
    src = RUNTIME_JS.read_text(encoding="utf-8")
    m = re.search(rf"function {name}\([^)]*\)\s*\{{.*?\n    \}}", src, re.S)
    assert m, f"{name} not found in runtime.js"
    return m.group(0)


def test_trap_b_the_runtime_throws_on_an_unknown_swap_key():
    """Executed against the real extracted applySwap — not grepped.

    The old viseme path silently kept the previous texture on an unknown key
    (seven distinct silent paths, wave5_research.md §1); the value domain is
    now as loud as the target and property domains.
    """
    script = "\n".join(
        [
            "function refitToBox() {}",
            "const PIXI = { Assets: { get: (id) => ({ id }) } };",
            _extract("unknownSwapKey"),
            _extract("applySwap"),
            "const child = { _anAssetSets: { hands: { fist: 'a1', palm: 'a2' } } };",
            "const node = { name: 'gale/left_hand', children: [child] };",
            "try { applySwap(child, node, 'hands', 'FISTT'); console.log('SILENT'); }",
            "catch (e) { console.log('RAISED: ' + e.message); }",
        ]
    )
    out = _run_node(script)
    assert out.startswith("RAISED"), f"unknown swap key was not refused: {out}"
    assert "FISTT" in out and "fist" in out and "palm" in out, (
        "the error must name the bad key and the known keys"
    )


def test_the_runtime_swaps_a_texture_and_refits_for_a_known_key():
    script = "\n".join(
        [
            "let refitted = 0;",
            "function refitToBox() { refitted++; }",
            "const PIXI = { Assets: { get: (id) => ({ id }) } };",
            _extract("unknownSwapKey"),
            _extract("applySwap"),
            "const child = { _anAssetSets: { hands: { fist: 'a1' } } };",
            "const node = { name: 'gale/left_hand', children: [child] };",
            "applySwap(child, node, 'hands', 'fist');",
            "console.log(JSON.stringify({ tex: child.texture.id, refitted }));",
        ]
    )
    got = json.loads(_run_node(script))
    assert got == {"tex": "a1", "refitted": 1}


def test_apply_property_routes_an_unknown_set_to_a_loud_error():
    """applyProperty's default case: no matching set on the node → throw
    listing the built-ins AND the node's actual set names."""
    script = "\n".join(
        [
            _extract("applyProperty"),
            "const child = { _anAssetSets: { hands: { fist: 'a1' } } };",
            "const node = { name: 'gale/left_hand', children: [child], scale: {}, skew: {}, pivot: {} };",
            "try { applyProperty(node, 'body_facing', 'left'); console.log('SILENT'); }",
            "catch (e) { console.log('RAISED: ' + e.message); }",
        ]
    )
    out = _run_node(script)
    assert out.startswith("RAISED")
    assert "body_facing" in out and "hands" in out, (
        "the error must name the missing set and the node's real sets"
    )


def test_the_procedural_mouth_declares_viseme_as_a_draw_set():
    """The drawn mouth's swap vocabulary is data on the object, not a name
    check in the apply path: applyProperty finds `viseme` in _anDrawSets and
    calls the redraw function through the same generic branch as textures."""
    script = "\n".join(
        [
            "const calls = [];",
            "function drawMouthShape(g, code) { calls.push(code); }",
            _extract("unknownSwapKey"),
            _extract("applySwap"),
            _extract("applyProperty"),
            "const g = { _anDrawSets: { viseme: { keys: ['A', 'X'], apply: drawMouthShape } } };",
            "const node = { name: 'c/head/mouth', children: [g], scale: {}, skew: {}, pivot: {} };",
            "applyProperty(node, 'viseme', 'A');",
            "console.log(JSON.stringify(calls));",
        ]
    )
    assert json.loads(_run_node(script)) == ["A"]


def test_the_drawn_mouth_is_loud_on_an_unknown_shape():
    """drawMouthShape's `|| VISEME_SHAPES.X` fallback silently drew the
    closed mouth for typos and for lowercase codes. Gone."""
    src = RUNTIME_JS.read_text(encoding="utf-8")
    shapes = re.search(r"const VISEME_SHAPES = \{.*?\n    \};", src, re.S).group(0)
    script = "\n".join(
        [
            shapes,
            "const _LIP_COLOR = 0, _MOUTH_FILL = 0, _TEETH_COLOR = 0, _TONGUE_COLOR = 0;",
            _extract("drawMouthShape"),
            "const g = { clear(){}, lineStyle(){}, beginFill(){}, endFill(){}, moveTo(){}, quadraticCurveTo(){}, drawRect(){}, drawEllipse(){} };",
            "try { drawMouthShape(g, 'nope'); console.log('SILENT'); }",
            "catch (e) { console.log('RAISED: ' + e.message); }",
        ]
    )
    out = _run_node(script)
    assert out.startswith("RAISED") and "nope" in out


# --------------------------------------------------------------- browser lane


@pytest.mark.browser
def test_an_authored_hand_and_facing_swap_change_the_pixels(gale_store, tmp_path):
    """The done-when, rendered: both fixture sets animate from authored
    actions, and the frames on either side of each swap differ.

    Runs on the labelled browser lane; the compile-level tests above are what
    every PR sees.
    """
    import numpy as np
    from PIL import Image

    from an.adapters._base import RenderContext
    from an.adapters.cutout.render import CutoutRenderer

    shot = _shot(
        [
            SetAction(target="gale/left_hand", property="hands", value="fist", at=0.0),
            SetAction(target="gale/left_hand", property="hands", value="point", at=1.0),
            SetAction(target="gale/torso", property="body_facing", value="front", at=0.0),
            SetAction(target="gale/torso", property="body_facing", value="left", at=1.0),
        ]
    )
    ctx = RenderContext(
        mall={"characters": gale_store},
        work_dir=tmp_path / "work",
        fps=12,
        resolution=(320, 240),
        strict_assets=True,
    )
    ctx.work_dir.mkdir(parents=True, exist_ok=True)
    result = CutoutRenderer().render(shot, ctx)

    def frame_at(t: float) -> np.ndarray:
        out = tmp_path / f"f{t}.png"
        subprocess.run(
            [
                "ffmpeg", "-v", "error", "-ss", str(t), "-i", str(result.mp4_path),
                "-frames:v", "1", "-y", str(out),
            ],
            check=True,
        )
        return np.asarray(Image.open(out).convert("RGB"))

    before, after = frame_at(0.5), frame_at(1.5)
    assert (before != after).any(), (
        "the authored swaps at t=1.0 must change the rendered pixels"
    )


# --------------------------------------------- the adversarial-review round


def test_a_set_holds_only_until_the_next_action_on_its_property(gale_store):
    """A `set` must not mask a later `tween` on the same target/property.

    The first hold rule ("first `at` → shot end") appended the set clip after
    every tween clip, and later-wins evaluation let `set x=50 at 0` pin the
    property for the whole shot — the most common authoring shape ("set the
    start pose, then animate") became a no-op tween (an#87 review). The hold
    now ends at the next action's start.
    """
    from an.ir.compose import delay, sequence

    scene = compile_shot(
        _shot(
            [
                SetAction(target="gale/torso", property="x", value=50.0, at=0.0),
                sequence(
                    delay(1.0),
                    TweenAction(
                        target="gale/torso", property="x", from_value=0.0,
                        to_value=100.0, duration=0.5,
                    ),
                ),
                SetAction(target="gale/torso", property="x", value=-7.0, at=1.8),
            ],
            duration=3.0,
        ),
        mall={"characters": gale_store},
    )
    holds = sorted(
        (p.start_time, p.duration)
        for t in scene.timeline.tracks
        for p in t.clips
        if p.animation_id.startswith("__set__")
    )
    # Two runs: [0.0, 1.0) ends at the tween's start; [1.8, 3.0] runs to end.
    assert holds == [
        (0.0, pytest.approx(1.0)),
        (pytest.approx(1.8), pytest.approx(1.2)),
    ]
    # And, evaluated through the Python spec, the tween is visible at t=1.25.
    tl = _python_timeline(scene)
    key = ("gale/torso", "x")
    assert _evaluate(tl, 0.5)[key] == 50.0
    assert _evaluate(tl, 1.25)[key] == pytest.approx(50.0)
    assert _evaluate(tl, 1.5)[key] == pytest.approx(100.0)
    assert _evaluate(tl, 2.5)[key] == -7.0


def test_the_ir_validator_reads_the_migrated_descriptor(tmp_path):
    """Every committed pre-0.3.0 descriptor has no `asset_sets` on disk, so a
    validator reading the raw dict refused swaps the compiler accepts —
    `an validate`/`an iterate` were unusable for swaps on every example
    project (an#87 review)."""
    from an.ir.validate import validate_semantic
    from an.ir.schema import Meta, SceneIR

    old_doc = {
        "kind": "CharacterDescriptor",
        "schema_version": "0.1.0",
        "name": "robo",
        "viseme_map": {"A": "mouth_a", "X": "mouth_x"},
    }
    scene = SceneIR(
        meta=Meta(title="t", duration=1.0),
        timeline=[
            Shot(
                id="s",
                style="cutout",
                duration=1.0,
                entities=[AssetRef(kind="character", id="robo", store="characters", ref="robo")],
                actions=[
                    SetAction(target="robo/head/mouth", property="viseme", value="A"),
                    SetAction(target="robo/head/left_eye", property="eyelid", value="CLOSED"),
                ],
            )
        ],
    )
    report = validate_semantic(scene, available_characters={"robo": old_doc})
    assert report.passed, [f.description for f in report.findings]


def test_the_ir_validator_sees_swaps_nested_in_compositions(gale_store):
    """The documented `start:` idiom wraps every leaf in a `sequence`; a gate
    that walked only top-level actions had a hole exactly there."""
    from an.ir.compose import delay, sequence
    from an.ir.validate import validate_semantic
    from an.ir.schema import Meta, SceneIR

    scene = SceneIR(
        meta=Meta(title="t", duration=2.0),
        timeline=[
            _shot(
                [
                    sequence(
                        delay(0.5),
                        SetAction(target="gale/left_hand", property="hands", value="NOPE"),
                    )
                ]
            )
        ],
    )
    report = validate_semantic(scene, available_characters=gale_store)
    assert not report.passed
    assert any("NOPE" in f.description for f in report.findings)


@pytest.mark.parametrize(
    "rig",
    [
        "misc/bench/corpus/graded_field/assets/characters/graded-field-rig",
        "misc/bench/corpus/saturated_outline/assets/characters/saturated-rig",
    ],
)
def test_the_committed_corpus_rigs_pass_the_asset_set_checks(rig):
    """`an character validate` must validate the MIGRATED document: both
    corpus rigs are 0.1.0 on disk, and read raw the 0.3.0 default `eyelid` set
    was checked against un-renamed attachments — every one failed its own
    validator (an#87 review). The only blocking findings they may carry are
    the honest missing-closed-eye ones."""
    from an.characters.validate import validate_character

    report = validate_character(Path(__file__).resolve().parents[1] / rig)
    blocking = [f for f in report.findings if f.severity == "error"]
    assert all("eye_" in f.ir_path and "closed" in f.ir_path for f in blocking), [
        (f.ir_path, f.description) for f in blocking
    ]


def test_the_transform_vocabulary_is_one_set_in_three_places():
    """`an.base.TRANSFORM_PROPERTIES` is the SSOT the IR validator and the
    character validator import; the compiler DERIVES its rest-value table from
    `TransformJSON`, so this is the pin that keeps the derivation equal to the
    declared vocabulary — and the pin the review found missing."""
    from an.adapters.cutout.compile import (
        PROCEDURAL_MOUTH_SETS,
        RUNTIME_APPLIED_PROPERTIES,
        _PROPERTY_REST_VALUES,
    )
    from an.base import TRANSFORM_PROPERTIES
    from an.ir import validate as ir_validate

    assert set(_PROPERTY_REST_VALUES) == TRANSFORM_PROPERTIES
    assert RUNTIME_APPLIED_PROPERTIES == TRANSFORM_PROPERTIES
    assert ir_validate._TRANSFORM_PROPERTIES == TRANSFORM_PROPERTIES
    assert ir_validate._PROCEDURAL_SWAP_SETS == PROCEDURAL_MOUTH_SETS


def test_a_lowercase_authored_viseme_on_a_procedural_rig_is_refused():
    """Exact key match, like every other set. The first cut upper-cased at
    check time and then emitted the raw code, so a compile-ACCEPTED scene
    reached the runtime's case-sensitive throw (an#87 review)."""
    shot = Shot(
        id="s",
        style="cutout",
        duration=1.0,
        entities=[AssetRef(kind="character", id="c", store="characters", ref="c")],
        actions=[SetAction(target="c/head/mouth", property="viseme", value="a")],
    )
    with pytest.raises(CutoutCompileError, match="'A'"):
        compile_shot(shot, mall={"characters": {}})
    ok = shot.model_copy(
        update={"actions": [SetAction(target="c/head/mouth", property="viseme", value="A")]}
    )
    scene = compile_shot(ok, mall={"characters": {}})
    assert _channels(scene, "viseme")[0].keyframes[0].value == "A"


def test_the_procedural_mouth_declares_its_set_as_data():
    """The compiler no longer branches on the set NAME for the drawn mouth: the
    mouth's VisualJSON carries `asset_sets={viseme: {A: A, ..., X: X}}`, the
    mirror of the runtime's `_anDrawSets`."""
    shot = Shot(
        id="s",
        style="cutout",
        duration=1.0,
        entities=[AssetRef(kind="character", id="c", store="characters", ref="c")],
    )
    scene = compile_shot(shot, mall={"characters": {}})
    head = next(c for c in scene.scene.children[0].children if c.name == "head")
    mouth = next(c for c in head.children if c.name == "mouth")
    assert mouth.visual.kind == "mouth"
    assert mouth.visual.asset_sets["viseme"]["X"] == "X"
    assert set(mouth.visual.asset_sets["viseme"]) == set("ABCDEFGHX")


def test_the_rest_key_is_derived_from_the_default_attachment(gale_store, tmp_path):
    """A viseme vocabulary that is not Rhubarb's (MPEG-4 numbers, Azure names)
    must still close its mouth: the rest key is the key whose art is the
    slot's default attachment, not the literal 'X' (an#87 review)."""
    import json as _json

    from an.ir.schema import Dialogue, VisemeKeyframe, VisemeTrack

    desc_path = tmp_path / "gale" / "character.json"
    doc = _json.loads(desc_path.read_text(encoding="utf-8"))
    doc["asset_sets"]["viseme"] = {"SIL": "mouth_x", "AA": "mouth_a", "EH": "mouth_c"}
    desc_path.write_text(_json.dumps(doc), encoding="utf-8")
    shot = _shot()
    shot = shot.model_copy(
        update={
            "dialogue": [
                Dialogue(
                    speaker="gale",
                    text="hi",
                    start=0.0,
                    duration=1.0,
                    viseme_track=VisemeTrack(
                        convention="mpeg4",
                        keyframes=[
                            VisemeKeyframe(time=0.0, viseme="SIL"),
                            VisemeKeyframe(time=0.3, viseme="AA"),
                        ],
                    ),
                )
            ]
        }
    )
    scene = compile_shot(shot, mall={"characters": gale_store})
    (ch,) = _channels(scene, "viseme")
    assert [k.value for k in ch.keyframes] == ["SIL", "AA", "SIL"]


def test_a_set_named_like_a_transform_is_refused(gale_store, tmp_path):
    """The reservation the design promised and the first cut did not enforce:
    a set named `alpha` would be applied by the runtime's static switch, never
    as a swap (an#87 review)."""
    import json as _json

    from an.characters.validate import validate_character

    desc_path = tmp_path / "gale" / "character.json"
    doc = _json.loads(desc_path.read_text(encoding="utf-8"))
    doc["asset_sets"]["alpha"] = {"a": "fist"}
    desc_path.write_text(_json.dumps(doc), encoding="utf-8")
    with pytest.raises(CutoutCompileError, match="alpha"):
        compile_shot(_shot(), mall={"characters": gale_store})
    report = validate_character(tmp_path / "gale")
    assert any(
        f.severity == "error" and "alpha" in f.description for f in report.findings
    )


def test_a_baked_face_provenance_with_overlay_true_is_flagged(gale_store, tmp_path):
    """Nothing infers `face_overlay` from provenance any more (a declared fact
    is only worth having if nothing second-guesses it), so a current-schema
    descriptor written to the OLD convention gets a loud advisory instead of
    a silent double face (an#87 review)."""
    import json as _json

    from an.characters.validate import validate_character

    desc_path = tmp_path / "gale" / "character.json"
    doc = _json.loads(desc_path.read_text(encoding="utf-8"))
    doc["metadata"] = {"art_provenance": "dicebear"}
    desc_path.write_text(_json.dumps(doc), encoding="utf-8")
    report = validate_character(tmp_path / "gale")
    assert any("face_overlay" in f.ir_path for f in report.findings)


def test_a_swap_tween_with_the_default_easing_does_not_warn(gale_store):
    """The warning is for an easing the author WROTE; TweenAction's default
    is 'ease_in_out', which would otherwise be reported as 'asked for'."""
    import warnings as _warnings

    with _warnings.catch_warnings():
        _warnings.simplefilter("error", CutoutCompileWarning)
        scene = compile_shot(
            _shot(
                [
                    TweenAction(
                        target="gale/torso",
                        property="body_facing",
                        from_value="front",
                        to_value="left",
                        duration=1.0,
                    )
                ]
            ),
            mall={"characters": gale_store},
        )
    assert _channels(scene, "body_facing")[0].keyframes[0].easing == "step"


# ------------------------------------------- pins the mutation review found missing


def test_a_swap_on_an_unknown_node_is_refused_naming_the_known_paths(gale_store):
    with pytest.raises(CutoutCompileError, match="gale/nowhere"):
        compile_shot(
            _shot([SetAction(target="gale/nowhere", property="hands", value="fist")]),
            mall={"characters": gale_store},
        )


def _validate_gale(tmp_path, mutate):
    import json as _json

    from an.characters.validate import validate_character

    desc_path = tmp_path / "gale" / "character.json"
    doc = _json.loads(desc_path.read_text(encoding="utf-8"))
    mutate(doc, tmp_path / "gale")
    desc_path.write_text(_json.dumps(doc), encoding="utf-8")
    return validate_character(tmp_path / "gale")


def test_validate_blocks_a_key_whose_attachment_no_slot_carries(gale_store, tmp_path):
    def mutate(doc, _):
        doc["asset_sets"]["hands"]["wave"] = "hand_wave"

    report = _validate_gale(tmp_path, mutate)
    assert any(
        f.severity == "error" and "hand_wave" in f.description for f in report.findings
    )


def test_validate_advises_a_spare_key_whose_file_is_missing(gale_store, tmp_path):
    def mutate(doc, char_dir):
        (char_dir / "parts" / "hand_point.svg").unlink()

    report = _validate_gale(tmp_path, mutate)
    hits = [f for f in report.findings if "hand_point.svg" in f.ir_path]
    assert hits and all(f.severity == "warning" for f in hits), hits


def test_validate_blocks_a_missing_file_that_is_the_slot_s_default_art(
    gale_store, tmp_path
):
    """Research §8: blocking when the set's art is the slot's only/default
    drawing — the slot then draws nothing — advisory for a spare key."""

    def mutate(doc, char_dir):
        (char_dir / "parts" / "hand_fist.svg").unlink()  # the slot's default

    report = _validate_gale(tmp_path, mutate)
    hits = [f for f in report.findings if "hand_fist.svg" in f.ir_path]
    assert hits and any(f.severity == "error" for f in hits), hits


def test_validate_advises_when_a_set_s_attachments_differ_in_geometry(
    gale_store, tmp_path
):
    def mutate(doc, _):
        doc["skins"]["default"]["slots"]["left_hand"]["point"]["anchor"] = [0.0, 0.0]

    report = _validate_gale(tmp_path, mutate)
    assert any(
        f.severity == "warning" and "geometry" in f.description
        for f in report.findings
    )


def test_the_factory_declares_face_overlay_from_the_dicebear_path(tmp_path, monkeypatch):
    """`face_overlay` is DECLARED by the factory (an#87): False when the head
    is a DiceBear avatar with the face baked in, True for the offline
    geometric fallback. The DiceBear fetch is stubbed — no network."""
    import json as _json

    from an.characters import factory

    offline = factory.new_character(tmp_path / "a", name="off", use_dicebear=False)
    assert _json.loads(offline.read_text(encoding="utf-8"))["face_overlay"] is True

    monkeypatch.setattr(
        factory,
        "fetch_dicebear",
        lambda seed, style: (
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64" width="64" '
            'height="64"><circle cx="32" cy="32" r="30" fill="#ccc"/></svg>'
        ),
    )
    monkeypatch.setattr(factory, "_check_style_is_usable", lambda *a, **k: None)
    baked = factory.new_character(
        tmp_path / "b", name="dice", use_dicebear=True, acknowledge_attribution=True
    )
    doc = _json.loads(baked.read_text(encoding="utf-8"))
    assert doc["metadata"]["art_provenance"] == "dicebear"
    assert doc["face_overlay"] is False


def test_the_0_3_0_migration_repairs_stored_animation_tracks():
    """Pinned as a test, not only the doctest: the 0.2.0 migration renamed
    slots in `slots`/`skins` but never in `animations`, so every stored
    descriptor carried `slot:eye_l.attachment` with `eye_l_open` frames."""
    from an.ir.migrate import migrate

    out = migrate(
        {
            "kind": "CharacterDescriptor",
            "schema_version": "0.2.0",
            "name": "x",
            "animations": {
                "blink": {
                    "name": "blink",
                    "tracks": [
                        {
                            "target": "slot:eye_r.attachment",
                            "type": "step",
                            "frames": [[0.0, "eye_r_open"], [0.05, "eye_r_closed"]],
                        }
                    ],
                }
            },
        },
        kind="CharacterDescriptor",
    )
    (track,) = out["animations"]["blink"]["tracks"]
    assert track["target"] == "slot:right_eye.attachment"
    assert [v for _, v in track["frames"]] == ["open", "closed"]


def test_an_active_tween_governs_over_a_hold_at_the_shared_instant(gale_store):
    """Two precedence facts the skeptic pass measured (an#87):

    - at the exact handoff instant (an end-inclusive hold meeting a tween's
      start — the common frame-aligned case) the TWEEN's first frame shows;
    - a set authored inside a running tween's window takes effect when the
      window ends, not mid-tween.

    Both follow from hold clips being placed before per-action clips in the
    track, which later-wins evaluation turns into "an active tween governs".
    """
    from an.ir.compose import delay, sequence

    scene = compile_shot(
        _shot(
            [
                SetAction(target="gale/torso", property="x", value=50.0, at=0.0),
                sequence(
                    delay(1.0),
                    TweenAction(
                        target="gale/torso", property="x", from_value=0.0,
                        to_value=100.0, duration=1.0, easing="linear",
                    ),
                ),
                SetAction(target="gale/torso", property="x", value=-7.0, at=1.5),
            ],
            duration=3.0,
        ),
        mall={"characters": gale_store},
    )
    tl = _python_timeline(scene)
    key = ("gale/torso", "x")
    assert _evaluate(tl, 0.9)[key] == 50.0
    assert _evaluate(tl, 1.0)[key] == 0.0  # the tween's first frame, not 50
    assert _evaluate(tl, 1.75)[key] == pytest.approx(75.0)  # tween governs
    assert _evaluate(tl, 2.0)[key] == pytest.approx(100.0)
    assert _evaluate(tl, 2.5)[key] == -7.0  # the in-window set resumes after


def test_a_set_past_the_shot_end_warns(gale_store):
    with pytest.warns(CutoutCompileWarning, match="past the shot's end"):
        compile_shot(
            _shot([SetAction(target="gale/torso", property="x", value=1.0, at=9.0)]),
            mall={"characters": gale_store},
        )
