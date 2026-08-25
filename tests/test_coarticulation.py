"""Co-articulation passes in front of viseme emission (an#97, epic #9 Wave 6).

The pure passes are doctested in `an/adapters/cutout/coarticulate.py`; this
module pins how the COMPILER uses them, that the old drop-not-hold condenser
is reproducible behind the `COARTICULATION_ENABLED` flag (the demo's left
pane), what the passes do to the wave's mid-line golden scene, the
keyframes-per-second provenance, and the legibility judge's parser + cassette
node.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from an.adapters.cutout import compile as compile_mod
from an.adapters.cutout.coarticulate import (
    DEFAULT_MIN_HOLD_S,
    DOMINANCE,
    Cue,
    coarticulate,
    condense,
    lead,
    suppress_weak,
)
from an.adapters.cutout.compile import compile_shot
from an.ir.schema import AssetRef, Dialogue, Shot, VisemeKeyframe, VisemeTrack

from .conftest import requires_live_api
from .test_swap_channels import _evaluate, _python_timeline

ROOT = Path(__file__).resolve().parents[1]


def _shot(keys, *, duration=1.0, fps=24):
    return Shot(
        id="s1",
        renderer="cutout",
        duration=duration + 0.5,
        entities=[AssetRef(kind="character", id="c", store="characters", ref="c-v1")],
        dialogue=[
            Dialogue(
                speaker="c",
                text="hi",
                start=0.0,
                duration=duration,
                viseme_track=VisemeTrack(keyframes=[VisemeKeyframe(time=t, viseme=v) for t, v in keys]),
            )
        ],
    )


def _mouth_keys(scene):
    (clip,) = [a for aid, a in scene.animations.items() if aid.startswith("__viseme__")]
    (ch,) = clip.channels
    return [(round(k.time, 4), k.value) for k in ch.keyframes]


def _compile(shot, **kw):
    import warnings

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return compile_shot(shot, mall={"characters": {}}, fps=24, **kw)


# ------------------------------------------------------------ the defect


CLUSTER = [(0.0, "X"), (0.30, "B"), (0.34, "A"), (0.38, "D"), (0.80, "X")]


def test_the_old_condenser_dropped_the_shapes_a_viewer_reads(monkeypatch):
    """Epic #9 defect 5b, reproduced through the flag: the 40 ms `B` owned the
    window and the closure `A` and the open vowel `D` were discarded."""
    monkeypatch.setattr(compile_mod, "COARTICULATION_ENABLED", False)
    keys = _mouth_keys(_compile(_shot(CLUSTER)))
    assert [v for _, v in keys] == ["X", "B", "X", "X"]  # + the terminal rest


def test_the_passes_hold_and_vote_and_lead():
    """With the passes: the window at the cluster votes for `D` (its span
    inside the window times its dominance beats the 40 ms `B` and `A`), and
    every shape is led by two frames."""
    keys = _mouth_keys(_compile(_shot(CLUSTER)))
    values = [v for _, v in keys]
    assert "D" in values and "B" not in values
    # The 40 ms `B` is a sub-frame tongue shape and is suppressed before the
    # vote, so the window opens at `A` (0.34) and its winner `D` shows there —
    # led by two frames at 24 fps.
    t_d = next(t for t, v in keys if v == "D")
    assert t_d == pytest.approx(0.34 - 2 / 24, abs=1e-3)


def test_the_terminal_rest_invariant_survives_the_passes():
    keys = _mouth_keys(_compile(_shot(CLUSTER)))
    assert keys[-1] == (1.0, "X")
    assert all(0.0 <= t <= 1.0 for t, _ in keys)


def test_the_passes_thin_the_raw_track_but_not_below_the_old_condenser(monkeypatch):
    """The wave's measurement, at the compiler, on `misc/bench/corpus/dialogue`:
    fewer viseme keyframes per second than the RAW provider track, capped by
    the hold — and MORE than the old condenser, which was cheaper only by
    dropping the shapes a viewer reads. The direction against the old loop is
    pinned so a doc claiming "fewer than before" fails here first."""
    from an.bench.run import viseme_keyframes_per_second
    from an.project import load

    p = load(ROOT / "misc/bench/corpus/dialogue")
    shot = p.scene.timeline[0]
    on = compile_shot(shot, mall=p.mall, fps=24, strict_assets=True)
    monkeypatch.setattr(compile_mod, "COARTICULATION_ENABLED", False)
    off = compile_shot(shot, mall=p.mall, fps=24, strict_assets=True)
    from an.adapters.cutout.serialize import to_dict

    rate_on = viseme_keyframes_per_second(to_dict(on))
    rate_off = viseme_keyframes_per_second(to_dict(off))
    line = shot.dialogue[0]
    rate_raw = (len(line.viseme_track.keyframes) - 1) / line.duration
    assert rate_on is not None and rate_off is not None
    # Against the RAW provider track the passes thin the mouth to the hold's
    # ceiling (~1/0.14 s). Against the old condenser the count goes UP — the
    # old loop had fewer keyframes only because it DROPPED the closures and
    # open vowels a viewer reads; the vote keeps a shape per window and never
    # loses one. Measured on this scene: raw ~17/s, old ~5.8/s, passes ~7.3/s.
    assert rate_on < rate_raw
    assert rate_on <= 1.0 / DEFAULT_MIN_HOLD_S + 0.5
    assert rate_off < rate_on, (rate_off, rate_on)
    # And the frame-14 golden changes: today's `C` is voted against.

    before = _evaluate(_python_timeline(off), 14 / 24)[("talker/head/mouth", "viseme")]
    after = _evaluate(_python_timeline(on), 14 / 24)[("talker/head/mouth", "viseme")]
    assert before == "C"
    assert after != before


def test_a_line_ending_between_frames_still_closes_the_mouth():
    """The terminal rest at `line.duration` must be SAMPLED: with a 0.71 s
    line at 24 fps, frame 17 is 0.708 s and frame 18 is 0.75 s — outside a
    0.71 s window — so the runtime kept frame 17's shape and the mouth stayed
    open after the line (`single_character` f0024, pre-an#97). The clip window
    now ends on the first frame at or after the line's end."""
    from tests.test_swap_channels import _evaluate, _python_timeline

    shot = _shot([(0.0, "X"), (0.2, "D"), (0.5, "C"), (0.71, "X")], duration=0.71)
    scene = _compile(shot)
    placed = next(p for t in scene.timeline.tracks for p in t.clips if p.animation_id.startswith("__viseme__"))
    assert placed.duration == pytest.approx(18 / 24)
    tl = _python_timeline(scene)
    assert _evaluate(tl, 18 / 24)[("c/head/mouth", "viseme")] == "X"


def test_provenance_carries_the_rate_per_shot():
    from types import SimpleNamespace as NS

    from an.bench.run import shot_policy_provenance

    from an.adapters.cutout.serialize import to_dict

    speaking = to_dict(_compile(_shot(CLUSTER)))
    silent = to_dict(_compile(Shot(id="q", renderer="cutout", duration=1.0)))
    prov = shot_policy_provenance([NS(shot_id="a", scene_json=speaking), NS(shot_id="b", scene_json=silent)])
    assert prov["viseme_keyframes_per_second"]["b"] is None
    assert prov["viseme_keyframes_per_second"]["a"] > 0


# ------------------------------------------------------------ the rules


def test_dominance_order_is_the_sourced_one():
    """A (bilabial closure) > F, G > E, D > C > X > B, H (tongue)."""
    d = DOMINANCE
    assert d["A"] > d["F"] == d["G"] > d["E"] == d["D"] > d["C"] > d["X"] > d["B"] == d["H"]


def test_a_cue_intensity_scales_its_vote():
    """A provider that knows a `B` is the vowel EE, not a consonant, can say so."""
    # `B`'s table dominance is 0.3, so a provider vouching for it as the vowel
    # EE has to scale it past the closure `A` (0.04 s x 1.0) to win a window.
    strong_b = [(0.0, "X"), (0.30, "B", 5.0), (0.34, "A"), (0.38, "D", 0.1), (0.80, "X")]
    weak_b = [(0.0, "X"), (0.30, "B", 0.1), (0.34, "A"), (0.38, "D", 1.0), (0.80, "X")]
    assert [c.code for c in condense(strong_b, min_hold_s=0.14)][1] == "B"
    assert [c.code for c in condense(weak_b, min_hold_s=0.14)][1] == "D"


def test_lead_never_moves_a_cue_before_zero_and_keeps_order():
    out = lead([(0.0, "X"), (0.02, "A"), (0.05, "D"), (0.9, "X")], lead_s=2 / 24)
    assert out[0].time == 0.0 and [c.time for c in out] == sorted(c.time for c in out)
    assert out[0].code == "D"  # the cue nearest the sound wins the collision at 0


def test_no_pass_constant_is_in_the_viseme_cache_key():
    """A knob change is a recompile, never a paid re-alignment."""
    import inspect

    from an.audio import pipeline

    src = inspect.getsource(pipeline)
    for name in ("min_hold", "lead_s", "decay_s", "DOMINANCE", "coarticulate"):
        assert name not in src, name
    assert DEFAULT_MIN_HOLD_S == compile_mod._LEGACY_MIN_VISEME_GAP_S, "unchanged until measured"


# ------------------------------------------------------------ the judge


def test_legibility_parser_distinguishes_no_verdict_from_a_low_score():
    from an.verify.vision import _parse_legibility

    assert _parse_legibility('{"legibility": 1, "heard": ""}') == (1, "")
    assert _parse_legibility('```json\n{"legibility": 3, "heard": "hold"}\n```') == (3, "hold")
    assert _parse_legibility("") is None
    assert _parse_legibility('{"legibility": true}') is None
    assert _parse_legibility('{"legibility": "4"}') is None


def test_judge_legibility_goes_through_the_injected_seam_with_the_text_in_the_prompt():
    from an.verify.vision import judge_legibility, legibility_prompt

    seen = {}

    def fake(frames, *, prompt, model, max_tokens, api_key=None):
        seen["prompt"] = prompt
        return '{"legibility": 4, "heard": "hold the shape"}'

    out = judge_legibility([b"png"], "Hold the shape", judge=fake)
    assert out == (4, "hold the shape")
    assert "Hold the shape" in seen["prompt"] and seen["prompt"] == legibility_prompt("Hold the shape")


def test_a_different_line_is_a_different_recording():
    from an.verify.vision import judge_key, legibility_prompt

    a = judge_key([b"x"], prompt=legibility_prompt("Hold the shape"), model="m", max_tokens=1)
    b = judge_key([b"x"], prompt=legibility_prompt("Hold the vote"), model="m", max_tokens=1)
    assert a != b


LINE = "Hold the shape, then vote."


def _strips():
    """The frozen strips of the `dialogue` line, before and after
    (`tests/_lipsync_strips.py` is the freezer and the SSOT for their shape)."""
    from tests._lipsync_strips import load_strips

    return load_strips()


def test_legibility_does_not_drop_under_the_passes_replay_only():
    """The wave's second number, on the committed cassette — replay only; a
    miss is `CassetteMiss`, never a call. Skips (loudly) until the strips and
    the cassette are recorded once with `AN_LIVE_API_TESTS=1`."""
    from tests._vision_cassettes import CASSETTE_DIR, memoized_judge
    from an.verify.vision import judge_key, judge_legibility, legibility_prompt

    strips = _strips()
    if len(strips) < 2:
        pytest.skip("frozen lipsync strips missing — python tests/_lipsync_strips.py")
    for variant in strips:
        key = judge_key(strips[variant], prompt=legibility_prompt(LINE))
        if not (CASSETTE_DIR / f"{key}.json").is_file():
            pytest.skip(f"no cassette for the {variant} strip — record once with AN_LIVE_API_TESTS=1 pytest -m live_api")
    judge = memoized_judge(replay_only=True)
    before = judge_legibility(strips["before"], LINE, judge=judge)
    after = judge_legibility(strips["after"], LINE, judge=judge)
    assert before is not None and after is not None
    assert after[0] >= before[0], (before, after)


@pytest.mark.live_api
@requires_live_api
def test_record_the_legibility_cassette():
    """Spends once (~$0.005 x 2). Gated on the explicit positive opt-in — the
    marker alone opts out of the network guard, `requires_live_api` is the
    skip (the first draft had only the marker and CI ran it)."""
    from tests._vision_cassettes import memoized_judge
    from an.verify.vision import judge_legibility

    strips = _strips()
    assert len(strips) == 2, "freeze the strips first: python tests/_lipsync_strips.py"
    judge = memoized_judge(replay_only=False)
    for variant in strips:
        assert judge_legibility(strips[variant], LINE, judge=judge) is not None


def test_the_demo_flag_restores_itself():
    """The demo flips the module flag for one render and puts it back."""
    src = (ROOT / "misc/demos/build_demos.py").read_text(encoding="utf-8")
    assert "compile_mod.COARTICULATION_ENABLED = False" in src
    assert "compile_mod.COARTICULATION_ENABLED = original" in src
    assert json.dumps(compile_mod.COARTICULATION_ENABLED) == "true"


def test_the_recording_test_skips_without_the_opt_in(tmp_path):
    """The mutant that reached CI: `@pytest.mark.live_api` alone opts the test
    OUT of the network guard and skips nothing — `requires_live_api` is the
    skip. Observed through pytest itself, with the opt-in and the CI marker
    both stripped from the environment, so the assertion is about what a bare
    `pytest` does and not about which decorators the function carries."""
    import os
    import subprocess
    import sys

    env = {k: v for k, v in os.environ.items() if k not in ("AN_LIVE_API_TESTS", "CI")}
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-rs", "--no-header",
         "tests/test_coarticulation.py::test_record_the_legibility_cassette"],
        cwd=ROOT, env=env, capture_output=True, text=True, check=False,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "1 skipped" in proc.stdout and "AN_LIVE_API_TESTS" in proc.stdout, proc.stdout
    assert "failed" not in proc.stdout and "passed" not in proc.stdout.split("\n")[-2], proc.stdout


# --- an#97 review: the four mutants that survived, and the four edge defects ---


def _codes(cues):
    return [(round(c.time, 3), c.code) for c in cues]


def test_a_carried_member_votes_with_its_in_window_span_not_its_raw_span():
    """Mutant 1b: `F` (raw span 0.10 from 0.10) carries into the window at 0.14,
    where its in-window span is only 0.06 — `A` (0.07 inside) beats it. Voting
    with the raw span would hand F the window."""
    out = _codes(condense([(0, "X"), (0.10, "F"), (0.20, "A"), (0.27, "C"), (0.8, "X")], min_hold_s=0.14, end=1.0))
    assert out == [(0.0, "X"), (0.14, "A"), (0.28, "C"), (0.8, "X")], out


def test_the_decay_pass_delays_a_rest_that_arrives_too_soon():
    """Mutant 5: a rest 50 ms after `D` is pushed to `D + 0.12` before the hold
    sees it; without the pass the rest either shows at its raw (led) time or
    wins the window outright. Here D leads to 0.217, the rest to 0.267, decay
    puts it at 0.337, and the hold places it at its own window at 0.357."""
    out = _codes(coarticulate([(0, "X"), (0.30, "D"), (0.35, "X"), (0.9, "B")], fps=24, end=1.0))
    rest_times = [t for t, code in out if code == "X" and t > 0]
    assert rest_times and rest_times[0] >= round(0.30 - 2 / 24 + 0.12, 3) - 1e-9, out
    assert out[1] == (0.217, "D"), out


@pytest.mark.parametrize("bad", [0, 6, -1, 5.5])
def test_the_legibility_parser_refuses_a_score_off_the_scale(bad):
    """Mutant 12: only 1–5 is a verdict; 0, 6 or a fraction is a reply that
    did not follow the rubric and must read as `None`, never as a number."""
    from an.verify.vision import _parse_legibility

    assert _parse_legibility(json.dumps({"legibility": bad, "heard": "x"})) is None


def test_a_raw_key_past_the_line_end_is_not_emitted():
    """Mutant 14: a provider key at 1.2 s on a 1.0 s line must not become a
    keyframe; only the terminal rest sits at or after `line.duration`."""
    from an.adapters.cutout.serialize import to_dict

    track = VisemeTrack(keyframes=[VisemeKeyframe(time=0.0, viseme="X"), VisemeKeyframe(time=0.4, viseme="D"), VisemeKeyframe(time=1.2, viseme="A")])
    shot = Shot(
        id="s", renderer="cutout", duration=2.0,
        entities=[AssetRef(kind="character", id="c", store="characters", ref="c-v1")],
        dialogue=[Dialogue(speaker="c", text="hi", start=0.0, duration=1.0, viseme_track=track)],
    )
    js = to_dict(compile_shot(shot, fps=24))
    (clip,) = [a for k, a in js["animations"].items() if k.startswith("__viseme__")]
    (channel,) = clip["channels"]
    late = [k for k in channel["keyframes"] if k["time"] >= 1.0]
    assert late == [{"time": 1.0, "value": "X", "easing": "step"}], channel["keyframes"]


def test_without_end_the_last_cue_always_survives():
    """D4: with `end` unknown the last cue used to have a zero span, lose every
    vote and vanish — `[(0, X), (0.1, A)]` condensed to just the rest."""
    assert _codes(condense([(0, "X"), (0.1, "A")], min_hold_s=0.14)) == [(0.0, "X"), (0.14, "A")]
    assert _codes(condense([(0, "X"), (0.3, "D"), (0.4, "X")], min_hold_s=0.14)) == [(0.0, "X"), (0.3, "D"), (0.44, "X")]
    assert _codes(suppress_weak([(0, "X"), (0.2, "D"), (0.5, "B")], max_weak_s=0.04))[-1] == (0.5, "B")


def test_window_edges_are_not_float_fragile():
    """D5: `0.28 + 0.14 == 0.42000000000000004`, so a cue at exactly three
    holds fell INSIDE the third window and, with `end` given, was placed a
    rounding error late; with `end` unknown it vanished."""
    out = _codes(condense([(0, "X"), (0.14, "C"), (0.28, "D"), (0.42, "A")], min_hold_s=0.14, end=1.0))
    assert out == [(0.0, "X"), (0.14, "C"), (0.28, "D"), (0.42, "A")], out
    raw = condense([(0, "X"), (0.14, "C"), (0.28, "D"), (0.42, "A")], min_hold_s=0.14, end=1.0)
    assert raw[-1].time == 0.42, raw[-1].time


def test_a_track_ending_on_rest_still_ends_on_rest_after_the_passes():
    """D6: the decay pushed a closing rest past the line's end and it was lost
    from the standalone API (the compiler masks this with its own terminal
    rest). It now lands at `end`."""
    out = _codes(coarticulate([(0, "X"), (0.3, "D"), (0.69, "C"), (0.71, "X")], fps=24, end=0.71))
    assert out[-1] == (0.71, "X"), out


def test_the_knobs_are_validated():
    with pytest.raises(ValueError, match="lead_s"):
        coarticulate([(0, "X")], fps=24, end=1.0, lead_s=-0.1)
    with pytest.raises(ValueError, match="fps"):
        coarticulate([(0, "X")], fps=0, end=1.0)


def test_out_of_order_dialogue_lines_are_emitted_in_time_order():
    """D8: two back-to-back lines on one speaker — the first's frame-ceiled
    window overlaps the second's first frame, and the runtime resolves same-
    track overlap by clip order. Authored `[line2, line1]`, the mouth used to
    show line 1's rest over line 2's opening shape for one frame."""
    from an.adapters.cutout.serialize import to_dict

    def track(code):
        return VisemeTrack(keyframes=[VisemeKeyframe(time=0.0, viseme="X"), VisemeKeyframe(time=0.05, viseme=code)])

    line1 = Dialogue(speaker="c", text="one", start=0.0, duration=0.71, viseme_track=track("D"))
    line2 = Dialogue(speaker="c", text="two", start=0.71, duration=0.71, viseme_track=track("A"))
    for order in ([line1, line2], [line2, line1]):
        shot = Shot(
            id="s", renderer="cutout", duration=2.0,
            entities=[AssetRef(kind="character", id="c", store="characters", ref="c-v1")],
            dialogue=list(order),
        )
        compiled = compile_shot(shot, fps=24)
        js = to_dict(compiled)
        assert _evaluate(_python_timeline(compiled), 18 / 24)[("c/head/mouth", "viseme")] == "A", order
        (trk,) = [t for t in js["timeline"]["tracks"] if t["target_root"] == "c"]
        assert [c["start_time"] for c in trk["clips"] if "__viseme__" in c["animation_id"]] == [0.0, 0.71]
