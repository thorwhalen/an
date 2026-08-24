"""Blinks are compiled channels (an#88), not a runtime pass.

The runtime used to blink by regex: a post-pose sweep over every
`<entity>/head/(left_eye|right_eye)` node that forced `scale.y` on every
frame — which is why an authored eye `scale_y` could never reach the screen.
The compiler now emits one channel per eye, ahead of everything authored on
the entity's track, with the runtime's exact schedule (period, duration,
depth, and the phase as a pure function of the entity name).

Mechanism splits by what the eye can do: an eye whose visual projects the
`eyelid` set swaps ART (a step channel through the one swap implementation);
any other eye — the procedural drawn eye, a rig without closed-eye art — gets
the sine squash as a `scale_y` channel sampled at the frame times.
"""

from __future__ import annotations

import math
import re
import shutil
from pathlib import Path

import pytest

from an.adapters.cutout.compile import (
    EYE_NODE_NAMES,
    _BLINK_DEPTH,
    _BLINK_DURATION_S,
    _BLINK_PERIOD_S,
    _blink_windows,
    blink_phase,
    compile_shot,
)
from an.ir.schema import AssetRef, Shot, TweenAction
from an.stores.characters import CharactersStore

RUNTIME_JS = (
    Path(__file__).resolve().parents[1] / "an" / "data" / "cutout_runtime" / "runtime.js"
)
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "characters"


def _procedural_shot(duration=2.5, actions=()):
    return Shot(
        id="s",
        style="cutout",
        duration=duration,
        entities=[AssetRef(kind="character", id="charlie", store="characters", ref="c")],
        actions=list(actions),
    )


def _blink_channels(scene):
    """target -> (property, keyframes at ABSOLUTE times), merged across the
    one-clip-per-window placements (an#88 review: per-window clips, so an
    authored eye value persists between blinks like every other property)."""
    from an.adapters.cutout.serialize import KeyframeJSON

    starts = {
        p.animation_id: p.start_time for t in scene.timeline.tracks for p in t.clips
    }
    out: dict = {}
    for aid, a in scene.animations.items():
        if not aid.startswith("__blink__"):
            continue
        for ch in a.channels:
            prop, kfs = out.setdefault(ch.target, (ch.property, []))
            assert prop == ch.property
            kfs.extend(
                KeyframeJSON(time=starts[aid] + k.time, value=k.value, easing=k.easing)
                for k in ch.keyframes
            )
    for prop, kfs in out.values():
        kfs.sort(key=lambda k: k.time)
    return out


# ---------------------------------------------------------------- the schedule


def test_the_phase_is_the_runtime_s_rule_ported_exactly():
    """Pinned against the values the research measured from the JS
    (`_strHash % 1000 / 1000`): charlie 0.762, maya 0.284 — and `plates`
    (the saturated_outline entity), whose int32 hash is NEGATIVE, so it
    pins the `Math.abs` step that every positive-hash name lets slip."""
    assert blink_phase("charlie") == 0.762
    assert blink_phase("maya") == 0.284
    assert blink_phase("plates") == 0.667


def test_blink_windows_reproduce_the_runtime_s_cycle_rule():
    """The runtime blinked when `(t + phase*P) % P < D`; every window the
    compiler emits must satisfy exactly that predicate at its interior points
    and nowhere just outside."""
    for entity in ("charlie", "maya", "field", "ada"):
        phase = blink_phase(entity)
        windows = _blink_windows(entity, 12.0)
        assert windows, entity
        for start, end in windows:
            assert end - start == pytest.approx(_BLINK_DURATION_S)
            mid = (start + end) / 2
            assert (mid + phase * _BLINK_PERIOD_S) % _BLINK_PERIOD_S < _BLINK_DURATION_S
            just_after = end + 1e-6
            assert (
                just_after + phase * _BLINK_PERIOD_S
            ) % _BLINK_PERIOD_S >= _BLINK_DURATION_S


def test_the_golden_blink_frames_are_where_the_research_measured_them():
    """single_character f0024 (t=1.0) and promote_demo f0070 (t=70/24) sit
    mid-blink — the only two goldens the move could touch (measured after:
    only promote_demo's PNG changed; single_character's squash is
    byte-identical)."""
    assert any(s <= 1.0 < e for s, e in _blink_windows("charlie", 2.5))
    assert any(s <= 70 / 24 < e for s, e in _blink_windows("maya", 3.0))
    # graded_field / saturated_outline: 0.5s shots, first blink beyond the end.
    assert not [w for w in _blink_windows("field", 0.5) if w[0] < 0.5]
    assert not [w for w in _blink_windows("plates", 0.5) if w[0] < 0.5]


def test_the_hash_iterates_utf16_units_like_the_runtime_did():
    """`charCodeAt` walks UTF-16 code units; a non-BMP character is two of
    them. Values measured from the deleted JS `_strHash` under node."""
    from an.adapters.cutout.compile import _js_string_hash

    assert _js_string_hash("\U0001F600") == 1772899
    assert _js_string_hash("a\U0001F600b") == 57849694
    assert _js_string_hash("charlie") % 1000 == 762


# ------------------------------------------------------------ the squash path


def test_procedural_eyes_get_a_scale_y_squash_channel_at_the_frame_times():
    scene = compile_shot(_procedural_shot(), mall={"characters": {}}, fps=24)
    chans = _blink_channels(scene)
    assert set(chans) == {"charlie/head/left_eye", "charlie/head/right_eye"}
    prop, kfs = chans["charlie/head/left_eye"]
    assert prop == "scale_y"
    # The golden frame 24 (t=1.0) carries the value the runtime computed there:
    # cycle 0.048 → 1 - 0.95*sin(0.048/0.14*pi) = 0.163434 (research §6).
    at_golden = [k for k in kfs if k.time == pytest.approx(1.0)]
    assert at_golden and at_golden[0].value == pytest.approx(0.163434, abs=1e-6)
    # Outside every window the value is exactly 1.0 (the return-to-rest
    # keyframe, applied on a rendered frame; between windows the pose
    # carries no eye value at all, so an authored value persists).
    for k in kfs:
        in_window = any(s < k.time < e for s, e in _blink_windows("charlie", 2.5))
        if not in_window:
            assert k.value == 1.0
    # Every in-window keyframe is at a frame time, so the frame render sees
    # the sampled value rather than an interpolation between neighbours.
    for k in kfs:
        if k.value != 1.0:
            assert (k.time * 24) == pytest.approx(round(k.time * 24))


def test_the_squash_values_match_the_runtime_s_formula():
    scene = compile_shot(_procedural_shot(duration=6.0), mall={"characters": {}}, fps=30)
    _, kfs = _blink_channels(scene)["charlie/head/right_eye"]
    phase = blink_phase("charlie")
    for k in kfs:
        cycle = (k.time + phase * _BLINK_PERIOD_S) % _BLINK_PERIOD_S
        if cycle < _BLINK_DURATION_S and k.value != 1.0:
            u = cycle / _BLINK_DURATION_S
            assert k.value == pytest.approx(1.0 - _BLINK_DEPTH * math.sin(u * math.pi))


# ------------------------------------------------------------- the swap path


@pytest.fixture()
def gale_store(tmp_path):
    shutil.copytree(FIXTURES / "gale", tmp_path / "gale")
    return CharactersStore(tmp_path)


def test_an_eye_with_closed_art_blinks_by_eyelid_swap(gale_store):
    # 8 s: gale's phase puts its first blink past 3 s, so a short shot has an
    # OPEN-only channel — correct, and not what this test is about.
    shot = Shot(
        id="s",
        style="cutout",
        duration=8.0,
        entities=[AssetRef(kind="character", id="gale", store="characters", ref="gale")],
    )
    scene = compile_shot(shot, mall={"characters": gale_store})
    chans = _blink_channels(scene)
    assert set(chans) == {"gale/head/left_eye", "gale/head/right_eye"}
    prop, kfs = chans["gale/head/left_eye"]
    assert prop == "eyelid"
    assert kfs[0].value == "OPEN"
    assert {k.value for k in kfs} <= {"OPEN", "CLOSED"}
    assert all(k.easing == "step" for k in kfs)
    assert any(k.value == "CLOSED" for k in kfs)
    assert scene.meta.blink_phases == {"gale": blink_phase("gale")}


def test_a_rig_without_closed_eye_art_falls_back_to_the_squash(gale_store, tmp_path):
    for side in ("l", "r"):
        (tmp_path / "gale" / "parts" / f"eye_{side}_closed.svg").unlink()
    # 8 s, so gale's first blink window (3.44 s) falls inside the shot —
    # per-window clips mean a shot with no window has no clip at all.
    shot = Shot(
        id="s",
        style="cutout",
        duration=8.0,
        entities=[AssetRef(kind="character", id="gale", store="characters", ref="gale")],
    )
    scene = compile_shot(shot, mall={"characters": gale_store})
    props = {p for p, _ in _blink_channels(scene).values()}
    assert props == {"scale_y"}


def test_a_baked_face_character_has_no_eyes_and_so_no_blinks():
    descriptor = {
        "kind": "CharacterDescriptor",
        "name": "diane",
        "face_overlay": False,
    }
    shot = Shot(
        id="s",
        style="cutout",
        duration=3.0,
        entities=[AssetRef(kind="character", id="diane", store="characters", ref="d")],
    )
    scene = compile_shot(shot, mall={"characters": {"d": descriptor}})
    assert not _blink_channels(scene)
    assert scene.meta.blink_phases == {}


# ------------------------------------------------------ authored eyes survive


def test_an_authored_eye_scale_y_tween_survives_to_the_pose():
    """The done-when's 'an authored eye scale_y tween survives to screen':
    the runtime's post-pose reset used to clobber it on every frame. Blink
    clips sit FIRST on the track, so a later authored clip wins."""
    from tests.test_swap_channels import _evaluate, _python_timeline

    shot = _procedural_shot(
        duration=2.5,
        actions=[
            TweenAction(
                target="charlie/head/left_eye",
                property="scale_y",
                from_value=3.0,
                to_value=3.0,
                duration=2.5,
                easing="linear",
            )
        ],
    )
    scene = compile_shot(shot, mall={"characters": {}}, fps=24)
    tl = _python_timeline(scene)
    key = ("charlie/head/left_eye", "scale_y")
    for t in (0.0, 1.0, 2.0):  # t=1.0 is mid-blink for charlie
        assert _evaluate(tl, t)[key] == 3.0
    # And the other eye, un-authored, still blinks.
    assert _evaluate(tl, 1.0)[("charlie/head/right_eye", "scale_y")] < 0.2


def test_an_authored_eye_value_persists_between_blinks_like_any_property():
    """One clip PER WINDOW: outside a blink the pose carries no eye value, so
    an authored tween's end value holds the way `scale_x`'s does. A
    whole-shot 1.0 fill snapped `scale_y` back the frame after the tween
    ended while `scale_x` held — the an#88 review's D2."""
    from tests.test_swap_channels import _evaluate, _python_timeline

    shot = _procedural_shot(
        duration=2.5,
        actions=[
            TweenAction(
                target="charlie/head/left_eye", property=p, from_value=3.0,
                to_value=3.0, duration=0.5, easing="linear",
            )
            for p in ("scale_y", "scale_x")
        ],
    )
    scene = compile_shot(shot, mall={"characters": {}}, fps=24)
    tl = _python_timeline(scene)
    pose = _evaluate(tl, 0.75)  # after the tweens, outside any blink window
    assert ("charlie/head/left_eye", "scale_y") not in pose
    assert ("charlie/head/left_eye", "scale_x") not in pose


def test_an_eye_that_rests_closed_does_not_blink(gale_store, tmp_path):
    """A sleeping character: the author closed the eyes; the old runtime never
    opened them either (it only squashed). Neither swap nor squash."""
    import json as _json

    desc_path = tmp_path / "gale" / "character.json"
    doc = _json.loads(desc_path.read_text(encoding="utf-8"))
    for slot in doc["slots"]:
        if slot["name"] in ("left_eye", "right_eye"):
            slot["attachment"] = "closed"
    desc_path.write_text(_json.dumps(doc), encoding="utf-8")
    shot = Shot(
        id="s",
        style="cutout",
        duration=8.0,
        entities=[AssetRef(kind="character", id="gale", store="characters", ref="gale")],
    )
    scene = compile_shot(shot, mall={"characters": gale_store})
    assert not _blink_channels(scene)


def test_a_window_straddling_the_shot_start_begins_in_the_right_state(gale_store):
    """Entity `awg` has phase 0.009: its first window is (-0.036, 0.104), CLOSED
    from -0.001 to 0.069 — so frame 0 must be CLOSED (an#88 review D3)."""
    from tests.test_swap_channels import _evaluate, _python_timeline

    shot = Shot(
        id="s",
        style="cutout",
        duration=2.0,
        entities=[AssetRef(kind="character", id="awg", store="characters", ref="gale")],
    )
    scene = compile_shot(shot, mall={"characters": gale_store}, fps=24)
    tl = _python_timeline(scene)
    assert _evaluate(tl, 0.0)[("awg/head/left_eye", "eyelid")] == "CLOSED"
    assert _evaluate(tl, 1 / 24)[("awg/head/left_eye", "eyelid")] == "CLOSED"
    assert _evaluate(tl, 0.1)[("awg/head/left_eye", "eyelid")] == "OPEN"


def test_the_eyelid_channel_is_closed_exactly_for_the_central_half(gale_store):
    """CLOSED iff t is inside [start + 0.25·span, start + 0.75·span), OPEN
    everywhere else — evaluated at every frame. Reversing the OPEN/CLOSED
    order survived the first battery (eyes closed 54% of the time; only the
    browser-lane golden would have noticed)."""
    from an.adapters.cutout.compile import _EYELID_CLOSED_SPAN
    from tests.test_swap_channels import _evaluate, _python_timeline

    shot = Shot(
        id="s",
        style="cutout",
        duration=8.0,
        entities=[AssetRef(kind="character", id="gale", store="characters", ref="gale")],
    )
    scene = compile_shot(shot, mall={"characters": gale_store}, fps=24)
    tl = _python_timeline(scene)
    lo, hi = _EYELID_CLOSED_SPAN
    spans = [(s + lo * (e - s), s + hi * (e - s)) for s, e in _blink_windows("gale", 8.0)]
    closed_frames = 0
    for f in range(0, 8 * 24 + 1):
        t = f / 24
        pose = _evaluate(tl, t)
        value = pose.get(("gale/head/left_eye", "eyelid"), "OPEN")
        expected = "CLOSED" if any(a <= t < b for a, b in spans) else "OPEN"
        assert value == expected, (t, value, expected)
        closed_frames += value == "CLOSED"
    assert 0 < closed_frames < 8 * 24 * 0.1


def test_a_baked_face_never_blinks_even_if_the_rig_builds_eye_nodes(gale_store, tmp_path):
    """The policy lives in the emitter, not in the rig builder's naming: a
    baked face whose head slot is not named `head` used to get its face
    overlays (and blinks) back (an#88 review, mutant 8)."""
    import json as _json

    desc_path = tmp_path / "gale" / "character.json"
    doc = _json.loads(desc_path.read_text(encoding="utf-8"))
    doc["face_overlay"] = False
    for slot in doc["slots"]:
        if slot["name"] == "head":
            slot["name"] = "noggin"
    doc["skins"]["default"]["slots"]["noggin"] = doc["skins"]["default"]["slots"].pop("head")
    desc_path.write_text(_json.dumps(doc), encoding="utf-8")
    shot = Shot(
        id="s",
        style="cutout",
        duration=8.0,
        entities=[AssetRef(kind="character", id="gale", store="characters", ref="gale")],
    )
    scene = compile_shot(shot, mall={"characters": gale_store})
    # The rig builder DOES build the eyes here (a head slot not named after
    # its bone is not a primary slot, so nothing nests under it — a rig
    # design rule, not a blink one), which is exactly why the no-blink
    # policy has to live in the emitter.
    paths = set(_paths(scene.scene))
    assert any(p.rsplit("/", 1)[-1] in EYE_NODE_NAMES for p in paths)
    assert not _blink_channels(scene)
    assert scene.meta.blink_phases == {}


def test_an_unnested_eye_slot_still_blinks_by_its_leaf_name(gale_store, tmp_path):
    """The old regex needed `<entity>/head/<eye>`; the leaf-name rule blinks
    an eye wherever the rig put it (a head slot named otherwise nests the
    eyes at depth 2)."""
    import json as _json

    desc_path = tmp_path / "gale" / "character.json"
    doc = _json.loads(desc_path.read_text(encoding="utf-8"))
    for slot in doc["slots"]:
        if slot["name"] == "head":
            slot["name"] = "noggin"
    doc["skins"]["default"]["slots"]["noggin"] = doc["skins"]["default"]["slots"].pop("head")
    desc_path.write_text(_json.dumps(doc), encoding="utf-8")
    shot = Shot(
        id="s",
        style="cutout",
        duration=8.0,
        entities=[AssetRef(kind="character", id="gale", store="characters", ref="gale")],
    )
    scene = compile_shot(shot, mall={"characters": gale_store})
    assert "gale/left_eye" in _blink_channels(scene)


def _paths(node, prefix=""):
    path = f"{prefix}/{node.name}" if prefix else node.name
    if prefix or node.name != "root":
        yield path
        child_prefix = path
    else:
        child_prefix = ""
    for c in node.children:
        yield from _paths(c, child_prefix)


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_the_compiled_squash_matches_the_deleted_runtime_rule_at_every_frame():
    """The strongest browser-free evidence that the move changed no pixel:
    the deleted JS rule — `_strHash` and `(t + phase*P) % P < D`, sine
    squash — re-executed under node at every frame time, against the
    compiled channel evaluated through the Python spec. Covers a
    negative-hash name (`plates`) and several frame rates. (The independent
    review measured 1302 frames, 0 mismatches; this commits that check.)
    """
    import json
    import subprocess

    from tests.test_swap_channels import _evaluate, _python_timeline

    js = r"""
    function _strHash(s) { let h = 0; for (let i = 0; i < s.length; i++) { h = ((h << 5) - h) + s.charCodeAt(i); h |= 0; } return Math.abs(h); }
    const P = 4.0, D = 0.14;
    const cases = JSON.parse(process.argv[1]);
    const out = cases.map(([name, fps, duration]) => {
      const phase = (_strHash(name) % 1000) / 1000.0;
      const vals = [];
      const n = Math.round(duration * fps);
      for (let f = 0; f <= n; f++) {
        const t = f / fps;
        const cycle = (t + phase * P) % P;
        vals.push(cycle < D ? 1.0 - 0.95 * Math.sin((cycle / D) * Math.PI) : 1.0);
      }
      return vals;
    });
    console.log(JSON.stringify(out));
    """
    cases = [["charlie", 24, 2.5], ["plates", 30, 6.0], ["teacher", 25, 9.0], ["ada", 60, 5.0]]
    proc = subprocess.run(
        ["node", "-e", js, json.dumps(cases)], capture_output=True, text=True, timeout=30
    )
    assert proc.returncode == 0, proc.stderr
    expected = json.loads(proc.stdout)
    for (name, fps, duration), row in zip(cases, expected):
        shot = Shot(
            id="s",
            style="cutout",
            duration=duration,
            entities=[AssetRef(kind="character", id=name, store="characters", ref="c")],
        )
        tl = _python_timeline(compile_shot(shot, mall={"characters": {}}, fps=fps))
        key = (f"{name}/head/left_eye", "scale_y")
        last = 1.0
        for f, want in enumerate(row):
            pose = _evaluate(tl, f / fps)
            # Outside a clip the pose carries nothing — the runtime keeps the
            # last applied value, which the clip's rest keyframe made 1.0.
            got = pose.get(key, last)
            last = got
            assert got == pytest.approx(want, abs=1e-9), (name, fps, f / fps, got, want)


def test_the_runtime_carries_no_blink_machinery():
    """A source-text guard, deliberately: restoring the old post-pose pass
    beside the compiled channels would render byte-identically on every
    corpus golden (the pass forces the same value), so no pixel test could
    see it — only an authored eye channel in a browser could. Text is what
    is left to pin."""
    src = RUNTIME_JS.read_text(encoding="utf-8")
    code = re.sub(r"//[^\n]*", "", re.sub(r"/\*.*?\*/", "", src, flags=re.S))
    assert "applyProceduralBlinks" not in code
    assert "_strHash" not in code
    assert not re.search(r"left_eye\|right_eye", code)
    assert "blink" not in code.lower()


def test_eye_node_names_are_the_default_rig_s_eye_slots():
    from an.characters.schema import CharacterDescriptor

    slots = {s.name for s in CharacterDescriptor(name="x").slots}
    assert EYE_NODE_NAMES <= slots
