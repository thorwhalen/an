"""The metric panel: pure numpy, no I/O, no subprocess.

Every function here is ``f(arrays, mask) -> float``. Loading is
:mod:`an.bench.imageio`'s job. The split is what lets the whole panel run in
the default CI leg — numpy is a hard dependency, ffmpeg and Playwright are not
— which is the only part of this wave that main CI can ever see.

**The set is the research's corrected one, and it is not the epic's draft.**
All twelve originally-proposed metrics were refuted. The corrections that are
easiest to undo by accident, each guarded by a test:

- ``edge_transition_width``'s flatness tolerance must not be 0. With ``> 0``,
  ±3-LSB dither sends the metric to 255.0.
- ``off_palette_pixel_fraction`` must not use ``np.unique(..., axis=0)``:
  1.833 s/frame at 1080p against 0.019 s for the packed form, 94x, identical
  result.
- ``encode_flicker_on_held_pixels`` must **cast before subtracting**.
  ``np.abs(a - b)`` on uint8 is the identity on unsigned dtypes, so the literal
  proposed form measured the *sign* of the change. It stayed monotone, which is
  exactly why it would have shipped unnoticed. And it must report a **rate**,
  not a mean: the median held-pixel delta is 0 at every CRF.
- ``encode_ringing_excess`` is the difference of two overshoot means, so the
  source-hardness term cancels. Raw overshoot is a joint function of source
  hardness and encoder fidelity with one degree of freedom, which is why any
  move toward crisper outlines raised it under an unchanged encoder.
- ``edge_masked_distinct_colours`` must be handed a mask built from
  :func:`luma_u8`, never from :func:`luma709` directly. ``luma709`` returns
  **float in [0,1]** and :func:`an.bench.masks.edge_mask` thresholds at **40 on
  0-255**, so the float form makes every two-apart gradient <= 1.0 and the mask
  comes back **entirely empty** — measured: 0 selected pixels against 4 for the
  same hard step. The metric would then be ``nan`` on every scene, which reads
  as "the check could not run" rather than as a bug (an#55).

One metric the epic named is deliberately absent: ``mean_adjacent_frame_ssim``
moves the **wrong way** (0.958 at crf18 -> 0.977 at crf51, because a crushed
video is smoother). Its existing use as a frozen-render detector in
:mod:`an.verify.media_quality` is a different and legitimate job.
"""

from __future__ import annotations

from typing import Any

#: Two neighbours within this many code values count as "flat". NOT 0 — see
#: the module docstring.
EDGE_FLAT_TOL: int = 4

#: Trim fraction for `edge_transition_width`'s mean, so one pathological run
#: cannot carry the number.
EDGE_TRIM: float = 0.10

#: A flat-field pixel more than this far off is "deviated".
FLAT_DEV_TOL: int = 6

#: A held pixel that moved by at least this much "flickered".
FLICKER_DELTA_TOL: int = 2

#: `ssim_map` window radius; 7x7, matched to feature size rather than the
#: global-moment form.
SSIM_RADIUS: int = 3


def _trimmed_mean(values: Any, trim: float) -> float:
    import numpy as np

    v = np.sort(values)
    k = int(len(v) * trim)
    core = v[k : len(v) - k] if len(v) - 2 * k > 0 else v
    return float(core.mean())


# --------------------------------------------------------------- render-side


def edge_transition_width(
    rgb: Any, *, tol: int = EDGE_FLAT_TOL, trim: float = EDGE_TRIM
) -> tuple[float, float]:
    """Average thickness, in pixels, of the fuzzy band between two flat areas.

    Under 1 is a jagged staircase; ~1 is clean AA; 3+ means the picture has
    gone soft. **Two-sided**, which is the whole reason it replaced a palette
    cardinality count: cardinality reads AA-off as 4, AA-on as 45 and a 3x3
    blur as 416, so "the number went up" means "AA restored" and "the picture
    went soft" indiscriminately.

    Returns ``(trimmed_mean, median)``.

    >>> import numpy as np
    >>> a = np.zeros((1, 4, 8, 3), np.uint8); a[0, :, 4:] = 255
    >>> round(edge_transition_width(a)[0], 3)   # one hard step = a 2px band
    2.0
    """
    import numpy as np

    a = rgb.astype(np.int16)
    dl = np.abs(a[:, :, 1:-1] - a[:, :, :-2]).max(-1)
    dr = np.abs(a[:, :, 1:-1] - a[:, :, 2:]).max(-1)
    nonflat = ~((dl <= tol) & (dr <= tol))
    padded = np.pad(nonflat, ((0, 0), (0, 0), (1, 1)))
    d = np.diff(padded.astype(np.int8), axis=2)
    starts = np.argwhere(d == 1)[:, 2]
    ends = np.argwhere(d == -1)[:, 2]
    runs = ends - starts
    if runs.size == 0:
        return 0.0, 0.0
    return _trimmed_mean(runs, trim), float(np.median(runs))


def pack_rgb(rgb: Any) -> Any:
    """``(N,H,W,3)`` uint8 -> ``(N,H,W)`` uint32, one integer per colour.

    >>> import numpy as np
    >>> hex(int(pack_rgb(np.array([[[[0x12, 0x34, 0x56]]]], np.uint8))[0, 0, 0]))
    '0x123456'
    """
    import numpy as np

    a = rgb.astype(np.uint32)
    return (a[..., 0] << 16) | (a[..., 1] << 8) | a[..., 2]


def off_palette_pixel_fraction(packed: Any, palette: Any) -> float:
    """Fraction of the frame whose colour is not one the compiler declared.

    >>> import numpy as np
    >>> p = np.array([[[0x000000, 0xffffff, 0x808080]]], np.uint32)
    >>> round(off_palette_pixel_fraction(p, [0x000000, 0xffffff]), 4)
    0.3333
    """
    import numpy as np

    return 1.0 - float(np.isin(packed, np.asarray(list(palette), np.uint32)).mean())


def off_palette_top_colours(packed: Any, palette: Any, *, top: int = 10) -> list[dict]:
    """The most frequent off-palette colours, as ``[{"hex", "count"}]``.

    Not decoration: this metric's whole meaning is "not one of the colours the
    compiler declared", so a palette that under-collects turns it into a large
    plausible number with no error anywhere. If the top entries are exact hex
    literals from the staged art, the derivation missed them; if they are
    blends sitting between two palette entries, that is anti-aliasing and the
    number is right.

    >>> import numpy as np
    >>> p = np.array([[[1, 1, 2]]], np.uint32)
    >>> off_palette_top_colours(p, [2], top=1)
    [{'hex': '#000001', 'count': 2}]
    """
    import numpy as np

    off = packed[~np.isin(packed, np.asarray(list(palette), np.uint32))]
    if off.size == 0:
        return []
    values, counts = np.unique(off, return_counts=True)
    order = np.argsort(counts)[::-1][:top]
    return [{"hex": "#%06x" % int(values[i]), "count": int(counts[i])} for i in order]


#: How far off the line between two palette colours a pixel may sit and still
#: be called a blend of them. 8-bit channels, so a couple of code values covers
#: rounding in the compositor.
BLEND_TOLERANCE: int = 3


def classify_off_palette(entries: list[dict], palette: Any) -> list[dict]:
    """Say, for each off-palette colour, whether it is a blend of two declared ones.

    This is the difference between a diagnostic and a list of hex codes. The
    metric's whole meaning is "not one of the colours the compiler declared",
    so a palette that under-collects turns it into a large, plausible number
    with no error anywhere. Anti-aliasing legitimately produces colours *on the
    segment between* two declared colours; a missed literal does not.

    So each entry gains ``blend_of`` (the two palette colours it sits between,
    or ``None``). A row whose top off-palette colours are all blends is
    reporting anti-aliasing correctly. One that is not is a derivation bug, and
    now it says so in the ledger rather than waiting for someone to look.

    >>> classify_off_palette([{"hex": "#808080"}], [0x000000, 0xffffff])[0]["blend_of"]
    ['#000000', '#ffffff']
    >>> classify_off_palette([{"hex": "#ff00ff"}], [0x000000, 0xffffff])[0]["blend_of"]
    """
    import numpy as np

    pal = np.asarray(
        [[(c >> 16) & 255, (c >> 8) & 255, c & 255] for c in sorted(palette)],
        np.float64,
    )
    out: list[dict] = []
    for entry in entries:
        h = entry["hex"].lstrip("#")
        v = np.array([int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)], np.float64)
        found = None
        for i in range(len(pal)):
            for j in range(i + 1, len(pal)):
                a, b = pal[i], pal[j]
                ab = b - a
                denom = float(ab @ ab)
                if denom == 0:
                    continue
                t = float((v - a) @ ab) / denom
                if not 0.0 <= t <= 1.0:
                    continue
                if np.abs(a + t * ab - v).max() <= BLEND_TOLERANCE:
                    found = [
                        "#%02x%02x%02x" % tuple(int(x) for x in a),
                        "#%02x%02x%02x" % tuple(int(x) for x in b),
                    ]
                    break
            if found:
                break
        out.append({**entry, "blend_of": found})
    return out


def frame_distinct_colours(packed: Any) -> float:
    """Mean number of distinct colours per frame.

    A flatness / palette-discipline **guard**, with no predicted direction on
    an AA change. Do not count it alongside `off_palette_pixel_fraction`; they
    are the same family.

    >>> import numpy as np
    >>> frame_distinct_colours(np.array([[[1, 1, 2]], [[3, 4, 5]]], np.uint32))
    2.5
    """
    import numpy as np

    return float(np.mean([len(np.unique(f)) for f in packed]))


def edge_masked_distinct_colours(packed: Any, edge: Any) -> tuple[float, int]:
    """Mean distinct colours per frame, counted ONLY on the edge mask.

    The half of `frame_distinct_colours` that is about edges: an interior-only
    change — a gradient laid into a flat field, a soft shadow — moves the
    whole-frame count and cannot reach this one at all. The second doctest
    below is that property, and it is the whole of what the mask buys.

    **It does NOT make the number blind to a whole-frame blur, and an#55's
    premise that it would is refuted.** The mask is recomputed from the frame
    being measured, and a blur WIDENS the edge band, so the mask grows to admit
    the new gradation. Measured on the six committed goldens, 3x3 box blur,
    ratio against k=1:

    ==================  ===========  ===========
    scene               whole-frame  edge-masked
    ==================  ===========  ===========
    aa_probe                 10.25x        9.50x
    graded_field              2.04x        2.35x
    multi_shot                7.92x        4.63x
    promote_demo              1.25x        0.83x
    saturated_outline         1.49x        1.14x
    single_character          8.60x        5.70x
    ==================  ===========  ===========

    Damped on four of six, WORSE on `graded_field`, and nowhere near blind.
    (an#55 quotes "1.8x-9.3x" for the whole-frame column; the real range on
    these goldens is 1.25x-10.25x, wider at both ends.)

    **What separates a blur from a supersample is the WIDTH half of the pair,
    not this one.** The same blur puts `edge_transition_width` at 2.1x-3.4x
    (`aa_probe` 2.655 -> 5.594 px), while an exact k=2 resolve moves it +2.6%
    to +8.0% (research §4). So read this metric BESIDE
    `edge_transition_width`: colours up with width flat is gradation added;
    colours up with width doubled is a soft picture.

    **Evidence for a human reader, never a gate.** an#41's criterion counts
    metrics independently and cannot express a conjunction, so neither half of
    the pair may be declared as counting on the strength of the other.

    ``edge`` must come from :func:`an.bench.masks.edge_mask` applied to
    :func:`luma_u8` — **not** to :func:`luma709`, which is float in [0,1]
    against a threshold of 40 on 0-255 and yields an empty mask every time.

    Returns ``(mean, frames_measured)``. A frame whose mask is empty is
    **skipped, not averaged in as zero** — a zero would drag the mean down and
    read as exactly the regression this metric exists to notice. With no such
    frame the answer is ``nan``, which the caller records as `unavailable`;
    :func:`an.bench.ledger.measured` refuses it.

    >>> import numpy as np
    >>> from an.bench.masks import edge_mask
    >>> c = np.zeros((1, 4, 16, 3), np.uint8); c[0, :, 8:] = 255
    >>> edge_masked_distinct_colours(pack_rgb(c), edge_mask(luma_u8(c)))
    (2.0, 1)

    A change entirely inside a flat field moves the whole-frame count and not
    this one — this, and only this, is what the mask buys:

    >>> d = c.copy()
    >>> for x in range(2, 6): d[0, :, x] = 8 * (x - 1)
    ...
    >>> frame_distinct_colours(pack_rgb(d))
    6.0
    >>> edge_masked_distinct_colours(pack_rgb(d), edge_mask(luma_u8(d)))
    (2.0, 1)

    An empty mask is not a colour count of zero:

    >>> flat = np.full((1, 4, 8, 3), 128, np.uint8)
    >>> edge_masked_distinct_colours(pack_rgb(flat), edge_mask(luma_u8(flat)))
    (nan, 0)
    """
    import numpy as np

    per_frame = [len(np.unique(f[m])) for f, m in zip(packed, edge) if m.any()]
    if not per_frame:
        return float("nan"), 0
    return float(np.mean(per_frame)), len(per_frame)


# --------------------------------------------------------------- encode-side


def masked_mean_abs(a: Any, b: Any, mask: Any) -> float:
    """``|a - b|`` averaged over ``mask``, with the cast that makes it correct.

    >>> import numpy as np
    >>> x = np.array([[[10, 200]]], np.uint8); y = np.array([[[12, 190]]], np.uint8)
    >>> masked_mean_abs(x, y, np.ones((1, 1, 2), bool))
    6.0
    """
    import numpy as np

    if not mask.any():
        return float("nan")
    return float(np.abs(a.astype(np.int16) - b.astype(np.int16))[mask].mean())


def flat_field_deviation(
    src_rgb: Any, dec_rgb: Any, flat: Any, *, tol: int = FLAT_DEV_TOL
) -> tuple[float, float]:
    """Fraction of flat-field pixels the encoder moved by more than ``tol``.

    The strongest metric in the set and the only one measured genuinely
    orthogonal to the edge/AA axis: monotone over a 133x span on the CRF ladder
    (0.0003 -> 0.0399, crf18 -> crf51) and flat under every AA variant.

    Returns ``(fraction_over_tol, p99_of_the_deviation)``.

    >>> import numpy as np
    >>> s = np.zeros((1, 3, 3, 3), np.uint8)
    >>> d = s.copy(); d[0, 1, 1] = 20
    >>> flat_field_deviation(s, d, np.ones((1, 3, 3), bool))[0]
    0.1111111111111111
    """
    import numpy as np

    if not flat.any():
        return float("nan"), float("nan")
    dev = np.abs(dec_rgb.astype(np.int16) - src_rgb.astype(np.int16)).max(-1)[flat]
    return float((dev > tol).mean()), float(np.percentile(dev, 99))


def encode_flicker_on_held_pixels(
    src_rgb: Any, dec_rgb: Any, *, tol: int = FLICKER_DELTA_TOL
) -> float:
    """Fraction of perfectly-held pixels that moved in the delivered video.

    Held-pose "boiling" — the worst artefact for limited-motion animation.
    Pooled over every frame pair rather than averaged per pair, so a pair with
    three held pixels does not weigh as much as one with seventy thousand.

    >>> import numpy as np
    >>> s = np.zeros((2, 1, 4, 3), np.uint8)
    >>> d = s.copy(); d[1, 0, 0] = 5
    >>> encode_flicker_on_held_pixels(s, d)
    0.25
    """
    import numpy as np

    s = src_rgb.astype(np.int16)
    v = dec_rgb.astype(np.int16)
    held = np.abs(s[1:] - s[:-1]).max(-1) == 0
    if not held.any():
        return float("nan")
    moved = np.abs(v[1:] - v[:-1]).max(-1)
    return float((moved[held] >= tol).mean())


def overshoot_mean(dec_luma: Any, src_luma: Any, ring: Any) -> float:
    """Mean positive excursion above the source, over the ring band.

    >>> import numpy as np
    >>> s = np.zeros((1, 1, 2), np.uint8); d = np.array([[[5, 0]]], np.uint8)
    >>> overshoot_mean(d, s, np.ones((1, 1, 2), bool))
    2.5
    """
    import numpy as np

    if not ring.any():
        return float("nan")
    return float(
        np.maximum(dec_luma.astype(np.int16) - src_luma.astype(np.int16), 0)[
            ring
        ].mean()
    )


def encode_ringing_excess(
    dec_luma: Any, lossless_luma: Any, src_luma: Any, ring: Any
) -> float:
    """How much more the lossy encode overshoots than a lossless one does.

    Both legs share the source, so the source-spectrum term cancels: AA-off
    raises both together (correctly reporting "the encoder did not get worse"),
    a genuine crispness improvement also raises both (no false regression), and
    a CRF change raises only the lossy leg.

    **Provisional.** `edge_band_mae` is recorded beside it so research open
    question 4 — "does plain edge-band MAE beat this?" — is answered by the
    ledger rather than by nobody.

    >>> import numpy as np
    >>> s = np.zeros((1, 1, 2), np.uint8)
    >>> encode_ringing_excess(np.array([[[9, 0]]], np.uint8),
    ...                       np.array([[[3, 0]]], np.uint8), s, np.ones((1, 1, 2), bool))
    3.0
    """
    return overshoot_mean(dec_luma, src_luma, ring) - overshoot_mean(
        lossless_luma, src_luma, ring
    )


# ------------------------------------------- shared reductions (golden-side,
# and the render-side edge mask via `luma_u8`)


def _box_mean(a: Any, k: int) -> Any:
    """Uniform ``k x k`` mean at stride 1, via a summed-area table."""
    import numpy as np

    r = k // 2
    pad = np.pad(a, r, mode="edge")
    integral = np.cumsum(np.cumsum(pad, axis=0), axis=1)
    integral = np.pad(integral, ((1, 0), (1, 0)))
    h, w = a.shape
    total = (
        integral[k:, k:] - integral[:-k, k:] - integral[k:, :-k] + integral[:-k, :-k]
    )
    return total[:h, :w] / (k * k)


def ssim_map(a: Any, b: Any, *, r: int = SSIM_RADIUS) -> Any:
    """Windowed SSIM at stride 1, as a per-pixel map.

    Added **beside** :func:`an.verify.media.ssim`, never replacing it: that
    function's global-moment form is what
    :data:`an.verify.media_quality`'s frozen-render threshold was tuned
    against, and `MediaQualityVerifier` sits in the default orchestrate chain.

    Why it exists at all: the metrics survey concluded SSIM should be excluded
    because whole-frame SSIM scores a total eye-blink at 0.9989. That
    conclusion was **refuted** — only the *global-moment* reduction is blind.
    With the window matched to feature size, min-over-windows scores the same
    blink at 0.279 (1080p) and 0.063 (native).

    Do not cross-check against ffmpeg's ``ssim`` filter: it uses overlapped 8x8
    block sums at 4-pixel stride, disagrees by up to 0.0201, and the
    disagreement *grows* with degradation.

    >>> import numpy as np
    >>> x = np.linspace(0, 1, 64).reshape(8, 8)
    >>> round(float(ssim_map(x, x).min()), 6)
    1.0
    """
    import numpy as np

    c1, c2 = 0.01**2, 0.03**2
    k = 2 * r + 1
    a = np.asarray(a, np.float64)
    b = np.asarray(b, np.float64)
    ma, mb = _box_mean(a, k), _box_mean(b, k)
    saa = _box_mean(a * a, k) - ma * ma
    sbb = _box_mean(b * b, k) - mb * mb
    sab = _box_mean(a * b, k) - ma * mb
    return ((2 * ma * mb + c1) * (2 * sab + c2)) / (
        (ma * ma + mb * mb + c1) * (saa + sbb + c2)
    )


#: BT.709 luma coefficients, recorded in the ledger so a future change to the
#: reduction is visible rather than folded into the number.
LUMA_709: tuple[float, float, float] = (0.2126, 0.7152, 0.0722)


def luma709(rgb: Any) -> Any:
    """``(H,W,3)`` uint8 -> ``(H,W)`` float in [0,1]. PIL-free.

    >>> import numpy as np
    >>> round(float(luma709(np.full((1, 1, 3), 255, np.uint8))[0, 0]), 6)
    1.0
    """
    import numpy as np

    a = np.asarray(rgb, np.float64) / 255.0
    return a[..., 0] * LUMA_709[0] + a[..., 1] * LUMA_709[1] + a[..., 2] * LUMA_709[2]


def luma_u8(rgb: Any) -> Any:
    """``(...,3)`` uint8 -> ``(...)`` uint8 luma, on the 0-255 scale a plane uses.

    The one conversion between :func:`luma709`, which returns **float in
    [0,1]**, and :func:`an.bench.masks.edge_mask`, whose threshold is **40 on
    0-255**. Handing the float straight to the mask is not a wrong number, it is
    an **empty mask**: every two-apart gradient is <= 1.0, so nothing is ever an
    edge and the metric downstream reads `unavailable` on every scene. Written
    once, named and tested here rather than open-coded at each call site,
    because it cost a debugging round the first time (an#55).

    **This is FULL-RANGE luma and the encode-side plane is not.**
    :data:`an.bench.imageio.SOURCE_SCALE_FILTER` pins ``out_range=tv``, so
    ffmpeg's Y sits in [16,235] and its gradients are 219/255 of these. At one
    threshold that makes the render-side mask the **wider** of the two —
    measured: a 45-code-value step selects 4 pixels here and 0 there — which is
    why the row records it under its own operator string rather than reusing
    :data:`an.bench.masks.EDGE_OPERATOR`.

    >>> import numpy as np
    >>> a = np.zeros((1, 1, 2, 3), np.uint8); a[0, 0, 1] = 255
    >>> luma_u8(a).tolist()
    [[[0, 255]]]
    """
    import numpy as np

    return np.rint(luma709(rgb) * 255.0).clip(0, 255).astype(np.uint8)


def golden_comparison(today_rgb: Any, golden_rgb: Any) -> dict:
    """The full-frame identity gate plus its diagnostics.

    Full-frame, not edge-masked: that is what makes the one sentence true, and
    what catches the flat-interior regressions an edge mask is blind to — and
    for this look the flat fields are most of the picture.

    ``changed_px`` and ``max_delta`` belong in a failure message, not in the
    metrics block; they are returned here so the caller can put them there.

    >>> import numpy as np
    >>> a = np.zeros((4, 4, 3), np.uint8)
    >>> golden_comparison(a, a)["identical"]
    True
    """
    import numpy as np

    t = today_rgb.astype(np.int16)
    g = golden_rgb.astype(np.int16)
    if t.shape != g.shape:
        return {
            "identical": False,
            "changed_px": None,
            "max_delta": None,
            "shape_mismatch": [list(t.shape), list(g.shape)],
            "min_ssim_win8": None,
        }
    diff = np.abs(t - g).max(-1)
    return {
        "identical": bool((diff > 0).sum() == 0),
        "changed_px": int((diff > 0).sum()),
        "max_delta": int(diff.max()),
        "shape_mismatch": None,
        "min_ssim_win8": float(ssim_map(luma709(today_rgb), luma709(golden_rgb)).min()),
    }
