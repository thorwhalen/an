"""A faked `subprocess.run` must not reach outside the module under test (an#152).

This module is the regression gate for the bug that zero-truncated the
machine's Python launcher four times (thorwhalen/priv#127). The chain, all of
it ordinary code:

    monkeypatch.setattr(render_mod.subprocess, "run", fake)   # patches the SHARED module
    ...
    environment_record()  ->  platform.platform()
                          ->  platform.architecture(sys.executable)
                          ->  subprocess.run(["file", "-b", sys.executable])
                          ->  fake  ->  Path(cmd[-1]).write_bytes(b"")

`cmd[-1]` is `sys.executable`, so the fake truncated the running interpreter.

**Every test here colds `platform`'s caches explicitly**, and that is the load-
bearing detail rather than a flourish: `platform.platform()` memoises, so on a
warm cache it never shells out and a test written without the cold would pass
whatever the code did — the "the test ran and measured something else" shape
this repo has been bitten by repeatedly. The cache must be reset to `{}` and not
`None`: `platform.platform()` calls `.get()` on it, so `None` raises
`AttributeError` and the test then fails for a third, unrelated reason.

Nothing here writes to the real interpreter, under any outcome. The stand-in is
a throwaway file under `tmp_path` that `sys.executable` is pointed at, so a
regression fails an assertion instead of breaking the developer's machine —
which is the only honest way to keep a test for this.
"""

from __future__ import annotations

import ast
import os
import platform
import subprocess
import sys
from pathlib import Path

import pytest

from an.adapters.cutout import render as render_mod
from an.bench import environment as env_mod
from tests._fake_subprocess import (
    OutsideSandbox,
    patch_subprocess_run,
    touch_output,
)

#: Content for the file standing in for `sys.executable`. Any non-empty bytes
#: would do; a recognisable string makes a failure dump readable.
_STANDIN_BYTES: bytes = b"#!/not/a/real/interpreter\n" * 8

#: The shell-out chain is POSIX-only, so the tests that exercise it are too.
#: `platform._syscmd_file` opens with ``if sys.platform in ('dos', 'win32',
#: 'win16'): return default`` — it never spawns anything — and Windows'
#: `platform.platform()` reads `sys.getwindowsversion()` while `processor()`
#: reads `PROCESSOR_IDENTIFIER` from the environment. So on Windows there is no
#: `file -b <sys.executable>` and no `uname -p` to intercept, and the outage
#: this module guards could not have happened.
#:
#: **Applied per test, not to the module.** Three of the six assert things that
#: are true on every platform — that the shared `subprocess` module is left
#: alone, that `touch_output` refuses, and that no unscoped spelling has crept
#: back — and those are worth running on Windows, which since an#22 gates the
#: release. Skipping the module would have silently retired them there.
#:
#: A vacuous pass is the alternative and it is worse: on Windows the chain tests
#: would pass *because nothing shells out*, which is the "the test ran and
#: measured something else" shape rather than evidence of the fix.
posix_chain_only = pytest.mark.skipif(
    sys.platform == "win32",
    reason=(
        "platform._syscmd_file returns early on win32 and never spawns, so "
        "there is no `file -b <sys.executable>` to intercept; this test would "
        "pass vacuously rather than exercise the an#152 chain"
    ),
)


def _cold_platform_caches(monkeypatch) -> None:
    """Make `platform.platform()` shell out, the way a fresh process would.

    `_platform_cache` and `_sys_version_cache` are dicts and must be reset to
    `{}`; `_uname_cache` is an object or None. Set through `monkeypatch` so the
    session's own caches are restored afterwards — a test that leaves them cold
    makes every later `platform` call spawn processes.
    """
    monkeypatch.setattr(platform, "_platform_cache", {}, raising=False)
    monkeypatch.setattr(platform, "_sys_version_cache", {}, raising=False)
    monkeypatch.setattr(platform, "_uname_cache", None, raising=False)


@pytest.fixture
def standin_executable(tmp_path, monkeypatch):
    """Point `sys.executable` at a throwaway file with known content."""
    exe = tmp_path / "standin_python"
    exe.write_bytes(_STANDIN_BYTES)
    monkeypatch.setattr(sys, "executable", str(exe))
    return exe


@posix_chain_only
def test_platform_platform_really_does_shell_out_with_a_cold_cache():
    """The premise of every other test here, asserted rather than assumed.

    If a future Python stops running `file -b <sys.executable>` from
    `platform.platform()`, the regression tests below would pass for a reason
    that has nothing to do with the fix. This fails first and says so.

    Records the argv rather than writing anything, so it is safe on any machine.
    """
    seen: list[list[str]] = []
    real_run = subprocess.run

    def recorder(cmd, *a, **kw):
        seen.append(list(cmd) if not isinstance(cmd, str) else [cmd])
        return real_run(["true"], capture_output=True, text=True)

    saved = (
        platform._platform_cache,
        platform._sys_version_cache,
        platform._uname_cache,
    )
    platform._platform_cache, platform._sys_version_cache = {}, {}
    platform._uname_cache = None
    subprocess.run = recorder
    try:
        platform.platform()
        platform.architecture()
    finally:
        subprocess.run = real_run
        (
            platform._platform_cache,
            platform._sys_version_cache,
            platform._uname_cache,
        ) = saved

    # `realpath`, because `platform._syscmd_file` follows symlinks before
    # shelling out. That detail is not incidental: it is why the outage
    # truncated `…/3.12.12/bin/python3.12`, the real binary, rather than the
    # `p12/bin/python3` symlink that `sys.executable` names — so every env
    # sharing that interpreter died at once.
    want = os.path.realpath(sys.executable)
    tails = [os.path.realpath(cmd[-1]) for cmd in seen]
    assert want in tails, (
        "`platform.platform()`/`architecture()` no longer pass sys.executable "
        f"as the last argv element on this build (saw {seen}). The an#152 "
        "regression tests below are then testing nothing — re-derive the chain "
        "before deleting or weakening them."
    )


@posix_chain_only
def test_the_faked_run_does_not_reach_the_interpreter(
    tmp_path, monkeypatch, standin_executable
):
    """MUTATION: `patch_subprocess_run` -> `setattr(render_mod.subprocess, "run", …)`.

    The whole bug, reproduced against a stand-in. With the module-scoped patch,
    `platform.platform()` inside `environment_record` reaches the REAL
    `subprocess.run`, so the stand-in is never written. With the shared-module
    patch it is truncated to 0 bytes, and this fails.
    """
    _cold_platform_caches(monkeypatch)

    class _Result:
        returncode = 0
        stderr = ""
        stdout = ""

    def fake_run(cmd, *a, **kw):
        touch_output(cmd[-1], root=tmp_path, argv=list(cmd))
        return _Result()

    patch_subprocess_run(monkeypatch, render_mod, fake_run)
    render_mod._ffmpeg_mux(tmp_path, 24, tmp_path / "out.mp4")

    monkeypatch.setattr(
        env_mod, "ffmpeg_identity", lambda: {"path": None, "banner": None}
    )
    # The exact call that pairs the fake with `platform.platform()`.
    env_mod.environment_record(pix_fmt="yuv420p", browser={})

    assert standin_executable.read_bytes() == _STANDIN_BYTES, (
        "the stand-in for sys.executable was modified by a faked "
        "subprocess.run. On a real machine this is the developer's Python "
        "interpreter, truncated to 0 bytes (an#152, thorwhalen/priv#127)."
    )


def test_the_shared_subprocess_module_is_left_alone(tmp_path, monkeypatch):
    """The property in one line: patching a module must not patch the stdlib.

    MUTATION: same as above. This states the invariant directly rather than via
    its most expensive consequence, so a reader sees WHAT is wrong before
    reading WHAT IT COST.
    """
    before = subprocess.run

    def fake_run(cmd, *a, **kw):  # pragma: no cover - never called here
        raise AssertionError("should not run")

    patch_subprocess_run(monkeypatch, render_mod, fake_run)

    assert subprocess.run is before, (
        "`subprocess.run` was replaced process-wide. Every library in the "
        "interpreter now gets this fake, including `platform`, which shells "
        "out `file -b <sys.executable>`."
    )
    assert render_mod.subprocess.run is fake_run, (
        "the module under test did not get the fake, so the patch is scoped to "
        "nothing at all"
    )
    # And the shim is still a usable `subprocess` for everything else.
    assert render_mod.subprocess.CalledProcessError is subprocess.CalledProcessError


def test_the_output_helper_refuses_a_path_outside_the_test_directory(tmp_path):
    """MUTATION: drop the `inside` check from `touch_output`.

    The closest guard to the mistake, and the one with the best message. It is
    what turns "a command this fake was never meant to see" from a silent write
    into a named failure.
    """
    outside = tmp_path.parent / "not_mine.bin"
    with pytest.raises(OutsideSandbox, match="outside the test's own directory"):
        touch_output(outside, root=tmp_path, argv=["file", "-b", str(outside)])
    assert not outside.exists(), "the refusal must happen BEFORE the write"


@posix_chain_only
def test_the_uname_probe_no_longer_litters_the_working_directory(
    tmp_path, monkeypatch, standin_executable
):
    """The corroborating artefact, kept as a test.

    `platform.processor()` runs `uname -p`, so a fake writing to `cmd[-1]`
    dropped a 0-byte file literally named `-p` into the working directory. One
    sat untracked in the worktrees for weeks and was deleted as litter rather
    than read as the evidence it was. An inexplicable stray file created during
    a test run is a write-path bug; this asserts it does not come back.
    """
    _cold_platform_caches(monkeypatch)
    monkeypatch.chdir(tmp_path)

    class _Result:
        returncode = 0
        stderr = ""
        stdout = ""

    def fake_run(cmd, *a, **kw):
        touch_output(cmd[-1], root=tmp_path, argv=list(cmd))
        return _Result()

    patch_subprocess_run(monkeypatch, render_mod, fake_run)
    monkeypatch.setattr(
        env_mod, "ffmpeg_identity", lambda: {"path": None, "banner": None}
    )
    env_mod.environment_record(pix_fmt="yuv420p", browser={})

    assert not (tmp_path / "-p").exists(), (
        "a 0-byte `-p` appeared: `uname -p` was intercepted by the fake, which "
        "means the patch is process-wide again (an#152)"
    )


#: The one place in the suite allowed to replace the shared `subprocess.run`,
#: named as ``(filename, function)`` so the exemption cannot spread by accident.
#:
#: `test_platform_platform_really_does_shell_out_with_a_cold_cache` MEASURES the
#: global behaviour — whether the stdlib still shells out with `sys.executable`
#: as ``cmd[-1]`` — and there is no scoped way to observe that: `platform`
#: imports `subprocess` *inside* its functions, so there is no `platform.
#: subprocess` attribute to rebind. Its recorder writes nothing, runs one
#: `true`, and restores in a `finally`.
#:
#: An allowlist of exactly one, with its reason, rather than no escape hatch at
#: all: a guard that cannot express a legitimate exception is a guard someone
#: eventually deletes wholesale.
_SHARED_PATCH_ALLOWED: frozenset[tuple[str, str]] = frozenset(
    {
        (
            "test_subprocess_blast_radius.py",
            "test_platform_platform_really_does_shell_out_with_a_cold_cache",
        )
    }
)


def _enclosing_function(tree, node):
    """Name of the function `node` sits in, or ``"<module>"``."""
    best = "<module>"
    for candidate in ast.walk(tree):
        if not isinstance(candidate, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        end = getattr(candidate, "end_lineno", candidate.lineno)
        if candidate.lineno <= node.lineno <= end:
            best = candidate.name
    return best


def _names_shared_subprocess_run(target) -> bool:
    """Is this assignment target the shared `subprocess.run`?

    Two spellings, and the second is the one the first draft of this guard
    missed: ``<mod>.subprocess.run = fake`` and a bare ``subprocess.run = fake``
    after ``import subprocess``.
    """
    if not (isinstance(target, ast.Attribute) and target.attr == "run"):
        return False
    owner = target.value
    if isinstance(owner, ast.Attribute) and owner.attr == "subprocess":
        return True  # `_node.subprocess.run = ...`
    return isinstance(owner, ast.Name) and owner.id == "subprocess"


def unscoped_sites(source: str) -> list[tuple[int, str]]:
    """``[(lineno, enclosing_function), ...]`` for every unscoped spelling.

    A function over SOURCE TEXT rather than a loop inlined in the test, so the
    detector can be exercised against known-good and known-bad snippets
    directly. A scanner only ever run over a tree that currently passes is a
    scanner nobody has checked can fail.
    """
    tree = ast.parse(source)
    hits: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        hit = False
        if isinstance(node, ast.Call):
            func = node.func
            # BOTH spellings of the call itself: `monkeypatch.setattr(...)` is
            # an Attribute, the builtin `setattr(...)` is a Name. The first cut
            # of this scanner knew only the Attribute form, so a plain
            # `setattr(subprocess, "run", f)` read as clean.
            is_setattr = (
                isinstance(func, ast.Attribute) and func.attr == "setattr"
            ) or (isinstance(func, ast.Name) and func.id == "setattr")
            if is_setattr and node.args:
                target = node.args[0]
                if isinstance(target, ast.Attribute) and target.attr == "subprocess":
                    hit = True  # setattr(x.subprocess, "run", ...)
                elif isinstance(target, ast.Constant) and isinstance(target.value, str):
                    hit = target.value.endswith("subprocess.run")
                elif isinstance(target, ast.Name) and target.id == "subprocess":
                    hit = True  # setattr(subprocess, "run", ...)
        elif isinstance(node, (ast.Assign, ast.AugAssign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            hit = any(_names_shared_subprocess_run(t) for t in targets)
        if hit:
            hits.append((node.lineno, _enclosing_function(tree, node)))
    return hits


#: Every spelling that replaces the shared module's ``run``, and every
#: near-miss that must NOT be flagged. Written as source text because that is
#: what the scanner reads.
_UNSCOPED_VARIANTS: tuple[str, ...] = (
    'monkeypatch.setattr(mod.subprocess, "run", fake)',
    'monkeypatch.setattr("pkg.mod.subprocess.run", fake)',
    'monkeypatch.setattr(subprocess, "run", fake)',
    "mod.subprocess.run = fake",
    "subprocess.run = fake",
    "an.bench.mutants.subprocess.run = fake",
    # The BUILTIN setattr, not `monkeypatch.setattr` — a `Name` call func
    # rather than an `Attribute`. The scanner read both of these as clean until
    # review caught it.
    'setattr(subprocess, "run", fake)',
    'setattr(mod.subprocess, "run", fake)',
)

_SCOPED_VARIANTS: tuple[str, ...] = (
    # The shape this guard steers towards: the module's OWN name is rebound.
    'monkeypatch.setattr(mod, "subprocess", shim)',
    # `from subprocess import run` in the product module — patching the name it
    # actually calls is correct and must not be flagged.
    'monkeypatch.setattr(mod, "run", fake)',
    "patch_subprocess_run(monkeypatch, mod, fake)",
    # A local variable that merely happens to be called `run`.
    "run = fake",
    # Reading it is not replacing it.
    "original = mod.subprocess.run",
)


def test_the_allowlist_is_pinned_by_literal():
    """MUTATION: add a second entry to `_SHARED_PATCH_ALLOWED`.

    Without this the allowlist is a hole that widens silently: appending one
    `(file, function)` pair exempts a whole function from the scanner, and every
    other test in this module stays green while doing it. Measured — a second
    entry plus a reintroduced `_node.subprocess.run = failing` passes the rest
    of the file.

    So the set is pinned by literal equality, the shape this repo already uses
    for its comparability tables: widening it has to be a deliberate edit HERE,
    next to the reason, rather than a line appended somewhere else.

    **The bar for a second entry is high.** The one exemption exists because
    `platform` imports `subprocess` *inside* its functions, so no module
    attribute exists to rebind and the global patch is the only way to observe
    the behaviour under test. "It is awkward to scope" is not that reason.
    """
    assert _SHARED_PATCH_ALLOWED == frozenset(
        {
            (
                "test_subprocess_blast_radius.py",
                "test_platform_platform_really_does_shell_out_with_a_cold_cache",
            )
        }
    ), (
        "the allowlist changed. Each entry exempts an entire function from the "
        "unscoped-patch scanner, so a new one needs the justification the "
        "existing one carries: no scoped spelling exists for what it does. If "
        "you are adding an entry because scoping is inconvenient, scope it "
        "instead (an#152)."
    )


@pytest.mark.parametrize("source", _UNSCOPED_VARIANTS)
def test_the_scanner_catches_every_unscoped_spelling(source):
    """MUTATION: drop the `Assign` branch, or the bare-`Name` case.

    The first draft of this scanner caught the two `setattr` forms and missed
    both assignment forms — and `tests/test_node_runner.py` was using the
    assignment form in three places at the time, so the guard shipped green
    against live instances of the defect it exists to catch. Review found it.
    """
    assert unscoped_sites(source), f"not flagged: {source}"


@pytest.mark.parametrize("source", _SCOPED_VARIANTS)
def test_the_scanner_does_not_flag_the_scoped_spellings(source):
    """MUTATION: flag on the attribute name `run` regardless of its owner.

    A guard that also fails the correct spelling teaches people to delete it.
    `setattr(mod, "run", fake)` after `from subprocess import run` is the case
    that matters: it patches the name the module actually calls, which is
    exactly right.
    """
    assert not unscoped_sites(source), f"wrongly flagged: {source}"


def test_every_destructive_fake_in_the_suite_uses_the_scoped_helper():
    """MUTATION: reintroduce any unscoped spelling anywhere under `tests/`.

    The tests above prove the fix for the sites that exist. This is what stops
    the next one being written the old way — the AST scanner is the weaker half
    of the guard, as `tests/test_browser_gate.py` says of its own, but it is the
    half that catches a NEW file nobody thought to check.

    **Four spellings, because the first draft of this guard caught two of them
    and review found the rest.** All four replace the shared module's `run`:

    ===================================== =============================
    spelling                              caught by
    ===================================== =============================
    ``setattr(mod.subprocess, "run", f)``  the `Call` branch
    ``setattr("pkg.mod.subprocess.run")``  the `Call` branch, string form
    ``mod.subprocess.run = f``             the `Assign` branch
    ``subprocess.run = f``                 the `Assign` branch, bare name
    ===================================== =============================

    What it must NOT flag is ``setattr(mod, "run", f)`` after ``from subprocess
    import run`` — that already patches the module's own name and is the shape
    this guard is steering people towards.
    """
    offenders = []
    for path in sorted(Path(__file__).parent.glob("*.py")):
        for lineno, func in unscoped_sites(path.read_text(encoding="utf-8")):
            if (path.name, func) in _SHARED_PATCH_ALLOWED:
                continue
            offenders.append(f"{path.name}:{lineno} (in {func})")

    assert not offenders, (
        "these replace `run` on the SHARED `subprocess` module rather than the "
        "name the module under test uses, so the fake reaches every library in "
        "the interpreter — including `platform`, which shells out "
        "`file -b <sys.executable>` (an#152, thorwhalen/priv#127):\n  "
        + "\n  ".join(offenders)
        + "\nUse `tests._fake_subprocess.patch_subprocess_run` instead."
    )
