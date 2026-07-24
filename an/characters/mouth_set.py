"""Generate the 9-shape default mouth set as parametric SVGs.

Produces drop-in art for ``mouth_a`` … ``mouth_h`` plus ``mouth_x``, with
shape parameters cribbed from Daniel Wolf's Rhubarb README (see research
§2.2). Every shape is rendered into the same per-mouth canvas
(``DEFAULT_MOUTH_VIEWBOX``, default 256×128) with a centered anchor, so
swapping attachments at runtime needs no per-shape offset.

The set is deliberately stylized — flat colors, bold strokes — so it reads
at the small sizes a typical cutout puppet uses (~30-40 px tall on a
1080p frame). It's not meant to compete with hand-drawn art; it's the
"always works" fallback.

>>> svgs = generate_default_mouths()
>>> sorted(svgs.keys())
['mouth_a', 'mouth_b', 'mouth_c', 'mouth_d', 'mouth_e', 'mouth_f', 'mouth_g', 'mouth_h', 'mouth_x']
>>> len(svgs['mouth_a']) > 100
True
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable

from an.characters.schema import MOUTH_SHAPES


#: Mouth canvas viewBox (width, height) — small per-shape and centered so the
#: anchor is always (0.5, 0.5).
DEFAULT_MOUTH_VIEWBOX: tuple[int, int] = (256, 128)

# Color palette (hex). Tuned for legibility at small sizes; override via
# ``generate_default_mouths(palette=...)``.
_DEFAULT_PALETTE: dict[str, str] = {
    "lip": "#9c3a3a",
    "fill": "#3a1414",
    "teeth": "#fafafa",
    "tongue": "#c25a5a",
    "skin": "#f1c9a5",  # not used directly; reserved for future overlays
}


# Per-shape geometry parameters. Each entry describes a quad-arc lens
# mouth, optionally with teeth and/or tongue overlays. Field meanings:
#   width:  full mouth width as a fraction of the canvas width
#   height: full mouth height as a fraction of the canvas height
#   open:   0..1 — how much to bulge the bottom lip downward
#   smile:  -1..1 — corner upturn (positive = smile)
#   teeth:  draw upper-teeth band (used for G/F-style "teeth on lip")
#   tongue: draw a tongue dot (used for H "long L")
_SHAPES: dict[str, dict[str, float | bool]] = {
    "x": {"width": 0.30, "height": 0.05, "open": 0.0, "smile": 0.05},  # idle
    "a": {"width": 0.32, "height": 0.07, "open": 0.05, "smile": 0.10},  # M/B/P
    "b": {
        "width": 0.36,
        "height": 0.18,
        "open": 0.4,
        "smile": 0.0,
        "teeth": True,
    },  # K/S/T/EE
    "c": {"width": 0.42, "height": 0.32, "open": 0.7, "smile": 0.0},  # EH/AE
    "d": {"width": 0.46, "height": 0.50, "open": 1.0, "smile": 0.0},  # AA wide-open
    "e": {"width": 0.34, "height": 0.42, "open": 0.85, "smile": -0.05},  # AO/ER rounded
    "f": {"width": 0.22, "height": 0.34, "open": 0.9, "smile": -0.10},  # UW/OW puckered
    "g": {
        "width": 0.36,
        "height": 0.14,
        "open": 0.2,
        "smile": 0.0,
        "teeth": True,
    },  # F/V teeth-on-lip
    "h": {
        "width": 0.30,
        "height": 0.13,
        "open": 0.15,
        "smile": 0.0,
        "tongue": True,
    },  # L
}


def _shape_svg(
    shape: str,
    *,
    canvas: tuple[int, int],
    palette: dict[str, str],
) -> str:
    """Render one mouth shape as an SVG document string."""
    cw, ch = canvas
    cx, cy = cw / 2.0, ch / 2.0
    s = _SHAPES[shape]

    half_w = (cw * float(s["width"])) / 2.0
    half_h = (ch * float(s["height"])) / 2.0
    smile_y = float(s["smile"]) * half_h * 1.5
    open_amt = float(s["open"])

    # Top lip: lifted slightly with smile offset; its control point is above.
    top_ctrl_y = cy + smile_y - half_h * 0.7
    # Bottom lip: openness pushes the control point down.
    bot_ctrl_y = cy + smile_y + half_h * (0.5 + 0.5 * open_amt)

    left_x = cx - half_w
    right_x = cx + half_w

    fill = palette.get("fill", _DEFAULT_PALETTE["fill"])
    lip = palette.get("lip", _DEFAULT_PALETTE["lip"])
    teeth = palette.get("teeth", _DEFAULT_PALETTE["teeth"])
    tongue = palette.get("tongue", _DEFAULT_PALETTE["tongue"])

    body = (
        f'<path d="M {left_x:.2f} {cy + smile_y:.2f} '
        f"Q {cx:.2f} {top_ctrl_y:.2f} {right_x:.2f} {cy + smile_y:.2f} "
        f'Q {cx:.2f} {bot_ctrl_y:.2f} {left_x:.2f} {cy + smile_y:.2f} Z" '
        f'fill="{fill}" stroke="{lip}" stroke-width="2" stroke-linejoin="round"/>'
    )

    overlays: list[str] = []
    if s.get("teeth"):
        # Upper-teeth strip just below the top lip.
        teeth_w = half_w * 1.2
        teeth_h = max(2.0, half_h * 0.18)
        teeth_y = cy + smile_y - teeth_h * 0.2
        overlays.append(
            f'<rect x="{cx - teeth_w / 2:.2f}" y="{teeth_y:.2f}" '
            f'width="{teeth_w:.2f}" height="{teeth_h:.2f}" '
            f'fill="{teeth}" rx="1.5"/>'
        )
    if s.get("tongue"):
        # Small tongue dot below midline.
        tongue_rx = half_w * 0.32
        tongue_ry = half_h * 0.40
        tongue_cy = cy + smile_y + half_h * 0.20
        overlays.append(
            f'<ellipse cx="{cx:.2f}" cy="{tongue_cy:.2f}" '
            f'rx="{tongue_rx:.2f}" ry="{tongue_ry:.2f}" '
            f'fill="{tongue}"/>'
        )

    body_id = f"mouth_{shape}"
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {cw} {ch}" '
        f'width="{cw}" height="{ch}">'
        f'<g id="{body_id}">'
        f"{body}"
        f"{''.join(overlays)}"
        "</g>"
        "</svg>"
    )


def generate_default_mouths(
    *,
    canvas: tuple[int, int] = DEFAULT_MOUTH_VIEWBOX,
    palette: dict[str, str] | None = None,
    shapes: Iterable[str] = MOUTH_SHAPES,
) -> dict[str, str]:
    """Return ``{"mouth_<letter>": <svg-string>, ...}`` for every shape.

    >>> svgs = generate_default_mouths()
    >>> 'mouth_x' in svgs and 'viewBox' in svgs['mouth_x']
    True
    """
    pal = dict(_DEFAULT_PALETTE)
    if palette:
        pal.update(palette)
    return {
        f"mouth_{shape}": _shape_svg(shape, canvas=canvas, palette=pal)
        for shape in shapes
    }


def write_default_mouths(
    out_dir: str | Path,
    *,
    canvas: tuple[int, int] = DEFAULT_MOUTH_VIEWBOX,
    palette: dict[str, str] | None = None,
    shapes: Iterable[str] = MOUTH_SHAPES,
) -> list[Path]:
    """Write the default mouth SVGs into ``out_dir`` (created if missing).

    Returns the list of paths written, sorted by shape order.
    """
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for name, svg in generate_default_mouths(
        canvas=canvas, palette=palette, shapes=shapes
    ).items():
        path = out / f"{name}.svg"
        path.write_text(svg, encoding="utf-8")
        written.append(path)
    return written
