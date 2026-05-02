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
  group, preserving the parent SVG's viewBox.
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


def extract_part(source: Any, part_id: str) -> ET.ElementTree:
    """Emit a standalone SVG tree containing only the group with the given id.

    The returned tree's root is a fresh ``<svg>`` whose ``viewBox`` matches
    the source's. The matched group is appended unchanged. If no match is
    found, raises :class:`KeyError`.
    """
    tree = _parse(source)
    root = tree.getroot()
    target = _find_by_id(root, part_id)
    if target is None:
        raise KeyError(f"no <g id={part_id!r}> in source SVG")

    new_root = ET.Element(_SVG_TAG)
    new_root.set("viewBox", root.get("viewBox") or "0 0 1024 1024")
    if root.get("width"):
        new_root.set("width", root.get("width"))
    if root.get("height"):
        new_root.set("height", root.get("height"))
    # Deep-copy by serialize+parse so the new tree is independent.
    cloned = ET.fromstring(ET.tostring(target))
    new_root.append(cloned)
    return ET.ElementTree(new_root)


def _find_by_id(root: ET.Element, target_id: str) -> ET.Element | None:
    """Depth-first search for the first element whose ``id`` matches."""
    for el in root.iter():
        if el.get("id") == target_id:
            return el
    return None


def write_svg(
    tree_or_element: Any, path: str | Path | None = None
) -> bytes:
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
