"""The doctest lane: armed, and kept in sync with what CI actually passes.

`CLAUDE.md` told readers that doctests cover the public API. Nothing ran them
(an#61): `testpaths = ["tests"]` scoped CI's `--doctest-modules` away from the
package, so every `>>>` under `an/` was prose that happened to be syntactically
checkable.

Two things can silently un-arm it again, so both are guarded here.
"""

from __future__ import annotations

import ast
import sys
import tomllib
from pathlib import Path

PYPROJECT = Path(__file__).resolve().parents[1] / "pyproject.toml"

#: The flags the wads CI action passes via `-o`, which OVERRIDES the ini key.
#: Sourced from `i2mint/wads`'s run-tests action, not from this repo.
CI_DOCTEST_OPTIONFLAGS: frozenset[str] = frozenset(
    {"ELLIPSIS", "IGNORE_EXCEPTION_DETAIL"}
)


def _pytest_config() -> dict:
    with PYPROJECT.open("rb") as f:
        return tomllib.load(f)["tool"]["pytest"]["ini_options"]


def test_the_package_is_in_testpaths_so_ci_collects_its_doctests():
    """Dropping `an` here disarms 120 doctests without failing anything."""
    assert "an" in _pytest_config()["testpaths"]


def test_local_doctest_flags_match_what_ci_passes():
    """The trap this closes.

    CI replaces this key wholesale with `-o`. Any flag here that CI does not
    pass is a flag a doctest may come to rely on — and it would pass locally
    and fail in CI, which is the worst way to learn about it.
    `NORMALIZE_WHITESPACE` was here and is the reason this test exists.
    """
    local = frozenset(_pytest_config()["doctest_optionflags"])
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
    with PYPROJECT.open("rb") as f:
        deps = tomllib.load(f)["project"]["dependencies"]
    return {d.split(">")[0].split("=")[0].split("[")[0].strip().lower() for d in deps}


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
