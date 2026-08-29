"""The pinned ffmpeg decodes, and the lossless leg every encode-side metric
is measured against.

**The reference is the lossless encode, not the PNGs — and that is a
correction, made because CI caught the alternative.** The obvious design reads
the source frames back with an explicit conversion
(`-vf scale=out_range=tv:out_color_matrix=bt709 -pix_fmt yuv444p`) and compares
the delivered mp4 against that. Measured against a mathematically lossless
(`-qp 0`) encode of the same frames, that conversion agrees **exactly** on
ffmpeg 8.1 (luma residual 0.0000, max 0) and **does not** on the Linux runner's
older build (0.6290, max 5). A floor of 0.63 is 42% of `coded_luma_edge_error`'s
whole crf23 value, so on that build every encode-side luma number would have
been measuring a colour-conversion disagreement and reporting it as encoder
damage.

The fix is not a tolerance. It is to stop having a second conversion at all:
`-qp 0` is lossless, so **the qp0 decode's luma plane IS the plane libx264
received**, on every build, by definition. Referencing the metrics to it
removes the assumption instead of widening it, and it costs nothing extra —
`encode_ringing_excess` already needed that leg.

**That "IS" is a claim about the leg's INPUT FORMAT as much as its rate
control**, and the leg pinned `-pix_fmt yuv420p` until an#72. Against a 4:4:4
delivery the reference was therefore a *different colour pipeline* from the file
it referenced, and every metric measured against it carried the whole 4:2:0
conversion this leg exists to cancel — as a term that does not cancel and does
vary by build.

The leg now takes its format from the **delivered file itself**
(`delivered_pix_fmt`, threaded in by `run.lossless_reference`). Probing rather
than re-deriving is the load-bearing part: **two** seams set the delivered
format — `RenderContext.pix_fmt`, which `an render --pix-fmt` uses and which the
mux resolves FIRST, and the `DEFAULT_PIX_FMT` module global, which is only its
fallback. A leg that consults either one covers only that one, and an#72's first
fix consulted the global — so it silently re-pinned on the seam a user can
actually reach, with every guard green.

(No `pix_fmt` lever exists: `MUTATIONS` is `high_crf`/`disabled_aa`/`supersample`
and `_check_registered` enforces it at import. The global's seam is kept for a
lever that has never been registered — see `an-dev-bench`'s `pix_fmt` row for why
— and outside the product it is driven only by tests. So "the lever rebinds it"
is a statement about a seam's purpose, not about anything that runs.)

Note which metrics the mismatch reached: `flat_field_deviation`,
`flat_field_p99_dev` and `encode_flicker_on_held_pixels` reduce over RGB, so
chroma reaches them, while the luma-domain metrics never moved —
`coded_luma_edge_error` and `encode_ringing_excess` are identical to the last
digit between a 4:2:0 and a 4:4:4 leg on all ten corpus scenes.

Two things this does NOT change:

- **The chroma metric still references the direct RGB->444 conversion**, because
  its subject *is* the 4:2:0 subsampling that happens during the conversion —
  which is exactly the term this leg cancels, so a lossless-referenced version
  is blind to it whatever format the leg is encoded in. (With a tracking leg
  and a 4:4:4 delivery, both legs are 4:4:4 and the subsampling does not exist
  to be measured; tracking makes the panel honest, not sighted.) It does **not**
  read ~0, though, and that half of the old wording was wrong: measured
  2026-08-29 on four scenes, mean |dCr| over the edge mask reads 1.71 / 1.90 /
  2.24 / 2.50 against the shipped metric's 7.19 / 9.29 / 8.04 / 2.93, because it
  measures chroma *quantiser* damage instead (an#72).
- **The PNG conversion is still performed and its distance from the encoder's
  input is still recorded** (`png_to_encoder_input_luma`), because that number
  is exactly the build-dependence that was hiding inside a hard equality. It is
  provenance now, not a gate.

`-map 0:v:0 -fps_mode passthrough` on every mp4 decode: the delivered file
carries an AAC track, and implicit frame-rate conversion would silently re-time
the sequence that every encode-side metric pairs frame-for-frame.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from an.base import MP4_FASTSTART_ARGS
from an.adapters.cutout.render import (
    DETERMINISTIC_X264_ARGS,
    SUPPORTED_PIX_FMTS,
    _check_pix_fmt,
)

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
        "ffmpeg",
        "-v",
        "error",
        "-start_number",
        "0",
        "-i",
        str(frames_dir / DEFAULT_FRAME_PNG_PATTERN),
        "-pix_fmt",
        RGB_PIX_FMT,
        "-f",
        "rawvideo",
        "-",
    ]


def source_yuv_command(frames_dir: Path) -> list[str]:
    """Decode the pre-encode PNG sequence to planar YUV, **range- and matrix-pinned**."""
    return [
        "ffmpeg",
        "-v",
        "error",
        "-start_number",
        "0",
        "-i",
        str(frames_dir / DEFAULT_FRAME_PNG_PATTERN),
        "-vf",
        SOURCE_SCALE_FILTER,
        "-pix_fmt",
        YUV_PIX_FMT,
        "-f",
        "rawvideo",
        "-",
    ]


def decoded_rgb_command(mp4: Path) -> list[str]:
    """Decode the delivered mp4 to raw RGB."""
    return [
        "ffmpeg",
        "-v",
        "error",
        "-i",
        str(mp4),
        "-map",
        "0:v:0",
        "-fps_mode",
        "passthrough",
        "-pix_fmt",
        RGB_PIX_FMT,
        "-f",
        "rawvideo",
        "-",
    ]


def decoded_yuv_command(mp4: Path) -> list[str]:
    """Decode the delivered mp4 to planar YUV.

    No ``scale`` filter here, deliberately: the file carries BT.709 tags
    (an#34) so ffmpeg already decodes it in the space the source leg is pinned
    to. Adding one would convert twice.
    """
    return [
        "ffmpeg",
        "-v",
        "error",
        "-i",
        str(mp4),
        "-map",
        "0:v:0",
        "-fps_mode",
        "passthrough",
        "-pix_fmt",
        YUV_PIX_FMT,
        "-f",
        "rawvideo",
        "-",
    ]


def delivered_pix_fmt(mp4: Path) -> str:
    """The pixel format ``mp4`` is actually encoded in, from the file itself.

    The delivered encode resolves its format from ``RenderContext.pix_fmt``
    **or** the module global (`render._check_pix_fmt`), and only the file knows
    which won — so the lossless leg cannot re-derive it and be sure of matching.
    Re-deriving is what an#72's first fix did, and it covered the bench lever's
    seam (which rebinds the global) while missing the product's own
    (``an render --pix-fmt``, which sets the context and never touches it).

    Probed, not remembered: this is the one source of truth that no future seam
    can route around.
    """
    out = run_raw(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=pix_fmt",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(mp4),
        ]
    )
    fmt = out.decode("utf-8", "replace").strip()
    if not fmt:
        raise BenchDecodeError(f"ffprobe reported no pixel format for {mp4}")
    if fmt not in SUPPORTED_PIX_FMTS:
        # Refused HERE rather than downstream: `lossless_encode_command` would
        # raise `CutoutRenderError` for it, a *render* error from a bench path,
        # and `lossless_reference` sits outside `_scene_metrics`'s try/finally
        # so it would abort the run instead of being recorded. Unreachable
        # through `an`'s own encoder, which pins one of two formats — but the
        # probe reads a FILE, and a file can be anything.
        raise BenchDecodeError(
            f"{mp4} is encoded in {fmt!r}, which is not one of "
            f"{SUPPORTED_PIX_FMTS}. The lossless leg has to be encodable in the "
            "delivered format to be the plane libx264 received."
        )
    return fmt


def lossless_encode_command(
    frames_dir: Path, fps: int, out: Path, *, pix_fmt: str | None = None
) -> list[str]:
    """`-qp 0` with otherwise identical flags, for `encode_ringing_excess`.

    Identical to the delivered encode except for the rate control, so the
    difference of the two overshoot means cancels the source-hardness term —
    which is the whole reason `encode_ringing_excess` replaced raw overshoot.

    **Pinned in its ENCODER SETTINGS, tracking in its INPUT FORMAT** — and the
    asymmetry is the correction an#72 turned on. Two module globals reach this
    command and they must be reached differently:

    - ``DETERMINISTIC_X264_ARGS`` is bound at IMPORT, above, so a lever that
      rebinds it (``high_crf``) cannot move the reference. The reference has to
      stay lossless, or every encode-side metric is measured against a moving
      target and the lever produces beautiful numbers about nothing.
    - ``pix_fmt`` is resolved at CALL time through the product's own
      :func:`~an.adapters.cutout.render._check_pix_fmt`. The bench passes the
      format **probed off the delivered mp4** (:func:`delivered_pix_fmt`);
      ``None`` falls back to ``DEFAULT_PIX_FMT``, which is right for a caller
      with no delivered file to match and wrong for one that has it.

    The difference is not a preference. ``-pix_fmt`` is not an encoder setting:
    it names **what libx264 receives**, and being what libx264 received is this
    leg's entire purpose (see the module docstring). Pinning it does not keep
    the reference lossless — it makes the reference a *different colour
    pipeline* from the delivered file, so every metric measured against it
    silently acquires the whole 4:2:0 conversion the reference exists to
    cancel. Measured on the corpus at 4:4:4: family E
    (``encode_flicker_on_held_pixels``) changes SIGN on three of ten scenes
    between a pinned leg and a tracking one — from as-declared to contrary in
    every case — and family D (``flat_field_deviation``) moves by up to 24.8
    percentage points. The affected metrics are exactly the RGB-domain ones,
    which reduce over three channels so chroma reaches them; the luma-domain
    metrics were never affected — ``coded_luma_edge_error`` and
    ``encode_ringing_excess`` are identical to the last digit between a 4:2:0
    and a 4:4:4 leg on all ten scenes.

    A default (4:2:0) render is byte-identical to before this change, so no
    committed ledger row is invalidated.
    """
    # Resolved through the product's own validator rather than a second copy of
    # the fallback, so this leg cannot be encoded in a format the delivered
    # encode would have refused. Imported at MODULE scope, unlike
    # `DETERMINISTIC_X264_ARGS` above and for the opposite reason: that import
    # binds a VALUE, which is what pins the rate control; this one binds a
    # FUNCTION, whose body reads `DEFAULT_PIX_FMT` from `render`'s globals when
    # it is called. Where the import sits makes no difference here, and an
    # earlier version of this comment claimed it did.
    resolved = _check_pix_fmt(pix_fmt)
    args = [a for a in DETERMINISTIC_X264_ARGS]
    # Swap the CRF pair for `-qp 0`; everything else (threads, preset, colour
    # tags) must stay identical or the two legs stop being comparable.
    for flag in ("-crf",):
        if flag in args:
            i = args.index(flag)
            del args[i : i + 2]
    return [
        "ffmpeg",
        "-y",
        "-loglevel",
        "error",
        "-framerate",
        str(fps),
        "-i",
        str(frames_dir / DEFAULT_FRAME_PNG_PATTERN),
        "-c:v",
        "libx264",
        "-pix_fmt",
        resolved,
        "-qp",
        "0",
        *args,
        *MP4_FASTSTART_ARGS,
        str(out),
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


def _reshape(
    buf: bytes,
    *,
    planes: int,
    height: int,
    width: int,
    label: str,
    frames: int | None,
) -> Any:
    """Shape a raw decode, refusing a buffer that is not exactly ``frames`` of them.

    ``frames`` is keyword-only and **has no default**, because the defect it
    closes is arithmetically invisible. This used to test only that the byte
    count *divides* by ``planes * height * width`` — and a k-times supersample
    makes the buffer exactly ``k**2`` larger, so the check always passed and the
    reshape silently produced ``k**2 * N`` frames of scrambled pixels. At k=2
    destination row 0 is the source row's left half and row 1 its right half, so
    most horizontal runs survive and every family-A metric returns a believable
    number. A default here would let a new leg opt out of the check by omission.

    ``None`` means "the count is not known independently": the mp4 legs, whose
    frame count is whatever the encoder emitted, and which the run deliberately
    tolerates disagreeing (``frame_count_disagreement``). There only
    divisibility can be checked, and the size of the *pixels* is guarded
    upstream instead — the delivered mp4 is muxed from the very PNGs that
    :func:`an.bench.run._assert_declared_resolution` has already sized.

    **This is a deliberate behaviour change on the PNG legs**, and the only one
    in an#54: a source-side count disagreement used to be *recorded* as
    ``frame_count_disagreement`` and is now a refusal. That is the right way
    round — ffmpeg's image2 demuxer reads the contiguous ``frame_%06d.png`` run
    from 0, so a short read there means the frame sequence has a hole, and
    every per-frame pairing below it is offset. The mp4 legs keep the recorded
    form, because their count legitimately differs.
    """
    import numpy as np

    per_frame = planes * height * width
    if per_frame == 0:
        raise BenchDecodeError(
            f"{label}: a {planes}x{height}x{width} frame has no pixels, so the "
            f"{len(buf)}-byte decode cannot be shaped at all."
        )
    if frames is not None and len(buf) != per_frame * frames:
        raise BenchDecodeError(
            f"{label}: {len(buf)} bytes is not exactly {frames} frames of "
            f"{planes}x{height}x{width} ({per_frame * frames} bytes) — "
            f"{len(buf) / per_frame:g} frames' worth arrived. The decoded "
            "stream is not the declared resolution. A k-times supersample "
            "divides evenly by k**2, so a divisibility check would have passed "
            "here and produced k**2 as many scrambled frames instead."
        )
    if len(buf) % per_frame:
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


def source_rgb(frames_dir: Path, *, height: int, width: int, frames: int) -> Any:
    """``(N, H, W, 3)`` uint8 of the pre-encode PNGs.

    ``frames`` is how many PNGs are on disk — the caller counted them — and it
    is required rather than derived, so the decode cannot silently return a
    different number of them.
    """
    return _reshape(
        run_raw(source_rgb_command(frames_dir)),
        planes=3,
        height=height,
        width=width,
        label="source_rgb",
        frames=frames,
    )


def source_yuv(frames_dir: Path, *, height: int, width: int, frames: int) -> Any:
    """``(N, 3, H, W)`` uint8 planar YUV of the pre-encode PNGs, range-pinned."""
    return _reshape(
        run_raw(source_yuv_command(frames_dir)),
        planes=3,
        height=height,
        width=width,
        label="source_yuv",
        frames=frames,
    )


def decoded_rgb(mp4: Path, *, height: int, width: int) -> Any:
    """``(N, H, W, 3)`` uint8 of the delivered mp4.

    ``frames=None``: the run deliberately tolerates the encoder emitting a
    different count from the source leg and records it as
    ``frame_count_disagreement``, so an equality here would turn a recorded
    disagreement into a crash. See :func:`_reshape`.
    """
    return _reshape(
        run_raw(decoded_rgb_command(mp4)),
        planes=3,
        height=height,
        width=width,
        label="decoded_rgb",
        frames=None,
    )


def decoded_yuv(mp4: Path, *, height: int, width: int) -> Any:
    """``(N, 3, H, W)`` uint8 planar YUV of the delivered mp4. ``frames=None`` — see :func:`decoded_rgb`."""
    return _reshape(
        run_raw(decoded_yuv_command(mp4)),
        planes=3,
        height=height,
        width=width,
        label="decoded_yuv",
        frames=None,
    )


def video_stream_bytes(mp4: Path) -> int:
    """Sum of the video stream's packet sizes — the AAC track excluded.

    ``file_bytes`` includes the audio track, which the renderer always emits
    (silent if there is no dialogue), so it varies with the audio cache's state.
    This one does not.
    """
    out = run_raw(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "packet=size",
            "-of",
            "csv=p=0",
            str(mp4),
        ]
    )
    total = 0
    for line in out.decode("ascii", "replace").splitlines():
        line = line.strip().rstrip(",")
        if line:
            total += int(line)
    return total
