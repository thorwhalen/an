"""Props: a rig whose art is not a person (an#108).

`AssetRef(kind="prop")` was accepted by the IR, errored by validate and
**raised** by the compiler. All three now agree it is legitimate — and
"agree" is the load-bearing word: a validator that passes a scene the
compiler refuses is worse than no validator, because it is trusted.

The fixture is `tests/fixtures/props/lamp/` — a two-state desk lamp with an
`asset_sets` channel named `lamp` carrying `off`/`on`. Two states are the
swap-channel machinery a character's viseme already uses, not a second
vocabulary, which is the whole reason a prop is not a new minimal document
with a `states` field.
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

import pytest

from an.adapters.cutout.compile import CutoutCompileError, compile_shot
from an.ir.schema import AssetRef, SetAction, Shot, StagePlacement
from an.ir.validate import validate_semantic
from an.props import PROP_DOCUMENT_KIND, PropDescriptor
from an.stores.props import PropsStore

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "props" / "lamp"


@pytest.fixture()
def mall(tmp_path):
    shutil.copytree(FIXTURE, tmp_path / "lamp")
    return {"props": PropsStore(tmp_path)}


def _shot(*, actions=(), stage=None, ref="lamp", duration=2.0) -> Shot:
    return Shot(
        id="s1",
        renderer="cutout",
        duration=duration,
        entities=[
            AssetRef(kind="prop", id="lamp", store="props", ref=ref, stage=stage)
        ],
        actions=list(actions),
    )


def _validate(shot, mall):
    """`validate_semantic` over one shot, WITH the stores.

    Without them the prop checks skip — deliberately, since "no store" and "no
    descriptor" are different facts — so a test that omits them asserts
    nothing.
    """
    from an.ir.schema import Meta, SceneIR

    return validate_semantic(
        SceneIR(meta=Meta(), timeline=[shot]),
        available_characters=mall.get("characters"),
        available_props=mall.get("props"),
    )


def _node(scene, name):
    def walk(n):
        if n.name == name:
            return n
        for c in n.children:
            found = walk(c)
            if found is not None:
                return found
        return None

    return walk(scene.scene)


# --- the three surfaces agree ------------------------------------------------


def test_a_prop_compiles_instead_of_raising(mall):
    """The refusal this replaces named the entity and told the author to delete
    it. Four tests pinned it; they invert here."""
    scene = compile_shot(_shot(), mall=mall, fps=24, strict_assets=True)
    assert _node(scene, "lamp") is not None
    assert _node(scene, "body") is not None


def test_validate_agrees_that_a_prop_is_renderable(mall):
    """`_check_renderable` predicts what compile refuses. It used to predict a
    refusal that no longer happens — which is the same defect as missing one,
    pointing the other way.

    Passing the STORE is the load-bearing half. The first version of this test
    passed none, so it asserted the absence of findings from a check that never
    ran — it would have passed identically if validate looked at nothing at
    all (an#108 review, H6).
    """
    report = _validate(_shot(), mall)
    assert report.passed, [f"[{f.severity}] {f.ir_path}: {f.description}" for f in report.findings]


def test_a_set_action_times_itself_with_at_not_start():
    """Guarding the trap this test suite fell into while being written.

    `SetAction` has `at`; the MARKDOWN vocabulary has `start`, which
    `an.ir.sync` turns into a `delay` wrapper. Actions are `extra="allow"`, so
    `SetAction(..., start=1.0)` is accepted, lands in `model_extra`, and
    compiles to a swap at t=0 — a silent no-op with the same shape as the
    an#106 renames. Pre-existing and out of scope for an#108, but pinned here
    so it is written down somewhere executable.
    """
    a = SetAction(target="x", property="p", value="v", at=1.0)
    assert a.at == 1.0 and not a.model_extra
    assert "start" not in SetAction.model_fields
    assert SetAction(target="x", property="p", value="v", start=1.0).model_extra == {
        "start": 1.0
    }


def test_the_drawable_kinds_are_exactly_what_the_compiler_dispatches_on():
    """Pinned against the compiler's own source, not against a copy of the list.

    validate's verdict IS compile's. A second hand-maintained vocabulary is
    how they drift, and the drift is silent in the direction that matters —
    validate passing a scene the render then refuses, after the author has
    paid for TTS and a browser launch.
    """
    import inspect

    from an.adapters.cutout import compile as compile_mod
    from an.ir.validate import _CONFIGURING_ENTITY_KINDS, _DRAWABLE_ENTITY_KINDS

    src = inspect.getsource(compile_mod._build_scene_root)
    dispatched = {
        kind
        for kind in ("character", "environment", "prop", "voice")
        if f'entity.kind == "{kind}"' in src
    }
    assert dispatched == set(_DRAWABLE_ENTITY_KINDS), dispatched ^ set(
        _DRAWABLE_ENTITY_KINDS
    )
    # `voice` is the one kind that is legitimately never dispatched: it
    # configures the render rather than appearing in it.
    assert "voice" in _CONFIGURING_ENTITY_KINDS and "voice" not in dispatched


# --- two states, through the machinery a viseme already uses -----------------


def test_a_two_state_prop_swaps_mid_shot(mall):
    """The done-when of an#108. `set lamp on` at t=1.0 is the same code path a
    character's `set mouth …` takes — `asset_sets` declares the channel, the
    compiler emits a step-interpolated swap channel, the runtime resolves it
    by name."""
    from an.adapters.cutout.timeline import evaluate_timeline, timeline_from_scene

    scene = compile_shot(
        _shot(actions=[SetAction(target="lamp/body", property="lamp", value="on", at=1.0)]),
        mall=mall,
        fps=24,
        strict_assets=True,
    )
    tl = timeline_from_scene(scene)
    before = evaluate_timeline(tl, 0.5).get(("lamp/body", "lamp"))
    after = evaluate_timeline(tl, 1.5).get(("lamp/body", "lamp"))
    assert after == "on", after

    # BEFORE the set there is no channel value, and that is correct rather than
    # a gap: the initial picture comes from the node the builder built, whose
    # `asset_id` is the slot's default attachment. Asserting `before == "off"`
    # would be asserting a rest keyframe nothing emits — and would pass just as
    # well if the sprite started blank, which is what actually goes wrong here.
    assert before is None, before
    body = _node(scene, "body")
    off_alias = scene.assets.textures[body.visual.asset_id].src
    assert off_alias.endswith("parts/off.svg"), off_alias

    # …and both drawings are registered, so the swap has its texture loaded
    # before the key changes rather than at the moment it does.
    srcs = sorted(a.src for a in scene.assets.textures.values())
    assert any(s.endswith("parts/off.svg") for s in srcs), srcs
    assert any(s.endswith("parts/on.svg") for s in srcs), srcs
    assert sorted(body.visual.asset_sets["lamp"]) == ["off", "on"]


def test_a_prop_s_art_is_addressed_under_the_props_prefix(mall):
    """`props/`, not `characters/` — the staging step resolves the prefix to a
    store, so the wrong one copies from the wrong directory."""
    scene = compile_shot(_shot(), mall=mall, fps=24, strict_assets=True)
    assert scene.assets.textures
    for asset in scene.assets.textures.values():
        assert asset.src.startswith("props/lamp/"), asset.src


def test_an_undeclared_swap_key_is_refused(mall):
    """The same loudness a character's swap gets (an#87): an unknown key used
    to leave the previous texture on screen."""
    with pytest.raises(CutoutCompileError, match="lamp"):
        compile_shot(
            _shot(actions=[SetAction(target="lamp/body", property="lamp", value="flickering")]),
            mall=mall,
            fps=24,
            strict_assets=True,
        )


# --- no placeholder, on purpose ---------------------------------------------


def test_an_unresolvable_prop_raises_rather_than_drawing_a_person(mall):
    """A character falls back to the built-in placeholder rig so a scene still
    renders. That reasoning inverts for a prop: the placeholder IS a humanoid,
    so the fallback meant to prevent an#33's wrong-art failure would CAUSE it
    — a person standing where the lamp should be, in a render that reports
    success."""
    with pytest.raises(CutoutCompileError, match="no such entry"):
        compile_shot(_shot(ref="chandelier"), mall=mall, fps=24, strict_assets=True)


def test_a_character_descriptor_in_the_props_store_is_refused(tmp_path):
    """Same rig fields, different defaults — and a `CharacterDescriptor` here
    would re-seed a seven-bone humanoid with a face and a blink from an empty
    list, which is precisely the thing a prop must not be."""
    store = PropsStore(tmp_path)
    store["lamp"] = {"kind": "CharacterDescriptor", "name": "lamp"}
    with pytest.raises(CutoutCompileError, match="kind='CharacterDescriptor'"):
        compile_shot(_shot(), mall={"props": store}, fps=24, strict_assets=True)


# --- placement ---------------------------------------------------------------


def test_stage_placement_puts_the_prop_where_it_says(mall):
    scene = compile_shot(
        _shot(stage=StagePlacement(at=(-120.0, 30.0), scale=0.5)),
        mall=mall,
        fps=24,
        strict_assets=True,
    )
    node = _node(scene, "lamp")
    assert (node.transform.x, node.transform.y) == (-120.0, 30.0)
    assert node.transform.scale_x == pytest.approx(0.5)


def test_an_unplaced_entity_is_byte_identical_to_before(mall):
    """`stage=None` is what every pre-an#108 document has, and it must compile
    to exactly what it compiled to before the field existed — which is why the
    contract hashes did not move. Neither placement branch runs."""
    a = compile_shot(_shot(), mall=mall, fps=24, strict_assets=True)
    b = compile_shot(_shot(stage=None), mall=mall, fps=24, strict_assets=True)
    assert a.model_dump_json() == b.model_dump_json()


def test_placement_replaces_the_layout_rather_than_offsetting_it():
    """A placed character's x must not depend on how many others are in the
    shot: offsetting would move an explicitly placed entity when a third one
    is added, and placement that moves is not placement."""
    from an.adapters.cutout.compile import _apply_stage_placement
    from an.adapters.cutout.serialize import NodeJSON, TransformJSON

    node = NodeJSON(name="x", transform=TransformJSON(x=333.0, y=7.0))
    _apply_stage_placement(
        node, AssetRef(kind="prop", id="p", store="props", ref="p", stage=StagePlacement(at=(10.0, 20.0)))
    )
    assert (node.transform.x, node.transform.y) == (10.0, 20.0)


# --- the descriptor is not a character ---------------------------------------


def test_a_prop_descriptor_is_not_seeded_with_a_person():
    """`CharacterDescriptor(name="sword")` is a seven-bone humanoid with a face
    and a blink, re-seeded from an empty list by `model_post_init`. That is the
    measured reason a prop is its own document rather than `kind: "prop"` on a
    character."""
    from an.characters.schema import CharacterDescriptor

    person = CharacterDescriptor(name="sword")
    assert len(person.bones) > 5 and person.animations and person.asset_sets

    prop = PropDescriptor(name="sword")
    assert [b.name for b in prop.bones] == ["root"]
    assert [s.name for s in prop.slots] == ["body"]
    assert prop.animations == {} and prop.asset_sets == {} and prop.skins == {}


def test_the_prop_document_kind_is_registered_separately():
    """Keyed per kind, because two documents both at `0.1.0` that migrate
    differently is the collision an#77 fixed."""
    from an.ir.migrate import KINDS

    assert KINDS[PROP_DOCUMENT_KIND.name] is PROP_DOCUMENT_KIND
    assert PROP_DOCUMENT_KIND.version_field == "schema_version"
    assert PROP_DOCUMENT_KIND.name != "CharacterDescriptor"


def test_a_markdown_set_with_start_is_refused_rather_than_dropped():
    """`start` on a `set` did nothing, silently — found while authoring the
    corpus fixture for this very PR.

    The parser pops `start` for tween/play/expression and wraps them in
    `sequence(delay(start), action)`. A `set` is instantaneous, so its time is
    `at` — and the `set` branch reads `at` alone, so `{kind: set, start: 1.0}`
    compiled to a swap at t=0. The author sees a lamp lit from the first frame
    and no message anywhere. Verified in real frames before and after.

    A REFUSAL rather than an alias: `at` and `start` would be two names for
    one number, which is what this wave keeps removing.
    """
    from an.ir.sync import SceneMarkdownError, markdown_to_ir

    md = """# X

```yaml meta
title: X
duration: 2
fps: 24
default_renderer: cutout
```

## Shot s1 (cutout)

```yaml shot
duration: 2
```

```yaml actions
- kind: set
  target: lamp/body
  property: lamp
  value: 'on'
  start: 1.0
```
"""
    with pytest.raises(SceneMarkdownError, match=r"`at: 1\.0`"):
        markdown_to_ir(md)

    # …and `at:` is accepted, at the time it names.
    scene = markdown_to_ir(md.replace("  start: 1.0", "  at: 1.0"))
    assert scene.timeline[0].actions[0].at == 1.0


def test_the_markdown_round_trip_keeps_a_set_s_time():
    """md -> json -> md must not lose `at`. It didn't — but the same
    round-trip DID erase the timing when the parser silently dropped `start`,
    which is how a fixture ends up lit from frame 0 with a scene.md that says
    otherwise."""
    from an.ir.sync import ir_to_markdown, markdown_to_ir

    md = """# X

```yaml meta
title: X
duration: 2
fps: 24
default_renderer: cutout
```

## Shot s1 (cutout)

```yaml shot
duration: 2
```

```yaml actions
- kind: set
  target: lamp/body
  property: lamp
  value: 'on'
  at: 1.25
```
"""
    once = markdown_to_ir(md)
    twice = markdown_to_ir(ir_to_markdown(once))
    assert twice.timeline[0].actions[0].at == 1.25


# --- validate's verdict IS compile's, on the prop path -----------------------


def _compiles(shot, mall) -> bool:
    try:
        compile_shot(shot, mall=mall, fps=24, strict_assets=True)
        return True
    except CutoutCompileError:
        return False


@pytest.mark.parametrize(
    "name,make",
    [
        ("resolves", lambda: _shot()),
        ("missing ref", lambda: _shot(ref="chandelier")),
        ("undeclared key", lambda: _shot(actions=[SetAction(target="lamp/body", property="lamp", value="flickering")])),
        ("declared key", lambda: _shot(actions=[SetAction(target="lamp/body", property="lamp", value="on", at=1.0)])),
        ("unknown channel", lambda: _shot(actions=[SetAction(target="lamp/body", property="glow", value="on")])),
    ],
)
def test_validate_and_compile_reach_the_same_verdict(mall, name, make):
    """The claim, tested as a claim.

    `test_the_drawable_kinds_are_exactly_what_the_compiler_dispatches_on` pins
    which KINDS are dispatched. It says nothing about descriptor resolution,
    which is where the two actually disagreed: a prop ref missing from the
    store, and a `CharacterDescriptor` sitting in the props store, both passed
    validate and raised at compile (an#108 review, H1/H2). The harsher outcome
    had the weaker prediction — a prop raises where a character falls back —
    which is the exact inversion of what a pre-flight is for.
    """
    shot = make()
    assert _validate(shot, mall).passed is _compiles(shot, mall), name


def test_a_wrong_kind_document_in_the_props_store_is_an_ERROR_at_validate(tmp_path):
    """Not merely "no descriptor": a `CharacterDescriptor` under `assets/props/`
    is reported by name, because the fix is to move or retype the file rather
    than to write one."""
    store = PropsStore(tmp_path)
    store["lamp"] = {"kind": "CharacterDescriptor", "name": "lamp"}
    report = _validate(_shot(), {"props": store})
    (finding,) = [f for f in report.findings if f.severity == "error"]
    assert "PropDescriptor" in finding.description and "'props'" in finding.description


def test_a_declared_key_whose_ART_is_missing_is_caught_at_validate(mall, tmp_path):
    """The compiler registers only attachments whose files resolve, then
    refuses a key whose art is gone. Validate read the DECLARED set, so a key
    that cannot be drawn passed the free pre-flight and raised after the
    author had paid for a browser launch — with `strict_assets` either way
    (an#108 review, H3)."""
    import os

    os.remove(Path(mall["props"]._root) / "lamp" / "parts" / "on.svg")
    shot = _shot(actions=[SetAction(target="lamp/body", property="lamp", value="on", at=0.5)])
    report = _validate(shot, mall)
    assert not report.passed
    assert any("'off'" in f.description for f in report.findings), [
        f.description for f in report.findings
    ]
    assert not _compiles(shot, mall)


def test_a_store_that_was_not_supplied_skips_its_checks_rather_than_failing_them(mall):
    """"No store" and "no descriptor" are different facts.

    an#108's first pass changed the gate from "characters store absent → skip"
    to "no stores at all → skip", which made
    `validate_semantic(scene, available_characters=X)` — the signature every
    caller outside this repo has — report EVERY prop swap as
    "has no descriptor declaring asset sets" on a scene that compiles fine
    (an#108 review, H5).
    """
    from an.ir.schema import Meta, SceneIR

    shot = _shot(actions=[SetAction(target="lamp/body", property="lamp", value="on", at=0.5)])
    scene = SceneIR(meta=Meta(), timeline=[shot])
    assert validate_semantic(scene, available_characters={}).passed
    assert validate_semantic(scene).passed
    # …and supplying it does check.
    assert _validate(_shot(ref="ghost"), mall).passed is False


def test_an_expression_on_a_prop_is_an_error_even_though_it_compiles(mall):
    """A DELIBERATE divergence, written down so it is not mistaken for the
    other kind.

    `_check_renderable`'s rule is "error wherever the pipeline raises". This is
    the other rule: an `expression` targeting a prop compiles to NOTHING — the
    face solver skips a non-character — and a silent no-op is what
    `test_loud_discards.py` exists to prevent. So validate errors on a scene
    that renders, on purpose, and says why: "it would compile to nothing".
    """
    from an.ir.compose import expression

    shot = _shot(actions=[expression("lamp", preset="happy", duration=0.5)])
    report = _validate(shot, mall)
    assert not report.passed
    assert any("compile to nothing" in f.description for f in report.findings), [
        f.description for f in report.findings
    ]
    assert _compiles(shot, mall), "and it does compile — that is the divergence"


def test_stage_placement_refuses_a_value_that_cannot_survive_a_round_trip():
    """`inf` and `nan` serialize to JSON `null`, and re-validating that raises.
    A scene file written with either is corrupt one way, and the author would
    find out on the next load rather than at the edit that did it."""
    import pydantic

    for kwargs in ({"scale": float("inf")}, {"at": (float("nan"), 0.0)}, {"at": (float("inf"), 0.0)}):
        with pytest.raises(pydantic.ValidationError):
            StagePlacement(**kwargs)
    # `gt=0` already covered these; pinned so the guard is not "loosened back".
    for kwargs in ({"scale": 0.0}, {"scale": -1.0}):
        with pytest.raises(pydantic.ValidationError):
            StagePlacement(**kwargs)
