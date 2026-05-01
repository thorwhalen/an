"""Composition + flatten: combinator semantics, duration, absolute times."""

from __future__ import annotations

from an import (
    delay,
    flatten,
    loop,
    parallel,
    sequence,
    set_,
    tween,
)
from an.ir.compose import duration_of


def test_tween_flat():
    a = tween("a", "x", to=1.0, duration=2.0)
    flat = flatten(a)
    assert len(flat) == 1
    assert flat[0].start == 0.0
    assert flat[0].end == 2.0


def test_sequence_chains_times():
    a = sequence(
        tween("a", "x", to=1.0, duration=1.0),
        tween("a", "x", to=0.0, duration=2.0),
    )
    flat = flatten(a)
    assert [(f.start, f.end) for f in flat] == [(0.0, 1.0), (1.0, 3.0)]


def test_parallel_starts_simultaneously():
    a = parallel(
        tween("a", "x", to=1.0, duration=2.0),
        tween("b", "y", to=1.0, duration=3.0),
    )
    flat = flatten(a)
    assert all(f.start == 0.0 for f in flat)
    ends = sorted(f.end for f in flat)
    assert ends == [2.0, 3.0]


def test_delay_advances_cursor_only():
    a = sequence(
        tween("a", "x", to=1.0, duration=1.0),
        delay(0.5),
        tween("a", "x", to=0.0, duration=1.0),
    )
    flat = flatten(a)
    assert [(round(f.start, 2), round(f.end, 2)) for f in flat] == [
        (0.0, 1.0),
        (1.5, 2.5),
    ]


def test_loop_unrolls():
    a = loop(tween("a", "x", to=1.0, duration=0.5), 3)
    flat = flatten(a)
    assert len(flat) == 3
    assert [round(f.end - f.start, 3) for f in flat] == [0.5, 0.5, 0.5]
    assert [round(f.start, 3) for f in flat] == [0.0, 0.5, 1.0]


def test_set_action_does_not_advance_cursor():
    a = sequence(
        set_("a", "visible", True, at=0.0),
        tween("a", "x", to=1.0, duration=1.0),
    )
    flat = flatten(a)
    # Two leaf actions: the set + the tween. The set is at t=0, tween starts at t=0.
    assert [round(f.start, 3) for f in flat] == [0.0, 0.0]


def test_duration_of_matches_flatten_extent():
    a = sequence(
        parallel(
            tween("a", "x", to=1.0, duration=2.0),
            tween("b", "y", to=1.0, duration=3.0),
        ),
        delay(0.5),
        tween("c", "z", to=1.0, duration=1.0),
    )
    expected = 3.0 + 0.5 + 1.0
    assert duration_of(a) == expected
    flat = flatten(a)
    assert max(f.end for f in flat) == expected


def test_nested_round_trip_through_schema():
    """Composition tree should serialize and reload as a schema object."""
    from an.ir.schema import SequenceAction
    a = sequence(
        tween("a", "x", to=1.0, duration=1.0),
        parallel(
            tween("a", "y", to=2.0, duration=1.0),
            tween("a", "z", to=3.0, duration=1.0),
        ),
    )
    assert isinstance(a, SequenceAction)
    blob = a.model_dump_json()
    reloaded = SequenceAction.model_validate_json(blob)
    flat_orig = flatten(a)
    flat_reloaded = flatten(reloaded)
    assert [(f.start, f.end) for f in flat_orig] == [
        (f.start, f.end) for f in flat_reloaded
    ]
