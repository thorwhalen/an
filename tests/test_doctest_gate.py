"""The doctest lane: armed, and kept in sync with what CI actually passes.

`CLAUDE.md` told readers that doctests cover the public API. Nothing ran them
(an#61): `testpaths = ["tests"]` scoped CI's `--doctest-modules` away from the
package, so every `>>>` under `an/` was prose that happened to be syntactically
checkable.

Two things can silently un-arm it again, so both are guarded here.
"""

from __future__ import annotations

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
