"""RhubarbLipSync — calls the rhubarb-lip-sync binary for phoneme-aligned visemes.

Requires the ``rhubarb`` binary on PATH. macOS: ``brew install rhubarb-lipsync``.
Linux/Windows: download from the project's GitHub releases.

Falls back gracefully (raises a clear error) if the binary is missing — the
default ``OfflineLipSync`` keeps the pipeline functional in the meantime.
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
_RECOGNIZER: str = "phonetic"  # works well without a dialog file


class RhubarbLipSync:
    """Wrap the rhubarb CLI. Implements the ``LipSyncProvider`` protocol."""

    name: str = "rhubarb"
    convention: str = "rhubarb"

    def __init__(
        self,
        *,
        binary_path: str | None = None,
        recognizer: str = _RECOGNIZER,
        timeout_s: float = _DEFAULT_TIMEOUT_S,
    ) -> None:
        self.binary_path = binary_path or shutil.which("rhubarb")
        self.recognizer = recognizer
        self.timeout_s = timeout_s

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

            dialog_path = d / "transcript.txt"
            dialog_path.write_text(transcript, encoding="utf-8")
            out_json = d / "out.json"

            cmd = [
                self.binary_path,
                "-f", "json",
                "-r", self.recognizer,
                "--dialogFile", str(dialog_path),
                "-o", str(out_json),
                str(audio_path),
            ]
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
        visemes = [
            Viseme(time=float(c["start"]), code=str(c["value"]))
            for c in cues
        ]
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
