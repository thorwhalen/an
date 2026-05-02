"""Silhouette rendering and comparison for the silhouette test.

The silhouette test (Disney/AnimSchool, see research §6.4) is a quick
read-test for character distinctiveness: fill the character with solid
black, and if you can still tell who's who, the design is strong.

This module renders an SVG to a binary silhouette PNG (Playwright is the
underlying rasterizer — already a project dep) and computes an IoU score
between two silhouettes after centering and scaling them to a common
canvas. A score near 1.0 means the silhouettes are nearly identical
(BAD — the characters are indistinguishable). A score below ~0.5 is
typically the sweet spot for visually-distinct characters.

>>> from an.characters import generate_default_mouths
>>> import tempfile, pathlib
>>> # Skip the doctest body — it requires Playwright with Chromium installed.
"""

from __future__ import annotations

import io
import tempfile
from pathlib import Path
from typing import Any


def _ensure_pil() -> Any:
    try:
        from PIL import Image, ImageOps
    except ImportError as e:  # pragma: no cover
        raise RuntimeError(
            "Pillow is required for silhouette comparison. "
            "Install with: pip install Pillow"
        ) from e
    return Image, ImageOps


def render_silhouette(
    svg_source: str | Path,
    out_png: str | Path,
    *,
    size: tuple[int, int] = (256, 256),
    background: str = "#ffffff",
) -> Path:
    """Render an SVG to a binary silhouette PNG (black on white).

    Uses Playwright/Chromium to rasterize, then PIL to threshold by alpha
    or luminance. Returns the output path.

    The output is RGB; the silhouette is filled with black (#000) and the
    background with the given color.
    """
    from playwright.sync_api import sync_playwright

    Image, _ImageOps = _ensure_pil()

    svg_path = Path(svg_source).resolve()
    if not svg_path.exists():
        # Allow raw SVG strings.
        if isinstance(svg_source, str) and svg_source.lstrip().startswith("<"):
            tmp = Path(tempfile.mkstemp(suffix=".svg")[1])
            tmp.write_text(svg_source, encoding="utf-8")
            svg_path = tmp
        else:
            raise FileNotFoundError(svg_source)

    width, height = size
    out = Path(out_png)
    out.parent.mkdir(parents=True, exist_ok=True)

    html = (
        "<!doctype html><html><head><style>"
        "html,body{margin:0;padding:0;background:transparent;}"
        "img{display:block;width:100%;height:100%;object-fit:contain;}"
        "</style></head><body>"
        f'<img src="file://{svg_path}"/>'
        "</body></html>"
    )

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": width, "height": height})
        page.set_content(html)
        page.wait_for_load_state("networkidle")
        png_bytes = page.screenshot(omit_background=True)
        browser.close()

    img = Image.open(io.BytesIO(png_bytes)).convert("RGBA")
    img = img.resize((width, height))
    # Threshold by alpha → opaque pixels become silhouette.
    alpha = img.split()[-1]
    mask = alpha.point(lambda v: 0 if v >= 128 else 255)  # 0=fg, 255=bg
    bg = Image.new("RGB", (width, height), background)
    fg = Image.new("RGB", (width, height), "#000000")
    composed = Image.composite(bg, fg, mask)
    composed.save(out)
    return out


def compare_silhouettes(
    a: str | Path, b: str | Path, *, size: tuple[int, int] = (256, 256)
) -> float:
    """Return IoU between two silhouette PNGs (0..1; lower = more distinct).

    Both images are resized to ``size``, converted to grayscale, thresholded
    at the midpoint, and the intersection-over-union of the foreground (dark)
    pixels is computed.

    >>> # Two identical silhouettes → IoU = 1.0; two empty → 0.0 (no overlap).
    >>> # Tested via test suite, not doctest, since it requires Playwright.
    """
    Image, ImageOps = _ensure_pil()

    def _mask(p: str | Path):
        im = Image.open(p).convert("L").resize(size)
        # Foreground = dark pixels (silhouette filled with black).
        return im.point(lambda v: 1 if v < 128 else 0)

    ma = _mask(a)
    mb = _mask(b)
    pix_a = list(ma.getdata())
    pix_b = list(mb.getdata())
    inter = sum(1 for x, y in zip(pix_a, pix_b) if x and y)
    union = sum(1 for x, y in zip(pix_a, pix_b) if x or y)
    if union == 0:
        return 0.0
    return inter / union
