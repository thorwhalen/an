"""MediaQualityVerifier — post-render quality checks on the actual mp4.

Phase 9. Implements the ``Verifier`` Protocol using helpers from
``an.verify.media``. Runs only when given a ``RenderResult`` (skips silently
on pre-render calls). Adds three signals to the orchestrator's verify pass:

1. **Audio level**: max dB above a floor — catches "AAC stream present but
   silent because the TTS produced empty bytes".
2. **Silence vs. dialogue**: if the IR contains dialogue, the rendered
   audio shouldn't be near-silent for most of the duration.
3. **Frame motion**: the mean SSIM between adjacent sampled frames
   shouldn't be ~1.0 — a frozen render reads as flat.

Each check produces a ``Finding`` whose severity is "warning" so the run
proceeds; the orchestrator can decide whether to surface or block.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from an.adapters._base import RenderResult
from an.ir.schema import SceneIR
from an.verify._base import VerificationReport
from an.verify.media import (
    audio_volume,
    detect_silence,
    extract_frames,
    ssim_image_files,
)


_MAX_DB_FLOOR: float = -75.0  # below this max_db, audio is effectively silent
_DIALOGUE_SILENCE_RATIO: float = 0.7  # >70% silence in a dialogue render = bad
_FROZEN_SSIM_THRESHOLD: float = 0.999  # adjacent-frame mean SSIM above this = frozen
_FRAME_SAMPLE_FPS: float = 4.0  # how densely to sample frames for the SSIM check


class MediaQualityVerifier:
    """Post-render quality checks. Implements ``Verifier``."""

    name: str = "media_quality"

    def __init__(
        self,
        *,
        max_db_floor: float = _MAX_DB_FLOOR,
        dialogue_silence_ratio: float = _DIALOGUE_SILENCE_RATIO,
        frozen_ssim_threshold: float = _FROZEN_SSIM_THRESHOLD,
        frame_sample_fps: float = _FRAME_SAMPLE_FPS,
    ) -> None:
        self.max_db_floor = max_db_floor
        self.dialogue_silence_ratio = dialogue_silence_ratio
        self.frozen_ssim_threshold = frozen_ssim_threshold
        self.frame_sample_fps = frame_sample_fps

    def verify(self, ir: SceneIR, render: RenderResult | None) -> VerificationReport:
        report = VerificationReport()
        if render is None or not render.mp4_path or not render.mp4_path.exists():
            report.add(
                "info", "<media>", "no render result; skipping media quality checks"
            )
            return report

        # 1. Audio volume
        try:
            vol = audio_volume(render.mp4_path)
            max_db = vol.get("max_db", float("-inf"))
            if max_db < self.max_db_floor:
                report.add(
                    "warning",
                    "<audio>",
                    f"max audio level {max_db:.1f} dB is below floor "
                    f"{self.max_db_floor:.1f} — audio likely silent",
                    suggested_fix="check that the TTS provider returned non-empty audio",
                )
        except Exception as e:
            report.add("info", "<audio>", f"audio_volume probe failed: {e!r}")

        # 2. Silence vs. dialogue
        has_dialogue = any(line for shot in ir.timeline for line in shot.dialogue)
        if has_dialogue and render.duration > 0:
            try:
                spans = detect_silence(
                    render.mp4_path, noise_db=-40.0, min_duration_s=0.4
                )
                total_silence = sum(s.duration for s in spans)
                ratio = total_silence / render.duration
                if ratio > self.dialogue_silence_ratio:
                    report.add(
                        "warning",
                        "<audio/dialogue>",
                        f"{ratio * 100:.0f}% of the render is silent but the IR has "
                        f"dialogue lines — speech may be missing or cut off",
                        suggested_fix="rerun with --tts elevenlabs (offline TTS is silent)",
                    )
            except Exception as e:
                report.add("info", "<audio/dialogue>", f"silence probe failed: {e!r}")

        # 3. Frame motion via mean SSIM
        try:
            with tempfile.TemporaryDirectory() as d:
                frames = extract_frames(render.mp4_path, d, fps=self.frame_sample_fps)
                if len(frames) >= 2:
                    ssims = [
                        ssim_image_files(frames[i], frames[i + 1])
                        for i in range(len(frames) - 1)
                    ]
                    mean_ssim = sum(ssims) / len(ssims)
                    if mean_ssim > self.frozen_ssim_threshold:
                        report.add(
                            "warning",
                            "<video>",
                            f"mean adjacent-frame SSIM is {mean_ssim:.4f}; "
                            f"frames are nearly identical — render may be frozen",
                            suggested_fix="confirm the IR has actions/visemes that drive motion",
                        )
        except Exception as e:
            report.add("info", "<video>", f"frame motion probe failed: {e!r}")

        return report
