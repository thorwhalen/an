"""SVG manipulation: namespace-aware DOM helpers using stdlib ``xml.etree``.

Pure stdlib so the package stays dependency-light. Operations supported:

- :func:`promote_inkscape_labels_to_ids` — copy ``inkscape:label`` to ``id``
  on each group, since Inkscape doesn't auto-promote labels (a 2008-vintage
  bug; see research §1.2).
- :func:`normalize_svg` — promote labels, ensure a viewBox is set, return
  the parsed ``ElementTree``.
- :func:`extract_pivots` — read the ``<g id="skeleton">`` group of named
  ``<circle>`` elements and return ``{name: (cx, cy)}``.
- :func:`extract_part` — emit a standalone SVG containing only the named
  group. By default it writes a viewBox **cropped** to the part's own bbox
  while copying the parent's ``width``/``height``, which letterboxes the part
  under ``preserveAspectRatio="xMidYMid meet"`` (see #75). The crop rect's
  parent-space origin survives as the viewBox's first two numbers.
- :func:`write_svg` — pretty-print an ``ElementTree`` (or ``Element``) to
  disk with the SVG namespace set as the default.

>>> raw = '''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">
...   <g id="skeleton"><circle id="neck" cx="50" cy="40" r="2"/></g>
...   <g id="illustration"><g id="head"><circle cx="50" cy="40" r="20"/></g></g>
... </svg>'''
>>> import io
>>> tree = normalize_svg(io.StringIO(raw))
>>> extract_pivots(tree)
{'neck': (50.0, 40.0)}
>>> part = extract_part(tree, 'head')
>>> b'<g id="head"' in write_svg(part)
True
"""

from __future__ import annotations

import io
import re
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET


SVG_NS = "http://www.w3.org/2000/svg"
INKSCAPE_NS = "http://www.inkscape.org/namespaces/inkscape"
SODIPODI_NS = "http://sodipodi.sourceforge.net/DTD/sodipodi-0.0.dtd"
XLINK_NS = "http://www.w3.org/1999/xlink"

ET.register_namespace("", SVG_NS)
ET.register_namespace("inkscape", INKSCAPE_NS)
ET.register_namespace("sodipodi", SODIPODI_NS)
ET.register_namespace("xlink", XLINK_NS)

_SVG_TAG = f"{{{SVG_NS}}}svg"
_G_TAG = f"{{{SVG_NS}}}g"
_CIRCLE_TAG = f"{{{SVG_NS}}}circle"
_INK_LABEL = f"{{{INKSCAPE_NS}}}label"

# Strict id-safe identifier: spaces become underscores, then keep [A-Za-z0-9_-].
_ID_BAD_CHAR = re.compile(r"[^A-Za-z0-9_\-]+")


def _parse(source: Any) -> ET.ElementTree:
    """Parse ``source`` (path, file-like, or already-parsed ``ElementTree``)."""
    if isinstance(source, ET.ElementTree):
        return source
    if isinstance(source, ET.Element):
        return ET.ElementTree(source)
    if isinstance(source, (str, Path)) and Path(str(source)).exists():
        return ET.parse(str(source))
    if hasattr(source, "read"):
        return ET.parse(source)
    if isinstance(source, str):
        return ET.ElementTree(ET.fromstring(source))
    raise TypeError(f"unsupported svg source: {type(source).__name__}")


def _label_to_id(label: str) -> str:
    """Convert an Inkscape label to an id-safe string."""
    cleaned = label.strip().replace(" ", "_")
    cleaned = _ID_BAD_CHAR.sub("", cleaned)
    return cleaned or "unnamed"


def promote_inkscape_labels_to_ids(tree: ET.ElementTree) -> int:
    """Copy ``inkscape:label`` to ``id`` on each group missing an id.

    Returns the number of groups updated.

    Inkscape stores the user-visible name in the ``inkscape:label`` attribute
    and does NOT promote it to ``id`` on save. This is a long-standing UX
    issue (Inkscape bug #243383); the workaround is to promote at parse time.
    """
    n = 0
    for g in tree.iter(_G_TAG):
        if g.get("id"):
            continue
        label = g.get(_INK_LABEL)
        if label:
            g.set("id", _label_to_id(label))
            n += 1
    return n


def _ensure_viewbox(tree: ET.ElementTree, fallback: str = "0 0 1024 1024") -> str:
    """Ensure the root SVG has a viewBox attribute; return the resolved value.

    If the root has only ``width``/``height``, derive a viewBox from those.
    """
    root = tree.getroot()
    vb = root.get("viewBox")
    if vb:
        return vb
    w = root.get("width")
    h = root.get("height")
    if w and h:
        # Strip units (Inkscape may emit "100mm").
        w_num = re.sub(r"[^\d.\-]", "", w) or "1024"
        h_num = re.sub(r"[^\d.\-]", "", h) or "1024"
        vb = f"0 0 {w_num} {h_num}"
    else:
        vb = fallback
    root.set("viewBox", vb)
    return vb


def normalize_svg(
    source: Any, *, fallback_viewbox: str = "0 0 1024 1024"
) -> ET.ElementTree:
    """Promote Inkscape labels to ids and ensure a viewBox is set.

    Returns the parsed ``ElementTree``. Idempotent: running it twice is a
    no-op on the second pass.
    """
    tree = _parse(source)
    promote_inkscape_labels_to_ids(tree)
    _ensure_viewbox(tree, fallback=fallback_viewbox)
    return tree


#: Attributes an SVG root may use to declare its rasterised size.
_SIZE_ATTRS: tuple[str, str] = ("width", "height")

#: Trailing units we accept on a width/height and ignore (SVG user units).
_UNIT_SUFFIXES: tuple[str, ...] = ("px", "pt", "cm", "mm", "in", "pc")


def _strip_units(value: str) -> float:
    """Parse an SVG length, tolerating a unit suffix. Percentages are refused."""
    text = value.strip()
    if text.endswith("%"):
        raise ValueError(f"percentage length {value!r} has no intrinsic size")
    for suffix in _UNIT_SUFFIXES:
        if text.endswith(suffix):
            text = text[: -len(suffix)]
            break
    return float(text)


def raster_size(source: Any) -> tuple[float, float]:
    """Return the ``(width, height)`` an SVG declares for its own raster.

    This is the size the browser rasterises the file at, which is what a
    ``Sprite`` then scales — **not** the extent of the drawn art. The two differ
    whenever :func:`extract_part` has cropped the viewBox while copying the
    parent's dimensions, which is the defect behind #75.

    Falls back to the viewBox extent when no ``width``/``height`` is declared,
    matching the browser.

    >>> raster_size('<svg xmlns="http://www.w3.org/2000/svg" '
    ...             'viewBox="0 0 10 20" width="100" height="100"/>')
    (100.0, 100.0)
    >>> raster_size('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 20"/>')
    (10.0, 20.0)
    """
    root = _parse(source).getroot()
    declared = [root.get(name) for name in _SIZE_ATTRS]
    if all(declared):
        return (_strip_units(declared[0]), _strip_units(declared[1]))
    view_box = root.get("viewBox")
    if not view_box:
        raise ValueError("SVG declares neither width/height nor a viewBox")
    parts = view_box.split()
    if len(parts) != 4:
        raise ValueError(f"malformed viewBox {view_box!r}")
    return (float(parts[2]), float(parts[3]))


def extract_pivots(
    source: Any, *, skeleton_id: str = "skeleton"
) -> dict[str, tuple[float, float]]:
    """Return ``{name: (cx, cy)}`` for every named ``<circle>`` under skeleton.

    Pivots use the Pose Animator convention: a ``<g id="skeleton">`` group
    sibling of the illustration, containing one ``<circle>`` per named joint.
    The circle's ``cx``/``cy`` is the pivot in the same coordinate system as
    the art (the SVG's viewBox).
    """
    tree = _parse(source)
    skeleton = _find_by_id(tree.getroot(), skeleton_id)
    if skeleton is None:
        return {}
    pivots: dict[str, tuple[float, float]] = {}
    for c in skeleton.iter(_CIRCLE_TAG):
        cid = c.get("id")
        if not cid:
            continue
        try:
            cx = float(c.get("cx", "0"))
            cy = float(c.get("cy", "0"))
        except ValueError:
            continue
        pivots[cid] = (cx, cy)
    return pivots


_DEFS_TAG = f"{{{SVG_NS}}}defs"
_RECT_TAG = f"{{{SVG_NS}}}rect"
_ELLIPSE_TAG = f"{{{SVG_NS}}}ellipse"
_PATH_TAG = f"{{{SVG_NS}}}path"
_PATH_NUMBER = re.compile(r"-?\d*\.?\d+(?:[eE][-+]?\d+)?")


def _bbox_union(
    a: tuple[float, float, float, float] | None,
    b: tuple[float, float, float, float] | None,
) -> tuple[float, float, float, float] | None:
    """Union of two ``(x_min, y_min, x_max, y_max)`` bounding boxes."""
    if a is None:
        return b
    if b is None:
        return a
    return (min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3]))


def _element_bbox(
    el: ET.Element,
) -> tuple[float, float, float, float] | None:
    """Approximate bbox of a primitive SVG element from its attributes.

    Handles ``<rect>``, ``<circle>``, ``<ellipse>``, and ``<path>``. Path
    bbox is derived from all numeric pairs in the ``d`` attribute — that
    overestimates curves with off-curve control points, but the result is
    safe (always contains the visible art) and sufficient for cropping.
    Returns ``None`` for elements with no inferable bbox.
    """
    tag = el.tag

    def _f(name: str, default: float = 0.0) -> float:
        try:
            return float(el.get(name, default))
        except (TypeError, ValueError):
            return default

    if tag == _RECT_TAG:
        x, y, w, h = _f("x"), _f("y"), _f("width"), _f("height")
        return (x, y, x + w, y + h)
    if tag == _CIRCLE_TAG:
        cx, cy, r = _f("cx"), _f("cy"), _f("r")
        return (cx - r, cy - r, cx + r, cy + r)
    if tag == _ELLIPSE_TAG:
        cx, cy, rx, ry = _f("cx"), _f("cy"), _f("rx"), _f("ry")
        return (cx - rx, cy - ry, cx + rx, cy + ry)
    if tag == _PATH_TAG:
        d = el.get("d") or ""
        nums = [float(n) for n in _PATH_NUMBER.findall(d)]
        if len(nums) < 2:
            return None
        xs = nums[0::2]
        ys = nums[1::2]
        if not xs or not ys:
            return None
        return (min(xs), min(ys), max(xs), max(ys))
    return None


def _subtree_bbox(
    el: ET.Element,
) -> tuple[float, float, float, float] | None:
    """Recursive bbox over all primitive descendants of ``el``."""
    box = _element_bbox(el)
    for child in el:
        box = _bbox_union(box, _subtree_bbox(child))
    return box


def extract_part(
    source: Any, part_id: str, *, crop_viewbox: bool = True, padding: float = 8.0
) -> ET.ElementTree:
    """Emit a standalone SVG tree containing only the group with the given id.

    Any top-level ``<defs>`` from the source is copied so the part can
    resolve gradient / pattern / filter references like
    ``fill="url(#some_gradient)"``. The matched group is appended unchanged.

    When ``crop_viewbox`` is True (the default), the new SVG's viewBox is
    cropped to the bounding box of the part's primitive content (rect /
    circle / ellipse / path) plus ``padding`` units on each side. This
    ensures the part fills its display rectangle when sized by the
    renderer; without it, a part drawn in a small region of a 1024×1024
    character canvas would be displayed at a fraction of the available
    pixels. Falls back to the source viewBox when no bbox can be derived.

    If no match is found, raises :class:`KeyError`.
    """
    tree = _parse(source)
    root = tree.getroot()
    target = _find_by_id(root, part_id)
    if target is None:
        raise KeyError(f"no <g id={part_id!r}> in source SVG")

    new_root = ET.Element(_SVG_TAG)
    src_viewbox = root.get("viewBox") or "0 0 1024 1024"
    viewbox = src_viewbox
    if crop_viewbox:
        bbox = _subtree_bbox(target)
        if bbox is not None:
            x_min, y_min, x_max, y_max = bbox
            x_min -= padding
            y_min -= padding
            x_max += padding
            y_max += padding
            w = max(x_max - x_min, 1.0)
            h = max(y_max - y_min, 1.0)
            viewbox = f"{x_min:.2f} {y_min:.2f} {w:.2f} {h:.2f}"
    new_root.set("viewBox", viewbox)
    if root.get("width"):
        new_root.set("width", root.get("width"))
    if root.get("height"):
        new_root.set("height", root.get("height"))
    for defs in root.findall(_DEFS_TAG):
        new_root.append(ET.fromstring(ET.tostring(defs)))
    cloned = ET.fromstring(ET.tostring(target))
    new_root.append(cloned)
    return ET.ElementTree(new_root)


def _find_by_id(root: ET.Element, target_id: str) -> ET.Element | None:
    """Depth-first search for the first element whose ``id`` matches.

    A character's parts are always ``<g>`` groups; the matching ``id`` may
    *also* appear on a skeleton pivot ``<circle>`` (e.g.
    ``<circle id="head">``). Prefer the ``<g>`` so part extraction picks
    up the art group, not the pivot point.
    """
    matches: list[ET.Element] = [el for el in root.iter() if el.get("id") == target_id]
    if not matches:
        return None
    for el in matches:
        if el.tag == _G_TAG:
            return el
    return matches[0]


def write_svg(tree_or_element: Any, path: str | Path | None = None) -> bytes:
    """Serialize an ``ElementTree`` or ``Element`` to bytes (and optionally disk).

    Always emits ``<?xml version="1.0" encoding="UTF-8"?>`` and the SVG
    namespace as the default, so the output is a valid standalone SVG.
    """
    if isinstance(tree_or_element, ET.ElementTree):
        root = tree_or_element.getroot()
    else:
        root = tree_or_element
    buf = io.BytesIO()
    # Don't pass default_namespace=SVG_NS — that forces ElementTree to
    # require every attribute be namespace-qualified, which fails on the
    # mixed input we get from wrapping/inlining external SVGs. The default
    # serialization preserves the input namespace mapping.
    ET.ElementTree(root).write(buf, encoding="utf-8", xml_declaration=True)
    data = buf.getvalue()
    if path is not None:
        Path(path).write_bytes(data)
    return data
