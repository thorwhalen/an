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

    scene_root = _build_scene_root(shot, mall)
    animations, tracks = _compile_actions(shot.actions, shot.duration)
    # Phase 4: emit a viseme channel per dialogue line that has a viseme_track.
    _add_viseme_clips(shot, animations, tracks)
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
        assets=AssetsJSON(),  # Asset table populated in 2C when art exists
    )


# -----------------------------------------------------------------------------
# Scene tree construction
# -----------------------------------------------------------------------------


def _build_scene_root(shot: Shot, mall: Mapping[str, Mapping]) -> NodeJSON:
    """Construct the cutout scene tree under a single root from shot.entities.

    Multiple characters get spread along the x-axis so they don't overlap.
    For N characters, positions are evenly distributed across a fixed band;
    a single character lives at the center.
    """
    children: list[NodeJSON] = []
    characters_store = mall.get("characters") or {}
    char_entities = [e for e in shot.entities if e.kind == "character"]
    n_chars = len(char_entities)
    char_positions = _layout_character_positions(n_chars)
    char_idx = 0
    for entity in shot.entities:
        if entity.kind == "character":
            x = char_positions[char_idx]
            char_idx += 1
            sub = _build_character_subtree(entity, characters_store)
            sub.transform.x = x
            children.append(sub)
        # Other entity kinds (environment, prop) get sketched in later phases.
    return NodeJSON(name="root", children=children)


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


def _build_character_subtree(entity: AssetRef, characters_store: Mapping) -> NodeJSON:
    """Build a NodeJSON subtree for one character, with placeholder parts.

    If the characters store has the character's metadata, we honor any
    declared part list / visuals; otherwise we fall back to ``_PLACEHOLDER_PARTS``
    so the rest of the pipeline can run before art exists.
    """
    char_meta: dict[str, Any] = {}
    if entity.ref in characters_store:
        try:
            value = characters_store[entity.ref]
            if isinstance(value, dict):
                char_meta = value
        except KeyError:
            char_meta = {}

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
        children.append(
            NodeJSON(
                name=part,
                transform=TransformJSON(x=float(geom["x"]), y=float(geom["y"])),
                visual=VisualJSON(
                    kind="rect",
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
        # Head children: hair patch on top, two eyes, mouth (driven by the
        # viseme channel). All are tiny PixiJS Graphics children of the head
        # container, so they inherit head's transform automatically.
        for child in char_node.children:
            if child.name != "head":
                continue
            child.slots["mouth"] = SlotJSON(name="mouth", x=0, y=15)
            # Hair: small horizontal band atop the head.
            child.children.append(
                NodeJSON(
                    name="hair",
                    transform=TransformJSON(x=0.0, y=-22.0),
                    visual=VisualJSON(kind="rect", width=44.0, height=10.0, color=hair),
                )
            )
            # Eyes: two small dark squares.
            for eye_name, ex in (("left_eye", -10.0), ("right_eye", 10.0)):
                child.children.append(
                    NodeJSON(
                        name=eye_name,
                        transform=TransformJSON(x=ex, y=-3.0),
                        visual=VisualJSON(
                            kind="rect", width=6.0, height=6.0, color="#1a1a1a"
                        ),
                    )
                )
            # Mouth (viseme target).
            child.children.append(
                NodeJSON(
                    name="mouth",
                    transform=TransformJSON(x=0.0, y=15.0),
                    visual=VisualJSON(
                        kind="rect", width=20.0, height=4.0, color="#552222"
                    ),
                )
            )
    return char_node


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

    # Re-pass: ensure we built every named animation we referenced.
    for placed_list in placed_by_track.values():
        for p in placed_list:
            if p.animation_id not in animations and not p.animation_id.startswith(
                "__play__"
            ):
                # Should not happen; defensive
                animations[p.animation_id] = AnimationClipJSON(
                    name=p.animation_id, duration=p.duration or 0.001
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
        from_value = action.from_value if action.from_value is not None else 0.0
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


def _add_viseme_clips(
    shot: Shot,
    animations: dict[str, AnimationClipJSON],
    tracks: list[TrackJSON],
) -> None:
    """For each dialogue line with a viseme_track, emit a step-channel that
    drives ``<speaker>/head/mouth:viseme`` over the line's time span.

    Side-effects ``animations`` (adds named clips) and ``tracks`` (appends to
    or creates the speaker's track).
    """
    track_lookup: dict[str, TrackJSON] = {t.target_root: t for t in tracks}
    for i, line in enumerate(shot.dialogue):
        if line.viseme_track is None or not line.viseme_track.keyframes:
            continue
        if line.start is None or line.duration is None:
            # No timing assigned (audio pipeline didn't run); skip silently.
            continue
        speaker = line.speaker
        target = f"{speaker}/head/mouth"
        anim_id = f"__viseme__{shot.id}_{i}"

        # Build keyframes: every viseme keyframe with step easing.
        # Pin the first keyframe at time 0 (channel-local) and the last at the
        # line's duration so the channel covers the full window.
        kfs: list[KeyframeJSON] = []
        for j, kf in enumerate(line.viseme_track.keyframes):
            t = max(0.0, min(line.duration, float(kf.time)))
            kfs.append(KeyframeJSON(time=t, value=kf.viseme, easing="step"))
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
    if shot.camera is None or not shot.camera.move:
        return
    move = shot.camera.move
    if move not in _CAMERA_MOVES or move == "hold":
        return
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
