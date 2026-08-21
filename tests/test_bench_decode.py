"""The decode calibration — the bench's single largest risk, asserted (an#36).

Every encode-side metric is `f(source[i], decoded[i])`. If the two legs are
decoded in different colour ranges or matrices, all of them measure that
mismatch and report it as encoder damage — and they do so with plausible,
monotone numbers, which is why this needs an assertion rather than a comment.

Measured on this repo: against a mathematically lossless (`-qp 0`) encode of
the same PNGs, the pinned source decode gives a luma residual of **0.0000,
max 0**, and the obvious unpinned spelling gives 5.33. The gap is an order of
magnitude larger than the crf18->23 signal these metrics exist to see.

Worse, the natural fix does not work on the natural spelling: ffmpeg **silently
ignores** the `scale` filter's `out_color_matrix` / `out_range` options for
`-pix_fmt gray`, so research §1.4's literal pseudocode reintroduces the defect
with a fix applied that does nothing.

Needs ffmpeg, not a browser: the frames are synthesised here.
"""

from __future__ import annotations

import struct
import zlib
from pathlib import Path

import numpy as np
import pytest

from an.bench import imageio
from an.bench.run import BenchError, decode_calibration

pytestmark = [pytest.mark.ffmpeg]

H, W, N = 48, 64, 4
FPS = 24

#: Saturated fills against black outlines: the gamut extremes flat 2D line art
#: is made of, and precisely where an RGB round trip clips.
def _write_png(path: Path, arr: np.ndarray) -> None:
    h, w, _ = arr.shape
    raw = b"".join(b"\x00" + arr[y].tobytes() for y in range(h))

    def chunk(tag: bytes, data: bytes) -> bytes:
        body = tag + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))

    path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw, 9))
        + chunk(b"IEND", b"")
    )


@pytest.fixture(scope="module")
def frames_dir(tmp_path_factory) -> Path:
    d = tmp_path_factory.mktemp("bench-frames")
    for i in range(N):
        a = np.full((H, W, 3), 255, np.uint8)
        a[8:40, 8 + i : 40 + i] = (255, 0, 0)
        a[8:40, 6 + i : 8 + i] = (0, 0, 0)
        a[16:24, 44:60] = (0, 200, 60)
        _write_png(d / f"frame_{i:06d}.png", a)
    return d


def test_the_pinned_decode_reads_a_lossless_encode_back_exactly(frames_dir, tmp_path):
    """A hard equality, not a tolerance. Any nonzero value is a mismatch."""
    report = decode_calibration(frames_dir, FPS, height=H, width=W)
    assert report["luma_residual_max"] == 0
    assert report["luma_residual_mean"] == 0.0


def test_the_unpinned_decode_would_report_a_conversion_as_encoder_damage(
    frames_dir, tmp_path
):
    """The mutation this guard exists for, run rather than described.

    If someone "simplifies" the `scale` filter away, the source leg is decoded
    full-range with the wrong matrix, and every encode-side metric inherits it.
    """
    mp4 = tmp_path / "lossless.mp4"
    imageio.run_raw(imageio.lossless_encode_command(frames_dir, FPS, mp4))
    dec = imageio.decoded_yuv(mp4, height=H, width=W)

    unpinned_cmd = [
        c for c in imageio.source_yuv_command(frames_dir)
        if c not in ("-vf", imageio.SOURCE_SCALE_FILTER)
    ]
    unpinned = np.frombuffer(imageio.run_raw(unpinned_cmd), np.uint8).reshape(
        -1, 3, H, W
    )
    residual = np.abs(dec[:, 0].astype(np.int16) - unpinned[:, 0].astype(np.int16))
    assert residual.mean() > 1.0, (
        f"the unpinned decode read a residual of {residual.mean():.4f}; if this "
        "ever drops to zero, ffmpeg's defaults changed and the pin may no "
        "longer be doing what this test claims"
    )


def test_the_gray_pixel_format_silently_ignores_the_range_and_matrix_options(
    frames_dir,
):
    """Research §1.4's literal pseudocode reads the luma with `-pix_fmt gray`.

    ffmpeg accepts the `scale` filter's `out_color_matrix` / `out_range` there
    and does nothing with them — so the natural fix is a no-op that looks like
    a fix. That is why the luma plane is read out of the pinned `yuv444p`
    decode instead.
    """
    base = [
        "ffmpeg", "-v", "error", "-start_number", "0",
        "-i", str(frames_dir / "frame_%06d.png"),
        "-pix_fmt", "gray", "-f", "rawvideo", "-",
    ]
    scaled = base[:9] + ["-vf", imageio.SOURCE_SCALE_FILTER] + base[9:]
    assert imageio.run_raw(base) == imageio.run_raw(scaled), (
        "if these ever differ, ffmpeg has started honouring the options for "
        "gray and the module docstring's reasoning needs revisiting"
    )


def test_the_calibration_raises_rather_than_recording_a_bad_number(
    frames_dir, monkeypatch
):
    """A recorded zero is the evidence a row's numbers mean what they say."""
    pinned = imageio.source_yuv_command  # bound BEFORE the patch, or it recurses

    def unpinned(d: Path) -> list[str]:
        return [c for c in pinned(d) if c not in ("-vf", imageio.SOURCE_SCALE_FILTER)]

    monkeypatch.setattr(imageio, "source_yuv_command", unpinned)
    with pytest.raises(BenchError, match="calibration failed"):
        decode_calibration(frames_dir, FPS, height=H, width=W)


def test_a_length_mismatch_is_refused_rather_than_silently_reshaped(frames_dir):
    """An off-by-one pairing makes every encode-side metric measure motion."""
    with pytest.raises(imageio.BenchDecodeError, match="whole number"):
        imageio._reshape(b"\x00" * 7, planes=3, height=H, width=W, label="source_yuv")
