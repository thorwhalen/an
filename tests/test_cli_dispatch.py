"""The CLI surface (an#45) — 17 commands that had no test at all until now.

`an/__main__.py` swapped `argh` (LGPL-3.0) for `typer` (MIT), and the dispatcher
is the whole of what changed. That is exactly the shape of change that goes
wrong invisibly: every command still *exists*, `--help` still works, and a flag
quietly stops being accepted or a return value quietly stops being printed.

So these tests assert the three things argh gave for free and typer does not —
hyphenated command names, printed return values, propagated exit codes — plus
the projection itself: every function in the dispatch lists reaches the CLI,
under the name the documentation uses.

They run in the **default** CI leg. `CliRunner` invokes in-process, so nothing
here renders, spends, or launches a browser: the commands exercised are the ones
whose bodies are pure (`--help`) or cheap, and the ones with expensive bodies are
checked for *registration and signature*, which is what the swap could break.
"""

from __future__ import annotations

import sys

import pytest
from typer.testing import CliRunner

from an.__main__ import build_app, command_name
from an.tools import _dispatch_funcs, _dispatch_namespaces

runner = CliRunner()


def _app():
    return build_app()


def _registered(app) -> list[str]:
    """The command names typer will actually accept, read off the app object.

    Structural rather than scraped from `--help`: the help text wraps, and a
    substring check against it says `character-new` contains `new` — so a
    prefix-stripping bug passes a text assertion while breaking every documented
    invocation. Measured: that is exactly which mutation survived the first pass.
    """
    return [c.name for c in app.registered_commands]


def _group_commands(app, group: str) -> list[str]:
    info = next(g for g in app.registered_groups if g.name == group)
    return [c.name for c in info.typer_instance.registered_commands]


# ------------------------------------------------------------- the projection


def test_every_dispatched_function_reaches_the_cli():
    """MUTATION: in `build_app`, skip a function, or drop the namespace loop.

    `an.tools._dispatch_funcs` is the SSOT for the CLI surface — the
    "dispatch to interface" pillar. A function on that list that the app never
    registers is a command the docs promise and the binary does not have.
    """
    app = _app()
    assert _registered(app) == [command_name(f) for f in _dispatch_funcs]
    assert [g.name for g in app.registered_groups] == list(_dispatch_namespaces)
    assert runner.invoke(app, ["--help"]).exit_code == 0


def test_command_names_are_hyphenated_as_argh_spelled_them():
    """MUTATION: in `build_app`, drop `name=command_name(func)`.

    Typer's default is the underscored function name, so `an bench_compare`
    would replace `an bench-compare` — and every documented invocation, every
    README line and every skill would be wrong at once.
    """
    underscored = [f for f in _dispatch_funcs if "_" in f.__name__]
    assert underscored, "no multi-word command, so this test asserts nothing"
    names = _registered(_app())
    for func in underscored:
        assert func.__name__ not in names, (
            f"{func.__name__} is exposed with an underscore; it should be "
            f"{command_name(func)}"
        )
        assert command_name(func) in names


def test_a_namespaced_command_is_reachable_under_its_group():
    """MUTATION: in `build_app`, drop `app.add_typer(sub, name=group)`.

    The documented spelling is `an character new`. The functions are named bare
    in `an/characters/cli.py`, exactly as argh required, so there is no prefix
    to strip — a strip was defensive code with no live case, and it survived its
    own mutation test, which is how it was found and removed.
    """
    names = _group_commands(_app(), "character")
    assert names == [command_name(f) for f in _dispatch_namespaces["character"]]
    assert runner.invoke(_app(), ["character", "--help"]).exit_code == 0
    assert runner.invoke(_app(), ["character", "validate", "--help"]).exit_code == 0


@pytest.mark.parametrize(
    "func", _dispatch_funcs, ids=[f.__name__ for f in _dispatch_funcs]
)
def test_every_command_has_help_derived_from_its_docstring(func):
    """argh used the docstring as the description, and these docstrings document
    every flag — so losing it loses the entire per-flag documentation of the CLI.

    NOT mutation-tested by removing `help=inspect.getdoc(func)`: that survives,
    because `functools.wraps` copies `__doc__` and typer falls back to it. Two
    independent sources, so neither is individually guardable — which is worth
    knowing rather than papering over. What IS guarded is `functools.wraps`
    itself, below, and it matters far more.
    """
    result = runner.invoke(_app(), [command_name(func), "--help"])
    assert result.exit_code == 0, result.output
    first_line = (func.__doc__ or "").strip().splitlines()[0]
    # Typer wraps at the terminal width, so compare on the first few words
    # rather than the whole line.
    opening = " ".join(first_line.split()[:4])
    assert opening in " ".join(result.output.split()), (
        f"{func.__name__}'s help does not come from its docstring"
    )


def test_the_wrapper_preserves_the_signature_that_is_the_command_line():
    """MUTATION: in `_printing`, drop the `@functools.wraps(func)`.

    `inspect.signature` follows `__wrapped__`, and that signature IS the command
    line: typer reads it to derive every argument and every flag. Without it
    typer sees `(*args, **kwargs)` and the command accepts nothing — every flag
    on all 17 commands disappears at once, and `--help` still renders.
    """

    def sized(width: int = 3, label: str = "x") -> str:
        return f"{label}:{width}"

    def other() -> str:
        return "y"

    app = build_app([sized, other], {})
    result = runner.invoke(app, ["sized", "--width", "7", "--label", "wide"])
    assert result.exit_code == 0, result.output
    assert "wide:7" in result.output
    assert "--width" in " ".join(runner.invoke(app, ["sized", "--help"]).output.split())


# ---------------------------------------------------- the three argh behaviours


def test_a_commands_return_value_is_printed():
    """MUTATION: in `_printing`, drop the `typer.echo(result)`.

    Every function in `an.tools` returns its report as a string. Typer discards
    return values, so without the wrapper the CLI runs correctly and shows
    NOTHING — the worst possible failure for a tool whose whole output is a
    diagnostic report.
    """

    def says(word: str = "hello") -> str:
        return f"the answer is {word}"

    def other() -> str:
        return "unused"

    result = runner.invoke(build_app([says, other], {}), ["says", "--word", "there"])
    assert result.exit_code == 0, result.output
    assert "the answer is there" in result.output


def test_a_command_that_returns_none_prints_nothing_extra():
    """MUTATION: in `_printing`, `if result is not None` -> `if True`.

    `None` would print as the string "None", which is worse than silence.
    """

    def quiet() -> None:
        return None

    def other() -> str:
        return "x"

    result = runner.invoke(build_app([quiet, other], {}), ["quiet"])
    assert result.exit_code == 0
    # Exactly empty, not merely free of the word "None": `typer.echo(None)`
    # emits a bare newline rather than the string, so asserting on the text
    # would pass with the guard removed. Measured while mutation-testing this.
    assert result.output == "", repr(result.output)


def test_a_nonzero_exit_is_not_swallowed():
    """MUTATION: in `_printing`, wrap the call in `try/except SystemExit: pass`.

    `bench-compare --strict` and `bench-mutants` print and then `sys.exit(1)`;
    that exit code is the entire point of `--strict`, and a wrapper that
    swallowed it would give CI a permanently green gate.
    """

    def fails() -> str:
        print("printed before exiting")
        sys.exit(3)

    def other() -> str:
        return "x"

    result = runner.invoke(build_app([fails, other], {}), ["fails"])
    assert result.exit_code == 3
    assert "printed before exiting" in result.output


def test_a_single_command_app_still_needs_its_subcommand_name():
    """MUTATION: in `build_app`, delete the `@app.callback()`.

    Typer collapses an app with exactly ONE command into a bare invocation, so
    `an init <dir>` would silently become `an <dir>` the day the dispatch list
    is trimmed to one entry. The callback pins group mode regardless.
    """

    def only(value: str = "x") -> str:
        return f"ran with {value}"

    app = build_app([only], {})
    assert _registered(app) == ["only"]
    assert runner.invoke(app, ["only", "--value", "y"]).output.strip() == "ran with y"
    assert runner.invoke(app, ["--help"]).exit_code == 0
    # Group mode: the bare invocation is a usage error, not a silent run.
    assert runner.invoke(app, ["--value", "y"]).exit_code != 0


# ------------------------------------------------------------ flags, for real


def test_flags_keep_their_hyphenated_long_form():
    """MUTATION: none — this pins a user-visible spelling against a future change.

    argh exposed `keep_render` as `--keep-render`; typer does the same, and this
    says so out loud because it is the one thing a reader would want confirmed
    before trusting a documented invocation.
    """
    result = runner.invoke(_app(), ["bench", "--help"])
    assert result.exit_code == 0
    flattened = " ".join(result.output.split())
    for flag in (
        "--scenes",
        "--out",
        "--keep-render",
        "--quiet",
        "--bless",
        "--compare",
    ):
        assert flag in flattened, f"{flag} is gone from `an bench`"


def test_a_real_command_runs_end_to_end(tmp_path):
    """`an init` then `an validate` — the cheapest pair that actually does work.

    MUTATION: in `build_app`, register the raw function instead of `_printing`.

    A `--help` test proves registration; this proves the argument actually
    reaches the function and the result actually reaches the terminal.
    """
    target = tmp_path / "demo"
    created = runner.invoke(_app(), ["init", str(target)])
    assert created.exit_code == 0, created.output
    assert "initialized an project" in created.output
    assert (target / "scene.md").is_file()

    checked = runner.invoke(_app(), ["validate", str(target)])
    assert checked.exit_code == 0, checked.output
    assert "validation:" in checked.output


def test_an_unknown_command_fails_rather_than_doing_something():
    result = runner.invoke(_app(), ["definitely-not-a-command"])
    assert result.exit_code != 0


# ------------------------------------------------------------- the dependency


def test_the_cli_no_longer_imports_argh_or_argcomplete():
    """MUTATION: re-import either in `an/__main__.py`.

    an#45 replaced `argh` (LGPL-3.0) with `typer` (MIT) and dropped
    `argcomplete` with it — argcomplete hooks `argparse` specifically, so once
    the argparse parser was gone it was a dependency that did nothing. An import
    that came back would put the licence question back with it, silently.
    """
    import an.__main__ as entry

    source = __import__("pathlib").Path(entry.__file__).read_text(encoding="utf-8")
    for gone in ("import argh", "import argcomplete", "ArghParser"):
        assert gone not in source, f"{gone!r} is back in the CLI entry point"
    assert "import typer" in source
