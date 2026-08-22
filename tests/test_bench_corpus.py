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
        "scene": {
            "visual": {"kind": "rect"},
            "children": [{"visual": {"kind": "eye"}}],
        },
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
            AssetRef(
                kind="character", id="charlie", store="characters", ref="charlie-v1"
            )
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
    (
        fixture / ".an" / "render_work" / "shot_s1" / "frames" / "frame_000099.png"
    ).write_bytes(b"stale")
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


# --------------------------------------------------------------- an#38 additions


def test_every_fixture_declares_two_golden_frames_and_says_what_moves():
    """MUTATION: drop `golden_frames` (or `golden_note`) from any one fixture.

    Two frames, because one cannot notice a scene that renders its first instant
    correctly and then stops. And a recorded reason, because "pick a time where
    something moved" is a rule that decays into a habit — measured on
    `promote_demo`, frame 0 and the `duration/2` frame differ by exactly ZERO
    pixels, so the obvious choice blesses one picture twice.
    """
    from an.bench.golden import REQUIRED_GOLDEN_FRAMES

    for name, fixture in DFLT_FIXTURES.items():
        assert len(fixture.golden_frames) == REQUIRED_GOLDEN_FRAMES, (
            f"{name} declares {len(fixture.golden_frames)} golden frames"
        )
        assert fixture.golden_note.strip(), f"{name} does not say what moves"
        assert fixture.golden_frames[0] != fixture.golden_frames[1], (
            f"{name} pins the same time twice"
        )


def test_the_corpus_covers_the_four_scenes_the_research_says_it_lacked():
    """MUTATION: delete any one of the four an#38 fixtures.

    Pinned by NAME rather than by counting, because a count is satisfied by any
    four scenes and each of these four exists for its own measured reason:
    banding has no edge in it; the shipped examples' 4:2:0 edge error is ~3x
    smaller than a saturated pattern's; a single-shot render short-circuits
    `_ffmpeg_concat` to `shutil.copy`; and axis-aligned `drawRect` edges are
    bit-identical with MSAA on or off.
    """
    assert {"graded_field", "saturated_outline", "multi_shot", "aa_probe"} <= set(
        DFLT_FIXTURES
    )


def test_the_bench_owned_fixtures_need_no_prepare_step():
    """A metrics fixture must hold still.

    MUTATION: give one of the four a `prepare` that regenerates its art.

    `promote_demo` legitimately has one — its rig is a gitignored build product,
    and exercising `an.characters.promote` is part of what that scene is for.
    The four bench-owned scenes must not: their pixels have to be a function of
    the repo alone, or every change to an unrelated generator forces a re-bless.
    """
    from an.bench.corpus import CORPUS_DIRNAME

    for name, fixture in DFLT_FIXTURES.items():
        if fixture.path.startswith(CORPUS_DIRNAME):
            assert fixture.prepare is None, (
                f"{name} lives in the bench-owned corpus but has a prepare step, "
                "so its pixels depend on code outside its own directory"
            )


def test_every_corpus_fixture_is_committed_whole():
    """MUTATION: add `misc/bench/corpus/*/assets/` to .gitignore.

    The failure this repeats: every `examples/*/assets/` IS gitignored, and
    before an#33 a missing descriptor made the compiler fall back to the
    procedural rig with zero warnings — so three CI runners agreed perfectly
    about a picture that was not the picture.
    """
    import subprocess
    from pathlib import Path

    from an.bench.corpus import CORPUS_DIRNAME
    from an.bench.paths import repo_root

    root = repo_root()
    for name, fixture in DFLT_FIXTURES.items():
        if not fixture.path.startswith(CORPUS_DIRNAME):
            continue
        directory = root / fixture.path
        assert directory.is_dir(), f"{name}: {directory} does not exist"
        tracked = subprocess.run(
            ["git", "ls-files", "--", fixture.path],
            cwd=root,
            capture_output=True,
            text=True,
            check=False,
        ).stdout.split()
        tracked = [Path(t).as_posix() for t in tracked]
        # `.as_posix()` on both sides: `git ls-files` always prints forward
        # slashes and `Path.relative_to` yields the platform separator, so on
        # Windows every path "differed" and the whole corpus read as untracked.
        # Caught by the Windows leg on the first run of this test.
        on_disk = {
            p.relative_to(root).as_posix()
            for p in directory.rglob("*")
            if p.is_file() and ".an" not in p.parts and "output" not in p.parts
        }
        untracked = sorted(on_disk - set(tracked))
        assert not untracked, f"{name} has untracked fixture files: {untracked}"


def test_the_multi_shot_fixture_ids_do_not_sort_into_timeline_order():
    """The ordering trap has to stay armed.

    MUTATION: rename `multi_shot`'s shots to `s1` and `s2`.

    `an/render.py` concatenates in `scene.timeline` order; a directory-name sort
    agrees only by luck. This fixture is the thing that notices when it does
    not, and it can only do that while its ids disagree with their own sort.
    """
    from an.bench.paths import repo_root

    scene_md = (repo_root() / DFLT_FIXTURES["multi_shot"].path / "scene.md").read_text(
        encoding="utf-8"
    )
    ids = [
        line.split()[2] for line in scene_md.splitlines() if line.startswith("## Shot ")
    ]
    assert len(ids) >= 2, "the multi-shot fixture must have more than one shot"
    assert ids != sorted(ids), (
        f"shot ids {ids} sort into timeline order, so a directory-name sort would "
        "agree with the timeline and this fixture would stop catching the bug"
    )


def test_iter_shot_dirs_follows_the_timeline_not_the_directory_name(tmp_path):
    """MUTATION: `for shot_id in order` -> `for shot_dir in sorted(root.glob(...))`.

    Directory order and timeline order agree only when the ids happen to sort
    that way. When they do not, every encode-side metric pairs source frame *i*
    of one shot against decoded frame *i* of another, and reports plausible
    numbers for a comparison that never happened.
    """
    from an.bench.corpus import iter_shot_dirs

    for shot_id in ("intro", "beat"):
        (tmp_path / f"shot_{shot_id}").mkdir()
    assert [i for i, _ in iter_shot_dirs(tmp_path, order=["intro", "beat"])] == [
        "intro",
        "beat",
    ]
    assert sorted(["intro", "beat"]) == ["beat", "intro"], (
        "the two orders must disagree, or this test asserts nothing"
    )


def test_a_shot_the_timeline_names_but_the_render_did_not_produce_is_refused(tmp_path):
    """MUTATION: `if not shot_dir.is_dir(): raise` -> `continue`.

    Skipping would silently shorten the source leg, which is the same defect as
    pairing it out of order.
    """
    from an.bench.corpus import iter_shot_dirs

    (tmp_path / "shot_intro").mkdir()
    with pytest.raises(CorpusError, match="beat"):
        list(iter_shot_dirs(tmp_path, order=["intro", "beat"]))


def test_corpus_entity_ids_are_pinned_by_literal():
    """A rename silently re-phases every blink and moves every metric.

    MUTATION: rename an entity in any corpus `scene.md`.
    """
    from an.bench.paths import repo_root

    expected = {
        "graded_field": ["field"],
        "saturated_outline": ["plates"],
        "aa_probe": ["probe"],
        "multi_shot": ["back", "ada", "back", "ada"],
    }
    root = repo_root()
    for name, ids in expected.items():
        text = (root / DFLT_FIXTURES[name].path / "scene.md").read_text(
            encoding="utf-8"
        )
        found = [
            line.split("id:", 1)[1].strip()
            for line in text.splitlines()
            if line.strip().startswith("id:")
        ]
        assert found == ids, f"{name}: entity ids are {found}, expected {ids}"


def test_the_golden_corpus_is_not_excluded_from_the_sdist():
    """MUTATION: add `[tool.hatch.build.targets.sdist] exclude = ["misc"]`.

    `an bench` needs a source checkout by construction — `repo_root()` refuses
    to run against a wheel — so an sdist that dropped `misc/bench/` would be a
    source tree the bench cannot run in. Measured on 0.1.29, the corpus and its
    goldens are 1.0% of the sdist against `examples/` at 69.6%, and the wheel
    excludes both already. The decision is recorded in `pyproject.toml`; this
    pins it against a well-meaning slimming pass.
    """
    from an.bench.paths import repo_root

    # Scanned as text rather than parsed: `tomllib` is Python 3.11+, this repo
    # supports 3.10, and the assertion is about whether a SECTION exists at all
    # — which a text scan answers directly. (Caught by the 3.10 leg on the first
    # run of this test.)
    lines = (repo_root() / "pyproject.toml").read_text(encoding="utf-8").splitlines()
    section: str | None = None
    offending: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            section = stripped
            continue
        if section == "[tool.hatch.build.targets.sdist]" and "misc" in stripped:
            offending.append(stripped)
    assert not offending, (
        f"[tool.hatch.build.targets.sdist] mentions misc/ ({offending}). Whatever "
        "it says, it now decides whether the bench corpus reaches the sdist — "
        "and `an bench` needs a source checkout by construction. Re-read the "
        "decision recorded above that section before changing this."
    )


def test_no_corpus_fixture_ships_an_an_toml():
    """MUTATION: restore any `misc/bench/corpus/*/an.toml`.

    `an.project.load` never reads `an.toml` — only `init` writes one — so a
    committed one is not configuration, it is a second place for a scene's fps
    and resolution to be written down. The four the corpus first shipped all
    declared `fps = 30` and `resolution = [1920, 1080]` against every
    `scene.md`'s 24 and 320x240, so the only thing they could ever do was tell a
    reader the wrong thing about what the bench renders.
    """
    from an.bench.corpus import CORPUS_DIRNAME
    from an.bench.paths import repo_root

    root = repo_root()
    strays = sorted(
        str(p.relative_to(root))
        for fixture in DFLT_FIXTURES.values()
        if fixture.path.startswith(CORPUS_DIRNAME)
        for p in (root / fixture.path).glob("an.toml")
    )
    assert not strays, f"{strays} contradict their own scene.md and are read by nothing"


# ------------------------------ an#54: what the staging copy leaves behind


def _fixture_tree(root):
    """A fixture shaped like a real one: an audio cache to keep, shots to drop."""
    (root / "artifacts" / "audio").mkdir(parents=True)
    (root / "artifacts" / "audio" / "line.wav").write_bytes(b"audio")
    (root / "artifacts" / "shots").mkdir(parents=True)
    (root / "artifacts" / "shots" / "s1.mp4").write_bytes(b"stale render")
    # A directory named `shots` that has nothing to do with `mall["shots"]`.
    (root / "assets" / "characters" / "maya" / "shots").mkdir(parents=True)
    (root / "assets" / "characters" / "maya" / "shots" / "keep.svg").write_text(
        "<svg/>", encoding="utf-8"
    )
    (root / ".an").mkdir()
    (root / ".an" / "render_work").mkdir()
    (root / "output").mkdir()
    (root / "scene.md").write_text("# scene", encoding="utf-8")
    return root


def test_the_staging_copy_drops_the_previous_renders_shots_and_keeps_the_audio(
    tmp_path,
):
    """MUTATION: `IGNORED_RELPATHS_ON_COPY = ("artifacts/shots",)` -> `()`.

    `mall["shots"]` is `<project>/artifacts/shots` and it is GITIGNORED, so the
    previous render's per-shot mp4s crossed into every bench run on a developer
    machine and on no clean checkout — in the module whose docstring is "do not
    inherit a stale render". `artifacts/` itself is kept on purpose: it holds
    the audio cache, whose warm/cold state the bench records rather than
    destroys.
    """
    from an.bench.capture import stage_copy

    src = _fixture_tree(tmp_path / "fixture")
    copy = stage_copy(src, tmp_path / "work")

    assert (copy / "artifacts" / "audio" / "line.wav").is_file(), (
        "the audio cache must survive — its warm/cold state is a recorded fact"
    )
    assert not (copy / "artifacts" / "shots").exists()
    assert not (copy / ".an").exists() and not (copy / "output").exists()


def test_the_exclusion_is_by_path_and_not_by_basename(tmp_path):
    """MUTATION: match the basename `shots` at any depth instead of the path.

    That is the obvious `shutil.ignore_patterns("shots")` spelling, and it also
    deletes a character rig's `assets/characters/*/shots`. The other obvious
    spelling — `"artifacts/shots"` as an ignore_patterns entry — matches
    NOTHING, because the closure is handed bare names and no name contains a
    separator. Both fail silently, in opposite directions.
    """
    from an.bench.capture import stage_copy

    src = _fixture_tree(tmp_path / "fixture")
    copy = stage_copy(src, tmp_path / "work")
    assert (copy / "assets" / "characters" / "maya" / "shots" / "keep.svg").is_file(), (
        'a directory called `shots` that is not `mall["shots"]` must survive'
    )


def test_a_shot_records_the_pixel_size_actually_on_disk(tmp_path):
    """MUTATION: sample one frame instead of reading every frame's IHDR.

    The uniform case still passes under that mutation; the mixed case is what
    catches it, and a mixed sequence is exactly what a half-applied supersample
    produces.
    """
    import numpy as np

    from an.bench.capture import distinct_png_sizes
    from an.bench.png import encode_png

    uniform = tmp_path / "uniform"
    uniform.mkdir()
    for i in range(4):
        (uniform / f"frame_{i:06d}.png").write_bytes(
            encode_png(np.zeros((240, 320, 3), np.uint8))
        )
    assert distinct_png_sizes(uniform) == ((320, 240),)

    mixed = tmp_path / "mixed"
    mixed.mkdir()
    for i, (w, h) in enumerate([(320, 240), (320, 240), (640, 480), (640, 480)]):
        (mixed / f"frame_{i:06d}.png").write_bytes(
            encode_png(np.zeros((h, w, 3), np.uint8))
        )
    assert distinct_png_sizes(mixed) == ((320, 240), (640, 480))
