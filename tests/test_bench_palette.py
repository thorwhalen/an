"""The palette derivation (an#36) — the metric's meaning depends on it entirely.

`off_palette_pixel_fraction` means "not one of the colours the compiler
declared". A derivation that under-collects turns it into a large, plausible
number with no error anywhere, and a derivation that over-collects makes it a
lower bound nobody knows is a lower bound. Both failures are silent.

Three specific traps, each with a test:

- The runtime's colour rule is **not CSS**. `"#222"` pads to `"222000"`.
- Some painted colours are **runtime constants**, never in the JSON.
- Some `visual.color` values are **inert** and must not be collected.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from an.bench.palette import (
    RUNTIME_EYE_COLOURS,
    RUNTIME_MOUTH_COLOURS,
    palette_for_scene,
    parse_color,
    runtime_literal_colours,
    svg_colours,
)

RUNTIME_JS = Path(__file__).resolve().parents[1] / "an/data/cutout_runtime/runtime.js"


def _scene(*nodes, background="#ffffff", textures=None) -> dict:
    return {
        "meta": {"background": background},
        "scene": {"name": "root", "children": list(nodes)},
        "assets": {"textures": textures or {}},
    }


def _node(kind: str, **visual) -> dict:
    return {"name": kind, "visual": {"kind": kind, **visual}, "children": []}


# ----------------------------------------------------------------- parse_color


@pytest.mark.parametrize(
    "value,expected",
    [
        ("#ffffff", 0xFFFFFF),
        ("#222", 0x222000),  # NOT 0x222222 — this is the trap
        ("222", 0x222000),
        ("#ffffffaa", 0xFFFFFF),
        (None, 0x888888),
        (42, 0x888888),
        ("rebeccapurple", 0x888888),
    ],
)
def test_parse_color_is_the_runtime_rule_not_css(value, expected):
    """`hex.padEnd(6,'0').slice(0,6)`, mirrored exactly.

    A CSS-correct 3-digit expander maps `"#222"` to `0x222222`; the runtime
    paints `0x222000`. Substituting the correct-looking rule would put a colour
    in the palette that no pixel has, and drop the one every pixel has.
    """
    assert parse_color(value) == expected


def test_the_declared_runtime_constants_match_the_runtime_source():
    """Adding a fifth mouth colour must redden a test, not inflate a metric.

    Read from the file that actually paints them, in the same spirit as the
    launch-flag guard: assert the call site, do not trust the constant.
    """
    declared = set(RUNTIME_EYE_COLOURS) | set(RUNTIME_MOUTH_COLOURS)
    painted = runtime_literal_colours(RUNTIME_JS)
    missing = sorted(hex(c) for c in painted - declared)
    assert not missing, (
        f"runtime.js paints {missing}, which the bench palette does not declare; "
        "every pixel of those colours would be counted as off-palette"
    )


def test_the_guard_notices_a_new_runtime_colour(tmp_path):
    """Mutation-tested in place: the guard above must not be decoration."""
    mutant = tmp_path / "runtime.js"
    mutant.write_text(
        RUNTIME_JS.read_text(encoding="utf-8") + "\ng.beginFill(0xdeadbe, 1.0);\n",
        encoding="utf-8",
    )
    declared = set(RUNTIME_EYE_COLOURS) | set(RUNTIME_MOUTH_COLOURS)
    assert 0xDEADBE in runtime_literal_colours(mutant) - declared


# --------------------------------------------------------- per-kind collection


def test_a_rect_contributes_its_declared_colour():
    pal = palette_for_scene(_scene(_node("rect", color="#123456")), runtime_dir=Path("."))
    assert 0x123456 in pal["palette"]


def test_a_mouth_contributes_the_runtime_constants_and_not_its_own_colour():
    """`drawMouthShape` never reads the node's colour — collecting it is noise."""
    pal = palette_for_scene(
        _scene(_node("mouth", color="#abcdef")), runtime_dir=Path(".")
    )
    assert set(RUNTIME_MOUTH_COLOURS) <= set(pal["palette"])
    assert 0xABCDEF not in pal["palette"], (
        "the mouth's visual.color is inert; a palette entry no pixel has makes "
        "the metric a silent lower bound"
    )


def test_an_eye_contributes_both_its_pupil_and_the_runtime_sclera():
    pal = palette_for_scene(_scene(_node("eye", color="#101010")), runtime_dir=Path("."))
    assert 0x101010 in pal["palette"]
    assert set(RUNTIME_EYE_COLOURS) <= set(pal["palette"])


def test_an_svg_sprite_does_not_contribute_its_schema_default():
    """Every svg_sprite carries `#888888`, which is the schema default, not paint."""
    pal = palette_for_scene(
        _scene(_node("svg_sprite", color="#888888", asset_id="a")),
        runtime_dir=Path("."),
    )
    assert 0x888888 not in pal["palette"]


# ------------------------------------------------------------------ SVG parsing


def _write_svg(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "part.svg"
    p.write_text(f'<svg xmlns="http://www.w3.org/2000/svg">{body}</svg>', encoding="utf-8")
    return p


def test_svg_colours_reads_both_attribute_and_style_spellings(tmp_path):
    """A regex over `fill="…"` misses `style="fill:#abc"` entirely."""
    svg = _write_svg(
        tmp_path,
        '<rect fill="#112233"/><rect style="fill:#445566;stroke:#778899"/>',
    )
    found, unresolved = svg_colours(svg)
    assert {0x112233, 0x445566, 0x778899} <= found
    assert not unresolved


def test_a_hidden_subtree_contributes_nothing(tmp_path):
    """Colours that never reach a pixel do not belong in the palette."""
    svg = _write_svg(
        tmp_path, '<g style="display:none"><rect fill="#abcdef"/></g><rect fill="#111111"/>'
    )
    found, _ = svg_colours(svg)
    assert 0x111111 in found and 0xABCDEF not in found


def test_an_unresolvable_token_is_reported_and_never_guessed(tmp_path):
    """Guessing puts a wrong colour in the palette and the metric reads low."""
    svg = _write_svg(tmp_path, '<rect fill="url(#grad)"/><rect fill="rebeccapurple"/>')
    found, unresolved = svg_colours(svg)
    assert not found
    assert "rebeccapurple" in unresolved


def test_a_gradient_reference_is_reported_rather_than_treated_as_no_colour(tmp_path):
    """`url(#grad)` is not `none`, and the difference is load-bearing.

    A gradient paints a continuum between its stops. `stop-color` is collected,
    so the endpoints reach the palette — but the interpolated pixels between
    them do not, and they are real. Reporting the reference is what lets a
    reviewer read a high `off_palette_pixel_fraction` on such a scene as
    "gradients, expected" rather than "the derivation is broken".
    """
    svg = _write_svg(
        tmp_path,
        '<defs><linearGradient id="g">'
        '<stop stop-color="#000000"/><stop stop-color="#ffffff"/>'
        "</linearGradient></defs>"
        '<rect fill="url(#g)"/>',
    )
    found, unresolved = svg_colours(svg)
    assert {0x000000, 0xFFFFFF} <= found, "the stops are real colours and are collected"
    assert any("url(" in t for t in unresolved), (
        "the reference itself must be reported, so a palette that is "
        "endpoints-only says so rather than looking complete"
    )


def test_none_and_transparent_are_not_colours(tmp_path):
    svg = _write_svg(tmp_path, '<rect fill="none" stroke="transparent"/>')
    found, unresolved = svg_colours(svg)
    assert not found and not unresolved


def test_an_unparseable_svg_is_reported_rather_than_silently_empty(tmp_path):
    p = tmp_path / "broken.svg"
    p.write_text("<svg><rect fill=", encoding="utf-8")
    found, unresolved = svg_colours(p)
    assert not found
    assert any(t.startswith("unparseable:") for t in unresolved)


# ------------------------------------------------------------------ provenance


def test_a_referenced_but_unstaged_asset_is_reported(tmp_path):
    """A texture that never reached the runtime dir contributes no colours.

    Recorded rather than skipped: those pixels rendered as a white rectangle,
    and the palette must say it does not know what they were.
    """
    pal = palette_for_scene(
        _scene(
            _node("svg_sprite", asset_id="head"),
            textures={"head": {"src": "characters/x/parts/head.svg"}},
        ),
        runtime_dir=tmp_path,
    )
    assert any(t.startswith("unstaged:") for t in pal["unresolved_svg_colour_tokens"])


def test_the_svg_path_marks_the_palette_as_a_superset(tmp_path):
    """All nine visemes are declared; only some paint. A lower bound, recorded."""
    pal = palette_for_scene(
        _scene(_node("svg_sprite", asset_id="head")), runtime_dir=tmp_path
    )
    assert pal["palette_is_superset"] is True


def test_a_procedural_scene_is_not_a_superset():
    pal = palette_for_scene(_scene(_node("rect", color="#000000")), runtime_dir=Path("."))
    assert pal["palette_is_superset"] is False


def test_palette_sources_separates_the_two_halves_of_the_derivation():
    """So a reviewer can see WHICH half moved when the number does."""
    pal = palette_for_scene(_scene(_node("mouth"), _node("rect", color="#010203")),
                            runtime_dir=Path("."))
    assert pal["palette_sources"]["runtime_constants"] == len(RUNTIME_MOUTH_COLOURS)
    assert pal["palette_sources"]["scene_json"] >= 2  # background + the rect
