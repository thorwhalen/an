"""Whether an art package is one the compiler can actually render.

The artist-facing contract, checked offline. Wave 4 (#78) exists because the
previous check had no teeth in two separate ways:

- **It checked file existence only.** A part that was present but drew nothing
  passed, and then rendered invisibly — the one failure mode with no diagnostic
  anywhere in the pipeline (``misc/docs/wave4_research.md`` §4).
- **It returned a bespoke report type**, so the orchestrator's typed-error
  routing did not apply to any character problem. Findings here are
  :class:`an.verify._base.Finding`, the same type every verifier emits, with
  ``ir_path`` pointing at the file or slot that needs the fix.

Everything here is offline and free: no render, no browser, no network. That is
the point — an illustrator should be able to run it before delivering, and a
contract they cannot check themselves is a contract that gets them paid for work
that cannot land.
"""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Iterator

from an.characters.schema import (
    MOUTH_SHAPES,
    REQUIRED_PARTS,
    CharacterDescriptor,
)
from an.characters.svg_utils import SVG_NS, extract_pivots
from an.verify._base import Finding, VerificationReport

#: Elements an art package may not contain.
#:
#: Not a security perimeter — the renderer loads these files into a headless
#: browser we control — but a portability one: each of these makes a part render
#: differently, or not at all, depending on the rasteriser, and a part that
#: depends on script execution is not a drawing.
PROHIBITED_ELEMENTS: dict[str, str] = {
    "script": "executable content; a part is a drawing, not a program",
    "foreignObject": "embeds non-SVG content that most rasterisers drop",
    "image": "raster embed; inline it or ship it as its own part",
}

#: Elements that put ink on the canvas. A part containing none of these is
#: blank, whatever else it contains.
DRAWABLE_ELEMENTS: frozenset[str] = frozenset(
    {"path", "rect", "circle", "ellipse", "line", "polyline", "polygon", "text", "use"}
)

#: Severity for a problem that stops the part rendering correctly.
BLOCKING: str = "error"

#: Severity for a problem worth fixing that still renders.
ADVISORY: str = "warning"


def _localname(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def _iter_parts(char_dir: Path) -> Iterator[tuple[str, Path]]:
    """``(relative name, path)`` for every part SVG an art package ships."""
    parts = char_dir / "parts"
    if not parts.is_dir():
        return
    for path in sorted(parts.rglob("*.svg")):
        yield path.relative_to(parts).as_posix(), path


def _check_part(rel: str, path: Path, report: VerificationReport) -> None:
    """Open one part and report what would go wrong at render time."""
    ir_path = f"parts/{rel}"
    try:
        root = ET.parse(path).getroot()
    except ET.ParseError as e:
        report.add(
            BLOCKING,
            ir_path,
            f"{rel} is not parseable XML: {e}",
            "Re-export it. A malformed part makes the asset loader never settle, "
            "so the render hangs rather than failing (an#79).",
        )
        return

    descendants = [_localname(el.tag) for el in root.iter()]

    for name, why in PROHIBITED_ELEMENTS.items():
        if name in descendants:
            report.add(
                BLOCKING,
                ir_path,
                f"{rel} contains <{name}>: {why}",
                f"Remove the <{name}>.",
            )

    if not DRAWABLE_ELEMENTS.intersection(descendants):
        report.add(
            BLOCKING,
            ir_path,
            f"{rel} contains no drawable element "
            f"({', '.join(sorted(DRAWABLE_ELEMENTS))})",
            "Draw something, or delete the part and the slot that names it. A "
            "geometry-less part renders invisibly with no error anywhere — the "
            "failure this check exists for.",
        )

    view_box = root.get("viewBox")
    if not view_box:
        report.add(
            ADVISORY,
            ir_path,
            f"{rel} declares no viewBox, so its size depends on the rasteriser",
            "Add a viewBox matching the art's extent.",
        )
        return
    try:
        numbers = [float(v) for v in view_box.split()]
    except ValueError:
        numbers = []
    if len(numbers) != 4:
        report.add(
            BLOCKING, ir_path, f"{rel} has a malformed viewBox {view_box!r}", None
        )
        return
    if numbers[2] <= 0 or numbers[3] <= 0:
        report.add(
            BLOCKING,
            ir_path,
            f"{rel} has a zero-or-negative viewBox extent {view_box!r}",
            "A zero-dimension part makes the asset loader never settle (an#79).",
        )
        return

    width, height = root.get("width"), root.get("height")
    if width and height:
        try:
            declared = (float(width.rstrip("px")), float(height.rstrip("px")))
        except ValueError:
            return
        vb_aspect = numbers[2] / numbers[3]
        declared_aspect = declared[0] / declared[1] if declared[1] else 0
        if declared_aspect and abs(vb_aspect - declared_aspect) > 0.01 * vb_aspect:
            report.add(
                ADVISORY,
                ir_path,
                f"{rel} rasterises at {declared[0]:g}x{declared[1]:g} but its viewBox "
                f"is {numbers[2]:g}x{numbers[3]:g}, so the art is letterboxed inside "
                f"its own texture",
                "Make width/height match the viewBox extent. This is the defect "
                "an#75 fixed in `extract_part`; a hand-authored part can reintroduce it.",
            )


def validate_character(
    char_dir: str | Path, *, name: str | None = None
) -> VerificationReport:
    """Check an art package against the contract, offline.

    Reports a :class:`~an.verify._base.Finding` per problem: a missing or
    unparseable descriptor, absent required parts or mouth shapes, a part that
    draws nothing, a prohibited construct, a letterboxed part, a joint name
    colliding with a part id, and an unpopulated ``AssetSource``.

    >>> import tempfile, pathlib
    >>> with tempfile.TemporaryDirectory() as d:
    ...     report = validate_character(d, name="nobody")
    >>> report.passed
    False
    >>> any("character.json" in f.description for f in report.findings)
    True
    """
    directory = Path(char_dir)
    who = name or directory.name
    report = VerificationReport()

    if not directory.is_dir():
        report.add(BLOCKING, who, f"{directory} does not exist", None)
        return report

    descriptor: CharacterDescriptor | None = None
    desc_path = directory / "character.json"
    if not desc_path.exists():
        report.add(
            BLOCKING,
            "character.json",
            f"{who} has no character.json",
            "Run `an character new`, or write one; without it nothing knows the rig.",
        )
    else:
        try:
            descriptor = CharacterDescriptor.model_validate_json(
                desc_path.read_text(encoding="utf-8")
            )
        except (ValueError, json.JSONDecodeError) as e:
            report.add(
                BLOCKING, "character.json", f"{who}'s descriptor is invalid: {e}", None
            )

    parts_dir = directory / "parts"
    for part in REQUIRED_PARTS:
        if not (parts_dir / f"{part}.svg").exists():
            report.add(
                BLOCKING,
                f"parts/{part}.svg",
                f"{who} is missing required part {part!r}",
                "Draw it, or drop the slot that names it from the descriptor.",
            )
    for shape in MOUTH_SHAPES:
        if not (parts_dir / "mouth" / f"mouth_{shape}.svg").exists():
            report.add(
                BLOCKING,
                f"parts/mouth/mouth_{shape}.svg",
                f"{who} is missing mouth shape {shape!r}",
                "Run `an character mouths` to generate the default set.",
            )

    for rel, path in _iter_parts(parts_dir.parent):
        _check_part(rel, path, report)

    _check_joint_names(directory, descriptor, report)

    if descriptor is not None and descriptor.source is None:
        report.add(
            ADVISORY,
            "character.json",
            f"{who} declares no AssetSource",
            "Record where the art came from. `None` means 'we made this', which is "
            "a claim — and a licence defect is the only failure that reaches "
            "backwards through finished work.",
        )
    return report


def _check_joint_names(
    directory: Path,
    descriptor: CharacterDescriptor | None,
    report: VerificationReport,
) -> None:
    """A pivot id that is also a part id is ambiguous, structurally.

    `_find_by_id` prefers the `<g>` over a same-id `<circle>` precisely because
    this collision happens. That is a workaround for a missing namespace, and
    this is the check that makes the namespace real: if a drawing calls a joint
    `head` and also calls a part `head`, extraction picks one by a rule nobody
    reading the drawing can see.
    """
    canonical = next(directory.glob("*.svg"), None)
    if canonical is None:
        return
    try:
        pivots = set(extract_pivots(canonical))
    except (OSError, ET.ParseError, ValueError):
        return
    part_ids = {stem for stem, _ in _iter_parts(directory)}
    part_ids = {p.rsplit(".svg", 1)[0].rsplit("/", 1)[-1] for p in part_ids}
    for clash in sorted(pivots & part_ids):
        report.add(
            ADVISORY,
            f"{canonical.name}#{clash}",
            f"{clash!r} names both a joint and a part",
            "Rename the joint. Extraction resolves the collision by preferring "
            "the group, which is a rule the drawing does not show.",
        )


def format_report(report: VerificationReport, *, name: str) -> str:
    """A short human-readable rendering, for the CLI."""
    verdict = "OK" if report.passed else "FAILED"
    lines = [f"character {name!r}: {verdict} ({len(report.findings)} finding(s))"]
    for finding in report.findings:
        lines.append(f"  [{finding.severity}] {finding.ir_path}: {finding.description}")
        if finding.suggested_fix:
            lines.append(f"      -> {finding.suggested_fix}")
    return "\n".join(lines)


def render_contract() -> str:
    """The artist-facing spec, generated from the schema and the checks above.

    Every line here is read out of a live object: the required parts and mouth
    shapes from :mod:`an.characters.schema`, the slot and attachment layout from
    a freshly-built descriptor, the prohibitions from
    :data:`PROHIBITED_ELEMENTS`, and the drawable set from
    :data:`DRAWABLE_ELEMENTS`. Nothing is retyped, so the document and the
    validator cannot disagree.
    """
    descriptor = CharacterDescriptor(name="example")
    skin = descriptor.skins["default"]
    view_box = descriptor.view_box

    lines = [
        f"# Art package contract (character schema {descriptor.schema_version})",
        "",
        "Everything below is checked offline by `an character validate`.",
        "",
        "## Layout",
        "",
        "    <name>/",
        "      character.json        the descriptor: bones, slots, skins, asset_sets",
        "      <name>.svg            the canonical drawing, with a <g id='skeleton'>",
        "      parts/                one SVG per attachment",
        "        mouth/              the viseme set",
        "",
        "## Coordinate space",
        "",
        f"All positions are in view_box units; the default is {list(view_box)}.",
        "A bone's position is relative to its parent; an attachment's is relative",
        "to its bone. The whole rig is scaled by one uniform factor at compile",
        "time, so **aspect ratio is intrinsic to the art** — a part is placed and",
        "uniformly scaled, never stretched to fit a box.",
        "",
        "## Joints",
        "",
        'Draw a `<g id="skeleton">` of named `<circle>` elements; each bone names',
        "the joint it stands for, and those coordinates become the rig:",
        "",
    ]
    for bone in descriptor.bones:
        joint = bone.pivot or "(no joint; keeps its default placement)"
        lines.append(f"    {bone.name:<10} <- {joint}")
    lines += [
        "",
        "A joint id must not also be a part id.",
        "",
        "## Required parts",
        "",
        "    " + ", ".join(REQUIRED_PARTS),
        "",
        f"    mouth/: {', '.join('mouth_' + s for s in MOUTH_SHAPES)}",
        "",
        "## Slots and their attachments",
        "",
    ]
    for slot in sorted(descriptor.slots, key=lambda s: (s.draw_order, s.name)):
        available = sorted(skin.slots.get(slot.name, {}))
        lines.append(
            f"    {slot.name:<11} bone={slot.bone:<8} draw_order={slot.draw_order}  "
            f"{', '.join(available)}"
        )
    lines += [
        "",
        "A slot's name is its scene-graph node name, which is what `scene.md`",
        "targets. Attachment names are a separate namespace and follow the files.",
        "",
        "## Every part SVG",
        "",
        "  - declares a viewBox, and `width`/`height` matching its extent",
        "    (otherwise the art is letterboxed inside its own texture)",
        "  - contains at least one of: " + ", ".join(sorted(DRAWABLE_ELEMENTS)),
        "  - contains none of:",
    ]
    for name, why in PROHIBITED_ELEMENTS.items():
        lines.append(f"      <{name}> — {why}")
    lines += [
        "",
        "## Provenance",
        "",
        "Populate `source` unless the art is your own. A licence defect is the",
        "only failure that reaches backwards through finished work.",
    ]
    return "\n".join(lines)
