"""Regression: two-character dialogue scene shows BOTH characters
and the mouth actually animates across frames.

This catches two bugs that previously slipped through:

1. ``_build_scene_root`` placed every character at (0, 0) — they overlapped
   so only one character was visible.
2. The JS runtime indexed nodes by paths starting with ``root/`` while the
   Python compiler emitted channel targets like ``charlie/head/mouth``.
   Lookups failed → mouth never updated.
"""

from __future__ import annotations

import collections
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

from an import init
from an.ir.schema import AssetRef, Dialogue, Meta, Resolution, SceneIR, Shot
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


# Approximate-color matching tolerances for the placeholder character palette.
_BLUE = (58, 110, 166)
_PEACH = (245, 200, 154)
_TOL = 15


def _color_count(pixels, target):
    return sum(
        1 for c in pixels
        if all(abs(c[i] - target[i]) < _TOL for i in range(3))
    )


def test_two_characters_render_distinct_and_mouth_animates():
    with tempfile.TemporaryDirectory() as d:
        root = init(Path(d) / "demo")
        proj = load(root)
        proj.scene = SceneIR(
            meta=Meta(
                title="two", duration=2.0, fps=12,
                resolution=Resolution(width=640, height=360),
            ),
            timeline=[
                Shot(
                    id="s1", style="cutout", duration=2.0,
                    entities=[
                        AssetRef(kind="character", id="alpha",
                                 store="characters", ref="alpha-v1"),
                        AssetRef(kind="character", id="beta",
                                 store="characters", ref="beta-v1"),
                    ],
                    dialogue=[
                        Dialogue(speaker="alpha", text="speaking now"),
                    ],
                )
            ],
        )
        proj.mall["scenes"]["main"] = proj.scene
        out = render_project(root, output_name="multi")
        assert out.exists()

        from PIL import Image

        # Pull a mid-shot frame.
        frame = Path(d) / "mid.png"
        subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-i", str(out),
             "-vf", "select=eq(n\\,12)", "-vframes", "1", str(frame)],
            check=True, capture_output=True,
        )
        im = Image.open(frame).convert("RGB")
        pixels = list(im.getdata())
        # Color-agnostic: count "non-background" pixels (anything not near-white).
        # Each character draws a head + torso + 2 arms + 2 eyes + hair + mouth ≈
        # 6500 px on a 640×360 canvas. Two characters should give ≥10000.
        non_white = sum(
            1 for c in pixels
            if c[0] < 240 or c[1] < 240 or c[2] < 240
        )
        assert non_white > 10000, (
            f"only {non_white} non-white pixels — two characters should yield more"
        )
        # Two distinct character palettes should produce a wider color range
        # than one (per-character palette adds variety).
        unique = len({c for c in pixels if (c[0] < 240 or c[1] < 240 or c[2] < 240)})
        assert unique >= 8, f"only {unique} distinct non-bg colors — palette too narrow"

        # Mouth animation: extract frames at fps=4 and ensure they differ.
        frame_dir = Path(d) / "frames"
        frame_dir.mkdir()
        subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-i", str(out),
             "-vf", "fps=4", str(frame_dir / "f_%03d.png")],
            check=True, capture_output=True,
        )
        frames = sorted(frame_dir.glob("f_*.png"))
        contents = {p.read_bytes() for p in frames}
        assert len(contents) >= 3, (
            f"only {len(contents)} unique frames — mouth not animating"
        )
