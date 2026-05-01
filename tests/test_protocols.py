"""Protocol stub sanity tests.

These don't ship implementations — just confirm the Protocols are runtime-checkable
and the dataclasses construct cleanly.
"""

from __future__ import annotations

from pathlib import Path

from an.adapters import (
    Renderer,
    RenderContext,
    RenderResult,
    RendererRegistry,
)
from an.audio import AudioClip, LipSyncProvider, TTSProvider, Viseme, VisemeTrack
from an.ir.schema import Shot
from an.verify import Finding, VerificationReport, Verifier


def test_render_result_dataclass():
    r = RenderResult(mp4_path=Path("/tmp/x.mp4"), duration=1.0)
    assert r.mp4_path == Path("/tmp/x.mp4")
    assert r.duration == 1.0
    assert r.frame_manifest == []


def test_renderer_registry_round_trips():
    class Fake:
        name = "fake"
        supported_styles = ("cutout",)

        def can_render(self, shot):
            return shot.style == "cutout"

        def render(self, shot, ctx):
            return RenderResult(mp4_path=Path("/tmp/x.mp4"), duration=shot.duration)

    reg = RendererRegistry()
    reg.register(Fake())
    assert "fake" in reg.names()
    found = reg.find_for(Shot(id="s1", style="cutout", duration=1.0))
    assert found is not None
    assert found.name == "fake"
    none_found = reg.find_for(Shot(id="s2", style="manim", duration=1.0))
    assert none_found is None


def test_renderer_protocol_runtime_check():
    class Fake:
        name = "fake"
        supported_styles = ("cutout",)

        def can_render(self, shot):
            return True

        def render(self, shot, ctx):
            return RenderResult(mp4_path=Path("/tmp/x.mp4"), duration=1.0)

    assert isinstance(Fake(), Renderer)


def test_verifier_protocol_runtime_check():
    class FakeVerifier:
        name = "fake"

        def verify(self, ir, render):
            return VerificationReport()

    assert isinstance(FakeVerifier(), Verifier)


def test_verification_report_severity_routing():
    report = VerificationReport()
    report.add("warning", "timeline/0", "minor")
    assert report.passed is True
    report.add("error", "timeline/1", "broken")
    assert report.passed is False
    assert len(report.findings) == 2
    assert all(isinstance(f, Finding) for f in report.findings)


def test_audio_dataclasses():
    clip = AudioClip(duration=1.5, sample_rate=22050)
    assert clip.duration == 1.5
    assert clip.sample_rate == 22050
    track = VisemeTrack(visemes=[Viseme(time=0.0, code="A"), Viseme(time=0.1, code="B")])
    assert len(track.visemes) == 2
    assert track.convention == "rhubarb"


def test_tts_and_lipsync_protocols_runtime_checkable():
    class FakeTTS:
        name = "fake"

        def synthesize(self, text, voice_id, **kw):
            return AudioClip(transcript=text)

        def list_voices(self):
            return []

    class FakeLip:
        name = "fake"
        convention = "rhubarb"

        def align(self, audio, transcript):
            return VisemeTrack()

    assert isinstance(FakeTTS(), TTSProvider)
    assert isinstance(FakeLip(), LipSyncProvider)


def test_render_context_carries_mall():
    mall = {"voices": {}}
    ctx = RenderContext(mall=mall, work_dir=Path("/tmp"))
    assert ctx.fps > 0
    assert ctx.resolution[0] > 0
    assert ctx.mall is mall
