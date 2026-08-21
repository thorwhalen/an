"""The runtime survives repeated `anLoadScene` calls — i.e. hot reload works (an#6).

`an preview` polls the scene file and calls `anLoadScene` again on every change, so
the SECOND load is the one that matters and the one nothing covered. It used to
detach `<canvas id="stage">` from the DOM during teardown (`app.destroy(true, …)`),
after which PixiJS silently rendered into an orphan canvas: the preview went blank
on the first edit and never recovered, without an error anywhere.

Marked `browser`, so it is collected everywhere and gated by the browser gate in
`tests/conftest.py` — which skips it in CI, where the `cutout` extra is not
installed (an#22). The always-running static guard is
`test_cutout_runtime_files.py::test_load_scene_does_not_detach_the_canvas`.
"""

from __future__ import annotations

import pytest

from an.adapters.cutout.render import _serve_dir
from an.adapters.cutout.runtime_files import runtime_dir



pytestmark = pytest.mark.browser


def _scene(bg: str) -> dict:
    """A minimal renderable scene; `bg` distinguishes one load from the next."""
    return {
        "meta": {"width": 320, "height": 240, "background": bg, "duration": 1.0},
        "assets": {},
        "scene": {
            "name": "root",
            "children": [
                {
                    "name": "dot",
                    "visual": {"kind": "ellipse", "rx": 20, "ry": 20,
                               "fill": "#ff0000"},
                    "x": 0, "y": 0,
                }
            ],
        },
        "animations": {},
        "timeline": {"tracks": []},
    }


def test_reloading_a_scene_keeps_the_canvas_in_the_document():
    from playwright.sync_api import sync_playwright

    with _serve_dir(runtime_dir()) as base_url, sync_playwright() as p:
        browser = p.chromium.launch(args=["--no-sandbox"])
        page = browser.new_page(viewport={"width": 320, "height": 240})
        page.goto(f"{base_url}/index.html")
        page.wait_for_function("() => window.anLoadScene && window.PIXI")

        page.evaluate("async s => { await window.anLoadScene(s); }", _scene("#ffffff"))
        first = page.evaluate(
            "() => { const c = document.getElementById('stage');"
            "        return { present: !!c, attached: !!c && c.isConnected,"
            "                 canvases: document.querySelectorAll('canvas').length }; }"
        )
        assert first == {"present": True, "attached": True, "canvases": 1}

        # THE REGRESSION: the second load is what `an preview` does on every edit.
        page.evaluate("async s => { await window.anLoadScene(s); }", _scene("#000000"))
        second = page.evaluate(
            "() => { const c = document.getElementById('stage');"
            "        return { present: !!c, attached: !!c && c.isConnected,"
            "                 canvases: document.querySelectorAll('canvas').length }; }"
        )
        # Before the fix: present=False, and PixiJS had made itself a detached one.
        assert second == {"present": True, "attached": True, "canvases": 1}, (
            f"canvas did not survive the reload: {second}"
        )

        # And a third, because a preview session reloads many times.
        page.evaluate("async s => { await window.anLoadScene(s); }", _scene("#00ff00"))
        assert page.evaluate(
            "() => document.querySelectorAll('canvas').length"
        ) == 1, "each reload leaked an extra canvas"

        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.evaluate("() => window.anSetTime(0.0)")
        assert not errors, errors

        browser.close()


def test_load_scene_refuses_to_render_into_a_missing_canvas():
    """The guard that would have made the original bug loud instead of silent."""
    from playwright.sync_api import sync_playwright

    with _serve_dir(runtime_dir()) as base_url, sync_playwright() as p:
        browser = p.chromium.launch(args=["--no-sandbox"])
        page = browser.new_page(viewport={"width": 320, "height": 240})
        page.goto(f"{base_url}/index.html")
        page.wait_for_function("() => window.anLoadScene && window.PIXI")
        page.evaluate("() => document.getElementById('stage').remove()")

        with pytest.raises(Exception) as excinfo:
            page.evaluate("async s => { await window.anLoadScene(s); }", _scene("#ffffff"))
        assert "stage" in str(excinfo.value)

        browser.close()
