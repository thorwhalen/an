"""The rig contract's instrument, and the violation it currently records.

Wave 4 (#9) asserts that **aspect ratio is intrinsic to the art and the
compiler may never override it**. This module measures whether that holds, and
today it does not: the compiler sizes every sprite from module constants, so a
part's shape is decided by its box rather than by its art.

These tests recorded the violation as numbers before the fix, so the after-half
would be a diff rather than a claim. **#73/#74 have landed and every ratio moved
to 1.000**, so they assert the invariant directly now.

The before-numbers stay below as :data:`RECORDED_DISTORTION_BEFORE` — not to
assert against, but because "10 of 11 sprites distorted on both rigs, worst
3.929x" is the measurement that justified rewriting the largest function in
`compile.py`. Deleting it would leave these tests true but unexplained.
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

#: Measured 2026-08-23 at 2db25ce, **before** the rig contract landed.
#: `max(sx,sy)/min(sx,sy)` per node; 1.0 means the art's shape survives.
#: Evidence, not an assertion.
RECORDED_DISTORTION_BEFORE: dict[str, dict[str, float]] = {
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


class _CharacterStore(dict):
    """A mall store: dict-like for the compiler, `_root` for part sizing.

    The real render path's stores are dol filesystem stores and carry `_root`.
    A plain dict here would make the compiler unable to read any part's size,
    so every box would fall back to the default and the measurement would be
    about the fallback rather than about the rig.
    """

    def __init__(self, mapping, root: Path):
        super().__init__(mapping)
        self._root = root


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
        {"characters": _CharacterStore({ref: desc}, root)},
    )
    return scene, part_fidelity(scene, asset_root=root.parent)


def assert_uniform(parts) -> None:
    """The invariant: a part is placed and uniformly scaled, never stretched."""
    offenders = [
        f"{p.node_path} {p.aspect_distortion:.3f}x" for p in parts if not p.is_uniform()
    ]
    assert not offenders, "sprites scaled non-uniformly: " + ", ".join(offenders)


@pytest.mark.parametrize("fixture,ref", DESCRIPTOR_RIGS)
def test_every_descriptor_sprite_keeps_the_shape_its_art_was_drawn_with(fixture, ref):
    """THE invariant of Wave 4 (#74).

    Aspect ratio is intrinsic to the art and the compiler may never override it.
    Before the rewrite this failed on 10 of 11 sprites on both rigs, worst
    3.929x — see :data:`RECORDED_DISTORTION_BEFORE`.
    """
    _, parts = _measure(fixture, ref)
    assert parts, "no sprites measured — the fixture stopped exercising the path"
    assert_uniform(parts)


@pytest.mark.parametrize("fixture,ref", DESCRIPTOR_RIGS)
def test_the_head_is_no_longer_the_only_part_that_survives(fixture, ref):
    """Before the rewrite exactly one part was uniform, and only by coincidence
    — square art that happened to meet a square box. That coincidence is what
    let a spot-check conclude the rig was fine. Now every part is uniform by
    rule; if the head is ever the only one again, the rule is gone."""
    _, parts = _measure(fixture, ref)
    uniform = [p.node_path for p in parts if p.is_uniform()]
    assert len(uniform) == len(parts)
    assert uniform != ["root/charlie/head"]


@pytest.mark.parametrize("fixture,ref", DESCRIPTOR_RIGS)
def test_every_sprite_declares_the_contain_fit(fixture, ref):
    """What actually enforces the invariant, and the half a geometry check
    cannot see.

    Under `contain` the runtime scales both axes by one factor, so the box no
    longer decides the art's shape — which is exactly why
    `aspect_distortion` returns 1.0 for it, and why that would be vacuous
    without this. The other half, that the runtime honours the policy, needs a
    browser and lives in `test_uniform_fit_browser.py`.
    """
    _, parts = _measure(fixture, ref)
    assert parts
    assert {p.fit for p in parts} == {"contain"}


@pytest.mark.parametrize("fixture,ref", DESCRIPTOR_RIGS)
def test_each_box_is_sized_from_the_art_not_from_a_constant(fixture, ref):
    """The stronger, fit-independent claim: the box agrees with the art's shape.

    `contain` guarantees the art is never reshaped, but it would also hide a
    compiler that sized every part 345x345 and let the runtime letterbox it.
    Box-vs-raster agreement is what says the boxes come from the descriptor.
    """
    _, parts = _measure(fixture, ref)
    off = [
        f"{p.node_path} {p.box_aspect_disagreement:.3f}x"
        for p in parts
        if p.box_aspect_disagreement > DFLT_ASPECT_TOLERANCE
    ]
    assert not off, "boxes disagree with their art's shape: " + ", ".join(off)


@pytest.mark.parametrize("fixture,ref", DESCRIPTOR_RIGS)
def test_the_before_record_still_describes_this_rig(fixture, ref):
    """Guards the evidence, not the behaviour. Every part the before-table names
    must still exist, or the wave's headline number stops being verifiable."""
    _, parts = _measure(fixture, ref)
    assert {p.node_path for p in parts} == set(RECORDED_DISTORTION_BEFORE[fixture])
    assert max(RECORDED_DISTORTION_BEFORE[fixture].values()) > 1.0


@pytest.mark.parametrize("fixture,ref", DESCRIPTOR_RIGS)
def test_no_sprite_is_reported_as_a_finding_any_more(fixture, ref):
    """`aspect_findings` reported 10 offenders per rig; it must now report none.

    Kept rather than deleted: it is the same call the orchestrator would make,
    so it proves the routing surface agrees with the geometry one.
    """
    scene, _ = _measure(fixture, ref)
    root = CORPUS / fixture / "assets"
    assert aspect_findings(scene, asset_root=root) == []


def test_the_procedural_path_has_no_sprites_to_distort():
    """Scope check: this instrument measures the descriptor path only.

    `aa_probe` builds from `rect` primitives, which carry no source art and so
    cannot disagree with it. If this ever finds a sprite, the procedural path
    has grown one and the instrument's scope claim is stale.
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
    """A tolerance loose enough to admit a real distortion would retire the
    invariant silently. 1.005 was the smallest violation the corpus contained."""
    assert DFLT_ASPECT_TOLERANCE < 1.005
    assert DFLT_ASPECT_TOLERANCE > 1.0
