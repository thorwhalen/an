"""The doctest lane: armed, and kept in sync with what CI actually passes.

`CLAUDE.md` told readers that doctests cover the public API. Nothing ran them
(an#61): `testpaths = ["tests"]` scoped CI's `--doctest-modules` away from the
package, so every `>>>` under `an/` was prose that happened to be syntactically
checkable.

Two things can silently un-arm it again, so both are guarded here.
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

PYPROJECT = Path(__file__).resolve().parents[1] / "pyproject.toml"

#: The flags the wads CI action passes via `-o`, which OVERRIDES the ini key.
#: Sourced from `i2mint/wads`'s run-tests action, not from this repo.
CI_DOCTEST_OPTIONFLAGS: frozenset[str] = frozenset(
    {"ELLIPSIS", "IGNORE_EXCEPTION_DETAIL"}
)


def _toml_list(section: str, key: str) -> list[str]:
    """Read a list-valued key out of one `pyproject.toml` section, by text.

    Deliberately not `tomllib`: it is stdlib only from 3.11 and CI runs 3.10.
    Deliberately not `pytestconfig.getini` either — CI **overrides**
    `doctest_optionflags` with `-o`, so the resolved value under CI is CI's own
    and comparing it against itself would prove nothing. What this guard is
    about is what the *file* declares.

    And deliberately not `importlib.metadata.requires`: an editable install's
    recorded metadata goes stale (this one still lists `argh`, removed in
    an#45, and omits `typer`), so it answers a question about the last install
    rather than about the source.

    Asserts rather than returning empty on a miss — a silently empty list here
    would make every test below vacuously pass.
    """
    text = PYPROJECT.read_text(encoding="utf-8")
    parts = text.split(f"[{section}]", 1)
    assert len(parts) == 2, f"no [{section}] section in pyproject.toml"
    body = re.split(r"\n\[", parts[1], maxsplit=1)[0]
    match = re.search(rf"^{re.escape(key)}\s*=\s*\[(.*?)^\]", body, re.S | re.M)
    assert match, f"{key} not found as a multi-line list in [{section}]"
    return re.findall(r'"([^"]+)"', match.group(1))


def test_the_package_is_in_testpaths_so_ci_collects_its_doctests():
    """Dropping `an` here disarms 120 doctests without failing anything."""
    assert "an" in _toml_list("tool.pytest.ini_options", "testpaths")


def test_local_doctest_flags_match_what_ci_passes():
    """The trap this closes.

    CI replaces this key wholesale with `-o`. Any flag here that CI does not
    pass is a flag a doctest may come to rely on — and it would pass locally
    and fail in CI, which is the worst way to learn about it.
    `NORMALIZE_WHITESPACE` was here and is the reason this test exists.
    """
    local = frozenset(_toml_list("tool.pytest.ini_options", "doctest_optionflags"))
    assert local == CI_DOCTEST_OPTIONFLAGS, (
        f"local doctest flags {sorted(local)} disagree with CI's "
        f"{sorted(CI_DOCTEST_OPTIONFLAGS)}. A flag CI does not pass makes a "
        f"doctest that relies on it pass here and fail there."
    )


# ---------------------------------------------------------------------------
# What `--doctest-modules` can trip over: imports, not doctests
# ---------------------------------------------------------------------------

AN = PYPROJECT.parent / "an"

#: Import name -> distribution name, where they differ among our declared deps.
_IMPORT_TO_DIST: dict[str, str] = {"yaml": "pyyaml"}

#: The one module allowed to import an undeclared dependency at module level.
#: `an.genre` declares this package's genre to `nw` and is opt-in by design:
#: `an/__init__.py` never imports it, so `import an` stays nw-free.
KNOWN_OPTIONAL_MODULE_IMPORTS: dict[str, str] = {"an/genre.py": "nw"}


def _declared_distributions() -> set[str]:
    """The distribution names `[project] dependencies` declares."""
    return {
        re.split(r"[<>=!;\[ ]", spec, maxsplit=1)[0].strip().lower()
        for spec in _toml_list("project", "dependencies")
        if spec.strip()
    }


def _module_level_third_party_imports() -> dict[str, set[str]]:
    """`{relative path: {top-level module}}` for imports at module scope only.

    Module scope is the whole point: an import inside a function is lazy and
    cannot break collection, which is the repo's convention for optional deps.
    """
    declared = _declared_distributions()
    found: dict[str, set[str]] = {}
    for path in sorted(AN.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in tree.body:  # module level only, deliberately
            if isinstance(node, ast.Import):
                names = [a.name.split(".")[0] for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                names = [node.module.split(".")[0]]
            else:
                continue
            for name in names:
                if name in sys.stdlib_module_names or name in ("__future__", "an"):
                    continue
                if _IMPORT_TO_DIST.get(name, name).lower() in declared:
                    continue
                rel = str(path.relative_to(PYPROJECT.parent))
                found.setdefault(rel, set()).add(name)
    return found


def test_only_the_known_module_imports_an_undeclared_dependency():
    """The guard that would have caught an#61's CI failure before CI did.

    `--doctest-modules` imports every module it scans and CI installs no
    optional extras, so a module-level import of anything undeclared breaks
    **collection** — not one test, the whole run. Keep optional imports inside
    the function that needs them; if a module genuinely must import one at
    module scope, add it here and to `an/conftest.py` together.
    """
    found = {
        path: sorted(mods) for path, mods in _module_level_third_party_imports().items()
    }
    expected = {path: [mod] for path, mod in KNOWN_OPTIONAL_MODULE_IMPORTS.items()}
    assert found == expected


def test_the_conftest_skips_exactly_those_modules_when_the_dep_is_absent():
    """The declaration and the collection rule must not drift apart."""
    conftest = (AN / "conftest.py").read_text(encoding="utf-8")
    for path in KNOWN_OPTIONAL_MODULE_IMPORTS:
        assert Path(path).name in conftest, f"{path} not handled in an/conftest.py"


def test_the_excluded_module_carries_no_doctests():
    """Skipping it costs nothing today. If this fails, the skip started hiding
    real coverage and the module needs a lazy import instead."""
    for path in KNOWN_OPTIONAL_MODULE_IMPORTS:
        assert ">>>" not in (PYPROJECT.parent / path).read_text(encoding="utf-8")
