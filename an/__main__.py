"""an CLI entry point — a thin dispatcher over :data:`an.tools._dispatch_funcs`.

Subcommands:

    an init <dir>         — create a fresh project
    an validate <dir>     — schema + semantic validation
    an sync <dir>         — reconcile scene.md ↔ ir/scene.json
    an check              — diagnose backend system deps

**Typer is wired programmatically, never as decorators on the functions
themselves.** That is the "dispatch to interface" pillar: plain Python functions
are the business logic and the CLI is a projection of them, so `an.tools`'s
functions stay importable, testable and callable with no CLI framework in their
signatures. Decorating them at the definition site would put `click` types in
the business layer, which is the shape this package exists not to have.

**Typer replaced argh, and the reason is licensing rather than ergonomics**
(an#45). `argh` declares LGPL-3.0 and was the only declared-copyleft
distribution in `an`'s hard dependency set, against the MIT/BSD/Apache/ISC rule
this repo and its federation state. Typer is MIT.
`argcomplete` went with it: it hooks `argparse` specifically, so once the
argparse-based parser was gone it was a dependency that did nothing. Typer
supplies the replacement itself (``typer/completion.py``), reachable as
``an --install-completion``; verified working under a pty, emitting a correct
``#compdef an`` block.

**What typer pulls in depends on which typer resolves, and `pyproject.toml`
pins no version.** 0.19.x requires `click` (BSD-3); 0.27.1 — what a fresh
`pip install an` gets today — requires `shellingham`, `rich` and
`annotated-doc`, and **no click at all**. Earlier text here said flatly that
typer "pulls click" and that click supplied the completion; both were true of
the locally installed version and false of the package. The licence perimeter
now walks the installed transitive closure rather than the five declared names,
and says out loud that its answer is about the environment it runs in.

Three behaviours argh gave for free that are reproduced here on purpose, because
each one is user-visible and none is typer's default:

1. **A command's name is hyphenated.** ``bench_compare`` is ``an
   bench-compare``, as it was under argh. Typer would otherwise expose the
   underscored name and every documented invocation would break.
2. **A command's return value is printed.** Every function in `an.tools`
   returns its report as a string and argh printed it; typer discards return
   values, so the wrapper prints it. Without this the CLI runs correctly and
   shows nothing, which is the worst possible failure for a diagnostic tool.
3. **A function that exits nonzero still exits nonzero.** ``bench-compare
   --strict`` and ``bench-mutants`` call ``sys.exit(1)`` after printing; the
   wrapper must not swallow ``SystemExit``.
"""

from __future__ import annotations

import inspect
import re
from typing import Any, Callable

import typer

from an.ir.migrate import DocumentMigrationError
from an.ir.sync import SceneMarkdownError, SceneValidationError
from an.tools import _dispatch_funcs, _dispatch_namespaces


def command_name(func: Callable[..., Any]) -> str:
    """The CLI name for a dispatched function: underscores become hyphens.

    Matches what argh did, so no documented invocation changes.

    >>> def bench_compare(): ...
    >>> command_name(bench_compare)
    'bench-compare'
    """
    return func.__name__.replace("_", "-")


#: A docstring line that documents one parameter, in the convention
#: `an.tools` and `an/characters/cli.py` both already follow.
_PARAM_DOC = re.compile(r"^[a-z_][a-z0-9_]*: ")


def help_text(func: Callable[..., Any]) -> str | None:
    """The docstring, with its per-parameter lines protected from rewrapping.

    argh kept one line per flag; click and Rich rewrap a paragraph, so the six
    lines documenting `an bench`'s flags arrived as a single run-on — "scenes:
    ... (default: all of them) out: ledger path ... keep_render: keep the
    throwaway ...". That is the entire per-flag documentation of this CLI, and
    losing it was a silent regression that a first-four-words help assertion
    could not see.

    Click's escape for this is a line containing only ``\b`` before a block it
    must not rewrap, so one is inserted ahead of each run of parameter lines.
    Cheaper and less fragile than rebuilding every function's signature to
    attach `typer.Option(help=...)` per parameter, which is the nicer shape and
    would put CLI types where the dispatch pillar says they must not go.
    """
    doc = inspect.getdoc(func)
    if not doc:
        return None
    out: list[str] = []
    inside = False
    for line in doc.splitlines():
        documents_a_param = bool(_PARAM_DOC.match(line))
        if documents_a_param and not inside:
            out.append("\b")
        inside = documents_a_param
        out.append(line)
    return "\n".join(out)


#: Errors whose repair is a human editing their own file. The CLI prints these
#: as a sentence and exits 1; everything else keeps its traceback, because a
#: traceback is the right output for a bug and the wrong output for a typo.
_REFUSALS: tuple[type[BaseException], ...] = (
    DocumentMigrationError,
    SceneMarkdownError,
    SceneValidationError,
)


def _printing(func: Callable[..., Any]) -> Callable[..., Any]:
    """Wrap ``func`` so its return value reaches the terminal.

    Typer ignores what a command returns. Every function in `an.tools` returns
    its report as a string, so without this the CLI would run correctly and
    print nothing.

    ``functools.wraps`` is load-bearing rather than tidy: ``inspect.signature``
    follows ``__wrapped__``, and that signature IS the command line — typer
    reads it to derive every argument and flag. Without it typer sees
    ``(*args, **kwargs)`` and, measured, does not merely drop the flags: it
    raises ``RuntimeError: Type not yet supported: typing.Any`` inside
    ``typer.main.get_group``, before any command runs. The whole CLI dies,
    ``--help`` included. An earlier note here said "``--help`` still renders",
    which understated it (an#45 review).
    """
    import functools

    @functools.wraps(func)
    def run(*args: Any, **kwargs: Any) -> None:
        try:
            result = func(*args, **kwargs)
        except _REFUSALS as e:
            # A message for a human, and still a failure. These three types are
            # the ones whose repair is "edit one line of your own file", so a
            # traceback would bury the sentence that says which line. Anything
            # else keeps its traceback: an#106's first pass caught bare
            # `ValueError` here-ish (in `an.tools`) and swallowed
            # `json.JSONDecodeError` and `CutoutCompileError` with it, so a
            # corrupt `ir/scene.json` and a failed render both printed one
            # nameless line and exited 0.
            typer.echo(str(e))
            raise typer.Exit(1) from e
        if result is not None:
            typer.echo(result)

    return run


def build_app(
    funcs: list[Callable[..., Any]] | None = None,
    namespaces: dict[str, list[Callable[..., Any]]] | None = None,
) -> typer.Typer:
    """Project the dispatch lists onto a typer app.

    Takes the lists as arguments rather than reading the module globals so the
    projection is testable against a small set — the CLI surface is 17 commands
    and had no test at all before an#45.
    """
    app = typer.Typer(
        add_completion=True,
        no_args_is_help=True,
        pretty_exceptions_show_locals=False,
        help=__doc__.split("\n\n")[0],
        # argh accepted `-h` everywhere and click does not by default, so
        # `an render -h` exited 2 with "No such option". Restored at the root;
        # it propagates to every command, group and nested group.
        context_settings={"help_option_names": ["-h", "--help"]},
    )

    @app.callback()
    def _group() -> None:
        """Force subcommand mode.

        Typer collapses an app with exactly ONE command into a bare invocation
        with no subcommand name — so `an init <dir>` would silently become
        `an <dir>` the day the dispatch list is trimmed to one entry. A callback
        pins group mode regardless of how many commands are registered, which
        also makes `build_app([one_func])` behave like the real CLI in a test.
        """

    for func in funcs if funcs is not None else _dispatch_funcs:
        app.command(name=command_name(func), help=help_text(func))(_printing(func))
    for group, group_funcs in (
        namespaces if namespaces is not None else _dispatch_namespaces
    ).items():
        sub = typer.Typer(no_args_is_help=True, help=f"{group} subcommands")
        for func in group_funcs:
            # No group-prefix stripping: argh mounted the group and used the
            # function names as they are, and `an/characters/cli.py` names them
            # bare (`new`, `mouths`, ...) accordingly. A strip here was
            # defensive code with no live case — it survived its own mutation
            # test, which is how it was found.
            sub.command(name=command_name(func), help=help_text(func))(_printing(func))
        app.add_typer(sub, name=group)
    return app


def main() -> None:
    """Dispatch the CLI."""
    build_app()()


if __name__ == "__main__":
    main()
