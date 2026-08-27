"""`loop_mode` semantics, and the Python↔JS parity that keeps them honest.

Python has always evaluated `loop_mode`; the JS cutout runtime ignored it and played
every clip once. Now both implement it — which creates a new failure mode: the two
drifting apart. `an/adapters/cutout/clip.py::_wrap_time` is the spec, and
`runtime.js::wrapTime` is a port of it.

Tier 1 is a golden table, always run: it pins the spec itself, including the three
edge cases that are easy to get subtly wrong.

Tier 2 runs the same table through the real `wrapTime` in `runtime.js` under node,
and skips when node is absent. The same extraction pattern also pins
`evaluateChannel` (`test_cutout_channel_parity.py`) and executes `applyPose` /
`applyProperty` (`test_loud_discards.py`).
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from tests._node import run_node

from an.adapters.cutout.clip import LoopMode, _wrap_time

RUNTIME_JS = Path(__file__).resolve().parents[1] / "an/data/cutout_runtime/runtime.js"

#: (mode, duration, t, expected). Computed from the Python implementation.
GOLDEN = [
    # once — clamp; past the end the last frame holds. Negative t clamps to 0.
    ("once", 2.0, -1.0, 0.0),
    ("once", 2.0, 0.0, 0.0),
    ("once", 2.0, 1.0, 1.0),
    ("once", 2.0, 2.0, 2.0),
    ("once", 2.0, 5.0, 2.0),
    # loop — modulo the duration.
    ("loop", 2.0, 0.0, 0.0),
    ("loop", 2.0, 1.5, 1.5),
    # EDGE: the loop point lands exactly ON a keyframe. It wraps to 0.0, so the
    # FIRST keyframe renders at the period boundary, not the last. Deliberate.
    ("loop", 2.0, 2.0, 0.0),
    ("loop", 1.0, 1.0, 0.0),
    ("loop", 2.0, 2.5, 0.5),
    ("loop", 2.0, 7.25, 1.25),
    ("loop", 0.14, 0.5, 0.08),
    # ping_pong — bounce over period 2*duration.
    ("ping_pong", 2.0, 0.0, 0.0),
    ("ping_pong", 2.0, 1.0, 1.0),
    # EDGE: the direction flip. t == duration is the apex, INCLUSIVE; just past it
    # the motion descends.
    ("ping_pong", 2.0, 2.0, 2.0),
    ("ping_pong", 2.0, 3.0, 1.0),
    ("ping_pong", 2.0, 4.0, 0.0),
    ("ping_pong", 2.0, 4.5, 0.5),
    ("ping_pong", 2.0, 7.25, 0.75),
    # EDGE: zero-length. `Clip` forbids duration <= 0, but a hand-written or
    # programmatically-built descriptor can carry one, so neither side may divide
    # by it or loop forever.
    ("once", 0.0, 5.0, 0.0),
    ("loop", 0.0, 5.0, 0.0),
    ("ping_pong", 0.0, 5.0, 0.0),
]


@pytest.mark.parametrize("mode,duration,t,expected", GOLDEN)
def test_golden_table(mode, duration, t, expected):
    assert _wrap_time(t, duration, LoopMode(mode)) == pytest.approx(expected)


def test_every_loop_mode_is_covered():
    """A new LoopMode member must arrive with golden rows, not silently untested."""
    assert {m for m, _, _, _ in GOLDEN} == {m.value for m in LoopMode}


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_js_wrap_time_matches_python_exactly():
    """The port must agree with the spec on every golden row.

    Loads the real runtime.js rather than a copy of the function, so a future edit
    to the shipped file is what gets checked.
    """
    script = f"""
    const fs = require('fs');
    const src = fs.readFileSync({str(RUNTIME_JS)!r}, 'utf8');
    // runtime.js is an IIFE around a browser global; lift just the function out.
    const start = src.indexOf('function wrapTime');
    if (start < 0) {{ console.error('wrapTime not found in runtime.js'); process.exit(2); }}
    let depth = 0, i = src.indexOf('{{', start), end = -1;
    for (let j = i; j < src.length; j++) {{
        if (src[j] === '{{') depth++;
        else if (src[j] === '}}') {{ depth--; if (depth === 0) {{ end = j + 1; break; }} }}
    }}
    const wrapTime = new Function('return (' + src.slice(start, end) + ')')();
    const cases = {json.dumps([[m, d, t] for m, d, t, _ in GOLDEN])};
    console.log(JSON.stringify(cases.map(([m, d, t]) => wrapTime(t, d, m))));
    """
    proc = run_node(script)
    assert proc.returncode == 0, f"node failed: {proc.stderr}"
    got = json.loads(proc.stdout)

    assert len(got) == len(GOLDEN)
    mismatches = [
        (mode, duration, t, expected, actual)
        for (mode, duration, t, expected), actual in zip(GOLDEN, got)
        if actual != pytest.approx(expected)
    ]
    assert not mismatches, f"JS/Python drift: {mismatches}"


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_js_treats_an_unknown_mode_as_once():
    """An unrecognised mode must degrade to `once`, never loop forever."""
    script = f"""
    const fs = require('fs');
    const src = fs.readFileSync({str(RUNTIME_JS)!r}, 'utf8');
    const start = src.indexOf('function wrapTime');
    let depth = 0, end = -1;
    for (let j = src.indexOf('{{', start); j < src.length; j++) {{
        if (src[j] === '{{') depth++;
        else if (src[j] === '}}') {{ depth--; if (depth === 0) {{ end = j + 1; break; }} }}
    }}
    const wrapTime = new Function('return (' + src.slice(start, end) + ')')();
    console.log(JSON.stringify([
        wrapTime(5.0, 2.0, 'boomerang'), wrapTime(5.0, 2.0, undefined)
    ]));
    """
    proc = run_node(script)
    assert proc.returncode == 0, f"node failed: {proc.stderr}"
    assert json.loads(proc.stdout) == [2.0, 2.0]  # clamped, i.e. `once`
