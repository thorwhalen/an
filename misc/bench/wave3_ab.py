"""Render the Wave 3 supersampling A/B contact sheets.

Hand-run, not a test: it drives real Chromium renders, so it lives here beside
``misc/dev_sanity.py`` rather than under ``tests/``.

For each corpus scene it renders twice — once as the pipeline ships today, once
with PixiJS's ``resolution: k`` and ``autoDensity: false`` — then resolves the
supersampled frames back to the declared size three different ways and lays the
results out side by side::

    python misc/bench/wave3_ab.py                      # every scene, k=2
    python misc/bench/wave3_ab.py --k 3 saturated_outline
    python misc/bench/wave3_ab.py --out /tmp/ab        # somewhere else

Two sheets per scene:

``<scene>_full.png``
    The whole frame, three ways. Shows that nothing moved except the edges.
``<scene>_zoom.png``
    A nearest-neighbour magnification of the busiest edge region, chosen by
    edge-mask density rather than by eye. This is the one worth looking at —
    at 320x240 the difference is a pixel wide and invisible at 1:1.

The three resolves, and why they are the three:

``box``
    The exact ``k x k`` block mean. At an integer ratio this IS the supersample
    resolve, not an approximation of it, and it is what Wave 3 ships.
``lanczos``
    What the epic's brief specified. Included because it is measurably wrong
    here, and a picture makes the reason obvious in a way the number does not:
    the negative lobes ring on hard-edged flat fills.
``nearest``
    The control: keep one sub-sample per output pixel and discard the rest.
    It is **not** a reconstruction of the k=1 picture and does not measure the
    same — MSAA is applied at the supersampled scale, so decimating it lands a
    sharper, more aliased edge than rendering at 1x with MSAA does
    (`saturated_outline`: 2.114 px against k=1's 2.368 px). That ordering is
    the point of including it. It brackets ``box`` from below: discarding
    sub-samples aliases, averaging them resolves, and ``box`` sits above both
    k=1 and ``nearest`` because it is adding real gradation rather than
    softening — which is the claim the whole wave rests on.
"""

from __future__ import annotations

import argparse
import shutil
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

import numpy as np

from an.bench import corpus as bench_corpus
from an.bench import masks as M
from an.bench.metrics import edge_transition_width, frame_distinct_colours, luma_u8
from an.bench.paths import repo_root
from an.bench.png import read_png, to_rgb, write_png

#: The one line in ``runtime.js`` the supersample patch is anchored to. Pinned
#: as a literal for the same reason the AA lever pins ``antialias: true``: a
#: rename must fail loudly here rather than silently produce an un-patched
#: render that looks like a null result.
APP_OPEN = "app = new PIXI.Application({"

#: Magnification for the zoom sheet. Nearest-neighbour, so a pixel stays a
#: pixel — a smooth zoom would show the resampler's edges, not the render's.
DFLT_ZOOM = 6

#: Crop taken before magnification, in declared-resolution pixels.
DFLT_CROP = (72, 54)

#: Gutter between panels, and the label strip's height, in output pixels.
GUTTER = 8
LABEL_H = 14

DFLT_K = 2
RESOLVES = ("nearest", "box", "lanczos")


@contextmanager
def supersampled_runtime(k: int) -> Iterator[None]:
    """Render at ``k`` times the declared resolution, in a copy of the runtime.

    ``autoDensity: false`` is load-bearing and is the whole finding: with it
    ``true``, Pixi sets the canvas CSS size to the *logical* size and Chromium
    composites the k-times backbuffer down before the screenshot — a blind
    downscale with no filter choice and no record of having happened. The
    shipped ``runtime.js`` is never written to.
    """
    from an.adapters.cutout import render
    from an.adapters.cutout.runtime_files import runtime_dir

    if k == 1:
        yield
        return
    staged = Path(tempfile.mkdtemp(prefix="an-wave3-ab-")) / "runtime"
    shutil.copytree(runtime_dir(), staged)
    source = (staged / "runtime.js").read_text(encoding="utf-8")
    if source.count(APP_OPEN) != 1:
        shutil.rmtree(staged.parent, ignore_errors=True)
        raise SystemExit(
            f"expected exactly one {APP_OPEN!r} in runtime.js, found "
            f"{source.count(APP_OPEN)} — the patch anchor has moved."
        )
    (staged / "runtime.js").write_text(
        source.replace(
            APP_OPEN, f"{APP_OPEN}\n            resolution: {k}, autoDensity: false,"
        ),
        encoding="utf-8",
    )
    original = render.runtime_dir
    render.runtime_dir = lambda: staged
    try:
        yield
    finally:
        render.runtime_dir = original
        shutil.rmtree(staged.parent, ignore_errors=True)


def render_frames(scene: str, *, k: int) -> np.ndarray:
    """``(N, H, W, 3)`` uint8 for one corpus scene, rendered at ``k``."""
    from an.bench.capture import capture_fixture

    fixture = bench_corpus.DFLT_FIXTURES[scene]
    keep = Path(tempfile.mkdtemp(prefix=f"an-wave3-ab-{scene}-"))
    try:
        with supersampled_runtime(k):
            capture = capture_fixture(
                scene, fixture, repo_root=repo_root(), keep_render=keep
            )
        frames = [
            to_rgb(read_png(png))[..., :3]
            for shot in capture.shots
            for png in sorted(shot.frames_dir.glob("frame_*.png"))
        ]
        return np.stack(frames)
    finally:
        shutil.rmtree(keep, ignore_errors=True)


def resolve(frames: np.ndarray, k: int, how: str) -> np.ndarray:
    """Bring ``k``-times frames back to the declared size."""
    if k == 1:
        return frames
    if how == "box":
        n, h, w, c = frames.shape
        # The PRODUCT's resolve since an#58, not a fourth copy. This script's
        # whole job is to compare `box` against its alternatives, so it has to
        # be measuring the same `box` the renderer and the bench lever use.
        from an.adapters.cutout.supersample import block_mean_resolve

        return np.stack([block_mean_resolve(f, k) for f in frames])
    if how == "nearest":
        return frames[:, ::k, ::k, :]
    from PIL import Image

    n, h, w, _ = frames.shape
    return np.stack(
        [
            np.asarray(
                Image.fromarray(frames[i])
                .resize((w // k, h // k), Image.LANCZOS)
                .convert("RGB")
            )
            for i in range(n)
        ]
    )


def busiest_frame_and_crop(
    frames: np.ndarray, crop: tuple[int, int]
) -> tuple[int, int, int]:
    """``(frame_index, y, x)`` of the window holding the most edge pixels.

    Chosen by edge-mask density so the zoom is reproducible, rather than by
    someone picking a nice-looking corner.
    """
    cw, ch = crop
    n, h, w, _ = frames.shape
    best = (-1, 0, 0, 0.0)
    for i in range(n):
        mask = M.edge_mask(luma_u8(frames[i : i + 1]))[0].astype(np.int32)
        # Summed-area table, so every candidate window is an O(1) lookup.
        integral = mask.cumsum(0).cumsum(1)
        for y in range(0, max(1, h - ch), 4):
            for x in range(0, max(1, w - cw), 4):
                y1, x1 = min(y + ch, h) - 1, min(x + cw, w) - 1
                total = integral[y1, x1]
                if y:
                    total -= integral[y - 1, x1]
                if x:
                    total -= integral[y1, x - 1]
                if y and x:
                    total += integral[y - 1, x - 1]
                if total > best[3]:
                    best = (i, y, x, float(total))
    index, y, x, _ = best
    return (index if index >= 0 else 0), y, x


def _label(text: str, width: int) -> np.ndarray:
    """A dark strip carrying ``text``, ``(LABEL_H, width, 3)`` uint8."""
    from PIL import Image, ImageDraw

    img = Image.new("RGB", (width, LABEL_H), (24, 24, 27))
    ImageDraw.Draw(img).text((3, 2), text, fill=(228, 228, 231))
    return np.asarray(img)


def contact_sheet(panels: list[tuple[str, np.ndarray]]) -> np.ndarray:
    """Lay labelled panels out in a row on a dark background."""
    height = max(p.shape[0] for _, p in panels)
    width = sum(p.shape[1] for _, p in panels) + GUTTER * (len(panels) + 1)
    sheet = np.full((height + LABEL_H + GUTTER * 2, width, 3), 24, np.uint8)
    x = GUTTER
    for caption, panel in panels:
        h, w = panel.shape[:2]
        sheet[GUTTER : GUTTER + LABEL_H, x : x + w] = _label(caption, w)
        sheet[GUTTER + LABEL_H : GUTTER + LABEL_H + h, x : x + w] = panel
        x += w + GUTTER
    return sheet


def magnify(
    frame: np.ndarray, y: int, x: int, crop: tuple[int, int], zoom: int
) -> np.ndarray:
    cw, ch = crop
    patch = frame[y : y + ch, x : x + cw]
    return np.repeat(np.repeat(patch, zoom, axis=0), zoom, axis=1)


def stats(frames: np.ndarray) -> str:
    packed_ = frames.astype(np.uint32)
    packed_ = (packed_[..., 0] << 16) | (packed_[..., 1] << 8) | packed_[..., 2]
    return (
        f"edge {float(edge_transition_width(frames)[0]):.3f}px  "
        f"colours {float(frame_distinct_colours(packed_)):.0f}"
    )


def build(scene: str, *, k: int, out: Path, zoom: int, crop: tuple[int, int]) -> None:
    base = render_frames(scene, k=1)
    supersampled = render_frames(scene, k=k)
    variants = {"k=1 (today)": base} | {
        f"k={k} {how}": resolve(supersampled, k, how) for how in RESOLVES
    }

    index, y, x = busiest_frame_and_crop(base, crop)
    out.mkdir(parents=True, exist_ok=True)

    full = contact_sheet(
        [
            (f"{name}  {stats(frames)}", frames[index])
            for name, frames in variants.items()
        ]
    )
    write_png(out / f"{scene}_full.png", full)

    zoomed = contact_sheet(
        [
            (f"{name}  ({zoom}x)", magnify(frames[index], y, x, crop, zoom))
            for name, frames in variants.items()
        ]
    )
    write_png(out / f"{scene}_zoom.png", zoomed)

    print(f"{scene}: frame {index}, crop ({x},{y}) {crop[0]}x{crop[1]}")
    for name, frames in variants.items():
        print(f"    {name:16s} {stats(frames)}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("scenes", nargs="*", default=None)
    parser.add_argument("--k", type=int, default=DFLT_K)
    parser.add_argument("--zoom", type=int, default=DFLT_ZOOM)
    parser.add_argument(
        "--out", type=Path, default=repo_root() / "misc" / "bench" / "wave3_ab"
    )
    args = parser.parse_args()
    for scene in args.scenes or list(bench_corpus.DFLT_FIXTURES):
        build(scene, k=args.k, out=args.out, zoom=args.zoom, crop=DFLT_CROP)


if __name__ == "__main__":
    main()
