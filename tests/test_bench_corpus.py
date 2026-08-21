"""The bench corpus declares what it renders, and the check is independent (an#36).

Direct analogue of `tests/test_crossarch_fixtures.py`, which exists because
three CI runners once agreed perfectly about a picture that was never rendered.
`strict_assets=True` now catches that at compile time — but it trusts the
compiler that produced the scene, and this check reads the artifact the browser
actually loaded. Two independent opinions, deliberately.
"""

from __future__ import annotations

import pytest

from an.bench.corpus import (
    BENCH_RENDER_KWARGS,
    DFLT_FIXTURES,
    CorpusError,
    Fixture,
    assert_render_path,
    visual_kinds,
)


def test_every_fixture_declares_the_render_path_it_must_exercise():
    for name, fixture in DFLT_FIXTURES.items():
        assert fixture.expect_visual_kinds, (
            f"{name} declares no expected visual kinds, so a silent fallback to "
            "a different render path would produce a clean-looking row"
        )


def test_the_corpus_covers_both_render_paths():
    """The descriptor path is 12x more sensitive to a rasteriser flip.

    A procedural-only corpus under-reports exactly the case that matters
    (2.94% vs 0.24% of pixels under GPU-vs-software).
    """
    declared = set().union(*(f.expect_visual_kinds for f in DFLT_FIXTURES.values()))
    assert "svg_sprite" in declared, "the descriptor (SVG-sprite) path"
    assert declared & {"rect", "ellipse"}, "the procedural path"


def test_the_render_knobs_are_pinned_and_not_flags():
    """A bench whose render knobs vary per invocation produces incomparable rows."""
    assert BENCH_RENDER_KWARGS == {
        "auto_audio": False,
        "parallel": 1,
        "strict_assets": True,
    }


def test_a_capture_that_missed_its_render_path_is_refused():
    fixture = Fixture(path="x", expect_visual_kinds=frozenset({"svg_sprite"}))
    with pytest.raises(CorpusError, match="svg_sprite"):
        assert_render_path("x", fixture, {"rect", "ellipse"})


def test_a_capture_that_hit_its_render_path_passes():
    fixture = Fixture(path="x", expect_visual_kinds=frozenset({"rect"}))
    assert_render_path("x", fixture, {"rect", "ellipse", "mouth"})


def test_visual_kinds_reads_the_scene_tree_and_not_the_whole_document():
    """an#33 added `asset_resolution`, whose `kind` is an ENTITY kind.

    Sweeping every `kind` in the document — which is what the crossarch version
    did — mixes entity kinds into a field named for visual kinds, so
    `expect_visual_kinds` starts matching against a vocabulary it was never
    written for.
    """
    staged = {
        "scene": {"visual": {"kind": "rect"}, "children": [{"visual": {"kind": "eye"}}]},
        "asset_resolution": [{"kind": "character", "resolved": "placeholder"}],
        "assets": {"textures": {"a": {"kind": "ignored"}}},
    }
    assert visual_kinds(staged) == {"rect", "eye"}


def test_the_fixture_that_uses_the_placeholder_rig_declares_it(tmp_path):
    """`single_character` INTENDS the built-in rig; `strict_assets` refuses fallbacks.

    Its prepare step writes exactly `_PLACEHOLDER_PARTS` into the copy's store,
    so the compiled tree — and therefore every pixel — is identical to the
    fallback's, and the record changes from `fallback: true` to `resolved:
    "parts"`. That equivalence is what makes this a declaration rather than a
    different scene, and it is asserted here rather than assumed.
    """
    from an.adapters.cutout.compile import _PLACEHOLDER_PARTS, compile_shot
    from an.ir.schema import AssetRef, Shot

    shot = Shot(
        id="s1",
        style="cutout",
        duration=1.0,
        entities=[
            AssetRef(kind="character", id="charlie", store="characters", ref="charlie-v1")
        ],
    )
    with pytest.warns(Warning):
        fell_back = compile_shot(shot, mall={"characters": {}})
    declared = compile_shot(
        shot,
        mall={"characters": {"charlie-v1": {"parts": list(_PLACEHOLDER_PARTS)}}},
        strict_assets=True,
    )
    assert fell_back.scene == declared.scene, (
        "the declaration must render the same picture as the fallback did; if "
        "these diverge, the corpus fixture has quietly become a different scene"
    )
    assert declared.asset_resolution[0].fallback is False


def test_the_copy_leaves_the_previous_render_behind(tmp_path):
    """`frames/` is never cleared and image2 reads the contiguous run from 0.

    A stale render inherited by the copy appends frames to the SOURCE leg of
    every encode-side metric and to nothing else, so all of them silently pair
    frame i of one sequence with frame i of a different one. Verified here
    without rendering, because the whole failure is that rendering succeeds.
    """
    from an.bench.capture import stage_copy

    fixture = tmp_path / "proj"
    (fixture / ".an" / "render_work" / "shot_s1" / "frames").mkdir(parents=True)
    (fixture / ".an" / "render_work" / "shot_s1" / "frames" / "frame_000099.png").write_bytes(b"stale")
    (fixture / "output").mkdir()
    (fixture / "output" / "main.mp4").write_bytes(b"stale")
    (fixture / "artifacts" / "audio").mkdir(parents=True)
    (fixture / "artifacts" / "audio" / "a.wav").write_bytes(b"cache")
    (fixture / "scene.md").write_text("# scene", encoding="utf-8")

    copy = stage_copy(fixture, tmp_path / "work")

    assert (copy / "scene.md").is_file(), "the project itself must be copied"
    assert not (copy / ".an").exists(), "a previous render must not be inherited"
    assert not (copy / "output").exists(), "nor its output"
    assert (copy / "artifacts" / "audio" / "a.wav").is_file(), (
        "the audio cache IS kept — its warm/cold state is recorded rather than "
        "destroyed, because it affects wall-time and nothing else"
    )
