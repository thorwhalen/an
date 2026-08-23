"""The rig contract's instrument, and the violation it currently records.

Wave 4 (#9) asserts that **aspect ratio is intrinsic to the art and the
compiler may never override it**. This module measures whether that holds, and
today it does not: the compiler sizes every sprite from module constants, so a
part's shape is decided by its box rather than by its art.

These tests deliberately **record the violation as numbers** rather than assert
the invariant. That is the point of landing them before the fix: the ratios
below are the before-half of the wave's evidence, measured on committed art with
no browser and no render, so the after-half is a diff rather than a claim.

**When #73/#74 land, these tests fail.** That is the success signal. Replace the
recorded tables with :func:`assert_uniform`, which is written and unused.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from an.adapters.cutout.compile import compile_shot
from an.adapters.cutout.fidelity import (
    DFLT_ASPECT_TOLERANCE,
    aspect_findings,
    part_fidelity,
)
from an.ir.schema import Shot

CORPUS = Path(__file__).resolve().parents[1] / "misc" / "bench" / "corpus"

#: The two corpus fixtures whose characters travel the descriptor path.
DESCRIPTOR_RIGS: tuple[tuple[str, str], ...] = (
    ("saturated_outline", "saturated-rig"),
    ("graded_field", "graded-field-rig"),
)

#: Measured 2026-08-23 on the committed rigs, at 2db25ce. `max(sx,sy)/min(sx,sy)`
#: per node; 1.0 means the art's shape survives. Every entry above 1.0 is a
#: sprite whose art the compiler reshapes.
RECORDED_DISTORTION: dict[str, dict[str, float]] = {
    "saturated_outline": {
        "root/charlie/leg_l": 1.005,
        "root/charlie/leg_r": 1.005,
        "root/charlie/torso": 1.015,
        "root/charlie/arm_l": 1.179,
        "root/charlie/arm_r": 1.179,
        "root/charlie/head": 1.000,
        "root/charlie/head/left_eye": 1.500,
        "root/charlie/head/right_eye": 1.500,
        "root/charlie/head/left_brow": 3.000,
        "root/charlie/head/right_brow": 3.000,
        "root/charlie/head/mouth": 2.000,
    },
    "graded_field": {
        "root/charlie/leg_l": 3.158,
        "root/charlie/leg_r": 3.158,
        "root/charlie/torso": 1.182,
        "root/charlie/arm_l": 3.929,
        "root/charlie/arm_r": 3.929,
        "root/charlie/head": 1.000,
        "root/charlie/head/left_eye": 1.500,
        "root/charlie/head/right_eye": 1.500,
        "root/charlie/head/left_brow": 3.000,
        "root/charlie/head/right_brow": 3.000,
        "root/charlie/head/mouth": 2.000,
    },
}


def _measure(fixture: str, ref: str):
    root = CORPUS / fixture / "assets" / "characters"
    desc = json.loads((root / ref / "character.json").read_text(encoding="utf-8"))
    scene = compile_shot(
        Shot(
            id="s1",
            duration=1.0,
            entities=[
                {
                    "id": "charlie",
                    "kind": "character",
                    "store": "characters",
                    "ref": ref,
                }
            ],
        ),
        {"characters": {ref: desc}},
    )
    return scene, part_fidelity(scene, asset_root=root.parent)


def assert_uniform(parts) -> None:
    """The assertion Wave 4 is for. Unused until #73/#74 land — then it replaces
    :data:`RECORDED_DISTORTION` and the recorded tables are deleted."""
    offenders = [
        f"{p.node_path} {p.aspect_distortion:.3f}x" for p in parts if not p.is_uniform()
    ]
    assert not offenders, "sprites scaled non-uniformly: " + ", ".join(offenders)


@pytest.mark.parametrize("fixture,ref", DESCRIPTOR_RIGS)
def test_descriptor_sprite_distortion_matches_the_recorded_measurement(fixture, ref):
    """Pin today's distortion per node, so the fix shows up as a diff.

    Failing here means one of two things: the compiler's boxes changed (the fix —
    update the table to 1.000 and switch to `assert_uniform`), or a corpus rig's
    art changed shape (re-measure and say so in the bless reason).
    """
    _, parts = _measure(fixture, ref)
    measured = {p.node_path: round(p.aspect_distortion, 3) for p in parts}
    assert measured == RECORDED_DISTORTION[fixture]


@pytest.mark.parametrize("fixture,ref", DESCRIPTOR_RIGS)
def test_only_the_head_survives_and_only_because_it_is_square(fixture, ref):
    """The one uniform part is uniform by coincidence, not by design.

    This is the trap a spot-check falls into: `head` looks correct, so the rig
    looks correct. It is square art in a square box — nothing preserved its
    aspect, the box simply happened to agree.
    """
    _, parts = _measure(fixture, ref)
    uniform = [p for p in parts if p.is_uniform()]
    assert [p.node_path for p in uniform] == ["root/charlie/head"]
    (head,) = uniform
    assert head.box[0] == head.box[1], "head's box is square"
    assert head.raster[0] == head.raster[1], "head's art is square"


@pytest.mark.parametrize("fixture,ref", DESCRIPTOR_RIGS)
def test_the_violation_is_reported_as_typed_findings(fixture, ref):
    """`aspect_findings` routes each offender to its own scene-graph node.

    The orchestrator dispatches a fix by `ir_path`, so a per-part path is what
    makes this actionable rather than merely true.
    """
    scene, parts = _measure(fixture, ref)
    root = CORPUS / fixture / "assets"
    findings = aspect_findings(scene, asset_root=root)
    assert len(findings) == sum(1 for p in parts if not p.is_uniform())
    assert {f.ir_path for f in findings} == {
        p.node_path for p in parts if not p.is_uniform()
    }
    assert all(f.severity == "error" for f in findings)
    assert all("intrinsic to the art" in (f.suggested_fix or "") for f in findings)


def test_the_procedural_path_has_no_sprites_to_distort():
    """Scope check: this instrument measures the descriptor path only.

    `single_character` builds from `rect`/`ellipse` primitives, which carry no
    source art and so cannot disagree with it. If this ever finds a sprite, the
    procedural path has grown one and the instrument's scope claim is stale.
    """
    ref = "probe-rig"
    root = CORPUS / "aa_probe" / "assets" / "characters"
    desc = json.loads((root / ref / "character.json").read_text(encoding="utf-8"))
    scene = compile_shot(
        Shot(
            id="s1",
            duration=1.0,
            entities=[
                {"id": "c", "kind": "character", "store": "characters", "ref": ref}
            ],
        ),
        {"characters": {ref: desc}},
    )
    assert part_fidelity(scene, asset_root=root.parent) == []


def test_the_tolerance_guards_float_noise_and_nothing_more():
    """A tolerance loose enough to admit a real distortion would retire the test.

    1.005 is the smallest violation the corpus actually contains (`saturated`'s
    legs), so the tolerance must sit well below it or that row goes silent.
    """
    assert DFLT_ASPECT_TOLERANCE < 1.005
    assert DFLT_ASPECT_TOLERANCE > 1.0
