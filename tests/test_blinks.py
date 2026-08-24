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
    return {
        ch.target: (ch.property, ch.keyframes)
        for aid, a in scene.animations.items()
        if aid.startswith("__blink__")
        for ch in a.channels
    }


# ---------------------------------------------------------------- the schedule


def test_the_phase_is_the_runtime_s_rule_ported_exactly():
    """Pinned against the values the research measured from the JS
    (`_strHash % 1000 / 1000`): charlie 0.762, maya 0.284."""
    assert blink_phase("charlie") == 0.762
    assert blink_phase("maya") == 0.284


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
    mid-blink — the two goldens this change re-blesses, and no others."""
    assert any(s <= 1.0 < e for s, e in _blink_windows("charlie", 2.5))
    assert any(s <= 70 / 24 < e for s, e in _blink_windows("maya", 3.0))
    # graded_field / saturated_outline: 0.5s shots, first blink beyond the end.
    assert not [w for w in _blink_windows("field", 0.5) if w[0] < 0.5]
    assert not [w for w in _blink_windows("plates", 0.5) if w[0] < 0.5]


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
    # Outside every window the value is exactly 1.0 (no residual squash).
    assert kfs[0].time == 0.0 and kfs[0].value == 1.0
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
    shot = Shot(
        id="s",
        style="cutout",
        duration=3.0,
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


def test_the_runtime_carries_no_blink_machinery():
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
