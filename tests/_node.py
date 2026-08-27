"""One place that owns running a `node -e` script from a test.

Eight call sites each spelled `subprocess.run(["node", "-e", script], …,
timeout=30)`. That is a magic number repeated eight times against this repo's
own rule, and it is also a **release hazard**: an#22 made the Windows leg
blocking and gave `publish` a `needs` edge on it, so a slow `node` startup on a
Windows runner can hold a release. It happened once on an#123 — a re-run on the
identical SHA went green and the commit touched no JS.

The reflex answer, re-running, is indistinguishable from the reflex that hides a
real Windows failure, and a blocking leg is only worth having while a red one
means something. So the fix is here, at the call, not `continue-on-error` on the
job.

Three things this owns:

- **The bound, once**, and larger on Windows, where process startup is
  measurably slower.
- **One retry, on `TimeoutExpired` only.** Never on a non-zero exit: that is the
  script failing, which is the thing these tests exist to catch, and retrying it
  would turn a real regression into a slow pass.
- **A message that says which happened**, so the next reader can tell a slow
  runner from a broken script without re-deriving it from a traceback.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys

import pytest

#: Seconds to allow `node` to start, evaluate and exit. Not measuring the
#: script — every one of these evaluates in milliseconds — so it is a bound on
#: the runner, not on the work.
NODE_TIMEOUT_S: float = 30.0

#: One call needs longer than the rest and says why: `test_screen_space` loads
#: the vendored PixiJS bundle before it evaluates anything.
NODE_BUNDLE_TIMEOUT_S: float = 60.0

#: Windows process startup is measurably slower, and a bound tuned for Linux is
#: what let a `node` stall hold a release (an#128). Applied to whichever base a
#: caller asks for, so a longer call stays proportionally longer.
WINDOWS_TIMEOUT_FACTOR: float = 3.0


def timeout_for(base: float = NODE_TIMEOUT_S) -> float:
    """`base`, scaled for the platform this is running on."""
    return base * WINDOWS_TIMEOUT_FACTOR if sys.platform == "win32" else base

#: Attempts on `TimeoutExpired`. Two, not more: one retry distinguishes "the
#: runner stalled" from "this never completes", and a third would start hiding
#: the second case.
NODE_ATTEMPTS: int = 2


def have_node() -> bool:
    """Whether `node` is on PATH at all."""
    return shutil.which("node") is not None


requires_node = pytest.mark.skipif(not have_node(), reason="node is not installed")


def run_node(
    script: str, *args: str, timeout: float = NODE_TIMEOUT_S
) -> subprocess.CompletedProcess:
    """Evaluate `script` with `node -e`, and return the completed process.

    Raises `pytest.fail` on a timeout that survives the retry, naming it as a
    runner stall rather than letting a bare `TimeoutExpired` traceback imply the
    script hangs. A non-zero exit is returned unchanged for the caller to
    assert on — it is a real failure and must stay loud.
    """
    bound = timeout_for(timeout)
    last: subprocess.TimeoutExpired | None = None
    for _attempt in range(NODE_ATTEMPTS):
        try:
            return subprocess.run(
                ["node", "-e", script, *args],
                capture_output=True,
                text=True,
                timeout=bound,
            )
        except subprocess.TimeoutExpired as exc:  # pragma: no cover - runner-dependent
            last = exc
    pytest.fail(
        f"`node -e` did not finish within {bound}s on either of "
        f"{NODE_ATTEMPTS} attempts. Every script run through here evaluates in "
        f"milliseconds, so this is a stalled runner rather than a hanging "
        f"script — but it is reported rather than retried away, because a "
        f"blocking Windows leg is only worth having while a red one means "
        f"something (an#22, an#128). Last: {last}"
    )


def node_json(script: str, *args: str, timeout: float = NODE_TIMEOUT_S):
    """`run_node`, asserting a clean exit and parsing stdout as JSON.

    The shape seven of the eight call sites wanted.
    """
    proc = run_node(script, *args, timeout=timeout)
    assert proc.returncode == 0, f"node failed: {proc.stderr}"
    return json.loads(proc.stdout.strip())
