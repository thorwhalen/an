"""Word timings are retained, not discarded, by the audio pipeline (an#96).

Providers that align from words (whisper, `WordTimingsLipSync`) put them on
`VisemeTrack.words`; the pipeline persists them in the existing viseme sidecar
under the SAME cache key and stamps them into the IR as `Dialogue.word_timings`
(line-relative, JSON-only). Providers with no words leave `None`, and must not
be re-aligned forever for lacking them.
"""

from __future__ import annotations

import json

import pytest

from an.audio.injectable_lipsync import StaticWordTimings, WordTimingsLipSync
from an.audio.lipsync import Viseme, VisemeTrack
from an.audio.offline_lipsync import OfflineLipSync
from an.audio.pipeline import produce_audio_for_dialogue, produce_audio_for_scene
from an.audio.tts import AudioClip
from an.ir.schema import Dialogue, Meta, SceneIR, Shot, WordTimingIR

WORDS = [("Hold", 0.05, 0.30), ("the", 0.32, 0.45), ("shape", 0.50, 0.90)]


class _TTS:
    name = "fake_tts"

    def synthesize(self, text, voice_id="default", **kw):
        return AudioClip(bytes_=b"RIFF" + b"\x00" * 64, duration=1.0, voice_id=voice_id, transcript=text)

    def list_voices(self):
        return []


class _WordlessLipSync:
    """A provider that never sees words and does not claim to."""

    name = "wordless"
    convention = "rhubarb"

    def __init__(self):
        self.calls = 0

    def align(self, audio, transcript):
        self.calls += 1
        return VisemeTrack(visemes=[Viseme(0.0, "X"), Viseme(1.0, "X")], duration=audio.duration)


def _scene():
    return SceneIR(
        meta=Meta(title="t", duration=2.0),
        timeline=[Shot(id="s", style="cutout", duration=2.0, dialogue=[Dialogue(speaker="a", text="Hold the shape")])],
    )


def test_a_word_provider_puts_its_words_on_the_track_and_declares_it():
    ls = WordTimingsLipSync(StaticWordTimings(WORDS))
    assert ls.emits_word_timings is True
    track = ls.align(_TTS().synthesize("Hold the shape"), "Hold the shape")
    assert track.words == WORDS


def test_offline_provider_has_no_words_and_says_so():
    track = OfflineLipSync().align(_TTS().synthesize("hi"), "hi")
    assert track.words is None
    assert not getattr(OfflineLipSync(), "emits_word_timings", False)


def test_words_are_stamped_into_the_ir_line_relative_and_survive_json():
    scene = _scene()
    produce_audio_for_scene(scene, tts=_TTS(), lipsync=WordTimingsLipSync(StaticWordTimings(WORDS)))
    line = scene.timeline[0].dialogue[0]
    assert line.word_timings == [WordTimingIR(text=t, start=s, end=e) for t, s, e in WORDS]
    back = SceneIR.model_validate(json.loads(scene.model_dump_json()))
    assert back.timeline[0].dialogue[0].word_timings == line.word_timings


def test_the_sidecar_carries_words_under_the_unchanged_key():
    """Persistence is the existing viseme payload growing a field, not a new
    store and not a new key — the cache is a function of the same inputs."""
    mall = {"audio": {}, "visemes": {}}
    line = Dialogue(speaker="a", text="Hold the shape")
    ls = WordTimingsLipSync(StaticWordTimings(WORDS))
    _, track = produce_audio_for_dialogue(line, mall, tts=_TTS(), lipsync=ls)
    (key,) = mall["visemes"]
    payload = json.loads(mall["visemes"][key].decode("utf-8"))
    assert payload["words"] == [[t, s, e] for t, s, e in WORDS]
    # Replay: the words come back from the sidecar without re-aligning.
    calls = {"n": 0}
    original = ls.align

    def counting(audio, transcript):
        calls["n"] += 1
        return original(audio, transcript)

    ls.align = counting
    _, again = produce_audio_for_dialogue(line, mall, tts=_TTS(), lipsync=ls)
    assert calls["n"] == 0 and again.words == WORDS


def test_a_pre_an96_sidecar_is_realigned_once_for_a_word_provider_only():
    """An old payload has no `words`. A provider WITH words re-aligns once (same
    key, payload replaced); a provider WITHOUT words replays it untouched."""
    mall = {"audio": {}, "visemes": {}}
    line = Dialogue(speaker="a", text="Hold the shape")
    ls = WordTimingsLipSync(StaticWordTimings(WORDS))
    produce_audio_for_dialogue(line, mall, tts=_TTS(), lipsync=ls)
    (key,) = mall["visemes"]
    old = json.loads(mall["visemes"][key].decode("utf-8"))
    del old["words"]
    mall["visemes"][key] = json.dumps(old).encode("utf-8")

    _, track = produce_audio_for_dialogue(line, mall, tts=_TTS(), lipsync=ls)
    assert track.words == WORDS
    assert json.loads(mall["visemes"][key].decode("utf-8"))["words"] is not None

    wordless = _WordlessLipSync()
    mall2 = {"audio": {}, "visemes": {}}
    produce_audio_for_dialogue(line, mall2, tts=_TTS(), lipsync=wordless)
    (key2,) = mall2["visemes"]
    payload2 = json.loads(mall2["visemes"][key2].decode("utf-8"))
    assert payload2["words"] is None
    del payload2["words"]
    mall2["visemes"][key2] = json.dumps(payload2).encode("utf-8")
    produce_audio_for_dialogue(line, mall2, tts=_TTS(), lipsync=wordless)
    assert wordless.calls == 1, "a word-less provider must not re-align over a missing field"


def test_already_done_does_not_loop_on_a_provider_without_words():
    """The idempotence rule gained `word_timings is not None` — gated on the
    provider's declaration, or every Rhubarb/offline project re-synthesizes
    on every run (the mutant the review named)."""
    scene = _scene()
    wordless = _WordlessLipSync()
    produce_audio_for_scene(scene, tts=_TTS(), lipsync=wordless)
    assert scene.timeline[0].dialogue[0].word_timings is None
    produce_audio_for_scene(scene, tts=_TTS(), lipsync=wordless)
    assert wordless.calls == 1


def test_already_done_realigns_a_wordless_line_once_for_a_word_provider():
    scene = _scene()
    ls = WordTimingsLipSync(StaticWordTimings(WORDS))
    produce_audio_for_scene(scene, tts=_TTS(), lipsync=ls)
    line = scene.timeline[0].dialogue[0]
    line.word_timings = None  # a line stamped before an#96
    produce_audio_for_scene(scene, tts=_TTS(), lipsync=ls)
    assert scene.timeline[0].dialogue[0].word_timings is not None
    produce_audio_for_scene(scene, tts=_TTS(), lipsync=ls)  # and then it is done


def test_scene_md_never_carries_word_timings():
    """JSON-only, like visemes: the md writer emits `speaker [emotion]: text`."""
    from an.ir.sync import ir_to_markdown, markdown_to_ir

    scene = _scene()
    produce_audio_for_scene(scene, tts=_TTS(), lipsync=WordTimingsLipSync(StaticWordTimings(WORDS)))
    md = ir_to_markdown(scene)
    assert "word_timings" not in md and "Hold" in md
    assert markdown_to_ir(md).timeline[0].dialogue[0].word_timings is None


@pytest.mark.parametrize("bad", [("x", "0.1", 0.2)])
def test_ir_word_timing_is_numeric(bad):
    """A stray string start is a schema error, not a later crash."""
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        WordTimingIR(text=bad[0], start="not-a-number", end=bad[2])
