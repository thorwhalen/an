"""Render bigger, then resolve back exactly — the supersample knob's two halves.

**`autoDensity: false` is the whole plumbing finding, and it is load-bearing.**
`resolution: k` alone reproduces the failure it exists to avoid: with
`autoDensity: true` PixiJS sets the canvas CSS size to the *logical* size, so
Chromium composites the k-times backbuffer down before the screenshot — a blind
browser downscale, no filter choice, no record that it happened. Measured on a
declared 320x240 scene: neither key -> 320x240 PNGs; `resolution: 2,
autoDensity: false` -> 640x480; `resolution: 2, autoDensity: true` -> 320x240.
It is the option whose name most suggests it is the right one.

**The resolve is an exact k x k block mean, and calling it a filter would be
wrong** — at an integer ratio it *is* the supersample resolve. Measured against
the alternatives on all six corpus scenes: PIL's `BOX` agrees with it to four
decimals, and lanczos triples the edge band on the most idiom-like scene
(+208.8% on `saturated_outline`), because its negative lobes ring on hard-edged
flat fills. An ffmpeg-side `-vf scale` is refused for a second, independent
reason: it would move `x264_argv`, refusing every encode-side metric, and retire
the cross-arch verdict's load-bearing "ffmpeg never touches a frame" clause.

**Why PIL here and not `an.bench.png`.** The bench's codec exists so a committed
golden is a function of the *pixel data alone* rather than of Chromium's libpng
settings — a goal about files that get committed and diffed, which render-path
frames are not. And it is the wrong tool for this job by an order of magnitude:
Chromium's screenshots are Paeth-filtered on ~87% of rows (measured: 209 of 240),
which takes its scalar unfilter path at **416 ns/px against PIL's 31 ns/px**.
Extrapolated to a 3840x2160 supersampled frame that is **3.46 s of decoding per
frame** versus 256 ms — more than the render itself costs. `pillow` is declared
by the `cutout` extra, which this module cannot run without anyway.
"""

from __future__ import annotations

import io
from typing import Any

from an.base import DEFAULT_SUPERSAMPLE

#: The factor at which every code path here is a no-op rather than merely cheap.
#: Aliased from :data:`an.base.DEFAULT_SUPERSAMPLE` rather than restated: the
#: default and the off-switch are the same fact, and two copies of a fact drift.
NO_SUPERSAMPLE: int = DEFAULT_SUPERSAMPLE


class SupersampleError(ValueError):
    """A supersample factor or frame that cannot be resolved exactly."""


def check_factor(factor: int) -> int:
    """Validate a supersample factor, or refuse with the reason.

    >>> check_factor(1), check_factor(2)
    (1, 2)
    >>> check_factor(0)
    Traceback (most recent call last):
      ...
    an.adapters.cutout.supersample.SupersampleError: supersample must be >= 1, got 0
    """
    if not isinstance(factor, int) or isinstance(factor, bool):
        raise SupersampleError(
            f"supersample must be an int, got {type(factor).__name__}. A "
            "fractional factor cannot be resolved by an exact block mean, and "
            "approximating it would make the render a measurement of the "
            "resampler rather than of the scene."
        )
    if factor < 1:
        raise SupersampleError(f"supersample must be >= 1, got {factor}")
    return factor


def block_mean_resolve(frame: Any, factor: int) -> Any:
    """``(H*k, W*k, C)`` uint8 -> ``(H, W, C)`` uint8, by an exact ``k x k`` mean.

    Two-step, summing rows and then columns in ``uint16``, rather than the
    obvious ``reshape(...).astype(float64).mean(axis=(1, 3))``. The two agree
    **bit for bit** — asserted exhaustively over every possible 2x2 block, and
    on real frames — and the two-step form is 2.3x faster at 1080p (111.7 ms
    against 262.0 ms), because the cost here is the strided reduce and the
    64-bit temporary, not the arithmetic.

    Rounding is spelled out rather than inherited: ``np.rint`` is banker's
    rounding, so a block averaging exactly ``.5`` goes to the EVEN neighbour.
    Getting that wrong changes one code value on every half-block, which is
    invisible in a picture and moves every golden.

    >>> import numpy as np
    >>> f = np.array([[0, 0, 1, 2], [0, 4, 1, 2]], np.uint8)[..., None].repeat(3, -1)
    >>> block_mean_resolve(f, 2)[0, :, 0].tolist()
    [1, 2]
    >>> block_mean_resolve(f, 1) is f
    True
    """
    import numpy as np

    if factor == NO_SUPERSAMPLE:
        return frame
    big_h, big_w, channels = frame.shape
    if big_h % factor or big_w % factor:
        raise SupersampleError(
            f"a {big_w}x{big_h} frame is not a whole multiple of k={factor}, so "
            "there is no exact block mean. Resolving it approximately would "
            "make every edge measurement a measurement of the resolver."
        )
    height, width = big_h // factor, big_w // factor
    # uint16 holds factor**2 * 255 without overflow up to factor == 16.
    rows = frame.reshape(height, factor, big_w, channels).sum(axis=1, dtype=np.uint16)
    total = rows.reshape(height, width, factor, channels).sum(axis=2, dtype=np.uint16)
    area = factor * factor
    quotient, remainder = np.divmod(total, area)
    # `2 * remainder` against `area`, NOT `remainder` against `area // 2`.
    # An ODD area has no exact half: at k=3 a remainder of 4 is a true mean of
    # q + 4/9, which is below .5 and must round DOWN — but `area // 2` is also
    # 4, so the tie-break would fire and round it up. Caught by
    # `tests/test_supersample.py` at k=3, which is the factor research §3a says
    # reaches the measured ceiling on every scene that has one.
    twice = 2 * remainder
    tie_to_even = (twice == area) & (quotient % 2 == 1)
    return (quotient + ((twice > area) | tie_to_even)).astype(np.uint8)


def resolve_png_bytes(data: bytes, *, factor: int) -> bytes:
    """Decode a screenshot, block-mean it down by ``factor``, re-encode.

    Returns ``data`` unchanged at ``factor == 1``, so the un-supersampled path
    keeps Chromium's own bytes and pays nothing at all — which is what makes
    this knob free when it is off.

    **The early return sits above the imports deliberately.** "Off is free"
    should mean free of the *dependency* too: Pillow is declared by the `cutout`
    extra, so the default path must not need it merely to decide it has nothing
    to do. Without this, importing it here would make the knob's own tests
    unrunnable in the default CI lane, which installs `dev,test` and not
    `cutout` — and CI is where that was found.
    """
    if factor == NO_SUPERSAMPLE:
        return data

    import numpy as np
    from PIL import Image

    with Image.open(io.BytesIO(data)) as image:
        frame = np.asarray(image.convert("RGB"))
    out = io.BytesIO()
    Image.fromarray(block_mean_resolve(frame, factor)).save(out, format="PNG")
    return out.getvalue()
