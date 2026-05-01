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
# a (x, y) offset and gives it a distinct fill color. Used only when the
# characters store has no rig info for the requested character.
_PLACEHOLDER_PARTS: tuple[str, ...] = ("head", "torso", "left_arm", "right_arm")
_PLACEHOLDER_PART_LAYOUT: dict[str, dict[str, float | str]] = {
    "head": {"x": 0.0, "y": -55.0, "width": 50.0, "height": 50.0, "color": "#f4c89a"},
    "torso": {"x": 0.0, "y": 0.0, "width": 60.0, "height": 80.0, "color": "#3a6ea5"},
    "left_arm": {
        "x": -50.0,
        "y": -10.0,
        "width": 30.0,
        "height": 70.0,
        "color": "#3a6ea5",
    },
    "right_arm": {
        "x": 50.0,
        "y": -10.0,
        "width": 30.0,
        "height": 70.0,
        "color": "#3a6ea5",
    },
    "left_leg": {
        "x": -18.0,
        "y": 65.0,
        "width": 30.0,
        "height": 70.0,
        "color": "#2c3e50",
    },
    "right_leg": {
        "x": 18.0,
        "y": 65.0,
        "width": 30.0,
        "height": 70.0,
        "color": "#2c3e50",
    },
}


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
    """Construct the cutout scene tree under a single root from shot.entities."""
    children: list[NodeJSON] = []
    characters_store = mall.get("characters") or {}
    for entity in shot.entities:
        if entity.kind == "character":
            children.append(_build_character_subtree(entity, characters_store))
        # Other entity kinds (environment, prop) get sketched in later phases.
    return NodeJSON(name="root", children=children)


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
    children: list[NodeJSON] = []
    for part in parts:
        layout = _PLACEHOLDER_PART_LAYOUT.get(
            part,
            {"x": 0.0, "y": 0.0, "width": 50.0, "height": 50.0, "color": "#cccccc"},
        )
        children.append(
            NodeJSON(
                name=part,
                transform=TransformJSON(x=float(layout["x"]), y=float(layout["y"])),
                visual=VisualJSON(
                    kind="rect",
                    width=float(layout["width"]),
                    height=float(layout["height"]),
                    color=str(layout["color"]),
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
        # Phase 4: head gets a real mouth child node (rendered as a small rect),
        # plus a "mouth" slot for any future attachment overrides. The viseme
        # channel below targets <speaker>/head/mouth and drives its shape.
        for child in char_node.children:
            if child.name == "head":
                child.slots["mouth"] = SlotJSON(name="mouth", x=0, y=15)
                child.children.append(
                    NodeJSON(
                        name="mouth",
                        transform=TransformJSON(x=0.0, y=15.0),
                        visual=VisualJSON(
                            kind="rect",
                            width=20.0,
                            height=4.0,
                            color="#552222",
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
