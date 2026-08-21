"""MediaQualityVerifier — pre-render skips, post-render evaluates."""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

import pytest

from an import init
from an.adapters._base import RenderResult
from an.ir.schema import (
    AssetRef,
    Dialogue,
    Meta,
    Resolution,
    SceneIR,
    Shot,
)
from an.orchestrate import orchestrate
from an.project import load
from an.verify.media_quality import MediaQualityVerifier




# Gated per test, not per module: this file mixes pure-Python checks with
# ones that drive a real render.
# `test_no_render_returns_info` passes `None` for the render result, so it
# needs neither a browser nor ffmpeg.


def test_no_render_returns_info():
    """Without a RenderResult, the verifier reports info and passes."""
    scene = SceneIR(timeline=[Shot(id="s1", duration=1.0)])
    rep = MediaQualityVerifier().verify(scene, None)
    assert rep.passed
    assert any("no render result" in f.description for f in rep.findings)


@pytest.mark.browser
@pytest.mark.ffmpeg
def test_default_orchestrator_uses_media_quality():
    """orchestrate() with no explicit verifiers should include media_quality."""
    with tempfile.TemporaryDirectory() as d:
        root = init(Path(d) / "demo")
        proj = load(root)
        proj.scene = SceneIR(
            meta=Meta(title="x", duration=0.5, fps=12,
                      resolution=Resolution(width=160, height=120)),
            timeline=[Shot(id="s1", style="cutout", duration=0.5)],
        )
        proj.mall["scenes"]["main"] = proj.scene
        report = orchestrate(root, output_name="x")
        # The verifications list should include both built-ins.
        assert report.success
        # MediaQualityVerifier ran post-render; layout lint ran twice
        # (pre + post). At minimum we should see ≥3 reports.
        assert len(report.verifications) >= 3


@pytest.mark.browser
@pytest.mark.ffmpeg
def test_dialogue_render_with_offline_tts_flagged_as_silent():
    """A render with dialogue but offline (silent) TTS gets flagged."""
    with tempfile.TemporaryDirectory() as d:
        root = init(Path(d) / "demo")
        proj = load(root)
        proj.scene = SceneIR(
            meta=Meta(title="x", duration=1.5, fps=12,
                      resolution=Resolution(width=240, height=180)),
            timeline=[
                Shot(
                    id="s1", style="cutout", duration=1.5,
                    entities=[
                        AssetRef(kind="character", id="c",
                                 store="characters", ref="c-v1")
                    ],
                    dialogue=[Dialogue(speaker="c", text="some words here")],
                )
            ],
        )
        proj.mall["scenes"]["main"] = proj.scene
        report = orchestrate(root, output_name="x")
        # Find the post-render media-quality verification.
        all_findings = [
            f for vr in report.verifications for f in vr.findings
        ]
        # Either the silent-audio warning OR the dialogue-silence-ratio
        # warning should fire (offline TTS produces silent WAV).
        silent_or_dialogue = [
            f for f in all_findings
            if "silent" in f.description.lower()
            or "speech may be missing" in f.description.lower()
        ]
        assert silent_or_dialogue, (
            "expected MediaQualityVerifier to flag offline-TTS silent dialogue"
        )
