"""The browser gate itself — an#22.

Every "verified by rendering" claim in this repo is verified on a developer
machine, because CI installs no browser. That is a deliberate cost decision
(see ``tests/conftest.py``'s browser-gate section). What was *not* deliberate is
how it used to be implemented: eleven modules opened with a module-level

    playwright = pytest.importorskip("playwright.sync_api", ...)

which does not skip a browser test — it aborts the module import, so its tests
are never collected. Measured on the parent of the commit that added this file:
472 tests collected with Playwright present, 438 without. Fourteen of the
thirty-four casualties needed no browser at all, including every SSIM test for
``an.verify.media`` — the primitives Wave 2's metrics ledger is built on.

Nothing reported it, and nothing could: a test that is not collected does not
appear in the skip count either, so the deficit was invisible in both the pass
count and the skip count.

This file is the guard against that returning. Its rules, and which environment
each one bites in:

======================================  ==========================================
Rule                                    Caught by
======================================  ==========================================
No module skips at import time          ``test_no_test_module_skips_at_import_time``
                                        (static, everywhere) and
                                        ``test_every_test_module_imports_without_playwright``
                                        (dynamic, everywhere)
No module probes for a browser at        ``test_no_test_module_launches_a_browser_at_import_time``
import time
Which tests EXIST does not depend on     ``test_collection_is_identical_with_and_without_playwright``
what is installed                        (bites on a machine that HAS Playwright)
An explicit opt-in that cannot be        ``test_explicit_opt_in_that_cannot_be_honoured_is_an_error``
honoured is an ERROR, not a skip
A gated run says so out loud             ``test_the_run_announces_how_many_browser_tests_were_skipped``
======================================  ==========================================

Every one of these is mutation-tested — see the PR that introduced it.
"""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

import pytest

from .conftest import BROWSER_ENV_VAR, requirement_verdict

TESTS_DIR = Path(__file__).resolve().parent
REPO_ROOT = TESTS_DIR.parent

#: Callables that must never run while a test module is being imported. The first
#: aborts the import (and with it the module's whole test inventory); the second
#: launches a real browser, which eleven modules each did independently.
_FORBIDDEN_AT_IMPORT = ("importorskip", "launch")


def _test_modules() -> list[Path]:
    return sorted(p for p in TESTS_DIR.glob("test_*.py"))


def _dotted(node: ast.AST) -> str:
    """Best-effort dotted name of a call target (``pytest.importorskip`` etc.)."""
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
    return ".".join(reversed(parts))


def _module_level_calls(path: Path) -> list[tuple[int, str]]:
    """Every call reachable while importing ``path``, as ``(lineno, dotted_name)``.

    "Reachable while importing" means: in a top-level statement, or in the
    *decorator* / *default* of one — not in a function or method body, which does
    not run until the test does.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found = []
    for stmt in tree.body:
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            # Only the decorators and argument defaults execute at import time.
            subtrees = list(getattr(stmt, "decorator_list", []))
            args = getattr(stmt, "args", None)
            if args is not None:
                subtrees += [d for d in (args.defaults or []) if d is not None]
                subtrees += [d for d in (args.kw_defaults or []) if d is not None]
        else:
            subtrees = [stmt]
        for sub in subtrees:
            for node in ast.walk(sub):
                if isinstance(node, ast.Call):
                    found.append((node.lineno, _dotted(node.func)))
    return found


# ---------------------------------------------------------------- static rules


def test_no_test_module_skips_at_import_time():
    """`importorskip` at module level deletes tests instead of skipping them.

    The whole point of an#22: the deficit it creates is invisible, because an
    uncollected test is counted neither as passed nor as skipped. Put the gate on
    the test — `@pytest.mark.browser` — and import Playwright inside the body.
    """
    offenders = [
        f"{p.name}:{lineno} calls {name}()"
        for p in _test_modules()
        for lineno, name in _module_level_calls(p)
        if name.split(".")[-1] == "importorskip"
    ]
    assert not offenders, (
        "module-level importorskip removes tests from collection rather than "
        "skipping them:\n  " + "\n  ".join(offenders)
    )


def test_no_test_module_launches_a_browser_at_import_time():
    """Probing for Chromium during collection costs a browser launch per module.

    Eleven modules each defined their own ``_chromium_installed()`` and called it
    at import. Use ``conftest.chromium_available()``, which is cached.
    """
    offenders = [
        f"{p.name}:{lineno} calls {name}()"
        for p in _test_modules()
        for lineno, name in _module_level_calls(p)
        if name.split(".")[-1] == "launch"
    ]
    assert not offenders, (
        "a browser is being launched during collection; use the cached "
        "conftest.chromium_available() instead:\n  " + "\n  ".join(offenders)
    )


def test_the_forbidden_call_list_is_actually_being_looked_for(tmp_path):
    """Guard the guard: `_module_level_calls` must see what it claims to see.

    A scanner whose parser silently returns nothing passes every scan. This
    feeds it a module that breaks both rules and requires both to be reported —
    and a third call inside a function body, which must NOT be flagged, because
    a scanner that flags everything is as useless as one that flags nothing.
    """
    src = (
        "import pytest\n"
        "pw = pytest.importorskip('playwright.sync_api')\n"
        "b = pw.chromium.launch()\n"
        "def test_x():\n"
        "    pytest.importorskip('nothing')\n"  # inside a body: must NOT be flagged
    )
    probe = tmp_path / "_scanner_probe.py"
    probe.write_text(src, encoding="utf-8")
    names = [n.split(".")[-1] for _, n in _module_level_calls(probe)]
    assert names.count("importorskip") == 1, (
        f"scanner missed or double-counted the module-level call: {names}"
    )
    assert "launch" in names, f"scanner missed the module-level launch: {names}"


# --------------------------------------------------------------- the decision


@pytest.mark.parametrize(
    "opt_in,available,ci,expected",
    [
        # No instruction: run it if you can, skip it in CI, skip it if you can't.
        (None, True, False, "run"),
        (None, False, False, "skip"),
        (None, True, True, "skip"),
        (None, False, True, "skip"),
        # Explicitly asked for: run it, or FAIL. Never quietly skip. A CI job
        # whose `playwright install` failed must go red, not green with 24 skips.
        (True, True, False, "run"),
        (True, True, True, "run"),
        (True, False, False, "error"),
        (True, False, True, "error"),
        # Explicitly refused: skip, even where it could run.
        (False, True, False, "skip"),
        (False, False, False, "skip"),
        (False, True, True, "skip"),
    ],
)
def test_requirement_verdict_matrix(opt_in, available, ci, expected):
    action, message = requirement_verdict(
        "headless browser",
        opt_in=opt_in,
        available=available,
        ci=ci,
        install_hint="hint",
    )
    assert action == expected
    if action != "run":
        assert message, "a skip or an error must say why"


def test_an_honoured_opt_in_is_the_only_way_to_run_in_ci():
    """CI runs a browser test only on an explicit request — never by accident."""
    assert requirement_verdict(
        "headless browser", opt_in=None, available=True, ci=True, install_hint="h"
    )[0] == "skip"
    assert requirement_verdict(
        "headless browser", opt_in=True, available=True, ci=True, install_hint="h"
    )[0] == "run"


# ------------------------------------------------------------ dynamic rules


def _without_playwright(tmp_path: Path) -> dict:
    """An environment in which importing ``playwright`` raises ImportError.

    Shadowing the real package is what lets this run on a developer machine,
    where Playwright IS installed and the regression would therefore be
    invisible to a plain collection.
    """
    stub = tmp_path / "no_playwright"
    (stub / "playwright").mkdir(parents=True)
    (stub / "playwright" / "__init__.py").write_text(
        "raise ImportError('playwright shadowed by tests/test_browser_gate.py')\n",
        encoding="utf-8",
    )
    import os

    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(
        [str(stub), str(REPO_ROOT), env.get("PYTHONPATH", "")]
    ).rstrip(os.pathsep)
    env.pop(BROWSER_ENV_VAR, None)
    return env


def _collect(env) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", "-p", "no:cacheprovider"],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
    )


def _node_ids(stdout: str) -> set[str]:
    return {line.strip() for line in stdout.splitlines() if "::" in line}


def test_every_test_module_imports_without_playwright(tmp_path):
    """Import every test module with Playwright absent; none may raise.

    This is the rule's dynamic form, and unlike the collection comparison below
    it is non-vacuous *everywhere*: it shadows Playwright even on a machine that
    has it. A module that skips at import shows up here as ``Skipped``.
    """
    env = _without_playwright(tmp_path)
    names = [p.stem for p in _test_modules()]
    script = (
        "import importlib, sys, traceback\n"
        f"names = {names!r}\n"
        "bad = []\n"
        "for n in names:\n"
        "    try:\n"
        "        importlib.import_module('tests.' + n)\n"
        "    except BaseException as e:\n"
        "        bad.append(f'{n}: {type(e).__name__}: {e}')\n"
        "print('\\n'.join(bad))\n"
        "sys.exit(1 if bad else 0)\n"
    )
    proc = subprocess.run(
        [sys.executable, "-c", script],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, (
        "a test module failed to import with Playwright absent — its tests would "
        "vanish from collection in CI rather than be skipped:\n"
        f"{proc.stdout}\n{proc.stderr}"
    )


def test_collection_is_identical_with_and_without_playwright(tmp_path):
    """Which tests EXIST must not depend on whether a browser is installed.

    Bites on a machine that has Playwright — which is the only place the
    regression can be introduced, since that is where the author works.
    """
    import os

    plain = dict(os.environ)
    plain.pop(BROWSER_ENV_VAR, None)
    with_pw = _collect(plain)
    without_pw = _collect(_without_playwright(tmp_path))
    assert with_pw.returncode == 0, with_pw.stdout + with_pw.stderr
    assert without_pw.returncode == 0, without_pw.stdout + without_pw.stderr

    a, b = _node_ids(with_pw.stdout), _node_ids(without_pw.stdout)
    assert a == b, (
        "collection changed when Playwright was removed — "
        f"only with Playwright: {sorted(a - b)}; only without: {sorted(b - a)}"
    )


def test_explicit_opt_in_that_cannot_be_honoured_is_an_error(tmp_path):
    """`AN_BROWSER_TESTS=1` with no browser must abort the run, not skip.

    The failure this prevents: a CI job whose browser install silently failed
    reports green with every rendering test skipped — which is precisely the
    state an#22 exists to end, reproduced inside the fix for it.
    """
    env = _without_playwright(tmp_path)
    env[BROWSER_ENV_VAR] = "1"
    proc = _collect(env)
    assert proc.returncode != 0, (
        "pytest exited 0 with an unhonourable browser opt-in:\n" + proc.stdout
    )
    combined = proc.stdout + proc.stderr
    assert BROWSER_ENV_VAR in combined, combined[-2000:]


def test_the_run_announces_how_many_browser_tests_were_skipped(tmp_path):
    """A green run must never be silent about having checked zero pixels."""
    env = _without_playwright(tmp_path)
    env["CI"] = "true"
    proc = _collect(env)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    lines = [ln for ln in proc.stdout.splitlines() if ln.startswith("browser tests:")]
    assert lines, "no browser-test accounting line in the run summary:\n" + proc.stdout
    assert "skipped" in lines[0], lines[0]
    assert "0 ran" in lines[0], lines[0]


def test_the_browser_lane_is_not_empty():
    """A floor on the gated population, so stripping the markers is noticed.

    Not a pinned exact count — that would go stale on every new render test and
    get "fixed" by editing the number. A floor only moves when someone deletes
    coverage.
    """
    marked = [
        p.name
        for p in _test_modules()
        if "pytest.mark.browser" in p.read_text(encoding="utf-8")
    ]
    assert len(marked) >= 10, (
        f"only {len(marked)} modules carry a browser marker: {marked}"
    )


def test_the_conftest_doctests_actually_run():
    """CI collects doctests from `tests/` only, and never from a conftest.

    So the examples in `conftest._env_flag` / `requirement_verdict` — which are
    the executable statement of the tri-state flag and the decision matrix — are
    dead unless something runs them. Leaving them dead would be the same shape as
    the bug this file exists to prevent: a claim that nothing checks.
    """
    import doctest

    from . import conftest as _conftest

    results = doctest.testmod(_conftest, verbose=False, report=False)
    assert results.attempted > 0, "no doctests found in conftest — did they move?"
    assert results.failed == 0, f"{results.failed} conftest doctest(s) failed"
