"""Co-articulation for a swap mouth: the passes between a provider's raw viseme
track and the compiler's channel emission (an#97, epic #9 Wave 6).

A lip-sync provider hands the compiler a list of ``(time, code)`` cues — one
mouth shape per phoneme or per character, at whatever density it produced.
Shown as-is, that track *flickers*: consonant clusters swap the mouth faster
than a frame or two can show, tongue-only shapes swap the lips for one frame, and
every shape lands exactly on its sound instead of a beat ahead of it. These
passes turn the raw track into what an animator would key, in this order:

1. **Symbolic** — :func:`merge_duplicates` drops a cue whose shape is already
   showing; :func:`suppress_weak` drops a low-dominance cue that would show for
   less than one frame (JALI, Edwards et al. 2016 §4.2: "Tongue-only visemes
   (l n t d g k N) have no influence on the lips" — for a swap mouth, "take the
   neighbour's shape" is "do not swap").
2. **Anticipation** — :func:`lead` moves every cue earlier by a fixed lead
   (JALI: "speech onset begins 120 ms before the apex"; the animator's "two
   frames ahead"; Rhubarb's own ``maxExtensionDuration`` of 60 ms), clamped
   at 0.
3. **Decay** — :func:`decay` gives a shape its time to close: a rest cue that
   arrives sooner than ``decay_s`` after the shape before it is pushed out to
   ``decay_s`` (JALI: "another 120 ms to decay to zero"), never past the next
   speaking cue.
4. **Minimum hold** — :func:`condense`, LAST. It **holds and votes**; it never
   drops a cue unvoted (a carried loser can lose its next window too and never
   show — outvoted twice, not skipped). Windows of at least ``min_hold_s`` open at each cue that clears the
   previous window; within a window the shape with the largest
   ``span × dominance`` (Rhubarb's "select shape with highest total duration
   within the candidate range", weighted by Cohen–Massaro's per-segment
   dominance) shows, **placed at the window start**. The old compiler loop
   ``continue``d past every cue inside the window, so a consonant cluster
   collapsed to whichever shape arrived first — the defect epic #9 names.

Order matters: (1) before (4) so a one-frame /t/ never wins a window; (2) and
(3) before (4) so the hold is measured on shifted times. Every pass is a pure
function over ``list[Cue]``, runs in the **compiler** (never in the audio
pipeline, so a knob change is a recompile and never a paid re-alignment), and
none of these constants may ever enter a cache key.

Dominance: the ORDER of :data:`DOMINANCE` is sourced — A (bilabial closure;
JALI rule 1 "must close the lips") > F, G (lip-heavy) > E, D > C > X > B, H
(tongue) — and its values are art direction. A provider that knows the
character behind a cue may scale it through ``Cue.intensity`` (Rhubarb's ``B``
codes both "most consonants" and the vowel EE, so the letter alone cannot say).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterable, Sequence

#: Per-shape dominance for Rhubarb's letters. Order sourced, values ours.
DOMINANCE: dict[str, float] = {
    "A": 1.0,
    "F": 0.9,
    "G": 0.9,
    "E": 0.8,
    "D": 0.8,
    "C": 0.6,
    "X": 0.5,
    "B": 0.3,
    "H": 0.3,
}
#: A shape not in the table (another convention's code) is neither strong nor weak.
DEFAULT_DOMINANCE: float = 0.5
#: Below this dominance a cue is "weak" for :func:`suppress_weak`.
WEAK_BELOW: float = 0.5
#: Anticipation lead — two frames at 24 fps (art direction; JALI's 120 ms is the ceiling).
DEFAULT_LEAD_S: float = 2 / 24
#: Time a shape is given to close before rest (JALI's "120 ms to decay").
DEFAULT_DECAY_S: float = 0.12
#: The minimum hold, unchanged from the pre-an#97 compiler until measured.
DEFAULT_MIN_HOLD_S: float = 0.14


@dataclass(frozen=True, slots=True)
class Cue:
    """One mouth-shape cue: when it starts, which shape, how loudly it wants the lips."""

    time: float
    code: str
    intensity: float = 1.0

    @property
    def dominance(self) -> float:
        return DOMINANCE.get(self.code, DEFAULT_DOMINANCE) * self.intensity


def _cues(keys: Iterable) -> list[Cue]:
    """Accept ``Cue``s or ``(t, code[, intensity])`` tuples; sort by time; upper-case codes."""
    out = []
    for k in keys:
        if isinstance(k, Cue):
            out.append(Cue(float(k.time), str(k.code).upper(), float(k.intensity)))
        else:
            t, code = float(k[0]), str(k[1]).upper()
            out.append(Cue(t, code, float(k[2]) if len(k) > 2 else 1.0))
    return sorted(out, key=lambda c: c.time)


#: Window-edge tolerance: ``0.28 + 0.14`` is ``0.42000000000000004``, so a cue at
#: exactly three holds would otherwise fall *inside* the third window.
_EDGE_EPS: float = 1e-9


def _spans(cues: Sequence[Cue], *, end: float | None) -> list[float]:
    """How long each cue would show if nothing were held: until the next cue,
    then until ``end`` — or **forever** when ``end`` is unknown, so a last cue
    is never a zero-span nobody that loses every vote and vanishes (review of
    an#97: ``condense([(0, "X"), (0.1, "A")], min_hold_s=0.14)`` used to
    return just the rest)."""
    return [
        (cues[i + 1].time - c.time)
        if i + 1 < len(cues)
        else (max(0.0, end - c.time) if end is not None else math.inf)
        for i, c in enumerate(cues)
    ]


def merge_duplicates(keys: Iterable) -> list[Cue]:
    """Drop a cue whose shape is the one already showing.

    >>> [(c.time, c.code) for c in merge_duplicates([(0, "X"), (0.1, "B"), (0.2, "B"), (0.3, "C")])]
    [(0.0, 'X'), (0.1, 'B'), (0.3, 'C')]
    """
    out: list[Cue] = []
    for c in _cues(keys):
        if out and out[-1].code == c.code:
            continue
        out.append(c)
    return out


def suppress_weak(
    keys: Iterable, *, max_weak_s: float, end: float | None = None
) -> list[Cue]:
    """Drop a weak (low-dominance) cue that would show for less than ``max_weak_s``.

    >>> raw = [(0.0, "X"), (0.20, "D"), (0.40, "B"), (0.43, "D"), (0.80, "X")]
    >>> [(c.time, c.code) for c in suppress_weak(raw, max_weak_s=0.04)]
    [(0.0, 'X'), (0.2, 'D'), (0.8, 'X')]

    A weak cue that lasts is kept — this is not the minimum-hold pass:

    >>> [(c.time, c.code) for c in suppress_weak([(0.0, "X"), (0.2, "B"), (0.5, "D")], max_weak_s=0.04)]
    [(0.0, 'X'), (0.2, 'B'), (0.5, 'D')]
    """
    cues = _cues(keys)
    spans = _spans(cues, end=end)
    kept = [
        c
        for c, span in zip(cues, spans)
        if not (c.dominance < WEAK_BELOW and span < max_weak_s)
    ]
    return merge_duplicates(kept)


def lead(keys: Iterable, *, lead_s: float) -> list[Cue]:
    """Anticipation: every cue moves ``lead_s`` earlier, clamped at 0.

    Cues that collide at 0 collapse to the last one — the shape that is still
    the state once the collision is over. An opening burst shorter than the
    lead (``[(0, X), (0.02, A), (0.05, D), (0.07, X)]``) therefore never opens
    the mouth; the hold would have refused a 70 ms word anyway, and the old
    condenser did.

    >>> [(round(c.time, 3), c.code) for c in lead([(0.0, "X"), (0.05, "D"), (0.5, "B")], lead_s=0.08)]
    [(0.0, 'D'), (0.42, 'B')]
    """
    out: list[Cue] = []
    for c in _cues(keys):
        moved = Cue(max(0.0, c.time - lead_s), c.code, c.intensity)
        if out and out[-1].time == moved.time:
            out[-1] = moved
        else:
            out.append(moved)
    return merge_duplicates(out)


def decay(
    keys: Iterable, *, decay_s: float, rest: str = "X", end: float | None = None
) -> list[Cue]:
    """Give a shape ``decay_s`` to close: a rest arriving sooner than that after
    the shape before it is pushed out to ``decay_s``, never past the next cue
    and never past ``end`` (a rest pushed to ``end`` is where the line closes).

    >>> [(round(c.time, 3), c.code) for c in decay([(0.0, "X"), (0.2, "D"), (0.25, "X"), (0.6, "B")], decay_s=0.12)]
    [(0.0, 'X'), (0.2, 'D'), (0.32, 'X'), (0.6, 'B')]
    >>> [(round(c.time, 3), c.code) for c in decay([(0.0, "X"), (0.2, "D"), (0.25, "X"), (0.30, "B")], decay_s=0.12)]
    [(0.0, 'X'), (0.2, 'D'), (0.3, 'B')]
    """
    cues = _cues(keys)
    out: list[Cue] = []
    for i, c in enumerate(cues):
        if c.code == rest and out and out[-1].code != rest:
            earliest = out[-1].time + decay_s
            nxt = cues[i + 1].time if i + 1 < len(cues) else None
            if c.time < earliest:
                if nxt is not None and earliest >= nxt:
                    continue  # the next shape arrives first: no rest in between
                if end is not None:
                    earliest = min(earliest, end)
                c = Cue(earliest, c.code, c.intensity)
        out.append(c)
    return merge_duplicates(out)


def condense(
    keys: Iterable, *, min_hold_s: float, end: float | None = None
) -> list[Cue]:
    """Enforce a minimum hold by voting, never by dropping.

    Windows of ``min_hold_s`` open at each cue that clears the previous window.
    Every cue inside a window — the opener included — votes with the length of
    its raw span **that falls inside the window** times its dominance
    (Rhubarb's "select shape with highest total duration within the candidate
    range", weighted); the winner shows for the window, placed at the window
    start. A member whose span runs past the window's end and did not win is
    not lost: it opens the next window at the window's end, delayed — that is
    the hold doing its job. Ties go to the later arrival.

    The defect the epic names, verbatim semantics of the old compiler loop —
    'A' (the closure) and 'D' (the open vowel) are gone and a 40 ms 'B' holds
    for 500 ms:

    >>> raw = [(0.0, "X"), (0.30, "B"), (0.34, "A"), (0.38, "D"), (0.80, "X")]
    >>> old = []
    >>> for t, v in raw:
    ...     if old and (t - old[-1][0]) < 0.14:
    ...         continue
    ...     old.append((t, v))
    >>> old
    [(0.0, 'X'), (0.3, 'B'), (0.8, 'X')]

    The vote in the window at 0.30: B (0.04 × 0.3) and A (0.04 × 1.0) lose to
    D (0.06 in-window × 0.8), which shows at the window's start:

    >>> [(c.time, c.code) for c in condense(raw, min_hold_s=0.14)]
    [(0.0, 'X'), (0.3, 'D'), (0.8, 'X')]

    A cue exactly at the window edge opens the next window, untouched — also
    at the third edge, where ``0.28 + 0.14`` is a hair over ``0.42`` in binary:

    >>> [(c.time, c.code) for c in condense([(0.0, "X"), (0.14, "C"), (0.28, "D"), (0.42, "A")], min_hold_s=0.14)]
    [(0.0, 'X'), (0.14, 'C'), (0.28, 'D'), (0.42, 'A')]

    Without ``end`` the last cue shows forever, so it always survives (it opens
    its own window when it loses one):

    >>> [(c.time, c.code) for c in condense([(0.0, "X"), (0.1, "A")], min_hold_s=0.14)]
    [(0.0, 'X'), (0.14, 'A')]

    Windows chain, so nothing is lost: the opening rest keeps its window
    (0.10 of rest beats a 20 ms closure and 20 ms of F); F, running past the
    window, opens the next one at 0.14 and wins it over the 40 ms of C inside
    it; C in turn opens the window at 0.28:

    >>> [(c.time, c.code) for c in condense([(0.0, "X"), (0.10, "A"), (0.12, "F"), (0.20, "C"), (0.9, "X")], min_hold_s=0.14)]
    [(0.0, 'X'), (0.14, 'F'), (0.28, 'C'), (0.9, 'X')]

    A winner equal to the shape already showing is merged:

    >>> [(c.time, c.code) for c in condense([(0.0, "X"), (0.05, "B"), (0.10, "X")], min_hold_s=0.14)]
    [(0.0, 'X')]
    """
    cues = _cues(keys)
    if not cues:
        return []
    spans = _spans(cues, end=end)
    ends = [c.time + sp for c, sp in zip(cues, spans)]
    out: list[Cue] = []
    i = 0
    start = cues[0].time
    carried: Cue | None = None  # a member delayed into the next window as its opener
    while i < len(cues) or carried is not None:
        window_end = start + min_hold_s
        members: list[tuple[Cue, float]] = []
        if carried is not None:
            members.append(
                (carried, max(0.0, min(ends[carried_idx], window_end) - start))
            )
        j = i
        while j < len(cues) and (
            cues[j].time < window_end - _EDGE_EPS or (not members and j == i)
        ):
            overlap = max(0.0, min(ends[j], window_end) - max(cues[j].time, start))
            members.append((cues[j], overlap))
            j += 1
        # Ties go to the later arrival: scan reversed so max() keeps the last.
        winner, _ = max(reversed(members), key=lambda m: m[1] * m[0].dominance)
        if not out or out[-1].code != winner.code:
            out.append(Cue(start, winner.code, winner.intensity))
        # The last member runs past the window and lost: it opens the next one.
        last_cue, _ = members[-1]
        last_idx = j - 1 if members[-1][0] is not carried else carried_idx
        carried = None
        if last_cue is not winner and ends[last_idx] > window_end + _EDGE_EPS:
            carried, carried_idx = last_cue, last_idx
            start = window_end
        elif j < len(cues):
            start = cues[j].time  # the loop above guarantees it clears the window
        i = j
    return out


def coarticulate(
    keys: Iterable,
    *,
    fps: int,
    end: float | None = None,
    min_hold_s: float = DEFAULT_MIN_HOLD_S,
    lead_s: float = DEFAULT_LEAD_S,
    decay_s: float = DEFAULT_DECAY_S,
    rest: str = "X",
) -> list[Cue]:
    """All four passes, in the order the module docstring gives.

    >>> raw = [(0.0, "X"), (0.30, "B"), (0.34, "A"), (0.38, "D"), (0.80, "X")]
    >>> [(round(c.time, 3), c.code) for c in coarticulate(raw, fps=24, end=1.0)]
    [(0.0, 'X'), (0.257, 'D'), (0.717, 'X')]

    A track that ends on ``rest`` still does after the passes, at ``end`` when
    the decay left no room before it (the compiler appends its own terminal
    rest at the line's end regardless — this is for every other caller):

    >>> [(round(c.time, 3), c.code) for c in coarticulate([(0, "X"), (0.3, "D"), (0.69, "C"), (0.71, "X")], fps=24, end=0.71)]
    [(0.0, 'X'), (0.217, 'D'), (0.607, 'C'), (0.71, 'X')]

    The knobs are validated up front — a negative lead or a zero hold is a
    typo, not a style:

    >>> coarticulate(raw, fps=24, end=1.0, min_hold_s=0)
    Traceback (most recent call last):
    ...
    ValueError: min_hold_s must be > 0, got 0
    """
    if not (fps > 0):
        raise ValueError(f"fps must be > 0, got {fps}")
    if not (min_hold_s > 0):
        raise ValueError(f"min_hold_s must be > 0, got {min_hold_s}")
    if lead_s < 0 or decay_s < 0:
        raise ValueError(f"lead_s and decay_s must be >= 0, got {lead_s} and {decay_s}")
    cues_in = _cues(keys)
    one_frame = 1.0 / fps
    cues = suppress_weak(cues_in, max_weak_s=one_frame, end=end)
    cues = lead(cues, lead_s=lead_s)
    cues = decay(cues, decay_s=decay_s, rest=rest, end=end)
    out = condense(cues, min_hold_s=min_hold_s, end=end)
    if (
        end is not None
        and cues_in
        and cues_in[-1].code == rest
        and out
        and out[-1].code != rest
    ):
        # The closing rest lost its last window (or the decay pushed it onto
        # the end): the line still closes, at `end`.
        out.append(Cue(float(end), rest, cues_in[-1].intensity))
    return out


if __name__ == "__main__":
    import doctest

    print(doctest.testmod())
