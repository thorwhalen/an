"""Regression: rendered mp4s carry an AAC audio stream (silent or not).

Catches the bug where the cutout renderer never muxed audio into the mp4 —
shot mp4s had only a video stream, and even ffmpeg-concatenating them
produced a silent final video despite stamped viseme tracks.

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


def _streams_of(path: Path) -> dict[str, dict]:
    """Return {codec_type: {codec_name, channels, ...}} for the given mp4."""
    result = subprocess.run(
        [
            "ffprobe", "-v", "error",
            "-show_entries",
            "stream=codec_type,codec_name,channels,sample_rate",
            "-of", "default=noprint_wrappers=1",
            str(path),
        ],
        capture_output=True, text=True, check=True,
    )
    streams: dict[str, dict] = {}
    current: dict[str, str] = {}
    for line in result.stdout.splitlines():
        if "=" not in line:
            continue
        k, v = line.split("=", 1)
        current[k] = v
        if k == "codec_type":
            streams[v] = current
            current = {}
    # Last stream may have been emitted without a trailing codec_type marker.
    return streams


def test_silent_shot_still_has_audio_stream():
    """A shot with no dialogue should still get a silent audio track so
    concat across heterogeneous shots doesn't fail."""
    with tempfile.TemporaryDirectory() as d:
        root = init(Path(d) / "demo")
        proj = load(root)
        proj.scene = SceneIR(
            meta=Meta(title="silent", duration=0.5, fps=12,
                      resolution=Resolution(width=160, height=120)),
            timeline=[Shot(id="s1", renderer="cutout", duration=0.5)],
        )
        proj.mall["scenes"]["main"] = proj.scene
        out = render_project(root, output_name="silent")
        streams = _streams_of(out)
        assert "video" in streams
        assert "audio" in streams, f"no audio stream in {out}"
        assert streams["audio"].get("codec_name") == "aac"


def test_dialogue_shot_carries_audio_stream():
    """A shot with dialogue should get audio at line.start times."""
    with tempfile.TemporaryDirectory() as d:
        root = init(Path(d) / "demo")
        proj = load(root)
        proj.scene = SceneIR(
            meta=Meta(title="speak", duration=2.0, fps=12,
                      resolution=Resolution(width=240, height=180)),
            timeline=[
                Shot(
                    id="s1", renderer="cutout", duration=2.0,
                    entities=[
                        AssetRef(kind="character", id="c",
                                 store="characters", ref="c-v1")
                    ],
                    dialogue=[
                        Dialogue(speaker="c", text="hello there"),
                    ],
                )
            ],
        )
        proj.mall["scenes"]["main"] = proj.scene
        out = render_project(root, output_name="speak")
        streams = _streams_of(out)
        assert streams["audio"]["codec_name"] == "aac"
        # Audio_ref should have been stamped on the dialogue.
        proj_reloaded = load(root)
        line = proj_reloaded.scene.timeline[0].dialogue[0]
        assert line.audio_ref is not None
        assert line.audio_ref in proj_reloaded.mall["audio"]


def test_multi_shot_concat_preserves_audio():
    """Concat across two shots (one silent + one with dialogue) should produce
    a single mp4 with one continuous audio track."""
    with tempfile.TemporaryDirectory() as d:
        root = init(Path(d) / "demo")
        proj = load(root)
        proj.scene = SceneIR(
            meta=Meta(title="x", duration=2.0, fps=12,
                      resolution=Resolution(width=160, height=120)),
            timeline=[
                Shot(id="quiet", renderer="cutout", duration=1.0),
                Shot(
                    id="loud", renderer="cutout", duration=1.0,
                    dialogue=[Dialogue(speaker="x", text="hi")],
                ),
            ],
        )
        proj.mall["scenes"]["main"] = proj.scene
        out = render_project(root, output_name="multi")
        streams = _streams_of(out)
        assert streams["audio"]["codec_name"] == "aac"
