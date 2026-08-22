"""The metric panel, against arrays whose answers are arithmetic (an#36).

These run in the **default lane** — no marker, no browser, no ffmpeg — because
numpy is a hard dependency and this is the only part of Wave 2's instrument
that main CI can ever see.

Each test named `_the_corrected_form_` guards a specific refutation. All twelve
originally-proposed metrics were refuted; the corrections are what is here, and
several of them are one careless edit from being undone in a way that still
produces plausible, monotone numbers.
"""

from __future__ import annotations

import numpy as np
import pytest

from an.bench import metrics as M
from an.bench import masks


def _step(width: int = 8, split: int = 4, frames: int = 1) -> np.ndarray:
    """A hard black/white vertical step — one edge, no anti-aliasing."""
    a = np.zeros((frames, 4, width, 3), np.uint8)
    a[:, :, split:] = 255
    return a


# ------------------------------------------------------- edge_transition_width


def test_a_hard_step_is_a_two_pixel_band():
    """The two pixels straddling the step are each unlike one neighbour."""
    mean, median = M.edge_transition_width(_step())
    assert mean == pytest.approx(2.0)
    assert median == pytest.approx(2.0)


def test_a_softened_step_reads_wider_than_a_hard_one():
    """The metric's whole point: it is two-sided, and soft is a failure too."""
    hard = _step(width=16, split=8)
    soft = hard.astype(np.float64).copy()
    for i in range(1, 4):
        soft[:, :, 8 - i] = 255 * (0.5 - i * 0.12)
        soft[:, :, 7 + i] = 255 * (0.5 + i * 0.12)
    assert (
        M.edge_transition_width(soft.astype(np.uint8))[0]
        > M.edge_transition_width(hard)[0]
    )


def _dithered_flat(amplitude: int, *, width: int = 64) -> np.ndarray:
    """A flat grey field with +/-`amplitude` per-pixel noise on every channel."""
    rng = np.random.default_rng(0)
    flat = np.full((1, 8, width, 3), 128, np.int16)
    noise = rng.integers(-amplitude, amplitude + 1, flat.shape[:3])[..., None]
    return np.clip(flat + noise, 0, 255).astype(np.uint8)


#: What a genuine hard step reads, and therefore the scale the numbers below
#: have to be read against.
HARD_EDGE_BAND_PX: float = 2.0


@pytest.mark.parametrize("amplitude", [1, 2, 3])
def test_a_zero_flatness_tolerance_reads_a_dithered_flat_field_as_all_edge(amplitude):
    """The refutation: a flat field is never bit-flat, so `> 0` measures noise.

    Measured here at 64px wide: tol=0 reads 10.1 / 20.5 / 32.4 for +/-1 / 2 / 3
    dither, against 2.0 for a genuine hard step. It scales with the frame — the
    research measured 255.0 at 1080p — which is the point: the number stops
    being about the picture and becomes about the frame width.
    """
    value = M.edge_transition_width(_dithered_flat(amplitude), tol=0)[0]
    assert value > 4 * HARD_EDGE_BAND_PX, (
        f"tol=0 read {value:.2f} on +/-{amplitude} dither, against "
        f"{HARD_EDGE_BAND_PX} for a real hard edge; it must be swamped by noise, "
        "which is why the shipped tolerance is not zero"
    )


def test_the_shipped_tolerance_absorbs_dither_up_to_half_its_value():
    """The exact envelope, recorded rather than assumed.

    `edge_transition_width` takes the max over channels, so two neighbours
    dithered by +a and -a differ by 2a. The shipped tolerance therefore absorbs
    amplitudes up to `EDGE_FLAT_TOL / 2` and no further — which is a fact about
    the constant, not a lucky choice, and is why this asserts both sides.
    """
    assert M.EDGE_FLAT_TOL > 0, "the shipped tolerance must not be zero"
    inside = M.EDGE_FLAT_TOL // 2
    assert M.edge_transition_width(_dithered_flat(inside))[0] == 0.0, (
        f"+/-{inside} dither is inside the tolerance and must read as flat"
    )
    assert M.edge_transition_width(_dithered_flat(inside + 1))[0] > 0.0, (
        f"+/-{inside + 1} dither exceeds it and must not be silently absorbed — "
        "a tolerance that swallowed everything would be as blind as tol=0 is loud"
    )


# ------------------------------------------------- off_palette_pixel_fraction


def test_an_exactly_declared_frame_is_entirely_on_palette():
    packed = M.pack_rgb(_step())
    assert M.off_palette_pixel_fraction(packed, [0x000000, 0xFFFFFF]) == 0.0


def test_one_blended_pixel_moves_it_off_zero():
    a = _step()
    a[0, 0, 3] = (128, 128, 128)
    packed = M.pack_rgb(a)
    assert M.off_palette_pixel_fraction(packed, [0x000000, 0xFFFFFF]) > 0.0


def test_the_packed_form_agrees_with_the_slow_one_it_replaced():
    """94x faster and identical. The slow form is `np.unique(..., axis=0)`."""
    rng = np.random.default_rng(1)
    a = rng.integers(0, 8, (2, 6, 6, 3), dtype=np.uint8)
    palette = [0x000000, 0x010203, 0x070707]
    packed = M.pack_rgb(a)
    slow = 0
    for frame in a.reshape(-1, 3):
        if (
            (int(frame[0]) << 16) | (int(frame[1]) << 8) | int(frame[2])
        ) not in palette:
            slow += 1
    assert M.off_palette_pixel_fraction(packed, palette) == pytest.approx(
        slow / a[..., 0].size
    )


def test_a_blend_of_two_declared_colours_is_classified_as_one():
    """Risk 2's permanent guard: anti-aliasing vs a missed literal.

    Without this the metric is a large plausible number whenever the palette
    derivation under-collects, with no error anywhere.
    """
    entries = [{"hex": "#808080", "count": 5}, {"hex": "#ff00ff", "count": 5}]
    out = M.classify_off_palette(entries, [0x000000, 0xFFFFFF])
    assert out[0]["blend_of"] == ["#000000", "#ffffff"]
    assert out[1]["blend_of"] is None, "a colour off every segment is not a blend"


# -------------------------------------------------------- flat_field_deviation


def test_an_undamaged_flat_field_reads_zero():
    src = np.zeros((1, 4, 4, 3), np.uint8)
    assert M.flat_field_deviation(src, src.copy(), np.ones((1, 4, 4), bool))[0] == 0.0


def test_the_flat_mask_excludes_the_pixels_around_an_edge():
    """The metric covers the ~90% of the frame no edge metric touches."""
    a = _step(width=9, split=5)
    flat = masks.flat_mask(a)
    assert flat[0, 0, 0], "far from the edge is flat"
    assert not flat[0, 0, 5], "on the edge is not"
    assert not flat[0, 0, 4], "and neither is its dilated neighbour"


# ---------------------------------------------- encode_flicker_on_held_pixels


def test_the_corrected_form_casts_before_subtracting():
    """`np.abs(a - b)` on uint8 is the identity on unsigned dtypes.

    The refutation is subtler than "it gives a big number": on the RATE form a
    decrease of 10 wraps to 246 and is counted either way, so a careless test
    passes. The cases that separate the two forms are decreases *below* the
    threshold — a held pixel that came back one code value darker is not
    flicker, and the uncast form wraps it to 255 and counts it.

    So this feeds exactly that: a decrease of 1 on one pixel, an increase of 5
    on another. The correct answer is 0.25; the uncast form says 0.5.
    """
    src = np.zeros((2, 1, 4, 3), np.uint8)
    src[:] = 5  # every pixel held, and bright enough to be decremented
    dec = src.copy()
    dec[1, 0, 0] = 4  # a 1-value DECREASE — below the threshold, not flicker
    dec[1, 0, 1] = 10  # a 5-value increase — genuine flicker

    assert M.encode_flicker_on_held_pixels(src, dec) == pytest.approx(0.25), (
        "only the 5-value move is flicker"
    )

    uncast = np.abs(dec[1:] - dec[:-1]).max(-1)  # the literal proposed expression
    counted_by_uncast = float((uncast[np.ones_like(uncast, bool)] >= 2).mean())
    assert counted_by_uncast == pytest.approx(0.5), (
        f"the uncast form counts {counted_by_uncast:.2f}; if this ever equals "
        "the cast form's answer, this test no longer demonstrates the bug"
    )


def test_it_reports_a_rate_and_not_a_mean():
    """The median held-pixel delta is 0 at every CRF, so a mean is dilution."""
    src = np.zeros((2, 1, 100, 3), np.uint8)
    dec = src.copy()
    dec[1, 0, :3] = 200  # three loud pixels among a hundred quiet ones
    assert M.encode_flicker_on_held_pixels(src, dec) == pytest.approx(0.03)


def test_pooling_is_over_all_pairs_not_a_mean_of_per_pair_means():
    """A pair with three held pixels must not weigh as much as one with many."""
    src = np.zeros((3, 1, 4, 3), np.uint8)
    src[2, 0, 1:] = 90  # only one pixel is held between frames 1 and 2
    dec = src.copy()
    dec[1, 0, 0] = 9
    pooled = M.encode_flicker_on_held_pixels(src, dec)
    # pair0: 4 held, 1 moved. pair1: 1 held, 1 moved (it carries dec's change).
    assert pooled == pytest.approx(2 / 5), (
        "pooled over 5 held observations, not the mean of 0.25 and 1.0"
    )


# -------------------------------------------------------- encode_ringing_excess


def test_the_excess_cancels_the_source_hardness_term():
    """Both legs share the source, so only the encoder's part survives."""
    src = np.zeros((1, 1, 2), np.uint8)
    ring = np.ones((1, 1, 2), bool)
    lossy = np.array([[[9, 0]]], np.uint8)
    lossless = np.array([[[3, 0]]], np.uint8)
    assert M.encode_ringing_excess(lossy, lossless, src, ring) == pytest.approx(3.0)


def test_a_harder_source_raises_both_legs_and_moves_the_excess_less():
    """The refutation that killed raw overshoot: it has one degree of freedom."""
    src = np.zeros((1, 1, 4), np.uint8)
    ring = np.ones((1, 1, 4), bool)
    soft = (np.array([[[4, 0, 4, 0]]], np.uint8), np.array([[[2, 0, 2, 0]]], np.uint8))
    hard = (
        np.array([[[40, 0, 40, 0]]], np.uint8),
        np.array([[[38, 0, 38, 0]]], np.uint8),
    )
    soft_raw = M.overshoot_mean(soft[0], src, ring)
    hard_raw = M.overshoot_mean(hard[0], src, ring)
    assert hard_raw > 5 * soft_raw, "raw overshoot tracks source hardness"
    assert M.encode_ringing_excess(*soft, src, ring) == pytest.approx(
        M.encode_ringing_excess(*hard, src, ring)
    ), "the excess does not"


# ------------------------------------------------------------------- ssim_map


def test_ssim_map_of_an_image_with_itself_is_one_everywhere():
    x = np.linspace(0, 1, 64).reshape(8, 8)
    assert float(M.ssim_map(x, x).min()) == pytest.approx(1.0)


def test_the_windowed_form_sees_a_local_change_the_global_one_is_blind_to():
    """The refutation that saved SSIM from being dropped entirely.

    The metrics survey concluded SSIM should be excluded because whole-frame
    SSIM scores a total eye-blink at 0.9989. Only the *global-moment* reduction
    is blind — with the window matched to feature size, the same change scores
    an order of magnitude lower.
    """
    from an.verify.media import ssim as global_ssim

    # Shaped like a real cutout frame: a large flat fill, a few strong dark
    # features, and one small feature that vanishes. The proportions matter —
    # the global form is blind because the flat fill dominates its moments, so
    # a synthetic with near-zero global variance would not reproduce it.
    base = np.ones((256, 256))
    base[40:200, 40:60] = 0.1  # a torso-sized dark block
    base[40:200, 190:210] = 0.1  # and another
    base[100:120, 100:160] = 0.3  # a mid-tone feature
    blinked = base.copy()
    blinked[60:66, 120:132] = 0.05  # one eye-sized feature appears

    assert global_ssim(base, blinked) > 0.99, (
        f"the global form read {global_ssim(base, blinked):.5f}; it is supposed "
        "to be blind here, and if it is not, this test no longer demonstrates "
        "the refutation"
    )
    assert float(M.ssim_map(base, blinked).min()) < 0.5, (
        "the windowed form must see it; if this ever fails, the metric has "
        "stopped being the local detector it was kept for"
    )


def test_luma_is_computed_without_pillow():
    """The bench path is numpy + ffmpeg only; Pillow's licence is an open question."""
    white = np.full((2, 2, 3), 255, np.uint8)
    assert float(M.luma709(white).min()) == pytest.approx(1.0)
    assert sum(M.LUMA_709) == pytest.approx(1.0)


def test_the_golden_comparison_is_full_frame_and_not_edge_masked():
    """An edge mask returns 1.00000 on a visibly wrong flat interior."""
    a = np.zeros((8, 8, 3), np.uint8)
    b = a.copy()
    b[2:5, 2:5] = 40  # entirely inside a flat field, no edge involved
    out = M.golden_comparison(a, b)
    assert out["identical"] is False
    assert out["changed_px"] == 9
    assert out["max_delta"] == 40


# ---------------------------------------- edge_masked_distinct_colours (an#55)


def _box3(a):
    """3x3 box blur, edge-replicated, per channel. The canonical soft-picture."""
    p = np.pad(a.astype(np.float64), ((0, 0), (1, 1), (1, 1), (0, 0)), mode="edge")
    out = np.zeros_like(p[:, 1:-1, 1:-1])
    for dy in range(3):
        for dx in range(3):
            out += p[:, dy : dy + p.shape[1] - 2, dx : dx + p.shape[2] - 2]
    return np.rint(out / 9).clip(0, 255).astype(np.uint8)


def test_an_interior_only_change_moves_the_whole_frame_count_and_not_the_masked_one():
    """MUTATION: `f[m]` -> `f` in `edge_masked_distinct_colours`.

    This is the ONE property the mask buys, so it is the one the test pins. A
    gradient laid entirely inside a flat field is invisible to the masked count
    and plainly visible to the whole-frame one.
    """
    a = np.zeros((1, 8, 24, 3), np.uint8)
    a[0, :, 12:] = 255
    b = a.copy()
    for x in range(2, 8):
        b[0, :, x] = 8 * (x - 1)  # a ramp, well away from the step at x=12

    edge_a = masks.edge_mask(M.luma_u8(a))
    edge_b = masks.edge_mask(M.luma_u8(b))
    assert M.frame_distinct_colours(M.pack_rgb(b)) > M.frame_distinct_colours(
        M.pack_rgb(a)
    )
    assert (
        M.edge_masked_distinct_colours(M.pack_rgb(b), edge_b)[0]
        == (M.edge_masked_distinct_colours(M.pack_rgb(a), edge_a)[0])
    )


def test_the_masked_count_is_NOT_blind_to_a_blur_and_the_docstring_says_so():
    """an#55's premise, refuted by measurement and pinned so it stays refuted.

    The issue argued that restricting the count to the edge mask makes it blind
    to "the frame got blurrier". It does not: the mask is recomputed from the
    frame being measured, and a blur WIDENS the edge band, so the mask grows to
    admit the new gradation. This test exists so nobody re-derives the claim
    from the metric's name.

    MUTATION: none needed — it fails the moment someone writes a blur-immunity
    assertion in its place.
    """
    a = np.zeros((1, 16, 32, 3), np.uint8)
    a[0, :, 16:] = 255
    b = _box3(a)

    masked_before = M.edge_masked_distinct_colours(
        M.pack_rgb(a), masks.edge_mask(M.luma_u8(a))
    )[0]
    masked_after = M.edge_masked_distinct_colours(
        M.pack_rgb(b), masks.edge_mask(M.luma_u8(b))
    )[0]
    assert masked_after > masked_before, (
        "if this ever becomes False the refutation has been undone by accident "
        "and the metric's docstring, which states it as measured, is now wrong"
    )
    # What DOES separate the two cases is the width half of the pair.
    assert M.edge_transition_width(b)[0] > 1.5 * M.edge_transition_width(a)[0]


def test_an_empty_edge_mask_is_unavailable_and_never_a_colour_count_of_zero():
    """MUTATION: `return float("nan"), 0` -> `return 0.0, 0`.

    A substituted zero is the largest possible DOWNWARD move in the one metric
    that exists to notice a downward move, on the one kind of scene (no hard
    edge anywhere) where the number means nothing at all.
    """
    flat = np.full((3, 8, 8, 3), 128, np.uint8)
    value, measured_frames = M.edge_masked_distinct_colours(
        M.pack_rgb(flat), masks.edge_mask(M.luma_u8(flat))
    )
    assert value != value, "an absent measurement must be NaN, not 0.0"
    assert measured_frames == 0

    from an.bench.ledger import LedgerSchemaError, measured

    with pytest.raises(LedgerSchemaError):
        measured(value)


def test_a_frame_with_no_edges_is_skipped_rather_than_averaged_in_as_zero():
    """One blank frame in a sequence must not drag the mean toward zero."""
    a = np.zeros((2, 8, 24, 3), np.uint8)
    a[0, :, 12:] = 255  # frame 0 has a step; frame 1 is uniform black
    value, measured_frames = M.edge_masked_distinct_colours(
        M.pack_rgb(a), masks.edge_mask(M.luma_u8(a))
    )
    assert measured_frames == 1
    assert value == 2.0


def test_the_float_luma_trap_produces_an_empty_mask_rather_than_a_wrong_number():
    """MUTATION: `luma_u8(rgb)` -> `luma709(rgb)` at the mask's call site.

    `luma709` is float in [0,1] and `edge_mask` thresholds at 40 on 0-255, so
    every two-apart gradient is <= 1.0 and NOTHING is ever an edge. The metric
    then reads `unavailable` on every scene — which looks like "the check could
    not run", not like a bug.
    """
    a = np.zeros((1, 8, 24, 3), np.uint8)
    a[0, :, 12:] = 255
    assert masks.edge_mask(M.luma_u8(a)).sum() > 0
    assert masks.edge_mask(M.luma709(a).astype(np.uint8)).sum() == 0
