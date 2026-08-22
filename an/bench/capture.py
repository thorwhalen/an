"""Render one corpus fixture into a throwaway copy, and hand back its artifacts.

Two things this module exists to get right, both of which produce plausible
numbers when got wrong:

**Render into a copy.** The render path mutates the project directory — scene
mtimes, the decisions log, ``.an/render_work`` — so rendering in place makes
the git sha in the ledger filename a lie about the tree that produced the row.

**Do not inherit a stale render.** ``shutil.copytree`` of a developer's
checkout would carry ``.an/render_work`` and ``output/`` across, ``frames/`` is
never cleared, and ffmpeg's image2 demuxer reads the contiguous
``frame_%06d.png`` run from 0 — so a longer previous render is silently
appended to this one. Every encode-side metric pairs source frame *i* with
decoded frame *i*, so that appends garbage to one leg and shifts nothing on the
other. ``artifacts/`` is deliberately kept *except for one subdirectory*: it
holds the audio cache, whose warm/cold state is recorded rather than destroyed
— but ``artifacts/shots`` is ``mall["shots"]``, the previous render's per-shot
mp4s, and this module's whole promise is that nothing of a previous render
crosses. It is gitignored, so it does not reproduce on a clean checkout: a
per-developer landmine, in the module whose docstring says the opposite.
"""

from __future__ import annotations

import shutil
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

from an.bench.corpus import (
    BENCH_RENDER_KWARGS,
    Fixture,
    assert_render_path,
    iter_shot_dirs,
    staged_scene,
    visual_kinds,
)
from an.bench.png import read_png_dimensions

#: Copied for the render, but never these: they are the previous render's
#: output, and one of them silently extends this one's frame sequence.
#: Matched on the **basename**, at any depth — that is exactly what
#: ``shutil.ignore_patterns`` does, and it is why ``artifacts/shots`` cannot be
#: spelled here. See :data:`IGNORED_RELPATHS_ON_COPY`.
IGNORED_ON_COPY: tuple[str, ...] = (".an", "output", ".anima")

#: Excluded by their path **relative to the project root**, POSIX-spelled.
#: ``mall["shots"]`` is ``<project>/artifacts/shots``, and ``artifacts/``
#: itself is kept on purpose — it holds the audio cache, whose warm/cold state
#: this module records rather than destroys.
#:
#: Neither spelling belongs in :data:`IGNORED_ON_COPY`, and **both fail
#: silently**. ``shutil.ignore_patterns`` returns a closure handed the NAMES
#: inside one directory, which it ``fnmatch.filter``s — so ``"artifacts/shots"``
#: can never match anything (no name contains a separator) and a bare
#: ``"shots"`` would delete every directory of that name **anywhere** in the
#: tree, a character rig's included.
IGNORED_RELPATHS_ON_COPY: tuple[str, ...] = ("artifacts/shots",)

#: Where the renderer leaves its per-shot working tree inside the project.
RENDER_WORK_RELPATH: str = ".an/render_work"

#: How a shot's frames are named on disk. One constant rather than the literal
#: repeated at each glob site.
FRAME_PNG_GLOB: str = "frame_*.png"


def distinct_png_sizes(frames_dir: Path) -> tuple[tuple[int, int], ...]:
    """Every distinct ``(width, height)`` among a shot's frame PNGs, sorted.

    Read from each file's IHDR — 24 bytes per frame — so reading all of them
    costs nothing and catches what sampling one would miss: a sequence whose
    size changes partway through, which is what a half-applied supersample
    produces.

    **Recorded here, enforced elsewhere.** Rendering a fixture at a size the
    scene does not declare is a legitimate thing to do —
    ``misc/bench/wave3_ab.py`` patches ``runtime.js`` to ``resolution: k,
    autoDensity: false`` and drives :func:`capture_fixture` directly to measure
    the supersample — so this module reports what it saw and
    :mod:`an.bench.run` is where the *bench's* invariant is asserted.
    """
    return tuple(
        sorted({read_png_dimensions(p) for p in Path(frames_dir).glob(FRAME_PNG_GLOB)})
    )


@dataclass(slots=True)
class ShotCapture:
    """One rendered shot's artifacts."""

    shot_id: str
    frames_dir: Path
    scene_json: dict
    runtime_dir: Path
    frame_count: int
    #: The shot's declared duration, from the IR rather than from the staged
    #: scene, so the expected frame count is derived from the same number the
    #: renderer used.
    duration: float = 0.0
    #: The distinct pixel sizes actually on disk, from each PNG's IHDR. The
    #: independent half of a pair whose other half — ``SceneCapture.resolution``
    #: — comes from the staged scene's ``meta`` and never from a file. Empty
    #: only when the shot wrote no frames.
    frame_sizes: tuple[tuple[int, int], ...] = ()


@dataclass(slots=True)
class SceneCapture:
    """One fixture's whole render."""

    name: str
    source: str
    prepared: bool
    project_dir: Path
    mp4: Path
    shots: list[ShotCapture]
    resolution: tuple[int, int]
    fps: int
    duration: float
    n_declared_entity_refs: int
    visual_kinds: set[str]
    asset_resolution: list[dict]
    audio_cache: str
    wall_seconds: float
    determinism: dict = field(default_factory=dict)


class CaptureError(RuntimeError):
    """A capture could not produce something the metrics need."""


def _ignore_for(fixture_dir: Path):
    """``copytree``'s ``ignore``, for basenames **and** project-relative paths.

    ``copytree`` calls this once per directory with ``(that directory, the
    names in it)``, so a path-shaped exclusion has to be reconstructed from the
    directory it is handed — which is precisely why ``shutil.ignore_patterns``
    cannot express one, and why asking it to do so is a silent no-op rather
    than an error.
    """
    by_name = shutil.ignore_patterns(*IGNORED_ON_COPY)

    def ignore(path: str, names: list[str]) -> set[str]:
        try:
            here = Path(path).relative_to(fixture_dir).as_posix()
        except ValueError as e:
            # Never seen: `copytree` builds every path it passes here by
            # joining onto the one it was given. Raised rather than quietly
            # degrading to basenames-only, because under-excluding is the
            # defect this function exists to fix and it leaves no trace.
            raise CaptureError(
                f"stage_copy was asked about {path!r}, which is not under the "
                f"fixture root {fixture_dir}, so the path-relative exclusions "
                f"{IGNORED_RELPATHS_ON_COPY} could not be applied to it"
            ) from e
        prefix = "" if here == "." else f"{here}/"
        return set(by_name(path, names)) | {
            n for n in names if prefix + n in IGNORED_RELPATHS_ON_COPY
        }

    return ignore


def stage_copy(fixture_dir: Path, base: Path) -> Path:
    """Copy a fixture into ``base``, leaving the previous render behind.

    Split out of :func:`capture_fixture` so the exclusion is testable without
    rendering anything — which matters, because the failure it prevents is
    silent. ``frames/`` is never cleared and ffmpeg's image2 demuxer reads the
    contiguous ``frame_%06d.png`` run from 0, so a longer previous render is
    appended to this one's source leg and to nothing else.

    Two kinds of exclusion, because one kind cannot say both things:
    :data:`IGNORED_ON_COPY` by basename at any depth, and
    :data:`IGNORED_RELPATHS_ON_COPY` by path from the project root — which is
    the only way to drop ``artifacts/shots`` while keeping ``artifacts/audio``.
    """
    base.mkdir(parents=True, exist_ok=True)
    work_copy = base / fixture_dir.name
    if work_copy.exists():
        shutil.rmtree(work_copy)
    shutil.copytree(fixture_dir, work_copy, ignore=_ignore_for(fixture_dir))
    return work_copy


def _audio_cache_state(project_dir: Path) -> str:
    audio = project_dir / "artifacts" / "audio"
    return "warm" if audio.is_dir() and any(audio.iterdir()) else "cold"


def capture_fixture(
    name: str,
    fixture: Fixture,
    *,
    repo_root: Path,
    keep_render: Path | None = None,
) -> SceneCapture:
    """Render ``fixture`` in a throwaway copy and return its artifacts.

    The copy lives until the caller is done with it — the metrics read the
    frames — so this is a context-free function that leaves the tree in place
    and hands back the path. :func:`captured` is the scoped form.
    """
    from an.project import load
    from an.render import render

    fixture_dir = repo_root / fixture.path
    if not fixture_dir.is_dir():
        raise CaptureError(f"fixture {name!r} not found at {fixture_dir}")

    base = (
        Path(keep_render)
        if keep_render
        else Path(tempfile.mkdtemp(prefix=f"an-bench-{name}-"))
    )
    work_copy = stage_copy(fixture_dir, base)
    if fixture.prepare is not None:
        fixture.prepare(work_copy)

    audio_cache = _audio_cache_state(work_copy)
    project = load(work_copy)
    scene = project.scene

    started = time.perf_counter()
    output_mp4 = Path(render(project, **BENCH_RENDER_KWARGS))
    wall = time.perf_counter() - started

    work_dir = work_copy / RENDER_WORK_RELPATH
    shots: list[ShotCapture] = []
    all_kinds: set[str] = set()
    resolutions: set[tuple[int, int]] = set()
    # TIMELINE order, never directory order — `an/render.py` concatenates the
    # per-shot mp4s in `scene.timeline` order, and pairing source frames against
    # the decoded concat in any other order silently measures inter-shot motion.
    timeline_order = [s.id for s in scene.timeline]
    durations = {s.id: float(s.duration) for s in scene.timeline}
    for shot_id, shot_dir in iter_shot_dirs(work_dir, order=timeline_order):
        frames = shot_dir / "frames"
        pngs = sorted(frames.glob(FRAME_PNG_GLOB))
        js = staged_scene(shot_dir)
        all_kinds |= visual_kinds(js)
        meta = js.get("meta") or {}
        resolutions.add((int(meta.get("width", 0)), int(meta.get("height", 0))))
        shots.append(
            ShotCapture(
                shot_id=shot_id,
                frames_dir=frames,
                scene_json=js,
                runtime_dir=shot_dir / "runtime",
                frame_count=len(pngs),
                duration=durations.get(shot_id, 0.0),
                frame_sizes=distinct_png_sizes(frames),
            )
        )

    if not shots:
        raise CaptureError(
            f"fixture {name!r} produced no shots — looked under {work_dir}"
        )
    assert_render_path(name, fixture, all_kinds)
    if len(resolutions) > 1:
        raise CaptureError(
            f"fixture {name!r} rendered shots at mixed resolutions {sorted(resolutions)}; "
            "every ratio-form metric means something different at each, so the "
            "scene's row would pool two incompatible measurements"
        )

    return SceneCapture(
        name=name,
        source=fixture.path,
        prepared=fixture.prepare is not None,
        project_dir=work_copy,
        mp4=output_mp4,
        shots=shots,
        resolution=next(iter(resolutions)),
        fps=int(scene.meta.fps),
        duration=float(scene.meta.duration),
        n_declared_entity_refs=sum(len(s.entities) for s in scene.timeline),
        visual_kinds=all_kinds,
        asset_resolution=[
            dict(r) for s in shots for r in (s.scene_json.get("asset_resolution") or [])
        ],
        audio_cache=audio_cache,
        wall_seconds=round(wall, 3),
    )


def expected_frame_count(duration: float, fps: int) -> int:
    """The renderer's own frame-count expression, reused rather than restated.

    ``max(1, int(round(duration * fps)))`` — and Python 3's ``round`` is
    banker's rounding, so ``math.ceil`` or ``int(x + 0.5)`` silently disagrees
    on every half-frame duration.

    >>> expected_frame_count(2.5, 24)
    60
    >>> expected_frame_count(0.0, 24)
    1
    """
    return max(1, int(round(duration * fps)))


def cleanup(capture: SceneCapture) -> None:
    """Remove a capture's throwaway tree."""
    base = capture.project_dir.parent
    if base.exists() and base.name.startswith("an-bench-"):
        shutil.rmtree(base, ignore_errors=True)


def dirty_paths(repo_root: Path) -> list[str]:
    """`git status --porcelain` lines, so a capture can prove it touched nothing."""
    import subprocess

    out = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
    )
    return sorted(line for line in out.stdout.splitlines() if line.strip())
