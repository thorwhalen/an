"""A filter-0 PNG writer and a full-filter reader — numpy and stdlib only.

The golden corpus is committed as PNGs, and the gate compares **decoded
pixels**. Both halves of that sentence need this module.

**Why write our own encoder rather than copy Chromium's bytes.** A committed
golden written by copying the screenshot file is a function of Chromium's
libpng settings as well as of the picture. Re-encoding every row with filter
type 0 makes the committed file a function of the **pixel data alone**, so a
future Chromium bump that changes its filter heuristic produces no diff at all
when the picture has not moved. Measured on the five real frames in this repo,
filter-0 re-encoding is also *smaller* every time — between -5.2% and -22.3%
(research §3 recorded -0.30%, from a single frame; the range is wider both ways
than one sample suggested).

**Why the reader must handle all five filter types.** The bless path reads
Chromium's own screenshots, and Chromium emits Sub / Up / Paeth. Only the files
this module writes are filter-0.

**Why the filters are unfiltered over a ``bytearray`` rather than a numpy
array.** Sub, Average and Paeth are sequential along the row — byte *x* depends
on byte *x - bpp* of the same row — so they cannot be vectorised, and the inner
loop is scalar either way. Scalar indexing into a ``bytearray`` is measurably
cheaper than into a numpy array: 32 ms against 111 ms for a 320x240 Chromium
frame, and 444 ms against 2,430 ms for a mixed-filter 1920x1080 one. A filter-0
file skips the loop entirely (a whole-row slice), which is why reading a golden
costs ~0.1 ms at 320x240 and ~1.8 ms at 1080p.

The round trip is exact, and it is verified in both directions against an
independent decoder: ``ffmpeg -pix_fmt rgb24`` agrees with :func:`decode_png`
on every rendered frame in the repo, and reads back what :func:`encode_png`
writes.
"""

from __future__ import annotations

import struct
import zlib
from typing import Any

#: PNG's fixed 8-byte signature.
PNG_SIGNATURE: bytes = b"\x89PNG\r\n\x1a\n"

#: zlib level for the IDAT stream. 9 because a golden is written rarely and
#: read often, and because the committed bytes are reviewed in a diff.
DFLT_ZLIB_LEVEL: int = 9

#: PNG colour types this module understands, mapped to their channel count.
#: 2 is what Chromium's canvas screenshot emits (verified on every rendered
#: frame in the repo); 6 is accepted because a future capture path could emit
#: it. Everything else is refused by name rather than mis-decoded.
_CHANNELS_BY_COLOUR_TYPE: dict[int, int] = {2: 3, 6: 4}

_COLOUR_TYPE_NAMES: dict[int, str] = {
    0: "greyscale",
    2: "truecolour (RGB)",
    3: "indexed / palette",
    4: "greyscale + alpha",
    6: "truecolour + alpha (RGBA)",
}

#: What :func:`encode_png` writes, and the only channel count the golden gate
#: compares. Alpha is dropped at the boundary (see :func:`to_rgb`), never here.
RGB_CHANNELS: int = 3

#: Bit depth. 16-bit PNGs are refused rather than truncated — a silently
#: halved code value is exactly the class of bug this corpus exists to catch.
SUPPORTED_BIT_DEPTH: int = 8


class PngFormatError(ValueError):
    """A PNG this module deliberately does not decode, or a malformed one.

    Typed and specific on purpose: the alternative to refusing is returning a
    plausible array, and a golden gate that compares a plausible array is worse
    than one that does not run.
    """


def _chunk(kind: bytes, payload: bytes) -> bytes:
    return (
        struct.pack(">I", len(payload))
        + kind
        + payload
        + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)
    )


def encode_png(rgb: Any, *, level: int = DFLT_ZLIB_LEVEL) -> bytes:
    """Encode ``(H, W, 3)`` uint8 as an 8-bit truecolour PNG, every row filter 0.

    >>> import numpy as np
    >>> data = encode_png(np.zeros((2, 3, 3), np.uint8))
    >>> data[:8] == PNG_SIGNATURE
    True
    >>> decode_png(data).shape
    (2, 3, 3)
    """
    import numpy as np

    arr = np.asarray(rgb)
    if arr.dtype != np.uint8:
        raise PngFormatError(
            f"encode_png needs a uint8 array; got {arr.dtype}. A float array "
            "would be silently truncated, and the golden would then differ "
            "from the frame it was blessed from."
        )
    if arr.ndim != 3 or arr.shape[2] != RGB_CHANNELS:
        raise PngFormatError(
            f"encode_png needs an (H, W, 3) array; got shape {tuple(arr.shape)}. "
            "Drop alpha at the boundary with `to_rgb` so the loss is explicit."
        )
    height, width, _ = arr.shape
    if height == 0 or width == 0:
        raise PngFormatError(f"refusing to encode an empty image {width}x{height}")

    # One leading filter byte per row, left at 0.
    raw = np.zeros((height, width * RGB_CHANNELS + 1), np.uint8)
    raw[:, 1:] = np.ascontiguousarray(arr).reshape(height, width * RGB_CHANNELS)
    header = struct.pack(
        ">IIBBBBB", width, height, SUPPORTED_BIT_DEPTH, 2, 0, 0, 0
    )
    return (
        PNG_SIGNATURE
        + _chunk(b"IHDR", header)
        + _chunk(b"IDAT", zlib.compress(raw.tobytes(), level))
        + _chunk(b"IEND", b"")
    )


def _parse_chunks(data: bytes) -> tuple[tuple[int, ...], bytes]:
    """``(IHDR fields, concatenated IDAT payload)``, refusing anything malformed."""
    if data[:8] != PNG_SIGNATURE:
        raise PngFormatError("not a PNG: the 8-byte signature does not match")
    header: tuple[int, ...] | None = None
    idat: list[bytes] = []
    offset = 8
    while offset + 8 <= len(data):
        (length,) = struct.unpack(">I", data[offset : offset + 4])
        kind = data[offset + 4 : offset + 8]
        payload = data[offset + 8 : offset + 8 + length]
        if len(payload) != length:
            raise PngFormatError(
                f"truncated {kind!r} chunk: declared {length} bytes, found {len(payload)}"
            )
        # PNG's own integrity check, and it costs about a microsecond for a
        # whole frame. What it buys is that a golden mangled in transit — a
        # text-mode checkout translating CRLF is the classic one — becomes a
        # typed error instead of silently different pixels compared against a
        # gate that then reports a regression nobody made.
        (declared_crc,) = struct.unpack(">I", data[offset + 8 + length : offset + 12 + length])
        actual_crc = zlib.crc32(kind + payload) & 0xFFFFFFFF
        if declared_crc != actual_crc:
            raise PngFormatError(
                f"CRC mismatch on the {kind.decode('ascii', 'replace')} chunk "
                f"(declared {declared_crc:#010x}, computed {actual_crc:#010x}). "
                "The file is corrupt; comparing its pixels against a golden "
                "would report a regression nobody made."
            )
        if kind == b"IHDR":
            header = struct.unpack(">IIBBBBB", payload)
        elif kind == b"IDAT":
            idat.append(payload)
        elif kind == b"IEND":
            break
        offset += 12 + length
    if header is None:
        raise PngFormatError("no IHDR chunk")
    if not idat:
        raise PngFormatError("no IDAT chunk")
    return header, zlib.decompress(b"".join(idat))


def _unfilter(raw: bytes, *, height: int, width: int, bpp: int) -> Any:
    """Reverse the per-row filters. See the module docstring for why bytearray."""
    import numpy as np

    stride = width * bpp
    if len(raw) != height * (stride + 1):
        raise PngFormatError(
            f"decompressed IDAT is {len(raw)} bytes; {height} rows of "
            f"{stride} + 1 filter byte need {height * (stride + 1)}"
        )
    out = bytearray(height * stride)
    prev = bytearray(stride)
    pos = 0
    written = 0
    for row in range(height):
        filter_type = raw[pos]
        pos += 1
        cur = bytearray(raw[pos : pos + stride])
        pos += stride
        if filter_type == 0:
            pass
        elif filter_type == 1:  # Sub
            for x in range(bpp, stride):
                cur[x] = (cur[x] + cur[x - bpp]) & 0xFF
        elif filter_type == 2:  # Up
            for x in range(stride):
                cur[x] = (cur[x] + prev[x]) & 0xFF
        elif filter_type == 3:  # Average
            for x in range(bpp):
                cur[x] = (cur[x] + (prev[x] >> 1)) & 0xFF
            for x in range(bpp, stride):
                cur[x] = (cur[x] + ((cur[x - bpp] + prev[x]) >> 1)) & 0xFF
        elif filter_type == 4:  # Paeth
            for x in range(bpp):
                cur[x] = (cur[x] + prev[x]) & 0xFF
            for x in range(bpp, stride):
                a = cur[x - bpp]
                b = prev[x]
                c = prev[x - bpp]
                p = a + b - c
                pa = p - a
                pa = -pa if pa < 0 else pa
                pb = p - b
                pb = -pb if pb < 0 else pb
                pc = p - c
                pc = -pc if pc < 0 else pc
                cur[x] = (
                    cur[x] + (a if (pa <= pb and pa <= pc) else (b if pb <= pc else c))
                ) & 0xFF
        else:
            raise PngFormatError(
                f"row {row} declares filter type {filter_type}; PNG defines 0-4"
            )
        out[written : written + stride] = cur
        written += stride
        prev = cur
    # `np.frombuffer` over the bytearray itself, not over `bytes(out)`: the
    # latter is immutable, so the returned array would be read-only and a
    # caller that wants to perturb a decoded frame — which is exactly what a
    # test proving the gate can go red does — gets a confusing
    # `assignment destination is read-only` instead.
    return np.frombuffer(out, np.uint8).reshape(height, width, bpp)


def decode_png(data: bytes) -> Any:
    """Decode an 8-bit truecolour PNG to ``(H, W, C)`` uint8, ``C`` in ``{3, 4}``.

    Refuses — rather than approximates — 16-bit, palette, greyscale and
    interlaced images, naming what it found.

    >>> import numpy as np
    >>> a = np.array([[[1, 2, 3], [4, 5, 6]]], np.uint8)
    >>> np.array_equal(decode_png(encode_png(a)), a)
    True
    """
    (width, height, depth, colour_type, compression, filter_method, interlace), raw = (
        _parse_chunks(data)
    )
    if depth != SUPPORTED_BIT_DEPTH:
        raise PngFormatError(
            f"bit depth {depth} is not supported (only {SUPPORTED_BIT_DEPTH}). "
            "Truncating 16-bit samples would silently halve every code value."
        )
    if interlace:
        raise PngFormatError(
            "interlaced (Adam7) PNGs are not supported; the golden writer never "
            "produces one and Chromium's canvas screenshot does not either"
        )
    if compression != 0 or filter_method != 0:
        raise PngFormatError(
            f"unsupported compression={compression} filter_method={filter_method}; "
            "PNG defines only 0 for both"
        )
    if colour_type not in _CHANNELS_BY_COLOUR_TYPE:
        raise PngFormatError(
            f"colour type {colour_type} "
            f"({_COLOUR_TYPE_NAMES.get(colour_type, 'unknown')}) is not supported; "
            f"this module decodes {sorted(_CHANNELS_BY_COLOUR_TYPE)}"
        )
    bpp = _CHANNELS_BY_COLOUR_TYPE[colour_type]
    return _unfilter(raw, height=height, width=width, bpp=bpp)


def to_rgb(arr: Any) -> Any:
    """Drop a **fully opaque** alpha channel, refusing to drop a meaningful one.

    The golden gate compares RGB (:func:`an.bench.metrics.golden_comparison`
    takes three channels). Dropping alpha unconditionally would make a
    transparency regression invisible to the one gate that exists to see
    changes, so a non-opaque alpha is an error rather than a silent narrowing.

    >>> import numpy as np
    >>> to_rgb(np.full((1, 1, 4), 255, np.uint8)).shape
    (1, 1, 3)
    """
    import numpy as np

    a = np.asarray(arr)
    if a.ndim != 3:
        raise PngFormatError(f"expected an (H, W, C) array; got shape {tuple(a.shape)}")
    channels = a.shape[2]
    if channels == RGB_CHANNELS:
        return a
    if channels != RGB_CHANNELS + 1:
        raise PngFormatError(f"cannot reduce {channels} channels to RGB")
    alpha = a[..., 3]
    opaque = int(alpha.min())
    if opaque != 255:
        raise PngFormatError(
            f"refusing to drop a meaningful alpha channel (minimum {opaque}, "
            "not 255). The golden gate compares RGB, so a transparency change "
            "would be invisible to it."
        )
    return a[..., :RGB_CHANNELS]


def read_png(path: Any) -> Any:
    """``(H, W, 3)`` uint8 for a PNG on disk, alpha dropped only if opaque."""
    from pathlib import Path

    return to_rgb(decode_png(Path(path).read_bytes()))


def write_png(path: Any, rgb: Any, *, level: int = DFLT_ZLIB_LEVEL) -> Any:
    """Write ``rgb`` as a filter-0 PNG and **verify the round trip** before returning.

    The verification is not defensive noise: it is the only thing standing
    between a bug in this module's own encoder and a committed golden that
    silently disagrees with the frame it was blessed from. Research §3 asks for
    exactly this — "assert the round trip at bless time against the in-memory
    screenshot pixels, so a bug in `an`'s own encoder cannot hide".
    """
    import numpy as np
    from pathlib import Path

    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(encode_png(rgb, level=level))
    # Read back from DISK, not from the buffer. Verifying the buffer proves the
    # encoder round-trips; verifying the file also proves the bytes reached the
    # disk intact, which is the thing a human is about to commit. It costs one
    # extra read of a few kilobytes, at the one moment in the lifecycle where
    # somebody is looking.
    if not np.array_equal(decode_png(out.read_bytes()), np.asarray(rgb)):
        out.unlink(missing_ok=True)
        raise PngFormatError(
            f"{out} does not decode back to the array it was given. Refusing to "
            "leave behind a golden that does not represent the frame it was "
            "blessed from."
        )
    return out
