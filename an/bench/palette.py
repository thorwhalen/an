"""Derive the set of colours a compiled shot declared — never hand-pin it.

``off_palette_pixel_fraction``'s whole meaning is "not one of the colours the
compiler declared for this shot". A hand-tuned palette constant would make it a
per-scene magic number, which is exactly what killed its predecessor
(``blend_pixel_fraction``: a 12-flat-fill, anti-aliasing-free frame read 2.46%
at ``K=10``, i.e. 4.8x the entire claimed signal).

So the palette is read out of the artifacts the browser actually loaded — the
staged ``scene.json`` and the staged SVG files beside it — never from a
re-compile. A re-compile can differ from what ran: a missing character
descriptor makes the compiler fall back to the procedural rig, and before
an#33 it did so without a word.

**Three things this module must get right, each a silent failure otherwise:**

1. ``parse_color`` is a **verbatim mirror of the runtime's rule**
   (``hex.padEnd(6,'0').slice(0,6)``), not a CSS parser. A CSS-correct 3-digit
   expander maps ``"#222"`` to ``0x222222``; the runtime paints ``0x222000``.
2. Some colours are **runtime constants, never present in the JSON** — the eye
   whites, the four mouth colours. A JSON-only sweep under-collects them and
   the metric reads high with no error anywhere.
3. Some ``visual.color`` values are **inert**: ``drawMouthShape`` never reads
   its node's colour, and every ``svg_sprite`` carries the ``#888888`` schema
   default. Collecting those over-collects.

Both directions are recorded in ``palette_sources`` so a reviewer can see which
half moved when the number does.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Iterable

#: The runtime's fallback when a `visual.color` is absent or not a string.
RUNTIME_DEFAULT_COLOUR: int = 0x888888

#: Painted by `makeEye` regardless of `visual.color`: the sclera fill and the
#: 0.6-alpha outline. The outline's alpha means it paints BLENDS, so it is a
#: lower bound on that node's contribution — the safe direction.
RUNTIME_EYE_COLOURS: tuple[int, ...] = (0xFFFFFF, 0x222222)

#: Painted by `drawMouthShape`: lip, fill, teeth, tongue. The mouth node's own
#: `visual.color` is never read.
RUNTIME_MOUTH_COLOURS: tuple[int, ...] = (0x6B2B2B, 0x2A1010, 0xFAFAFA, 0xB04848)

#: The runtime's fallback pupil colour when a `visual.color` is absent.
RUNTIME_EYE_PUPIL_DEFAULT: str = "#1a1a1a"

#: `visual.kind` values whose `visual.color` the runtime actually paints.
COLOURED_KINDS: frozenset[str] = frozenset({"rect", "ellipse"})

#: `visual.kind` values whose `visual.color` is inert.
INERT_COLOUR_KINDS: frozenset[str] = frozenset({"mouth", "svg_sprite", "sprite"})

#: SVG attribute values that name no colour.
_NON_COLOURS: frozenset[str] = frozenset(
    {"none", "transparent", "currentcolor", "inherit", ""}
)

_HEX_RE = re.compile(r"^#?[0-9a-fA-F]{3,8}$")
_STYLE_DECL_RE = re.compile(r"([a-zA-Z-]+)\s*:\s*([^;]+)")


def parse_color(value: Any) -> int:
    """The runtime's own colour rule, mirrored exactly.

    ``runtime.js``::

        function parseColor(s) {
            if (typeof s !== 'string') return 0x888888;
            const hex = s.startsWith('#') ? s.slice(1) : s;
            return parseInt(hex.padEnd(6, '0').slice(0, 6), 16);
        }

    Note what that is **not**: a 3-digit CSS shorthand expander. ``"#222"``
    pads to ``"222000"``, so the runtime paints ``0x222000`` and so must this.

    >>> hex(parse_color("#222"))
    '0x222000'
    >>> hex(parse_color("#ffffffaa"))
    '0xffffff'
    >>> hex(parse_color(None))
    '0x888888'
    """
    if not isinstance(value, str):
        return RUNTIME_DEFAULT_COLOUR
    hexpart = value[1:] if value.startswith("#") else value
    try:
        return int(hexpart.ljust(6, "0")[:6], 16)
    except ValueError:
        return RUNTIME_DEFAULT_COLOUR


def _iter_nodes(node: Any) -> Iterable[dict]:
    if isinstance(node, dict):
        yield node
        for child in node.get("children") or []:
            yield from _iter_nodes(child)


def _style_values(style: str) -> dict[str, str]:
    return {
        k.strip().lower(): v.strip() for k, v in _STYLE_DECL_RE.findall(style or "")
    }


def svg_colours(svg_path: Path) -> tuple[set[int], set[str]]:
    """Every colour literal an SVG paints, plus the tokens that could not be resolved.

    Parsed as XML rather than scraped with a regex, so ``style="fill:#abc"``
    and a ``display:none`` subtree are both handled — the first is a spelling a
    regex over ``fill="…"`` misses entirely, and the second contributes colours
    to a palette that never reach a pixel.

    An unresolvable token (a named colour, a ``url(#gradient)`` reference) is
    **returned rather than guessed**: guessing puts a wrong colour in the
    palette, and the metric then reads low with no error anywhere.
    """
    colours: set[int] = set()
    unresolved: set[str] = set()
    try:
        root = ET.parse(svg_path).getroot()
    except (ET.ParseError, OSError):
        return colours, {f"unparseable:{svg_path.name}"}

    def walk(el: Any, hidden: bool) -> None:
        style = _style_values(el.get("style", ""))
        el_hidden = (
            hidden or style.get("display") == "none" or el.get("display") == "none"
        )
        if not el_hidden:
            candidates = [
                el.get("fill"),
                el.get("stroke"),
                el.get("stop-color"),
                style.get("fill"),
                style.get("stroke"),
                style.get("stop-color"),
            ]
            for raw in candidates:
                if raw is None:
                    continue
                token = raw.strip()
                if token.lower() in _NON_COLOURS:
                    continue
                if _HEX_RE.match(token):
                    colours.add(parse_color(token))
                else:
                    unresolved.add(token)
        for child in el:
            walk(child, el_hidden)

    walk(root, False)
    return colours, unresolved


def palette_for_scene(scene_json: dict, *, runtime_dir: Path) -> dict:
    """The declared palette of one staged shot.

    ``scene_json`` is the **staged** compiled scene (what the browser loaded);
    ``runtime_dir`` is the directory it was served from, which is where its
    SVGs were staged.

    Returns a dict carrying the palette itself plus enough provenance to
    explain a move: which half of the derivation each colour came from, whether
    the palette is a superset of what was painted, and every token that could
    not be resolved.
    """
    colours: set[int] = set()
    sources = {"scene_json": 0, "runtime_constants": 0, "svg": 0}
    unresolved: set[str] = set()

    meta = scene_json.get("meta") or {}
    colours.add(parse_color(meta.get("background")))
    sources["scene_json"] += 1

    textures = ((scene_json.get("assets") or {}).get("textures")) or {}
    referenced_aliases: set[str] = set()

    for node in _iter_nodes(scene_json.get("scene") or {}):
        visual = node.get("visual")
        if not isinstance(visual, dict):
            continue
        kind = visual.get("kind")
        if kind in COLOURED_KINDS:
            colours.add(parse_color(visual.get("color") or "#888888"))
            sources["scene_json"] += 1
        elif kind == "eye":
            colours.add(parse_color(visual.get("color") or RUNTIME_EYE_PUPIL_DEFAULT))
            sources["scene_json"] += 1
            colours.update(RUNTIME_EYE_COLOURS)
            sources["runtime_constants"] += len(RUNTIME_EYE_COLOURS)
        elif kind == "mouth":
            # visual.color is deliberately NOT read: drawMouthShape ignores it.
            colours.update(RUNTIME_MOUTH_COLOURS)
            sources["runtime_constants"] += len(RUNTIME_MOUTH_COLOURS)
        elif kind in ("svg_sprite", "sprite"):
            # visual.color here is the #888888 schema default on every one.
            if visual.get("asset_id"):
                referenced_aliases.add(str(visual["asset_id"]))
            # Every key of every swap set can reach the screen mid-shot, so
            # all of them join the reference palette. (This read `viseme_assets`
            # until an#87 generalised the field; a stale key here degrades
            # SILENTLY — `or {}` — into an under-collected palette, which is
            # why `tests/test_bench_palette.py` pins a compiled scene's swap
            # aliases flowing through.)
            for key_map in (visual.get("asset_sets") or {}).values():
                for alias in (key_map or {}).values():
                    referenced_aliases.add(str(alias))

    # A viseme set declares all nine mouth shapes while a given render paints
    # only some, so the SVG half makes the palette a SUPERSET. That is the safe
    # direction — it can only make `off_palette_pixel_fraction` a lower bound —
    # but it must be recorded, not assumed.
    palette_is_superset = bool(referenced_aliases)
    for alias in sorted(referenced_aliases):
        asset = textures.get(alias)
        src = (asset or {}).get("src") if isinstance(asset, dict) else None
        if not src:
            unresolved.add(f"alias-without-src:{alias}")
            continue
        staged = runtime_dir / src
        if not staged.is_file():
            unresolved.add(f"unstaged:{src}")
            continue
        found, bad = svg_colours(staged)
        colours.update(found)
        sources["svg"] += len(found)
        unresolved.update(bad)

    return {
        "palette": sorted(colours),
        "palette_hex": ["#%06x" % c for c in sorted(colours)],
        "palette_sources": sources,
        "palette_is_superset": palette_is_superset,
        "unresolved_svg_colour_tokens": sorted(unresolved),
    }


#: Where the runtime's own hard-coded colours live, for the source cross-check.
RUNTIME_JS_RELPATH: str = "an/data/cutout_runtime/runtime.js"

_RUNTIME_LITERAL_RE = re.compile(r"0x([0-9a-fA-F]{6})\b")


def runtime_literal_colours(runtime_js: Path) -> set[int]:
    """Every 6-digit hex literal the runtime source paints.

    Used to cross-check :data:`RUNTIME_EYE_COLOURS` and
    :data:`RUNTIME_MOUTH_COLOURS` against the file that actually paints them,
    so adding a fifth mouth colour reddens a test instead of silently inflating
    ``off_palette_pixel_fraction``.
    """
    src = runtime_js.read_text(encoding="utf-8")
    painted: set[int] = set()
    for line in src.splitlines():
        if (
            "beginFill" in line
            or "lineStyle" in line
            or "_COLOR" in line
            or "_FILL" in line
        ):
            painted.update(int(m, 16) for m in _RUNTIME_LITERAL_RE.findall(line))
    return painted
