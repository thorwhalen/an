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

from an.audio.lipsync import LipSyncProvider, VisemeTrack
from an.audio.offline_lipsync import OfflineLipSync
from an.audio.offline_tts import OfflineTTS
from an.audio.tts import AudioClip, TTSProvider
from an.ir.schema import Dialogue, SceneIR, VisemeKeyframe
from an.ir.schema import VisemeTrack as IRVisemeTrack
from an.util import _stable_hash


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
        for line in shot.dialogue:
            already_done = (
                line.viseme_track is not None
                and line.duration is not None
                and line.audio_ref is not None
            )
            if already_done:
                cursor = (line.start or cursor) + line.duration
                continue
            audio, track = produce_audio_for_dialogue(
                line, mall, tts=tts, lipsync=lipsync
            )
            line.duration = audio.duration
            if line.start is None:
                line.start = cursor
            line.viseme_track = _to_ir_viseme_track(track)
            # Same content hash as in produce_audio_for_dialogue so the
            # renderer can find the audio bytes in mall["audio"].
            line.audio_ref = _stable_hash(
                {
                    "text": line.text,
                    "voice": line.voice_ref or voice_default,
                    "tts": tts.name,
                }
            )
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
            return VisemeTrack(
                visemes=[
                    __import__("an.audio.lipsync", fromlist=["Viseme"]).Viseme(
                        time=v["time"],
                        code=v["code"],
                        intensity=v.get("intensity", 1.0),
                    )
                    for v in payload.get("visemes", [])
                ],
                convention=payload.get("convention", lipsync.convention),
                duration=payload.get("duration", audio.duration),
            )
        except Exception:
            # Fall through to recompute; cache content was malformed.
            pass
    track = lipsync.align(audio, transcript)
    if mall is not None and "visemes" in mall:
        payload = {
            "visemes": [asdict(v) for v in track.visemes],
            "convention": track.convention,
            "duration": track.duration,
        }
        mall["visemes"][cache_key] = json.dumps(payload).encode("utf-8")
    return track


def _wav_duration(wav_bytes: bytes) -> float:
    import io
    import wave

    with wave.open(io.BytesIO(wav_bytes), "rb") as wf:
        n = wf.getnframes()
        rate = wf.getframerate()
        return n / rate if rate else 0.0
