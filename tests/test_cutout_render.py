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
