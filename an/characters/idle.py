"""Idle animation factories: breath, blink, weight-shift.

Defaults are taken from production references (see research §6.3):

- 15 breaths/min ⇒ 4-second period.
- ±2 px torso vertical travel at a 1024-px-tall canonical character height.
- ±0.5° head rotation, phase-offset by 0.25 cycles from the chest.
- Blink closure ≈ 0.13 s; spontaneous blink gap 3-8 s (sampled per scene).

The functions return :class:`an.characters.IdleAnimation` instances ready
to drop into :attr:`CharacterDescriptor.animations`.

>>> a = breath_animation()
>>> a.name
'idle_breath'
>>> [t.target for t in a.tracks][:2]
['bone:torso.y', 'bone:head.rotation_deg']
>>> b = blink_animation()
>>> b.duration
0.18
"""

from __future__ import annotations

import math
import random

from an.characters.schema import AnimationTrack, IdleAnimation


# Canonical defaults — adjustable via keyword args.
DEFAULT_BREATH_PERIOD_S: float = 4.0
DEFAULT_BREATH_AMPLITUDE_PX: float = 2.0
DEFAULT_HEAD_TILT_DEG: float = 0.5
DEFAULT_WEIGHT_SHIFT_PERIOD_S: float = 6.0
DEFAULT_WEIGHT_SHIFT_AMPLITUDE_PX: float = 1.5
DEFAULT_BLINK_DURATION_S: float = 0.18
DEFAULT_BLINK_CLOSURE_S: float = 0.13


def breath_animation(
    *,
    period_s: float = DEFAULT_BREATH_PERIOD_S,
    amplitude_px: float = DEFAULT_BREATH_AMPLITUDE_PX,
    head_tilt_deg: float = DEFAULT_HEAD_TILT_DEG,
    include_weight_shift: bool = True,
    weight_shift_amplitude_px: float = DEFAULT_WEIGHT_SHIFT_AMPLITUDE_PX,
    weight_shift_period_s: float = DEFAULT_WEIGHT_SHIFT_PERIOD_S,
    name: str = "idle_breath",
) -> IdleAnimation:
    """Sine-wave breath on torso Y + head rotation; optional weight shift.

    The head tilt is phase-offset by 0.25 cycles to follow the chest with a
    natural lag. The optional weight shift is on a slower 6-second period to
    avoid a metronomic feel when both run at the same time.

    The animation's ``duration`` is the LCM-ish combined period: the longest
    sub-track period, so the overall loop closes cleanly.
    """
    duration = period_s
    if include_weight_shift:
        duration = max(period_s, weight_shift_period_s)

    tracks = [
        AnimationTrack(
            target="bone:torso.y",
            type="sine",
            amplitude=amplitude_px,
            phase=0.0,
        ),
        AnimationTrack(
            target="bone:head.rotation_deg",
            type="sine",
            amplitude=head_tilt_deg,
            phase=0.25,
        ),
    ]
    if include_weight_shift:
        tracks.append(
            AnimationTrack(
                target="bone:torso.x",
                type="sine",
                amplitude=weight_shift_amplitude_px,
                phase=0.5,
            )
        )
    return IdleAnimation(name=name, duration=duration, loop=True, tracks=tracks)


def blink_animation(
    *,
    closure_s: float = DEFAULT_BLINK_CLOSURE_S,
    duration_s: float = DEFAULT_BLINK_DURATION_S,
    eye_l_slot: str = "left_eye",
    eye_r_slot: str = "right_eye",
    open_attachment_l: str = "open",
    closed_attachment_l: str = "closed",
    open_attachment_r: str = "open",
    closed_attachment_r: str = "closed",
    name: str = "blink",
) -> IdleAnimation:
    """Step-animation that snaps both eye slots closed → open.

    The closure is centred: open → closed at ``(duration - closure) / 2`` →
    open again ``closure`` later. With the defaults (0.13s closure in an
    0.18s envelope) that is closed at 0.025s, open at 0.155s. (An earlier
    docstring claimed 0.05/0.13 — numbers from an older closure value; and
    the slot/attachment defaults were the stale pre-0.2.0 spellings
    ``eye_l``/``eye_l_open``, unnoticed for as long as nothing consumed
    ``descriptor.animations`` — both fixed in an#87.)
    """
    close_in = max(0.0, (duration_s - closure_s) / 2.0)
    close_out = close_in + closure_s
    tracks = [
        AnimationTrack(
            target=f"slot:{eye_l_slot}.attachment",
            type="step",
            frames=[
                (0.0, open_attachment_l),
                (close_in, closed_attachment_l),
                (close_out, open_attachment_l),
            ],
        ),
        AnimationTrack(
            target=f"slot:{eye_r_slot}.attachment",
            type="step",
            frames=[
                (0.0, open_attachment_r),
                (close_in, closed_attachment_r),
                (close_out, open_attachment_r),
            ],
        ),
    ]
    return IdleAnimation(name=name, duration=duration_s, loop=False, tracks=tracks)


def random_blink_schedule(
    duration_s: float,
    *,
    min_gap_s: float = 3.0,
    max_gap_s: float = 8.0,
    seed: int | None = None,
) -> list[float]:
    """Return a sorted list of blink start times across ``duration_s``.

    Used by the renderer to schedule spontaneous blinks. Gaps are uniform in
    ``[min_gap_s, max_gap_s]``.

    >>> times = random_blink_schedule(20.0, seed=0)
    >>> all(0 <= t < 20 for t in times)
    True
    >>> times == sorted(times)
    True
    """
    rng = random.Random(seed)
    out: list[float] = []
    t = rng.uniform(min_gap_s, max_gap_s)
    while t < duration_s:
        out.append(t)
        t += rng.uniform(min_gap_s, max_gap_s)
    return out


def evaluate_track(track: AnimationTrack, t: float, duration: float) -> object:
    """Evaluate a single animation track at time ``t``.

    For sine: ``amplitude * sin(2π * (t/duration + phase))``.
    For step: returns the value of the latest frame whose time ≤ ``t``.
    For linear: linear interpolation between bracketing frames.

    >>> tr = AnimationTrack(target='bone:torso.y', type='sine', amplitude=2.0)
    >>> round(evaluate_track(tr, 0.0, 4.0), 6)
    0.0
    >>> round(evaluate_track(tr, 1.0, 4.0), 6)
    2.0
    """
    if track.type == "sine":
        u = (t / duration if duration > 0 else 0.0) + track.phase
        return float(track.amplitude) * math.sin(2.0 * math.pi * u)
    if track.type in ("step", "linear"):
        frames = list(track.frames)
        if not frames:
            return None
        if track.type == "step":
            current = frames[0][1]
            for ft, fv in frames:
                if t >= ft:
                    current = fv
                else:
                    break
            return current
        # linear
        for i in range(len(frames) - 1):
            a_t, a_v = frames[i]
            b_t, b_v = frames[i + 1]
            if a_t <= t <= b_t:
                span = b_t - a_t
                u = 0.0 if span <= 0 else (t - a_t) / span
                if isinstance(a_v, (int, float)) and isinstance(b_v, (int, float)):
                    return a_v + (b_v - a_v) * u
                return a_v if u < 1.0 else b_v
        return frames[-1][1]
    raise ValueError(f"unsupported track type: {track.type!r}")
