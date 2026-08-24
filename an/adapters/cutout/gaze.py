"""Ambient saccades for a cutout rig's pupils: a seeded generator (an#99, epic #9 Wave 6).

Pure Python, no renderer. Given an entity's name, a shot's duration and its
frame rate, produce a **step** track of pupil offsets — where the eyes rest,
and when they jump — that the face solver adds onto the pupil nodes' ``x`` /
``y`` beside any authored gaze. Steps, not tweens: at 24 fps a frame is
41.7 ms and a small saccade is 20–200 ms, so a jump between two frames is the
honest rendering (the way the compiled blink squash samples at frame times).

The statistics are **design values**, labelled so: the canonical source
("Eyes Alive", Lee, Badler & Badler, SIGGRAPH 2002) was unreachable when this
was designed, and nothing was transcribed from memory. Fixation lengths are
right-skewed with a ~200 ms peak and a long tail, so they are drawn from a
gamma clipped to ``[FIXATION_MIN_S, FIXATION_MAX_S]``; amplitudes are mostly
small with a rare large jump and a horizontal bias; and a jump above
``BLINK_COUPLED_AMPLITUDE`` is moved to the centre of the nearest blink window
within ``BLINK_COUPLING_WINDOW_S`` when one exists, because gaze-evoked blinks
hide the pop.

Seeding follows the blink pattern — a pure function of the entity name — so
renaming a character re-seeds its saccades (the recorded blink hazard); the
seed is stamped into the compiled scene's ``meta`` beside ``blink_phases``.
Integer seeding of :class:`random.Random` is version-stable.

>>> track = saccade_track("gale", duration=2.0, fps=24)
>>> track[0].time, all(t.time <= 2.0 for t in track)
(0.0, True)
>>> track == saccade_track("gale", duration=2.0, fps=24)  # deterministic
True
>>> track != saccade_track("nora", duration=2.0, fps=24)  # per entity
True
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass

#: XOR'd into the entity-name hash so saccades and blinks never share a seed.
GAZE_SALT: int = 0x6A2E
#: Fixation length: a gamma with this shape/scale (peak ≈ 0.2 s, long tail),
#: clipped to the range below. Design values.
FIXATION_GAMMA_SHAPE: float = 2.0
FIXATION_GAMMA_SCALE_S: float = 0.18
FIXATION_MIN_S: float = 0.12
FIXATION_MAX_S: float = 1.5
#: Amplitudes in axis units ([-1, 1] of the rig's declared travel). Most
#: jumps are small; one in `LARGE_JUMP_ODDS` is large. Design values.
SMALL_AMPLITUDE: float = 0.25
LARGE_AMPLITUDE: float = 0.8
LARGE_JUMP_ODDS: int = 6
#: Horizontal bias: vertical amplitude is this fraction of horizontal.
VERTICAL_FRACTION: float = 0.5
#: Blink–saccade coupling: a jump at or above this amplitude is moved to the
#: centre of the nearest blink window within the coupling reach.
BLINK_COUPLED_AMPLITUDE: float = 0.6
BLINK_COUPLING_WINDOW_S: float = 0.15
#: Gaze returns toward centre: each new fixation target is drawn around the
#: rest with this pull (0 = a random walk, 1 = always from the centre).
CENTRE_PULL: float = 0.6


@dataclass(frozen=True, slots=True)
class GazeStep:
    """The pupils rest at ``(x, y)`` (axis units) from ``time`` on."""

    time: float
    x: float
    y: float


def gaze_seed(entity_id: str) -> int:
    """The generator's seed for an entity — a pure function of its name.

    >>> gaze_seed("gale") == gaze_seed("gale") and gaze_seed("gale") != gaze_seed("nora")
    True
    """
    from an.adapters.cutout.compile import _js_string_hash  # the blink's hash; lazy: compile imports this module

    return _js_string_hash(entity_id) ^ GAZE_SALT


def _snap(t: float, fps: int) -> float:
    return round(t * fps) / fps


def saccade_track(
    entity_id: str,
    *,
    duration: float,
    fps: int,
    blink_windows: list[tuple[float, float]] | None = None,
    amplitude: float = 1.0,
) -> list[GazeStep]:
    """Step keyframes on frame times, seeded by ``entity_id``.

    ``amplitude`` scales every jump (0 = the eyes hold centre; the design
    values assume 1). ``blink_windows`` are the entity's compiled blink
    windows, for the coupling rule.
    """
    rng = random.Random(gaze_seed(entity_id))
    windows = list(blink_windows or [])
    steps: list[GazeStep] = [GazeStep(0.0, 0.0, 0.0)]
    t = 0.0
    x = y = 0.0
    while True:
        fixation = min(FIXATION_MAX_S, max(FIXATION_MIN_S, rng.gammavariate(FIXATION_GAMMA_SHAPE, FIXATION_GAMMA_SCALE_S)))
        t += fixation
        if t >= duration:
            break
        large = rng.randrange(LARGE_JUMP_ODDS) == 0
        reach = (LARGE_AMPLITUDE if large else SMALL_AMPLITUDE) * amplitude
        # A new target: partly around the centre, partly a step from here.
        tx = (1.0 - CENTRE_PULL) * x + rng.uniform(-reach, reach)
        ty = (1.0 - CENTRE_PULL) * y + rng.uniform(-reach, reach) * VERTICAL_FRACTION
        tx, ty = max(-1.0, min(1.0, tx)), max(-1.0, min(1.0, ty))
        jump_t = t
        if windows and math.hypot(tx - x, ty - y) >= BLINK_COUPLED_AMPLITUDE * amplitude:
            centres = [(a + b) / 2.0 for a, b in windows]
            nearest = min(centres, key=lambda c: abs(c - t))
            if abs(nearest - t) <= BLINK_COUPLING_WINDOW_S:
                jump_t = nearest
        jump_t = _snap(jump_t, fps)
        if jump_t <= steps[-1].time or jump_t >= duration:
            continue
        x, y = tx, ty
        steps.append(GazeStep(jump_t, x, y))
    return steps


__all__ = ["GAZE_SALT", "GazeStep", "gaze_seed", "saccade_track"]
