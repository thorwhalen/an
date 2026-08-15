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
