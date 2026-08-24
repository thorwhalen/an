"""High-level entry points: build and inspect a character.

The :func:`new_character` function wires together fetching/wrapping art,
slicing it into per-part SVGs, generating the default mouth set, and
writing a complete character directory + ``character.json`` descriptor.

Checking one is :mod:`an.characters.validate`'s job, not this module's — it
opens every part and reports :class:`an.verify._base.Finding` s, so a character
problem routes the way every other verifier's does (an#78).

>>> import tempfile
>>> with tempfile.TemporaryDirectory() as d:
...     descriptor_path = new_character(d, name='nobody', use_dicebear=False)
...     descriptor_path.parent.name, descriptor_path.name
('nobody', 'character.json')
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
from an.characters.mouth_set import (
    DEFAULT_MOUTH_VARIANTS,
    mouth_attachment_name,
    write_default_mouths,
)
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
    mouth_variants: Optional[dict[str, float]] = None,
    gaze: bool = True,
) -> Path:
    """Build a complete character on disk.

    ``gaze`` (an#99) adds the eye stack — sclera and pupil slots under each
    lid, a filled closed lid, the ``gaze_travel`` clamp — through
    :func:`add_gaze`, so `gaze_x`/`gaze_y` and the ambient saccades reach the
    pupils. Off, the eye is the single pre-stack drawing.

    ``mouth_variants`` (an#98) — ``{form: smile offset}`` — writes one more
    9-shape mouth set per form (``mouth_<shape>_<form>.svg``) and declares it
    as the ``viseme@<form>`` swap set, with its attachments in the default
    skin's ``mouth`` slot, so an expression preset preferring that form
    selects it. ``None`` means :data:`~an.characters.mouth_set.DEFAULT_MOUTH_VARIANTS`
    (happy, sad); ``{}`` means the neutral set only.

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

    # Step 4: default mouths, plus the form variants (an#98)
    variants = (
        DEFAULT_MOUTH_VARIANTS if mouth_variants is None else dict(mouth_variants)
    )
    write_default_mouths(mouth_dir, variants=variants)

    # Step 5: derived parts (eyes, brows)
    _synthesize_eye_open(parts_dir / "eye_l_open.svg", side="l")
    _synthesize_eye_closed(parts_dir / "eye_l_closed.svg", side="l")
    _synthesize_eye_open(parts_dir / "eye_r_open.svg", side="r")
    _synthesize_eye_closed(parts_dir / "eye_r_closed.svg", side="r")
    _synthesize_brow(parts_dir / "brow_l.svg", side="l")
    _synthesize_brow(parts_dir / "brow_r.svg", side="r")

    # Step 6: descriptor. `face_overlay` is DECLARED here (an#87): a DiceBear
    # avatar has its face baked into the head SVG, so the overlay face parts
    # and the viseme channel are suppressed by this fact — the compiler no
    # longer sniffs metadata.art_provenance, which is provenance again.
    descriptor = CharacterDescriptor(
        name=name,
        display_name=name.title(),
        voice_ref=voice_ref,
        source_svg=f"{name}.svg",
        face_overlay=metadata.get("art_provenance") != "dicebear",
        metadata={**metadata, "pivots_detected": list(pivots.keys())},
        source=source,
    )
    declare_mouth_variants(descriptor, variants)
    descriptor.metadata["seed"] = seed_used
    desc_path = out / "character.json"
    desc_path.write_text(descriptor.model_dump_json(indent=2), encoding="utf-8")
    if gaze and descriptor.face_overlay:
        add_gaze(out, skin=skin)
    return desc_path


def declare_mouth_variants(
    descriptor: CharacterDescriptor, variants: dict[str, float]
) -> None:
    """Declare a ``viseme@<form>`` set per variant on ``descriptor`` — the set's
    keys map to ``mouth_<shape>_<form>`` attachments, which are added to the
    default skin's ``mouth`` slot with the neutral mouth's geometry. The
    neutral set is the SSOT for which shapes exist; a variant mirrors it.
    """
    from an.characters.schema import VISEME_CHANNEL

    neutral = descriptor.asset_sets.get(VISEME_CHANNEL) or {}
    skin = descriptor.skins.get("default")
    if skin is None or "mouth" not in skin.slots:
        return
    mouth_slot = skin.slots["mouth"]
    for form in variants:
        key_map: dict[str, str] = {}
        for key, attachment in neutral.items():
            # From the KEY, never the attachment name: a promoted hand rig maps
            # `X` to `mouth_shut`, and only `mouth_x_<form>.svg` is ever drawn.
            shape = key.lower()
            if shape not in MOUTH_SHAPES:
                continue
            variant_name = mouth_attachment_name(shape, form)
            template = mouth_slot.get(attachment)
            if template is None:
                continue
            mouth_slot[variant_name] = template.model_copy(
                update={"path": f"parts/mouth/{variant_name}.svg"}
            )
            key_map[key] = variant_name
        if key_map:
            descriptor.asset_sets[f"{VISEME_CHANNEL}@{form}"] = key_map


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


#: The eye's geometry in its 64x32 canvas, shared by the four synthesizers so
#: the sclera, the pupil and the lid outline agree (an#99).
EYE_CANVAS: tuple[int, int] = (64, 32)
EYE_CENTRE: tuple[int, int] = (32, 16)
EYE_RX, EYE_RY = 14, 10
PUPIL_R: int = 5
#: The parts a rig gains with `an character add-gaze`. Optional — never in
#: `REQUIRED_PARTS`: a pre-Wave-6 rig without them still renders, and gaze is
#: a no-op on it.
GAZE_PARTS: tuple[str, ...] = ("sclera_l", "sclera_r", "pupil_l", "pupil_r")


def _eye_svg(inner: str, *, gid: str) -> str:
    w, h = EYE_CANVAS
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<svg xmlns="{SVG_NS}" viewBox="0 0 {w} {h}" width="{w}" height="{h}">'
        f'<g id="{gid}">{inner}</g></svg>'
    )


def _synthesize_eye_open(path: Path, *, side: str, outline_only: bool = False) -> Path:
    """The open eye. ``outline_only`` (the gaze stack, an#99) draws the outline
    with a transparent interior, so the sclera and pupil slots beneath show
    through; the default keeps the pre-stack single drawing (white + pupil)."""
    cx, cy = EYE_CENTRE
    if outline_only:
        inner = f'<ellipse cx="{cx}" cy="{cy}" rx="{EYE_RX}" ry="{EYE_RY}" fill="none" stroke="#222" stroke-width="2"/>'
    else:
        inner = (
            f'<ellipse cx="{cx}" cy="{cy}" rx="{EYE_RX}" ry="{EYE_RY}" fill="#ffffff" stroke="#222" stroke-width="2"/>'
            f'<circle cx="{cx}" cy="{cy}" r="{PUPIL_R}" fill="#1a1a1a"/>'
        )
    path.write_text(_eye_svg(inner, gid=f"eye_{side}_open"), encoding="utf-8")
    return path


def _synthesize_eye_closed(path: Path, *, side: str, fill: str | None = None) -> Path:
    """The closed eye. With ``fill`` (the gaze stack) it is a FILLED skin-tone
    lid — a stroke-only closed eye would show the pupil through it."""
    cx, cy = EYE_CENTRE
    lid = ""
    if fill is not None:
        lid = f'<ellipse cx="{cx}" cy="{cy}" rx="{EYE_RX}" ry="{EYE_RY}" fill="{fill}" stroke="none"/>'
    inner = lid + f'<path d="M {cx - 14} {cy + 2} Q {cx} {cy + 8} {cx + 14} {cy + 2}" stroke="#222" stroke-width="3" fill="none" stroke-linecap="round"/>'
    path.write_text(_eye_svg(inner, gid=f"eye_{side}_closed"), encoding="utf-8")
    return path


def _synthesize_sclera(path: Path, *, side: str) -> Path:
    cx, cy = EYE_CENTRE
    inner = f'<ellipse cx="{cx}" cy="{cy}" rx="{EYE_RX}" ry="{EYE_RY}" fill="#ffffff" stroke="none"/>'
    path.write_text(_eye_svg(inner, gid=f"sclera_{side}"), encoding="utf-8")
    return path


def _synthesize_pupil(path: Path, *, side: str) -> Path:
    cx, cy = EYE_CENTRE
    inner = f'<circle cx="{cx}" cy="{cy}" r="{PUPIL_R}" fill="#1a1a1a"/>'
    path.write_text(_eye_svg(inner, gid=f"pupil_{side}"), encoding="utf-8")
    return path


def gaze_travel_for(rx: float = EYE_RX, ry: float = EYE_RY, pupil_r: float = PUPIL_R) -> dict[str, float]:
    """The pupil's travel per axis, in view-box units: the sclera's clearance
    minus the pupil's radius — the compile-time clamp that keeps the pupil
    inside the white without a runtime mask.

    >>> gaze_travel_for()
    {'x': 9.0, 'y': 5.0}
    """
    return {"x": float(rx - pupil_r), "y": float(ry - pupil_r)}


def add_gaze(char_dir: str | Path, *, skin: str | None = None) -> Path:
    """Give a character the eye stack (an#99): three sibling slots per eye under
    the head — ``<side>_sclera`` (white fill) below ``<side>_pupil`` below
    ``<side>_eye`` (the existing slot, now the lid, drawn above the pupil) —
    with synthesized parts, an outline-only open eye, a FILLED closed lid, the
    ``gaze_travel`` clamp, and draw orders that put the lid over the pupil.
    Idempotent: a rig that already has the stack is rewritten to the same
    state. Returns the descriptor path.

    This is the **expand** step for a pre-Wave-6 descriptor: no migration
    inserts pupil slots, because their art would be absent and absent art is
    fatal under `strict_assets` — every existing character would stop
    rendering on the bench.
    """
    from an.characters.schema import CharacterDescriptor, FACE_OFFSETS, Attachment, Slot
    from an.ir.migrate import migrate

    char_dir = Path(char_dir)
    desc_path = char_dir / "character.json"
    raw = json.loads(desc_path.read_text(encoding="utf-8"))
    desc = CharacterDescriptor.model_validate(migrate(raw, kind="CharacterDescriptor"))
    parts = char_dir / "parts"
    if skin is None:
        seed = str(desc.metadata.get("seed") or desc.metadata.get("dicebear_seed") or desc.name)
        skin = _palette_for_seed(seed)[0]
    for side in ("l", "r"):
        _synthesize_sclera(parts / f"sclera_{side}.svg", side=side)
        _synthesize_pupil(parts / f"pupil_{side}.svg", side=side)
        _synthesize_eye_open(parts / f"eye_{side}_open.svg", side=side, outline_only=True)
        _synthesize_eye_closed(parts / f"eye_{side}_closed.svg", side=side, fill=skin)
    skin_ = desc.skins.get("default")
    if skin_ is None:
        raise ValueError(f"{desc.name!r} has no default skin to add the eye stack to")
    by_name = {s.name: s for s in desc.slots}
    had_stack = "left_pupil" in by_name or "right_pupil" in by_name
    for side, eye_slot in (("l", "left_eye"), ("r", "right_eye")):
        eye = by_name.get(eye_slot)
        if eye is None:
            raise ValueError(f"{desc.name!r} has no {eye_slot!r} slot; the eye stack sits under it")
        x, y = FACE_OFFSETS.get(eye_slot, (0.0, 0.0))
        existing = skin_.slots.get(eye_slot, {})
        template = existing.get("open") or next(iter(existing.values()), None)
        if template is not None:
            x, y = template.x, template.y
        base = eye.draw_order - 1 if had_stack else eye.draw_order
        for kind, order in (("sclera", base - 1), ("pupil", base)):
            slot_name = f"{eye_slot.split('_')[0]}_{kind}"
            stem = f"{kind}_{side}"
            if slot_name not in by_name:
                desc.slots.append(Slot(name=slot_name, bone=eye.bone, draw_order=order, attachment=stem))
                by_name[slot_name] = desc.slots[-1]
            else:
                by_name[slot_name].draw_order = order
            skin_.slots[slot_name] = {stem: Attachment(path=f"parts/{stem}.svg", anchor=(0.5, 0.5), x=x, y=y)}
        # The lid draws above the pupil: bump it once (idempotent on a rig that
        # already has the stack; the mouth and brows already sit above).
        eye.draw_order = base + 1
    desc.gaze_travel = gaze_travel_for()
    desc.metadata["gaze_stack"] = "an#99"
    desc_path.write_text(desc.model_dump_json(indent=2), encoding="utf-8")
    return desc_path


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
