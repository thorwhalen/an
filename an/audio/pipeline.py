"""Audio pipeline orchestration: dialogue → audio → visemes → IR mutation.

Phase 3 wires the pieces together. ``produce_audio_for_scene`` walks every
``Dialogue`` in the scene, synthesizes its audio + viseme track via the
configured providers, persists artifacts to ``mall["audio"]`` /
``mall["visemes"]``, and stamps the resulting ``VisemeTrack`` and timing
back onto the ``Dialogue`` line so renderers can find it.

Defaults are the offline providers (silent WAV + deterministic visemes), so
the entire pipeline runs without API keys or external binaries.

>>> from an.audio.pipeline import default_tts, default_lipsync
>>> default_tts().name
'offline'
>>> default_lipsync().name
'offline'
"""

from __future__ import annotations

import json
from collections.abc import Mapping, MutableMapping
from dataclasses import asdict
from typing import Any

from an.audio.lipsync import LipSyncProvider, Viseme, VisemeTrack
from an.audio.offline_lipsync import OfflineLipSync
from an.audio.offline_tts import OfflineTTS
from an.audio.tts import AudioClip, TTSProvider
from an.ir.schema import Dialogue, SceneIR, VisemeKeyframe, WordTimingIR
from an.ir.schema import VisemeTrack as IRVisemeTrack
from an.util import _stable_hash


class AudioPipelineError(RuntimeError):
    """The scene declares audio the pipeline cannot produce. Carries detail."""


def default_tts() -> TTSProvider:
    """The default TTS provider: ``OfflineTTS``."""
    return OfflineTTS()


def default_lipsync() -> LipSyncProvider:
    """The default lip-sync provider: ``OfflineLipSync``."""
    return OfflineLipSync()


def produce_audio_for_dialogue(
    dialogue: Dialogue,
    mall: Mapping[str, MutableMapping] | None = None,
    *,
    tts: TTSProvider | None = None,
    lipsync: LipSyncProvider | None = None,
) -> tuple[AudioClip, VisemeTrack]:
    """Synthesize audio + visemes for one dialogue line.

    Side effects: when ``mall`` is provided, persists the WAV to
    ``mall["audio"]`` keyed by the content-hash of the dialogue, and persists
    the viseme JSON to ``mall["visemes"]`` similarly. Cache-friendly: a
    second call with identical inputs returns the cached versions.
    """
    tts = tts or default_tts()
    lipsync = lipsync or default_lipsync()
    voice_id = dialogue.voice_ref or "default"

    # Content hash governs caching.
    cache_key = _stable_hash(
        {"text": dialogue.text, "voice": voice_id, "tts": tts.name}
    )

    audio_clip = _load_or_synthesize(tts, dialogue.text, voice_id, mall, cache_key)

    viseme_cache_key = _stable_hash(
        {"audio_key": cache_key, "lipsync": lipsync.name, "transcript": dialogue.text}
    )
    track = _load_or_align(lipsync, audio_clip, dialogue.text, mall, viseme_cache_key)
    return audio_clip, track


def produce_audio_for_scene(
    scene: SceneIR,
    mall: Mapping[str, MutableMapping] | None = None,
    *,
    tts: TTSProvider | None = None,
    lipsync: LipSyncProvider | None = None,
) -> SceneIR:
    """Walk every dialogue line, synthesize, and stamp viseme tracks back.

    Mutates the ``scene`` in place AND returns it (for chaining).
    Stamps ``Dialogue.duration``, ``Dialogue.start`` (if unset),
    ``Dialogue.viseme_track``, and ``Dialogue.audio_ref`` (mall["audio"] key)
    so the renderer can find the audio later. Lines with an existing
    viseme_track AND audio_ref are skipped (idempotent).
    """
    tts = tts or default_tts()
    lipsync = lipsync or default_lipsync()
    voice_default = "default"
    cursor = 0.0
    for shot in scene.timeline:
        cursor = 0.0
        if shot.narration:
            # `Shot.narration` is fully modelled in the IR — text, voice_ref,
            # start, duration, viseme_track, audio_ref — and nothing has ever
            # consumed it: this loop walks `shot.dialogue` only, and the cutout
            # compiler has no narration path either. So a narrated shot produced
            # no audio AND no picture, silently. Narrator-over-visuals is the
            # shape of the whole explainer genre, so this is a real gap rather
            # than an oversight, and it is tracked as such.
            raise AudioPipelineError(
                f"shot {shot.id!r} declares {len(shot.narration)} narration "
                "line(s), which the audio pipeline does not synthesise — it "
                "walks shot.dialogue only. Narration produces neither audio nor "
                "video today. Use a dialogue line with an off-screen speaker as "
                "the workaround; the real fix is tracked at "
                "https://github.com/thorwhalen/an/issues/9."
            )
        for line in shot.dialogue:
            voice_id = line.voice_ref or voice_default
            expected_audio_ref = _stable_hash(
                {"text": line.text, "voice": voice_id, "tts": tts.name}
            )
            expected_viseme_ref = _stable_hash(
                {
                    "audio_key": expected_audio_ref,
                    "lipsync": lipsync.name,
                    "transcript": line.text,
                }
            )
            audio_store = mall.get("audio") if mall is not None else None
            viseme_store = mall.get("visemes") if mall is not None else None
            already_done = (
                line.audio_ref == expected_audio_ref
                and line.viseme_ref == expected_viseme_ref
                and line.viseme_track is not None
                and line.duration is not None
                # A line stamped before an#96 by a provider that HAS words is
                # re-aligned once so the words land; a provider without words
                # (offline, Rhubarb) never triggers this, or it would re-align
                # forever. The cache key is unchanged: the sidecar simply grew.
                and (line.word_timings is not None or not _emits_word_timings(lipsync))
                and (audio_store is None or expected_audio_ref in audio_store)
                and (viseme_store is None or expected_viseme_ref in viseme_store)
            )
            if already_done:
                cursor = (line.start or cursor) + line.duration
                continue
            # Either never synthesized, or providers changed → full re-synth.
            # If `audio_ref` was previously set (i.e. this is a re-synth, not
            # a first-time synth), reset start to the running cursor: the
            # stale start was computed against different audio durations and
            # reusing it would overlap neighbours. First-time synth respects
            # a user-supplied start.
            was_synthesized = line.audio_ref is not None
            audio, track = produce_audio_for_dialogue(
                line, mall, tts=tts, lipsync=lipsync
            )
            line.duration = audio.duration
            if was_synthesized or line.start is None:
                line.start = cursor
            line.viseme_track = _to_ir_viseme_track(track)
            line.word_timings = _to_ir_word_timings(track)
            line.audio_ref = expected_audio_ref
            line.viseme_ref = expected_viseme_ref
            cursor = line.start + audio.duration
    return scene


# -----------------------------------------------------------------------------
# Internals
# -----------------------------------------------------------------------------


def _to_ir_viseme_track(track: VisemeTrack) -> IRVisemeTrack:
    """Translate the audio-side dataclass to the IR's Pydantic model."""
    return IRVisemeTrack(
        keyframes=[VisemeKeyframe(time=v.time, viseme=v.code) for v in track.visemes]
    )


def _to_ir_word_timings(track: VisemeTrack) -> list[WordTimingIR] | None:
    """The track's word timings as IR models, or ``None`` when it has none."""
    if track.words is None:
        return None
    return [WordTimingIR(text=w[0], start=float(w[1]), end=float(w[2])) for w in track.words]


def _emits_word_timings(lipsync: LipSyncProvider) -> bool:
    """Whether ``lipsync`` declares that it fills ``VisemeTrack.words``."""
    return bool(getattr(lipsync, "emits_word_timings", False))


def _load_or_synthesize(
    tts: TTSProvider,
    text: str,
    voice_id: str,
    mall: Mapping[str, MutableMapping] | None,
    cache_key: str,
) -> AudioClip:
    if mall is not None and "audio" in mall and cache_key in mall["audio"]:
        wav_bytes = mall["audio"][cache_key]
        # Re-derive duration from WAV header for fidelity.
        duration = _wav_duration(wav_bytes)
        return AudioClip(
            bytes_=wav_bytes, duration=duration, voice_id=voice_id, transcript=text
        )
    clip = tts.synthesize(text, voice_id)
    if mall is not None and "audio" in mall and clip.bytes_ is not None:
        mall["audio"][cache_key] = clip.bytes_
    return clip


def _load_or_align(
    lipsync: LipSyncProvider,
    audio: AudioClip,
    transcript: str,
    mall: Mapping[str, MutableMapping] | None,
    cache_key: str,
) -> VisemeTrack:
    if mall is not None and "visemes" in mall and cache_key in mall["visemes"]:
        try:
            payload = json.loads(mall["visemes"][cache_key].decode("utf-8"))
            words = payload.get("words")
            cached = VisemeTrack(
                visemes=[
                    Viseme(
                        time=v["time"],
                        code=v["code"],
                        intensity=v.get("intensity", 1.0),
                    )
                    for v in payload.get("visemes", [])
                ],
                convention=payload.get("convention", lipsync.convention),
                duration=payload.get("duration", audio.duration),
                words=(
                    [(str(w[0]), float(w[1]), float(w[2])) for w in words]
                    if words is not None
                    else None
                ),
            )
            # A payload written before an#96 by a provider that HAS words is
            # missing them; re-align once so the sidecar carries them. The key
            # is the same, so the rewrite below replaces the old payload.
            if cached.words is not None or not _emits_word_timings(lipsync):
                return cached
        except Exception:
            # Fall through to recompute; cache content was malformed.
            pass
    track = lipsync.align(audio, transcript)
    if mall is not None and "visemes" in mall:
        payload = {
            "visemes": [asdict(v) for v in track.visemes],
            "convention": track.convention,
            "duration": track.duration,
            "words": (
                [[w[0], float(w[1]), float(w[2])] for w in track.words]
                if track.words is not None
                else None
            ),
        }
        mall["visemes"][cache_key] = json.dumps(payload).encode("utf-8")
    return track


def _wav_duration(wav_bytes: bytes) -> float:
    """Compatibility shim: duration of cached audio bytes.

    Tries the stdlib `wave` module first (fast, no subprocess). Falls back to
    ffprobe for non-WAV containers (mp3 from ElevenLabs etc.). Returns 0.0
    if both fail; the renderer will still mux the audio fine because ffmpeg
    sniffs format itself.
    """
    import io
    import wave

    try:
        with wave.open(io.BytesIO(wav_bytes), "rb") as wf:
            n = wf.getnframes()
            rate = wf.getframerate()
            return n / rate if rate else 0.0
    except wave.Error:
        return _ffprobe_duration(wav_bytes)


def _ffprobe_duration(audio_bytes: bytes) -> float:
    """Use ffprobe to read the duration of an arbitrary audio container."""
    import shutil
    import subprocess
    import tempfile

    if shutil.which("ffprobe") is None:
        return 0.0
    with tempfile.NamedTemporaryFile(delete=False, suffix=".bin") as tmp:
        tmp.write(audio_bytes)
        tmp_path = tmp.name
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                tmp_path,
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        s = result.stdout.strip()
        return float(s) if s else 0.0
    except Exception:
        return 0.0
    finally:
        import os

        try:
            os.unlink(tmp_path)
        except OSError:
            pass
