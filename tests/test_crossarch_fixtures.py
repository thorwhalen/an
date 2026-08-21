"""Every capture fixture must declare the render path it is supposed to exercise.

an#31. The first run of the cross-architecture experiment measured the wrong
thing on three CI runners and reported a confident answer: `promote_demo`'s
character is a gitignored build product, so on a clean checkout it was absent,
and the compiler falls back to the procedural rig **silently** (an#33). Three
machines agreed perfectly about a picture that was not the picture.

The fix is `Fixture.expect_visual_kinds`, checked against the scene JSON the
browser actually loaded. This file guards the guard: a fixture added without an
expectation would reintroduce exactly that failure, quietly, and the capture
would go green.

`misc/bench/` is not importable as a package, so the module is loaded by path.
"""

import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
CROSSARCH_PATH = REPO_ROOT / "misc" / "bench" / "crossarch.py"


def _load_crossarch():
    spec = importlib.util.spec_from_file_location("_crossarch", CROSSARCH_PATH)
    module = importlib.util.module_from_spec(spec)
    # Registered BEFORE exec: `Fixture` is a slotted dataclass under
    # `from __future__ import annotations`, and dataclasses resolves its string
    # annotations through `sys.modules[cls.__module__]`.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def crossarch():
    assert CROSSARCH_PATH.is_file(), f"the capture tool is missing at {CROSSARCH_PATH}"
    return _load_crossarch()


def test_every_fixture_declares_the_render_path_it_exercises(crossarch):
    undeclared = [
        name
        for name, fixture in crossarch.DFLT_FIXTURES.items()
        if not fixture.expect_visual_kinds
    ]
    assert not undeclared, (
        "these capture fixtures declare no expect_visual_kinds, so a silent "
        "fallback to a different render path would go unnoticed (an#33): "
        f"{undeclared}"
    )


def test_both_render_paths_are_covered(crossarch):
    """A procedural-only capture under-reports by construction.

    The descriptor path is 12x more sensitive to a rasteriser flip, so dropping
    it would leave the sensitive case unmeasured while still reporting a verdict.
    """
    declared = set().union(
        *(f.expect_visual_kinds for f in crossarch.DFLT_FIXTURES.values())
    )
    assert "svg_sprite" in declared, "no fixture exercises the SVG-sprite path"
    assert declared & {"rect", "ellipse"}, "no fixture exercises the procedural rig"


def test_every_fixture_path_exists(crossarch):
    missing = [
        f"{name} -> {fixture.path}"
        for name, fixture in crossarch.DFLT_FIXTURES.items()
        if not (REPO_ROOT / fixture.path).is_dir()
    ]
    assert not missing, (
        f"capture fixtures point at directories that do not exist: {missing}"
    )
