"""Faking a module's ``subprocess`` without faking everybody else's (an#152).

A test that wants to inspect the argv `an` hands ffmpeg, without needing ffmpeg,
reaches for ``monkeypatch.setattr(some_module.subprocess, "run", fake)``. That
spelling looks scoped and is not: ``some_module.subprocess`` **is** the one
shared `subprocess` module object, so patching its ``run`` attribute replaces
`subprocess.run` **process-wide**, for every library in the interpreter, for the
duration of the test.

That is not a style point. Paired with a fake that writes to ``cmd[-1]`` — the
natural shape, since the faked command is a mux whose output the caller checks
for — it zero-truncated the machine's Python launcher four times
(thorwhalen/priv#127, an#152). The chain is short and entirely ordinary:

    environment_record()          # an/bench/environment.py
      -> platform.platform()      # stdlib
      -> platform.architecture(sys.executable)
      -> subprocess.run(["file", "-b", sys.executable])   # ...now the fake
      -> Path(cmd[-1]).write_bytes(b"")                   # cmd[-1] IS sys.executable

`platform.platform()` memoises, so it fires only when that cache is cold when
the test runs — a function of test selection and ordering, which is why it read
as a haunting rather than a bug. The same interception drops a 0-byte ``./-p``
in the working directory, from `platform.processor()`'s ``uname -p``; that file
sat untracked in the worktrees for weeks, noticed and dismissed as litter.

This module supplies the two halves of the fix, so neither is something a future
test has to remember:

- :func:`patch_subprocess_run` rebinds **the name the module itself uses** —
  ``module.subprocess`` — to a shim that delegates everything except ``run``.
  Nothing outside that module's namespace is affected, so the stdlib's own
  `subprocess.run` is still the real one.
- :func:`touch_output` writes the file a faked command claims to produce, and
  **refuses** any path outside the test's own directory. A fake that writes
  wherever ``cmd[-1]`` points is one unexpected argv away from the outage above;
  refusing makes the surprise loud and local instead.
"""

from __future__ import annotations

import subprocess as _real_subprocess
from pathlib import Path
from typing import Any, Callable


class OutsideSandbox(AssertionError):
    """A faked command tried to write outside the test's own directory.

    An `AssertionError` because that is what it is: a test asserting something
    about its own blast radius and finding it false. The message carries the
    argv, because the useful next question is always "which command was that?"
    and the answer is usually one the fake was never meant to intercept.
    """


def touch_output(
    path: str | Path, *, root: Path, argv: list[str] | None = None
) -> None:
    """Create the output file a faked command claims to have written.

    ``root`` is the directory the test owns — ``tmp_path``, normally. Anything
    outside it raises :class:`OutsideSandbox` instead of being written.

    >>> import tempfile
    >>> with tempfile.TemporaryDirectory() as d:
    ...     root = Path(d)
    ...     touch_output(root / "out.mp4", root=root)
    ...     (root / "out.mp4").exists()
    True

    The refusal is the whole point of the function:

    >>> with tempfile.TemporaryDirectory() as d:
    ...     touch_output("/usr/bin/python3", root=Path(d))
    Traceback (most recent call last):
        ...
    tests._fake_subprocess.OutsideSandbox: ...
    """
    target = Path(path)
    root = Path(root).resolve()
    try:
        resolved = target.resolve()
        inside = resolved == root or root in resolved.parents
    except OSError:  # pragma: no cover - a path that will not even resolve
        inside = False
    if not inside:
        raise OutsideSandbox(
            f"a faked subprocess tried to write {target}, which is outside the "
            f"test's own directory ({root}). argv was {argv} -- almost "
            f"certainly a command this fake was never meant to intercept."
        )
    target.write_bytes(b"")


class _SubprocessShim:
    """The real `subprocess`, with ``run`` replaced. Delegates everything else.

    Deliberately not a `SimpleNamespace` carrying only ``run``: the module under
    test also reaches for `subprocess.CalledProcessError`, `subprocess.PIPE` and
    friends, and a stand-in that answers only the attribute the test thought
    about fails later, elsewhere, as an `AttributeError` that reads like a bug
    in the product.
    """

    def __init__(self, run: Callable[..., Any]) -> None:
        self.run = run

    def __getattr__(self, name: str) -> Any:
        return getattr(_real_subprocess, name)


def patch_subprocess_run(monkeypatch, module, run: Callable[..., Any]) -> None:
    """Point ``module``'s own ``subprocess`` name at a shim whose ``run`` is ``run``.

    The scoped spelling. ``monkeypatch.setattr(module.subprocess, "run", run)``
    is the unscoped one and must not be used: it mutates the shared module
    object, so `platform`, `dol`, `playwright` and every other importer get the
    fake too. This module's docstring records what that cost.

    Pass the *module object the product code lives in* — e.g.
    ``an.adapters.cutout.render`` — not the `subprocess` module.
    """
    assert getattr(module, "subprocess", None) is not None, (
        f"{module.__name__} has no `subprocess` attribute to rebind. This "
        f"helper patches the NAME a module uses, so the module must import "
        f"`subprocess` itself rather than `from subprocess import run`"
    )
    monkeypatch.setattr(module, "subprocess", _SubprocessShim(run))
