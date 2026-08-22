"""End-to-end cutout render: a real mp4 from a hand-built Shot.

Skipped automatically when ffmpeg / playwright / chromium aren't available so
``pytest`` stays green in minimal environments.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

from an import build_project_mall
from an.adapters.cutout import CutoutRenderer
from an.adapters._base import RenderContext
from an.ir.compose import tween
from an.ir.schema import Shot


# Gated per test, not per module: this file mixes pure-Python checks with
# ones that drive a real render.
# `test_renderer_rejects_non_cutout_shot` only asks `can_render`; it never
# renders anything.


@pytest.mark.browser
@pytest.mark.ffmpeg
def test_renderer_basic_smoke():
    """Render a 1-second cutout shot to mp4."""
    shot = Shot(
        id="smoke",
        style="cutout",
        duration=1.0,
        actions=[tween("root", "x", to=100.0, duration=1.0)],
    )
    with tempfile.TemporaryDirectory() as d:
        mall = build_project_mall(d, ensure=True)
        ctx = RenderContext(
            mall=mall, work_dir=Path(d) / "work", fps=12, resolution=(320, 240)
        )
        ctx.work_dir.mkdir(parents=True, exist_ok=True)
        renderer = CutoutRenderer()
        result = renderer.render(shot, ctx)
        assert result.mp4_path.exists()
        assert result.mp4_path.stat().st_size > 0
        # ffprobe sanity: the file should be a valid mp4 we can probe.
        probe = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=nb_frames",
                "-of",
                "csv=p=0",
                str(result.mp4_path),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if probe.returncode == 0 and probe.stdout.strip().isdigit():
            nb_frames = int(probe.stdout.strip())
            assert nb_frames >= 1
        # Frame manifest reflects what we screenshotted.
        assert len(result.frame_manifest) == 12  # 1.0s * 12 fps


def test_renderer_rejects_non_cutout_shot():
    renderer = CutoutRenderer()
    assert not renderer.can_render(Shot(id="x", style="manim", duration=1.0))
    assert renderer.can_render(Shot(id="x", style="cutout", duration=1.0))


@pytest.mark.browser
def test_hiding_the_stage_canvas_breaks_element_capture():
    """MUTATION: none -- this is the EVIDENCE the static guard cites.

    an#57 says the 15x seek-loop win is free because "frames come from
    `canvas.screenshot()`, which does not need the element composited to the
    page". This test is the measurement that refutes it, kept in the repo so
    the refutation is reproducible rather than a sentence in a comment.

    Two independent failures, and the second is the dangerous one: an
    `opacity:0` canvas passes Playwright's visibility check and screenshots a
    BLANK frame, so the idea does not fail loudly if it is spelled the clever
    way.
    """
    from playwright.sync_api import sync_playwright

    from an.adapters.cutout.render import DETERMINISTIC_CHROMIUM_ARGS, _serve_dir
    from an.adapters.cutout.runtime_files import runtime_dir

    scene = {
        "meta": {"width": 320, "height": 240, "background": "#ffffff", "duration": 1.0},
        "assets": {},
        "scene": {
            "name": "root",
            "children": [
                {
                    "name": "dot",
                    "x": 0,
                    "y": 0,
                    "visual": {
                        "kind": "ellipse",
                        "rx": 60,
                        "ry": 60,
                        "fill": "#112233",
                    },
                },
            ],
        },
        "animations": {},
        "timeline": {"tracks": []},
    }

    with _serve_dir(runtime_dir()) as base_url, sync_playwright() as p:
        browser = p.chromium.launch(
            args=list(DETERMINISTIC_CHROMIUM_ARGS), headless=True
        )
        page = browser.new_page(viewport={"width": 320, "height": 240})
        page.goto(f"{base_url}/index.html")
        page.wait_for_function("() => window.anLoadScene && window.PIXI")
        page.evaluate("async s => { await window.anLoadScene(s); }", scene)
        page.evaluate("() => window.anSetTime(0.0)")

        baseline = page.locator("#stage").screenshot(omit_background=False)
        assert len(set(baseline)) > 1, "the baseline frame is already blank"

        # 1. display:none -- Playwright refuses outright.
        page.evaluate(
            "() => { document.getElementById('stage').style.display = 'none'; }"
        )
        assert page.locator("#stage").is_visible() is False
        with pytest.raises(Exception) as e:  # noqa: PT011 - playwright's own type
            page.locator("#stage").screenshot(timeout=2_000)
        assert "imeout" in str(e.value), (
            "display:none must make the element screenshot time out, not "
            f"succeed: {e.value}"
        )

        # 2. opacity:0 -- accepted, and captures nothing. The quiet failure.
        page.evaluate(
            "() => { const c = document.getElementById('stage');"
            "        c.style.display = ''; c.style.opacity = '0'; }"
        )
        assert page.locator("#stage").is_visible() is True
        hidden = page.locator("#stage").screenshot(omit_background=False)
        assert hidden != baseline, "opacity:0 changed nothing -- re-check the probe"

        browser.close()


@pytest.mark.browser
@pytest.mark.ffmpeg
def test_renderer_carries_provenance():
    """Render result should describe what produced it."""
    shot = Shot(id="prov", style="cutout", duration=0.25)
    with tempfile.TemporaryDirectory() as d:
        mall = build_project_mall(d, ensure=True)
        ctx = RenderContext(
            mall=mall, work_dir=Path(d) / "work", fps=8, resolution=(160, 120)
        )
        ctx.work_dir.mkdir(parents=True, exist_ok=True)
        result = CutoutRenderer().render(shot, ctx)
        assert result.provenance["shot_id"] == "prov"
        assert result.provenance["fps"] == 8
        assert result.provenance["resolution"] == (160, 120)
