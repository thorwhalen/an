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


def test_every_command_that_writes_an_mp4_asks_for_faststart(tmp_path, monkeypatch):
    """MUTATION: drop `*MP4_FASTSTART_ARGS` from any ONE of the three commands.

    Three commands build the one file a user receives, and each of the last two
    re-lays the container with `-c copy` — which writes `moov` LAST. The flag
    was on `_ffmpeg_mux` alone, so it applied only to `silent.mp4`, a per-shot
    intermediate nobody is handed. A per-command test is what makes "it's on
    the mux" stop counting as "the deliverable has it".

    No ffmpeg needed — this intercepts `subprocess.run`, so it runs in the
    default CI leg. The file-level proof is
    `test_the_delivered_mp4_puts_moov_before_mdat`, which does need ffmpeg.
    """
    import an.render as project_render
    from an.base import MP4_FASTSTART_ARGS

    seen: dict[str, list[str]] = {}

    class _Result:
        returncode = 0
        stderr = ""

    def fake_run(cmd, *a, **kw):
        seen[cmd[-1]] = list(cmd)
        Path(cmd[-1]).write_bytes(b"")
        return _Result()

    monkeypatch.setattr(render_mod.subprocess, "run", fake_run)
    monkeypatch.setattr(project_render.subprocess, "run", fake_run)
    monkeypatch.setattr(project_render.shutil, "which", lambda _n: "/usr/bin/ffmpeg")

    mux_out = tmp_path / "silent.mp4"
    render_mod._ffmpeg_mux(tmp_path, 24, mux_out)

    shot_out = tmp_path / "shot.mp4"
    render_mod._ffmpeg_add_audio(mux_out, [], shot_out, 1.0)

    a_mp4, b_mp4 = tmp_path / "a.mp4", tmp_path / "b.mp4"
    a_mp4.write_bytes(b"")
    b_mp4.write_bytes(b"")
    concat_out = tmp_path / "main.mp4"
    project_render._ffmpeg_concat([a_mp4, b_mp4], concat_out)

    flag, value = MP4_FASTSTART_ARGS
    for label, out in (
        ("_ffmpeg_mux", mux_out),
        ("_ffmpeg_add_audio", shot_out),
        ("_ffmpeg_concat", concat_out),
    ):
        cmd = seen[str(out)]
        assert (flag, value) in _pairs(tuple(cmd)), (
            f"{label} does not pass {flag} {value}. Every command that writes an "
            f"mp4 must, because `-c copy` re-lays the container and writes moov "
            f"last — a flag on one stage is silently undone by the next.\n"
            f"cmd: {cmd}"
        )


@pytest.mark.ffmpeg
def test_the_delivered_mp4_puts_moov_before_mdat(tmp_path):
    """MUTATION: drop `*MP4_FASTSTART_ARGS` from `_ffmpeg_add_audio` (fails BOTH
    legs) or from `_ffmpeg_concat` (fails the multi-shot leg only).

    The command-level test above is not sufficient on its own, and its
    insufficiency is the actual history here: `_ffmpeg_mux` passed the flag for
    years and no delivered file carried it. This walks the real atom table of
    the two files a user can actually receive — the single-shot one (which
    `_ffmpeg_concat` reaches by `shutil.copy`, so only the shot mux can fix it)
    and the concatenated one.

    Two separate legs deliberately: five of the six bench corpus scenes are
    single-shot, so a multi-shot-only assertion would leave the common case
    untested.
    """
    import an.render as project_render

    frames = tmp_path / "frames"
    frames.mkdir()
    for i in range(4):
        _write_flat_png(
            frames / (render_mod.DEFAULT_FRAME_PNG_PATTERN % i), 32, 32, (200, 40, 40)
        )
    silent = tmp_path / "silent.mp4"
    render_mod._ffmpeg_mux(frames, 24, silent)
    shot = tmp_path / "shot.mp4"
    render_mod._ffmpeg_add_audio(silent, [], shot, 4 / 24)

    single = tmp_path / "single.mp4"
    project_render._ffmpeg_concat([shot], single)

    shot_b = tmp_path / "shot_b.mp4"
    shot_b.write_bytes(shot.read_bytes())
    multi = tmp_path / "multi.mp4"
    project_render._ffmpeg_concat([shot, shot_b], multi)

    for label, path in (("single-shot (shutil.copy)", single), ("concat", multi)):
        order = [name for name, _off in _top_level_atoms(path)]
        offsets = dict(_top_level_atoms(path))
        assert offsets["moov"] < offsets["mdat"], (
            f"the {label} deliverable is not faststart: atoms {order}, "
            f"moov@{offsets['moov']} mdat@{offsets['mdat']}. A player must "
            f"download the whole file before it can start."
        )


@pytest.mark.ffmpeg
def test_faststart_on_the_concat_is_a_remux_not_a_re_encode(tmp_path):
    """MUTATION: change `_ffmpeg_concat`'s `"-c", "copy"` to `"-c:v", "libx264"`.

    an#57 flagged this as UNVERIFIED and as the one way the change could do
    harm: if `-movflags +faststart` forced a transcode on the concat leg it
    would *create* the double encode epic #9 wrongly describes as existing. It
    does not — but that is a property of the ffmpeg build, so it is asserted
    rather than assumed. Measured on ffmpeg 8.1: the concatenated elementary
    stream is byte-identical to the inputs' streams appended.
    """
    import an.render as project_render

    frames = tmp_path / "frames"
    frames.mkdir()
    for i in range(4):
        _write_flat_png(
            frames / (render_mod.DEFAULT_FRAME_PNG_PATTERN % i), 32, 32, (200, 40, 40)
        )
    silent = tmp_path / "silent.mp4"
    render_mod._ffmpeg_mux(frames, 24, silent)
    a_mp4 = tmp_path / "a.mp4"
    render_mod._ffmpeg_add_audio(silent, [], a_mp4, 4 / 24)
    b_mp4 = tmp_path / "b.mp4"
    b_mp4.write_bytes(a_mp4.read_bytes())

    out = tmp_path / "main.mp4"
    project_render._ffmpeg_concat([a_mp4, b_mp4], out)

    def _annexb(p: Path) -> bytes:
        return subprocess.run(
            [
                "ffmpeg",
                "-v",
                "error",
                "-i",
                str(p),
                "-map",
                "0:v",
                "-c",
                "copy",
                "-bsf:v",
                "h264_mp4toannexb",
                "-f",
                "h264",
                "-",
            ],
            capture_output=True,
            check=True,
        ).stdout

    assert _annexb(out) == _annexb(a_mp4) + _annexb(b_mp4), (
        "the concat's video stream is not the two inputs' streams appended, so "
        "`-f concat -c copy -movflags +faststart` re-encoded on this ffmpeg "
        "build. That is the double encode an#57 warned the flag could create; "
        "drop the flag from _ffmpeg_concat and re-open the question."
    )


def _top_level_atoms(path: Path) -> list[tuple[str, int]]:
    """``[(atom_name, byte_offset), ...]`` for an mp4's top-level boxes.

    Hand-rolled so the faststart tests need no mp4 library; the same 8-byte
    header walk `ffprobe -v trace` reports, without the 3 MB of trace.
    """
    data = path.read_bytes()
    out: list[tuple[str, int]] = []
    off = 0
    while off + 8 <= len(data):
        size = struct.unpack(">I", data[off : off + 4])[0]
        name = data[off + 4 : off + 8].decode("latin-1")
        if size == 1:
            size = struct.unpack(">Q", data[off + 8 : off + 16])[0]
        elif size == 0:
            size = len(data) - off
        out.append((name, off))
        if size <= 0:
            break
        off += size
    return out


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
