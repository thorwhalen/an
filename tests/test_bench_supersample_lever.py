"""The supersample lever's own seams — offline, and fast on purpose.

Split from `test_bench_mutation.py`: that module renders the corpus once per
lever and takes minutes, and every test here is a registered mutant's
`caught_by` file, which `an bench-mutants` runs **in full, once per mutant**. A
guard nobody can afford to run is a guard nobody runs.

The exam itself — `test_a_deliberate_degradation_moves_three_causal_families`,
parametrized over `MUTATIONS` — stays there, because it genuinely needs the
renders (an#56).
"""

from __future__ import annotations

import pytest

from an.bench.mutations import LEVERS, MutationError


# ------------------------------------ an#56: the supersample lever's own seams


def test_the_supersample_lever_stages_a_runtime_that_renders_at_k(tmp_path):
    """MUTATION: `autoDensity: false` -> `autoDensity: true` in SUPERSAMPLE_OPTIONS.

    That mutation is the one this whole lever exists downstream of, and it is
    **silent**: the lever still applies, the runtime digest still moves, the
    PNGs still come out the declared size — because Chromium composites the
    k-times backbuffer down before the screenshot — and every other test in the
    suite stays green while the lever measures a blind browser downscale
    instead of a supersample. It is the option whose name most suggests it is
    the right one (research §1).

    Offline: no browser, no ffmpeg.
    """
    from an.adapters.cutout import render
    from an.adapters.cutout.runtime_files import runtime_dir
    from an.bench.mutations import AA_ON, APP_OPEN, SUPERSAMPLE_K

    shipped = runtime_dir()
    with LEVERS["supersample"].apply():
        staged = render.runtime_dir()
        assert staged != shipped, "the lever must not point at the shipped tree"
        source = (staged / "runtime.js").read_text(encoding="utf-8")
        assert source.count(f"resolution: {SUPERSAMPLE_K}") == 1
        assert source.count("autoDensity: false") == 1
        assert source.count("autoDensity: true") == 0, (
            "`autoDensity: true` reintroduces the blind Chromium downscale the "
            "whole plumbing finding is about"
        )
        assert source.count(AA_ON) == 1, (
            "the AA lever's pin must survive, so the two render levers compose"
        )
        inserted = source.index(APP_OPEN) + len(APP_OPEN)
        assert source[inserted : inserted + 1] == "\n"
        assert (
            source[inserted + 1 :].lstrip().startswith(f"resolution: {SUPERSAMPLE_K}")
        )

    assert render.runtime_dir() == shipped, "the rebinding must be undone"
    assert not staged.exists(), "the staged tree must be cleaned up"


def test_the_supersample_lever_rebinds_the_frame_stage_as_well_as_the_runtime():
    """MUTATION: `render._capture_frames = _capture_then_resolve` -> `= original`.

    **The second seam, and it is not optional.** Patching `runtime.js` alone
    leaves k-times PNGs on disk, and nothing downstream reads a resolution off
    the files — `capture.resolution` comes from the staged scene's `meta`. So
    ffmpeg would mux a 640x480 video against a 320x240 declaration and the
    golden gate would withhold family B's number, which is one of the three
    witnesses this lever's criterion cannot do without.

    Asserted here as well as in the browser exam because the exam takes minutes
    and this takes milliseconds: a guard nobody can afford to run is a guard
    nobody runs.
    """
    from an.adapters.cutout import render

    shipped_capture = render._capture_frames
    with LEVERS["supersample"].apply():
        assert render._capture_frames is not shipped_capture, (
            "the lever must wrap the frame stage; patching runtime.js alone "
            "leaves k-times PNGs that nothing downstream will notice"
        )
        assert render._capture_frames.__name__ == "_capture_then_resolve"
    assert render._capture_frames is shipped_capture, "the rebinding must be undone"


def test_the_supersample_fingerprint_refuses_the_aa_levers_runtime():
    """MUTATION: `if recorded != expected:` -> `if False:` in `_verify_supersample`.

    That reduces the fingerprint to `disabled_aa`'s inequality, which **any**
    render lever satisfies — both stage through one seam and both move
    `render_side.runtime_sha256`. A row rendered with `antialias: false` would
    then verify as a supersample row, the lever table would be written from the
    wrong lever's numbers, `mutation_may_not_have_applied` would stay empty, and
    nothing anywhere would go red. Same failure class `_verify_disabled_aa` was
    itself introduced to close, one level along.

    Offline: no render.
    """
    from an.bench.environment import runtime_sha256
    from an.bench.mutations import (
        SUPERSAMPLE_K,
        _disable_aa_patch,
        _expected_runtime_sha256,
        _supersample_patch,
        _verify_supersample,
    )

    shipped = runtime_sha256()
    aa = _expected_runtime_sha256(_disable_aa_patch)
    ss = _expected_runtime_sha256(lambda src: _supersample_patch(src, k=SUPERSAMPLE_K))
    assert len({shipped, aa, ss}) == 3, (
        "the three digests must be pairwise distinct, or this test asserts nothing"
    )

    def row(digest):
        return {
            "provenance": {"environment": {"render_side": {"runtime_sha256": digest}}}
        }

    with pytest.raises(MutationError, match="did not reach the render"):
        _verify_supersample(row(shipped))
    with pytest.raises(MutationError, match="Some OTHER render-side lever"):
        _verify_supersample(row(aa))
    with pytest.raises(MutationError, match="records no"):
        _verify_supersample({"provenance": {}})
    _verify_supersample(row(ss))  # must not raise


def test_the_resolve_is_the_exact_block_mean_and_not_a_decimation(tmp_path):
    """MUTATION: `blocks.mean(axis=(1, 3))` -> `frame[::k, ::k, :]`.

    Nearest-neighbour decimation produces a plausible picture AND a plausible
    `edge_transition_width` (measured 2.114 px against a block mean's 2.492 on
    `saturated_outline`), so nothing downstream flags it. Only a value
    assertion catches it — which is why the lever's resolve is pinned by value
    rather than by "the frames came out the right size".

    Second mutation: drop `.astype(np.float64)`, so the uint8 mean truncates and
    every resolved pixel is biased low.
    """
    import numpy as np

    from an.bench.mutations import SUPERSAMPLE_K, _resolve_frames_in_place
    from an.bench.png import encode_png, read_png

    frame = np.zeros((4, 4, 3), np.uint8)
    frame[0:2, 0:2] = [[[0, 0, 0], [0, 0, 0]], [[0, 0, 0], [4, 4, 4]]]
    frame[0:2, 2:4] = [[[0, 0, 0], [0, 0, 0]], [[1, 1, 1], [2, 2, 2]]]
    (tmp_path / "frame_000000.png").write_bytes(encode_png(frame))
    _resolve_frames_in_place(tmp_path, k=2)

    out = read_png(tmp_path / "frame_000000.png")
    assert out.shape == (2, 2, 3)
    assert out[0, 0].tolist() == [1, 1, 1], (
        "the mean of [0,0,0,4] is 1 — a decimation would give 0 and the maximum 4"
    )
    assert out[0, 1].tolist() == [1, 1, 1], "mean of [0,0,1,2] is 0.75, rint -> 1"

    odd = tmp_path / "odd"
    odd.mkdir()
    (odd / "frame_000000.png").write_bytes(encode_png(np.zeros((5, 4, 3), np.uint8)))
    with pytest.raises(MutationError, match="whole multiple"):
        _resolve_frames_in_place(odd, k=SUPERSAMPLE_K)
