"""VisionLMVerifier — skip-if-missing checks + JSON parser unit tests."""

from __future__ import annotations

import importlib.util
import os
import tempfile
from pathlib import Path

import pytest

from .conftest import requires_live_api

from an import init
from an.ir.schema import (
    AssetRef,
    Dialogue,
    Meta,
    Resolution,
    SceneIR,
    Shot,
)
from an.orchestrate import render_project
from an.project import load
from an.verify.vision import VisionLMVerifier, _parse_issues


# -----------------------------------------------------------------------------
# Pure parser tests (no API key needed)
# -----------------------------------------------------------------------------


def test_parse_issues_handles_plain_json():
    body = '{"issues": [{"severity": "warning", "where": "frame 1", "what": "x"}]}'
    out = _parse_issues(body)
    assert len(out) == 1
    assert out[0]["severity"] == "warning"


def test_parse_issues_handles_fenced_json():
    body = "Here's my analysis:\n```json\n{\"issues\": []}\n```\nthat's it."
    assert _parse_issues(body) == []


def test_parse_issues_handles_prose_around_json():
    body = "Sure, here goes:\n{\"issues\": [{\"what\": \"y\"}]}\nLet me know."
    out = _parse_issues(body)
    assert len(out) == 1
    assert out[0]["what"] == "y"


def test_parse_issues_returns_no_verdict_on_garbage():
    """`None` and `[]` are different answers, and conflating them was the bug.

    An empty reply, a refusal, and a literal `{"issues": []}` used to produce
    byte-identical reports at `info` severity, with `passed` True through all
    three — so "the model declined to answer" was indistinguishable from "the
    model looked and found nothing".
    """
    assert _parse_issues("not json at all") is None
    assert _parse_issues("") is None
    assert _parse_issues("I can't help with that.") is None
    assert _parse_issues('{"issues": []}') == []


def test_no_render_returns_info_finding():
    scene = SceneIR(timeline=[Shot(id="s1", duration=1.0)])
    rep = VisionLMVerifier().verify(scene, None)
    assert rep.passed
    assert any("no render result" in f.description for f in rep.findings)


def test_no_api_key_returns_info_finding():
    """Constructing without a key + with no env should skip cleanly."""
    saved = os.environ.pop("ANTHROPIC_API_KEY", None)
    try:
        with tempfile.TemporaryDirectory() as d:
            # Use a real existing path so we get past the "no render" check
            # and exercise the no-key path specifically.
            real_file = Path(d) / "fake.mp4"
            real_file.write_bytes(b"\x00" * 100)
            scene = SceneIR(timeline=[Shot(id="s1", duration=1.0)])
            from an.adapters._base import RenderResult
            rr = RenderResult(mp4_path=real_file, duration=1.0)
            rep = VisionLMVerifier(api_key=None).verify(scene, rr)
            assert rep.passed
            assert any("ANTHROPIC_API_KEY" in f.description for f in rep.findings)
    finally:
        if saved is not None:
            os.environ["ANTHROPIC_API_KEY"] = saved


# -----------------------------------------------------------------------------
# Live API test — SPENDS MONEY.
#
# This test was gated on "ffmpeg + chromium + the anthropic SDK + a key in the
# environment" and nothing else, which is exactly the pattern this repo's
# conftest exists to refuse: a key being present is not consent to spend, and
# that condition is satisfied by every developer machine and every agent session
# that has sourced a shell profile. It never fired only because the module-level
# `importorskip("playwright...")` above it meant the whole file was never
# collected — so the violation was invisible rather than absent. Collecting the
# file (an#22) is what surfaced it.
#
# `requires_live_api` adds the missing half: an explicit positive opt-in
# (AN_LIVE_API_TESTS=1) and never in CI. `live_api` additionally opts the test
# out of the autouse offline-network guard, which is what let it reach Anthropic
# at all.
# -----------------------------------------------------------------------------


@pytest.mark.live_api
@requires_live_api
@pytest.mark.browser
@pytest.mark.ffmpeg
@pytest.mark.skipif(
    importlib.util.find_spec("anthropic") is None
    or not os.environ.get("ANTHROPIC_API_KEY"),
    reason="needs the anthropic SDK + ANTHROPIC_API_KEY",
)
def test_vision_lm_full_pipeline_returns_findings():
    """Render a tiny scene + call Claude vision; parse whatever it says."""
    with tempfile.TemporaryDirectory() as d:
        root = init(Path(d) / "demo")
        proj = load(root)
        proj.scene = SceneIR(
            meta=Meta(title="vision", duration=0.5, fps=12,
                      resolution=Resolution(width=240, height=180)),
            timeline=[
                Shot(
                    id="s1", renderer="cutout", duration=0.5,
                    entities=[
                        AssetRef(kind="character", id="c",
                                 store="characters", ref="c-v1")
                    ],
                )
            ],
        )
        proj.mall["scenes"]["main"] = proj.scene
        out = render_project(root, output_name="vision")

        from an.adapters._base import RenderResult
        rr = RenderResult(mp4_path=out, duration=0.5)
        rep = VisionLMVerifier(frame_count=2).verify(proj.scene, rr)
        # Either zero findings (LM saw no issues) or some issues — both fine.
        # We only assert the call didn't crash and produced a report.
        assert rep is not None
