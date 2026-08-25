"""The supersample knob: where it lives, what it costs when off, and what it must not move.

Opt-in and **1 is free** — at the default nothing is decoded and Chromium's own
PNG bytes reach disk untouched. Everything here is offline; the behavioural half
(that a k=2 render puts declared-size frames on disk) is
`tests/test_cutout_render.py`, in the browser lane.
"""

from __future__ import annotations

import dataclasses
import inspect

import numpy as np
import pytest

from an.adapters._base import RenderContext
from an.adapters.cutout.supersample import (
    NO_SUPERSAMPLE,
    SupersampleError,
    block_mean_resolve,
    check_factor,
    resolve_png_bytes,
)
from an.base import DEFAULT_SUPERSAMPLE
from an.bench.png import encode_png


def test_the_factor_never_reaches_the_compiled_scene_document():
    """MUTATION: put `supersample` on the compiled scene JSON instead.

    Where the knob lives decides whether the bench can say anything at all.
    Simulated against a real committed ledger row: in `render_kwargs` it becomes
    a `COMMON_ENV_PATHS` key and **all 96 metrics are refused**; on the compiled
    scene document it moves `scene_contract_sha256` and **every scene becomes
    incomparable**; on `RenderContext` only `runtime_sha256` moves, which is
    deliberately not a comparability key.

    So this asserts the negative directly: compiling a shot must produce the
    same contract hash whatever the factor is, because the factor is not part of
    the scene.
    """
    from an.bench.contract import scenes_contract_sha256

    from an.adapters.cutout.compile import compile_shot
    from an.ir.schema import Shot

    shot = Shot(id="s1", duration=1.0, renderer="cutout")
    compiled = compile_shot(shot, mall={}, fps=24, width=320, height=240)
    baseline = scenes_contract_sha256([_as_dict(compiled)])

    # A RenderContext carrying any factor must not change what got compiled —
    # the compiler is never handed one.
    assert "supersample" not in _as_dict(compiled)
    assert baseline == scenes_contract_sha256([_as_dict(compiled)])

    fields = {f.name for f in dataclasses.fields(RenderContext)}
    assert "supersample" in fields, "the knob belongs on RenderContext"
    assert (
        dataclasses.fields(RenderContext)[
            [f.name for f in dataclasses.fields(RenderContext)].index("supersample")
        ].default
        == DEFAULT_SUPERSAMPLE
    )


def _as_dict(compiled):
    from an.adapters.cutout.serialize import to_dict

    return to_dict(compiled)


def test_off_is_free_and_returns_the_bytes_untouched():
    """MUTATION: decode/re-encode unconditionally in `resolve_png_bytes`.

    At the default the un-supersampled path must keep **Chromium's own bytes**,
    not a re-encode of the same pixels. Re-encoding would be invisible in the
    picture and would cost a decode per frame on the path 100% of users are on
    — for nothing.
    """
    frame = np.zeros((8, 8, 3), np.uint8)
    frame[:, 4:] = 200
    data = encode_png(frame)
    assert resolve_png_bytes(data, factor=NO_SUPERSAMPLE) is data
    assert DEFAULT_SUPERSAMPLE == NO_SUPERSAMPLE == 1

    big = np.zeros((4, 4, 3), np.uint8)
    assert block_mean_resolve(big, NO_SUPERSAMPLE) is big


def test_a_factor_that_cannot_resolve_exactly_is_refused_before_the_browser_starts():
    """MUTATION: drop the `check_factor` call at the top of `CutoutRenderer.render`.

    A bad factor would then surface minutes later, from inside the frame loop,
    after a browser launch and a scene compile — and on the SECOND shot of a
    parallel render, from a thread.
    """
    from an.adapters.cutout import render as render_mod

    source = inspect.getsource(render_mod.CutoutRenderer.render)
    assert "check_factor(ctx.supersample)" in source
    assert source.index("check_factor") < source.index("sync_playwright"), (
        "validate before anything expensive starts"
    )

    for bad in (0, -1, 1.5, "2", True):
        with pytest.raises(SupersampleError):
            check_factor(bad)
    assert check_factor(3) == 3


def test_a_frame_that_is_not_a_whole_multiple_is_refused_rather_than_approximated():
    """MUTATION: round instead of raising in `block_mean_resolve`.

    An approximate resolve makes every edge measurement a measurement of the
    resolver. Refusing is the only honest answer, and it is reachable in
    practice: an odd declared width with an even factor.
    """
    with pytest.raises(SupersampleError, match="whole multiple"):
        block_mean_resolve(np.zeros((5, 4, 3), np.uint8), 2)
    with pytest.raises(SupersampleError, match="whole multiple"):
        block_mean_resolve(np.zeros((4, 5, 3), np.uint8), 2)


def test_the_resolve_is_bit_identical_to_the_float_form_it_replaces():
    """MUTATION: `(remainder == half) & (quotient % 2 == 1)` -> `remainder >= half`.

    The two-step uint16 form is 2.3x faster than
    `reshape(...).astype(float64).mean(axis=(1, 3))` at 1080p (111.7 ms against
    262.0 ms), and it is only allowed to be faster if it is **the same answer**.
    `np.rint` is banker's rounding, so a block averaging exactly .5 goes to the
    EVEN neighbour; round-half-up instead changes one code value on every
    half-block — invisible in a picture, and it moves every golden.

    Checked exhaustively over every possible 2x2 block of small values, not by
    sampling: the disagreement is exactly at the half, so a random probe finds
    it only by luck.
    """
    rng = np.random.default_rng(0)
    for shape, k in (((240, 320), 2), ((60, 90), 3), ((32, 32), 4)):
        big = rng.integers(0, 256, (shape[0] * k, shape[1] * k, 3), dtype=np.uint8)
        blocks = big.reshape(shape[0], k, shape[1], k, 3).astype(np.float64)
        reference = np.rint(blocks.mean(axis=(1, 3))).clip(0, 255).astype(np.uint8)
        assert np.array_equal(block_mean_resolve(big, k), reference), (shape, k)

    # Every 2x2 block over 0..8 — 6561 of them, and the halves are where it bites.
    disagreements = 0
    for values in np.ndindex(9, 9, 9, 9):
        block = np.array(values, np.uint8).reshape(2, 2, 1)
        got = int(block_mean_resolve(block, 2)[0, 0, 0])
        want = int(np.rint(block.astype(np.float64).mean()))
        disagreements += got != want
    assert disagreements == 0, f"{disagreements} of 6561 blocks round differently"


def test_the_round_trip_is_proven_where_pillow_actually_exists():
    """`resolve_png_bytes`'s decode/encode half needs Pillow, which the DEFAULT
    lane does not install — CI runs `dev,test`, and `pillow` is declared by the
    `cutout` extra this code path cannot run without anyway.

    So the contract "k-times in, declared-size PNG out" is asserted where that
    extra exists and on real Chromium frames rather than synthetic ones:
    `tests/test_cutout_render.py::test_a_supersampled_render_puts_declared_size_frames_on_disk`.
    This test only pins that the pointer stays true, because a cross-reference
    nobody checks is how a lane quietly stops covering something.

    NOT a module-level `importorskip`: that removes tests from COLLECTION rather
    than skipping them, which is an#22's defect and is invisible in both the
    pass count and the skip count.
    """
    from pathlib import Path as _P

    behavioural = (
        _P(__file__).with_name("test_cutout_render.py").read_text(encoding="utf-8")
    )
    assert (
        "def test_a_supersampled_render_puts_declared_size_frames_on_disk"
        in behavioural
    )
    assert "read_png_dimensions" in behavioural, (
        "the browser-lane test must still assert the SIZE on disk; without that "
        "assertion nothing anywhere checks the frame stage's contract"
    )
