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

from an.adapters.cutout.clip import Clip
from an.adapters.cutout.clip import evaluate as _evaluate_clip
from an.adapters.cutout.pose import Pose, merge_poses


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
