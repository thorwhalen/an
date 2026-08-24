"""WhisperLipSync — faster-whisper word timestamps → viseme keyframes.

Phase 9. Bridges the gap between deterministic ``OfflineLipSync`` (twitchy,
char-distributed) and the system-binary-dependent ``RhubarbLipSync``. Uses
``faster-whisper`` to transcribe the rendered audio into word-level
timestamps, then distributes visemes within each word's [start, end] span
based on the word's letter→viseme mapping (collapsed-duplicates).

This gives ~75% of Rhubarb-quality lip-sync without any system binaries,
just a ~75 MB model download (cached after first use).

Trade-offs vs. OfflineLipSync:

- **Better**: timing is locked to actual word boundaries. Mouth holds shape
  through silent gaps between words instead of cycling through visemes.
- **Same**: viseme codes per phoneme are still our simple letter mapping;
  no IPA/ARPAbet awareness yet (that's a future upgrade with cmudict).
- **Cost**: ~3–5 seconds CPU inference for a 10s clip on first call;
  subsequent calls in the same process re-use the cached model.
"""

from __future__ import annotations

import io
import tempfile
from pathlib import Path
from typing import Iterable

from an.audio.lipsync import (
    LipSyncProvider,
    Viseme,
    VisemeTrack,
    WordTiming,
    word_timings_to_visemes,
)
from an.audio.tts import AudioClip


# Reuse the offline char→viseme mapping so the two providers stay consistent.
from an.audio.offline_lipsync import _CHAR_TO_VISEME, _REST_VISEME


_DEFAULT_MODEL_SIZE: str = "tiny"  # ~75 MB; "base" gives slightly better word-timing
_DEFAULT_DEVICE: str = "cpu"
_DEFAULT_COMPUTE_TYPE: str = "int8"
_MIN_WORD_GAP_FOR_REST: float = (
    0.20  # seconds; insert rest viseme in gaps wider than this
)


class WhisperLipSync:
    """faster-whisper word timestamps → visemes.

    Implements the ``LipSyncProvider`` protocol. The model is lazy-loaded on
    the first call (subsequent calls in the same process reuse the instance
    via the class-level ``_model`` cache).
    """

    #: Whisper aligns from words, so the track carries them (an#96).
    emits_word_timings: bool = True

    name: str = "whisper"
    convention: str = "rhubarb"

    _model = None  # class-level cache so repeated align() calls share the model

    def __init__(
        self,
        *,
        model_size: str = _DEFAULT_MODEL_SIZE,
        device: str = _DEFAULT_DEVICE,
        compute_type: str = _DEFAULT_COMPUTE_TYPE,
        char_to_viseme: dict[str, str] | None = None,
    ) -> None:
        self.model_size = model_size
        self.device = device
        self.compute_type = compute_type
        self._mapping = (
            dict(char_to_viseme) if char_to_viseme else dict(_CHAR_TO_VISEME)
        )

    def _get_model(self):
        if WhisperLipSync._model is not None:
            return WhisperLipSync._model
        try:
            from faster_whisper import WhisperModel  # type: ignore
        except ImportError as e:
            raise RuntimeError(
                "faster-whisper not installed. pip install faster-whisper "
                "(adds ~100 MB) or use the offline lipsync provider."
            ) from e
        WhisperLipSync._model = WhisperModel(
            self.model_size, device=self.device, compute_type=self.compute_type
        )
        return WhisperLipSync._model

    def align(self, audio: AudioClip, transcript: str) -> VisemeTrack:
        # faster-whisper takes a path or file-like. Materialize bytes to disk
        # if the caller didn't pass a path.
        if audio.path and Path(audio.path).exists():
            audio_path: str | Path = audio.path
            cleanup: Path | None = None
        elif audio.bytes_:
            tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".bin")
            tmp.write(audio.bytes_)
            tmp.close()
            audio_path = tmp.name
            cleanup = Path(tmp.name)
        else:
            raise ValueError("AudioClip needs either .path or .bytes_")

        try:
            model = self._get_model()
            segments, _info = model.transcribe(
                str(audio_path),
                word_timestamps=True,
                # faster-whisper auto-detects language; pass the transcript as
                # an "initial prompt" to bias decoding toward the known text.
                initial_prompt=transcript or None,
            )
            words: list[WordTiming] = []
            for seg in segments:
                for w in seg.words or []:
                    words.append((w.word, float(w.start), float(w.end)))
        finally:
            if cleanup is not None:
                try:
                    cleanup.unlink()
                except OSError:
                    pass

        return VisemeTrack(
            visemes=word_timings_to_visemes(
                words,
                total_duration=audio.duration,
                char_to_viseme=self._mapping,
                rest_viseme=_REST_VISEME,
                min_gap_for_rest=_MIN_WORD_GAP_FOR_REST,
            ),
            convention=self.convention,
            duration=audio.duration,
            words=list(words),
        )
