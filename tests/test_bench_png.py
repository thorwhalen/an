"""The golden corpus's PNG codec (an#38).

Every test here runs in the **default** CI leg: numpy is a hard dependency and
this module imports nothing else, which is the point — family B is render-side,
and a golden gate that needed ffmpeg would be a gate that main CI can never see
even in principle.

Each test names the one-line production mutation it exists to catch. That is
not decoration: the an#36 sweep found five surviving mutants, two of which were
guards asserting a table's *contents* rather than the check that reads it.
"""

from __future__ import annotations

import struct
import zlib

import numpy as np
import pytest

from an.bench.png import (
    DFLT_ZLIB_LEVEL,
    PNG_SIGNATURE,
    PngFormatError,
    decode_png,
    encode_png,
    read_png,
    to_rgb,
    write_png,
)


def _chunk(kind: bytes, payload: bytes) -> bytes:
    return (
        struct.pack(">I", len(payload))
        + kind
        + payload
        + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)
    )


def _paeth(a: int, b: int, c: int) -> int:
    p = a + b - c
    pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
    return a if (pa <= pb and pa <= pc) else (b if pb <= pc else c)


def _filtered_png(rgb: np.ndarray, filter_type: int) -> bytes:
    """Encode ``rgb`` applying ONE filter type to every row.

    A hand-rolled encoder rather than a real frame, because a real frame does
    not contain all five filter types and a decoder tested only on what
    Chromium happens to emit is a decoder tested on three of them.
    """
    h, w, _ = rgb.shape
    stride = w * 3
    raw = bytearray()
    prev = bytearray(stride)
    for y in range(h):
        line = bytearray(rgb[y].reshape(stride).tolist())
        out = bytearray(stride)
        for x in range(stride):
            left = line[x - 3] if x >= 3 else 0
            up = prev[x]
            up_left = prev[x - 3] if x >= 3 else 0
            if filter_type == 0:
                out[x] = line[x]
            elif filter_type == 1:
                out[x] = (line[x] - left) & 0xFF
            elif filter_type == 2:
                out[x] = (line[x] - up) & 0xFF
            elif filter_type == 3:
                out[x] = (line[x] - ((left + up) >> 1)) & 0xFF
            elif filter_type == 4:
                out[x] = (line[x] - _paeth(left, up, up_left)) & 0xFF
        raw.append(filter_type)
        raw.extend(out)
        prev = line
    header = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)
    return (
        PNG_SIGNATURE
        + _chunk(b"IHDR", header)
        + _chunk(b"IDAT", zlib.compress(bytes(raw), 6))
        + _chunk(b"IEND", b"")
    )


def _sample(seed: int = 0, *, height: int = 7, width: int = 5) -> np.ndarray:
    """Deliberately not flat: a flat image decodes correctly under a broken filter."""
    rng = np.random.default_rng(seed)
    return rng.integers(0, 256, (height, width, 3), dtype=np.uint8)


@pytest.mark.parametrize("filter_type", [0, 1, 2, 3, 4])
def test_every_png_filter_type_decodes_exactly(filter_type):
    """MUTATION: in `_unfilter`'s Paeth branch, `pb <= pc` -> `pb < pc`.

    Also catches dropping any one of the five branches. The sample is random
    rather than flat on purpose: a flat image round-trips through a *broken*
    predictor too, because every predictor agrees when every neighbour is equal.
    """
    rgb = _sample(filter_type)
    assert np.array_equal(decode_png(_filtered_png(rgb, filter_type)), rgb)


def test_the_writer_emits_filter_zero_on_every_row():
    """MUTATION: in `encode_png`, `raw = np.zeros(...)` -> `np.ones(...)`.

    Filter 0 is what makes a committed golden a function of the pixel data
    alone, so a Chromium filter-heuristic change produces no diff when the
    picture has not moved.
    """
    rgb = _sample(1, height=9, width=4)
    data = encode_png(rgb)
    # Re-derive the raw stream and read back the leading filter byte per row.
    idat = b""
    offset = 8
    while offset < len(data):
        (length,) = struct.unpack(">I", data[offset : offset + 4])
        if data[offset + 4 : offset + 8] == b"IDAT":
            idat += data[offset + 8 : offset + 8 + length]
        offset += 12 + length
    raw = zlib.decompress(idat)
    stride = rgb.shape[1] * 3
    filters = {raw[row * (stride + 1)] for row in range(rgb.shape[0])}
    assert filters == {0}, f"expected every row filter to be 0, found {sorted(filters)}"


def test_the_round_trip_is_exact():
    """MUTATION: in `encode_png`, `raw[:, 1:]` -> `raw[:, :-1]` (an off-by-one)."""
    rgb = _sample(2, height=13, width=11)
    assert np.array_equal(decode_png(encode_png(rgb)), rgb)


@pytest.mark.parametrize(
    "field,value,match",
    [
        ("depth", 16, "bit depth 16"),
        ("colour_type", 3, "indexed / palette"),
        ("colour_type", 0, "greyscale"),
        ("interlace", 1, "interlaced"),
        ("compression", 1, "unsupported compression"),
    ],
)
def test_an_unsupported_png_is_refused_by_name(field, value, match):
    """MUTATION: delete any one guard clause in `decode_png`.

    Refusing matters more than it looks: the alternative to a typed error is
    returning a plausible array, and a golden gate that compares a plausible
    array is worse than one that does not run.
    """
    fields = {
        "width": 2,
        "height": 2,
        "depth": 8,
        "colour_type": 2,
        "compression": 0,
        "filter_method": 0,
        "interlace": 0,
    }
    fields[field] = value
    header = struct.pack(
        ">IIBBBBB",
        fields["width"],
        fields["height"],
        fields["depth"],
        fields["colour_type"],
        fields["compression"],
        fields["filter_method"],
        fields["interlace"],
    )
    body = zlib.compress(b"\x00" * (fields["height"] * (fields["width"] * 3 + 1)))
    data = (
        PNG_SIGNATURE
        + _chunk(b"IHDR", header)
        + _chunk(b"IDAT", body)
        + _chunk(b"IEND", b"")
    )
    with pytest.raises(PngFormatError, match=match):
        decode_png(data)


def test_every_idat_chunk_is_read_not_only_the_first():
    """MUTATION: in `_parse_chunks`, `idat.append(payload)` -> `idat = [payload]`.

    Chromium splits the stream: `single_character`'s frame 0 has TWO IDAT chunks
    (1,720 and 6 bytes), and across this repo's rendered frames the count runs
    to nine. A first-chunk-only reader decodes THIS module's own single-chunk
    output perfectly and corrupts every real frame — the exact asymmetry that
    makes an encoder validating its own decoder worthless.
    """
    rgb = _sample(8, height=6, width=6)
    raw = bytearray()
    for row in rgb:
        raw.append(0)
        raw.extend(row.reshape(-1).tolist())
    compressed = zlib.compress(bytes(raw), 6)
    cut = len(compressed) // 3
    assert cut > 0, "the stream must actually split"
    header = struct.pack(">IIBBBBB", rgb.shape[1], rgb.shape[0], 8, 2, 0, 0, 0)
    data = (
        PNG_SIGNATURE
        + _chunk(b"IHDR", header)
        + _chunk(b"IDAT", compressed[:cut])
        + _chunk(b"IDAT", compressed[cut : 2 * cut])
        + _chunk(b"IDAT", compressed[2 * cut :])
        + _chunk(b"IEND", b"")
    )
    assert np.array_equal(decode_png(data), rgb)


def test_a_file_that_is_not_a_png_is_refused():
    with pytest.raises(PngFormatError, match="signature"):
        decode_png(b"not a png at all, not even close")


def test_a_truncated_idat_is_refused_rather_than_padded():
    """MUTATION: in `_unfilter`, drop the length check and slice defensively.

    numpy would happily reshape a short buffer into fewer rows, and the gate
    would then compare a differently-shaped array and report a shape mismatch
    for a file that is simply corrupt.
    """
    rgb = _sample(3)
    data = bytearray(encode_png(rgb))
    # Rewrite IDAT with one row's worth of data missing.
    short = zlib.compress(b"\x00" * ((rgb.shape[0] - 1) * (rgb.shape[1] * 3 + 1)), 6)
    header = struct.pack(">IIBBBBB", rgb.shape[1], rgb.shape[0], 8, 2, 0, 0, 0)
    data = (
        PNG_SIGNATURE
        + _chunk(b"IHDR", header)
        + _chunk(b"IDAT", short)
        + _chunk(b"IEND", b"")
    )
    with pytest.raises(PngFormatError, match="decompressed IDAT"):
        decode_png(data)


def test_write_png_refuses_when_its_own_encoder_is_broken(tmp_path, monkeypatch):
    """MUTATION: delete the `np.array_equal` check in `write_png`.

    The round-trip assertion at bless time is the only thing between a bug in
    this module's own encoder and a committed golden that silently disagrees
    with the frame it was blessed from — research §3 asks for exactly it.
    """
    import an.bench.png as png_module

    rgb = _sample(4)
    monkeypatch.setattr(
        png_module, "encode_png", lambda arr, **kw: encode_png(np.zeros_like(arr))
    )
    with pytest.raises(PngFormatError, match="does not decode back"):
        write_png(tmp_path / "broken.png", rgb)
    assert not (tmp_path / "broken.png").exists(), (
        "a golden that failed its own round trip must not reach the disk"
    )


def test_write_png_then_read_png_round_trips_through_the_filesystem(tmp_path):
    rgb = _sample(5, height=6, width=6)
    path = write_png(tmp_path / "nested" / "g.png", rgb)
    assert np.array_equal(read_png(path), rgb)


def test_encode_refuses_a_float_array():
    """MUTATION: drop the dtype check. A float array truncates silently."""
    with pytest.raises(PngFormatError, match="uint8"):
        encode_png(np.zeros((2, 2, 3), np.float32))


def test_encode_refuses_an_rgba_array():
    """Alpha is dropped at the boundary by `to_rgb`, explicitly, or not at all."""
    with pytest.raises(PngFormatError, match=r"\(H, W, 3\)"):
        encode_png(np.zeros((2, 2, 4), np.uint8))


def test_to_rgb_drops_an_opaque_alpha_and_refuses_a_meaningful_one():
    """MUTATION: in `to_rgb`, `if opaque != 255` -> `if False`.

    Dropping alpha unconditionally would make a transparency regression
    invisible to the one gate whose whole job is to see changes.
    """
    opaque = np.full((2, 2, 4), 255, np.uint8)
    assert to_rgb(opaque).shape == (2, 2, 3)
    translucent = opaque.copy()
    translucent[0, 0, 3] = 128
    with pytest.raises(PngFormatError, match="meaningful alpha"):
        to_rgb(translucent)


def test_identical_pixels_survive_a_different_compression_level():
    """The criterion is pixels; the bytes are an implementation detail.

    Two encodings of the same array at different zlib levels differ as files
    and are identical as pictures. Any gate that keys on the file bytes goes
    red here — which is the whole reason the golden criterion is
    `sha256(decoded array)` and never `sha256(file)`.
    """
    rgb = _sample(6, height=32, width=32)
    low = encode_png(rgb, level=1)
    high = encode_png(rgb, level=DFLT_ZLIB_LEVEL)
    assert low != high, "the two levels must actually produce different bytes"
    assert np.array_equal(decode_png(low), decode_png(high))


#: The committed fixture's content, as ARITHMETIC. Neither the bytes nor the
#: expected values come from this package: the PNG was encoded by ffmpeg/libpng
#: with `-pred mixed`, and the pixels are recomputed here from the formula that
#: generated them. That is what makes the filter tests an external check rather
#: than this module's encoder validating this module's decoder.
_FIXTURE_SHAPE: tuple[int, int] = (40, 24)


def _fixture_pattern() -> np.ndarray:
    """Five horizontal bands, each chosen to make libpng pick a different filter."""
    height, width = _FIXTURE_SHAPE
    out = np.zeros((height, width, 3), np.uint8)
    y, x = np.mgrid[0:height, 0:width]
    out[0:8] = np.random.default_rng(20260821).integers(
        0, 256, (8, width, 3), dtype=np.uint8
    )
    out[8:16] = (np.stack([x, x * 3, x * 7], -1) % 256).astype(np.uint8)[8:16]
    out[16:24] = (np.stack([y * 11, y * 5, y * 17], -1) % 256).astype(np.uint8)[16:24]
    out[24:32] = (
        np.stack([(y + x) * 2, (y + x) * 2 + 1, (y + x) * 2 + 2], -1) % 256
    ).astype(np.uint8)[24:32]
    out[32:40] = np.stack(
        [(y * x) % 256, (y * x + 31) % 256, (y * x + 91) % 256], -1
    ).astype(np.uint8)[32:40]
    return out


def test_a_foreign_encoders_adaptive_png_decodes_to_the_expected_pixels():
    """The default leg's ONE external reference for the unfilter code.

    MUTATION: any change to `_unfilter`'s Sub, Up, Average or Paeth branch.

    Without this, the filter tests pair a test-local forward filterer against
    `_unfilter`, both written from the same reading of the spec — the symmetric
    -bug shape, where an encoder and a decoder agree with each other and with
    nothing else. Here the bytes come from ffmpeg/libpng and the expected
    pixels come from arithmetic, so neither side is ours.

    The fixture carries all five filter types in 829 bytes (measured:
    None x2, Sub x5, Up x23, Average x1, Paeth x9) — Average in particular is
    1.7% of rows across this repo's rendered frames, so a decoder missing it
    passes on `single_character` and corrupts `park_bench_cartoon`.
    """
    from pathlib import Path

    fixture = Path(__file__).parent / "fixtures" / "png" / "adaptive_filters.png"
    data = fixture.read_bytes()
    filters = _row_filter_types(data)
    assert set(filters) == {0, 1, 2, 3, 4}, (
        f"the fixture must exercise every filter type; it uses {sorted(set(filters))}"
    )
    assert np.array_equal(decode_png(data), _fixture_pattern())


def _row_filter_types(data: bytes) -> list[int]:
    """The per-row filter byte of a PNG, read without using `an.bench.png`."""
    offset, idat, header = 8, b"", None
    while offset < len(data):
        (length,) = struct.unpack(">I", data[offset : offset + 4])
        kind = data[offset + 4 : offset + 8]
        if kind == b"IHDR":
            header = struct.unpack(">IIBBBBB", data[offset + 8 : offset + 8 + length])
        elif kind == b"IDAT":
            idat += data[offset + 8 : offset + 8 + length]
        offset += 12 + length
    width, height = header[0], header[1]
    raw = zlib.decompress(idat)
    stride = width * 3
    return [raw[row * (stride + 1)] for row in range(height)]


def test_a_corrupted_chunk_is_refused_by_its_crc():
    """MUTATION: delete the CRC check in `_parse_chunks`.

    The realistic corruption is not a hostile edit — it is a text-mode checkout
    translating CRLF inside the IDAT stream. Without the CRC, that decodes to
    different pixels and the gate reports a regression nobody made.
    """
    rgb = _sample(7, height=8, width=8)
    data = bytearray(encode_png(rgb))
    # Flip a bit inside the IDAT payload, leaving every length field intact.
    offset = 8
    while data[offset + 4 : offset + 8] != b"IDAT":
        (length,) = struct.unpack(">I", data[offset : offset + 4])
        offset += 12 + length
    data[offset + 9] ^= 0x01
    with pytest.raises(PngFormatError, match="CRC mismatch"):
        decode_png(bytes(data))


@pytest.mark.ffmpeg
def test_our_decoder_agrees_with_ffmpeg_on_a_real_rendered_frame(tmp_path):
    """Two decoders, one answer — in BOTH directions.

    The bench decodes the metrics panel's frames with ffmpeg and the golden
    frames with this module. Two paths that must agree, so the agreement is
    asserted against an independent implementation rather than assumed.

    MUTATION: in `_unfilter`, swap the Sub and Up branches.
    """
    import subprocess
    from pathlib import Path

    frames = sorted(
        Path("examples").glob("*/.an/render_work/*/frames/frame_000000.png")
    )
    if not frames:
        pytest.skip("no rendered example frames in this checkout")

    def ffmpeg_rgb(path, height, width):
        out = subprocess.run(
            [
                "ffmpeg",
                "-v",
                "error",
                "-i",
                str(path),
                "-pix_fmt",
                "rgb24",
                "-f",
                "rawvideo",
                "-",
            ],
            capture_output=True,
            check=True,
        ).stdout
        return np.frombuffer(out, np.uint8).reshape(height, width, 3)

    for frame in frames:
        ours = read_png(frame)
        h, w, _ = ours.shape
        assert np.array_equal(ours, ffmpeg_rgb(frame, h, w)), (
            f"decode disagreed on {frame}"
        )
        # And the other direction: what we WRITE must be readable by a real
        # PNG implementation, not only by our own reader.
        out = write_png(tmp_path / frame.parent.parent.name / "ours.png", ours)
        assert np.array_equal(ffmpeg_rgb(out, h, w), ours), f"ffmpeg disagreed on {out}"
