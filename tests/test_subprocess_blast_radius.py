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


def test_every_destructive_fake_in_the_suite_uses_the_scoped_helper():
    """MUTATION: reintroduce `setattr(<mod>.subprocess, "run", …)` anywhere.

    The tests above prove the fix for the sites that exist. This is what stops
    the next one being written the old way — the AST scanner is the weaker half
    of the guard, as `tests/test_browser_gate.py` says of its own, but it is the
    half that catches a NEW file nobody thought to check.

    It flags the shape `setattr(<anything>.subprocess, "run", …)`, which is the
    unscoped spelling. The scoped one patches the module object itself, so it
    never matches.
    """
    import ast

    offenders = []
    for path in sorted(Path(__file__).parent.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if not (isinstance(func, ast.Attribute) and func.attr == "setattr"):
                continue
            if not node.args:
                continue
            target = node.args[0]
            # `monkeypatch.setattr(x.subprocess, "run", ...)`
            if isinstance(target, ast.Attribute) and target.attr == "subprocess":
                offenders.append(f"{path.name}:{node.lineno}")
            # `monkeypatch.setattr("pkg.mod.subprocess.run", ...)`
            elif isinstance(target, ast.Constant) and isinstance(target.value, str):
                if target.value.endswith("subprocess.run"):
                    offenders.append(f"{path.name}:{node.lineno}")

    assert not offenders, (
        "these patch the SHARED `subprocess` module rather than the name the "
        "module under test uses, so the fake reaches every library in the "
        "interpreter — including `platform`, which shells out "
        "`file -b <sys.executable>` (an#152, thorwhalen/priv#127):\n  "
        + "\n  ".join(offenders)
        + "\nUse `tests._fake_subprocess.patch_subprocess_run` instead."
    )
