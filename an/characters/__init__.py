"""Character art system: Spine-shaped descriptor + SVG sidecars.

Public API:

- :class:`CharacterDescriptor` — the on-disk schema for a character (bones,
  slots, skins, viseme map, idle animations).
- :func:`new_character` — generate a fresh character (DiceBear or built-in).
- :func:`generate_default_mouths` — produce the 9-shape default mouth set.
- :func:`render_silhouette`, :func:`compare_silhouettes` — silhouette test.
- :func:`breath_animation`, :func:`blink_animation` — idle animation factories.
- :func:`validate_character` — completeness check against the schema.
- :func:`promote` — lift an inline character into the reusable mall.

Conventions (locked in):

- Slot/skin/animation separation modeled on Spine's JSON format.
- SVG layout: a ``<g id="skeleton">`` of named ``<circle>`` pivots and a
  sibling ``<g id="illustration">`` containing named part groups (Pose
  Animator convention).
- 9 mouth shapes, named ``mouth_a`` through ``mouth_h`` plus ``mouth_x``
  (the rest position), matching Rhubarb's A–H + X visemes.
- Time in seconds (float); `bone:<name>.<prop>` and
  `slot:<name>.attachment` are the two animation target syntaxes.

>>> from an.characters import MOUTH_SHAPES
>>> MOUTH_SHAPES
('a', 'b', 'c', 'd', 'e', 'f', 'g', 'h', 'x')
"""

from __future__ import annotations

from an.characters.schema import (
    CharacterDescriptor,
    Bone,
    Slot,
    Attachment,
    Skin,
    IdleAnimation,
    AnimationTrack,
    MOUTH_SHAPES,
    REQUIRED_PARTS,
    DEFAULT_VIEW_BOX,
    DEFAULT_VISEME_MAP,
    CHARACTER_SCHEMA_VERSION,
)
from an.characters.svg_utils import (
    normalize_svg,
    extract_part,
    extract_pivots,
    write_svg,
    promote_inkscape_labels_to_ids,
)
from an.characters.mouth_set import (
    generate_default_mouths,
    write_default_mouths,
    DEFAULT_MOUTH_VIEWBOX,
)
from an.characters.idle import (
    breath_animation,
    blink_animation,
    DEFAULT_BREATH_PERIOD_S,
    DEFAULT_BREATH_AMPLITUDE_PX,
    DEFAULT_HEAD_TILT_DEG,
    DEFAULT_BLINK_DURATION_S,
)
from an.characters.silhouette import (
    render_silhouette,
    compare_silhouettes,
)
from an.characters.dicebear import (
    fetch_dicebear,
    DICEBEAR_DEFAULT_STYLE,
    DICEBEAR_API_VERSION,
)
from an.characters.factory import new_character, validate_character
from an.characters.promote import promote
from an.characters.record import (
    record_character,
    record_preview_to_mp4,
    DEFAULT_RECORD_DURATION_S,
    DEFAULT_RECORD_SIZE,
)


__all__ = [
    "CharacterDescriptor",
    "Bone",
    "Slot",
    "Attachment",
    "Skin",
    "IdleAnimation",
    "AnimationTrack",
    "MOUTH_SHAPES",
    "REQUIRED_PARTS",
    "DEFAULT_VIEW_BOX",
    "DEFAULT_VISEME_MAP",
    "CHARACTER_SCHEMA_VERSION",
    "normalize_svg",
    "extract_part",
    "extract_pivots",
    "write_svg",
    "promote_inkscape_labels_to_ids",
    "generate_default_mouths",
    "write_default_mouths",
    "DEFAULT_MOUTH_VIEWBOX",
    "breath_animation",
    "blink_animation",
    "DEFAULT_BREATH_PERIOD_S",
    "DEFAULT_BREATH_AMPLITUDE_PX",
    "DEFAULT_HEAD_TILT_DEG",
    "DEFAULT_BLINK_DURATION_S",
    "render_silhouette",
    "compare_silhouettes",
    "fetch_dicebear",
    "DICEBEAR_DEFAULT_STYLE",
    "DICEBEAR_API_VERSION",
    "new_character",
    "validate_character",
    "promote",
    "record_character",
    "record_preview_to_mp4",
    "DEFAULT_RECORD_DURATION_S",
    "DEFAULT_RECORD_SIZE",
]
