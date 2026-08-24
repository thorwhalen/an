"""The artist-facing contract, and what happens when art is missing (an#76/#78).

Two halves of one boundary:

- **#76, at compile time.** A slot whose art is not on disk is recorded as a
  fallback, so it is audible by default and fatal under `strict_assets` — the
  treatment a missing *character* already got, now reaching inside the
  descriptor to the individual part.
- **#78, offline.** `validate_character` opens each part and reports typed
  `Finding`s, so a problem routes the way every other verifier's does.

The split is deliberate. A geometry check cannot live on the render path: the
bbox scraper under-covers real SVG (no transforms, no `<polygon>`), so a false
"draws nothing" would refuse a valid render. As a `Finding` a false positive is
a sentence a human reads.
"""

from __future__ import annotations

import json
import shutil
import warnings
from pathlib import Path

import pytest

from an.adapters.cutout.compile import CutoutCompileError, compile_shot
from an.characters.validate import (
    DRAWABLE_ELEMENTS,
    PROHIBITED_ELEMENTS,
    render_contract,
    validate_character,
)
from an.ir.schema import Shot

CORPUS = Path(__file__).resolve().parents[1] / "misc" / "bench" / "corpus"
RIG = CORPUS / "saturated_outline" / "assets" / "characters"
FIXTURES = Path(__file__).parent / "fixtures" / "art"


class _Store(dict):
    def __init__(self, mapping, root: Path):
        super().__init__(mapping)
        self._root = root


def _shot() -> Shot:
    return Shot(
        id="s1",
        duration=1.0,
        entities=[
            {"id": "charlie", "kind": "character", "store": "characters", "ref": "rig"}
        ],
    )


@pytest.fixture
def rig(tmp_path):
    """A copy of a committed descriptor rig, safe to mutilate."""
    root = tmp_path / "characters"
    shutil.copytree(RIG / "saturated-rig", root / "rig")
    desc = json.loads((root / "rig" / "character.json").read_text(encoding="utf-8"))
    return root, {"characters": _Store({"rig": desc}, root)}


# ---------------------------------------------------------------------------
# #76 — a declared part with no art
# ---------------------------------------------------------------------------


def test_a_missing_part_is_audible(rig):
    """It used to be silent. Measured in the research: `strict_assets` compiled
    a descriptor with a deleted head.svg with ZERO diagnostics."""
    root, mall = rig
    (root / "rig" / "parts" / "head.svg").unlink()
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        compile_shot(_shot(), mall)
    assert len(caught) == 1
    message = str(caught[0].message)
    assert "head" in message
    assert "parts/head.svg" in message


def test_a_missing_part_is_fatal_under_strict_assets(rig):
    """`strict_assets` exists for callers measuring pixels, and a hole in the
    picture is exactly the case where a plausible-looking render is wrong."""
    root, mall = rig
    (root / "rig" / "parts" / "torso.svg").unlink()
    with pytest.raises(CutoutCompileError, match="torso"):
        compile_shot(_shot(), mall, strict_assets=True)


def test_a_slot_that_still_draws_is_not_a_fallback(rig):
    """THE distinction that keeps this usable.

    A rig shipping open eyes but no closed ones is incomplete, not broken — the
    frame is right. Treating a partial slot as a fallback would make every rig
    without a blink refuse to render under `strict_assets`, and both committed
    corpus rigs are in exactly that state.
    """
    _, mall = rig  # saturated-rig ships no eye_*_closed
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        compile_shot(_shot(), mall, strict_assets=True)
    assert caught == []


def test_the_missing_part_names_the_slot_the_attachment_and_the_path(rig):
    """ "Texture not found" costs a debugging session."""
    root, mall = rig
    (root / "rig" / "parts" / "arm_l.svg").unlink()
    with pytest.raises(CutoutCompileError) as excinfo:
        compile_shot(_shot(), mall, strict_assets=True)
    message = str(excinfo.value)
    assert "charlie/arm_l" in message
    assert "'arm_l'" in message
    assert "parts/arm_l.svg" in message


# ---------------------------------------------------------------------------
# #78 — the offline contract
# ---------------------------------------------------------------------------


def test_a_part_that_draws_nothing_is_reported(tmp_path):
    """The regression fixture. Valid SVG, correctly sized, zero ink.

    This is the failure with no diagnostic anywhere in the pipeline: it renders
    invisibly, and no size or emptiness heuristic catches it because the file
    has a title and a gradient in it.
    """
    parts = tmp_path / "parts"
    parts.mkdir()
    shutil.copy(FIXTURES / "invisible_head.svg", parts / "head.svg")
    report = validate_character(tmp_path, name="ghost")
    blank = [f for f in report.findings if "no drawable element" in f.description]
    assert len(blank) == 1
    assert blank[0].ir_path == "parts/head.svg"
    assert blank[0].severity == "error"
    assert not report.passed


def test_findings_are_the_shared_type_so_error_routing_applies(tmp_path):
    """It returned a bespoke `ValidationReport` before, so the orchestrator's
    routing did not apply to any character problem."""
    from an.verify._base import Finding, VerificationReport

    report = validate_character(tmp_path / "nope", name="absent")
    assert isinstance(report, VerificationReport)
    assert all(isinstance(f, Finding) for f in report.findings)
    assert all(f.ir_path for f in report.findings), (
        "a finding with no path routes nowhere"
    )


@pytest.mark.parametrize("element", sorted(PROHIBITED_ELEMENTS))
def test_each_prohibited_element_is_reported(tmp_path, element):
    parts = tmp_path / "parts"
    parts.mkdir()
    (parts / "head.svg").write_text(
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10" '
        f'width="10" height="10"><rect width="5" height="5"/>'
        f"<{element}/></svg>",
        encoding="utf-8",
    )
    report = validate_character(tmp_path, name="x")
    assert any(f"<{element}>" in f.description for f in report.findings)


def test_a_letterboxed_part_is_reported(tmp_path):
    """The an#75 defect, reintroducible by hand even though `extract_part` no
    longer emits it."""
    parts = tmp_path / "parts"
    parts.mkdir()
    (parts / "arm_l.svg").write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 60 276" '
        'width="1024" height="1024"><rect width="60" height="276"/></svg>',
        encoding="utf-8",
    )
    report = validate_character(tmp_path, name="x")
    assert any("letterboxed" in f.description for f in report.findings)


def test_a_malformed_part_is_reported_rather_than_crashing_the_validator(tmp_path):
    parts = tmp_path / "parts"
    parts.mkdir()
    (parts / "head.svg").write_text("<svg><rect", encoding="utf-8")
    report = validate_character(tmp_path, name="x")
    assert any("not parseable" in f.description for f in report.findings)


def test_a_good_part_produces_no_finding_about_itself(tmp_path):
    """The check must not cry wolf, or it stops being read."""
    parts = tmp_path / "parts"
    parts.mkdir()
    (parts / "head.svg").write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 240 276" '
        'width="240" height="276"><path d="M0 0 L240 276"/></svg>',
        encoding="utf-8",
    )
    report = validate_character(tmp_path, name="x")
    assert not [f for f in report.findings if f.ir_path == "parts/head.svg"]


# ---------------------------------------------------------------------------
# The contract is generated, so it cannot drift from the checker
# ---------------------------------------------------------------------------


def test_the_contract_names_every_rule_the_validator_enforces():
    """A hand-written contract is one that gets an illustrator paid for work
    that cannot land. This is the test that keeps it derived."""
    from an.characters.schema import MOUTH_SHAPES, REQUIRED_PARTS

    text = render_contract()
    for part in REQUIRED_PARTS:
        assert part in text, f"contract omits required part {part}"
    for shape in MOUTH_SHAPES:
        assert f"mouth_{shape}" in text
    for element in PROHIBITED_ELEMENTS:
        assert f"<{element}>" in text
    for element in DRAWABLE_ELEMENTS:
        assert element in text


def test_the_contract_reports_the_schema_version_it_describes():
    from an.characters.schema import CHARACTER_SCHEMA_VERSION

    assert CHARACTER_SCHEMA_VERSION in render_contract()


def test_the_contract_lists_the_joints_bones_actually_read():
    """The pivot names are the artist's half of `bones_from_pivots`. If the
    contract named a joint no bone reads, an illustrator would draw it for
    nothing."""
    from an.characters.schema import CharacterDescriptor

    text = render_contract()
    for bone in CharacterDescriptor(name="x").bones:
        if bone.pivot:
            assert bone.pivot in text
