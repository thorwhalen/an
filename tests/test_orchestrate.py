"""Orchestrator: validate → render → verify."""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

import pytest

from an import init
from an.ir.schema import AssetRef, Meta, Resolution, SceneIR, Shot
from an.orchestrate import OrchestratorReport, orchestrate
from an.project import load
from an.verify import LayoutLintVerifier


def test_orchestrate_skip_render_runs_lint_only():
    with tempfile.TemporaryDirectory() as d:
        root = init(Path(d) / "demo")
        proj = load(root)
        proj.scene = SceneIR(
            meta=Meta(title="t", duration=2.0),
            timeline=[Shot(id="s1", style="cutout", duration=2.0)],
        )
        proj.mall["scenes"]["main"] = proj.scene
        report = orchestrate(root, skip_render=True)
        assert isinstance(report, OrchestratorReport)
        assert report.success
        assert report.output_path is None
        assert report.validation is not None
        assert len(report.verifications) >= 1


def test_orchestrate_fails_on_invalid_scene():
    with tempfile.TemporaryDirectory() as d:
        root = init(Path(d) / "demo")
        proj = load(root)
        # Inject duplicate shot ids → semantic failure.
        proj.scene = SceneIR(
            timeline=[
                Shot(id="dup", duration=1.0),
                Shot(id="dup", duration=1.0),
            ]
        )
        proj.mall["scenes"]["main"] = proj.scene
        report = orchestrate(root, skip_render=True)
        assert not report.success
        assert report.error is not None




@pytest.mark.browser
@pytest.mark.ffmpeg
def test_orchestrate_full_pipeline_renders():
    with tempfile.TemporaryDirectory() as d:
        root = init(Path(d) / "demo")
        proj = load(root)
        proj.scene = SceneIR(
            meta=Meta(title="t", duration=0.5, fps=12,
                      resolution=Resolution(width=160, height=120)),
            timeline=[Shot(id="s1", style="cutout", duration=0.5,
                           entities=[AssetRef(kind="character", id="c",
                                              store="characters", ref="c-v1")])],
        )
        proj.mall["scenes"]["main"] = proj.scene
        report = orchestrate(root, output_name="orch")
        assert report.success
        assert report.output_path is not None
        assert report.output_path.exists()
        assert report.output_path.stat().st_size > 0
