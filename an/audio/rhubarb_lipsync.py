"""RhubarbLipSync — calls the rhubarb-lip-sync binary for phoneme-aligned visemes.

Requires the ``rhubarb`` binary on PATH. macOS: ``brew install rhubarb-lipsync``.
Linux/Windows: download from the project's GitHub releases.

Falls back gracefully (raises a clear error) if the binary is missing — the
default ``OfflineLipSync`` keeps the pipeline functional in the meantime.

**The recognizer follows the language** (an#96, epic #9 defect 5a). Rhubarb has
two: ``pocketSphinx`` — its default, "use for English recordings", the only one
that reads ``--dialogFile`` (it builds a dialog language model and mixes it 90/10
with the default) — and ``phonetic``, "use for non-English recordings", which
``UNUSED(dialog)``s the transcript at source. This module used to pass
``-r phonetic`` **and** ``--dialogFile`` unconditionally: English speech from an
English transcript ran the language-independent recognizer and the transcript
it wrote to disk was never read. Now ``recognizer=None`` (the default) resolves
per ``language`` — ``"en"`` → ``pocketSphinx`` with the dialog file, anything
else → ``phonetic`` and **no transcript is written** (a file nothing reads is a
lie waiting for the next reader). An explicit ``recognizer`` still overrides.
``name`` carries the recognizer so the viseme cache key changes with it and no
stale ``phonetic`` track replays.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from pathlib import Path

from an.audio.lipsync import Viseme, VisemeTrack
from an.audio.tts import AudioClip


_DEFAULT_TIMEOUT_S: float = 60.0
#: Rhubarb's own default and its English recognizer — the one that reads the
#: dialog file.
_ENGLISH_RECOGNIZER: str = "pocketSphinx"
#: Language-independent; ignores the dialog file at source.
_PHONETIC_RECOGNIZER: str = "phonetic"
#: The languages `pocketSphinx` (CMU Sphinx US English acoustic model) covers.
ENGLISH_LANGUAGES: frozenset[str] = frozenset({"en"})
RECOGNIZERS: frozenset[str] = frozenset({_ENGLISH_RECOGNIZER, _PHONETIC_RECOGNIZER})


def recognizer_for(language: str) -> str:
    """The Rhubarb recognizer for a BCP-47 language tag (primary subtag only).

    >>> recognizer_for("en"), recognizer_for("en-GB"), recognizer_for("fr")
    ('pocketSphinx', 'pocketSphinx', 'phonetic')
    """
    primary = language.split("-", 1)[0].lower()
    return _ENGLISH_RECOGNIZER if primary in ENGLISH_LANGUAGES else _PHONETIC_RECOGNIZER


class RhubarbLipSync:
    """Wrap the rhubarb CLI. Implements the ``LipSyncProvider`` protocol.

    >>> RhubarbLipSync(binary_path="/bin/rhubarb").recognizer
    'pocketSphinx'
    >>> RhubarbLipSync(binary_path="/bin/rhubarb", language="de").recognizer
    'phonetic'
    >>> RhubarbLipSync(binary_path="/bin/rhubarb", language="de").name
    'rhubarb:phonetic'
    """

    convention: str = "rhubarb"

    def __init__(
        self,
        *,
        binary_path: str | None = None,
        language: str = "en",
        recognizer: str | None = None,
        timeout_s: float = _DEFAULT_TIMEOUT_S,
    ) -> None:
        self.binary_path = binary_path or shutil.which("rhubarb")
        self.language = language
        chosen = recognizer if recognizer is not None else recognizer_for(language)
        if chosen not in RECOGNIZERS:
            raise ValueError(
                f"unknown rhubarb recognizer {chosen!r}; known: {sorted(RECOGNIZERS)}"
            )
        self.recognizer = chosen
        self.timeout_s = timeout_s

    @property
    def name(self) -> str:
        # The recognizer is part of the identity: it changes the track, so it
        # must change the viseme cache key (the pipeline hashes `name`).
        return f"rhubarb:{self.recognizer}"

    @property
    def uses_dialog_file(self) -> bool:
        """Whether the chosen recognizer reads a transcript at all."""
        return self.recognizer == _ENGLISH_RECOGNIZER

    def align(self, audio: AudioClip, transcript: str) -> VisemeTrack:
        if not self.binary_path:
            raise RuntimeError(
                "rhubarb binary not found on PATH. Install with: "
                "brew install rhubarb-lipsync (macOS) or grab a release from "
                "https://github.com/DanielSWolf/rhubarb-lip-sync/releases."
            )
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            audio_path = audio.path
            if audio_path is None:
                if audio.bytes_ is None:
                    raise ValueError("AudioClip needs either .path or .bytes_")
                audio_path = d / "audio.wav"
                audio_path.write_bytes(audio.bytes_)

            out_json = d / "out.json"
            cmd = [self.binary_path, "-f", "json", "-r", self.recognizer]
            if self.uses_dialog_file:
                dialog_path = d / "transcript.txt"
                dialog_path.write_text(transcript, encoding="utf-8")
                cmd += ["--dialogFile", str(dialog_path)]
            cmd += ["-o", str(out_json), str(audio_path)]
            try:
                subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=self.timeout_s,
                    check=True,
                )
            except subprocess.CalledProcessError as e:
                raise RuntimeError(
                    f"rhubarb failed (rc={e.returncode}): {e.stderr}"
                ) from e
            data = json.loads(out_json.read_text(encoding="utf-8"))

        cues = data.get("mouthCues", [])
        visemes = [Viseme(time=float(c["start"]), code=str(c["value"])) for c in cues]
        if cues:
            # Append a final rest at the last cue's "end" so the track matches duration.
            last_end = float(cues[-1]["end"])
            if not visemes or visemes[-1].code != "X":
                visemes.append(Viseme(time=last_end, code="X"))
        return VisemeTrack(
            visemes=visemes,
            convention=self.convention,
            duration=audio.duration,
        )
