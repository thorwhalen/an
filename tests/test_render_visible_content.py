"""Regression test: rendered mp4 actually contains visible content.

The original Phase 2D shipped with a white-screen render because the example
scene had no entities AND the markdown parser couldn't extract them. This test
catches that regression by extracting a frame and checking that it isn't
overwhelmingly white (i.e. the character rectangles are present).
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

from an import init
from an.ir.schema import AssetRef, Meta, Resolution, SceneIR, Shot
from an.orchestrate import render_project
from an.project import load




pytestmark = [pytest.mark.browser, pytest.mark.ffmpeg]


_NEAR_WHITE_THRESHOLD = 240  # rgb component above this counts as "near white"
_MIN_NON_WHITE_FRACTION = 0.05  # at least 5% of pixels must be non-white


def _non_white_fraction(png_path: Path) -> float:
    """Return the fraction of pixels in the PNG that are not near-white."""
    from PIL import Image as PIL
    im = PIL.open(png_path).convert("RGB")
    pixels = list(im.getdata())
    near_white = sum(
        1 for r, g, b in pixels
        if r >= _NEAR_WHITE_THRESHOLD
        and g >= _NEAR_WHITE_THRESHOLD
        and b >= _NEAR_WHITE_THRESHOLD
    )
    return 1.0 - near_white / len(pixels)


def test_rendered_character_is_visible():
    """An entity-bearing shot should render a non-white character on screen."""
    with tempfile.TemporaryDirectory() as d:
        root = init(Path(d) / "demo")
        proj = load(root)
        proj.scene = SceneIR(
            meta=Meta(
                title="visible",
                duration=0.5,
                fps=12,
                resolution=Resolution(width=320, height=240),
            ),
            timeline=[
                Shot(
                    id="s1",
                    renderer="cutout",
                    duration=0.5,
                    entities=[
                        AssetRef(
                            kind="character",
                            id="charlie",
                            store="characters",
                            ref="charlie-v1",
                        )
                    ],
                )
            ],
        )
        proj.mall["scenes"]["main"] = proj.scene

        output = render_project(root, output_name="visible")
        assert output.exists()

        # Extract first frame.
        frame_path = Path(d) / "frame.png"
        result = subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-loglevel",
                "error",
                "-i",
                str(output),
                "-vframes",
                "1",
                str(frame_path),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, f"ffmpeg failed: {result.stderr}"
        assert frame_path.exists()

        non_white = _non_white_fraction(frame_path)
        assert non_white >= _MIN_NON_WHITE_FRACTION, (
            f"rendered frame is {(1-non_white)*100:.1f}% white — "
            f"character not visible (white-screen regression)"
        )
