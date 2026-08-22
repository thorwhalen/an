"""Cutout runtime files: presence + helpers."""

from __future__ import annotations

from an.adapters.cutout.runtime_files import (
    runtime_dir,
    runtime_index_html,
    runtime_js,
)


def test_runtime_dir_exists():
    p = runtime_dir()
    assert p.is_dir()


def test_index_html_present_and_loads_runtime_js():
    p = runtime_index_html()
    assert p.is_file()
    text = p.read_text(encoding="utf-8")
    assert "runtime.js" in text
    assert "<canvas" in text


def test_runtime_js_present_with_public_api():
    p = runtime_js()
    assert p.is_file()
    text = p.read_text(encoding="utf-8")
    # The four documented globals
    for fn in ("anLoadScene", "anSetTime", "anCanvasReady", "anRuntimeVersion"):
        assert fn in text, f"runtime.js missing {fn!r}"


def test_load_scene_installs_a_fresh_canvas_on_reload():
    """Reloading a scene must put a NEW <canvas id="stage"> in the document (an#6).

    A static check because the failure it guards is *silent*: `app.destroy(true, …)`
    detaches the canvas, the next `getElementById('stage')` returns null, and PixiJS —
    given `view: null` — quietly creates its own orphan canvas. Nothing throws;
    `an preview` just goes blank on the first hot reload and never recovers.

    Note the invariant is "a fresh canvas is installed", NOT "removeView is false".
    Merely keeping the old element does not work either: its WebGL context dies with
    the renderer and cannot be re-acquired, so the next PIXI.Application on the same
    canvas fails outright. Both halves are needed.

    The behavioural test is test_preview_reload.py, but it needs a browser and skips
    wherever playwright is absent — including CI. This one always runs.
    """
    text = runtime_js().read_text(encoding="utf-8")
    assert "createElement('canvas')" in text, (
        "a reload must install a fresh canvas; reusing the old one gets a dead WebGL "
        "context, and not replacing it at all leaves PixiJS rendering into an orphan"
    )
    assert "fresh.id = 'stage'" in text, "the replacement must keep the 'stage' id"


#: CSS declarations that stop Chromium compositing `#stage`. Each either makes
#: Playwright's element screenshot time out (it waits for visibility) or makes
#: it capture a blank frame (it reads the compositor) — an#57, measured.
UNCOMPOSITED_SPELLINGS: tuple[str, ...] = (
    "display:none",
    "visibility:hidden",
    "content-visibility:hidden",
    "opacity:0",
)


def _stage_rule_bodies(html: str) -> list[str]:
    """Every `#stage { … }` rule body in a `<style>` block, whitespace stripped.

    A **rule** parse rather than a line scan, and that is the whole point of the
    helper existing: the obvious line-scoped version passes a mutation spelled
    across three lines, which is not a guard. Stripping whitespace also means
    `display : none` and `display:none` are the same string to the caller.
    """
    import re

    bodies = []
    for style in re.findall(r"<style>(.*?)</style>", html, re.S):
        flat = re.sub(r"\s+", "", style)
        bodies += re.findall(r"#stage\{([^}]*)\}", flat)
    return bodies


def test_the_capture_page_never_stops_compositing_the_stage_canvas():
    """MUTATION: set `#stage { display: none; }` in index.html — proven inline below.

    an#57 proposed exactly that, on the premise that `canvas.screenshot()` does
    not need the element composited. **It does.** `_capture_frames` calls
    `page.locator('#stage').screenshot(...)`, and Playwright implements an
    element screenshot as a **page capture clipped to the element's document
    rect**: it first awaits visibility, then reads the compositor. So
    `display:none` / `visibility:hidden` make it time out, and the two spellings
    Playwright *does* accept — `opacity:0` and off-screen positioning — return
    an all-white frame.

    Both failure modes are expensive to discover: a 30-second hang per frame, or
    a green render of a blank film. The behavioural evidence needs a browser and
    skips in CI (an#22); this one always runs.

    Not in `an/bench/mutants.py`, deliberately: `check_sites` and
    `test_every_declared_mutant_produces_parseable_python` both `compile()` the
    mutated file as Python, so an `index.html` mutant would break them rather
    than prove anything. The mutation is applied to a synthetic string here
    instead — including the **multi-line** spelling, which a line-scoped guard
    would wave through.
    """
    html = runtime_index_html().read_text(encoding="utf-8")
    bodies = _stage_rule_bodies(html)
    assert bodies, "index.html no longer styles #stage at all"
    for body in bodies:
        for spelling in UNCOMPOSITED_SPELLINGS:
            assert spelling not in body, (
                f"index.html's #stage rule sets {spelling!r}. The render path "
                "captures via `page.locator('#stage').screenshot()`, which is a "
                "page capture clipped to the element: an uncomposited canvas "
                "either times out the locator or yields a blank frame. an#57."
            )

    # The guard, mutation-tested against synthetic documents rather than a
    # registry entry. The three-line form is the one that matters: it is what a
    # reformat produces, and a line scan passes it.
    for mutant in (
        "<style>#stage { display: none; }</style>",
        "<style>#stage {\n    display: none;\n}</style>",
        "<style>#stage { display : none ; }</style>",
        "<style>#stage { opacity: 0; }</style>",
    ):
        found = _stage_rule_bodies(mutant)
        assert found, f"the rule parser missed {mutant!r} entirely"
        assert any(
            spelling in body for body in found for spelling in UNCOMPOSITED_SPELLINGS
        ), f"the guard would have waved through {mutant!r}"
