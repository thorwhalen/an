"""The lossless leg's relationship to the delivered encode.

The leg exists to BE what libx264 received (see `an/bench/imageio.py`'s module
docstring), and every encode-side metric is measured against it. Two module
globals reach its command and they are reached differently on purpose:

- ``DETERMINISTIC_X264_ARGS`` is bound at import, so a lever that rebinds it
  cannot move the reference.
- ``DEFAULT_PIX_FMT`` is resolved at call time, so the leg is encoded in the
  format the delivered file was.

That asymmetry is the correction an#72 turned on, and it is what these tests
pin. Before it, the leg hardcoded ``-pix_fmt yuv420p`` while the delivered
encode read the module global — so under ``an render --pix-fmt yuv444p`` the
two legs differed in *two* dimensions and the leg's own docstring ("identical to
the delivered encode except for the rate control") was false. Measured on the
corpus, that mismatch changed the SIGN of family E on three of ten scenes.

Only the last test needs ffmpeg. The rest are argv assertions and run in the
default CI leg, deliberately: gating a test that needs no binary behind a
binary is how an#22's thirty-four tests stopped being collected.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from an.adapters.cutout import render
from an.bench import imageio

#: What `main` emitted before an#72, verbatim. A literal rather than a
#: construction: the point is that a default render is byte-identical, and a
#: pin that derives itself from the code it guards cannot see the code change.
DEFAULT_LEG_ARGV: tuple[str, ...] = (
    "ffmpeg",
    "-y",
    "-loglevel",
    "error",
    "-framerate",
    "24",
    "-i",
    "FRAMES/frame_%06d.png",
    "-c:v",
    "libx264",
    "-pix_fmt",
    "yuv420p",
    "-qp",
    "0",
    "-threads",
    "1",
    "-preset",
    "medium",
    "-colorspace",
    "bt709",
    "-color_primaries",
    "bt709",
    "-color_trc",
    "bt709",
    "-color_range",
    "tv",
    "-x264-params",
    "colorprim=bt709:transfer=bt709:colormatrix=bt709",
    "-movflags",
    "+faststart",
    "OUT.mp4",
)


def _argv(pix_fmt: str | None = None) -> tuple[str, ...]:
    cmd = imageio.lossless_encode_command(
        Path("FRAMES"), 24, Path("OUT.mp4"), pix_fmt=pix_fmt
    )
    return tuple(cmd)


@pytest.fixture
def delivered_pix_fmt(monkeypatch):
    """Rebind the module global the way the bench's lever would."""

    def rebind(fmt: str):
        monkeypatch.setattr(render, "DEFAULT_PIX_FMT", fmt)

    return rebind


def test_the_default_leg_is_unchanged_so_no_committed_ledger_row_is_invalidated():
    """MUTATION: change the resolved default, or reorder any flag.

    Every row in `misc/bench/ledger/` was captured against this exact command.
    an#72 is a correction to what happens under a 4:4:4 render and must not be
    a re-baseline of the 4:2:0 default — so this is a literal pin, not a
    derivation.
    """
    assert _argv() == DEFAULT_LEG_ARGV


def test_the_leg_is_encoded_in_the_delivered_pixel_format(delivered_pix_fmt):
    """MUTATION: restore the hardcoded ``"yuv420p"`` in `lossless_encode_command`.

    The leg's purpose is to be the plane libx264 received. Pinning its format
    while the delivered encode's is rebindable does not keep the reference
    lossless — it makes the reference a different colour pipeline.
    """
    delivered_pix_fmt("yuv444p")
    argv = _argv()
    i = argv.index("-pix_fmt")
    assert argv[i + 1] == "yuv444p", (
        "the lossless leg did not follow the delivered pixel format; every "
        "encode-side metric is now measured across a 4:2:0/4:4:4 conversion "
        "boundary that the reference exists to cancel"
    )
    # ...and nothing else moved with it.
    rest = argv[:i] + argv[i + 2 :]
    j = DEFAULT_LEG_ARGV.index("-pix_fmt")
    assert rest == DEFAULT_LEG_ARGV[:j] + DEFAULT_LEG_ARGV[j + 2 :]


def test_the_leg_is_pinned_in_its_encoder_settings_while_tracking_its_input_format(
    monkeypatch, delivered_pix_fmt
):
    """The asymmetry itself — the load-bearing claim, in one test.

    MUTATION: import ``DETERMINISTIC_X264_ARGS`` inside the function instead of
    at module scope (i.e. make the rate control call-time too). The leg would
    then follow ``high_crf`` and stop being lossless, which is the failure the
    import-time binding has always prevented.

    ``-crf`` must NOT track: it is an encoder setting, and a reference that
    moves with the lever measures nothing. ``-pix_fmt`` MUST track: it is not
    an encoder setting, it names what the encoder receives.
    """
    monkeypatch.setattr(
        render, "DETERMINISTIC_X264_ARGS", ("-threads", "1", "-crf", "51")
    )
    delivered_pix_fmt("yuv444p")
    argv = _argv()

    assert "-crf" not in argv and "51" not in argv, (
        "the lossless leg followed a rebound DETERMINISTIC_X264_ARGS; the "
        "reference must stay lossless under every lever"
    )
    assert argv[argv.index("-qp") + 1] == "0"
    assert "-preset" in argv, (
        "the leg lost the pinned encoder settings it had at import — it is "
        "reading the rebound tuple, not the pinned one"
    )
    assert argv[argv.index("-pix_fmt") + 1] == "yuv444p"


def test_an_unsupported_format_is_refused_here_too(delivered_pix_fmt):
    """MUTATION: resolve with ``pix_fmt or DEFAULT_PIX_FMT`` instead of
    ``_check_pix_fmt``.

    Resolution goes through the product's own validator rather than a second
    copy of the fallback, so the leg cannot be encoded in a format the
    delivered encode would have refused.
    """
    from an.adapters.cutout.render import CutoutRenderError

    with pytest.raises(CutoutRenderError, match="not one of"):
        _argv("rgb24")


@pytest.mark.ffmpeg
def test_a_tracking_leg_is_closer_to_the_encoders_input_than_a_pinned_one(tmp_path):
    """The argv tests say the flag moved; this says the flag mattered.

    MUTATION: hardcode ``"yuv420p"`` in `lossless_encode_command`, or assert on
    the tracking leg alone.

    Asserted as a COMPARISON within one build, never as ``== 0``. Whether this
    build's explicit RGB->YUV conversion reproduces libx264's exactly is a
    property of the ffmpeg build — it does on 8.1 and does not on the Linux
    runner's older one — and a hard equality on that is precisely the mistake
    `an/bench/imageio.py`'s docstring records. The claim here is relative and
    holds on any build: under a 4:4:4 delivery, a leg that tracks the format
    sits closer to the source conversion in CHROMA than a leg pinned to 4:2:0.
    """
    from an.bench.png import write_png

    h, w, n = 48, 64, 6
    frames = tmp_path / "frames"
    frames.mkdir()
    for i in range(n):
        a = np.full((h, w, 3), 255, np.uint8)
        # Saturated fills against black outlines — flat 2D art's own shape, and
        # the only content 4:2:0 visibly destroys. A grey ramp would make this
        # test pass for the wrong reason.
        a[8:40, 8 + i : 40 + i] = (255, 0, 0)
        a[8:40, 6 + i : 8 + i] = (0, 0, 0)
        a[16:24, 44:60] = (0, 200, 60)
        write_png(frames / f"frame_{i:06d}.png", a)

    src = imageio.source_yuv(frames, height=h, width=w, frames=n)

    def chroma_residual(pix_fmt: str) -> float:
        out = tmp_path / f"leg_{pix_fmt}.mp4"
        imageio.run_raw(
            imageio.lossless_encode_command(frames, 24, out, pix_fmt=pix_fmt)
        )
        leg = imageio.decoded_yuv(out, height=h, width=w)
        m = min(len(src), len(leg))
        assert m, "one of the legs decoded to zero frames"
        return float(
            np.abs(leg[:m, 1:].astype(np.int16) - src[:m, 1:].astype(np.int16)).mean()
        )

    tracking = chroma_residual("yuv444p")
    pinned = chroma_residual("yuv420p")
    assert tracking < pinned, (
        f"a 4:4:4 leg ({tracking}) is no closer to the encoder's input in "
        f"chroma than a 4:2:0 leg ({pinned}); if these are equal the decode is "
        "not reading chroma at all and every chroma number in the panel is "
        "measuring nothing"
    )
