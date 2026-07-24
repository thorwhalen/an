"""Media verification helpers — audio + frame quality checks for rendered mp4s.

Phase 8 Tier 2. These complement the existing IR-only ``LayoutLintVerifier``
by inspecting actual mp4 output: silence detection (catches cutoff dialogue),
audio level (catches missing audio), and per-frame perceptual diff via SSIM
(catches "all frames are identical" or "scene drifted between frames").

Designed to depend only on ``ffmpeg`` / ``ffprobe`` and ``numpy`` (Pillow when
loading frames). No scikit-image, no opencv.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

import numpy as np


# -----------------------------------------------------------------------------
# Audio
# -----------------------------------------------------------------------------


@dataclass(slots=True, frozen=True)
class SilenceSpan:
    """A contiguous run of near-silence inside an audio stream."""

    start: float
    end: float

    @property
    def duration(self) -> float:
        return self.end - self.start


def detect_silence(
    media_path: str | Path,
    *,
    noise_db: float = -30.0,
    min_duration_s: float = 0.3,
) -> list[SilenceSpan]:
    """Return ``SilenceSpan``s in the audio of ``media_path`` via ffmpeg.

    Wraps ``ffmpeg -af silencedetect=...`` and parses the stderr "silence_start"
    / "silence_end" lines. Useful for catching dialogue that got cut off
    (silence at the start/end of a shot when speech was expected).
    """
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-nostats",
        "-i",
        str(media_path),
        "-af",
        f"silencedetect=noise={noise_db}dB:d={min_duration_s}",
        "-f",
        "null",
        "-",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    text = result.stderr
    starts = [float(m) for m in re.findall(r"silence_start: ([0-9.]+)", text)]
    ends = [float(m) for m in re.findall(r"silence_end: ([0-9.]+) ", text)]
    spans: list[SilenceSpan] = []
    for s, e in zip(starts, ends):
        spans.append(SilenceSpan(start=s, end=e))
    # Trailing silence with no end — ffmpeg sometimes omits it; pad with a
    # synthetic span to media duration so callers can detect "ran out of audio".
    if len(starts) > len(ends):
        spans.append(SilenceSpan(start=starts[-1], end=_media_duration(media_path)))
    return spans


def audio_volume(media_path: str | Path) -> dict[str, float]:
    """Return dict with mean_db and max_db of the media's audio stream."""
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-nostats",
        "-i",
        str(media_path),
        "-af",
        "volumedetect",
        "-f",
        "null",
        "-",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    text = result.stderr
    mean = re.search(r"mean_volume: (-?[0-9.]+) dB", text)
    mx = re.search(r"max_volume: (-?[0-9.]+) dB", text)
    out: dict[str, float] = {}
    if mean:
        out["mean_db"] = float(mean.group(1))
    if mx:
        out["max_db"] = float(mx.group(1))
    return out


def _media_duration(media_path: str | Path) -> float:
    """Return the media's duration in seconds via ffprobe."""
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(media_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    s = result.stdout.strip()
    return float(s) if s else 0.0


# -----------------------------------------------------------------------------
# Frame perceptual diff (SSIM)
# -----------------------------------------------------------------------------


def ssim(a: np.ndarray, b: np.ndarray) -> float:
    """Mean SSIM between two single-channel images (float arrays in [0, 1]).

    Numpy-only implementation of Wang et al.'s SSIM. Computes on luminance,
    no per-window sliding (uses global means/variances) — fast and robust
    enough for "are these two frames structurally similar" checks.

    Returns a float in roughly ``[-1, 1]``; 1 means identical.

    >>> import numpy as np
    >>> x = np.zeros((8, 8), dtype=np.float32)
    >>> ssim(x, x)
    1.0
    """
    a = a.astype(np.float64)
    b = b.astype(np.float64)
    mu_a = a.mean()
    mu_b = b.mean()
    var_a = a.var()
    var_b = b.var()
    cov = ((a - mu_a) * (b - mu_b)).mean()
    c1 = (0.01) ** 2
    c2 = (0.03) ** 2
    num = (2 * mu_a * mu_b + c1) * (2 * cov + c2)
    den = (mu_a**2 + mu_b**2 + c1) * (var_a + var_b + c2)
    if den == 0:
        return 1.0 if num == 0 else 0.0
    return float(num / den)


def ssim_image_files(path_a: str | Path, path_b: str | Path) -> float:
    """SSIM between two image files (any format Pillow can read)."""
    from PIL import Image

    a = np.asarray(Image.open(path_a).convert("L"), dtype=np.float64) / 255.0
    b = np.asarray(Image.open(path_b).convert("L"), dtype=np.float64) / 255.0
    if a.shape != b.shape:
        raise ValueError(f"frame size mismatch: {a.shape} vs {b.shape}")
    return ssim(a, b)


def extract_frames(
    media_path: str | Path,
    out_dir: str | Path,
    *,
    fps: float = 4.0,
    pattern: str = "frame_%04d.png",
) -> list[Path]:
    """Extract frames from ``media_path`` at ``fps`` to ``out_dir``."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg",
        "-y",
        "-loglevel",
        "error",
        "-i",
        str(media_path),
        "-vf",
        f"fps={fps}",
        str(out_dir / pattern),
    ]
    subprocess.run(cmd, check=True, capture_output=True)
    return sorted(out_dir.glob("frame_*.png"))


# -----------------------------------------------------------------------------
# Optional transcript verification via faster-whisper
# -----------------------------------------------------------------------------


def transcribe(media_path: str | Path, *, model_size: str = "tiny") -> str:
    """Return the transcribed speech in ``media_path``.

    Lazily imports faster-whisper. Raises ``RuntimeError`` with a clear
    message when the package isn't installed.
    """
    try:
        from faster_whisper import WhisperModel  # type: ignore
    except ImportError as e:
        raise RuntimeError(
            "faster-whisper not installed. pip install faster-whisper "
            "(or skip transcript checks)."
        ) from e
    model = WhisperModel(model_size, device="cpu", compute_type="int8")
    segments, _info = model.transcribe(str(media_path))
    return " ".join(s.text.strip() for s in segments).strip()
