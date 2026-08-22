"""Every mask the panel is computed over, and the recorded operator for each.

Three of them serve the encode-side metrics, plus the ring. ``EDGE_OPERATOR``
has a second spelling, ``RENDER_EDGE_OPERATOR``: the same operator applied on
the **render** side, where the luma comes from the source RGB rather than from
ffmpeg. It is a second string rather than a reuse because it measures a
different plane — which is also what keeps family A's mask off the encoder's
toolchain, on the branch where ffmpeg is absent.

Each mask is derived **only from the reference (pre-encode) frames**, never
from the decoded ones. That is not a stylistic choice: a mask derived from the
decoded stream moves with the mutation, which is the defect that made
``edge_ssim`` invisible on the AA arm (Δ = +0.0004, ~11x the machine band,
because the reference moved with the mutation).

Every operator here is exported as a **string** as well as a function, because
the ledger has to record what it measured — a threshold nobody can read back is
a threshold that quietly changes.
"""

from __future__ import annotations

from typing import Any

#: Two-pixel-apart luma gradient above this counts as an edge. Recorded in the
#: ledger with the operator string, because the research is explicit that the
#: prototype's absolute numbers are ordinal evidence only and no threshold may
#: be written from them.
EDGE_MASK_THRESHOLD: int = 40

#: Structuring element for the flat-field erosion.
FLAT_DILATE_K: int = 3

EDGE_OPERATOR: str = (
    "max(|Y[:,2:]-Y[:,:-2]|, |Y[2:,:]-Y[:-2,:]|) > %d, on the REFERENCE luma"
    % EDGE_MASK_THRESHOLD
)
FLAT_OPERATOR: str = (
    "~dilate%d(any 4-neighbour RGB change in the SOURCE)" % FLAT_DILATE_K
)
HELD_OPERATOR: str = "|src[i+1]-src[i]|.max(-1) == 0, on the SOURCE"
RING_OPERATOR: str = "dilate2(edge) & ~edge"

#: The SAME operator on the render side, and deliberately a second string
#: rather than a reuse of `EDGE_OPERATOR`. `an.bench.imageio`'s
#: `SOURCE_SCALE_FILTER` pins `out_range=tv`, so ffmpeg's Y lives in [16,235]
#: and its two-apart gradients are 219/255 of the full-range ones this operator
#: sees. At one threshold that makes this the wider mask ON A HARD SYNTHETIC
#: STEP — measured, a 45-code-value step selects 4 pixels here and 0 on the
#: limited-range plane — but NOT uniformly on real frames, where the two run
#: within a percent either way and the render-side one is the narrower on
#: `graded_field` and `single_character`. Which is the point: the two are
#: different measurements, and a reader can only know which one a row carries
#: if the row says which plane it measured.
RENDER_EDGE_OPERATOR: str = (
    "max(|Y[:,2:]-Y[:,:-2]|, |Y[2:,:]-Y[:-2,:]|) > %d, on FULL-RANGE BT.709 "
    "luma from the SOURCE RGB (an.bench.metrics.luma_u8)" % EDGE_MASK_THRESHOLD
)


def dilate(mask: Any, k: int = 3) -> Any:
    """Binary dilation by a ``k x k`` square, numpy-only.

    Implemented as shifted ORs rather than a convolution: ``k`` is 2 or 3 here,
    so nine shifts beat pulling in scipy, and the package's dependency
    perimeter is four names wide on purpose.

    >>> import numpy as np
    >>> m = np.zeros((1, 5, 5), bool); m[0, 2, 2] = True
    >>> int(dilate(m, 3)[0].sum())
    9
    """
    import numpy as np

    r = k // 2
    out = np.zeros_like(mask)
    n, h, w = mask.shape
    for dy in range(-r, r + 1):
        for dx in range(-r, r + 1):
            ys = slice(max(0, dy), h + min(0, dy))
            yd = slice(max(0, -dy), h + min(0, -dy))
            xs = slice(max(0, dx), w + min(0, dx))
            xd = slice(max(0, -dx), w + min(0, -dx))
            out[:, yd, xd] |= mask[:, ys, xs]
    return out


def edge_mask(luma: Any, *, threshold: int = EDGE_MASK_THRESHOLD) -> Any:
    """Pixels straddling a hard luma step, from the reference plane only.

    ``luma`` is ``(N, H, W)`` uint8. The border ring is excluded because a
    two-apart difference is undefined there; excluding it is what keeps the
    operator string honest.

    >>> import numpy as np
    >>> y = np.zeros((1, 5, 6), np.uint8); y[0, :, 3:] = 255
    >>> int(edge_mask(y).sum())
    6
    """
    import numpy as np

    y = luma.astype(np.int16)
    m = np.zeros(luma.shape, bool)
    gh = np.abs(y[:, :, 2:] - y[:, :, :-2])
    gv = np.abs(y[:, 2:, :] - y[:, :-2, :])
    m[:, 1:-1, 1:-1] = np.maximum(gh[:, 1:-1, :], gv[:, :, 1:-1]) > threshold
    return m


def flat_mask(rgb: Any, *, k: int = FLAT_DILATE_K) -> Any:
    """The interior of large flat colour fields, from the source frames.

    ~90% of a flat-cutout frame, and the part no edge metric touches. Banding
    and blocking live here, and without this mask they are invisible.

    >>> import numpy as np
    >>> a = np.zeros((1, 9, 9, 3), np.uint8); a[0, :, 5:] = 255
    >>> bool(flat_mask(a)[0, 0, 0]) and not bool(flat_mask(a)[0, 0, 5])
    True
    """
    import numpy as np

    d = rgb.astype(np.int16)
    changed = np.zeros(rgb.shape[:3], bool)
    dx = np.abs(d[:, :, 1:] - d[:, :, :-1]).max(-1) > 0
    dy = np.abs(d[:, 1:, :] - d[:, :-1, :]).max(-1) > 0
    changed[:, :, :-1] |= dx
    changed[:, :, 1:] |= dx
    changed[:, :-1, :] |= dy
    changed[:, 1:, :] |= dy
    return ~dilate(changed, k)


def held_mask(rgb: Any) -> Any:
    """``(N-1, H, W)``: pixels the animator held perfectly still between frames.

    >>> import numpy as np
    >>> a = np.zeros((2, 3, 3, 3), np.uint8); a[1, 0, 0] = 5
    >>> int(held_mask(a).sum())
    8
    """
    import numpy as np

    s = rgb.astype(np.int16)
    return np.abs(s[1:] - s[:-1]).max(-1) == 0


def ring_mask(edge: Any) -> Any:
    """The band immediately *around* an edge, excluding the edge itself.

    Where ringing and mosquito noise land. Excluding the edge is what makes it
    a different measurement from `coded_luma_edge_error` rather than a second
    name for it.

    >>> import numpy as np
    >>> e = np.zeros((1, 5, 5), bool); e[0, 2, 2] = True
    >>> int(ring_mask(e).sum())
    8
    """
    return dilate(edge, 3) & ~edge
