"""High-level entry points: build, validate, and inspect a character.

The :func:`new_character` function wires together fetching/wrapping art,
slicing it into per-part SVGs, generating the default mouth set, and
writing a complete character directory + ``character.json`` descriptor.

The :func:`validate_character` function checks completeness against
:data:`an.characters.REQUIRED_PARTS` and the 9-shape mouth set.

>>> import tempfile, json, pathlib
>>> # validate_character on an empty dir gives a list of complaints:
>>> with tempfile.TemporaryDirectory() as d:
...     report = validate_character(d, name='nobody')
...     report.passed
False
"""

from __future__ import annotations

import hashlib
import json
import shutil
import textwrap
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional
from xml.etree import ElementTree as ET


def _stable_hash(seed: str) -> int:
    """Deterministic across processes (unlike Python's `hash()` for strings).

    Python's built-in ``hash()`` is salt-randomized per interpreter run, so
    using it for color derivation produces different palettes each render.
    """
    digest = hashlib.md5(seed.encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big")


from an.characters.licenses import (
    DICEBEAR_STYLE_LICENSES,
    attribution_for,
    dicebear_source,
    requires_acknowledgement,
)
from an.ir.assets import AssetSource
from an.characters.dicebear import (
    DICEBEAR_DEFAULT_STYLE,
    fetch_dicebear,
    wrap_dicebear_for_an,
)
from an.characters.mouth_set import write_default_mouths
from an.characters.schema import (
    CharacterDescriptor,
    MOUTH_SHAPES,
    REQUIRED_PARTS,
)
from an.characters.svg_utils import (
    extract_part,
    extract_pivots,
    normalize_svg,
    write_svg,
    SVG_NS,
)


# Each part SVG is a self-contained content-centered drawing. The canonical
# `<name>.svg` (built via wrap_dicebear_for_an) is for human inspection /
# silhouette test; the renderer uses these per-part files directly.


@dataclass
class ValidationReport:
    """Result of :func:`validate_character`."""

    name: str
    directory: Path
    passed: bool = True
    missing_parts: list[str] = field(default_factory=list)
    missing_mouths: list[str] = field(default_factory=list)
    pivots_found: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def format(self) -> str:
        """Render a short human-readable report."""
        head = f"character '{self.name}' at {self.directory}: " + (
            "OK" if self.passed else "FAILED"
        )
        lines = [head]
        if self.missing_parts:
            lines.append("  missing body parts: " + ", ".join(self.missing_parts))
        if self.missing_mouths:
            lines.append("  missing mouth shapes: " + ", ".join(self.missing_mouths))
        if self.pivots_found:
            lines.append("  pivots: " + ", ".join(self.pivots_found))
        for n in self.notes:
            lines.append(f"  note: {n}")
        return "\n".join(lines)


def _check_style_is_usable(style: str, *, acknowledge_attribution: bool) -> None:
    """Refuse a style whose licence puts a duty on the user, unless acknowledged.

    Not paternalism: `an` produces videos its user ships, and CC BY obliges them
    to credit an artist they have never heard of, for art they did not know was
    third-party. Making that an explicit flag is the difference between an
    informed choice and an unknowing violation.

    The default style is CC0, so the common path never sees this.
    """
    if not requires_acknowledgement(style) or acknowledge_attribution:
        return
    lic = DICEBEAR_STYLE_LICENSES.get(style)
    owed = attribution_for(style) or "an attribution you must display"
    raise ValueError(
        f"style {style!r} is licensed {lic.license if lic else 'UNVERIFIED'}, "
        "which obliges whoever ships the rendered video to credit the artist. "
        "Accept that duty with acknowledge_attribution=True (or "
        "--acknowledge-attribution on the CLI); the record is "
        "then written to the character's `source` field and `an credits` will "
        f"render it. What you would owe:\n  {owed}\n"
        f"For no obligation at all, use the default style "
        f"({DICEBEAR_DEFAULT_STYLE!r}, CC0)."
    )


def new_character(
    out_dir: str | Path,
    *,
    name: str,
    seed: Optional[str] = None,
    style: str = DICEBEAR_DEFAULT_STYLE,
    voice_ref: Optional[str] = None,
    use_dicebear: bool = True,
    acknowledge_attribution: bool = False,
    overwrite: bool = False,
) -> Path:
    """Build a complete character on disk.

    Steps:

    1. Fetch a DiceBear avatar (skip if ``use_dicebear=False`` — useful for
       offline tests).
    2. Wrap it into the canonical ``an`` cutout SVG (skeleton + illustration
       groups), saved as ``<name>.svg``.
    3. Slice each part into ``parts/<part>.svg``.
    4. Write the 9-shape default mouth set into ``parts/mouth/``.
    5. Synthesize a few derived parts (open/closed eyes, brows) so the
       character is complete out of the box.
    6. Emit a ``character.json`` descriptor.

    Returns the path to the created ``character.json``.

    Raises :class:`FileExistsError` if ``out_dir/name`` already exists and
    ``overwrite=False``.
    """
    out = Path(out_dir) / name
    if out.exists():
        if not overwrite:
            raise FileExistsError(out)
        shutil.rmtree(out)
    out.mkdir(parents=True)
    parts_dir = out / "parts"
    parts_dir.mkdir()
    mouth_dir = parts_dir / "mouth"
    mouth_dir.mkdir()

    # Step 1 & 2: source art
    seed_used = seed or name
    metadata: dict[str, object] = {"art_provenance": "fallback_geometric"}
    source: AssetSource | None = None
    if use_dicebear:
        _check_style_is_usable(style, acknowledge_attribution=acknowledge_attribution)
        try:
            avatar = fetch_dicebear(seed_used, style=style)
            metadata = {
                "art_provenance": "dicebear",
                "dicebear_style": style,
                "dicebear_seed": seed_used,
            }
            source = dicebear_source(style, seed=seed_used)
        except RuntimeError as e:
            avatar = _fallback_face_svg(seed_used)
            metadata = {
                "art_provenance": "fallback_geometric",
                "dicebear_error": str(e),
            }
    else:
        avatar = _fallback_face_svg(seed_used)

    canonical = wrap_dicebear_for_an(avatar, name=name)
    canonical_path = out / f"{name}.svg"
    canonical_path.write_text(canonical, encoding="utf-8")

    # Step 3: write self-contained per-part SVGs (centered, sized to fill
    # their own canvas). Done independently of the canonical for clean
    # texture loading in Pixi.
    tree = normalize_svg(canonical_path)
    pivots = extract_pivots(tree)
    skin, clothing, hair = _palette_for_seed(seed_used)
    _write_head_part(parts_dir / "head.svg", avatar)
    _write_torso_part(parts_dir / "torso.svg", clothing=clothing, accent=hair)
    _write_arm_part(parts_dir / "arm_l.svg", side="l", color=clothing)
    _write_arm_part(parts_dir / "arm_r.svg", side="r", color=clothing)
    _write_leg_part(parts_dir / "leg_l.svg", side="l", color="#3a3a4a")
    _write_leg_part(parts_dir / "leg_r.svg", side="r", color="#3a3a4a")

    # Step 4: default mouths
    write_default_mouths(mouth_dir)

    # Step 5: derived parts (eyes, brows)
    _synthesize_eye_open(parts_dir / "eye_l_open.svg", side="l")
    _synthesize_eye_closed(parts_dir / "eye_l_closed.svg", side="l")
    _synthesize_eye_open(parts_dir / "eye_r_open.svg", side="r")
    _synthesize_eye_closed(parts_dir / "eye_r_closed.svg", side="r")
    _synthesize_brow(parts_dir / "brow_l.svg", side="l")
    _synthesize_brow(parts_dir / "brow_r.svg", side="r")

    # Step 6: descriptor
    descriptor = CharacterDescriptor(
        name=name,
        display_name=name.title(),
        voice_ref=voice_ref,
        source_svg=f"{name}.svg",
        metadata={**metadata, "pivots_detected": list(pivots.keys())},
        source=source,
    )
    desc_path = out / "character.json"
    desc_path.write_text(descriptor.model_dump_json(indent=2), encoding="utf-8")
    return desc_path


def validate_character(
    char_dir: str | Path, *, name: Optional[str] = None
) -> ValidationReport:
    """Check that a character directory is complete and well-formed.

    Looks for: ``character.json``, all :data:`REQUIRED_PARTS` under
    ``parts/``, all 9 mouth shapes under ``parts/mouth/``, and at least one
    pivot in the canonical SVG (if one exists).
    """
    d = Path(char_dir)
    report = ValidationReport(name=name or d.name, directory=d)
    if not d.exists() or not d.is_dir():
        report.passed = False
        report.notes.append("directory does not exist")
        return report

    desc_path = d / "character.json"
    if not desc_path.exists():
        report.passed = False
        report.notes.append("no character.json")
    else:
        try:
            CharacterDescriptor.model_validate_json(
                desc_path.read_text(encoding="utf-8")
            )
        except Exception as e:
            report.passed = False
            report.notes.append(f"character.json invalid: {e}")

    parts = d / "parts"
    for part in REQUIRED_PARTS:
        if not (parts / f"{part}.svg").exists():
            report.missing_parts.append(part)
    mouth_dir = parts / "mouth"
    for shape in MOUTH_SHAPES:
        if not (mouth_dir / f"mouth_{shape}.svg").exists():
            report.missing_mouths.append(f"mouth_{shape}")

    # Pivots are nice-to-have, not required for validation pass.
    candidate = next(d.glob("*.svg"), None)
    if candidate is not None:
        try:
            pivots = extract_pivots(candidate)
            report.pivots_found = sorted(pivots.keys())
        except Exception as e:
            report.notes.append(f"could not parse {candidate.name}: {e}")

    if report.missing_parts or report.missing_mouths:
        report.passed = False
    return report


# -----------------------------------------------------------------------------
# Tiny SVG synthesizers for derived parts (used as offline-friendly defaults)
# -----------------------------------------------------------------------------


#: Hand-picked skin tones in the typical illustrative cartoon range
#: (warm pale → deep brown). Picked so any pair tends to read as distinct.
_SKIN_TONES: tuple[str, ...] = (
    "#fbe1c1",  # pale warm
    "#f4c89a",  # peach
    "#e8b687",  # tan
    "#d8a47f",  # warm tan
    "#c08a5a",  # brown
    "#8b5a3b",  # deep brown
    "#fce0c8",  # very pale
    "#f1c9a5",  # neutral light
)

#: Hair tones (dark earth + a couple of stylized colors).
_HAIR_TONES: tuple[str, ...] = (
    "#1a1a1a",  # near-black
    "#3b2a1a",  # dark brown
    "#5e3a1f",  # brown
    "#a8743f",  # ginger
    "#d4a017",  # blonde
    "#3a2a40",  # dark stylized purple
)


def _fallback_face_svg(seed: str) -> str:
    """Tiny fallback face SVG used when DiceBear is unavailable.

    Deterministic: same seed → same face (skin tone + hair color picked
    from hand-curated palettes via stable hash). Crucially: NO eyes /
    brows / mouth baked in — those are added by the overlay slots so
    they can blink and lip-sync. Phase 11d fix for the "four eyes" bug.
    """
    h = _stable_hash(seed)
    skin = _SKIN_TONES[h % len(_SKIN_TONES)]
    hair = _HAIR_TONES[(h >> 16) % len(_HAIR_TONES)]
    return textwrap.dedent(
        f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 80 80">
          <circle cx="40" cy="44" r="28" fill="{skin}"/>
          <path d="M 12 36 Q 40 4 68 36 L 60 24 L 40 14 L 20 24 Z" fill="{hair}"/>
        </svg>"""
    )


def _synthesize_eye_open(path: Path, *, side: str) -> Path:
    svg = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<svg xmlns="{SVG_NS}" viewBox="0 0 64 32" width="64" height="32">'
        f'<g id="eye_{side}_open">'
        '<ellipse cx="32" cy="16" rx="14" ry="10" fill="#ffffff" stroke="#222" stroke-width="2"/>'
        '<circle cx="32" cy="16" r="5" fill="#1a1a1a"/>'
        "</g></svg>"
    )
    path.write_text(svg, encoding="utf-8")
    return path


def _synthesize_eye_closed(path: Path, *, side: str) -> Path:
    svg = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<svg xmlns="{SVG_NS}" viewBox="0 0 64 32" width="64" height="32">'
        f'<g id="eye_{side}_closed">'
        '<path d="M 18 18 Q 32 24 46 18" stroke="#222" stroke-width="3" fill="none" stroke-linecap="round"/>'
        "</g></svg>"
    )
    path.write_text(svg, encoding="utf-8")
    return path


def _palette_for_seed(seed: str) -> tuple[str, str, str]:
    """Return ``(skin, clothing, hair)`` deterministic from ``seed``.

    Picks from a small, hand-tuned set so two characters from different
    seeds tend to look distinct.
    """
    palettes = (
        ("#f4c89a", "#3a6ea5", "#3b2a1a"),
        ("#d8a47f", "#a83249", "#1a1a1a"),
        ("#fbe1c1", "#2e7d4f", "#a8743f"),
        ("#e8c39e", "#d97706", "#5e3a1f"),
        ("#f1c9a5", "#7a8fb5", "#3a2a20"),
    )
    idx = _stable_hash(seed) % len(palettes)
    return palettes[idx]


def _write_head_part(path: Path, avatar_svg: str) -> Path:
    """Write the avatar SVG verbatim as the head part.

    The avatar's intrinsic viewBox (whether DiceBear's 762×762 or our 80×80
    fallback) is what Pixi maps to the sprite dimensions at render time, so
    no rewriting/rescaling is needed.
    """
    if not avatar_svg.lstrip().startswith("<?xml"):
        avatar_svg = '<?xml version="1.0" encoding="UTF-8"?>\n' + avatar_svg.lstrip()
    path.write_text(avatar_svg, encoding="utf-8")
    return path


def _write_torso_part(path: Path, *, clothing: str, accent: str) -> Path:
    """A 256x256 torso SVG: rounded rect with a slight collar accent."""
    svg = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<svg xmlns="{SVG_NS}" viewBox="0 0 256 256" width="256" height="256">'
        '<g id="torso">'
        f'<rect x="20" y="20" width="216" height="216" rx="40" ry="40" '
        f'fill="{clothing}" stroke="#222" stroke-width="6"/>'
        f'<path d="M 96 20 Q 128 60 160 20" stroke="{accent}" '
        f'stroke-width="6" fill="none"/>'
        "</g></svg>"
    )
    path.write_text(svg, encoding="utf-8")
    return path


def _write_arm_part(path: Path, *, side: str, color: str) -> Path:
    """A 64x256 arm SVG with hand at the bottom."""
    svg = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<svg xmlns="{SVG_NS}" viewBox="0 0 64 256" width="64" height="256">'
        f'<g id="arm_{side}">'
        f'<rect x="14" y="0" width="36" height="216" rx="18" ry="18" '
        f'fill="{color}" stroke="#222" stroke-width="4"/>'
        f'<circle cx="32" cy="232" r="20" fill="#f1c9a5" stroke="#222" '
        f'stroke-width="4"/>'
        "</g></svg>"
    )
    path.write_text(svg, encoding="utf-8")
    return path


def _write_leg_part(path: Path, *, side: str, color: str) -> Path:
    """A 80x256 leg SVG with shoe at the bottom."""
    svg = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<svg xmlns="{SVG_NS}" viewBox="0 0 80 256" width="80" height="256">'
        f'<g id="leg_{side}">'
        f'<rect x="20" y="0" width="40" height="220" rx="6" ry="6" '
        f'fill="{color}"/>'
        f'<ellipse cx="40" cy="232" rx="32" ry="18" fill="#1a1a1a"/>'
        "</g></svg>"
    )
    path.write_text(svg, encoding="utf-8")
    return path


def _synthesize_brow(path: Path, *, side: str) -> Path:
    tilt = 4 if side == "l" else -4
    svg = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<svg xmlns="{SVG_NS}" viewBox="0 0 80 24" width="80" height="24">'
        f'<g id="brow_{side}">'
        f'<path d="M 8 {12 + tilt} Q 40 4 72 {12 - tilt}" '
        'stroke="#3a2a20" stroke-width="6" fill="none" stroke-linecap="round"/>'
        "</g></svg>"
    )
    path.write_text(svg, encoding="utf-8")
    return path
