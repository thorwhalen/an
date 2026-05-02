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

from an.audio.lipsync import LipSyncProvider, Viseme, VisemeTrack
from an.audio.tts import AudioClip


# Reuse the offline char→viseme mapping so the two providers stay consistent.
from an.audio.offline_lipsync import _CHAR_TO_VISEME, _REST_VISEME


_DEFAULT_MODEL_SIZE: str = "tiny"  # ~75 MB; "base" gives slightly better word-timing
_DEFAULT_DEVICE: str = "cpu"
_DEFAULT_COMPUTE_TYPE: str = "int8"
_MIN_WORD_GAP_FOR_REST: float = 0.20  # seconds; insert rest viseme in gaps wider than this


class WhisperLipSync:
    """faster-whisper word timestamps → visemes.

    Implements the ``LipSyncProvider`` protocol. The model is lazy-loaded on
    the first call (subsequent calls in the same process reuse the instance
    via the class-level ``_model`` cache).
    """

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
            words: list[tuple[float, float, str]] = []
            for seg in segments:
                for w in (seg.words or []):
                    words.append((float(w.start), float(w.end), w.word))
        finally:
            if cleanup is not None:
                try:
                    cleanup.unlink()
                except OSError:
                    pass

        return VisemeTrack(
            visemes=list(self._visemes_for_words(words, audio.duration)),
            convention=self.convention,
            duration=audio.duration,
        )

    def _visemes_for_words(
        self, words: list[tuple[float, float, str]], total_duration: float
    ) -> Iterable[Viseme]:
        """Distribute visemes across word boundaries, with rest in gaps.

        Times are clamped to ``[0, total_duration]`` since whisper occasionally
        rounds word ends slightly past the audio's actual length.
        """
        max_t = max(0.0, total_duration if total_duration > 0 else 0.0)

        def clamp(t: float) -> float:
            return max(0.0, min(t, max_t))

        yield Viseme(time=0.0, code=_REST_VISEME)
        prev_end = 0.0
        for w_start, w_end, raw_word in words:
            w_start = clamp(w_start)
            w_end = clamp(w_end)
            # Insert a rest in the silent gap before this word (if wide enough).
            if w_start - prev_end > _MIN_WORD_GAP_FOR_REST:
                yield Viseme(time=clamp(prev_end + 0.05), code=_REST_VISEME)
            codes = list(self._codes_for_word(raw_word))
            if not codes:
                prev_end = w_end
                continue
            n = len(codes)
            duration = max(0.05, w_end - w_start)
            step = duration / n
            for i, code in enumerate(codes):
                yield Viseme(time=clamp(w_start + i * step), code=code)
            prev_end = w_end
        # Trailing rest at the end.
        yield Viseme(time=max_t, code=_REST_VISEME)

    def _codes_for_word(self, word: str) -> Iterable[str]:
        """Per-character viseme codes inside a single word, deduped."""
        previous: str | None = None
        for ch in word.lower().strip():
            if not ch.isalpha():
                continue
            code = self._mapping.get(ch, "C")
            if code == previous:
                continue
            previous = code
            yield code
