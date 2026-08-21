"""The golden gate: committed frames, compared on **decoded pixels**.

Family B of the panel. Everything else in :mod:`an.bench` measures a property
of one render; this compares today's render against a picture a human looked at
and blessed, so it is the only part that can catch a change nobody predicted.

Four decisions here that are easy to get wrong in a way that still looks like
it works:

**The criterion is ``sha256`` of the decoded RGB array, never the file bytes.**
Chromium 1187 -> 1223 changed 144 of 144 PNG files and **zero** pixels. A
file-byte gate goes red on the first Playwright bump for a reason that has
nothing to do with animation quality — and, worse, trains people to re-bless
without looking.

**The path keys on the Chromium build ALONE** — no platform, no arch. Measured
across arm64 macOS, x86-64 Linux and arm64 Linux, across two different
SwiftShader JIT backends: zero differing pixels *and* zero differing PNG bytes.
Carrying the platform would force one committed copy per platform for no
information. What the convention keeps is its real benefit: a Playwright bump
becomes a **new path requiring a deliberate re-bless**, not a red test with no
explanation.

**Three different absences get three different gates.** "This scene declares no
golden frames", "goldens exist but not for this Chromium build" and "the
Chromium build could not be determined at all" are three different facts, and a
reader who cannot tell them apart cannot act on any of them. The last one
matters most: :func:`an.bench.environment.probe_browser` never raises, it
returns ``{"error": ...}`` — so without a distinct gate an un-probeable browser
reads exactly like a scene nobody has blessed yet.

**A comparison against a golden written in the same run is a tautology**, so a
``--bless`` run records family B as gated rather than as a pass. The row would
otherwise carry a perfect score that no code could ever have failed.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Sequence

from an.bench import png
from an.bench.paths import golden_dir, golden_path

#: Per-scene, per-build bless record, committed beside the frames. Per build
#: rather than per scene: a Playwright bump adds a new set of frames under new
#: names, and the old set stays valid for anyone still on the old build.
BLESS_MANIFEST_TEMPLATE: str = "bless-chromium{chromium_build}.json"

#: How many golden frames a scene must declare. Two, because one frame tests a
#: single instant and cannot notice a scene that renders its first frame
#: correctly and then stops.
REQUIRED_GOLDEN_FRAMES: int = 2

#: Gate names. Literals rather than an enum because they are written into the
#: ledger and read back by `an bench --compare` from rows written by older
#: registries, so their spelling is a wire format.
GATE_UNDECLARED: str = "golden_frames_undeclared"
GATE_ABSENT: str = "golden_absent_for_chromium_build"
GATE_BUILD_UNKNOWN: str = "chromium_build_unknown"
GATE_JUST_BLESSED: str = "blessed_this_run"

#: Gate names that appear in rows written BEFORE this module existed, and what
#: they meant. `an bench --compare` (an#40) reads old rows as fact, so the one
#: place a reader has to look is here rather than in a commit message. The
#: an#36 row carried a single `golden_absent` for both family-B keys, at a time
#: when the corpus had no goldens for ANY build — which is this module's
#: `GATE_ABSENT`, not its `GATE_UNDECLARED` (the fixtures did declare no times,
#: but the gate was not distinguishing the two).
RETIRED_GATES: dict[str, str] = {"golden_absent": GATE_ABSENT}


class GoldenError(RuntimeError):
    """A bless was refused, or a committed golden is unusable."""


@dataclass(frozen=True, slots=True)
class FrameRef:
    """One pinned golden frame, resolved against the render that just happened."""

    key: str
    time: float
    index: int
    shot_id: str
    #: Index WITHIN that shot's frame directory.
    local_index: int


def frame_key(index: int) -> str:
    """The filename stem for a frame, zero-padded so a directory listing sorts.

    Keyed on the frame **index**, not the pinned time: the index is what names
    the picture. A change to ``fps`` moves the index, which changes the path,
    which makes the golden absent and therefore *gated* — loud, and pointing at
    the right cause.

    >>> frame_key(7)
    'f0007'
    """
    return f"f{index:04d}"


def frame_index_for(time: float, *, fps: int, n_frames: int) -> int:
    """The frame a pinned time names, snapped to the nearest one.

    ``int(time * fps)`` is the trap: ``0.25 * 24`` is ``5.999999999999999`` in
    binary floating point, so the obvious spelling silently picks frame 5.

    >>> frame_index_for(0.25, fps=24, n_frames=12)
    6
    >>> frame_index_for(0.0, fps=24, n_frames=12)
    0
    """
    index = int(round(time * fps))
    if not 0 <= index < n_frames:
        raise GoldenError(
            f"pinned golden time {time}s resolves to frame {index} at {fps}fps, "
            f"but the scene rendered {n_frames} frames (0..{n_frames - 1}). "
            "A time past the end of the scene names no picture."
        )
    return index


def resolve_frames(capture: Any, times: Sequence[float]) -> list[FrameRef]:
    """Map each pinned time onto ``(global index, shot, index within that shot)``.

    Indices run over the scene's **concatenated** timeline — what the delivered
    mp4 shows — so a pinned time can land in the second shot, which is exactly
    what ``multi_shot``'s second golden does.
    """
    counts = [s.frame_count for s in capture.shots]
    total = sum(counts)
    refs: list[FrameRef] = []
    for time in times:
        index = frame_index_for(time, fps=capture.fps, n_frames=total)
        remaining = index
        for shot, count in zip(capture.shots, counts):
            if remaining < count:
                refs.append(
                    FrameRef(
                        key=frame_key(index),
                        time=float(time),
                        index=index,
                        shot_id=shot.shot_id,
                        local_index=remaining,
                    )
                )
                break
            remaining -= count
    return refs


def frame_png_path(capture: Any, ref: FrameRef) -> Path:
    """Where the renderer left the PNG for one resolved frame."""
    from an.adapters.cutout.render import DEFAULT_FRAME_PNG_PATTERN

    shot = next(s for s in capture.shots if s.shot_id == ref.shot_id)
    return shot.frames_dir / (DEFAULT_FRAME_PNG_PATTERN % ref.local_index)


def pixels_sha256(rgb: Any) -> str:
    """``sha256`` over the decoded pixels — **shape and dtype included**.

    Never over file bytes, for the reason in the module docstring. And never
    over the raw buffer alone either: ``tobytes()`` carries no shape, so a
    320x240 frame and a 240x320 one holding the same bytes hash identically,
    and issue #38's literal "the criterion is ``sha256(decoded RGB array)``"
    would report PASS on a transposed frame. Both orientations are live in this
    corpus. :func:`an.bench.metrics.golden_comparison` catches it separately via
    its ``shape_mismatch`` branch; this makes the digest agree with the gate
    rather than quietly disagreeing with it.

    >>> import numpy as np
    >>> a = np.arange(24, dtype=np.uint8)
    >>> pixels_sha256(a.reshape(2, 4, 3)) == pixels_sha256(a.reshape(4, 2, 3))
    False
    """
    import numpy as np

    arr = np.ascontiguousarray(rgb)
    digest = hashlib.sha256()
    digest.update(f"{arr.dtype.str}:{arr.shape}|".encode("ascii"))
    digest.update(arr.tobytes())
    return digest.hexdigest()


def manifest_path(scene: str, chromium_build: str, *, root: Path | None = None) -> Path:
    """Where one scene's bless record for one Chromium build lives."""
    return (
        golden_dir(root)
        / scene
        / BLESS_MANIFEST_TEMPLATE.format(chromium_build=chromium_build)
    )


def load_manifest(scene: str, chromium_build: str, *, root: Path | None = None) -> dict | None:
    """The committed bless record, or ``None`` when this scene has never been blessed."""
    path = manifest_path(scene, chromium_build, root=root)
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def chromium_build_of(environment: dict) -> str | None:
    """The Chromium build from an environment record, or ``None`` if unknown.

    ``probe_browser`` never raises — it reports an ``error`` key — so "we could
    not ask the browser" arrives here as a missing field rather than as an
    exception, and must not be confused with "nobody has blessed this scene".
    """
    render_side = (environment or {}).get("render_side") or {}
    build = render_side.get("chromium_build")
    return str(build) if build else None


def iter_committed(scene: str, chromium_build: str, *, root: Path | None = None) -> Iterator[Path]:
    """Every committed golden PNG for one scene and build, in sorted order."""
    scene_dir = golden_dir(root) / scene
    if not scene_dir.is_dir():
        return
    suffix = f"-chromium{chromium_build}.png"
    yield from sorted(p for p in scene_dir.glob(f"*{suffix}"))


def compare_scene(
    capture: Any,
    *,
    times: Sequence[float],
    chromium_build: str,
    root: Path | None = None,
) -> dict:
    """Compare today's render against the committed goldens for one scene.

    Returns a dict with ``state`` in ``{"measured", "gated", "unavailable"}``,
    the reduced numbers the ledger carries, and a per-frame record for
    provenance. The reduction is **worst frame wins**: ``identical`` is the
    conjunction and ``min_ssim_win8`` is the minimum, because a maximum or a
    mean lets one clean frame hide a broken one, and this metric's own name is
    "the worst small window".
    """
    from an.bench import metrics as M

    if not times:
        return {
            "state": "gated",
            "gate": GATE_UNDECLARED,
            "detail": (
                f"the {capture.name!r} fixture declares no golden_frames, so "
                "there is nothing to compare against. Declare two times on the "
                "Fixture and run `an bench --bless <reason>`."
            ),
        }

    frames: list[dict] = []
    refs = resolve_frames(capture, times)
    for ref in refs:
        golden = golden_path(capture.name, ref.key, chromium_build, root=root)
        record: dict[str, Any] = {
            "frame_key": ref.key,
            "time": ref.time,
            "index": ref.index,
            "shot_id": ref.shot_id,
            "golden": str(golden.relative_to(golden_dir(root).parent.parent))
            if golden.is_file()
            else None,
        }
        if not golden.is_file():
            record["state"] = "absent"
            frames.append(record)
            continue
        today = png.read_png(frame_png_path(capture, ref))
        try:
            blessed = png.read_png(golden)
        except png.PngFormatError as e:
            record["state"] = "unreadable"
            record["error"] = str(e)
            frames.append(record)
            continue
        record["state"] = "compared"
        record.update(M.golden_comparison(today, blessed))
        record["today_sha256"] = pixels_sha256(today)
        record["golden_sha256"] = pixels_sha256(blessed)
        frames.append(record)

    unreadable = [f for f in frames if f["state"] == "unreadable"]
    if unreadable:
        return {
            "state": "unavailable",
            "detail": (
                "a committed golden could not be decoded: "
                + "; ".join(f"{f['frame_key']}: {f['error']}" for f in unreadable)
                + ". A check that could not run is not evidence that anything is fine."
            ),
            "frames": frames,
        }
    absent = [f for f in frames if f["state"] == "absent"]
    if absent:
        return {
            "state": "gated",
            "gate": GATE_ABSENT,
            "detail": (
                f"no committed golden for Chromium {chromium_build} at "
                + ", ".join(f["frame_key"] for f in absent)
                + f" (scene {capture.name!r}). A Playwright bump is a NEW path and "
                "a deliberate re-bless, not a red test: run "
                "`an bench --scenes <scene> --bless '<why>'` after looking at the diff."
            ),
            "frames": frames,
        }
    # A frame whose golden was blessed at a different resolution has NO ssim:
    # `golden_comparison` returns `min_ssim_win8: None` there, and a `measured`
    # ledger value refuses a null. The identity is still knowable — a
    # differently-shaped picture is definitely not the same picture — so the
    # boolean is reported and the number is not, which is exactly the split the
    # metrics/tripwires blocks exist to express.
    mismatched = [f for f in frames if f.get("shape_mismatch")]
    return {
        "state": "measured",
        "identical": all(f["identical"] for f in frames),
        "min_ssim_win8": (
            None
            if mismatched
            else min(float(f["min_ssim_win8"]) for f in frames)
        ),
        "shape_mismatch": [
            {"frame_key": f["frame_key"], "shapes": f["shape_mismatch"]}
            for f in mismatched
        ]
        or None,
        "changed_px": max(int(f["changed_px"] or 0) for f in frames),
        "max_delta": max(int(f["max_delta"] or 0) for f in frames),
        "frames": frames,
    }


def bless_scene(
    capture: Any,
    *,
    times: Sequence[float],
    chromium_build: str | None,
    reason: str,
    git: dict,
    scene_contract_sha256: str,
    golden_note: str = "",
    root: Path | None = None,
) -> dict:
    """Write one scene's golden frames and its bless record. Refuses, loudly.

    Every refusal here is a case where writing the file would produce a gate
    that cannot fail:

    - **no reason** — a re-bless with no recorded reason is the same failure as
      a silently widened threshold, which is the named failure mode this whole
      wave exists to prevent;
    - **fewer than two frames** — one frame cannot notice a scene that renders
      its first instant correctly and then stops;
    - **a pixel-identical pair** — measured on ``promote_demo``: frame 0 and the
      ``duration/2`` frame differ by exactly zero pixels, so the obvious choice
      blesses one picture twice and the second golden tests nothing;
    - **an unknown Chromium build** — the path keys on it, so without it the
      frames would be written under a name no future run could look up.
    """
    import numpy as np

    if not reason.strip():
        raise GoldenError(
            "`--bless` needs a reason, and a blank one does not count. It is "
            "written into the bless record beside the frames, and it is the only "
            "thing that distinguishes a considered re-bless from a silently "
            "widened threshold."
        )
    if chromium_build is None:
        raise GoldenError(
            "the Chromium build could not be determined, and the golden path "
            "keys on it. `an.bench.environment.probe_browser` reports an error "
            "rather than raising, so check the `render_side.error` field of the "
            "environment record."
        )
    if len(times) < REQUIRED_GOLDEN_FRAMES:
        raise GoldenError(
            f"scene {capture.name!r} declares {len(times)} golden frame(s); "
            f"{REQUIRED_GOLDEN_FRAMES} are required. One frame cannot notice a "
            "scene that renders its first instant correctly and then stops."
        )

    refs = resolve_frames(capture, times)
    decoded = [png.read_png(frame_png_path(capture, ref)) for ref in refs]
    for i in range(len(decoded)):
        for j in range(i + 1, len(decoded)):
            if np.array_equal(decoded[i], decoded[j]):
                raise GoldenError(
                    f"scene {capture.name!r}: golden frames {refs[i].key} "
                    f"(t={refs[i].time}s) and {refs[j].key} (t={refs[j].time}s) "
                    "are pixel-identical, so the second one tests nothing. Pin a "
                    "time where something has actually moved — measured on "
                    "`promote_demo`, frame 0 and duration/2 differ by ZERO pixels."
                )

    keep = {golden_path(capture.name, r.key, chromium_build, root=root) for r in refs}
    # A re-bless that moves a pinned time writes a NEW filename and would
    # otherwise leave the old frame committed forever — a PNG in the tree that
    # nothing reads, indistinguishable from one that is still a gate. Removed
    # here and named in the record, so the deletion is reviewable in the diff.
    removed = [
        p.name
        for p in iter_committed(capture.name, chromium_build, root=root)
        if p not in keep
    ]
    for name in removed:
        (golden_dir(root) / capture.name / name).unlink()

    written: list[dict] = []
    for ref, rgb in zip(refs, decoded):
        out = golden_path(capture.name, ref.key, chromium_build, root=root)
        png.write_png(out, rgb)
        written.append(
            {
                "frame_key": ref.key,
                "time": ref.time,
                "index": ref.index,
                "shot_id": ref.shot_id,
                "local_index": ref.local_index,
                "sha256": pixels_sha256(rgb),
                "file": out.name,
                "bytes": out.stat().st_size,
            }
        )

    record = {
        "scene": capture.name,
        "source": capture.source,
        "chromium_build": chromium_build,
        "blessed_at": datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z"),
        "git": git,
        "reason": reason.strip(),
        "what_moves": golden_note,
        "fps": capture.fps,
        "resolution": list(capture.resolution),
        "shot_ids": [s.shot_id for s in capture.shots],
        "scene_contract_sha256": scene_contract_sha256,
        "criterion": (
            "sha256 of the DECODED RGB array, never the file bytes: Chromium "
            "1187 -> 1223 changes every PNG file and zero pixels."
        ),
        "frames": written,
        "removed": removed,
    }
    path = manifest_path(capture.name, chromium_build, root=root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return record
