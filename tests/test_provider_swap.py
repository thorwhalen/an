"""Provider-swap auto-resynth: changing tts or lipsync triggers fresh synthesis.

The pipeline's idempotency check compares stamped audio_ref / viseme_ref
against the current providers' expected hashes. Mismatch → re-synthesize.
"""

from __future__ import annotations

import pytest

from an.audio.lipsync import VisemeTrack as AudioVisemeTrack, Viseme
from an.audio.pipeline import produce_audio_for_scene
from an.audio.providers import known_lipsync_names, known_tts_names, make_lipsync, make_tts
from an.audio.tts import AudioClip
from an.ir.schema import Dialogue, Meta, SceneIR, Shot


# A trivial fake TTS / LipSync that lets us tell which one ran.

class _MarkerTTS:
    name = "marker"

    def __init__(self) -> None:
        self.calls = 0

    def synthesize(self, text, voice_id="default", **kw):
        self.calls += 1
        return AudioClip(bytes_=b"RIFF" + b"\x00" * 100, duration=0.5,
                         voice_id=voice_id, transcript=text)

    def list_voices(self):
        return []


class _MarkerLipSync:
    name = "marker_ls"
    convention = "rhubarb"

    def __init__(self) -> None:
        self.calls = 0

    def align(self, audio, transcript):
        self.calls += 1
        return AudioVisemeTrack(
            visemes=[Viseme(time=0.0, code="X"), Viseme(time=0.5, code="X")],
            convention=self.convention,
            duration=audio.duration,
        )


def _two_lines() -> SceneIR:
    return SceneIR(
        meta=Meta(title="t", duration=2.0),
        timeline=[
            Shot(
                id="s1", style="cutout", duration=2.0,
                dialogue=[
                    Dialogue(speaker="x", text="hello"),
                    Dialogue(speaker="y", text="world"),
                ],
            )
        ],
    )


def test_provider_factories_known_names():
    assert "offline" in known_tts_names()
    assert "elevenlabs" in known_tts_names()
    assert "offline" in known_lipsync_names()
    assert "rhubarb" in known_lipsync_names()


def test_make_tts_unknown_raises():
    with pytest.raises(ValueError, match="unknown TTS"):
        make_tts("bogus")


def test_make_lipsync_unknown_raises():
    with pytest.raises(ValueError, match="unknown LipSync"):
        make_lipsync("bogus")


def test_changing_tts_triggers_resynth():
    """Re-running with a different TTS provider should re-synthesize."""
    scene = _two_lines()
    tts1, ls1 = _MarkerTTS(), _MarkerLipSync()
    tts1.name = "tts_a"
    produce_audio_for_scene(scene, tts=tts1, lipsync=ls1)
    assert tts1.calls == 2

    # Same providers → idempotent (no new calls).
    produce_audio_for_scene(scene, tts=tts1, lipsync=ls1)
    assert tts1.calls == 2

    # Different TTS → re-synthesize each line.
    tts2 = _MarkerTTS()
    tts2.name = "tts_b"
    produce_audio_for_scene(scene, tts=tts2, lipsync=ls1)
    assert tts2.calls == 2


def test_changing_lipsync_triggers_resynth():
    scene = _two_lines()
    tts1 = _MarkerTTS()
    tts1.name = "tts_a"
    ls1 = _MarkerLipSync()
    ls1.name = "ls_a"
    ls2 = _MarkerLipSync()
    ls2.name = "ls_b"

    produce_audio_for_scene(scene, tts=tts1, lipsync=ls1)
    assert ls1.calls == 2

    # Idempotent.
    produce_audio_for_scene(scene, tts=tts1, lipsync=ls1)
    assert ls1.calls == 2

    # Different lipsync → re-align (which also re-runs TTS via the produce
    # function — that's fine; total should reflect the second run).
    produce_audio_for_scene(scene, tts=tts1, lipsync=ls2)
    assert ls2.calls == 2


def test_audio_ref_and_viseme_ref_stamped():
    scene = _two_lines()
    produce_audio_for_scene(scene, tts=make_tts("offline"), lipsync=make_lipsync("offline"))
    for line in scene.timeline[0].dialogue:
        assert line.audio_ref is not None
        assert line.viseme_ref is not None
        assert line.audio_ref != line.viseme_ref  # different hashes
