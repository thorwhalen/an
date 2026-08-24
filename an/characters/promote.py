"""Asset promotion: lift an inline character from a scene into the mall.

The :func:`promote` function copies a character's SVG out of a scene's
inline assets into ``mall["characters"]/<as_>/`` as a reusable, named
character. This is the v0.2 deliverable per research §5.3.

>>> # Tested via tests/test_characters_promote.py with a tmp project.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from an.characters.factory import (
    new_character,
    _synthesize_brow,
    _synthesize_eye_closed,
    _synthesize_eye_open,
)
from an.characters.mouth_set import write_default_mouths
from an.characters.schema import CharacterDescriptor, bones_from_pivots
from an.characters.validate import validate_character
from an.characters.svg_utils import (
    extract_part,
    extract_pivots,
    normalize_svg,
    write_svg,
)


def promote(
    project_dir: str | Path,
    entity: str,
    as_: str,
    *,
    source_svg: str | Path | None = None,
    voice_ref: Optional[str] = None,
    use_dicebear: bool = True,
    overwrite: bool = False,
) -> Path:
    """Promote ``entity`` from ``project_dir``'s inline assets into the mall.

    Parameters
    ----------
    project_dir
        Path to an ``an`` project (must contain ``assets/characters/``).
    entity
        Inline entity id used inside the scene (the directory under
        ``assets/characters/<entity>``, or the SVG file at
        ``assets/characters/<entity>.svg``).
    as_
        The mall character id to register the result as. Becomes the
        directory name under ``assets/characters/`` and the descriptor's
        ``name`` field.
    source_svg
        Optional explicit path to a source SVG. If omitted, the function
        looks for ``assets/characters/<entity>.svg`` or
        ``assets/characters/<entity>/<entity>.svg``.
    voice_ref
        Voice reference to embed in the descriptor.
    use_dicebear
        Forwarded to :func:`~an.characters.factory.new_character` on the
        no-source fallback path. Pass ``False`` to keep the call offline —
        without it that fallback always reaches the DiceBear API, and
        ``new_character`` swallows the failure and generates geometry instead,
        so an offline test looks like it passed rather than like it was skipped.
    overwrite
        If False and the target already exists, raises ``FileExistsError``.

    Returns the path to the new ``character.json``.
    """
    pdir = Path(project_dir)
    chars_dir = pdir / "assets" / "characters"
    if not chars_dir.exists():
        raise FileNotFoundError(chars_dir)

    src = _resolve_source(chars_dir, entity, source_svg)
    if src is None or not src.exists():
        # Fall back to building from scratch — no inline source means we
        # treat the entity as a name and generate a fresh DiceBear character.
        return new_character(
            chars_dir,
            name=as_,
            seed=entity,
            voice_ref=voice_ref,
            use_dicebear=use_dicebear,
            overwrite=overwrite,
        )

    target = chars_dir / as_
    if target.exists():
        if not overwrite:
            raise FileExistsError(target)
        import shutil

        shutil.rmtree(target)
    target.mkdir(parents=True)
    parts_dir = target / "parts"
    parts_dir.mkdir()
    mouth_dir = parts_dir / "mouth"
    mouth_dir.mkdir()

    # Normalize and copy
    tree = normalize_svg(src)
    canonical = target / f"{as_}.svg"
    write_svg(tree, canonical)
    pivots = extract_pivots(tree)

    # Slice known parts
    sliced: list[str] = []
    for part_id in (
        "head",
        "torso",
        "arm_l",
        "arm_r",
        "leg_l",
        "leg_r",
        "brow_l",
        "brow_r",
    ):
        try:
            part_tree = extract_part(tree, part_id)
        except KeyError:
            continue
        write_svg(part_tree, parts_dir / f"{part_id}.svg")
        sliced.append(part_id)

    # Try eye variants — if the source has them, use them; otherwise
    # synthesize so the character is complete.
    for variant in ("eye_l_open", "eye_l_closed", "eye_r_open", "eye_r_closed"):
        try:
            part_tree = extract_part(tree, variant)
            write_svg(part_tree, parts_dir / f"{variant}.svg")
            sliced.append(variant)
        except KeyError:
            side = "l" if "_l_" in variant else "r"
            if "open" in variant:
                _synthesize_eye_open(parts_dir / f"{variant}.svg", side=side)
            else:
                _synthesize_eye_closed(parts_dir / f"{variant}.svg", side=side)

    # Brows fallback
    for variant in ("brow_l", "brow_r"):
        if not (parts_dir / f"{variant}.svg").exists():
            side = "l" if variant.endswith("_l") else "r"
            _synthesize_brow(parts_dir / f"{variant}.svg", side=side)

    # Always write the default mouth set (caller can replace afterwards).
    write_default_mouths(mouth_dir)

    # Descriptor
    descriptor = CharacterDescriptor(
        name=as_,
        display_name=as_.replace("-", " ").replace("_", " ").title(),
        voice_ref=voice_ref,
        source_svg=f"{as_}.svg",
        # The illustrator's own joints become the rig. Before an#75 this line
        # stored `list(pivots.keys())` and dropped every coordinate, so the art
        # was sliced correctly and then hung on a generic skeleton.
        bones=bones_from_pivots(pivots),
        metadata={
            "promoted_from": entity,
            "sliced_parts": sliced,
            "pivots_detected": sorted(pivots),
        },
    )
    desc_path = target / "character.json"
    desc_path.write_text(descriptor.model_dump_json(indent=2), encoding="utf-8")
    return desc_path


def _resolve_source(
    chars_dir: Path, entity: str, source_svg: str | Path | None
) -> Path | None:
    """Find the source SVG for ``entity``."""
    if source_svg is not None:
        return Path(source_svg)
    direct = chars_dir / f"{entity}.svg"
    if direct.exists():
        return direct
    folder = chars_dir / entity
    if folder.is_dir():
        candidate = folder / f"{entity}.svg"
        if candidate.exists():
            return candidate
        # any svg in the folder
        return next(folder.glob("*.svg"), None)
    return None
