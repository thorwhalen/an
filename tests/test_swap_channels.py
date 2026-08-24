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
    runtime = RUNTIME_JS.read_text(encoding="utf-8")
    compiler = (
        Path(__file__).resolve().parents[1]
        / "an"
        / "adapters"
        / "cutout"
        / "compile.py"
    ).read_text(encoding="utf-8")
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
    placed = scene.timeline.tracks[0].clips[0]
    assert placed.start_time == pytest.approx(1.02)
    assert placed.duration == pytest.approx(2.0 - 1.02)
    assert ch.keyframes[0].value == 2.0


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
    with pytest.raises(CutoutCompileError, match=r"fist.*palm.*point|declared key"):
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
            _extract("applySwap"),
            _extract("applyProperty"),
            "const g = { _anDrawSets: { viseme: drawMouthShape } };",
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
