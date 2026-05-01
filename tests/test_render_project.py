"""End-to-end project render: an init demo && render → mp4 on disk.

Skipped automatically when ffmpeg / playwright / chromium aren't available.
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

import pytest

from an import init
from an.ir.compose import tween
from an.orchestrate import render_project
from an.project import load


_FFMPEG = shutil.which("ffmpeg")
playwright = pytest.importorskip("playwright.sync_api", reason="playwright not installed")


def _chromium_installed() -> bool:
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            b = p.chromium.launch()
            b.close()
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _FFMPEG or not _chromium_installed(),
    reason="needs ffmpeg + playwright chromium",
)


def test_init_then_render_produces_mp4():
    """The Phase-2D demo path: an init demo → write a tiny scene → render → mp4 exists."""
    with tempfile.TemporaryDirectory() as d:
        root = init(Path(d) / "demo")
        proj = load(root)
        # Override the seeded scene with one that has a renderable shot.
        from an.ir.schema import Meta, Resolution, SceneIR, Shot

        proj.scene = SceneIR(
            meta=Meta(
                title="demo",
                duration=1.0,
                fps=12,
                resolution=Resolution(width=240, height=180),
            ),
            timeline=[
                Shot(
                    id="s1",
                    style="cutout",
                    duration=1.0,
                    actions=[tween("root", "x", to=50.0, duration=1.0)],
                )
            ],
        )
        proj.mall["scenes"]["main"] = proj.scene

        output = render_project(root, output_name="demo")
        assert output.exists()
        assert output.stat().st_size > 0
        # Mall's output store should hold the same bytes.
        assert "demo" in proj.mall["output"]
        assert len(proj.mall["output"]["demo"]) == output.stat().st_size


def test_render_concatenates_multiple_shots():
    """Two shots → one final mp4 via ffmpeg concat."""
    with tempfile.TemporaryDirectory() as d:
        root = init(Path(d) / "demo2")
        proj = load(root)
        from an.ir.schema import Meta, Resolution, SceneIR, Shot

        proj.scene = SceneIR(
            meta=Meta(
                title="two",
                duration=2.0,
                fps=12,
                resolution=Resolution(width=160, height=120),
            ),
            timeline=[
                Shot(id="a", style="cutout", duration=1.0),
                Shot(id="b", style="cutout", duration=1.0),
            ],
        )
        proj.mall["scenes"]["main"] = proj.scene

        output = render_project(root, output_name="two_shots")
        assert output.exists()
        # Both per-shot mp4s should be in the shots store.
        assert "a" in proj.mall["shots"]
        assert "b" in proj.mall["shots"]
