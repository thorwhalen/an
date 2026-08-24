"""The bench corpus: which projects are measured, and what each must actually render.

``expect_visual_kinds`` is not belt-and-braces. Every ``examples/*/assets/`` is
gitignored, and before an#33 a missing character descriptor made the compiler
fall back to the procedural rig with **zero** warnings. The first
cross-architecture capture measured exactly that: three CI runners agreed
perfectly about a picture that was not the picture, and the agreement read as a
clean positive result. It surfaced only because the local machine happened to
hold a *stale* build product and therefore disagreed.

So a fixture declares the render path it must exercise, and the check reads the
scene JSON **the browser actually loaded** — an independent second opinion to
``strict_assets=True``, which trusts the compiler that produced it.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterator, Sequence

#: Per-shot subdirectory naming inside `.an/render_work`, and the staged scene
#: filename. Mirrored from the renderer rather than restated as literals at
#: each use site.
SHOT_DIR_GLOB: str = "shot_*"
FRAMES_DIRNAME: str = "frames"
RUNTIME_DIRNAME: str = "runtime"
STAGED_SCENE_NAME: str = "scene.json"

#: Rendering knobs pinned for every bench capture, recorded verbatim into the
#: ledger. NOT flags: a bench whose render knobs vary per invocation produces
#: incomparable rows.
#:
#: ``auto_audio=False`` because audio cannot move a pixel and would otherwise
#: make the frames depend on the audio cache's warm/cold state; ``parallel=1``
#: because a timing-sensitive pool is one more thing to explain if the pixels
#: ever do differ; ``strict_assets=True`` because a stand-in asset renders
#: happily as a DIFFERENT picture (an#33).
BENCH_RENDER_KWARGS: dict[str, Any] = {
    "auto_audio": False,
    "parallel": 1,
    "strict_assets": True,
}


class CorpusError(RuntimeError):
    """A fixture did not render what it declared."""


def _declare_procedural_rig(entity_ref: str) -> Callable[[Path], None]:
    """Make a fixture's use of the built-in placeholder rig **explicit**.

    ``examples/single_character`` references ``charlie-v1``, which is in no
    store — and that is what it *means*: it is the asset-less smoke scene, and
    the placeholder rig is the picture it intends. But before an#33 that intent
    was indistinguishable from a missing descriptor, and the bench renders with
    ``strict_assets=True`` precisely so it can never measure a stand-in and file
    the row under this scene's name.

    So the fixture declares the rig instead of falling into it. The store entry
    lists exactly ``_PLACEHOLDER_PARTS``, which is the same list the fallback
    would have used, so the compiled scene tree — and therefore every pixel — is
    **identical**; what changes is that the compiled scene now records
    ``resolved: "parts", fallback: false``.

    Written into the throwaway copy, never into ``examples/``.
    """

    def prepare(project_dir: Path) -> None:
        from an.adapters.cutout.compile import _PLACEHOLDER_PARTS
        from an.project import load

        project = load(project_dir)
        project.mall["characters"][entity_ref] = {
            "name": entity_ref,
            "parts": list(_PLACEHOLDER_PARTS),
            "note": (
                "declared by the bench corpus so the procedural rig is a choice "
                "rather than a fallback (an#33). Byte-identical render."
            ),
        }

    return prepare


def _prepare_promote_demo(project_dir: Path) -> None:
    """Regenerate the promoted character from the committed source SVG.

    ``examples/promote_demo`` ships only ``raw_maya.svg``; the promoted
    character its scene references is a build product and is gitignored.
    Without this step the fixture renders on a clean checkout, produces no
    error, and — before an#33 — quietly drew a different character.
    """
    from an.characters import promote

    promote(project_dir, entity="raw_maya", as_="maya-promoted", overwrite=True)
    # The IR is regenerated from `scene.md` in the staged copy. Since an#96 the
    # scene has a dialogue line, and `python examples/promote_demo/build.py`
    # (auto_audio=True) stamps visemes into the committed `ir/scene.json` —
    # which would move this fixture's contract hash with developer state.
    # The bench renders with auto_audio=False and this scene's picture has no
    # visemes in it by design; dropping the staged IR makes that a property of
    # the fixture rather than of whoever last ran the example.
    ir = project_dir / "ir" / "scene.json"
    if ir.exists():
        ir.unlink()


@dataclass(frozen=True, slots=True)
class Fixture:
    """A corpus scene: where it lives, how to build it, what it must render."""

    path: str
    #: Run against the throwaway copy before loading, to regenerate build
    #: products the repo does not track.
    prepare: Callable[[Path], None] | None = None
    #: Visual kinds the staged scene MUST contain — see the module docstring.
    expect_visual_kinds: frozenset = frozenset()
    #: Times (seconds, into the scene's CONCATENATED timeline) at which a
    #: golden frame is blessed. Two per scene, the second chosen so something
    #: has actually moved — `--bless` refuses a pair whose two frames are
    #: pixel-identical, which is not hypothetical: `promote_demo`'s frame 0 and
    #: its `duration/2` frame differ by exactly **zero** pixels.
    golden_frames: tuple[float, ...] = field(default_factory=tuple)
    #: One line saying what moves between the two golden times. Carried as data
    #: because "pick a time where something moved" is a rule that decays into a
    #: habit, and the reason is what a reviewer needs when a golden goes red.
    golden_note: str = ""


#: Where the bench-owned fixtures live. NOT under `examples/`, and the reason
#: is mechanical rather than tidiness: `.gitignore` excludes every
#: `examples/*/assets/`, so a corpus scene that needs committed art cannot live
#: there without a carve-out per scene. `misc/` is not ignored at all.
#:
#: The second reason is that a metrics fixture must **hold still**. These four
#: carry their whole rig as committed files and have no ``prepare`` step, so
#: their pixels are a function of the repo alone — where `promote_demo`'s are a
#: function of `an.characters.promote`, and would need re-blessing whenever that
#: changes.
CORPUS_DIRNAME: str = "misc/bench/corpus"


#: The corpus. One fixture per render path, deliberately both: the descriptor
#: (SVG-sprite) path is 12x more sensitive to a rasteriser flip than the
#: procedural one (2.94% vs 0.24% of pixels under GPU-vs-software), so a
#: procedural-only corpus under-reports the case that matters.
#:
#: The four scenes an#38 adds, each for a **measured** reason:
#:
#: - ``graded_field`` — a real gradient (98 distinct luma levels down the centre
#:   column) over a large flat block. Banding has no edge in it, so every
#:   edge-masked metric is blind to it; and the gradient itself sits OUTSIDE the
#:   flat mask by construction (``flat_mask`` demands a zero 4-neighbour delta),
#:   which is why the scene carries a flat block too — measured 0.2795 of the
#:   frame, against 0.0341 for a gradient alone.
#: - ``saturated_outline`` — maximally saturated fills under a pure-black 12px
#:   outline. The shipped examples are 31 colours on white and their measured
#:   4:2:0 edge error is ~3x smaller, so the chroma metric under-reports exactly
#:   the artefact class the epic cares about. Highest edge-mask fraction in the
#:   corpus (0.0566).
#: - ``aa_probe`` — three bars pinned at 7, 23 and 45 degrees. Axis-aligned
#:   ``drawRect`` edges are bit-identical with MSAA on or off, so a corpus of
#:   axis-aligned art cannot validate an AA metric at all. Measured under the
#:   real AA lever (PixiJS ``antialias: false``): ``edge_transition_width``
#:   2.9866 -> 2.0000 and ``video_stream_bytes`` **+6.1%**. That last number is
#:   why this scene is load-bearing rather than decorative — on
#:   ``single_character`` the same lever moves the bytes **-6.1%**, the opposite
#:   of the declared direction, because AA-off on axis-aligned art removes
#:   intermediate colours instead of creating a staircase. Family F is only an
#:   honest witness for ``disabled_aa`` on a scene with non-axis-aligned edges.
#: - ``multi_shot`` — two shots, so ``an/render.py``'s ``_ffmpeg_concat`` is
#:   exercised at all (a single-shot render short-circuits it to
#:   ``shutil.copy``) and ``file_bytes`` stops meaning two different things
#:   depending on shot count. Its shot ids are ``intro`` then ``beat``
#:   **deliberately**: they sort the other way, so any code that recovers shot
#:   order from the directory name instead of the timeline pairs source frames
#:   against the wrong half of the concatenated video, and this fixture is what
#:   notices.
#:
#: One measured fact that shapes the set: the **descriptor path is nearly blind
#: to the AA lever** (96 differing pixels out of 12.4M on ``promote_demo``),
#: because MSAA applies to WebGL geometry and an SVG sprite is a pre-rasterised
#: texture. So the descriptor scenes are in the corpus for the rasteriser
#: sensitivity the cross-arch work measured, not as AA witnesses.
DFLT_FIXTURES: dict[str, Fixture] = {
    "single_character": Fixture(
        path="examples/single_character",
        prepare=_declare_procedural_rig("charlie-v1"),
        expect_visual_kinds=frozenset({"rect", "ellipse"}),
        golden_frames=(0.0, 1.0),
        golden_note=(
            "a blink (the compiled scale_y squash on the procedural eyes). Only "
            "253 pixels differ, and that is the point: blinks occupy 3.5% of "
            "frames, so frame 0 against duration/2 is a pixel-identical pair "
            "on this scene."
        ),
    ),
    "promote_demo": Fixture(
        path="examples/promote_demo",
        prepare=_prepare_promote_demo,
        expect_visual_kinds=frozenset({"svg_sprite"}),
        golden_frames=(0.0, 2.9167),
        golden_note=(
            "a blink — the compiled eyelid swap shows the closed-eye art at "
            "t=2.9167 (an earlier note blamed 'the idle animation', which "
            "nothing on the render path consumes). Measured: frame 0 against "
            "duration/2 differs by exactly ZERO pixels here, so the obvious "
            "second time would have blessed one image twice."
        ),
    ),
    "graded_field": Fixture(
        path=f"{CORPUS_DIRNAME}/graded_field",
        expect_visual_kinds=frozenset({"svg_sprite"}),
        golden_frames=(0.0, 0.1667),
        golden_note=(
            "the white marker sweeping across the gradient (6,270 px). Frame 4, "
            "not the obvious mid-scene frame 6: the marker advances by a "
            "sub-pixel step, so on frames 0, 1, 6, 8 and 11 it lands on an exact "
            "pixel boundary and AA-off changes ZERO pixels there. A blessed pair "
            "that no available mutation can move is a gate that cannot go red."
        ),
    ),
    "saturated_outline": Fixture(
        path=f"{CORPUS_DIRNAME}/saturated_outline",
        expect_visual_kinds=frozenset({"svg_sprite"}),
        golden_frames=(0.0, 0.25),
        golden_note="the head plate rotating through 0.3 rad (1,187 px).",
    ),
    "aa_probe": Fixture(
        path=f"{CORPUS_DIRNAME}/aa_probe",
        expect_visual_kinds=frozenset({"rect"}),
        golden_frames=(0.0, 0.25),
        golden_note=(
            "the fourth bar sweeping horizontally (4,200 px). The three angled "
            "bars are pinned and do not move — they are the AA subject."
        ),
    ),
    "dialogue": Fixture(
        path=f"{CORPUS_DIRNAME}/dialogue",
        expect_visual_kinds=frozenset({"rect", "ellipse", "mouth", "eye"}),
        golden_frames=(0.0, 0.6),
        golden_note=(
            "the mouth mid-line: frame 14 sits on the `h`/`a` of 'shape' and "
            "TODAY'S condenser shows `C` there, having dropped the `D` and `A` "
            "that follow inside its 0.14 s window — the drop-instead-of-hold "
            "defect PR-B (an#97) replaces with a vote, which is what will move "
            "this frame. The head is lifted 34 px above its rest by an absolute "
            "`set` so the placeholder rig's mouth clears the torso (127 px "
            "differ between the pair). The second golden sits INSIDE the spoken "
            "interval, which no other corpus scene does: `single_character` "
            "samples after its line ends and `promote_demo` renders mute in the "
            "bench (no visemes in its IR, by design). The visemes are the "
            "offline provider's, stamped into the committed ir/scene.json; the "
            "bench renders with auto_audio=False and reads them from there."
        ),
    ),
    "multi_shot": Fixture(
        path=f"{CORPUS_DIRNAME}/multi_shot",
        expect_visual_kinds=frozenset({"rect", "ellipse"}),
        golden_frames=(0.0, 0.25),
        golden_note=(
            "the whole picture: 0.25s is the FIRST frame of the second shot, so "
            "the pair spans the concat boundary (75,050 px). A golden pair "
            "inside one shot would not notice a shot rendered in the wrong order."
        ),
    ),
}


def iter_shot_dirs(
    work_dir: Path, *, order: Sequence[str]
) -> Iterator[tuple[str, Path]]:
    """``(shot_id, shot_dir)`` for every rendered shot, in **timeline** order.

    ``order`` is mandatory, and that is the whole point of this signature.
    ``an/render.py`` concatenates ``[r.mp4_path for r in shot_results]`` built
    from ``list(scene.timeline)``, while this function's previous form returned
    ``sorted(work_dir.glob("shot_*"))`` — directory-name order. The two agree
    only when the shot ids happen to sort into timeline order, and when they do
    not, every encode-side metric pairs source frame *i* of one shot against
    decoded frame *i* of another. The ``multi_shot`` fixture's ids (``intro``
    then ``beat``) are chosen so they disagree.

    >>> import tempfile
    >>> from pathlib import Path
    >>> d = Path(tempfile.mkdtemp())
    >>> for name in ("shot_intro", "shot_beat"): (d / name).mkdir()
    >>> [i for i, _ in iter_shot_dirs(d, order=["intro", "beat"])]
    ['intro', 'beat']
    """
    root = Path(work_dir)
    for shot_id in order:
        shot_dir = root / f"shot_{shot_id}"
        if not shot_dir.is_dir():
            raise CorpusError(
                f"the timeline declares shot {shot_id!r} but {shot_dir} does not "
                "exist. The renderer names each shot directory after the shot id, "
                "so a missing one means the render and the timeline disagree."
            )
        yield shot_id, shot_dir


def staged_scene(shot_dir: Path) -> dict:
    """The compiled scene JSON the browser actually loaded, for one shot."""
    path = shot_dir / RUNTIME_DIRNAME / STAGED_SCENE_NAME
    return json.loads(path.read_text(encoding="utf-8"))


def visual_kinds(scene_json: dict) -> set[str]:
    """Every ``visual.kind`` in a staged scene's node tree.

    Read from the staged file rather than re-compiled, so it reports what was
    rendered rather than what a second compile would produce.

    Scoped to ``scene`` and to the ``visual`` key specifically. A sweep for
    every ``kind`` anywhere in the document — which is what this was before —
    also collects an#33's ``asset_resolution`` entries, whose ``kind`` is
    ``"character"`` / ``"environment"``. Those are entity kinds, not visual
    kinds, and mixing them makes the field mean two things at once.

    >>> sorted(visual_kinds({"scene": {"visual": {"kind": "rect"},
    ...                                "children": [{"visual": {"kind": "eye"}}]},
    ...                      "asset_resolution": [{"kind": "character"}]}))
    ['eye', 'rect']
    """
    kinds: set[str] = set()

    def walk(node: Any) -> None:
        if not isinstance(node, dict):
            return
        visual = node.get("visual")
        if isinstance(visual, dict) and isinstance(visual.get("kind"), str):
            kinds.add(visual["kind"])
        for child in node.get("children") or []:
            walk(child)

    walk(scene_json.get("scene") or {})
    return kinds


def assert_render_path(name: str, fixture: Fixture, kinds: set[str]) -> None:
    """Refuse a capture that did not exercise the path its fixture declares."""
    missing = fixture.expect_visual_kinds - kinds
    if missing:
        raise CorpusError(
            f"fixture {name!r} rendered WITHOUT {sorted(missing)} — it staged "
            f"{sorted(kinds)} instead. Before an#33 a missing character "
            "descriptor made the compiler fall back to the procedural rig "
            "silently, so this capture would have measured a different picture "
            "and filed it as this scene's row."
        )
