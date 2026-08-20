"""Compile a top-level `Shot` (style="cutout") into a `CutoutSceneJSON`.

This is the bridge between the renderer-agnostic `an.ir` types and the
cutout-specific JSON contract that the JS runtime will consume in Phase 2B.

Strategy:

1. **Resolve entities** from the project mall: each `AssetRef` in
   ``shot.entities`` becomes a sub-tree of the cutout scene (a character with
   placeholder rect parts when the character store has no sidecar art yet).
2. **Flatten authoring actions** via `an.ir.compose.flatten` — every
   `tween`/`set`/`play`/composition produces leaf `FlatAction`s with
   absolute times.
3. **Compile each FlatAction to PlacedClipJSON entries** on the appropriate
   track. Tween → a 2-keyframe AnimationClipJSON + a PlacedClipJSON. Set →
   a 1-keyframe step-easing clip. Play → reference an existing animation.

The compiler is deterministic and side-effect-free (it doesn't write to the
mall). It reads only.

>>> from an.ir.schema import Meta, SceneIR, Shot
>>> from an.adapters.cutout.compile import compile_shot
>>> shot = Shot(id="s1", style="cutout", duration=2.0)
>>> j = compile_shot(shot, mall={"characters": {}})
>>> j.timeline.duration
2.0
"""

from __future__ import annotations

import warnings
from collections.abc import Mapping
from typing import Any

from an.ir.compose import FlatAction, flatten
from an.ir.schema import (
    Action,
    AssetRef,
    PlayAction,
    SetAction,
    Shot,
    TweenAction,
)

from an.adapters.cutout.serialize import (
    AnimationClipJSON,
    AssetJSON,
    AssetsJSON,
    ChannelJSON,
    CutoutSceneJSON,
    CutoutSceneMetaJSON,
    KeyframeJSON,
    NodeJSON,
    PlacedClipJSON,
    SlotJSON,
    TimelineJSON,
    TrackJSON,
    TransformJSON,
    VisualJSON,
)
from an.characters.schema import MOUTH_SHAPES, REQUIRED_PARTS


# Default placeholder character: a recognizable stick-figure layout in pixel
# space so the demo is *visible* without art assets. Each entry pins a part to
# a (x, y) offset; colors come from a per-character palette so multiple
# characters look distinct. Used only when the characters store has no rig.
_PLACEHOLDER_PARTS: tuple[str, ...] = ("head", "torso", "left_arm", "right_arm")
_PLACEHOLDER_PART_GEOMETRY: dict[str, dict[str, float]] = {
    "head": {"x": 0.0, "y": -55.0, "width": 50.0, "height": 50.0},
    "torso": {"x": 0.0, "y": 0.0, "width": 60.0, "height": 80.0},
    "left_arm": {"x": -50.0, "y": -10.0, "width": 30.0, "height": 70.0},
    "right_arm": {"x": 50.0, "y": -10.0, "width": 30.0, "height": 70.0},
    "left_leg": {"x": -18.0, "y": 65.0, "width": 30.0, "height": 70.0},
    "right_leg": {"x": 18.0, "y": 65.0, "width": 30.0, "height": 70.0},
}

# Per-character color palettes. Each entry is (skin, clothing, hair). Picked
# deterministically from the entity.id so re-renders are stable.
_CHARACTER_PALETTES: tuple[tuple[str, str, str], ...] = (
    ("#f4c89a", "#3a6ea5", "#3b2a1a"),  # peach skin, blue clothes, dark hair
    ("#d8a47f", "#a83249", "#1a1a1a"),  # tan skin, red clothes, black hair
    ("#fbe1c1", "#2e7d4f", "#a8743f"),  # pale skin, green clothes, ginger
    ("#a87a5d", "#5b3a8a", "#2a2a2a"),  # darker skin, purple clothes, black
    ("#e8c39e", "#d97706", "#5e3a1f"),  # warm skin, orange clothes, brown
)


def _palette_for(entity_id: str) -> tuple[str, str, str]:
    """Deterministic (skin, clothing, hair) palette for a given entity id."""
    idx = sum(ord(c) for c in entity_id) % len(_CHARACTER_PALETTES)
    return _CHARACTER_PALETTES[idx]


# Cap viseme keyframe density to ~7Hz; reduces "twitchy" mouth at full speed.
_MIN_VISEME_GAP_S: float = 0.14


def _property_rest_values() -> dict[str, float]:
    """Rest ("identity") value per animatable property, derived from the schema.

    A tween that declares no ``from_value`` starts from its property's rest
    value. This is not cosmetic: offsets and rotations rest at 0.0, but the
    *multiplicative* properties rest at 1.0, and defaulting all of them to 0.0
    silently breaks the two most obvious uses of a tween — a fade-out (``alpha``
    starting at 0 is already invisible, so the fade never happens and the
    element simply is not there) and a scale move (the subject pops in from
    nothing). Nothing had noticed because the camera builds its own explicit
    keyframes and no example authors a scale or alpha tween.

    **Derived, not restated.** A node's rest pose *is* ``TransformJSON``'s field
    defaults, so reading them off the model keeps one source of truth; a
    hand-maintained copy would be a second place to forget.
    """
    rest = {
        name: float(field.default)
        for name, field in TransformJSON.model_fields.items()
        if isinstance(field.default, (int, float))
        and not isinstance(field.default, bool)
    }
    # `rotation_rad` is an accepted alias for `rotation` in the runtime's
    # property switch and in `pose.py`; it is not a schema field, so it has to
    # be added here rather than derived.
    rest["rotation_rad"] = rest["rotation"]
    return rest


#: See :func:`_property_rest_values`.
_PROPERTY_REST_VALUES: dict[str, float] = _property_rest_values()


class CutoutCompileError(ValueError):
    """A shot cannot be compiled to a cutout scene. Carries actionable detail.

    A ``ValueError`` rather than a ``RuntimeError`` — unlike the rest of this
    package's error tree — because every one of these is "this value is not in
    the known set", which is the existing idiom for argument-level rejection.
    The render-time errors stay ``RuntimeError``: they are failures of the
    machinery, not of the input.
    """


class CutoutCompileWarning(UserWarning):
    """A shot compiles, but something in it will not reach the screen.

    The line between this and :class:`CutoutCompileError` is whether the author
    could plausibly have meant it. An unknown ``camera.move`` is always a
    mistake, so it raises. A speaker with no mouth is usually an off-screen
    narrator and occasionally a typo — refusing it would break the documented
    idiom, and passing in silence is what this whole change is against.
    """


def _rest_value_for(prop: str, target: str) -> float:
    """The implicit start of a tween on ``prop``, or refuse to invent one.

    A property with no numeric identity — a viseme code, a colour — has no
    meaningful "start from rest". Substituting 0.0 does not mean "unchanged", it
    means *zero*, and the renderer will happily apply it: a colour-valued tween
    with an implicit 0.0 start renders its subject solid black for the whole
    shot, silently. So this raises rather than guessing.
    """
    if prop in _PROPERTY_REST_VALUES:
        return _PROPERTY_REST_VALUES[prop]
    raise CutoutCompileError(
        f"tween on {target!r}:{prop!r} has no from_value, and {prop!r} has no "
        f"rest value to start from (known: {sorted(_PROPERTY_REST_VALUES)}). "
        "Give the action an explicit from_value, or use a `set` action if you "
        "meant to change it discretely — a tween cannot interpolate from a "
        "value that does not exist, and defaulting it to 0 would render as "
        "'fully transparent' / 'black' / 'scaled to nothing' rather than as "
        "'unchanged'."
    )


#: Fallback for a property with no declared rest value.
#:
#: Reachable only by a property that is neither a transform field nor an alias —
#: i.e. a discrete one such as ``viseme``, for which no numeric identity is
#: meaningful. Multiplying such a property by an implicit 0.0 is how a
#: colour-valued tween would render its subject solid black, so the compiler
#: refuses instead: see :func:`_rest_value_for`.
_DFLT_PROPERTY_REST_VALUE: float | None = None

# Emotion → (left_brow_tilt, right_brow_tilt) in radians. Mirrored so the
# brows look symmetric for happy/sad and asymmetric for surprise/skepticism.
_EMOTION_BROWS: dict[str, tuple[float, float]] = {
    "neutral": (0.0, 0.0),
    "happy": (-0.15, 0.15),  # outer ends raised
    "sad": (0.20, -0.20),  # inner ends raised, outer drops
    "angry": (0.30, -0.30),  # furrowed
    "surprised": (-0.25, 0.25),  # both up high
    "skeptical": (-0.20, 0.10),  # one brow raised
    "amused": (-0.10, 0.10),
    "thinking": (0.10, 0.0),
}


def _runtime_node_paths(node: NodeJSON, prefix: str = "") -> set[str]:
    """Every path the JS runtime will index for ``node``'s subtree.

    Mirrors ``buildSceneTree``, including the detail that the synthetic top-level
    ``root`` container is NOT indexed — the compiler emits target paths starting
    at the entity name. Getting that wrong here would make every check below
    off by one segment.
    """
    paths = set()
    path = f"{prefix}/{node.name}" if prefix else node.name
    if prefix or node.name != "root":
        paths.add(path)
        child_prefix = path
    else:
        child_prefix = ""  # skip the synthetic root, as the runtime does
    for child in node.children:
        paths |= _runtime_node_paths(child, child_prefix)
    return paths


def compile_shot(
    shot: Shot,
    mall: Mapping[str, Mapping] | None = None,
    *,
    fps: int = 30,
    width: int = 1920,
    height: int = 1080,
    background: str = "#ffffff",
) -> CutoutSceneJSON:
    """Compile a single cutout-style `Shot` to its JS-runtime JSON form."""
    if shot.style != "cutout":
        raise ValueError(f"compile_shot expects style='cutout'; got {shot.style!r}")
    mall = mall or {}

    textures: dict[str, AssetJSON] = {}
    scene_root = _build_scene_root(shot, mall, textures=textures)
    animations, tracks = _compile_actions(shot.actions, shot.duration)
    # Phase 4: emit a viseme channel per dialogue line that has a viseme_track.
    _add_viseme_clips(
        shot,
        animations,
        tracks,
        mall=mall,
        node_paths=_runtime_node_paths(scene_root),
    )
    # Phase 7: wire camera.move ("push_in", "pull_out", "hold") into a scale
    # animation on the synthetic scene root so directors get visible camera
    # behavior without writing channels by hand.
    _add_camera_clips(shot, animations, tracks)

    timeline = TimelineJSON(duration=shot.duration, tracks=tracks)

    return CutoutSceneJSON(
        meta=CutoutSceneMetaJSON(
            fps=fps,
            width=width,
            height=height,
            duration=shot.duration,
            background=background,
        ),
        scene=scene_root,
        animations=animations,
        timeline=timeline,
        assets=AssetsJSON(textures=textures),
    )


# -----------------------------------------------------------------------------
# Scene tree construction
# -----------------------------------------------------------------------------


def _build_scene_root(
    shot: Shot,
    mall: Mapping[str, Mapping],
    *,
    textures: dict[str, AssetJSON] | None = None,
) -> NodeJSON:
    """Construct the cutout scene tree under a single root from shot.entities.

    Multiple characters get spread along the x-axis so they don't overlap.
    For N characters, positions are evenly distributed across a fixed band;
    a single character lives at the center.
    """
    if textures is None:
        textures = {}
    children: list[NodeJSON] = []
    characters_store = mall.get("characters") or {}
    environments_store = mall.get("environments") or {}
    char_entities = [e for e in shot.entities if e.kind == "character"]
    n_chars = len(char_entities)
    char_positions = _layout_character_positions(n_chars)
    char_idx = 0
    # Process environments first so they sit BEHIND characters in z-order.
    for entity in shot.entities:
        if entity.kind == "environment":
            children.append(_build_environment_subtree(entity, environments_store))
    for entity in shot.entities:
        if entity.kind == "character":
            x = char_positions[char_idx]
            char_idx += 1
            sub = _build_character_subtree(entity, characters_store, textures=textures)
            sub.transform.x = x
            children.append(sub)
        elif entity.kind == "prop":
            raise CutoutCompileError(
                f"shot {shot.id!r}: entity {entity.id!r} is a prop, which the "
                "cutout renderer does not draw yet. Props — images, nine-slice "
                "panels, things a character holds — are planned; see "
                "https://github.com/thorwhalen/an/issues/9. Until then, remove the entity rather than leaving it "
                "in the scene, where it would be silently absent from the render."
            )
        # `voice` and `style` entities are legitimately not drawable: they
        # configure the render rather than appearing in it.
    return NodeJSON(name="root", children=children)


# Environment presets — built-in named backdrops. A user-supplied environment
# in the store can override fields by name (sky_color, ground_color, ground_y).
_ENV_PRESETS: dict[str, dict[str, Any]] = {
    "default": {"sky_color": "#cfe9ff", "ground_color": "#7cba6f", "ground_y": 100.0},
    "park": {"sky_color": "#a5d8ff", "ground_color": "#7cba6f", "ground_y": 110.0},
    "indoor": {"sky_color": "#f4e8c8", "ground_color": "#a07a4a", "ground_y": 120.0},
    "night": {"sky_color": "#1a2540", "ground_color": "#2c3e50", "ground_y": 110.0},
    "sunset": {"sky_color": "#f4a261", "ground_color": "#5b4b32", "ground_y": 110.0},
}


def _build_environment_subtree(entity: AssetRef, env_store: Mapping) -> NodeJSON:
    """Backdrop: a sky band + a ground band, full canvas width.

    Picks a preset by ``entity.ref`` (e.g. "park", "night"); the store
    can override any of (sky_color, ground_color, ground_y) by ref.
    """
    preset_key = (entity.ref or "default").lower()
    preset = dict(_ENV_PRESETS.get(preset_key, _ENV_PRESETS["default"]))
    if entity.ref in env_store:
        try:
            override = env_store[entity.ref]
            if isinstance(override, dict):
                unknown = sorted(set(override) - set(preset))
                if unknown:
                    # A warning, not an error. EnvironmentsStore is a
                    # JsonSidecarStore over a free-form meta.json, so `name` /
                    # `description` / `tags` are its natural shape — raising here
                    # would hard-fail ordinary data. The keys still do nothing,
                    # which is the part worth saying.
                    warnings.warn(
                        f"environment {entity.ref!r} declares {unknown}, which the "
                        f"cutout renderer does not read (it uses {sorted(preset)}), "
                        "so they have no effect on the render. Layered plates "
                        "and parallax planes are planned; see "
                        "https://github.com/thorwhalen/an/issues/9",
                        CutoutCompileWarning,
                        stacklevel=2,
                    )
                preset.update({k: v for k, v in override.items() if k in preset})
        except KeyError:
            pass
    # Sky and ground are HUGE rects so they fill the canvas regardless of size.
    # The runtime centers root at canvas/2 and applies camera scale, so 4000px
    # wide rects will always cover.
    huge = 4000.0
    ground_y = float(preset["ground_y"])
    sky_color = str(preset["sky_color"])
    ground_color = str(preset["ground_color"])
    return NodeJSON(
        name=entity.id,
        transform=TransformJSON(),
        children=[
            NodeJSON(
                name="sky",
                transform=TransformJSON(x=0.0, y=-huge / 2 + ground_y),
                visual=VisualJSON(
                    kind="rect", width=huge, height=huge, color=sky_color
                ),
            ),
            NodeJSON(
                name="ground",
                transform=TransformJSON(x=0.0, y=huge / 2 + ground_y),
                visual=VisualJSON(
                    kind="rect", width=huge, height=huge, color=ground_color
                ),
            ),
        ],
    )


def _layout_character_positions(n: int, *, spread: float = 220.0) -> list[float]:
    """Return ``n`` x-positions evenly distributed about 0.

    >>> _layout_character_positions(0)
    []
    >>> _layout_character_positions(1)
    [0.0]
    >>> _layout_character_positions(2, spread=200.0)
    [-100.0, 100.0]
    """
    if n <= 0:
        return []
    if n == 1:
        return [0.0]
    step = spread / (n - 1)
    return [-spread / 2 + i * step for i in range(n)]


def _build_character_subtree(
    entity: AssetRef,
    characters_store: Mapping,
    *,
    textures: dict[str, AssetJSON] | None = None,
) -> NodeJSON:
    """Build a NodeJSON subtree for one character.

    Phase 11b: if the characters store has a Phase-11a CharacterDescriptor
    for this entity (``kind == "CharacterDescriptor"``), build the SVG
    rig and populate the ``textures`` accumulator. Otherwise fall back to
    the procedural rig so legacy / asset-less characters keep rendering.
    """
    char_meta: dict[str, Any] = {}
    if entity.ref in characters_store:
        try:
            value = characters_store[entity.ref]
            if isinstance(value, dict):
                char_meta = value
        except KeyError:
            char_meta = {}

    if char_meta.get("kind") == "CharacterDescriptor":
        return _build_svg_character_subtree(
            entity, char_meta, textures=textures if textures is not None else {}
        )

    parts = char_meta.get("parts") or _PLACEHOLDER_PARTS
    skin, clothing, hair = _palette_for(entity.id)
    # Per-part color: head/limbs are skin colour, torso/arm-clothing is the
    # clothing colour. Override via a future store.parts.colors mapping.
    part_color: dict[str, str] = {
        "head": skin,
        "torso": clothing,
        "left_arm": clothing,
        "right_arm": clothing,
        "left_leg": "#2c3e50",
        "right_leg": "#2c3e50",
    }
    children: list[NodeJSON] = []
    for part in parts:
        geom = _PLACEHOLDER_PART_GEOMETRY.get(
            part, {"x": 0.0, "y": 0.0, "width": 50.0, "height": 50.0}
        )
        # Head renders as a fleshier ellipse; everything else stays a rect.
        kind = "ellipse" if part == "head" else "rect"
        children.append(
            NodeJSON(
                name=part,
                transform=TransformJSON(x=float(geom["x"]), y=float(geom["y"])),
                visual=VisualJSON(
                    kind=kind,
                    width=float(geom["width"]),
                    height=float(geom["height"]),
                    color=part_color.get(part, "#cccccc"),
                ),
            )
        )
    char_node = NodeJSON(
        name=entity.id,
        transform=TransformJSON(),
        slots={"root": SlotJSON(name="root")},
        children=children,
    )
    if "head" in parts:
        # Head children: hair on top, eyebrows above eyes, two eyes (white +
        # pupil drawn together by the runtime when kind="eye"), mouth (viseme
        # target — runtime draws curved lips per viseme code).
        for child in char_node.children:
            if child.name != "head":
                continue
            child.slots["mouth"] = SlotJSON(name="mouth", x=0, y=14)
            # Hair: rounded band atop the head.
            child.children.append(
                NodeJSON(
                    name="hair",
                    transform=TransformJSON(x=0.0, y=-20.0),
                    visual=VisualJSON(
                        kind="ellipse", width=46.0, height=18.0, color=hair
                    ),
                )
            )
            # Eyebrows: small dark rects above each eye; rotation = expression.
            for brow_name, bx in (("left_brow", -10.0), ("right_brow", 10.0)):
                child.children.append(
                    NodeJSON(
                        name=brow_name,
                        transform=TransformJSON(x=bx, y=-10.0),
                        visual=VisualJSON(
                            kind="rect",
                            width=10.0,
                            height=2.5,
                            color=hair,
                        ),
                    )
                )
            # Eyes: white sclera + dark pupil drawn together by makeEye.
            for eye_name, ex in (("left_eye", -10.0), ("right_eye", 10.0)):
                child.children.append(
                    NodeJSON(
                        name=eye_name,
                        transform=TransformJSON(x=ex, y=-3.0),
                        visual=VisualJSON(
                            kind="eye", width=10.0, height=8.0, color="#1a1a1a"
                        ),
                    )
                )
            # Mouth (viseme target).
            child.children.append(
                NodeJSON(
                    name="mouth",
                    transform=TransformJSON(x=0.0, y=14.0),
                    visual=VisualJSON(
                        kind="mouth", width=22.0, height=4.0, color="#552222"
                    ),
                )
            )
    return char_node


# -----------------------------------------------------------------------------
# Phase 11b: SVG-textured character rig from a CharacterDescriptor
# -----------------------------------------------------------------------------


# Render-display sizes for the SVG rig parts (in scene-graph pixels).
# Tuned so a 1024-px-tall character at scale 1 reads at ~360 px tall on a
# 1080p frame — roughly head:body 1:5 per the research §6.1 default.
_SVG_HEAD_SIZE: float = 96.0
_SVG_TORSO_SIZE: tuple[float, float] = (110.0, 130.0)
_SVG_ARM_SIZE: tuple[float, float] = (28.0, 110.0)
_SVG_LEG_SIZE: tuple[float, float] = (38.0, 120.0)
_SVG_EYE_SIZE: tuple[float, float] = (18.0, 12.0)
_SVG_BROW_SIZE: tuple[float, float] = (24.0, 8.0)
_SVG_MOUTH_SIZE: tuple[float, float] = (44.0, 22.0)


def _svg_asset_src(ref: str, rel_path: str) -> str:
    """Path used inside the runtime dir, relative to ``index.html``."""
    return f"characters/{ref}/{rel_path}"


def _register_texture(
    textures: dict[str, AssetJSON],
    alias: str,
    src: str,
) -> str:
    """Add a texture entry if not already present; return ``alias``."""
    if alias not in textures:
        textures[alias] = AssetJSON(src=src)
    return alias


def _build_svg_character_subtree(
    entity: AssetRef,
    desc: dict[str, Any],
    *,
    textures: dict[str, AssetJSON],
) -> NodeJSON:
    """Build a Sprite-based subtree for a Phase-11a CharacterDescriptor."""
    ref = entity.ref or entity.id
    name = desc.get("name", ref)

    # Register all the body / face / mouth parts as Pixi assets. Aliases follow
    # ``<entity_id>.<slot>`` (instance-specific, so two scene entities backed by
    # the same character ref get isolated alias namespaces — avoids collisions
    # if they ever diverge.)
    def _reg(slot: str, rel: str) -> str:
        return _register_texture(
            textures,
            f"{entity.id}.{slot}",
            _svg_asset_src(ref, rel),
        )

    head_alias = _reg("head", "parts/head.svg")
    torso_alias = _reg("torso", "parts/torso.svg")
    arm_l_alias = _reg("arm_l", "parts/arm_l.svg")
    arm_r_alias = _reg("arm_r", "parts/arm_r.svg")
    leg_l_alias = _reg("leg_l", "parts/leg_l.svg")
    leg_r_alias = _reg("leg_r", "parts/leg_r.svg")
    eye_l_alias = _reg("eye_l_open", "parts/eye_l_open.svg")
    eye_r_alias = _reg("eye_r_open", "parts/eye_r_open.svg")
    brow_l_alias = _reg("brow_l", "parts/brow_l.svg")
    brow_r_alias = _reg("brow_r", "parts/brow_r.svg")
    viseme_aliases: dict[str, str] = {}
    for shape in MOUTH_SHAPES:
        alias = _reg(f"mouth_{shape}", f"parts/mouth/mouth_{shape}.svg")
        viseme_aliases[shape.upper()] = alias
    # Default mouth attachment is the rest viseme (X).
    mouth_alias = viseme_aliases["X"]

    # Per-character viseme map: Rhubarb letter → asset alias. Carried on the
    # mouth visual so the runtime can swap textures on the existing
    # `<entity>/head/mouth:viseme` channel without IR plumbing.
    viseme_map: dict[str, str] = {}
    desc_map = desc.get("viseme_map") or {}
    for letter in ("A", "B", "C", "D", "E", "F", "G", "H", "X"):
        attachment = desc_map.get(letter, f"mouth_{letter.lower()}")
        viseme_map[letter] = f"{entity.id}.{attachment}"

    # If the head art has its own face baked in (DiceBear / hand-drawn full
    # avatars), don't overlay separate eye/brow/mouth sprites — they double
    # up with the baked features. The mouth overlay used to attach so it
    # could carry the lip-sync channel, but it sits below the avatar's
    # natural mouth and reads as awkward. Per SESSION_HANDOFF.md §3 we lock
    # it off too: lip-sync stays audio-only for these characters, and
    # production scenes with dialogue should hand-rig characters following
    # the Pose Animator convention (see ``examples/promote_demo/``).
    metadata = desc.get("metadata") or {}
    head_has_face = metadata.get("art_provenance") in ("dicebear", "external_avatar")

    leg_y = 70.0
    arm_y = -10.0
    head_y = -100.0
    torso_y = 0.0

    head_children: list[NodeJSON] = []
    if not head_has_face:
        head_children.extend(
            [
                # Eyes — paths match the procedural-blink regex so the
                # existing scale.y squash works on Sprites unchanged.
                NodeJSON(
                    name="left_eye",
                    transform=TransformJSON(x=-14.0, y=-6.0),
                    visual=VisualJSON(
                        kind="svg_sprite",
                        asset_id=eye_l_alias,
                        width=_SVG_EYE_SIZE[0],
                        height=_SVG_EYE_SIZE[1],
                    ),
                ),
                NodeJSON(
                    name="right_eye",
                    transform=TransformJSON(x=14.0, y=-6.0),
                    visual=VisualJSON(
                        kind="svg_sprite",
                        asset_id=eye_r_alias,
                        width=_SVG_EYE_SIZE[0],
                        height=_SVG_EYE_SIZE[1],
                    ),
                ),
                NodeJSON(
                    name="left_brow",
                    transform=TransformJSON(x=-14.0, y=-18.0),
                    visual=VisualJSON(
                        kind="svg_sprite",
                        asset_id=brow_l_alias,
                        width=_SVG_BROW_SIZE[0],
                        height=_SVG_BROW_SIZE[1],
                    ),
                ),
                NodeJSON(
                    name="right_brow",
                    transform=TransformJSON(x=14.0, y=-18.0),
                    visual=VisualJSON(
                        kind="svg_sprite",
                        asset_id=brow_r_alias,
                        width=_SVG_BROW_SIZE[0],
                        height=_SVG_BROW_SIZE[1],
                    ),
                ),
            ]
        )
    if not head_has_face:
        head_children.append(
            NodeJSON(
                name="mouth",
                transform=TransformJSON(x=0.0, y=22.0),
                visual=VisualJSON(
                    kind="svg_sprite",
                    asset_id=mouth_alias,
                    width=_SVG_MOUTH_SIZE[0],
                    height=_SVG_MOUTH_SIZE[1],
                    viseme_assets=viseme_map,
                ),
            )
        )

    head_node = NodeJSON(
        name="head",
        transform=TransformJSON(x=0.0, y=head_y),
        visual=VisualJSON(
            kind="svg_sprite",
            asset_id=head_alias,
            width=_SVG_HEAD_SIZE,
            height=_SVG_HEAD_SIZE,
        ),
        children=head_children,
    )

    children: list[NodeJSON] = [
        # Legs first (back of draw order)
        NodeJSON(
            name="leg_l",
            transform=TransformJSON(x=-14.0, y=leg_y),
            visual=VisualJSON(
                kind="svg_sprite",
                asset_id=leg_l_alias,
                width=_SVG_LEG_SIZE[0],
                height=_SVG_LEG_SIZE[1],
                anchor_y=0.0,
            ),
        ),
        NodeJSON(
            name="leg_r",
            transform=TransformJSON(x=14.0, y=leg_y),
            visual=VisualJSON(
                kind="svg_sprite",
                asset_id=leg_r_alias,
                width=_SVG_LEG_SIZE[0],
                height=_SVG_LEG_SIZE[1],
                anchor_y=0.0,
            ),
        ),
        # Torso
        NodeJSON(
            name="torso",
            transform=TransformJSON(x=0.0, y=torso_y),
            visual=VisualJSON(
                kind="svg_sprite",
                asset_id=torso_alias,
                width=_SVG_TORSO_SIZE[0],
                height=_SVG_TORSO_SIZE[1],
            ),
        ),
        # Arms (in front of torso)
        NodeJSON(
            name="arm_l",
            transform=TransformJSON(x=-60.0, y=arm_y),
            visual=VisualJSON(
                kind="svg_sprite",
                asset_id=arm_l_alias,
                width=_SVG_ARM_SIZE[0],
                height=_SVG_ARM_SIZE[1],
                anchor_y=0.0,
            ),
        ),
        NodeJSON(
            name="arm_r",
            transform=TransformJSON(x=60.0, y=arm_y),
            visual=VisualJSON(
                kind="svg_sprite",
                asset_id=arm_r_alias,
                width=_SVG_ARM_SIZE[0],
                height=_SVG_ARM_SIZE[1],
                anchor_y=0.0,
            ),
        ),
        # Head on top
        head_node,
    ]

    return NodeJSON(
        name=entity.id,
        transform=TransformJSON(),
        slots={"root": SlotJSON(name="root")},
        children=children,
    )


# -----------------------------------------------------------------------------
# Action → animations + timeline tracks
# -----------------------------------------------------------------------------


def _compile_actions(
    actions: list[Action], shot_duration: float
) -> tuple[dict[str, AnimationClipJSON], list[TrackJSON]]:
    """Flatten authoring actions and convert to per-action animation clips."""
    animations: dict[str, AnimationClipJSON] = {}
    placed_by_track: dict[str, list[PlacedClipJSON]] = {}

    flat_list: list[FlatAction] = []
    for action in actions:
        flat_list.extend(flatten(action))

    for i, flat in enumerate(flat_list):
        anim_id, track_root, placed = _compile_one(flat, ordinal=i)
        if anim_id is not None:
            # Built a fresh animation; register it.
            (animations[anim_id],) = (
                animations.get(anim_id, _build_anim_for(flat, anim_id)),
            )
            if anim_id not in animations:
                animations[anim_id] = _build_anim_for(flat, anim_id)
            else:
                # idempotent: ensure registered
                pass
        # Always register: rebuild map cleanly
        if anim_id is not None and anim_id not in animations:
            animations[anim_id] = _build_anim_for(flat, anim_id)
        placed_by_track.setdefault(track_root, []).append(placed)

    # Re-pass: every referenced animation must actually exist.
    #
    # This used to fabricate an empty, channel-less clip for anything missing,
    # which is how `play` came to look wired up while animating nothing: the clip
    # was present, carried the right duration, and moved not one property. The
    # `__play__` prefix guard here was dead code — nothing ever produced that
    # prefix — so the fabrication always fired.
    for placed_list in placed_by_track.values():
        for p in placed_list:
            if p.animation_id not in animations:
                raise CutoutCompileError(
                    f"action references animation {p.animation_id!r}, which is "
                    "not defined. Named reusable animations are not implemented: "
                    "`play` has nowhere to look them up from, so it cannot be "
                    "honoured — see https://github.com/thorwhalen/an/issues/7. "
                    "Use `tween` / `set` actions, or `sequence` / `parallel` to "
                    "compose them."
                )

    tracks = [
        TrackJSON(target_root=root, clips=clips)
        for root, clips in placed_by_track.items()
    ]
    return animations, tracks


def _compile_one(
    flat: FlatAction, *, ordinal: int
) -> tuple[str | None, str, PlacedClipJSON]:
    """Convert one FlatAction into (animation_id, track_root, placed)."""
    action = flat.action
    if isinstance(action, TweenAction):
        anim_id = f"__tween__{ordinal}"
        placed = PlacedClipJSON(
            animation_id=anim_id,
            start_time=flat.start,
            duration=action.duration,
        )
        return anim_id, _track_root_of(action.target), placed
    if isinstance(action, SetAction):
        anim_id = f"__set__{ordinal}"
        placed = PlacedClipJSON(
            animation_id=anim_id, start_time=flat.start, duration=0.001
        )
        return anim_id, _track_root_of(action.target), placed
    if isinstance(action, PlayAction):
        # Reference to an externally-declared animation (e.g. shot.options
        # could hold them); Phase 2A leaves this thin.
        placed = PlacedClipJSON(
            animation_id=action.animation,
            start_time=flat.start,
            duration=action.duration,
            speed=action.speed,
        )
        return None, _track_root_of(action.target), placed
    raise TypeError(f"unsupported FlatAction.action type: {type(action).__name__}")


def _build_anim_for(flat: FlatAction, anim_id: str) -> AnimationClipJSON:
    action = flat.action
    if isinstance(action, TweenAction):
        from_value = (
            action.from_value
            if action.from_value is not None
            else _rest_value_for(action.property, action.target)
        )
        return AnimationClipJSON(
            name=anim_id,
            duration=action.duration,
            channels=[
                ChannelJSON(
                    target=action.target,
                    property=action.property,
                    keyframes=[
                        KeyframeJSON(
                            time=0.0,
                            value=from_value,
                            easing=_easing_to_json(action.easing),
                        ),
                        KeyframeJSON(time=action.duration, value=action.to_value),
                    ],
                )
            ],
        )
    if isinstance(action, SetAction):
        return AnimationClipJSON(
            name=anim_id,
            duration=0.001,
            channels=[
                ChannelJSON(
                    target=action.target,
                    property=action.property,
                    keyframes=[
                        KeyframeJSON(time=0.0, value=action.value, easing="step")
                    ],
                )
            ],
        )
    raise TypeError(f"unsupported anim build for {type(action).__name__}")


def _easing_to_json(spec: Any) -> Any:
    if spec is None:
        return None
    if isinstance(spec, str):
        return spec
    if isinstance(spec, (list, tuple)):
        return list(spec)
    return None


def _track_root_of(target: str) -> str:
    """The first segment of a target path is the track root (the entity name)."""
    return target.split("/", 1)[0] if target else ""


# -----------------------------------------------------------------------------
# Phase 4: dialogue → viseme channels on the speaker's mouth node
# -----------------------------------------------------------------------------


_FACE_BAKED_PROVENANCES: tuple[str, ...] = ("dicebear", "external_avatar")


def _face_baked_speakers(shot: Shot, mall: Mapping[str, Mapping] | None) -> set[str]:
    """Return the entity ids whose backing descriptor has a face baked in.

    Used to suppress viseme channels for characters that don't have an
    overlay mouth node (DiceBear / external avatars). See
    ``_build_svg_character_subtree`` for the matching scene-tree branch.
    """
    if not mall:
        return set()
    chars_store = mall.get("characters") or {}
    out: set[str] = set()
    for entity in shot.entities:
        if entity.kind != "character":
            continue
        ref = entity.ref
        if ref is None or ref not in chars_store:
            continue
        try:
            desc = chars_store[ref]
        except KeyError:
            continue
        if not isinstance(desc, dict):
            continue
        provenance = (desc.get("metadata") or {}).get("art_provenance")
        if provenance in _FACE_BAKED_PROVENANCES:
            out.add(entity.id)
    return out


def _add_viseme_clips(
    shot: Shot,
    animations: dict[str, AnimationClipJSON],
    tracks: list[TrackJSON],
    *,
    mall: Mapping[str, Mapping] | None = None,
    node_paths: set[str] | None = None,
) -> None:
    """For each dialogue line with a viseme_track, emit a step-channel that
    drives ``<speaker>/head/mouth:viseme`` over the line's time span.

    Side-effects ``animations`` (adds named clips) and ``tracks`` (appends to
    or creates the speaker's track).

    Two kinds of speaker get no viseme channel, for the same reason: there is no
    mouth node for it to target.

    - Speakers backed by a face-baked descriptor (``art_provenance`` of
      ``"dicebear"`` or ``"external_avatar"``) — the face is drawn into the head
      SVG, so there is no overlay mouth.
    - **Speakers whose mouth node is not in the built scene.** One condition,
      two cases that are indistinguishable from here: the off-screen-narrator
      idiom (a speaker deliberately not an entity — the standing workaround
      while ``Shot.narration`` is unimplemented), and a character who IS on
      screen but whose rig has no head, which an entity-membership check misses
      and sends to a hard render failure.

    The second kind WARNS rather than passing in silence, because it cannot be
    told apart from a typo: ``speaker="charlei"`` against an on-screen
    ``charlie`` otherwise loses its lip-sync quietly while the audio still
    plays. Naming the scene's actual mouths makes the typo obvious.
    """
    face_baked = _face_baked_speakers(shot, mall)
    # `is not None`, not truthiness: a shot with no entities has an EMPTY path
    # set, and that is precisely a scene where no speaker has a mouth. Treating
    # empty as "cannot check" let the emptiest case through to a hard render
    # failure — which is how this was caught.
    paths = node_paths
    track_lookup: dict[str, TrackJSON] = {t.target_root: t for t in tracks}
    for i, line in enumerate(shot.dialogue):
        if line.viseme_track is None or not line.viseme_track.keyframes:
            continue
        if line.start is None or line.duration is None:
            # No timing assigned (audio pipeline didn't run); skip silently.
            continue
        speaker = line.speaker
        if speaker in face_baked:
            continue
        target = f"{speaker}/head/mouth"
        if paths is not None and target not in paths:
            mouths = sorted(p for p in paths if p.endswith("/head/mouth"))
            warnings.warn(
                f"shot {shot.id!r} dialogue line {i} is spoken by {speaker!r}, which "
                f"has no mouth node ({target!r} is not in the scene): it gets audio "
                "but no lip-sync. Expected for an off-screen narrator. If it was "
                f"not, the scene's mouths are: {mouths or 'none'}.",
                CutoutCompileWarning,
                stacklevel=2,
            )
            continue
        anim_id = f"__viseme__{shot.id}_{i}"

        # Build viseme keyframes (step-easing) — but cap density so adjacent
        # keyframes are at least _MIN_VISEME_GAP_S apart. Reduces the
        # "twitchy" look of per-character distribution at high densities.
        raw = [(float(kf.time), str(kf.viseme)) for kf in line.viseme_track.keyframes]
        condensed: list[tuple[float, str]] = []
        for t, v in raw:
            if condensed and (t - condensed[-1][0]) < _MIN_VISEME_GAP_S:
                continue
            condensed.append((t, v))
        kfs: list[KeyframeJSON] = []
        for t, v in condensed:
            t = max(0.0, min(line.duration, t))
            kfs.append(KeyframeJSON(time=t, value=v, easing="step"))
        # Always end with rest so the mouth closes when the line stops.
        if kfs and kfs[-1].time < line.duration:
            kfs.append(KeyframeJSON(time=line.duration, value="X", easing="step"))

        animations[anim_id] = AnimationClipJSON(
            name=anim_id,
            duration=line.duration,
            channels=[
                ChannelJSON(target=target, property="viseme", keyframes=kfs),
            ],
        )

        placed = PlacedClipJSON(
            animation_id=anim_id,
            start_time=float(line.start),
            duration=float(line.duration),
        )
        track = track_lookup.get(speaker)
        if track is None:
            track = TrackJSON(target_root=speaker, clips=[])
            tracks.append(track)
            track_lookup[speaker] = track
        track.clips.append(placed)

        # Emotion-driven eyebrow expression — set both brows' rotation while
        # the line is active, restore to neutral at the end.
        emotion = (line.emotion or "").lower().strip()
        if emotion in _EMOTION_BROWS:
            tilt_l, tilt_r = _EMOTION_BROWS[emotion]
            for brow_name, tilt in (("left_brow", tilt_l), ("right_brow", tilt_r)):
                emo_anim_id = f"__emo__{shot.id}_{i}_{brow_name}"
                animations[emo_anim_id] = AnimationClipJSON(
                    name=emo_anim_id,
                    duration=line.duration,
                    channels=[
                        ChannelJSON(
                            target=f"{speaker}/head/{brow_name}",
                            property="rotation",
                            keyframes=[
                                KeyframeJSON(time=0.0, value=tilt, easing="step"),
                                KeyframeJSON(
                                    time=line.duration, value=0.0, easing="step"
                                ),
                            ],
                        )
                    ],
                )
                track.clips.append(
                    PlacedClipJSON(
                        animation_id=emo_anim_id,
                        start_time=float(line.start),
                        duration=float(line.duration),
                    )
                )


# -----------------------------------------------------------------------------
# Phase 7: camera moves wired to root-container scale animation
# -----------------------------------------------------------------------------


# How much each named camera move zooms (final scale relative to start).
_CAMERA_MOVES: dict[str, tuple[float, float]] = {
    "hold": (1.0, 1.0),
    "push_in": (1.0, 1.25),
    "pull_out": (1.0, 0.8),
    "zoom_in": (1.0, 1.5),
    "zoom_out": (1.0, 0.7),
}


def _add_camera_clips(
    shot: Shot,
    animations: dict[str, AnimationClipJSON],
    tracks: list[TrackJSON],
) -> None:
    """If shot.camera.move is a known named move, emit a scale tween on the
    synthetic scene root over the shot's full duration.

    The synthetic root container in the JS runtime sits at canvas center and
    scales the entire scene; per-character motion remains independent.
    """
    if shot.camera is None or shot.camera.move is None:
        return
    # Normalise BEFORE the emptiness test, or the guard grows an arbitrary seam:
    # `move=""` fell through the falsiness check and was ignored, while
    # `move="  "` reached the lookup and raised. Same input, two behaviours.
    move = shot.camera.move.strip()
    if not move:
        return
    if move == "hold":
        return  # a real, correct no-op — not an unknown move
    if move not in _CAMERA_MOVES:
        raise CutoutCompileError(
            f"shot {shot.id!r} asks for camera.move={move!r}, which the cutout "
            f"renderer does not implement (it has: {sorted(_CAMERA_MOVES)}). "
            "A translating camera — pan, track, whip-pan — needs a real 2D "
            "camera node with per-layer parallax, which is planned (see "
            "https://github.com/thorwhalen/an/issues/9); today the camera is a "
            "scale tween on the scene root and cannot move sideways."
        )
    start_scale, end_scale = _CAMERA_MOVES[move]
    duration = max(0.001, float(shot.duration))
    for axis in ("scale_x", "scale_y"):
        anim_id = f"__camera__{shot.id}_{axis}"
        animations[anim_id] = AnimationClipJSON(
            name=anim_id,
            duration=duration,
            channels=[
                ChannelJSON(
                    target="root",
                    property=axis,
                    keyframes=[
                        KeyframeJSON(time=0.0, value=start_scale, easing="ease_in_out"),
                        KeyframeJSON(time=duration, value=end_scale),
                    ],
                )
            ],
        )
        tracks.append(
            TrackJSON(
                target_root="__camera__",
                clips=[
                    PlacedClipJSON(
                        animation_id=anim_id, start_time=0.0, duration=duration
                    )
                ],
            )
        )
