"""LayoutLintVerifier + HumanInTheLoopVerifier."""

from __future__ import annotations

import pytest

from an.ir.schema import (
    Dialogue,
    Meta,
    Resolution,
    SceneIR,
    Shot,
)
from an.verify import LayoutLintVerifier, HumanInTheLoopVerifier


def test_clean_scene_passes_lint():
    scene = SceneIR(
        meta=Meta(title="x", duration=5.0),
        timeline=[Shot(id="s1", renderer="cutout", duration=5.0)],
    )
    report = LayoutLintVerifier().verify(scene)
    assert report.passed
    assert all(f.severity != "error" for f in report.findings)


def test_lint_flags_duplicate_shot_id():
    scene = SceneIR(
        timeline=[
            Shot(id="dup", duration=1.0),
            Shot(id="dup", duration=1.0),
        ]
    )
    report = LayoutLintVerifier().verify(scene)
    assert not report.passed
    assert any("duplicate" in f.description for f in report.findings)


def test_lint_warns_on_meta_duration_mismatch():
    scene = SceneIR(
        meta=Meta(duration=10.0),
        timeline=[Shot(id="s1", duration=3.0)],
    )
    report = LayoutLintVerifier().verify(scene)
    assert any("doesn't match" in f.description for f in report.findings)


def test_lint_warns_on_dialogue_overflowing_shot():
    scene = SceneIR(
        timeline=[
            Shot(
                id="s1",
                duration=1.0,
                dialogue=[
                    Dialogue(speaker="x", text="long line", start=0.0, duration=2.0)
                ],
            )
        ]
    )
    report = LayoutLintVerifier().verify(scene)
    assert any("clipped or stretch" in f.description for f in report.findings)


def test_lint_errors_on_zero_duration_shot():
    scene = SceneIR(timeline=[Shot(id="s1", duration=0.0)])
    report = LayoutLintVerifier().verify(scene)
    assert not report.passed


def test_lint_errors_on_invalid_resolution():
    scene = SceneIR(
        meta=Meta(resolution=Resolution(width=0, height=10)),
        timeline=[Shot(id="s1", duration=1.0)],
    )
    report = LayoutLintVerifier().verify(scene)
    assert not report.passed


def test_human_verifier_skips_when_no_tty(monkeypatch):
    """When not running in a TTY, HumanInTheLoopVerifier should skip silently."""
    import sys
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    scene = SceneIR(timeline=[Shot(id="s1", duration=1.0)])
    from an.adapters._base import RenderResult
    from pathlib import Path

    rr = RenderResult(mp4_path=Path("/tmp/dummy.mp4"), duration=1.0)
    report = HumanInTheLoopVerifier().verify(scene, rr)
    # Skip is reported as info, not an error.
    assert report.passed
    assert any(f.severity == "info" for f in report.findings)


def test_human_verifier_no_render_returns_info():
    scene = SceneIR(timeline=[Shot(id="s1", duration=1.0)])
    report = HumanInTheLoopVerifier().verify(scene, None)
    assert report.passed
    assert any("nothing to inspect" in f.description for f in report.findings)
