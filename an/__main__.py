# PYTHON_ARGCOMPLETE_OK
"""an CLI entry point.

Subcommands:

    an init <dir>         — create a fresh project
    an validate <dir>     — schema + semantic validation
    an sync <dir>         — reconcile scene.md ↔ ir/scene.json
    an check              — diagnose backend system deps
"""

from __future__ import annotations

import argh

from an.tools import _dispatch_funcs, _dispatch_namespaces


def _dispatch_with_completion(funcs, namespaces=None):
    parser = argh.ArghParser()
    parser.add_commands(funcs)
    if namespaces:
        for ns, ns_funcs in namespaces.items():
            parser.add_commands(ns_funcs, group_name=ns)
    try:
        import argcomplete

        argcomplete.autocomplete(parser)
    except ImportError:
        pass
    parser.dispatch()


def main() -> None:
    """Dispatch the CLI."""
    _dispatch_with_completion(_dispatch_funcs, _dispatch_namespaces)


if __name__ == "__main__":
    main()
