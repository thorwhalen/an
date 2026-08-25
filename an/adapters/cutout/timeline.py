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

import math
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from an.adapters.cutout.channel import Channel, Keyframe
from an.adapters.cutout.clip import Clip, LoopMode, Pose, merge_poses
from an.adapters.cutout.clip import evaluate as _evaluate_clip

from an.adapters.cutout.serialize import TransformJSON

if TYPE_CHECKING:  # pragma: no cover - types only
    from an.adapters.cutout.serialize import CutoutSceneJSON, NodeJSON


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

    >>> from an.adapters.cutout.compile import compile_shot
    >>> from an.ir.compose import tween
    >>> from an.ir.schema import Shot
    >>> shot = Shot(id="s1", renderer="cutout", duration=2.0,
    ...             actions=[tween("root", "x", 10.0, 1.0, from_=0.0)])
    >>> scene = compile_shot(shot, mall=None, fps=24)
    >>> evaluate_timeline(timeline_from_scene(scene), 0.5)[("root", "x")]
    5.0
    """
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
                    PlacedClip(
                        clips[p.animation_id],
                        p.start_time,
                        p.duration,
                        p.speed,
                        # Carried for the same reason as `target_root`: nothing
                        # reads them YET (`evaluate_timeline` records the ramps
                        # and does not apply them — additive blending is 2B),
                        # and a reader that quietly drops a field it was handed
                        # is a lossy "rebuilds the evaluable form".
                        p.blend_in,
                        p.blend_out,
                    )
                    for p in t.clips
                ],
            )
            for t in scene.timeline.tracks
        ],
    )


# --- screen space -------------------------------------------------------------
#
# `evaluate_timeline` returns a POSE — `{(target, property): value}` — and a
# pose is not a position. Nothing in `an/` composed one until an#111, which is
# why the pan measurement could not be written: a rigid pan on `root` leaves
# every plane's LOCAL x at zero, so a local channel reads "no parallax" for a
# stage that is parallaxing correctly.


@dataclass(frozen=True, slots=True)
class Transform2D:
    """One node's local transform, in the runtime's own vocabulary.

    Field names and defaults mirror `applyTransform` in `runtime.js` exactly —
    `x`, `y`, `rotation`, `scale_x`, `scale_y`, `pivot_x`, `pivot_y` — because
    the point of this class is to agree with the vendored engine rather than to
    re-derive it. `skew` is deliberately absent: PixiJS composes skew into the
    same matrix, but no emitter in this package produces a skew channel, and a
    field nothing writes is a claim this compositor cannot honour.
    """

    x: float = 0.0
    y: float = 0.0
    rotation: float = 0.0
    scale_x: float = 1.0
    scale_y: float = 1.0
    pivot_x: float = 0.0
    pivot_y: float = 0.0

    def unapply(self, point: tuple[float, float]) -> tuple[float, float]:
        """The inverse of :meth:`apply` — a parent-space point, in local space.

        >>> t = Transform2D(x=10.0, pivot_x=3.0, scale_x=2.0, rotation=0.4)
        >>> round(t.unapply(t.apply((7.0, -2.0)))[0], 9)
        7.0
        """
        px, py = point[0] - self.x, point[1] - self.y
        if self.rotation:
            cos_r, sin_r = math.cos(-self.rotation), math.sin(-self.rotation)
            px, py = px * cos_r - py * sin_r, px * sin_r + py * cos_r
        return px / self.scale_x + self.pivot_x, py / self.scale_y + self.pivot_y

    def apply(self, point: tuple[float, float]) -> tuple[float, float]:
        """This node's local point, in its PARENT's coordinates.

        ``world = position + M·(local − pivot)`` — the composition PixiJS
        performs, and the reason `root.pivot` is a 2D camera: moving the pivot
        moves everything the node contains, in the opposite direction.

        >>> Transform2D(x=10.0).apply((0.0, 0.0))
        (10.0, 0.0)
        >>> Transform2D(pivot_x=25.0).apply((0.0, 0.0))
        (-25.0, 0.0)
        >>> Transform2D(scale_x=2.0).apply((5.0, 0.0))
        (10.0, 0.0)
        """
        lx, ly = point[0] - self.pivot_x, point[1] - self.pivot_y
        sx, sy = lx * self.scale_x, ly * self.scale_y
        if self.rotation:
            cos_r, sin_r = math.cos(self.rotation), math.sin(self.rotation)
            sx, sy = sx * cos_r - sy * sin_r, sx * sin_r + sy * cos_r
        return self.x + sx, self.y + sy


def transform_of(node: NodeJSON | None, pose: Pose | None = None) -> Transform2D:
    """A node's transform, with ``pose`` overriding what the document declares.

    The runtime applies a pose value by assigning the property on the display
    object, so a channel REPLACES the declared value rather than adding to it —
    which is why the parallax compensation carries the plane's own offset in
    every keyframe instead of an offset from it.
    """
    t = node.transform if node is not None else TransformJSON()
    values = {
        "x": t.x,
        "y": t.y,
        "rotation": t.rotation,
        "scale_x": t.scale_x,
        "scale_y": t.scale_y,
        "pivot_x": t.pivot_x,
        "pivot_y": t.pivot_y,
    }
    if pose:
        for (_target, prop), value in pose.items():
            if prop in values and isinstance(value, (int, float)):
                values[prop] = float(value)
    return Transform2D(**values)


def screen_position(
    scene: CutoutSceneJSON,
    path: str,
    *,
    pose: Pose | None = None,
    point: tuple[float, float] = (0.0, 0.0),
) -> tuple[float, float]:
    """Where ``point`` in ``path``'s local space lands on the canvas.

    The composition the runtime performs, walked from the node up to `root`
    and then offset by the canvas centre — which is where `runtime.js` places
    the root container.

    ``pose`` is keyed by the FULL path (`"street/hills"`), matching what
    `evaluate_timeline` returns, and each node reads only its own entry.

    >>> from an.adapters.cutout.serialize import CutoutSceneJSON, NodeJSON, TimelineJSON, TransformJSON
    >>> scene = CutoutSceneJSON(
    ...     scene=NodeJSON(name="root", children=[
    ...         NodeJSON(name="hill", transform=TransformJSON(x=40.0))]),
    ...     timeline=TimelineJSON(duration=1.0),
    ... )
    >>> scene.meta.width, scene.meta.height = 320, 240
    >>> screen_position(scene, "hill")
    (200.0, 120.0)

    …and moving the camera's pivot moves it the other way, which is the whole
    reason `root.pivot` is the camera:

    >>> screen_position(scene, "hill", pose={("root", "pivot_x"): 25.0})
    (175.0, 120.0)
    """
    chain = _node_chain(scene.scene, path)
    at = point
    for node, node_path in reversed(chain[1:]):
        at = transform_of(node, _pose_for(pose, node_path)).apply(at)
    # The root LAST, and from the pose alone. `runtime.js` builds its own root
    # container at the canvas centre and says so: "Do NOT apply its transform".
    # What it does apply to that container is pose channels — which is exactly
    # how the camera works, since `root.pivot` is the camera. Composing the
    # document root's declared transform as well would diverge from the engine
    # the moment anything wrote one (an#111 review, L1).
    root, root_path = chain[0]
    at = transform_of(_ROOT_AT_REST, _pose_for(pose, root_path)).apply(at)
    return at[0] + scene.meta.width / 2.0, at[1] + scene.meta.height / 2.0


#: A stand-in for the runtime's own root container: identity, because that is
#: what `runtime.js` creates before applying any pose to it.
_ROOT_AT_REST: Any = None


def _pose_for(pose: Pose | None, path: str) -> Pose | None:
    """The pose entries for one node, re-keyed to its bare name."""
    if not pose:
        return None
    name = path.rsplit("/", 1)[-1]
    return {
        (name, prop): value for (target, prop), value in pose.items() if target == path
    }


def _node_chain(root: NodeJSON, path: str) -> list[tuple[NodeJSON, str]]:
    """``[(node, its path)]`` from ``path`` up to and including the root.

    Raises rather than returning an empty chain: a path that names no node is a
    caller error, and silently measuring the root's position instead is the
    kind of plausible wrong answer this package refuses elsewhere.
    """
    chain: list[tuple[NodeJSON, str]] = [(root, root.name)]
    node = root
    walked: list[str] = []
    for part in path.split("/"):
        found = next((c for c in node.children if c.name == part), None)
        if found is None:
            where = "/".join(walked) or root.name
            raise KeyError(
                f"no node {part!r} under {where!r}; it has "
                f"{[c.name for c in node.children]}"
            )
        walked.append(part)
        node = found
        chain.append((node, "/".join(walked)))
    return chain
