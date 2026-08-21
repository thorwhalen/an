"""The x264 encode knobs are pinned, and the colour tags actually reach the file.

an#34. Two different promises, so two different tests:

- The **constructed command** carries the pinned knobs. Checked by intercepting
  `subprocess.run`, so it needs no ffmpeg binary and runs on every push — which
  matters, because the rest of what the encode does is invisible to the main CI.
- The **resulting file** carries all four colour fields. This one needs a real
  encode, and it exists because the obvious spelling silently half-works: with
  ffmpeg's `-color_primaries` / `-color_trc` alone, ffprobe reports
  `color_space=bt709` and `color_primaries=unknown` / `color_transfer=unknown`.
  A half-tagged file is worse than an untagged one — the player stops guessing
  the matrix but still guesses the primaries — and nothing in the command-level
  test can see the difference.
"""

from __future__ import annotations

import struct
import subprocess
import zlib
from pathlib import Path

import pytest

from an.adapters.cutout import render as render_mod
from an.adapters.cutout.render import DETERMINISTIC_X264_ARGS, _ffmpeg_mux

#: knob -> why it is pinned, quoted in the failure so the reason travels with it.
REQUIRED_KNOBS = {
    ("-threads", "1"): (
        "`auto` raises lookahead_threads above 1 at roughly -threads >= 12; a "
        "big CI runner crosses that line and a 4-core dev box never will"
    ),
    ("-crf", "23"): "libx264's default today; pinned against a build whose differs",
    ("-preset", "medium"): (
        "preset swings distinct colour counts 2.3x non-monotonically, against a "
        "crf18->23 signal of 1.35x"
    ),
    ("-colorspace", "bt709"): (
        "sets the RGB->YUV conversion matrix, not just the tag — without it the "
        "encode is BT.601 while a >=576-line player decodes BT.709"
    ),
    (
        "-color_range",
        "tv",
    ): "a no-op today; pinned so a differing default cannot move output",
}


def _pairs(args: tuple[str, ...]) -> set[tuple[str, str]]:
    return set(zip(args, args[1:]))


@pytest.mark.parametrize("knob", sorted(REQUIRED_KNOBS))
def test_the_knob_is_pinned(knob):
    assert knob in _pairs(DETERMINISTIC_X264_ARGS), (
        f"{' '.join(knob)} is pinned because {REQUIRED_KNOBS[knob]}"
    )


def test_the_vui_params_are_pinned():
    """ffmpeg's own primaries/transfer flags do not reach the bitstream."""
    pairs = dict(_pairs(DETERMINISTIC_X264_ARGS))
    params = pairs.get("-x264-params", "")
    for field in ("colorprim=bt709", "transfer=bt709", "colormatrix=bt709"):
        assert field in params, (
            f"{field} missing from -x264-params — without it the mp4 is "
            f"half-tagged: matrix set, primaries and transfer unknown"
        )


def test_the_mux_command_carries_the_pins(tmp_path, monkeypatch):
    """The constant is not the contract — the command ffmpeg receives is."""
    seen: dict[str, list[str]] = {}

    class _Result:
        returncode = 0
        stderr = ""

    def fake_run(cmd, *a, **kw):
        seen["cmd"] = list(cmd)
        Path(cmd[-1]).write_bytes(b"")  # the mux checks the output exists
        return _Result()

    monkeypatch.setattr(render_mod.subprocess, "run", fake_run)
    _ffmpeg_mux(tmp_path, 24, tmp_path / "out.mp4")

    cmd = seen["cmd"]
    missing = [
        " ".join(knob) for knob in REQUIRED_KNOBS if knob not in _pairs(tuple(cmd))
    ]
    assert not missing, f"the mux command does not pass: {missing}\ncmd: {cmd}"


def _write_flat_png(
    path: Path, width: int, height: int, rgb: tuple[int, int, int]
) -> None:
    """A minimal filter-0 PNG, so this test needs no image library."""

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    raw = b"".join(b"\x00" + bytes(rgb) * width for _ in range(height))
    path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )


@pytest.mark.ffmpeg
def test_the_encoded_file_carries_all_four_colour_fields(tmp_path):
    frames = tmp_path / "frames"
    frames.mkdir()
    for i in range(4):
        _write_flat_png(
            frames / (render_mod.DEFAULT_FRAME_PNG_PATTERN % i), 32, 32, (200, 40, 40)
        )

    out = tmp_path / "out.mp4"
    _ffmpeg_mux(frames, 24, out)

    probe = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=color_space,color_primaries,color_transfer,color_range",
            "-of",
            "default=noprint_wrappers=1",
            str(out),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    fields = dict(
        line.split("=", 1) for line in probe.stdout.strip().splitlines() if "=" in line
    )
    assert fields == {
        "color_space": "bt709",
        "color_primaries": "bt709",
        "color_transfer": "bt709",
        "color_range": "tv",
    }, (
        "the mp4 is not fully colour-tagged; `unknown` for primaries or transfer "
        f"means the -x264-params half was dropped. got: {fields}"
    )
