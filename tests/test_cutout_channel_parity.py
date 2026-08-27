"""Channel-evaluation parity: the shipped runtime.js against the Python spec.

`an/adapters/cutout/channel.py::evaluate` is the executable spec of
`runtime.js::evaluateChannel` (an#86). This file pins that claim the same way
`test_cutout_loop_modes.py` pins `wrapTime`: it lifts the *real* functions out
of the shipped runtime.js, runs them under node against a shared battery, and
compares to the Python implementation — behavioural, not textual, so a future
edit to either side is what gets checked.

The battery deliberately includes the cases where the two sides could disagree:

- every named easing on numeric AND string channels;
- **overshooting cubic beziers** on string channels — the old snap rule
  compared the *eased* position against 1.0, so an overshoot showed the second
  key early (or flapped A→B→A within one segment); both sides now snap on the
  raw segment position, and the overshoot rows are what keep that true;
- ``bool`` values — Python's ``isinstance(True, int)`` would lerp what JS's
  ``typeof`` snaps; both sides now snap them (the compiler refuses them
  upstream, but hand-built scene JSON is a supported input);
- duplicate-time keyframes, single-keyframe channels, and the clamps.

Also pinned here: the easing-name table exists in THREE hand-synced copies
(`an/base.py::EASING_PRESETS`, `easing.py::EASING_FUNCS`, runtime.js
``EASINGS``). Nothing else asserts they agree — a preset added to one side
used to fail only at render time in a browser.
"""

from __future__ import annotations

import json
import math
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from tests._node import run_node

from an.adapters.cutout.channel import Channel, Keyframe, evaluate
from an.adapters.cutout.easing import EASING_FUNCS
from an.base import EASING_PRESETS

RUNTIME_JS = (
    Path(__file__).resolve().parents[1] / "an" / "data" / "cutout_runtime" / "runtime.js"
)


# ---------------------------------------------------------------- the battery

_NAMED = ["linear", "ease", "ease_in", "ease_out", "ease_in_out", "step"]

#: Sample times chosen to hit: the clamps, exact keyframe times, segment
#: interiors, just-below-boundary positions where the old eased-snap rule
#: could fire early, the large-bezier row's measured divergence time, and the
#: exact float where (t - a.time) / span rounds up to 1.0 for the
#: string-boundary row (times harmlessly clamp on rows they don't concern).
_TIMES = [
    -0.5, 0.0, 0.146, 0.25, 0.5, 0.66, 0.75, 0.999, 0.9999999, 1.0, 1.5,
    1.7099999989999999, 2.0, 9.0,
    math.nextafter(9.767899248713501, 0.0), 9.767899248713501,
]


def _kf(time, value, easing=None):
    return {"time": time, "value": value, "easing": easing}


def _battery() -> list[dict]:
    cases: list[dict] = []
    # Numeric channels: every named easing, a plain bezier, an overshoot bezier
    # (overshoot is legitimate for numerics — animation overshoot is a feature).
    for easing in _NAMED + [[0.42, 0.0, 0.58, 1.0], [0.5, 2.0, 0.5, 2.0]]:
        cases.append(
            {"keyframes": [_kf(0.0, 0.0, easing), _kf(1.0, 10.0)], "kind": "numeric"}
        )
    # Large-magnitude bezier rows: a ULP of disagreement in `eased` is
    # amplified by (b - a), so these rows are what force the two Newton loops
    # to be structurally identical — the adversarial review of an#86 found the
    # Python loop's extra early-convergence break made 0→10 rows agree while
    # 1e9-scale rows missed the tolerance by five orders of magnitude.
    for easing in [[0.42, 0.0, 0.58, 1.0], [0.5, 2.0, 0.5, 2.0], [0.3, 3.0, 0.7, 0.0]]:
        cases.append(
            {
                "keyframes": [_kf(1.1, 1.0e9, easing), _kf(1.71, 0.0)],
                "kind": "numeric-large",
            }
        )
    # The snap-boundary rounding case: (t - a.time) / span rounds UP to 1.0
    # at t = nextafter(b.time, -inf) for these keyframe times, so a raw-u snap
    # shows 'B' one representable float early. The snap is time-based to make
    # the boundary exact; this row pins that both sides agree there.
    cases.append(
        {
            "keyframes": [
                _kf(0.1524221856720187, "A", "step"),
                _kf(9.767899248713501, "B"),
            ],
            "kind": "string-boundary",
        }
    )
    # String channels: the same easings PLUS the two measured overshoot shapes.
    # Under the old rule (0.5,2,0.5,2) showed the second key from u≈0.257 and
    # (0.3,3,0.7,0) flapped A→B→A across [0.146, 0.652]. Both must now hold 'A'
    # for the whole segment.
    for easing in _NAMED + [[0.5, 2.0, 0.5, 2.0], [0.3, 3.0, 0.7, 0.0]]:
        cases.append(
            {"keyframes": [_kf(0.0, "A", easing), _kf(1.0, "B")], "kind": "string"}
        )
    # A viseme-shaped 3-keyframe step channel.
    cases.append(
        {
            "keyframes": [
                _kf(0.0, "X", "step"),
                _kf(0.4, "C", "step"),
                _kf(1.0, "X", "step"),
            ],
            "kind": "string",
        }
    )
    # Mixed numeric/string takes the snap path on both sides.
    cases.append({"keyframes": [_kf(0.0, 3.0, "linear"), _kf(1.0, "B")], "kind": "mixed"})
    # Duplicate-time keyframes: zero-span segment resolves to the later value.
    cases.append(
        {
            "keyframes": [_kf(0.0, "A", "step"), _kf(0.5, "B", "step"), _kf(0.5, "C", "step"), _kf(1.0, "D", "step")],
            "kind": "string",
        }
    )
    # Single-keyframe channel.
    cases.append({"keyframes": [_kf(0.25, "only")], "kind": "string"})
    # Bools: both sides must SNAP, never lerp.
    cases.append(
        {"keyframes": [_kf(0.0, True, "linear"), _kf(1.0, False)], "kind": "bool"}
    )
    return cases


def _python_results(cases) -> list[list]:
    out = []
    for case in cases:
        ch = Channel(
            "t",
            "p",
            [
                Keyframe(
                    k["time"],
                    k["value"],
                    tuple(k["easing"]) if isinstance(k["easing"], list) else k["easing"],
                )
                for k in case["keyframes"]
            ],
        )
        out.append([evaluate(ch, t) for t in _TIMES])
    return out


# ------------------------------------------------------------- JS extraction


def _extract_js_block(src: str, start_marker: str) -> str:
    """Lift a brace-delimited definition out of the runtime.js IIFE."""
    start = src.index(start_marker)
    i = src.index("{", start)
    depth = 0
    for j in range(i, len(src)):
        if src[j] == "{":
            depth += 1
        elif src[j] == "}":
            depth -= 1
            if depth == 0:
                end = j + 1
                # Include a trailing `;` for const declarations.
                if src[end : end + 1] == ";":
                    end += 1
                return src[start:end]
    raise AssertionError(f"unbalanced braces after {start_marker!r}")


def _js_results(cases) -> list[list]:
    src = RUNTIME_JS.read_text(encoding="utf-8")
    pieces = [
        _extract_js_block(src, "const EASINGS ="),
        _extract_js_block(src, "function cubicBezier"),
        _extract_js_block(src, "function applyEasing"),
        _extract_js_block(src, "function evaluateChannel"),
    ]
    script = "\n".join(
        pieces
        + [
            f"const cases = {json.dumps(cases)};",
            f"const times = {json.dumps(_TIMES)};",
            "const out = cases.map(c => times.map(t => evaluateChannel(c, t)));",
            "console.log(JSON.stringify(out));",
        ]
    )
    proc = run_node(script)
    assert proc.returncode == 0, f"node failed: {proc.stderr}"
    return json.loads(proc.stdout)


# -------------------------------------------------------------------- tests


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_evaluate_channel_matches_the_python_spec_exactly():
    cases = _battery()
    py = _python_results(cases)
    js = _js_results(cases)
    assert len(py) == len(js) == len(cases)
    mismatches = []
    for ci, (case, prow, jrow) in enumerate(zip(cases, py, js)):
        for t, pv, jv in zip(_TIMES, prow, jrow):
            if isinstance(pv, float) and isinstance(jv, (int, float)):
                ok = jv == pytest.approx(pv, rel=1e-12, abs=1e-12)
            else:
                ok = pv == jv
            if not ok:
                mismatches.append((ci, case["kind"], t, pv, jv))
    assert not mismatches, f"JS/Python channel drift: {mismatches}"


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_overshoot_bezier_on_a_string_channel_holds_the_first_key():
    """The regression this file exists for, asserted directly (not via parity).

    Parity alone would stay green if BOTH sides regressed to the eased-snap
    rule together; this row asserts the intended behaviour absolutely.
    """
    flap = {"keyframes": [_kf(0.0, "A", [0.3, 3.0, 0.7, 0.0]), _kf(1.0, "B")]}
    over = {"keyframes": [_kf(0.0, "A", [0.5, 2.0, 0.5, 2.0]), _kf(1.0, "B")]}
    inside = [0.146, 0.25, 0.3, 0.5, 0.66, 0.999]
    for case in (flap, over):
        js_row = _js_results([case])[0]
        for t, v in zip(_TIMES, js_row):
            expected = "A" if t < 1.0 else "B"
            assert v == expected, f"t={t}: got {v!r}, wanted {expected!r}"
        ch = Channel(
            "t", "p", [Keyframe(k["time"], k["value"], tuple(k["easing"]) if isinstance(k["easing"], list) else None) for k in case["keyframes"]]
        )
        for t in inside:
            assert evaluate(ch, t) == "A"


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_both_sides_validate_easing_on_non_numeric_segments():
    """A typo'd easing name must raise on a SWAP channel too, in both languages.

    Easing is never APPLIED to a non-numeric value, so the lazy 'optimisation'
    of validating it only on the numeric branch would leave every other test
    green while the two evaluators diverge (Python raises, JS silently
    renders). The an#86 adversarial review demonstrated exactly that mutation
    surviving the original battery — this test is what kills it.
    """
    bad = {"keyframes": [_kf(0.0, "A", "eaze"), _kf(1.0, "B")]}
    src = RUNTIME_JS.read_text(encoding="utf-8")
    pieces = [
        _extract_js_block(src, "const EASINGS ="),
        _extract_js_block(src, "function cubicBezier"),
        _extract_js_block(src, "function applyEasing"),
        _extract_js_block(src, "function evaluateChannel"),
    ]
    script = "\n".join(
        pieces
        + [
            f"const bad = {json.dumps(bad)};",
            "try { evaluateChannel(bad, 0.5); console.log('SILENT'); }",
            "catch (e) { console.log('RAISED: ' + e.message); }",
        ]
    )
    proc = run_node(script)
    assert proc.returncode == 0, f"node failed: {proc.stderr}"
    out = proc.stdout.strip()
    assert out.startswith("RAISED") and "eaze" in out, (
        f"JS accepted an unknown easing on a non-numeric segment: {out}"
    )
    ch = Channel("a", "hands", [Keyframe(0.0, "A", "eaze"), Keyframe(1.0, "B")])
    with pytest.raises(ValueError, match="unknown easing preset"):
        evaluate(ch, 0.5)


def test_the_three_easing_tables_agree():
    """EASING_PRESETS (an/base.py), EASING_FUNCS (easing.py), EASINGS (runtime.js).

    Three hand-synced copies; a preset added to one side previously failed only
    at render time in a browser ('unknown easing') — loud, but invisible to
    every test.
    """
    src = RUNTIME_JS.read_text(encoding="utf-8")
    block = _extract_js_block(src, "const EASINGS =")
    js_names = set(re.findall(r"^\s*(\w+):", block, re.M))
    assert set(EASING_PRESETS) == set(EASING_FUNCS) == js_names, (
        f"easing tables drifted: presets={sorted(EASING_PRESETS)}, "
        f"python={sorted(EASING_FUNCS)}, js={sorted(js_names)}"
    )
