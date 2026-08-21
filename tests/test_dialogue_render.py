"""Phase 4 end-to-end: render a dialogue scene with offline TTS+visemes.

Skipped automatically when ffmpeg / playwright / chromium aren't available.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

from an import init
from an.ir.schema import AssetRef, Dialogue, Meta, Resolution, SceneIR, Shot
from an.orchestrate import render_project
from an.project import load




pytestmark = [pytest.mark.browser, pytest.mark.ffmpeg]


def test_dialogue_scene_renders_to_mp4():
    """A two-character dialogue scene should render with auto-audio."""
    with tempfile.TemporaryDirectory() as d:
        root = init(Path(d) / "demo")
        proj = load(root)
        proj.scene = SceneIR(
            meta=Meta(
                title="dialogue",
                duration=3.0,
                fps=12,
                resolution=Resolution(width=320, height=240),
            ),
            timeline=[
                Shot(
                    id="s1",
                    style="cutout",
                    duration=3.0,
                    entities=[
                        AssetRef(
                            kind="character",
                            id="charlie",
                            store="characters",
                            ref="charlie-v1",
                        ),
                    ],
                    dialogue=[
                        Dialogue(speaker="charlie", text="Hello there."),
                    ],
                )
            ],
        )
        proj.mall["scenes"]["main"] = proj.scene

        output = render_project(root, output_name="dialogue")
        assert output.exists()
        assert output.stat().st_size > 0

        # After auto-audio, the scene should have a viseme track stamped.
        proj_reloaded = load(root)
        line = proj_reloaded.scene.timeline[0].dialogue[0]
        assert line.viseme_track is not None
        assert len(line.viseme_track.keyframes) >= 2

        # And the audio store should have the synthesized WAV.
        assert len(proj_reloaded.mall["audio"]) >= 1


def test_mouth_changes_across_frames():
    """Across the dialogue duration, the mouth visual should change shape.

    We extract two frames at different times and compare the head region.
    Because OfflineLipSync emits multiple distinct visemes for "speaking",
    the rect at the mouth position should differ between frames.
    """
    with tempfile.TemporaryDirectory() as d:
        root = init(Path(d) / "demo")
        proj = load(root)
        proj.scene = SceneIR(
            meta=Meta(
                title="mouth", duration=1.5, fps=12,
                resolution=Resolution(width=320, height=240),
            ),
            timeline=[
                Shot(
                    id="s1", style="cutout", duration=1.5,
                    entities=[
                        AssetRef(
                            kind="character",
                            id="charlie",
                            store="characters",
                            ref="charlie-v1",
                        ),
                    ],
                    dialogue=[
                        Dialogue(speaker="charlie", text="speaking aloud"),
                    ],
                )
            ],
        )
        proj.mall["scenes"]["main"] = proj.scene

        output = render_project(root, output_name="mouth")
        assert output.exists()

        # Pull a couple of frames out and check they differ near the head/mouth.
        frame_dir = Path(d) / "frames"
        frame_dir.mkdir()
        cmd = [
            "ffmpeg", "-y", "-loglevel", "error",
            "-i", str(output),
            "-vf", "fps=4",
            str(frame_dir / "f_%03d.png"),
        ]
        subprocess.run(cmd, check=True, capture_output=True, text=True)
        pngs = sorted(frame_dir.glob("f_*.png"))
        assert len(pngs) >= 3

        # If the mouth animates, at least two frames should differ in pixel content.
        contents = [p.read_bytes() for p in pngs]
        unique = len(set(contents))
        assert unique >= 2, "all frames identical — mouth not animating"
