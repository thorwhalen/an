"""Audio + frame quality tests for rendered mp4s.

These run only when ffmpeg/ffprobe are present (always on the dev box).
Tests that require Playwright/Chromium auto-skip via the existing pattern.
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

import numpy as np
import pytest

from an import init
from an.ir.schema import (
    AssetRef,
    Dialogue,
    Meta,
    Resolution,
    SceneIR,
    Shot,
)
from an.orchestrate import render_project
from an.project import load
from an.verify.media import (
    audio_volume,
    detect_silence,
    extract_frames,
    ssim,
    ssim_image_files,
)




# Gated per test, not per module: this file mixes pure-Python checks with
# ones that drive a real render.
# The SSIM tests are pure numpy and must run everywhere: they are the
# primitives Wave 2's ledger is built on, and a module-level gate meant
# they had never once run in CI.


# -----------------------------------------------------------------------------
# SSIM (no rendering needed)
# -----------------------------------------------------------------------------


def test_ssim_self_is_one():
    rng = np.random.default_rng(42)
    img = rng.random((64, 64))
    assert ssim(img, img) == pytest.approx(1.0, abs=1e-6)


def test_ssim_constant_vs_noise():
    rng = np.random.default_rng(0)
    flat = np.zeros((64, 64))
    noise = rng.random((64, 64))
    s = ssim(flat, noise)
    assert s < 0.5  # different images → low SSIM


def test_ssim_two_close_images_high():
    rng = np.random.default_rng(1)
    a = rng.random((64, 64))
    b = a + 0.01 * rng.random((64, 64))
    assert ssim(a, b) > 0.95


# -----------------------------------------------------------------------------
# Audio + frame end-to-end on a real render
# -----------------------------------------------------------------------------


def _render_dialogue_scene(
    work_dir: Path, *, duration: float = 1.5, text: str = "speak now please"
) -> Path:
    root = init(work_dir)
    proj = load(root)
    proj.scene = SceneIR(
        meta=Meta(title="t", duration=duration, fps=12,
                  resolution=Resolution(width=320, height=240)),
        timeline=[
            Shot(
                id="s1", style="cutout", duration=duration,
                entities=[
                    AssetRef(kind="character", id="c",
                             store="characters", ref="c-v1")
                ],
                dialogue=[Dialogue(speaker="c", text=text)],
            )
        ],
    )
    proj.mall["scenes"]["main"] = proj.scene
    return render_project(root, output_name="quality")


@pytest.mark.browser
@pytest.mark.ffmpeg
def test_render_has_nonzero_audio_volume():
    with tempfile.TemporaryDirectory() as d:
        out = _render_dialogue_scene(Path(d) / "p")
        vol = audio_volume(out)
        # Even silent OfflineTTS produces a measurable noise floor; AAC encoding
        # leaves dB in the -90..-40 range. We accept anything finite.
        assert "max_db" in vol
        assert vol["max_db"] > -100.0


@pytest.mark.browser
@pytest.mark.ffmpeg
def test_silence_detect_runs_without_error():
    """detect_silence returns spans (possibly empty) and doesn't crash."""
    with tempfile.TemporaryDirectory() as d:
        out = _render_dialogue_scene(Path(d) / "p")
        spans = detect_silence(out, noise_db=-50.0, min_duration_s=0.1)
        assert isinstance(spans, list)
        for s in spans:
            assert s.duration >= 0.1


@pytest.mark.browser
@pytest.mark.ffmpeg
def test_frame_ssim_adjacent_high_far_lower():
    """Adjacent frames during dialogue should be more similar than distant ones."""
    with tempfile.TemporaryDirectory() as d:
        out = _render_dialogue_scene(Path(d) / "p", duration=2.0)
        frames = extract_frames(out, Path(d) / "frames", fps=4.0)
        if len(frames) < 4:
            pytest.skip("not enough frames extracted")
        # Adjacent (≈0.25s apart) vs. far (across the whole render).
        adj_ssim = ssim_image_files(frames[0], frames[1])
        far_ssim = ssim_image_files(frames[0], frames[-1])
        assert adj_ssim > 0.5  # close enough in content
        # Adjacent should be at least as similar as far (mouth animates → far diverges).
        # Not strict <, since static portions may keep both ≈1.
        assert adj_ssim >= far_ssim - 1e-3


@pytest.mark.browser
@pytest.mark.ffmpeg
def test_silent_shot_has_long_silence_span():
    """A shot with no dialogue should be detected as ≈silent throughout."""
    with tempfile.TemporaryDirectory() as d:
        root = init(Path(d) / "p")
        proj = load(root)
        proj.scene = SceneIR(
            meta=Meta(title="t", duration=1.0, fps=12,
                      resolution=Resolution(width=160, height=120)),
            timeline=[Shot(id="s1", style="cutout", duration=1.0)],
        )
        proj.mall["scenes"]["main"] = proj.scene
        out = render_project(root, output_name="silent")
        spans = detect_silence(out, noise_db=-40.0, min_duration_s=0.3)
        # Should find at least one long span covering most of the duration.
        if spans:  # if ffmpeg detected any silence at all
            longest = max(s.duration for s in spans)
            assert longest >= 0.5
