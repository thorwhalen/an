"""The eight expression presets are pairwise distinguishable on the committed
goldens (an#98) — an offline test on DECODED pixels, not a judge.

`misc/bench/corpus/expressions/` holds eight 0.25 s shots of one silent
character, one preset each; `an bench --bless` wrote one golden per shot at
its mid-frame. This test reads those PNGs back and asserts every pair differs
by at least ``MIN_PAIRWISE_CHANGED_PX`` inside the face crop. The floor is
HALF of the first bless's minimum (106 px, thinking vs skeptical — the two
asymmetric presets, as the research predicted): a regression that collapses
two emotions onto one face fails here before anyone looks at a frame, and
the bench's `--bless` refusal of pixel-identical pairs is the weaker, free
half of the same guarantee.
"""

from __future__ import annotations

from itertools import combinations
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
GOLDEN_DIR = ROOT / "misc" / "bench" / "golden" / "expressions"
#: Half of the first bless's minimum pairwise distance (106 px). Re-bless with
#: a reason before lowering it.
MIN_PAIRWISE_CHANGED_PX: int = 53
#: The face crop: (row0, row1, col0, col1) at 320x240 with the corpus's
#: `set face y 45` — head and brows, nothing below the collar.
FACE_CROP: tuple[int, int, int, int] = (0, 130, 60, 260)
N_PRESETS: int = 8


def _goldens() -> dict[str, object]:
    from an.bench.png import read_png

    files = sorted(GOLDEN_DIR.glob("f*.png"))
    return {f.name.split("-", 1)[0]: read_png(f) for f in files}


def test_every_expression_golden_is_committed():
    assert len(_goldens()) == N_PRESETS, sorted(GOLDEN_DIR.glob("*"))


def test_every_pair_of_presets_is_distinguishable_in_the_face():
    goldens = _goldens()
    if len(goldens) < N_PRESETS:
        pytest.skip("expressions goldens not blessed in this checkout")
    r0, r1, c0, c1 = FACE_CROP
    too_close = []
    for a, b in combinations(sorted(goldens), 2):
        diff = (goldens[a].astype(int) != goldens[b].astype(int)).any(axis=-1)
        face = int(diff[r0:r1, c0:c1].sum())
        if face < MIN_PAIRWISE_CHANGED_PX:
            too_close.append((a, b, face))
    assert not too_close, too_close


def test_the_difference_is_in_the_face_not_elsewhere():
    """A silent character with nothing else animating: everything that moves
    between two presets is inside the face crop."""
    goldens = _goldens()
    if len(goldens) < N_PRESETS:
        pytest.skip("expressions goldens not blessed in this checkout")
    r0, r1, c0, c1 = FACE_CROP
    names = sorted(goldens)
    a, b = goldens[names[0]], goldens[names[-1]]
    diff = (a.astype(int) != b.astype(int)).any(axis=-1)
    assert int(diff.sum()) == int(diff[r0:r1, c0:c1].sum())
