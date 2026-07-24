"""MacSayTTS — audible offline speech via macOS's built-in ``say`` command.

Free, local, no API keys, no Python deps. macOS-only. The ``say`` binary
ships with every macOS install since at least 10.4. Voices are listed
by ``say -v '?'`` and can be passed as ``voice_id``; common defaults:
``"Samantha"``, ``"Daniel"``, ``"Karen"``, ``"Alex"``.

Output is 16-bit little-endian PCM WAV at 22050 Hz so the rest of the
audio pipeline (which prefers WAV) can read frames + duration directly.

>>> tts = MacSayTTS()
>>> tts.name
'mac_say'
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
import wave
from pathlib import Path
from typing import Iterable

from an.audio.tts import AudioClip, VoiceMeta


_DEFAULT_VOICE_ID: str = "Samantha"
_DEFAULT_SAMPLE_RATE: int = 22050
_DEFAULT_DATA_FORMAT: str = f"LEI16@{_DEFAULT_SAMPLE_RATE}"


class MacSayTTSError(RuntimeError):
    """Raised when the ``say`` subprocess fails."""


class MacSayTTS:
    """macOS ``say``-backed TTSProvider.

    Implements the ``TTSProvider`` protocol. Audible, deterministic, and
    fully offline — uses Apple's voice synthesis bundled with the OS.
    """

    name: str = "mac_say"

    def __init__(
        self,
        *,
        default_voice_id: str = _DEFAULT_VOICE_ID,
        sample_rate: int = _DEFAULT_SAMPLE_RATE,
        rate_wpm: int | None = None,
    ) -> None:
        self.default_voice_id = default_voice_id
        self.sample_rate = sample_rate
        self.rate_wpm = rate_wpm

    def synthesize(self, text: str, voice_id: str | None = None, **kw) -> AudioClip:
        if shutil.which("say") is None:
            raise MacSayTTSError(
                "macOS 'say' command not found on PATH. MacSayTTS only works on "
                "macOS — for cross-platform audible TTS use ElevenLabsTTS."
            )

        effective_voice = (
            voice_id if voice_id and voice_id != "default" else self.default_voice_id
        )

        out_path = kw.get("path")
        if out_path is None:
            tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".wav")
            tmp.close()
            out_path = Path(tmp.name)
        else:
            out_path = Path(out_path)
            out_path.parent.mkdir(parents=True, exist_ok=True)

        cmd = [
            "say",
            "-v",
            effective_voice,
            "-o",
            str(out_path),
            "--data-format",
            f"LEI16@{self.sample_rate}",
        ]
        if self.rate_wpm is not None:
            cmd.extend(["-r", str(self.rate_wpm)])
        cmd.append(text)

        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
        except FileNotFoundError as e:
            raise MacSayTTSError(f"failed to launch 'say': {e}") from e
        if proc.returncode != 0:
            raise MacSayTTSError(
                f"'say' exited with code {proc.returncode}.\n"
                f"  cmd: {' '.join(cmd[:-1])} <text>\n"
                f"  stderr: {proc.stderr.strip()}\n"
                f"  hint: voice '{effective_voice}' may not be installed; "
                f"list voices with: say -v '?'"
            )

        wav_bytes = out_path.read_bytes()
        try:
            with wave.open(str(out_path), "rb") as wf:
                duration = wf.getnframes() / wf.getframerate()
                channels = wf.getnchannels()
        except wave.Error:
            duration = 0.0
            channels = 1

        return AudioClip(
            path=out_path if kw.get("path") else None,
            bytes_=wav_bytes,
            duration=duration,
            sample_rate=self.sample_rate,
            channels=channels,
            voice_id=effective_voice,
            transcript=text,
        )

    def list_voices(self) -> Iterable[VoiceMeta]:
        if shutil.which("say") is None:
            return []
        try:
            out = subprocess.run(
                ["say", "-v", "?"], capture_output=True, text=True, check=False
            )
        except FileNotFoundError:
            return []
        voices: list[VoiceMeta] = []
        for line in out.stdout.splitlines():
            parts = line.split("#", 1)
            head = parts[0].rstrip()
            if not head:
                continue
            tokens = head.split()
            if len(tokens) < 2:
                continue
            language = tokens[-1]
            name = " ".join(tokens[:-1])
            voices.append(
                VoiceMeta(
                    voice_id=name,
                    name=name,
                    provider=self.name,
                    language=language.replace("_", "-"),
                )
            )
        return voices
