"""Timeline: tracks of placed clips with absolute times and blend ramps.

A `Timeline` is a flat description of *what plays when*. It's the canonical
form passed downstream to the JS runtime in Phase 2B. Authoring composition
trees from `an.ir.compose` (sequence/parallel/etc.) get *flattened into* a
Timeline by `compile_shot` (see `compile.py`).

Evaluation semantics in Phase 2A:

- For each track, identify all clips active at time ``t``.
- Each active clip produces a `Pose`.
- Clips on **the same track** override each other in start-order (later wins).
- Clips on **different tracks** merge with later-track override semantics
  (track order in the list determines priority — last track wins on conflict).
- ``blend_in`` and ``blend_out`` ramps are recorded but **not yet applied** to
  pose values in 2A — the timeline produces the raw Pose and the renderer
  decides what to do with the ramps. Additive blending lands in 2B.

>>> from an.adapters.cutout.channel import Channel, Keyframe
>>> from an.adapters.cutout.clip import Clip
>>> ch = Channel("a", "x", [Keyframe(0.0, 0.0), Keyframe(1.0, 10.0)])
>>> clip = Clip("walk", duration=1.0, channels=[ch])
>>> tl = Timeline(duration=2.0, tracks=[Track("a", clips=[PlacedClip(clip, start_time=0.5)])])
>>> evaluate_timeline(tl, 1.0)[("a", "x")]
5.0
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from an.adapters.cutout.clip import Clip, Pose, merge_poses
from an.adapters.cutout.clip import evaluate as _evaluate_clip

if TYPE_CHECKING:  # pragma: no cover - types only
    from an.adapters.cutout.serialize import CutoutSceneJSON


@dataclass(slots=True)
class PlacedClip:
    """A clip placed at an absolute time on a track."""

    clip: Clip
    start_time: float = 0.0
    duration: float | None = None  # override; None = clip's natural duration
    speed: float = 1.0
    blend_in: float = 0.0
    blend_out: float = 0.0

    def __post_init__(self) -> None:
        if self.speed <= 0:
            raise ValueError(f"PlacedClip speed must be > 0; got {self.speed}")
        if self.blend_in < 0 or self.blend_out < 0:
            raise ValueError("blend_in/out must be non-negative")

    @property
    def effective_duration(self) -> float:
        """Duration this clip occupies on the timeline (after speed scaling)."""
        natural = self.duration if self.duration is not None else self.clip.duration
        return natural / self.speed

    @property
    def end_time(self) -> float:
        return self.start_time + self.effective_duration


@dataclass(slots=True)
class Track:
    """A sequence of placed clips that share a common purpose / target prefix.

    ``target_root`` is informational metadata for downstream tools (the JS
    runtime can use it to scope rendering); evaluation does not filter by it.
    """

    target_root: str = ""
    clips: list[PlacedClip] = field(default_factory=list)


@dataclass(slots=True)
class Timeline:
    """A duration + ordered list of tracks. The canonical playback structure."""

    duration: float
    tracks: list[Track] = field(default_factory=list)


def evaluate_timeline(timeline: Timeline, t: float) -> Pose:
    """Evaluate ``timeline`` at time ``t``, merging poses across tracks/clips."""
    track_poses: list[Pose] = []
    for track in timeline.tracks:
        active_poses: list[Pose] = []
        for placed in track.clips:
            # Inclusive-end semantics: a clip at [s, e] is active at t==e too.
            # This matches the natural reading of "play this clip from 0 to 1s"
            # (the final frame should still be visible at t=1.0).
            if placed.start_time <= t <= placed.end_time:
                local_t = (t - placed.start_time) * placed.speed
                active_poses.append(_evaluate_clip(placed.clip, local_t))
        if active_poses:
            track_poses.append(merge_poses(*active_poses))
    if not track_poses:
        return {}
    return merge_poses(*track_poses)


def timeline_from_scene(scene: CutoutSceneJSON) -> Timeline:
    """The compiled scene's `timeline`/`animations` as this module's `Timeline`.

    `compile_shot` produces a serialisable document (`an.adapters.cutout.serialize`)
    for the JS runtime; this rebuilds the *evaluable* form, so a caller can ask
    what a compiled scene's pose is at time `t` without a browser. It is the
    Python side of the parity contract: `evaluate_timeline` over this object is
    the executable spec `runtime.js` is tested against.

    Two fields are carried rather than defaulted, and both have cost a bug:
    `loop_mode` (without it every loop evaluated as `once` — an#7) and a
    list-valued `easing`, which is a cubic-bezier control quadruple and must
    stay a tuple for `Keyframe`.

    >>> from an.adapters.cutout.serialize import (
    ...     AnimationClipJSON, ChannelJSON, CutoutSceneJSON, KeyframeJSON,
    ...     NodeJSON, PlacedClipJSON, TimelineJSON, TrackJSON,
    ... )
    >>> scene = CutoutSceneJSON(
    ...     scene=NodeJSON(name="root"),
    ...     animations={"slide": AnimationClipJSON(
    ...         name="slide", duration=1.0,
    ...         channels=[ChannelJSON(target="a", property="x", keyframes=[
    ...             KeyframeJSON(time=0.0, value=0.0),
    ...             KeyframeJSON(time=1.0, value=10.0),
    ...         ])],
    ...     )},
    ...     timeline=TimelineJSON(duration=2.0, tracks=[
    ...         TrackJSON(target_root="a", clips=[PlacedClipJSON(animation_id="slide", start_time=0.5)]),
    ...     ]),
    ... )
    >>> evaluate_timeline(timeline_from_scene(scene), 1.0)[("a", "x")]
    5.0
    """
    from an.adapters.cutout.channel import Channel, Keyframe
    from an.adapters.cutout.clip import LoopMode

    clips = {
        aid: Clip(
            aid,
            duration=a.duration,
            loop_mode=LoopMode(a.loop_mode),
            channels=[
                Channel(
                    ch.target,
                    ch.property,
                    [
                        Keyframe(
                            k.time,
                            k.value,
                            tuple(k.easing) if isinstance(k.easing, list) else k.easing,
                        )
                        for k in ch.keyframes
                    ],
                )
                for ch in a.channels
            ],
        )
        for aid, a in scene.animations.items()
    }
    return Timeline(
        duration=scene.timeline.duration,
        tracks=[
            Track(
                t.target_root,
                [
                    PlacedClip(clips[p.animation_id], p.start_time, p.duration, p.speed)
                    for p in t.clips
                ],
            )
            for t in scene.timeline.tracks
        ],
    )
