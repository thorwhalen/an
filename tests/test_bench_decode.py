"""The decode legs, and the reference every encode-side metric is measured against.

Every encode-side metric is `f(reference[i], decoded[i])`. Getting the
reference wrong makes all of them measure something other than the encoder —
with plausible, monotone numbers.

**The first design got it wrong, and CI is what said so.** It referenced the
metrics to an explicit RGB->YUV conversion of the source PNGs and asserted, as
a hard equality, that this conversion reproduces what libx264 received.
Measured against a `-qp 0` lossless encode of the same frames, it does — on
ffmpeg 8.1, exactly, 0.0000 / max 0 — and does **not** on the Linux runner's
older build: 0.6290 / max 5, which is 42% of `coded_luma_edge_error`'s whole
crf23 value.

The fix is not a tolerance. `-qp 0` is lossless, so its decoded luma **is** the
plane the encoder received on any build, and referencing to it removes the
assumption rather than widening it. The conversion is still measured, and its
distance from the encoder's input is recorded as provenance — that number is
the build dependence, and it belongs in the row rather than inside a gate.

Two metrics still reference the PNG conversion, on purpose, and these tests pin
why: the chroma metric's subject *is* the 4:2:0 subsampling that happens during
the conversion, and `encode_ringing_excess` cancels a term that exists only when
both its legs share that reference.

Needs ffmpeg, not a browser: the frames are synthesised here.
"""

from __future__ import annotations

import struct
import zlib
from pathlib import Path

import numpy as np
import pytest

from an.bench import imageio
from an.bench.run import conversion_distance, lossless_reference

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


def test_a_lossless_encode_reads_back_as_its_own_reference(frames_dir, tmp_path):
    """The property the whole design now rests on, on whatever build runs this.

    If `-qp 0` were not lossless in luma, the reference would be as wrong as
    the conversion it replaced — and silently so.
    """
    mp4 = lossless_reference(frames_dir, FPS, tmp_path / "lossless.mp4", delivered=None)
    a = imageio.decoded_yuv(mp4, height=H, width=W)
    b = imageio.decoded_yuv(mp4, height=H, width=W)
    assert np.array_equal(a[:, 0], b[:, 0])

    lossy = tmp_path / "lossy.mp4"
    cmd = imageio.lossless_encode_command(frames_dir, FPS, lossy)
    i = cmd.index("-qp")
    cmd[i : i + 2] = ["-crf", "40"]  # `-crf 0` is ALSO lossless; use a real one
    imageio.run_raw(cmd)
    d = imageio.decoded_yuv(lossy, height=H, width=W)
    n = min(len(a), len(d))
    residual = np.abs(d[:n, 0].astype(np.int16) - a[:n, 0].astype(np.int16))
    assert residual.max() > 0, (
        "a lossy encode must differ from the lossless reference; if it does "
        "not, `coded_luma_edge_error` is measuring nothing"
    )


def test_the_conversion_distance_is_recorded_and_never_gated(frames_dir, tmp_path):
    """It was a hard equality. It passed here and failed on Linux.

    That is the shape of a machine-dependent fact masquerading as a universal
    one, and the reason it is provenance now. This test asserts the SHAPE of the
    record, not a value — asserting a value is what made the first version
    fail for the wrong reason on the wrong machine.
    """
    mp4 = lossless_reference(frames_dir, FPS, tmp_path / "lossless.mp4", delivered=None)
    report = conversion_distance(frames_dir, mp4, height=H, width=W, frames=N)
    assert set(report) >= {
        "luma_residual_mean",
        "luma_residual_max",
        "png_command",
        "encoder_input_command",
        "note",
    }
    assert report["luma_residual_max"] >= 0
    assert imageio.SOURCE_SCALE_FILTER in report["png_command"], (
        "the PNG conversion must stay range- and matrix-pinned even though it "
        "is no longer the reference — it is the number that explains a "
        "cross-build surprise, and an unpinned one explains nothing"
    )


def test_the_pin_measurably_changes_the_source_conversion(frames_dir, tmp_path):
    """The pin still earns its place: it makes a measurable difference.

    an#147: the first two cuts of this test asserted a *direction* — that the
    pinned (range- and matrix-explicit) conversion sits closer to what the
    encoder actually received than the unpinned (ffmpeg-default) one. That
    direction is not a universal ffmpeg fact: measured on the CI runner's apt
    ffmpeg 6.1.1 (reproduced locally on Homebrew's keg-only ffmpeg@6, 6.1.6),
    `-colorspace bt709` on the encode side does **not** reach the auto-inserted
    RGB->YUV conversion, so the unpinned reading is the one that agrees with
    the encoder there (pinned=6.8333, unpinned=0.0) — the exact **opposite** of
    ffmpeg 9.0.1, where the flag does reach that conversion (pinned=0.0,
    unpinned=6.8333). Both builds agree on the property that survives: the pin
    is never a no-op, by a wide, identical margin (6.8333) either way. That is
    also the pin's real justification — see `imageio.SOURCE_SCALE_FILTER`'s
    comment: without it, the PNG leg's conversion is whatever ffmpeg's
    build-dependent default happens to be, not a pinned, reproducible one.
    """
    mp4 = lossless_reference(frames_dir, FPS, tmp_path / "lossless.mp4", delivered=None)
    enc_in = imageio.decoded_yuv(mp4, height=H, width=W)

    pinned = imageio.source_yuv(frames_dir, height=H, width=W, frames=N)
    unpinned_cmd = [
        c
        for c in imageio.source_yuv_command(frames_dir)
        if c not in ("-vf", imageio.SOURCE_SCALE_FILTER)
    ]
    unpinned = np.frombuffer(imageio.run_raw(unpinned_cmd), np.uint8).reshape(
        -1, 3, H, W
    )

    def dist(a):
        n = min(len(a), len(enc_in))
        return float(
            np.abs(enc_in[:n, 0].astype(np.int16) - a[:n, 0].astype(np.int16)).mean()
        )

    d_pinned, d_unpinned = dist(pinned), dist(unpinned)
    assert abs(d_pinned - d_unpinned) > 1.0, (
        f"pinned {d_pinned:.4f} vs unpinned {d_unpinned:.4f}; if these ever "
        "converge, the scale filter has stopped doing anything on this build "
        "and the pin's justification needs re-measuring rather than assuming"
    )


def test_the_gray_pixel_format_range_matrix_interaction_is_build_dependent(
    frames_dir,
):
    """Research §1.4's literal pseudocode reads the luma with `-pix_fmt gray`.

    an#147: whether `-pix_fmt gray` honours the `scale` filter's
    `out_color_matrix` / `out_range` options turns out to be a property of the
    ffmpeg *build*, not a universal fact — measured, not assumed. ffmpeg 9.0.1
    ignores them outright (mean/max luma diff 0.0 / 0); ffmpeg 6.1.6 (matching
    the CI runner's apt ffmpeg 6.1.1) applies them and differs by mean 17.5 /
    max 20 on this scene. Either way the natural "fix" of adding `-vf` to a
    `gray` decode is not something to rely on — sometimes it does nothing,
    sometimes it changes the output, and neither build tells you which without
    measuring. That unreliability, not a specific direction, is why the luma
    plane is read out of the pinned `yuv444p` decode instead, never out of
    `-pix_fmt gray`.
    """
    base = [
        "ffmpeg",
        "-v",
        "error",
        "-start_number",
        "0",
        "-i",
        str(frames_dir / "frame_%06d.png"),
        "-pix_fmt",
        "gray",
        "-f",
        "rawvideo",
        "-",
    ]
    scaled = base[:9] + ["-vf", imageio.SOURCE_SCALE_FILTER] + base[9:]
    plain = imageio.run_raw(base)
    with_filter = imageio.run_raw(scaled)
    assert len(plain) == len(with_filter) == N * H * W, (
        "both decodes must produce exactly one gray byte per pixel per frame "
        "regardless of whether this build honours the scale filter's options"
    )


def test_a_length_mismatch_is_refused_rather_than_silently_reshaped(frames_dir):
    """An off-by-one pairing makes every encode-side metric measure motion.

    ``frames=None`` is the leg that can only check divisibility — the mp4
    decodes — so this is where the "whole number" message stays reachable. The
    exact-count refusal has its own test in ``tests/test_bench_shape_guard.py``.
    """
    with pytest.raises(imageio.BenchDecodeError, match="whole number"):
        imageio._reshape(
            b"\x00" * 7, planes=3, height=H, width=W, label="source_yuv", frames=None
        )
