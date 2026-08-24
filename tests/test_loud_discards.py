"""Eight places that accepted something and produced nothing, now audible (#15).

Seven of them had the same shape: the IR declares a capability, the compiler or
the runtime quietly declines it, and the author gets a render that is missing
something with no diagnostic anywhere. That is the worst failure mode this
package has, because it surfaces as "the animation looks wrong" days later and
gets attributed to whatever else shipped that day.

The eighth (§8, an#33) is worse, and is why the file grew: nothing was missing.
A declared asset the stores could not supply was **substituted** with a stand-in
that renders happily, so the output is not incomplete — it is a different,
plausible picture. Detecting it needs a record of what the compiler did, because
the two renders are identical in every observable the pixels carry.

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
    RUNTIME_APPLIED_PROPERTIES,
    compile_shot,
)
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

    src = Path(schema.__file__).read_text(encoding="utf-8")
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
        src = Path(__file__).resolve().parents[1].joinpath(mod).read_text(encoding="utf-8")
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
    src = RUNTIME_JS.read_text(encoding="utf-8")
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
    src = RUNTIME_JS.read_text(encoding="utf-8")
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


def test_the_runtime_switch_matches_what_the_compiler_can_emit():
    """EQUALITY against the compile-side SSOT — the drift gate, rehomed.

    This assertion used to be a subset-with-declared-gap against the Python
    applier's allow-list (`pose._ALLOWED_NODE_PROPS` / `UNRENDERED_PROPS`).
    That applier is gone (an#86): application is single-model in `runtime.js`,
    and the honest Python-side vocabulary is *what the compiler can emit* —
    `RUNTIME_APPLIED_PROPERTIES`, derived from the rest-value SSOT plus the
    discrete channel names. A property the runtime applies that the compiler
    cannot emit is dead runtime code; a property the compiler emits that the
    runtime does not apply is a hard render failure. Both directions fail here.
    """
    runtime = _runtime_switch_cases()
    assert runtime == set(RUNTIME_APPLIED_PROPERTIES), (
        "the runtime's applyProperty switch and the compiler's emittable "
        "vocabulary drifted apart. Runtime-only: "
        f"{sorted(runtime - set(RUNTIME_APPLIED_PROPERTIES))}; compiler-only: "
        f"{sorted(set(RUNTIME_APPLIED_PROPERTIES) - runtime)}"
    )


def test_the_iterate_prompt_enumerates_the_legal_properties():
    """The one real risk of making this loud.

    `an iterate` lets a model patch `actions` with arbitrary property names. A
    hallucinated `opacity` tween used to be silently inert; it is now a hard
    render failure, so the prompt has to say what is legal.
    """
    from an import iterate

    src = Path(iterate.__file__).read_text(encoding="utf-8")
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
        text = skill.read_text(encoding="utf-8")
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
    for rel in ("an/base.py", "CLAUDE.md", "README.md",
                ".claude/skills/an-dev/SKILL.md", ".claude/skills/an/SKILL.md"):
        for path, prop in re.findall(r"([a-z_]+(?:/[a-z_]+)+):([a-z_]+)",
                                     (root / rel).read_text(encoding="utf-8")):
            if path not in real:
                offenders.append(f"{rel}: {path}:{prop} — no rig builds {path!r}")
    assert not offenders, (
        "a doc offers a targeting example whose node does not exist, which now "
        "raises at render:\n" + "\n".join(offenders)
        + f"\n\nreal paths for a placeholder character: {sorted(real)}"
    )


# ------------------------------------- 8. a stand-in asset, drawn without a word


def _placeholder_rig_store(ref: str = "c-v1") -> dict:
    """A store entry that asks for EXACTLY the built-in placeholder rig.

    This is the "deliberate procedural character" case, and it is what makes
    an#33 a real ambiguity rather than a theoretical one: it compiles to a tree
    that is byte-identical to the tree a *missing* descriptor produces.
    """
    from an.adapters.cutout.compile import _PLACEHOLDER_PARTS

    return {"characters": {ref: {"parts": list(_PLACEHOLDER_PARTS)}}}


def test_a_missing_character_descriptor_is_no_longer_silent():
    """It drew a different character and said nothing (an#33).

    Verified directly at the time: `warnings raised: []`, and `svg_sprite`
    simply absent from the compiled scene. Three CI runners then agreed
    perfectly about a picture that was not the picture, and the agreement read
    as a clean positive result.
    """
    shot = Shot(id="s1", style="cutout", duration=1.0, entities=[_character()])
    with pytest.warns(CutoutCompileWarning) as record:
        compile_shot(shot, mall={"characters": {}})
    msg = "\n".join(str(w.message) for w in record)
    assert "c-v1" in msg, "the warning must name the ref that resolved to nothing"
    assert "characters" in msg, "and the store it looked in"
    assert "placeholder" in msg, "and what got drawn instead"


def test_strict_assets_refuses_to_draw_a_stand_in():
    """The gate anything measuring pixels needs."""
    shot = Shot(id="s1", style="cutout", duration=1.0, entities=[_character()])
    with pytest.raises(CutoutCompileError) as e:
        compile_shot(shot, mall={"characters": {}}, strict_assets=True)
    msg = str(e.value)
    assert "'s1'" in msg, "the error must name the shot"
    assert "c-v1" in msg and "characters" in msg
    assert "strict_assets" in msg, "and say how to opt back out deliberately"


def test_strict_assets_is_off_by_default_so_an_assetless_project_still_renders():
    """The fallback stays. `an` working out of the box depends on it.

    `examples/single_character` reaches the placeholder rig through exactly
    this path and must keep rendering from a clean checkout.
    """
    shot = Shot(id="s1", style="cutout", duration=1.0, entities=[_character()])
    with pytest.warns(CutoutCompileWarning):
        scene = compile_shot(shot, mall={"characters": {}})
    assert scene.scene.children, "the placeholder rig must still be drawn"


def test_the_compiled_scene_distinguishes_two_identical_pictures():
    """The heart of an#33, asserted as the ambiguity it actually is.

    A missing descriptor and a deliberately-procedural character compile to the
    SAME scene tree — so no assertion over the rendered pixels, the visual
    kinds, or the node paths can tell them apart. The record has to carry it.
    """
    shot = Shot(id="s1", style="cutout", duration=1.0, entities=[_character()])
    with pytest.warns(CutoutCompileWarning):
        missing = compile_shot(shot, mall={"characters": {}})
    intended = compile_shot(shot, mall=_placeholder_rig_store())

    assert missing.scene == intended.scene, (
        "these two must stay indistinguishable in the scene tree — if they ever "
        "diverge, this test is no longer testing an#33's ambiguity"
    )
    assert [r.fallback for r in missing.asset_resolution] == [True]
    assert [r.fallback for r in intended.asset_resolution] == [False]
    assert missing.asset_resolution[0].resolved == "placeholder"
    assert intended.asset_resolution[0].resolved == "parts"


def test_a_descriptor_backed_character_is_not_a_fallback():
    """The guard must not fire on the case it exists to protect."""
    shot = Shot(id="s1", style="cutout", duration=1.0, entities=[_character()])
    descriptor = {
        "kind": "CharacterDescriptor",
        "name": "c-v1",
        "parts": {"head": {"src": "characters/c-v1/parts/head.svg"}},
    }
    scene = compile_shot(
        shot, mall={"characters": {"c-v1": descriptor}}, strict_assets=True
    )
    assert [r.resolved for r in scene.asset_resolution] == ["descriptor"]
    assert not any(r.fallback for r in scene.asset_resolution)


def test_an_environment_ref_that_names_nothing_draws_the_default_audibly():
    """`ref: kitchen` silently rendered the generic backdrop.

    Same class as the character case: a plausible picture that is not the one
    the scene asked for.
    """
    shot = Shot(
        id="s1",
        style="cutout",
        duration=1.0,
        entities=[
            AssetRef(kind="environment", id="env", store="environments", ref="kitchen")
        ],
    )
    with pytest.warns(CutoutCompileWarning, match="kitchen"):
        scene = compile_shot(shot, mall={"environments": {}})
    assert [r.fallback for r in scene.asset_resolution] == [True]
    with pytest.raises(CutoutCompileError):
        compile_shot(shot, mall={"environments": {}}, strict_assets=True)


@pytest.mark.parametrize("ref", ["park", "night", "default"])
def test_a_built_in_environment_preset_is_not_a_fallback(ref):
    """Presets are a documented built-in, not a stand-in for a missing asset."""
    shot = Shot(
        id="s1",
        style="cutout",
        duration=1.0,
        entities=[
            AssetRef(kind="environment", id="env", store="environments", ref=ref)
        ],
    )
    scene = compile_shot(shot, mall={"environments": {}}, strict_assets=True)
    assert [r.resolved for r in scene.asset_resolution] == ["preset"]


def test_the_render_path_threads_strict_assets_to_the_compiler(monkeypatch):
    """A flag nothing reads is worse than no flag: it reads as protection.

    Asserted at the seam rather than end-to-end, so it runs in the default lane
    — no ffmpeg, no browser, and (via the stub below) not even Playwright
    installed. `CutoutRenderer.render` imports `playwright.sync_api` before it
    compiles anything, so a real import here would make the guard skip in CI,
    which is the same as not having it.
    """
    import sys
    import types

    from an.adapters._base import RenderContext
    from an.adapters.cutout import render as render_mod

    seen: dict = {}

    class _Stop(Exception):
        """Aborts the render at the seam under test; nothing past it is asserted."""

    def _spy(*args, **kwargs):
        seen.update(kwargs)
        raise _Stop

    if "playwright.sync_api" not in sys.modules:
        pkg = types.ModuleType("playwright")
        api = types.ModuleType("playwright.sync_api")
        api.sync_playwright = lambda: None
        pkg.sync_api = api
        monkeypatch.setitem(sys.modules, "playwright", pkg)
        monkeypatch.setitem(sys.modules, "playwright.sync_api", api)

    monkeypatch.setattr(render_mod, "compile_shot", _spy)
    monkeypatch.setattr(render_mod, "_ensure_ffmpeg_available", lambda: None)

    shot = Shot(id="s1", style="cutout", duration=1.0, entities=[_character()])
    ctx = RenderContext(mall={}, work_dir=Path("."), strict_assets=True)
    with pytest.raises(_Stop):
        render_mod.CutoutRenderer().render(shot, ctx)

    assert seen.get("strict_assets") is True, (
        "RenderContext.strict_assets did not reach compile_shot; the flag would "
        "silently protect nothing"
    )
