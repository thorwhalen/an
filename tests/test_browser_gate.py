"""The browser gate itself — an#22.

Every "verified by rendering" claim in this repo is verified on a developer
machine or an on-demand workflow run, because the default CI installs no
browser. That is a deliberate cost decision (see ``tests/conftest.py``'s
browser-gate section). What was *not* deliberate is how it used to be
implemented: eleven modules opened with a module-level

    playwright = pytest.importorskip("playwright.sync_api", ...)

which does not skip a browser test — it aborts the module import, so its tests
are never collected. Measured on the parent of the commit that added this file:
472 tests collected with Playwright present, 438 without. **Thirteen** of the
thirty-four casualties needed no browser at all, including every SSIM test for
``an.verify.media`` — the primitives Wave 2's metrics ledger is built on.

Nothing reported it, and nothing could: a test that is not collected appears in
neither the pass count nor the skip count, so the deficit was invisible in both
halves of the summary a reviewer reads.

WHAT THIS FILE DOES AND DOES NOT PROMISE
========================================

It does **not** make the bug "structurally impossible" — an earlier draft of
this docstring said that and an adversarial review disproved it in four
different ways, each with every guard green. What it does is make the *known*
routes loud, and make the *general* invariant checkable:

    Which tests EXIST must not depend on what is installed.

That invariant is asserted directly by
``test_collection_does_not_depend_on_the_environment``, which shadows every
optional import in the repo *and* strips the external tools from ``PATH``, then
compares node-id sets. It has a reference outside itself (pytest's own
collection), so it catches routes nobody has thought of — which the AST scanner,
being a list of known spellings, structurally cannot.

Every guard here is mutation-tested; see the PR that introduced it.
"""

from __future__ import annotations

import ast
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from . import conftest as _gate
from .conftest import BROWSER_ENV_VAR, requirement_verdict

TESTS_DIR = Path(__file__).resolve().parent
REPO_ROOT = TESTS_DIR.parent

#: Calls that must never run while a test module is being imported, and why.
#: This mapping is the single statement of the rule — the scanners iterate it
#: rather than hardcoding names, so adding a row here arms a new check.
FORBIDDEN_AT_IMPORT = {
    "importorskip": (
        "aborts the module import, so the module's whole test inventory "
        "disappears from collection instead of one test being skipped"
    ),
    "skip": (
        "`pytest.skip(..., allow_module_level=True)` has the same effect as "
        "`importorskip`: the tests stop existing rather than being skipped"
    ),
    "launch": (
        "launches a real browser during collection; eleven modules each did "
        "this independently. Use the cached conftest.chromium_available()"
    ),
}

#: Every optional import in this repo. Shadowing all of them at once is how the
#: environment-invariance guard avoids being a Playwright-shaped guard.
OPTIONAL_IMPORTS = (
    "playwright",
    "nw",
    "anthropic",
    "elevenlabs",
    "manim",
    "faster_whisper",
    "PIL",
)

#: External binaries the suite probes for. Removed from PATH by the same guard.
OPTIONAL_BINARIES = ("ffmpeg", "ffprobe", "node", "manim", "rhubarb")


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


class _ImportTimeCalls(ast.NodeVisitor):
    """Collect every call that runs while the module is being imported.

    Descends into class bodies — which execute at import, and which an earlier
    version of this scanner skipped, so a probe moved one indent to the right
    became invisible. Does **not** descend into function bodies, which run when
    the test runs; their decorators and argument defaults do run at import, so
    those are visited.
    """

    def __init__(self):
        self.calls: list[tuple[int, str]] = []

    def visit_Call(self, node):
        self.calls.append((node.lineno, _dotted(node.func)))
        self.generic_visit(node)

    def visit_FunctionDef(self, node):
        for dec in node.decorator_list:
            self.visit(dec)
        defaults = list(node.args.defaults) + [
            d for d in node.args.kw_defaults if d is not None
        ]
        for default in defaults:
            self.visit(default)

    visit_AsyncFunctionDef = visit_FunctionDef


def _module_level_calls(path: Path) -> list[tuple[int, str]]:
    """Every call reachable while importing ``path``, as ``(lineno, dotted)``."""
    visitor = _ImportTimeCalls()
    visitor.visit(ast.parse(path.read_text(encoding="utf-8"), filename=str(path)))
    return visitor.calls


# ---------------------------------------------------------------- static rules


@pytest.mark.parametrize("forbidden", sorted(FORBIDDEN_AT_IMPORT))
def test_no_test_module_calls_it_at_import_time(forbidden):
    """One case per rule in FORBIDDEN_AT_IMPORT, so a failure names the rule."""
    offenders = [
        f"{p.name}:{lineno} calls {name}()"
        for p in _test_modules()
        for lineno, name in _module_level_calls(p)
        if name.split(".")[-1] == forbidden
    ]
    assert not offenders, (
        f"{forbidden}() runs at import time, which {FORBIDDEN_AT_IMPORT[forbidden]}"
        ":\n  " + "\n  ".join(offenders)
    )


def test_no_conftest_removes_files_from_collection():
    """`collect_ignore` deletes tests without ever reporting a skip.

    Not a call, so the AST call-scanner above cannot see it — and it is the
    route an adversarial review used to reintroduce an#22 with every other
    guard green.
    """
    offenders = []
    for path in sorted(TESTS_DIR.rglob("*.py")):
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if re.match(r"\s*collect_ignore(_glob)?\s*(:.*)?=", line):
                offenders.append(f"{path.relative_to(REPO_ROOT)}:{n}: {line.strip()}")
    assert not offenders, (
        "collect_ignore removes files from collection, which is an#22 with a "
        "different spelling:\n  " + "\n  ".join(offenders)
    )


def test_the_scanner_sees_what_it_claims_to_see(tmp_path):
    """Guard the guard, once per rule, plus the two shapes it must NOT flag.

    A scanner whose parser silently returns nothing passes every scan, and one
    that flags everything is equally useless. This feeds it a module breaking
    every rule at module level and at CLASS-BODY level (which executes at
    import), plus the same calls inside a function body (which does not).
    """
    src = (
        "import pytest\n"
        "pw = pytest.importorskip('playwright.sync_api')\n"
        "b = pw.chromium.launch()\n"
        "pytest.skip('module level', allow_module_level=True)\n"
        "class TestThing:\n"
        "    _probe = pytest.importorskip('nope')\n"
        "def test_x():\n"
        "    pytest.importorskip('inside-a-body-is-fine')\n"
        "    pytest.skip('inside-a-body-is-fine')\n"
    )
    probe = tmp_path / "_scanner_probe.py"
    probe.write_text(src, encoding="utf-8")
    names = [n.split(".")[-1] for _, n in _module_level_calls(probe)]

    assert names.count("importorskip") == 2, (
        f"expected the module-level and class-body calls and NOT the one in a "
        f"function body; got {names}"
    )
    assert names.count("skip") == 1, f"function-body skip must not be flagged: {names}"
    assert "launch" in names, f"scanner missed the module-level launch: {names}"
    assert set(FORBIDDEN_AT_IMPORT) <= set(names), (
        "the probe no longer exercises every rule in FORBIDDEN_AT_IMPORT — add a "
        f"line for {sorted(set(FORBIDDEN_AT_IMPORT) - set(names))}"
    )


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


# ------------------------------------------------------------ subprocess rig


def _env(tmp_path: Path, *, shadow=(), drop_binaries=(), **overrides) -> dict:
    """A child environment with imports shadowed and binaries removed from PATH.

    Shadowing rather than uninstalling is what lets these guards be non-vacuous
    on a developer machine, which is the only place the regression can be
    written.
    """
    env = dict(os.environ)
    env.pop(BROWSER_ENV_VAR, None)
    env.pop("CI", None)

    if shadow:
        stub = tmp_path / "shadow"
        for name in shadow:
            (stub / name).mkdir(parents=True, exist_ok=True)
            (stub / name / "__init__.py").write_text(
                f"raise ImportError({name!r} + ' shadowed by test_browser_gate')\n",
                encoding="utf-8",
            )
        env["PYTHONPATH"] = os.pathsep.join(
            [str(stub), str(REPO_ROOT), env.get("PYTHONPATH", "")]
        ).rstrip(os.pathsep)
    else:
        env["PYTHONPATH"] = os.pathsep.join(
            [str(REPO_ROOT), env.get("PYTHONPATH", "")]
        ).rstrip(os.pathsep)

    if drop_binaries:
        keep = [
            d
            for d in env.get("PATH", "").split(os.pathsep)
            if d and not any(Path(d, b).exists() for b in drop_binaries)
        ]
        env["PATH"] = os.pathsep.join(keep)

    env.update(overrides)
    return env


def _pytest(env, *args) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", *args],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
    )


def _node_ids(proc: subprocess.CompletedProcess) -> set[str]:
    """Parse `--collect-only -q` node ids, and prove the parse was real.

    A comparison between two sets a parser silently emptied passes every time,
    so the parse is cross-checked against the count pytest itself reports. A
    change to pytest's output format then fails loudly instead of vacating the
    guard that depends on it.
    """
    stdout = proc.stdout
    ids = {line.strip() for line in stdout.splitlines() if "::" in line}
    trailer = re.search(r"(\d+)(?:/\d+)? tests? collected", stdout)
    assert trailer, f"no collection trailer to check the parse against:\n{stdout[-2000:]}"
    expected = int(trailer.group(1))
    assert len(ids) == expected, (
        f"parsed {len(ids)} node ids but pytest reported {expected} collected — "
        "the node-id parser has stopped matching pytest's output, which would "
        "silently vacate every comparison built on it"
    )
    return ids


# ------------------------------------------------------------ dynamic rules


def test_every_test_module_imports_with_every_optional_dependency_absent(tmp_path):
    """Import every test module with all optional imports shadowed; none may raise.

    Non-vacuous everywhere: it shadows the dependencies even on a machine that
    has them. A module that skips at import shows up here as ``Skipped``.
    """
    env = _env(tmp_path, shadow=OPTIONAL_IMPORTS)
    names = [p.stem for p in _test_modules()]
    script = (
        "import importlib, sys\n"
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
        "a test module failed to import with its optional dependency absent — "
        "its tests would vanish from collection rather than be skipped:\n"
        f"{proc.stdout}\n{proc.stderr}"
    )


def test_collection_does_not_depend_on_the_environment(tmp_path):
    """The invariant itself: which tests exist is independent of what is installed.

    This is the guard with a reference outside itself. The AST scanner is a list
    of spellings someone thought of; this compares pytest's own collection in a
    rich environment against collection in a stripped one, so it catches routes
    nobody enumerated — an ffmpeg-keyed module skip, a `collect_ignore`, a
    hand-rolled `sys.modules` check.
    """
    rich = _pytest(_env(tmp_path), "--collect-only")
    assert rich.returncode == 0, rich.stdout + rich.stderr
    stripped = _pytest(
        _env(tmp_path, shadow=OPTIONAL_IMPORTS, drop_binaries=OPTIONAL_BINARIES),
        "--collect-only",
    )
    assert stripped.returncode == 0, stripped.stdout + stripped.stderr

    a, b = _node_ids(rich), _node_ids(stripped)
    assert a == b, (
        "collection changed when the environment was stripped — only in the rich "
        f"environment: {sorted(a - b)}; only in the stripped one: {sorted(b - a)}"
    )


def test_explicit_opt_in_that_cannot_be_honoured_is_an_error(tmp_path):
    """`AN_BROWSER_TESTS=1` with no browser must abort the run, not skip.

    The failure this prevents: a CI job whose browser install silently failed
    reports green with every rendering test skipped — the state an#22 exists to
    end, reproduced inside the fix for it.
    """
    env = _env(tmp_path, shadow=("playwright",), **{BROWSER_ENV_VAR: "1"})
    proc = _pytest(env, "--collect-only", "-m", "browser")
    assert proc.returncode != 0, (
        "pytest exited 0 with an unhonourable browser opt-in:\n" + proc.stdout
    )
    combined = proc.stdout + proc.stderr
    # Assert the REQUIREMENT, not the variable name. AN_BROWSER_TESTS arms the
    # ffmpeg gate too, so asserting on the variable alone is satisfied by the
    # ffmpeg branch on any machine without ffmpeg — which is exactly what CI is,
    # so the guard would be exonerated in the one environment it protects.
    assert "headless browser" in combined, combined[-2000:]


def test_an_unhonourable_opt_in_does_not_abort_a_run_that_selected_nothing_gated(
    tmp_path,
):
    """The abort is scoped to the lane that was asked for.

    Raising unconditionally killed every invocation on a machine following this
    repo's own install hint: the `cutout` extra ships `ffmpeg-python`, a wrapper,
    not the ffmpeg binary. `AN_BROWSER_TESTS=1 pytest tests/test_browser_gate.py`
    then aborted with rc=4 before reading a single marker.
    """
    env = _env(
        tmp_path,
        shadow=("playwright",),
        drop_binaries=OPTIONAL_BINARIES,
        **{BROWSER_ENV_VAR: "1"},
    )
    proc = _pytest(env, "--collect-only", "-m", "not browser and not ffmpeg")
    assert proc.returncode == 0, (
        "an unhonourable opt-in aborted a run that selected no gated test:\n"
        + proc.stdout[-3000:]
        + proc.stderr[-2000:]
    )


def test_an_unrecognised_opt_in_value_is_an_error_not_a_silent_off(tmp_path):
    """`AN_BROWSER_TESTS=yse` must not read as "the user turned this off"."""
    env = _env(tmp_path, **{BROWSER_ENV_VAR: "yse"})
    proc = _pytest(env, "--collect-only")
    assert proc.returncode != 0, (
        "a typo'd opt-in was read as 'explicitly disabled' and silently skipped "
        "the whole rendering lane:\n" + proc.stdout[-2000:]
    )
    assert BROWSER_ENV_VAR in proc.stdout + proc.stderr


def test_the_run_announces_that_no_rendering_test_ran(tmp_path):
    """A green run must never be silent about having checked zero pixels.

    Runs the lane for real rather than collecting it, which is what makes this
    also the guard on the skip itself: delete `item.add_marker(skip)` and these
    tests attempt to run without a browser, so the return code stops being 0.
    """
    env = _env(tmp_path, shadow=("playwright",), CI="true")
    proc = _pytest(env, "-m", "browser")
    assert proc.returncode == 0, (
        "the gated lane did not come back clean — the skip is not being applied:\n"
        + proc.stdout[-3000:]
    )
    lines = [ln for ln in proc.stdout.splitlines() if ln.startswith("browser tests:")]
    assert lines, "no browser-test accounting line in the run summary:\n" + proc.stdout
    assert "0 ran" in lines[0], lines[0]
    assert "did not" in lines[0], lines[0]


def test_the_announcement_reports_an_observation_not_a_prediction():
    """`total - skipped` is a prediction, and it was wrong in ordinary runs.

    Asserted against the summary function directly rather than through a
    subprocess, because every real invocation that exposes the difference needs
    the gate verdict to be "run" — i.e. a browser — and there is none in CI. A
    subprocess form is therefore vacuous in exactly the environment that matters,
    which is how the first version of this guard passed the mutation.

    The state below is an ordinary `--collect-only` on a developer machine: 24
    gated tests collected, none skipped by the gate, and none executed. The
    prediction says 24 ran. Nothing ran.
    """
    written: list[str] = []

    class _Reporter:
        def write_line(self, line):
            written.append(line)

    # Save and RESTORE, never clear: these are the live session's counters, and
    # the first version of this test emptied them — so the real run's accounting
    # line silently disappeared from every full-suite run. A test that inspects
    # the honesty mechanism must not be what breaks it.
    saved_report = dict(_gate._GATE_REPORT)
    saved_ran = dict(_gate._RAN_COUNTS)
    _gate._GATE_REPORT.clear()
    _gate._GATE_REPORT.update({"browser": {"total": 24, "skipped": 0, "reason": ""}})
    _gate._RAN_COUNTS.clear()
    try:
        _gate.pytest_terminal_summary(_Reporter(), 0, None)
    finally:
        _gate._GATE_REPORT.clear()
        _gate._GATE_REPORT.update(saved_report)
        _gate._RAN_COUNTS.clear()
        _gate._RAN_COUNTS.update(saved_ran)

    assert written, "the summary printed nothing for 24 collected gated tests"
    assert "0 ran" in written[0], (
        "the summary reported a collection-time prediction rather than what "
        f"executed: {written[0]!r}"
    )
    assert "24 did not" in written[0], written[0]


def test_the_gate_honours_an_explicit_ci_false():
    """`CI=false` is a real convention and must not read as "in CI".

    Asserted on the REASON rather than the action, so it holds in both
    environments: with a browser the action is "run" and the CI text must be
    absent; without one the action is "skip" for the honest reason (no browser),
    and the CI text must still be absent. A `bool(env.get("CI"))` reading makes
    the CI text appear in both.
    """
    _action, message = _gate._gate_verdicts({"CI": "false"})["browser"]
    assert "CI installs no" not in message, (
        "CI=false was read as 'in CI', so a developer with that in their "
        f"environment silently loses the rendering lane: {message!r}"
    )


def test_the_ffmpeg_lane_is_not_reported_as_having_run(tmp_path):
    """The two lanes must not exonerate each other.

    Every `ffmpeg` test is also a `browser` test, so on the commonest developer
    configuration — ffmpeg present, no browser — the ffmpeg verdict is "run"
    while every one of its tests is skipped by the browser verdict. The
    per-requirement arithmetic reported "22 ran" for 22 tests that did not.
    """
    env = _env(tmp_path, shadow=("playwright",))
    proc = _pytest(env, "-m", "ffmpeg")
    assert proc.returncode == 0, proc.stdout[-3000:]
    lines = [ln for ln in proc.stdout.splitlines() if ln.startswith("ffmpeg tests:")]
    assert lines, "no ffmpeg accounting line:\n" + proc.stdout
    assert "0 ran" in lines[0], lines[0]


def test_the_browser_lane_is_not_empty(tmp_path):
    """A floor on the gated population, derived from collection, not from a grep.

    A grep for `pytest.mark.browser` is literally another copy of the thing it
    guards, so it can only confirm a copy-paste — and it counted this file,
    which mentions the marker in prose, so a third of the lane could be
    converted to hand-rolled skipifs without the number moving.

    Not a pinned exact count: that goes stale on every new render test and gets
    "fixed" by editing the number. A floor only moves when coverage is deleted.
    """
    proc = _pytest(_env(tmp_path), "--collect-only", "-m", "browser")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    ids = _node_ids(proc)
    modules = {node.split("::")[0] for node in ids}
    assert len(ids) >= 20, f"only {len(ids)} browser-marked tests: {sorted(ids)}"
    assert len(modules) >= 10, f"only {len(modules)} modules in the lane: {sorted(modules)}"


def test_the_conftest_doctests_actually_run():
    """A bare `pytest -q` collects no doctests at all, so run conftest's here.

    CI *does* reach them — it passes `--doctest-modules`, and
    `tests/conftest.py::tests.conftest.requirement_verdict` is in its collected
    set. But the local command in this repo's docs is a bare `pytest`, under
    which `--doctest-modules` is absent and every example in `_env_flag`,
    `_is_ci` and `requirement_verdict` is inert. Those examples are the
    executable statement of the tri-state flag and the decision matrix, so they
    should not be dead in the place people actually run.
    """
    import doctest

    from . import conftest as _conftest

    # ELLIPSIS only: it is the INTERSECTION of the two configured lanes — the
    # ini says `NORMALIZE_WHITESPACE ELLIPSIS`, CI overrides with `ELLIPSIS
    # IGNORE_EXCEPTION_DETAIL`. Running under the intersection means anything
    # green here is green in both; running under the union would let a doctest
    # pass here and fail in CI, which is the trap dol's CLAUDE.md documents.
    results = doctest.testmod(
        _conftest, verbose=False, report=False, optionflags=doctest.ELLIPSIS
    )
    assert results.attempted > 0, "no doctests found in conftest — did they move?"
    assert results.failed == 0, f"{results.failed} conftest doctest(s) failed"
