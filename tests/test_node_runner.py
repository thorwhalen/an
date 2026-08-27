"""The `node -e` runner's own guards (an#128).

A retry is a dangerous thing to add to a test suite: the whole value of an#22's
blocking Windows leg is that a red one means something, and a retry that fires
on the wrong condition converts a real regression into a slow pass. These pin
the condition.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

from tests import _node

ROOT = Path(__file__).resolve().parents[1]


def test_a_non_zero_exit_is_never_retried():
    """MUTATION: `except (TimeoutExpired, CalledProcessError)`, or retry on rc != 0.

    A failing script is the thing these tests exist to catch. Retrying it would
    make a real regression look like a slow runner, which is strictly worse than
    the flake the retry exists for.
    """
    calls = []

    def failing(argv, **kwargs):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 1, stdout="", stderr="boom")

    original = _node.subprocess.run
    _node.subprocess.run = failing
    try:
        proc = _node.run_node("process.exit(1)")
    finally:
        _node.subprocess.run = original

    assert proc.returncode == 1
    assert len(calls) == 1, f"a failing script was run {len(calls)} times"


def test_a_timeout_is_retried_exactly_once_then_reported():
    """MUTATION: retry forever, or do not retry at all.

    One retry separates "the runner stalled" from "this never completes". The
    failure message must say which, because a bare `TimeoutExpired` traceback
    reads as though the script hangs — and every script run through here
    evaluates in milliseconds.
    """
    calls = []

    def always_timeout(argv, **kwargs):
        calls.append(argv)
        raise subprocess.TimeoutExpired(argv, kwargs.get("timeout", 0))

    original = _node.subprocess.run
    _node.subprocess.run = always_timeout
    try:
        with pytest.raises(pytest.fail.Exception) as excinfo:
            _node.run_node("while(true){}")
    finally:
        _node.subprocess.run = original

    assert len(calls) == _node.NODE_ATTEMPTS == 2, calls
    message = str(excinfo.value)
    assert "stalled runner" in message
    assert "an#128" in message, "the reader needs the reason, not just the fact"


def test_a_timeout_that_clears_on_the_retry_succeeds():
    """MUTATION: report on the first timeout without retrying.

    Otherwise the retry is decoration and the an#123 occurrence would still
    have gone red.
    """
    attempts = {"n": 0}

    def stall_once(argv, **kwargs):
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise subprocess.TimeoutExpired(argv, kwargs.get("timeout", 0))
        return subprocess.CompletedProcess(argv, 0, stdout="42\n", stderr="")

    original = _node.subprocess.run
    _node.subprocess.run = stall_once
    try:
        assert _node.node_json("console.log(42)") == 42
    finally:
        _node.subprocess.run = original

    assert attempts["n"] == 2


def test_windows_gets_a_larger_bound_than_linux(monkeypatch):
    """MUTATION: return `base` unscaled from `timeout_for`.

    The observed failure was a Windows `node` startup, and Windows process
    startup is measurably slower. A single bound tuned for Linux is what let a
    stall hold a release. Asserted by asking `timeout_for` on both platforms
    rather than by reading the module's text, so the scaling is pinned rather
    than the spelling of it.
    """
    monkeypatch.setattr(_node.sys, "platform", "linux")
    assert _node.timeout_for(30.0) == 30.0

    monkeypatch.setattr(_node.sys, "platform", "win32")
    assert _node.timeout_for(30.0) > 30.0


def test_a_longer_call_stays_proportionally_longer_on_windows(monkeypatch):
    """MUTATION: a flat Windows bound instead of a factor.

    One call legitimately needs longer — `test_screen_space` loads the vendored
    PixiJS bundle before evaluating anything. A flat Windows override would
    SHORTEN it there, which is the failure this issue is about, pointing the
    other way.
    """
    monkeypatch.setattr(_node.sys, "platform", "win32")

    assert _node.timeout_for(_node.NODE_BUNDLE_TIMEOUT_S) > _node.timeout_for(
        _node.NODE_TIMEOUT_S
    )


def test_no_test_still_spells_the_node_invocation_by_hand():
    """MUTATION: leave one call site un-migrated.

    The bound was a magic number at eight sites; a ninth added later reopens the
    hazard silently. Asked of the text of every test module, because that is
    where the duplication was.
    """
    offenders = []
    for path in sorted(ROOT.glob("tests/**/*.py")):
        if path.name == "_node.py":
            continue
        text = path.read_text(encoding="utf-8")
        for n, line in enumerate(text.splitlines(), 1):
            if re.search(r'\["node",\s*"-e"', line):
                offenders.append(f"{path.relative_to(ROOT)}:{n}: {line.strip()[:70]}")

    assert not offenders, (
        "`node -e` is invoked directly instead of through `tests._node.run_node`, "
        "which owns the bound, the single retry and the message:\n  "
        + "\n  ".join(offenders)
    )
