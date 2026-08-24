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
)
from an.adapters.cutout.compile import compile_shot
from an.ir.schema import AssetRef, Dialogue, Shot, VisemeKeyframe, VisemeTrack

ROOT = Path(__file__).resolve().parents[1]


def _shot(keys, *, duration=1.0, fps=24):
    return Shot(
        id="s1",
        style="cutout",
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


def test_the_passes_reduce_keyframes_on_the_corpus_dialogue_scene(monkeypatch):
    """The wave's measurement, at the compiler: fewer mouth-shape changes per
    second on `misc/bench/corpus/dialogue` than the old condenser produced."""
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
    from tests.test_swap_channels import _evaluate, _python_timeline

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
    silent = to_dict(_compile(Shot(id="q", style="cutout", duration=1.0)))
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


LEGIBILITY_FRAMES = ROOT / "tests" / "fixtures" / "vision_frames" / "lipsync"
LINE = "Hold the shape, then vote."


def _strips():
    """The frozen 8-frame strips of the `dialogue` line, before and after."""
    out = {}
    for variant in ("before", "after"):
        files = sorted((LEGIBILITY_FRAMES / variant).glob("*.png"))
        if len(files) == 8:
            out[variant] = [f.read_bytes() for f in files]
    return out


def test_legibility_does_not_drop_under_the_passes_replay_only():
    """The wave's second number, on the committed cassette — replay only; a
    miss is `CassetteMiss`, never a call. Skips (loudly) until the strips and
    the cassette are recorded once with `AN_LIVE_API_TESTS=1`."""
    from tests._vision_cassettes import CASSETTE_DIR, memoized_judge
    from an.verify.vision import judge_key, judge_legibility, legibility_prompt

    strips = _strips()
    if len(strips) < 2:
        pytest.skip("frozen lipsync strips not recorded yet (tests/fixtures/vision_frames/lipsync/{before,after})")
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
def test_record_the_legibility_cassette():
    """Spends once (~$0.005 x 2). Gated on the explicit positive opt-in."""
    from tests._vision_cassettes import memoized_judge
    from an.verify.vision import judge_legibility

    strips = _strips()
    assert len(strips) == 2, "render the strips first: python misc/demos/build_demos.py lipsync-coarticulation, then freeze 8 frames per pane"
    judge = memoized_judge(replay_only=False)
    for variant in strips:
        assert judge_legibility(strips[variant], LINE, judge=judge) is not None


def test_the_demo_flag_restores_itself():
    """The demo flips the module flag for one render and puts it back."""
    src = (ROOT / "misc/demos/build_demos.py").read_text(encoding="utf-8")
    assert "compile_mod.COARTICULATION_ENABLED = False" in src
    assert "compile_mod.COARTICULATION_ENABLED = original" in src
    assert json.dumps(compile_mod.COARTICULATION_ENABLED) == "true"
