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

import json
import re
from pathlib import Path

import pytest

from an.adapters.cutout.compile import (
    _CAMERA_MOVES,
    _build_scene_root,
    _runtime_node_paths,
    CutoutCompileError,
    CutoutCompileWarning,
    compile_shot,
)
from an.adapters.cutout.pose import _ALLOWED_NODE_PROPS, UNRENDERED_PROPS
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
    VisemeKeyframe,
    VisemeTrack,
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

    # Every quoted move-ish name in the Camera block must either be implemented
    # or appear in a sentence that says it is NOT. An earlier version keyed on
    # the literal "e.g.", which the rewritten comment no longer contains — so
    # the match set was always empty and the test passed on any regression.
    # Fixing the thing a test guards must not be able to disarm the test.
    block = camera.group(0)
    quoted = set(re.findall(r'"(\w+)"', block))
    # A name is exonerated only if the SAME line disclaims it.
    disclaimed = set()
    for line in block.splitlines():
        if re.search(r"not implement|does not|RAISES|raises|NOT\b", line):
            disclaimed |= set(re.findall(r'"(\w+)"', line))
    advertised = quoted - disclaimed
    unimplemented = sorted(advertised - set(_CAMERA_MOVES) - {"hold"})
    assert not unimplemented, (
        f"Camera's docs present {unimplemented} as usable move names, but the "
        "cutout compiler does not implement them and now raises"
    )
    assert quoted, "the regex matched nothing at all — it has drifted"


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


def _spoken_line(speaker: str) -> Dialogue:
    """A dialogue line that actually reaches the viseme branch.

    Load-bearing: `_add_viseme_clips` `continue`s on a missing `viseme_track`
    and on missing timing, two guards BEFORE the mouth-node check. An earlier
    version of the test below passed a bare `Dialogue(speaker=..., text=...)`
    and so never reached the branch it claimed to cover — it was vacuous, and it
    was the test defending this change's headline claim.
    """
    return Dialogue(
        speaker=speaker,
        text="hi",
        start=0.0,
        duration=1.0,
        viseme_track=VisemeTrack(
            convention="rhubarb",
            keyframes=[VisemeKeyframe(time=0.0, viseme="A")],
        ),
    )


def test_the_helper_actually_reaches_the_viseme_branch():
    """Guards the guard: prove `_spoken_line` gets past the earlier `continue`s.

    Without this, a future change to `Dialogue`'s defaults could silently make
    every test below vacuous again, in exactly the way the first version was.
    """
    shot = Shot(
        id="s1",
        style="cutout",
        duration=1.0,
        entities=[_character()],
        dialogue=[_spoken_line("charlie")],
    )
    scene = compile_shot(shot)
    targets = {ch.target for a in scene.animations.values() for ch in a.channels}
    assert "charlie/head/mouth" in targets, (
        "an on-screen speaker produced no viseme channel, so every off-screen "
        "test below is testing nothing"
    )


def test_an_off_screen_speaker_warns_and_emits_no_channel():
    """The narration error recommends this idiom, so it must keep working.

    It must ALSO be audible: an off-screen narrator and a typo are
    indistinguishable here, and the first fix for this simply `continue`d —
    trading one silent discard for another.
    """
    shot = Shot(
        id="s1",
        style="cutout",
        duration=1.0,
        dialogue=[_spoken_line("narrator")],
    )
    with pytest.warns(CutoutCompileWarning, match="no mouth node"):
        scene = compile_shot(shot)
    targets = {ch.target for a in scene.animations.values() for ch in a.channels}
    assert not any(t.startswith("narrator") for t in targets), (
        "a viseme channel was emitted for a speaker with no mouth; the runtime "
        "would raise on the missing node"
    )


def test_a_typo_speaker_names_the_scenes_actual_mouths():
    """The whole reason the skip warns instead of passing silently."""
    shot = Shot(
        id="s1",
        style="cutout",
        duration=1.0,
        entities=[_character("charlie")],
        dialogue=[_spoken_line("charlei")],
    )
    with pytest.warns(CutoutCompileWarning, match="charlie/head/mouth"):
        compile_shot(shot)


# -------------------------------------------------------------------- 4. props


def test_a_prop_entity_raises_naming_the_shot_and_a_reachable_issue():
    """`an` is on PyPI, so an internal wave number means nothing to a user.

    Every error a pip-install user can hit must name WHERE (the shot) and point
    somewhere they can actually read.
    """
    shot = Shot(
        id="s1",
        style="cutout",
        duration=1.0,
        entities=[AssetRef(kind="prop", id="banner", store="characters", ref="b-v1")],
    )
    with pytest.raises(CutoutCompileError) as e:
        compile_shot(shot)
    msg = str(e.value)
    assert "'s1'" in msg, "the error must name the shot"
    assert "github.com/thorwhalen/an/issues" in msg, "and point at something readable"


def test_no_user_facing_error_cites_an_internal_wave_number():
    """Wave numbering is roadmap vocabulary; it is not in the package."""
    import re as _re

    for mod in ("an/adapters/cutout/compile.py", "an/audio/pipeline.py"):
        src = Path(__file__).resolve().parents[1].joinpath(mod).read_text()
        for m in _re.finditer(r'"[^"]*Wave \d[^"]*"', src):
            raise AssertionError(
                f"{mod} puts an internal wave reference in a user-facing string: "
                f"{m.group(0)}"
            )


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


def test_an_unread_environment_key_warns_rather_than_raising():
    """Warn, not raise — the perimeter was drawn in the wrong place at first.

    `EnvironmentsStore` is a `JsonSidecarStore` over a free-form meta.json, so
    `name` / `description` / `tags` are its natural shape. Raising on any
    non-preset key hard-fails ordinary data. The keys still do nothing, which is
    the part worth saying out loud.
    """
    shot = Shot(
        id="s1",
        style="cutout",
        duration=1.0,
        entities=[AssetRef(kind="environment", id="env", store="environments", ref="park")],
    )
    mall = {"environments": {"park": {"sky_color": "#001122", "parallax_layers": 3}}}
    with pytest.warns(CutoutCompileWarning, match="parallax_layers"):
        compile_shot(shot, mall=mall)


def test_ordinary_store_metadata_does_not_break_a_render():
    """The regression the raise-version would have shipped."""
    shot = Shot(
        id="s1",
        style="cutout",
        duration=1.0,
        entities=[AssetRef(kind="environment", id="env", store="environments", ref="park")],
    )
    mall = {"environments": {"park": {"name": "Park at dusk", "sky_color": "#001122"}}}
    with pytest.warns(CutoutCompileWarning):
        scene = compile_shot(shot, mall=mall)
    assert "#001122" in _visual_colors(scene.scene), (
        "the render-relevant key must still be applied while the metadata is ignored"
    )


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
    """The other half: the loud default must not swallow a legal property.

    Asserts WHERE each value lands, not merely that nothing threw. The first
    version only checked for the absence of an exception, so a mutant writing
    `case 'x': node.y = value` passed it unnoticed.
    """
    fn = _extract("applyProperty", r"function applyProperty\([^)]*\)\s*\{.*?\n    \}")
    props = sorted(_runtime_switch_cases() - {"viseme"})
    script = "\n".join(
        [
            "function setVisemeOnMouth() {}",
            fn,
            f"const props = {props!r};".replace("'", '"'),
            "const out = {};",
            # A fresh node per property, so a value landing on the wrong field
            # cannot be masked by another property having written there.
            "for (const p of props) {",
            "  const node = {scale: {}, skew: {}, pivot: {}};",
            "  applyProperty(node, p, 7);",
            "  out[p] = node;",
            "}",
            "console.log(JSON.stringify(out));",
        ]
    )
    landed = json.loads(_run_node(script))
    where = {
        "x": ("x", None),
        "y": ("y", None),
        "rotation": ("rotation", None),
        "rotation_rad": ("rotation", None),
        "alpha": ("alpha", None),
        "scale_x": ("scale", "x"),
        "scale_y": ("scale", "y"),
        "skew_x": ("skew", "x"),
        "skew_y": ("skew", "y"),
        "pivot_x": ("pivot", "x"),
        "pivot_y": ("pivot", "y"),
    }
    unmapped = sorted(set(props) - set(where))
    assert not unmapped, f"runtime.js gained properties this test does not check: {unmapped}"
    for prop, node in landed.items():
        outer, inner = where[prop]
        got = node.get(outer) if inner is None else (node.get(outer) or {}).get(inner)
        assert got == 7, (
            f"{prop!r} did not land on {outer}{'.' + inner if inner else ''}: {node}"
        )


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


def test_the_python_allow_list_is_a_subset_with_a_declared_gap():
    """SUBSET, not equality — and the gap must be exactly what is declared.

    An earlier version asserted equality, and "fixing" the failure by widening
    `_ALLOWED_NODE_PROPS` made things worse: `apply_pose` routes every allowed
    property into `TransformParams`, which has no `alpha` and no `viseme` field,
    so it accepted them and then died with a raw dataclass `TypeError` instead
    of its own informative `KeyError`. Advertising a capability you do not have
    is the same defect class as discarding one you do.
    """
    allowed = set(_ALLOWED_NODE_PROPS)
    runtime = _runtime_switch_cases()
    assert allowed - {"rotation_rad", "rotation"} <= runtime, (
        f"pose.py claims properties the runtime does not apply: "
        f"{sorted(allowed - {'rotation_rad', 'rotation'} - runtime)}"
    )
    assert runtime - allowed == set(UNRENDERED_PROPS), (
        "the gap between the two evaluators changed and is no longer what "
        f"UNRENDERED_PROPS declares: {sorted(runtime - allowed)}"
    )


def test_apply_pose_cannot_be_asked_for_a_property_it_would_crash_on():
    """The concrete failure the subset relationship prevents."""
    from an.adapters.cutout.pose import apply_pose
    from an.adapters.cutout.scene import Node, SceneGraph

    g = SceneGraph(Node("r"))
    for prop in sorted(UNRENDERED_PROPS):
        with pytest.raises(KeyError, match="unknown pose property"):
            apply_pose(g, {("r", prop): 0.5})


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


# ------------------------------------------- 8. the docs must not advertise them

def test_no_skill_advertises_a_capability_that_now_raises():
    """An error that contradicts the docs is worse than no error.

    `pan_left` was the original instance: named in the IR's own comment and dead
    in the compiler. The same trap applies to every doc that lists what a scene
    may contain — the `an` skill told an agent that `kind` may be `prop` and that
    `play` is a composition primitive, both of which now hard-fail.
    """
    skills = Path(__file__).resolve().parents[1] / ".claude/skills"
    offenders = []
    for skill in sorted(skills.glob("*/SKILL.md")):
        text = skill.read_text()
        for line in text.splitlines():
            # An enumeration of legal `kind` values must not offer `prop`.
            if "`kind`" in line and "∈" in line and "prop" in line.split("∈")[1]:
                offenders.append(f"{skill.parent.name}: {line.strip()[:90]}")
    assert not offenders, (
        "a skill advertises `prop` as a usable entity kind, but the compiler "
        f"raises on it:\n" + "\n".join(offenders)
    )


# ------------------------------------- 9. the wrapper that had no test at all

def test_a_js_runtime_throw_arrives_as_a_typed_error_naming_the_frame():
    """The ninth mutation: this wrapper had ZERO coverage.

    Deleting it left all 408 tests green, even though the PR body called it one
    of four supporting changes "without which this would trade one defect for
    another" — trading a silent discard for a raw
    `playwright._impl._errors.Error` with no shot, no frame and no time in it.

    Driven through a fake page rather than a browser, so it runs everywhere
    including CI, where no browser test runs at all (#22).
    """
    from an.adapters.cutout.render import CutoutRenderError, _capture_frames

    class _ThrowingPage:
        """Stands in for Playwright's Page; throws the way the runtime does."""

        def evaluate(self, *_a, **_kw):
            raise RuntimeError(
                'Error: unknown animated property "opacity" on "charlie"'
            )

    with pytest.raises(CutoutRenderError) as e:
        _capture_frames(_ThrowingPage(), total_frames=3, fps=12, frames_dir=Path("/tmp"))

    msg = str(e.value)
    assert "frame 0" in msg, "the error must name which frame failed"
    assert "RuntimeError" in msg, "and must name what actually failed, not assert a cause"
    assert "t=0.0000s" in msg, "the error must name the time, for a long shot"
    assert "opacity" in msg, "the runtime's own message is the informative part"


# ------------------ 10. validate must agree with the pipeline it predicts

_UNRENDERABLE_SHOTS = {
    "camera": lambda: Shot(id="s1", style="cutout", duration=1.0,
                           entities=[_character()], camera=Camera(move="pan_left")),
    "prop": lambda: Shot(id="s1", style="cutout", duration=1.0,
                         entities=[AssetRef(kind="prop", id="b", store="characters", ref="b-v1")]),
    "narration": lambda: Shot(id="s1", style="cutout", duration=1.0,
                              narration=[Narration(text="once")]),
    "play": lambda: Shot(id="s1", style="cutout", duration=1.0, entities=[_character()],
                         actions=[PlayAction(target="charlie", animation="walk", duration=1.0)]),
}


@pytest.mark.parametrize("name", sorted(_UNRENDERABLE_SHOTS))
def test_validate_reports_every_scene_the_pipeline_refuses(name):
    """The structural fix, and the point of this whole change.

    Before, each guard sat where the SYMPTOM was — in the compiler, in the audio
    pipeline, in the JS runtime — so `an validate` said "passed" about a scene
    that could not render, and the author only found out after paying for TTS
    synthesis or a Chromium launch. `iterate()` runs validate after applying a
    model's patches, so it was equally blind.

    Severity must be `error`, not `warning`: validate's verdict has to match the
    pipeline's, or it is confidently wrong rather than merely quiet.
    """
    from an.ir.schema import Meta, Resolution
    from an.ir.validate import validate_semantic

    scene = SceneIR(
        meta=Meta(title="t", duration=1.0, fps=12,
                  resolution=Resolution(width=64, height=48)),
        timeline=[_UNRENDERABLE_SHOTS[name]()],
    )
    report = validate_semantic(scene)
    assert not report.passed, f"validate says a {name} scene is fine; it cannot render"
    assert any(f.severity == "error" for f in report.findings), (
        "must be an error — the pipeline raises, so a warning understates it"
    )


def test_validate_still_passes_a_scene_that_renders():
    """The other half: the pre-flight must not reject working scenes.

    `voice` and `style` entities configure the render rather than appearing in
    it, and `hold` is a real no-op — all three would be easy to sweep up here.
    """
    from an.ir.schema import Meta, Resolution
    from an.ir.validate import validate_semantic

    scene = SceneIR(
        meta=Meta(title="t", duration=1.0, fps=12,
                  resolution=Resolution(width=64, height=48)),
        timeline=[
            Shot(
                id="s1", style="cutout", duration=1.0,
                camera=Camera(move="hold"),
                entities=[
                    _character(),
                    AssetRef(kind="voice", id="v", store="voices", ref="v-v1"),
                    AssetRef(kind="style", id="s", store="styles", ref="s-v1"),
                ],
                dialogue=[Dialogue(speaker="charlie", text="hi")],
            )
        ],
    )
    report = validate_semantic(scene)
    errors = [f for f in report.findings if f.severity == "error"]
    assert not errors, f"the pre-flight rejects a renderable scene: {errors}"


def test_the_validators_camera_list_matches_the_compilers():
    """Two copies, deliberately — the IR must not import an adapter.

    So they are pinned together here instead, the same way `artful` pins shared
    vocabulary across packages that must not depend on each other.
    """
    from an.ir.validate import _RENDERABLE_CAMERA_MOVES

    assert set(_RENDERABLE_CAMERA_MOVES) == set(_CAMERA_MOVES), (
        "the validator and the compiler disagree about which camera moves exist"
    )


def test_an_empty_camera_move_is_treated_like_any_other_unusable_value():
    """`move=""` used to be ignored while `move="  "` raised — same input, two
    behaviours, purely because falsiness was tested before normalisation."""
    for blank in ("", "   ", "\t"):
        shot = Shot(id="s1", style="cutout", duration=1.0,
                    entities=[_character()], camera=Camera(move=blank))
        compile_shot(shot)  # a blank move is "no move", consistently


# ------------------------- 11. the authoring surface refuses it first

def test_scene_md_refuses_play_at_the_authoring_surface():
    """The layer principle, applied to the surface an author actually edits.

    `scene.md` is the SSOT a human writes. A `play` accepted here round-trips
    through the IR, survives `an validate`, and dies at compile — having looked
    valid the whole way. This was the one finding no reviewer lens caught.
    """
    from an.ir.sync import markdown_to_ir

    md = (
        "# Test\n\n"
        "```yaml meta\ntitle: t\nduration: 1.0\nfps: 12\n```\n\n"
        "## Shot s1 (cutout)\n\n"
        "```yaml shot\nduration: 1.0\n```\n\n"
        "```yaml actions\n"
        "- {kind: play, target: charlie, animation: walk, duration: 1.0}\n"
        "```\n"
    )
    with pytest.raises(ValueError, match="play"):
        markdown_to_ir(md)


def test_scene_md_still_accepts_the_actions_that_work():
    from an.ir.sync import markdown_to_ir

    md = (
        "# Test\n\n"
        "```yaml meta\ntitle: t\nduration: 1.0\nfps: 12\n```\n\n"
        "## Shot s1 (cutout)\n\n"
        "```yaml shot\nduration: 1.0\n```\n\n"
        "```yaml actions\n"
        "- {kind: tween, target: charlie, property: alpha, to: 0.0, duration: 1.0}\n"
        "- {kind: set, target: charlie, property: x, value: 10.0}\n"
        "```\n"
    )
    scene = markdown_to_ir(md)
    assert len(scene.timeline[0].actions) == 2


def test_no_doc_offers_a_targeting_example_that_no_rig_builds():
    """`charlie/torso/left_arm:rotation` named a node nothing creates.

    Harmless while an unknown target was skipped; a trap now that it raises.
    The rigs are FLAT — arms are siblings of the torso.

    Checked against the paths a real compile actually produces, not against a
    word list. Two heuristics were tried first and both were wrong: a
    single-line negation check flagged the sentence *explaining* the
    deprecation, and a ±2-line window silently exonerated everything by picking
    up prose from neighbouring bullets. A guard whose exoneration rule is fuzzy
    is a guard that passes.

    Only TARGETING examples are checked — `path:property`. Bare prose mentions
    are how you write about the old example at all.
    """
    real = _runtime_node_paths(
        _build_scene_root(
            Shot(id="s1", style="cutout", duration=1.0, entities=[_character()]),
            {},
            textures={},
        )
    )
    root = Path(__file__).resolve().parents[1]
    offenders = []
    for rel in ("an/base.py", "CLAUDE.md", ".claude/skills/an-dev/SKILL.md",
                ".claude/skills/an/SKILL.md"):
        for path, prop in re.findall(r"([a-z_]+(?:/[a-z_]+)+):([a-z_]+)",
                                     (root / rel).read_text()):
            if path not in real:
                offenders.append(f"{rel}: {path}:{prop} — no rig builds {path!r}")
    assert not offenders, (
        "a doc offers a targeting example whose node does not exist, which now "
        "raises at render:\n" + "\n".join(offenders)
        + f"\n\nreal paths for a placeholder character: {sorted(real)}"
    )
