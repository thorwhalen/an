"""The four pinned ffmpeg decodes, and the lossless re-encode.

**The spelling of these commands moves the numbers**, which is the single
largest risk in the whole metrics ledger. Measured on this repo, against a
mathematically lossless (`-qp 0`) encode of the same PNGs:

===========================================  =========================
decode of the PNG source                     residual vs that encode
===========================================  =========================
``-pix_fmt yuv444p`` (the obvious form)      Y mean 5.33, max 20
``-vf scale=out_range=tv:out_color_matrix=   **Y mean 0.0000, max 0**
bt709 -pix_fmt yuv444p``
===========================================  =========================

The unpinned form measures a full-range/limited-range and matrix mismatch and
reports it as encoder damage. That is the exact defect the research's §1.4
correction was written to fix, reintroduced one level down — and it produces
plausible, monotone numbers, which is why it needs an assertion rather than a
comment.

Worse, the natural fix does not work on the natural spelling: research §1.4's
literal pseudocode reads the luma with ``-pix_fmt gray``, and ffmpeg **silently
ignores** the ``scale`` filter's ``out_color_matrix`` / ``out_range`` options
for ``gray`` — verified byte-identical output with and without the filter. So
the luma plane is read out of the pinned ``yuv444p`` decode instead, which also
means one subprocess serves both the luma and the chroma metrics.

``-map 0:v:0 -fps_mode passthrough`` on every mp4 decode: the delivered file
carries an AAC track, and implicit frame-rate conversion would silently
re-time the sequence that every encode-side metric pairs frame-for-frame.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from an.adapters.cutout.render import DETERMINISTIC_X264_ARGS

#: The pinned conversion applied to the PNG leg. Never remove it: without it
#: the encode-side metrics measure a colour-space conversion.
SOURCE_SCALE_FILTER: str = "scale=out_range=tv:out_color_matrix=bt709"

#: Planar 4:4:4 for both legs of every encode-side metric — never `rgb24` for
#: the edge metrics, whose defect was clipping precisely at the saturated fills
#: sitting against black outlines that flat 2D art is made of.
YUV_PIX_FMT: str = "yuv444p"
RGB_PIX_FMT: str = "rgb24"

#: The frame filename pattern the renderer writes. Imported rather than
#: restated so a rename cannot desynchronise the two.
from an.adapters.cutout.render import DEFAULT_FRAME_PNG_PATTERN  # noqa: E402


class BenchDecodeError(RuntimeError):
    """ffmpeg could not read something the bench needs."""


def source_rgb_command(frames_dir: Path) -> list[str]:
    """Decode the pre-encode PNG sequence to raw RGB.

    RGB is correct here and only here: the two metrics that use it
    (`flat_field_deviation`, `encode_flicker_on_held_pixels`) are masked to the
    flat interior and to held pixels, both off-edge by construction, so the
    clipping that ruins the edge metrics cannot reach them.
    """
    return [
        "ffmpeg", "-v", "error", "-start_number", "0",
        "-i", str(frames_dir / DEFAULT_FRAME_PNG_PATTERN),
        "-pix_fmt", RGB_PIX_FMT, "-f", "rawvideo", "-",
    ]


def source_yuv_command(frames_dir: Path) -> list[str]:
    """Decode the pre-encode PNG sequence to planar YUV, **range- and matrix-pinned**."""
    return [
        "ffmpeg", "-v", "error", "-start_number", "0",
        "-i", str(frames_dir / DEFAULT_FRAME_PNG_PATTERN),
        "-vf", SOURCE_SCALE_FILTER,
        "-pix_fmt", YUV_PIX_FMT, "-f", "rawvideo", "-",
    ]


def decoded_rgb_command(mp4: Path) -> list[str]:
    """Decode the delivered mp4 to raw RGB."""
    return [
        "ffmpeg", "-v", "error", "-i", str(mp4),
        "-map", "0:v:0", "-fps_mode", "passthrough",
        "-pix_fmt", RGB_PIX_FMT, "-f", "rawvideo", "-",
    ]


def decoded_yuv_command(mp4: Path) -> list[str]:
    """Decode the delivered mp4 to planar YUV.

    No ``scale`` filter here, deliberately: the file carries BT.709 tags
    (an#34) so ffmpeg already decodes it in the space the source leg is pinned
    to. Adding one would convert twice.
    """
    return [
        "ffmpeg", "-v", "error", "-i", str(mp4),
        "-map", "0:v:0", "-fps_mode", "passthrough",
        "-pix_fmt", YUV_PIX_FMT, "-f", "rawvideo", "-",
    ]


def lossless_encode_command(frames_dir: Path, fps: int, out: Path) -> list[str]:
    """`-qp 0` with otherwise identical flags, for `encode_ringing_excess`.

    Identical to the delivered encode except for the rate control, so the
    difference of the two overshoot means cancels the source-hardness term —
    which is the whole reason `encode_ringing_excess` replaced raw overshoot.
    """
    args = [a for a in DETERMINISTIC_X264_ARGS]
    # Swap the CRF pair for `-qp 0`; everything else (threads, preset, colour
    # tags) must stay identical or the two legs stop being comparable.
    for flag in ("-crf",):
        if flag in args:
            i = args.index(flag)
            del args[i : i + 2]
    return [
        "ffmpeg", "-y", "-loglevel", "error", "-framerate", str(fps),
        "-i", str(frames_dir / DEFAULT_FRAME_PNG_PATTERN),
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-qp", "0",
        *args, "-movflags", "+faststart", str(out),
    ]


def run_raw(cmd: list[str]) -> bytes:
    """Run an ffmpeg command and return its raw stdout, or raise with the stderr."""
    try:
        result = subprocess.run(cmd, capture_output=True, check=False)
    except OSError as e:
        raise BenchDecodeError(f"ffmpeg failed to launch: {e}") from e
    if result.returncode != 0:
        raise BenchDecodeError(
            f"ffmpeg exited {result.returncode} for:\n  {' '.join(cmd)}\n"
            + result.stderr.decode("utf-8", "replace")[-2000:]
        )
    return result.stdout


def _reshape(buf: bytes, *, planes: int, height: int, width: int, label: str) -> Any:
    import numpy as np

    per_frame = planes * height * width
    if per_frame == 0 or len(buf) % per_frame:
        raise BenchDecodeError(
            f"{label}: {len(buf)} bytes is not a whole number of "
            f"{planes}x{height}x{width} frames. The declared resolution and the "
            "decoded stream disagree, so every per-frame pairing below would be "
            "silently offset."
        )
    n = len(buf) // per_frame
    arr = np.frombuffer(buf, np.uint8)
    if planes == 3 and label.endswith("rgb"):
        return arr.reshape(n, height, width, 3)
    return arr.reshape(n, planes, height, width)


def source_rgb(frames_dir: Path, *, height: int, width: int) -> Any:
    """``(N, H, W, 3)`` uint8 of the pre-encode PNGs."""
    return _reshape(
        run_raw(source_rgb_command(frames_dir)),
        planes=3, height=height, width=width, label="source_rgb",
    )


def source_yuv(frames_dir: Path, *, height: int, width: int) -> Any:
    """``(N, 3, H, W)`` uint8 planar YUV of the pre-encode PNGs, range-pinned."""
    return _reshape(
        run_raw(source_yuv_command(frames_dir)),
        planes=3, height=height, width=width, label="source_yuv",
    )


def decoded_rgb(mp4: Path, *, height: int, width: int) -> Any:
    """``(N, H, W, 3)`` uint8 of the delivered mp4."""
    return _reshape(
        run_raw(decoded_rgb_command(mp4)),
        planes=3, height=height, width=width, label="decoded_rgb",
    )


def decoded_yuv(mp4: Path, *, height: int, width: int) -> Any:
    """``(N, 3, H, W)`` uint8 planar YUV of the delivered mp4."""
    return _reshape(
        run_raw(decoded_yuv_command(mp4)),
        planes=3, height=height, width=width, label="decoded_yuv",
    )


def video_stream_bytes(mp4: Path) -> int:
    """Sum of the video stream's packet sizes — the AAC track excluded.

    ``file_bytes`` includes the audio track, which the renderer always emits
    (silent if there is no dialogue), so it varies with the audio cache's state.
    This one does not.
    """
    out = run_raw([
        "ffprobe", "-v", "error", "-select_streams", "v:0",
        "-show_entries", "packet=size", "-of", "csv=p=0", str(mp4),
    ])
    total = 0
    for line in out.decode("ascii", "replace").splitlines():
        line = line.strip().rstrip(",")
        if line:
            total += int(line)
    return total
