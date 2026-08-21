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
from typing import Any, Callable, Iterator

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


@dataclass(frozen=True, slots=True)
class Fixture:
    """A corpus scene: where it lives, how to build it, what it must render."""

    path: str
    #: Run against the throwaway copy before loading, to regenerate build
    #: products the repo does not track.
    prepare: Callable[[Path], None] | None = None
    #: Visual kinds the staged scene MUST contain — see the module docstring.
    expect_visual_kinds: frozenset = frozenset()
    #: Times (seconds) at which an#38 blesses a golden frame. Empty here on
    #: purpose: the field ships now so the corpus work can pin per-scene times
    #: without a schema change, and `--bless` refuses a pair whose two frames
    #: are byte-identical.
    golden_frames: tuple[float, ...] = field(default_factory=tuple)


#: One fixture per render path, deliberately both: the descriptor (SVG-sprite)
#: path is 12x more sensitive to a rasteriser flip than the procedural one
#: (2.94% vs 0.24% of pixels under GPU-vs-software), so a procedural-only
#: corpus under-reports the case that matters.
#:
#: Four scenes the research says this corpus still lacks — a large flat or
#: gently-graded field, a saturated fill under a black outline, a multi-shot
#: project (a single-shot render short-circuits the concat to `shutil.copy`, so
#: `_ffmpeg_concat` is never exercised), and an `aa_probe` with edges at
#: non-axis angles (axis-aligned `drawRect` edges are bit-identical with MSAA
#: on or off, so a corpus of axis-aligned art cannot validate an AA metric at
#: all) — belong to an#38, which builds them rather than borrowing them from
#: `examples/`.
DFLT_FIXTURES: dict[str, Fixture] = {
    "single_character": Fixture(
        path="examples/single_character",
        prepare=_declare_procedural_rig("charlie-v1"),
        expect_visual_kinds=frozenset({"rect", "ellipse"}),
    ),
    "promote_demo": Fixture(
        path="examples/promote_demo",
        prepare=_prepare_promote_demo,
        expect_visual_kinds=frozenset({"svg_sprite"}),
    ),
}


def iter_shot_dirs(work_dir: Path) -> Iterator[tuple[str, Path]]:
    """``(shot_id, shot_dir)`` for every rendered shot, in sorted order."""
    for shot_dir in sorted(Path(work_dir).glob(SHOT_DIR_GLOB)):
        yield shot_dir.name[len("shot_") :], shot_dir


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
