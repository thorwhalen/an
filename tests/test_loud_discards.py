"""Seven places that accepted something and produced nothing, now audible (#15).

Each of these had the same shape: the IR declares a capability, the compiler or
the runtime quietly declines it, and the author gets a render that is missing
something with no diagnostic anywhere. That is the worst failure mode this
package has, because it surfaces as "the animation looks wrong" days later and
gets attributed to whatever else shipped that day.

Making them raise was verified safe *before* it was done — every site was patched
independently and the whole suite stayed green, because no test exercised any of
them. See `misc/docs/wave1_verification.md` §4.

Where the feature is genuinely unbuilt, the error names the wave that implements
it. "Not supported" is a dead end; "not supported — props land in Wave 7" is a
roadmap.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from an.adapters.cutout.compile import _CAMERA_MOVES, CutoutCompileError, compile_shot
from an.adapters.cutout.pose import _ALLOWED_NODE_PROPS
from an.audio.pipeline import AudioPipelineError, produce_audio_for_scene
from an.ir.schema import (
    AssetRef,
    Camera,
    Dialogue,
    Meta,
    Narration,
    PlayAction,
    Resolution,
    SceneIR,
    Shot,
)

RUNTIME_JS = Path(__file__).resolve().parents[1] / "an/data/cutout_runtime/runtime.js"


def _character(entity_id: str = "charlie") -> AssetRef:
    return AssetRef(kind="character", id=entity_id, store="characters", ref="c-v1")


# ------------------------------------------------------------------- 1. camera


def test_an_unknown_camera_move_raises_and_names_the_wave():
    shot = Shot(
        id="s1",
        style="cutout",
        duration=1.0,
        entities=[_character()],
        camera=Camera(move="pan_left"),
    )
    with pytest.raises(CutoutCompileError, match="pan_left"):
        compile_shot(shot)


def test_hold_is_a_real_no_op_and_must_not_raise():
    """`hold` early-returned through the same branch as an unknown move.

    It is a correct no-op, so only genuinely unknown names may raise — otherwise
    the change breaks the one camera value that always worked.
    """
    shot = Shot(
        id="s1",
        style="cutout",
        duration=1.0,
        entities=[_character()],
        camera=Camera(move="hold"),
    )
    compile_shot(shot)  # must not raise


@pytest.mark.parametrize("move", ["push_in", "pull_out", "zoom_in", "zoom_out"])
def test_the_implemented_moves_still_compile(move):
    shot = Shot(
        id="s1",
        style="cutout",
        duration=1.0,
        entities=[_character()],
        camera=Camera(move=move),
    )
    compile_shot(shot)


def test_the_schema_no_longer_advertises_a_move_the_compiler_lacks():
    """`pan_left` was named in the IR's own comment and dead in the compiler.

    An error that contradicts the schema is worse than no error, so the two are
    pinned together here.
    """
    from an.ir import schema

    src = Path(schema.__file__).read_text()
    camera = re.search(r"class Camera\(.*?\n\n\nclass ", src, re.S)
    assert camera, "Camera model not found"

    # The failure mode was an `e.g.`-style example list presenting move names as
    # usable. Checking for the whole word anywhere is too broad — the comment now
    # names `pan_left` precisely in order to say it is NOT implemented.
    examples = re.findall(r"e\.g\.\s*((?:\"\w+\"[,\s]*)+)", camera.group(0))
    advertised = {m for group in examples for m in re.findall(r'"(\w+)"', group)}
    unimplemented = sorted(advertised - set(_CAMERA_MOVES) - {"hold"})
    assert not unimplemented, (
        f"Camera's docs offer {unimplemented} as example move names, but the "
        "cutout compiler does not implement them and now raises"
    )


# --------------------------------------------------------------- 2. PlayAction


def test_play_raises_instead_of_compiling_to_a_clip_that_animates_nothing():
    """The defensive re-pass used to fabricate an empty, channel-less clip.

    That is how `play` came to look wired up while animating nothing: the clip
    was present, carried the right duration, and moved not one property (#7).
    """
    shot = Shot(
        id="s1",
        style="cutout",
        duration=4.0,
        entities=[_character()],
        actions=[PlayAction(target="charlie", animation="walk", duration=4.0)],
    )
    with pytest.raises(CutoutCompileError, match="walk"):
        compile_shot(shot)


# ---------------------------------------------------------------- 3. narration


def test_narration_raises_rather_than_producing_neither_audio_nor_video():
    scene = SceneIR(
        meta=Meta(title="n", duration=1.0, fps=12, resolution=Resolution(width=64, height=48)),
        timeline=[
            Shot(
                id="s1",
                style="cutout",
                duration=1.0,
                narration=[Narration(text="once upon a time")],
            )
        ],
    )
    with pytest.raises(AudioPipelineError, match="narration"):
        produce_audio_for_scene(scene)


def test_an_off_screen_speaker_is_still_the_supported_workaround():
    """The narration error recommends it, so it must keep working.

    A dialogue line whose speaker is not an entity gets audio and no mouth —
    which is exactly what an off-screen narrator is. Compiling it must not raise,
    and must not emit a viseme channel aimed at a node that was never built.
    """
    shot = Shot(
        id="s1",
        style="cutout",
        duration=1.0,
        dialogue=[Dialogue(speaker="narrator", text="hi")],
    )
    scene = compile_shot(shot)
    targets = {
        ch.target for anim in scene.animations.values() for ch in anim.channels
    }
    assert not any(t.startswith("narrator") for t in targets), (
        "a viseme channel was emitted for an off-screen speaker; the runtime "
        "would raise on the missing node"
    )


# -------------------------------------------------------------------- 4. props


def test_a_prop_entity_raises_and_names_the_wave():
    shot = Shot(
        id="s1",
        style="cutout",
        duration=1.0,
        entities=[AssetRef(kind="prop", id="banner", store="characters", ref="b-v1")],
    )
    with pytest.raises(CutoutCompileError, match="Wave 7"):
        compile_shot(shot)


@pytest.mark.parametrize("kind", ["voice", "style"])
def test_non_drawable_entity_kinds_are_legitimately_ignored(kind):
    """`voice` and `style` configure the render rather than appearing in it.

    They must not be swept into the prop error — that would make the guard fire
    on correct scenes.
    """
    shot = Shot(
        id="s1",
        style="cutout",
        duration=1.0,
        entities=[AssetRef(kind=kind, id="v", store="voices", ref="v-v1")],
    )
    compile_shot(shot)  # must not raise


# ---------------------------------------------------- 5. environment overrides


def test_an_unread_environment_key_raises():
    shot = Shot(
        id="s1",
        style="cutout",
        duration=1.0,
        entities=[AssetRef(kind="environment", id="env", store="environments", ref="park")],
    )
    mall = {"environments": {"park": {"sky_color": "#001122", "parallax_layers": 3}}}
    with pytest.raises(CutoutCompileError, match="parallax_layers"):
        compile_shot(shot, mall=mall)


def test_a_known_environment_key_still_overrides():
    shot = Shot(
        id="s1",
        style="cutout",
        duration=1.0,
        entities=[AssetRef(kind="environment", id="env", store="environments", ref="park")],
    )
    mall = {"environments": {"park": {"sky_color": "#001122"}}}
    scene = compile_shot(shot, mall=mall)
    colors = _visual_colors(scene.scene)
    assert "#001122" in colors, f"the override did not reach the render: {colors}"


def _visual_colors(node) -> set[str]:
    out = set()
    if node.visual is not None:
        out.add(node.visual.color)
    for child in node.children:
        out |= _visual_colors(child)
    return out


# ------------------------------------------------- 6 & 7. the runtime's silences


def _runtime_switch_cases() -> set[str]:
    src = RUNTIME_JS.read_text()
    body = re.search(r"function applyProperty\([^)]*\)\s*\{(.*?)\n    \}", src, re.S)
    assert body
    return set(re.findall(r"case\s+'([a-z_]+)'\s*:", re.sub(r"//[^\n]*", "", body.group(1))))


def _run_node(script: str) -> str:
    """Run a snippet under node and return its stdout.

    Behavioural, not textual. A grep for `throw new Error` proves the statement
    is *present*, not that it is *reachable* — verified by mutation: inserting a
    `break;` above the throw left a text-based version of these tests green while
    the property was silently ignored again.
    """
    import shutil
    import subprocess

    if shutil.which("node") is None:
        pytest.skip("node not installed")
    proc = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, timeout=30
    )
    assert proc.returncode == 0, f"node failed: {proc.stderr}"
    return proc.stdout.strip()


def _extract(name: str, pattern: str) -> str:
    src = RUNTIME_JS.read_text()
    m = re.search(pattern, src, re.S)
    assert m, f"{name} not found in runtime.js"
    return m.group(0)


def test_the_runtime_raises_on_an_unknown_property():
    """Executed, not grepped."""
    fn = _extract("applyProperty", r"function applyProperty\([^)]*\)\s*\{.*?\n    \}")
    script = "\n".join(
        [
            "function setVisemeOnMouth() {}",
            fn,
            "const node = {name: 'charlie', scale: {}, skew: {}, pivot: {}};",
            "try { applyProperty(node, 'opacity', 0.5); console.log('SILENT'); }",
            "catch (e) { console.log('RAISED: ' + e.message); }",
        ]
    )
    out = _run_node(script)
    assert out.startswith("RAISED"), f"unknown property was not refused: {out}"
    assert "opacity" in out and "alpha" in out, (
        "the error must name the offending property and list the legal ones"
    )


def test_the_runtime_still_applies_every_known_property():
    """The other half: the loud default must not swallow a legal property."""
    fn = _extract("applyProperty", r"function applyProperty\([^)]*\)\s*\{.*?\n    \}")
    props = sorted(_runtime_switch_cases() - {"viseme"})
    script = "\n".join(
        [
            "function setVisemeOnMouth() {}",
            fn,
            "const node = {name: 'c', scale: {}, skew: {}, pivot: {}};",
            f"const props = {props!r};".replace("'", '"'),
            "for (const p of props) { applyProperty(node, p, 1); }",
            "console.log('OK');",
        ]
    )
    assert _run_node(script) == "OK"


def test_the_runtime_raises_on_an_unknown_target():
    """Executed, not grepped."""
    order = _extract(
        "poseKeysInApplicationOrder",
        r"function poseKeysInApplicationOrder\(pose\) \{.*?\n    \}",
    )
    fn = _extract("applyPose", r"function applyPose\(pose\) \{.*?\n    \}")
    script = "\n".join(
        [
            order,
            fn,
            "const nodeIndex = {'charlie': {}};",
            "function applyProperty() {}",
            "try { applyPose({'chalrie::x': 1}); console.log('SILENT'); }",
            "catch (e) { console.log('RAISED: ' + e.message); }",
        ]
    )
    out = _run_node(script)
    assert out.startswith("RAISED"), f"unknown target was not refused: {out}"
    assert "chalrie" in out and "charlie" in out, (
        "the error must name the typo and the known paths — they are usually one "
        "character apart, which is the whole value of listing them"
    )


def test_the_python_allow_list_agrees_with_the_runtime():
    """`pose.py`'s allow-list had drifted — it lacked `viseme` and `alpha`.

    Nothing on the render path calls `apply_pose`, so no failure could surface
    the gap. Pinning them together is what keeps a second evaluator honest.
    """
    assert set(_ALLOWED_NODE_PROPS) == _runtime_switch_cases(), (
        'pose.py and runtime.js disagree about which properties are animatable'
    )


def test_the_iterate_prompt_enumerates_the_legal_properties():
    """The one real risk of making this loud.

    `an iterate` lets a model patch `actions` with arbitrary property names. A
    hallucinated `opacity` tween used to be silently inert; it is now a hard
    render failure, so the prompt has to say what is legal.
    """
    from an import iterate

    src = Path(iterate.__file__).read_text()
    prompt = src[src.index("actions: list of action dicts") :][:1200]
    for prop in ("scale_x", "alpha", "pivot_y"):
        assert prop in prompt, f"the prompt does not name {prop!r} as legal"
    assert "FAILS THE RENDER" in prompt or "fails the render" in prompt
