"""The render path's own failure modes: bounded, and typed (an#79).

Two holes, found while researching Wave 4 and fixed independently of it
(`misc/docs/wave4_research.md` §8):

1. **`anLoadScene` had no deadline.** A part SVG that is `<svg/>`, malformed, or
   zero-dimension makes `PIXI.Assets.load` never settle. `page.evaluate` awaits a
   returned promise with no timeout of its own, so the render hung indefinitely —
   measured past 120 s, with no error, no output and nothing to diagnose from.
2. **Three `page.evaluate` calls were unwrapped**, so the single most likely art
   failure in the product surfaced as
   ``TypeError: Cannot read properties of undefined (reading 'x')`` from a
   minified bundle, naming neither the shot nor the part.

The unit tests here need no browser. The one that proves the hang is actually
bounded does, and is marked accordingly — never claim this is "verified in CI"
without saying which lane.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from an.adapters._base import RenderContext
from an.adapters.cutout.render import (
    ASSET_LOAD_TIMEOUT_MARKER,
    DEFAULT_ASSET_LOAD_TIMEOUT_MS,
    CutoutRenderError,
    _evaluate,
    _LOAD_SCENE_JS,
)
from an.ir.schema import AssetRef, Shot


class _CharacterStore(dict):
    """A mall store: dict-like for the compiler, `_root` for the stager."""

    def __init__(self, mapping, root: Path):
        super().__init__(mapping)
        self._root = root


class _Boom:
    """A page whose `evaluate` fails the way Playwright's does."""

    def __init__(self, message: str):
        self._message = message

    def evaluate(self, expression, *args):
        raise RuntimeError(self._message)


class _Echo:
    def evaluate(self, expression, *args):
        return ("ok", expression, args)


def test_a_working_evaluate_is_passed_through_untouched():
    assert _evaluate(_Echo(), "() => 1", 7, doing="counting") == (
        "ok",
        "() => 1",
        (7,),
    )


def test_a_js_failure_becomes_a_typed_error_naming_what_we_were_doing():
    """The convention: subprocess/third-party failures are wrapped at the facade."""
    with pytest.raises(CutoutRenderError) as excinfo:
        _evaluate(
            _Boom("TypeError: Cannot read properties of undefined (reading 'x')"),
            "() => window.anLoadScene()",
            doing="loading the scene for shot 's1'",
        )
    message = str(excinfo.value)
    assert "loading the scene for shot 's1'" in message
    # The JS message is the informative half and must survive verbatim.
    assert "Cannot read properties of undefined" in message
    assert excinfo.value.__cause__ is not None


def test_the_hint_is_attached_when_one_is_offered():
    with pytest.raises(CutoutRenderError) as excinfo:
        _evaluate(
            _Boom("nope"),
            "()=>0",
            doing="loading",
            hint="Check the textures this shot declares.",
        )
    assert "Check the textures this shot declares." in str(excinfo.value)


def test_a_timeout_is_reported_as_a_timeout_not_as_a_load_failure():
    """The two causes need different advice, so they must not share a message.

    A load *failure* means the art is wrong. A load that never *settles* means
    the art is degenerate — and the remedy ("raise the deadline") is only ever
    right for the second, so conflating them sends the reader the wrong way.
    """
    with pytest.raises(CutoutRenderError) as excinfo:
        _evaluate(
            _Boom(f"Error: {ASSET_LOAD_TIMEOUT_MARKER}"),
            "()=>0",
            doing="loading the scene",
        )
    message = str(excinfo.value)
    assert "timed out" in message
    assert str(DEFAULT_ASSET_LOAD_TIMEOUT_MS) in message
    assert "never settled" in message
    assert "zero-dimension" in message


def test_the_load_expression_races_a_deadline_and_clears_its_timer():
    """Guards the shape of the JS, which no Python test can otherwise reach.

    `Promise.race` is the mechanism; `clearTimeout` in a `finally` is what stops
    a successful load from leaving a pending timer that keeps the page busy.
    """
    assert "Promise.race" in _LOAD_SCENE_JS
    assert "clearTimeout" in _LOAD_SCENE_JS
    assert "finally" in _LOAD_SCENE_JS
    assert ASSET_LOAD_TIMEOUT_MARKER in _LOAD_SCENE_JS
    assert "args.timeoutMs" in _LOAD_SCENE_JS


def test_the_deadline_is_read_at_call_time_so_it_can_be_lowered_for_a_test():
    """A constant baked in at import would make the browser test below take a
    minute. Reading the module attribute is also how the bench levers work."""
    import an.adapters.cutout.render as render_mod

    source = Path(render_mod.__file__).read_text(encoding="utf-8")
    assert '"timeoutMs": DEFAULT_ASSET_LOAD_TIMEOUT_MS' in source


@pytest.mark.browser
@pytest.mark.ffmpeg
def test_a_degenerate_part_svg_raises_instead_of_hanging(tmp_path, monkeypatch):
    """The regression this fix exists for, against a real browser.

    Before an#79 this call never returned. The assertion is therefore as much
    about *finishing* as about the message: the elapsed time is the witness that
    the deadline fired.

    Note the parts must **exist** and be degenerate. An *absent* part is a
    different failure (it fails the load outright rather than never settling),
    so a fixture that simply omits them would test the wrong hole.
    """
    import an.adapters.cutout.render as render_mod
    from an.characters.schema import MOUTH_SHAPES

    # Well below a legitimate load, so a pass is fast and a regression shows up
    # as a hang rather than as a minute of waiting.
    monkeypatch.setattr(render_mod, "DEFAULT_ASSET_LOAD_TIMEOUT_MS", 3_000)

    root = tmp_path / "characters"
    parts = root / "wedge-v1" / "parts"
    (parts / "mouth").mkdir(parents=True)
    body = (
        "head",
        "torso",
        "arm_l",
        "arm_r",
        "leg_l",
        "leg_r",
        "eye_l_open",
        "eye_r_open",
        "brow_l",
        "brow_r",
    )
    # The exact artefact of a half-finished export, and a shape the repo already
    # writes as a fixture elsewhere.
    for name in body:
        (parts / f"{name}.svg").write_text("<svg/>", encoding="utf-8")
    for shape in MOUTH_SHAPES:
        (parts / "mouth" / f"mouth_{shape}.svg").write_text("<svg/>", encoding="utf-8")

    shot = Shot(
        id="s1",
        renderer="cutout",
        duration=0.25,
        entities=[
            AssetRef(kind="character", id="c", store="characters", ref="wedge-v1")
        ],
    )
    characters = _CharacterStore(
        {"wedge-v1": {"kind": "CharacterDescriptor", "name": "wedge"}}, root
    )
    with tempfile.TemporaryDirectory() as work:
        ctx = RenderContext(
            mall={"characters": characters},
            work_dir=Path(work),
            fps=4,
            resolution=(64, 48),
        )
        with pytest.raises(CutoutRenderError) as excinfo:
            render_mod.CutoutRenderer().render(shot, ctx)

    message = str(excinfo.value)
    assert "timed out" in message
    assert "never settled" in message
