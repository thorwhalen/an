"""`step_hz`: opt-in stepped timing for authored tweens (an#89, Wave 5 PR-E).

Typed fields `Meta.step_hz` / `Shot.step_hz` (shot overrides scene; None =
smooth), carried through `scene.md` in BOTH directions, validated
`0 < step_hz <= fps`, and applied at compile by resampling each tween's curve
onto a scene-wide pose grid of step-eased keyframes. Everything that is not an
authored tween — the camera, compiled blinks, `play` clips, swap channels — is
exempt by construction: they are separate emission sites, not string-sniffed.

The knob is stamped into `meta.step_hz` ONLY when set, so the compiled
document of a scene that never turned it on is byte-identical to before the
field existed — which is what keeps every committed ledger row's
`scene_contract_sha256` comparable.
"""

from __future__ import annotations

import math
from pathlib import Path

import pytest

from an.adapters.cutout.channel import Channel, Keyframe, evaluate
from an.adapters.cutout.compile import compile_shot, step_times
from an.adapters.cutout.serialize import from_dict, to_dict
from an.bench.contract import scene_contract_sha256
from an.ir.compose import delay, sequence, tween
from an.ir.schema import AssetRef, Camera, Meta, SceneIR, Shot, TweenAction
from an.ir.sync import ir_to_markdown, markdown_to_ir
from an.ir.validate import validate_semantic

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "characters"


def _shot(actions=(), *, duration=2.0, camera=None, step_hz=None, entity="c"):
    return Shot(
        id="s1",
        renderer="cutout",
        duration=duration,
        camera=camera,
        step_hz=step_hz,
        entities=[
            AssetRef(kind="character", id=entity, store="characters", ref=entity)
        ],
        actions=list(actions),
    )


def _compile(shot, **kw):
    with _quiet():
        return compile_shot(shot, mall={"characters": {}}, **kw)


class _quiet:
    """Swallow the placeholder-rig warning; the stand-in is the point here."""

    def __enter__(self):
        import warnings

        self._ctx = warnings.catch_warnings()
        self._ctx.__enter__()
        warnings.simplefilter("ignore")
        return self

    def __exit__(self, *exc):
        return self._ctx.__exit__(*exc)


def _tween_channel(scene, ordinal=0):
    (ch,) = scene.animations[f"__tween__{ordinal}"].channels
    return ch


# --------------------------------------------------------------- the fields


def test_the_fields_round_trip_through_scene_md_in_both_directions():
    """The writer hand-enumerates meta fields and the reader whitelists shot
    keys, so a field missing from EITHER silently drops (research §10)."""
    scene = SceneIR(
        meta=Meta(title="t", duration=2.0, fps=30, step_hz=15.0),
        timeline=[_shot(step_hz=10.0)],
    )
    md = ir_to_markdown(scene)
    assert "step_hz: 15.0" in md.split("```yaml meta", 1)[1].split("```", 1)[0]
    assert "step_hz: 10.0" in md.split("```yaml shot", 1)[1].split("```", 1)[0]
    back = markdown_to_ir(md)
    assert back.meta.step_hz == 15.0
    assert back.timeline[0].step_hz == 10.0


def test_unset_step_hz_is_absent_from_scene_md_and_reads_back_as_none():
    scene = SceneIR(meta=Meta(title="t", duration=2.0), timeline=[_shot()])
    md = ir_to_markdown(scene)
    assert "step_hz" not in md
    back = markdown_to_ir(md)
    assert back.meta.step_hz is None and back.timeline[0].step_hz is None


@pytest.mark.parametrize("bad", [31.0, 1e9])
def test_validate_refuses_a_rate_above_fps(bad):
    scene = SceneIR(meta=Meta(title="t", duration=2.0, fps=30, step_hz=bad), timeline=[_shot()])
    report = validate_semantic(scene)
    assert not report.passed
    assert any(f.ir_path == "meta/step_hz" and "on twos" in f.description for f in report.findings)
    per_shot = SceneIR(meta=Meta(title="t", duration=2.0, fps=30), timeline=[_shot(step_hz=bad)])
    report = validate_semantic(per_shot)
    assert any(f.ir_path == "timeline/0/step_hz" for f in report.findings if f.severity == "error")


@pytest.mark.parametrize("bad", [0.0, -5.0])
def test_the_schema_refuses_a_non_positive_rate(bad):
    """`Field(gt=0)`: a scene declaring `step_hz: -5` fails at LOAD, before any
    path that skips validate can reach the compiler."""
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        Meta(title="t", step_hz=bad)
    with pytest.raises(ValidationError):
        Shot(id="s", step_hz=bad)


@pytest.mark.parametrize("bad", [0.0, -5.0, 31.0, float("nan"), float("inf")])
def test_the_compiler_refuses_the_same_range_because_a_render_never_validates(bad):
    """`an render --step-hz -5` bypasses both the schema (the value never
    touches the scene) and validate (a render does not run it). Before this
    guard, a non-positive rate spun `step_times` forever — a hang in a worker
    thread, not an error (an#89 review)."""
    from an.adapters.cutout.compile import CutoutCompileError

    shot = _shot([tween("c", "x", to=1.0, duration=1.0)])
    with pytest.raises(CutoutCompileError, match="step_hz"):
        compile_shot(shot, mall={"characters": {}}, fps=30, step_hz=bad)
    with pytest.raises(ValueError):
        step_times(0.0, 1.0, bad if bad <= 0 else -1.0)


def test_the_compiler_checks_against_the_fps_it_compiles_with():
    """Validate compares with `meta.fps`, but `render_project(fps=12)` compiles
    with 12: a 15 Hz grid the scene called fine is finer than those frames."""
    from an.adapters.cutout.compile import CutoutCompileError

    shot = _shot([tween("c", "x", to=1.0, duration=1.0)])
    assert len(_tween_channel(_compile(shot, fps=30, step_hz=15.0)).keyframes) == 16
    with pytest.raises(CutoutCompileError, match="step_hz"):
        compile_shot(shot, mall={"characters": {}}, fps=12, step_hz=15.0)


def test_a_non_positive_fps_is_reported_once_not_as_a_step_hz_riddle():
    scene = SceneIR(meta=Meta(title="t", duration=1.0, fps=0, step_hz=15.0), timeline=[_shot(step_hz=15.0)])
    paths = [f.ir_path for f in validate_semantic(scene).findings if f.severity == "error"]
    assert "meta/fps" in paths and not any(p.endswith("step_hz") for p in paths)


@pytest.mark.parametrize("prop", sorted(__import__("an.adapters.cutout.compile", fromlist=["_PROPERTY_REST_VALUES"])._PROPERTY_REST_VALUES))
def test_every_transform_property_is_stepped_not_just_x(prop):
    """A mutant exempting `alpha` from stepping survived a suite that only ever
    stepped `x` (an#89 review)."""
    shot = _shot([tween("c", prop, to=0.5, duration=1.0)])
    kfs = _tween_channel(_compile(shot, step_hz=10.0)).keyframes
    assert len(kfs) == 11 and all(k.easing == "step" for k in kfs)
    assert all(isinstance(k.value, float) for k in kfs)


@pytest.mark.parametrize("good", [None, 30.0, 15.0, 10.0, 0.5])
def test_validate_accepts_none_and_rates_up_to_fps(good):
    scene = SceneIR(meta=Meta(title="t", duration=2.0, fps=30, step_hz=good), timeline=[_shot(step_hz=good)])
    assert validate_semantic(scene).passed


# --------------------------------------------------------------- the grid


def test_step_times_are_a_shot_wide_grid_not_the_tweens_own():
    """A tween starting at 0.05 s under a 10 Hz grid updates at 0.1, 0.2 ... on
    the SHOT's clock (local 0.05, 0.15 ...), plus its own start and end. Shots
    compile independently, so the grid is per shot and restarts at a cut."""
    assert step_times(0.0, 0.3, 10) == [0.0, 0.1, 0.2, 0.3]
    assert [round(t, 6) for t in step_times(0.05, 0.3, 10)] == [0.0, 0.05, 0.15, 0.25, 0.3]
    # Aligned start: the grid point AT the start is the start, not a duplicate.
    assert step_times(0.2, 0.3, 10) == [0.0, pytest.approx(0.1), pytest.approx(0.2), 0.3]
    # Shorter than one step: a single step to the end value.
    assert step_times(0.0, 0.02, 10) == [0.0, 0.02]


# --------------------------------------------------------------- the compiler


def test_off_leaves_the_compiled_document_and_its_contract_hash_untouched():
    """The knob's absence must cost nothing — not one keyframe, not one byte of
    the document the ledger hashes. `meta.step_hz` is serialized only when set."""
    shot = _shot([tween("c", "x", to=100.0, duration=1.0)])
    doc = to_dict(_compile(shot))
    assert "step_hz" not in doc["meta"]
    assert from_dict(doc).meta.step_hz is None
    assert len(_tween_channel(from_dict(doc)).keyframes) == 2
    # The document carries no trace of the knob, so its contract hash is the
    # pre-#89 one. (An earlier draft compared the hash against itself —
    # `step_hz=None` IS the default — which asserted nothing; the key-absence
    # check above is what goes red when the wrap serializer is removed, and
    # the six committed ledger rows' hashes were verified equal at landing.)
    assert scene_contract_sha256(doc) == scene_contract_sha256(
        {**doc, "meta": {k: v for k, v in doc["meta"].items() if k != "step_hz"}}
    )


def test_on_resamples_each_tween_onto_step_eased_keyframes_of_the_smooth_curve():
    """Every grid keyframe carries the value the SMOOTH tween would show at that
    instant (evaluated through the Python spec) and step easing, so the pose
    holds until the next grid point. The end value lands exactly at the end."""
    shot = _shot([tween("c", "x", to=100.0, duration=1.0, easing="ease_in_out")])
    scene = _compile(shot, step_hz=15.0)
    assert scene.meta.step_hz == 15.0
    assert "step_hz" in to_dict(scene)["meta"]
    kfs = _tween_channel(scene).keyframes
    assert [k.easing for k in kfs] == ["step"] * len(kfs)
    assert [round(k.time, 6) for k in kfs] == [round(i / 15, 6) for i in range(15)] + [1.0]
    smooth = Channel("c", "x", [Keyframe(0.0, 0.0, "ease_in_out"), Keyframe(1.0, 100.0)])
    for k in kfs:
        assert k.value == pytest.approx(evaluate(smooth, k.time), abs=1e-12)
    assert kfs[0].value == 0.0 and kfs[-1].value == 100.0
    # The grid keyframe is strictly between from and to on an S-curve.
    assert 0.0 < kfs[7].value < 100.0


def test_a_delayed_tween_snaps_to_the_shot_grid():
    """`sequence(delay(0.05), tween)` under 10 Hz: updates at shot times 0.1,
    0.2 ... — clip-local 0.05, 0.15 ... — because "on twos" is a property of
    the frames, not of each tween's private clock."""
    shot = _shot([sequence(delay(0.05), tween("c", "x", to=10.0, duration=0.3))])
    kfs = _tween_channel(_compile(shot, step_hz=10.0)).keyframes
    assert [round(k.time, 6) for k in kfs] == [0.0, 0.05, 0.15, 0.25, 0.3]


def test_the_camera_is_exempt_by_construction():
    """`_add_camera_clips` is its own emission site, so the camera's scale tween
    stays a two-keyframe eased curve under any step_hz — a stepped character
    under a stepped camera would judder the whole frame."""
    shot = _shot([tween("c", "x", to=10.0, duration=1.0)], camera=Camera(move="push_in"))
    scene = _compile(shot, step_hz=10.0)
    camera = next(a for aid, a in scene.animations.items() if aid.startswith("__camera__"))
    (ch,) = camera.channels
    assert len(ch.keyframes) == 2
    assert ch.keyframes[0].easing != "step"
    assert len(_tween_channel(scene).keyframes) > 2


def test_swap_tweens_blinks_and_plays_are_not_resampled(tmp_path):
    """Swap channels are stepped by format already (their two keyframes stay
    two); blink clips and `play` clips are separate emission sites."""
    import shutil

    from an.ir.compose import play
    from an.stores.characters import CharactersStore

    shutil.copytree(FIXTURES / "gale", tmp_path / "gale")
    store = CharactersStore(tmp_path)
    # 6 s, not 3: gale's first compiled blink lands at ~3.4 s, so a 3 s shot
    # emits NO blink clip and the exemption below was vacuous for blinks
    # (an#89 review). The presence assertion keeps it from going vacuous again.
    shot = Shot(
        id="s",
        renderer="cutout",
        duration=6.0,
        entities=[AssetRef(kind="character", id="gale", store="characters", ref="gale")],
        actions=[
            tween("gale/left_hand", "hands", to="palm", duration=1.0, from_="fist"),
            play("gale", "idle_breath", duration=3.0),
        ],
    )
    on = compile_shot(shot, mall={"characters": store}, step_hz=10.0, fps=24)
    off = compile_shot(shot, mall={"characters": store}, fps=24)
    kinds = {aid.split("__")[1] for aid in on.animations}
    assert {"blink", "play", "tween"} <= kinds, kinds
    for aid in on.animations:
        if aid.startswith("__tween__"):
            assert len(on.animations[aid].channels[0].keyframes) == 2  # swap: untouched
        else:
            assert to_dict(on.animations[aid]) == to_dict(off.animations[aid]), aid


def test_a_tween_shorter_than_one_step_is_a_single_step_to_its_end():
    shot = _shot([tween("c", "x", to=10.0, duration=0.02)])
    kfs = _tween_channel(_compile(shot, step_hz=10.0)).keyframes
    assert [(k.time, k.value) for k in kfs] == [(0.0, 0.0), (0.02, 10.0)]


# --------------------------------------------------------------- the renderer


def test_the_shot_overrides_the_scene_and_the_scene_the_default():
    from an.adapters._base import RenderContext
    from an.adapters.cutout.render import effective_step_hz
    from an.ir.schema import resolve_step_hz

    # One rule, stated once in the IR layer; the renderer and preview call it.
    assert resolve_step_hz(Shot(id="s", step_hz=10.0), 15.0) == 10.0
    assert resolve_step_hz(Shot(id="s"), 15.0) == 15.0

    ctx = RenderContext(mall={}, work_dir=Path("."), step_hz=15.0)
    assert effective_step_hz(Shot(id="s"), ctx) == 15.0
    assert effective_step_hz(Shot(id="s", step_hz=10.0), ctx) == 10.0
    assert effective_step_hz(Shot(id="s"), RenderContext(mall={}, work_dir=Path("."))) is None


def test_render_project_passes_the_scene_declaration_unless_overridden(monkeypatch, tmp_path):
    """`an.render.render` builds the context from `meta.step_hz`; an explicit
    argument wins. Captured at the RenderContext, before any browser."""
    from an import init
    from an import render as render_mod
    from an.project import load

    root = init(tmp_path / "p")
    proj = load(root)
    proj.scene = SceneIR(meta=Meta(title="t", duration=1.0, fps=12, step_hz=6.0), timeline=[_shot(duration=1.0)])
    proj.mall["scenes"]["main"] = proj.scene

    seen = []

    class _Stop(Exception):
        pass

    real = render_mod.RenderContext

    def capture(**kw):
        seen.append(kw.get("step_hz"))
        raise _Stop

    monkeypatch.setattr(render_mod, "RenderContext", capture)
    for override in (None, 3.0):
        with pytest.raises(_Stop):
            render_mod.render(proj, auto_audio=False, step_hz=override)
    assert seen == [6.0, 3.0]
    assert real is not capture


def test_the_cli_maps_zero_to_the_scene_declaration(monkeypatch):
    from an import tools

    seen = {}

    def fake(project_dir, **kw):
        seen.update(kw)
        return Path("out.mp4")

    monkeypatch.setattr(tools, "_render_project", fake)
    tools.render("proj")
    assert seen["step_hz"] is None
    tools.render("proj", step_hz=15.0)
    assert seen["step_hz"] == 15.0


def test_the_bench_row_records_the_policy_per_shot():
    """Additive scene provenance, like `blink_phases`: the compiled meta's
    `step_hz` (None when smooth) per shot, so a stepped row says so. Asserted
    on the function the row is built from, with stand-in captures — an earlier
    draft grepped `run.py`'s source text, which a renamed key passed."""
    from types import SimpleNamespace as NS

    from an.bench.run import shot_policy_provenance

    stepped = _compile(_shot([tween("c", "x", to=1.0, duration=1.0)]), step_hz=12.0)
    smooth = _compile(_shot([tween("c", "x", to=1.0, duration=1.0)]))
    prov = shot_policy_provenance(
        [NS(shot_id="a", scene_json=to_dict(stepped)), NS(shot_id="b", scene_json=to_dict(smooth))]
    )
    assert prov["step_hz"] == {"a": 12.0, "b": None}
    assert set(prov["blink_phases"]) == {"a", "b"}


def test_twos_and_threes_are_what_the_docs_say_they_are():
    """At 30 fps, 15 Hz holds each pose for two frames and 10 Hz for three —
    the vocabulary the practice uses (research §10)."""
    fps = 30
    for hz, frames_per_pose in ((15.0, 2), (10.0, 3)):
        shot = _shot([tween("c", "x", to=30.0, duration=1.0, easing="linear")], duration=1.0)
        kfs = _tween_channel(_compile(shot, step_hz=hz, fps=fps)).keyframes
        gaps = {round((b.time - a.time) * fps) for a, b in zip(kfs, kfs[1:])}
        assert gaps == {frames_per_pose}, (hz, gaps)
        assert math.isclose(kfs[-1].time, 1.0)
