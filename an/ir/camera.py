"""Camera semantics: the named moves, and the one resolver that expands them.

This module exists because the alternative was two hand-maintained tables. The
compiler owned `CAMERA_MOVES` and `an.ir.validate` owned a frozenset of the
same names, reconciled by a test — which works, and is not what "a move that
validates cannot then raise" means. It means *one table*.

It lives under `an/ir/` rather than in the cutout adapter because the IR layer
must not import an adapter, and validate is in the IR layer. The camera is IR
semantics anyway: `Camera`/`CameraKey` are schema models, and a renderer that
cannot honour a move says so through `can_render`, not by keeping its own
vocabulary.

The geometry it encodes, verified numerically against the vendored PixiJS
composition: `root.pivot` IS a 2D camera, because PixiJS composes
``world = position + M·(local − pivot)`` and the runtime indexes the centre
container as ``root``. `+x` moves the CAMERA right, which moves content left.
"""

from __future__ import annotations

from typing import Callable

from an.ir.schema import Camera, CameraKey, Shot

__all__ = [
    "CAMERA_MOVES",
    "PAN_FRACTION",
    "CameraError",
    "camera_keys",
]

#: How far a pan travels, as a fraction of the canvas width (a tilt uses the
#: same fraction of the height). A third of the frame is a legible move at any
#: resolution and is the one number a named pan has to choose; an author who
#: wants a different distance writes `keys` and says so.
PAN_FRACTION: float = 1.0 / 3.0


class CameraError(ValueError):
    """A camera that cannot be resolved into keys.

    A plain `ValueError` subclass so the IR layer can raise it without knowing
    about any renderer; the cutout compiler re-raises it as a
    `CutoutCompileError` at its own boundary.
    """


def _zoom_keys(duration: float, start: float, end: float) -> list[CameraKey]:
    """Two keys that vary only `zoom` — the pre-an#109 document, as keys."""
    return [
        CameraKey(at=0.0, zoom=start, easing="ease_in_out"),
        CameraKey(at=duration, zoom=end),
    ]


def _move_keys(duration: float, axis: str, sign: float) -> list[CameraKey]:
    """Two keys that vary only one translation axis. The distance is filled in
    by :func:`camera_keys`, which is the only place that knows the canvas."""
    return [
        CameraKey(at=0.0, easing="ease_in_out"),
        CameraKey(at=duration, **{axis: sign}),
    ]


#: The named moves, as KEY LISTS. `move` is sugar over `keys` — one code path,
#: two front doors.
#:
#: The five zoom moves must desugar to **exactly** the document they produced
#: before an#109, which is more specific than "a scale tween": two animations
#: named `__camera__<shot>_scale_x`/`_scale_y`, one channel each targeting
#: `root`, keyframes `[(0.0, s0, "ease_in_out"), (duration, s1, null)]`, on two
#: tracks rooted at `"__camera__"`, and **no pivot channels**. The emitter emits
#: only the channels that actually vary, which is what makes that true rather
#: than merely intended — and what keeps every camera scene's contract hash
#: where it was.
CAMERA_MOVES: dict[str, Callable[[float], list[CameraKey]]] = {
    "hold": lambda d: [],
    "push_in": lambda d: _zoom_keys(d, 1.0, 1.25),
    "pull_out": lambda d: _zoom_keys(d, 1.0, 0.8),
    "zoom_in": lambda d: _zoom_keys(d, 1.0, 1.5),
    "zoom_out": lambda d: _zoom_keys(d, 1.0, 0.7),
    # an#109. `+x` moves the CAMERA right, so `pan_left` ends at negative x.
    # In film a lateral translation on a fixed head is a *truck*, not a pan; on
    # an orthographic 2D stage the two are indistinguishable, and the epic's
    # done-when says `pan_left`, so the ambiguity lives here rather than in a
    # purist rename nobody would search for.
    "pan_left": lambda d: _move_keys(d, "x", -1.0),
    "pan_right": lambda d: _move_keys(d, "x", 1.0),
    "tilt_up": lambda d: _move_keys(d, "y", -1.0),
    "tilt_down": lambda d: _move_keys(d, "y", 1.0),
}

#: Moves a pan/tilt scales by the canvas HEIGHT rather than the width. A frame
#: is wider than it is tall, so one span for both axes makes a tilt travel
#: further than the picture it is tilting across.
_VERTICAL_MOVES: frozenset[str] = frozenset({"tilt_up", "tilt_down"})


def camera_keys(shot: Shot, *, width: int, height: int) -> list[CameraKey]:
    """The shot's camera as an explicit key list — the ONE resolver.

    `move` and `keys` are two front doors on one path. **Validate calls this
    and so does the compiler**, which is what makes "a move that validates
    cannot then raise" true by construction rather than by two tables agreeing
    — the arrangement it replaced (an#109 review, H-1).

    >>> from an.ir.schema import Camera, Shot
    >>> push = Shot(id="s", renderer="cutout", duration=2.0, camera=Camera(move="push_in"))
    >>> [(k.at, k.zoom) for k in camera_keys(push, width=320, height=240)]
    [(0.0, 1.0), (2.0, 1.25)]
    >>> pan = Shot(id="s", renderer="cutout", duration=2.0, camera=Camera(move="pan_left"))
    >>> [(k.at, round(k.x, 3)) for k in camera_keys(pan, width=320, height=240)]
    [(0.0, 0.0), (2.0, -106.667)]
    """
    camera: Camera | None = shot.camera
    if camera is None:
        return []
    if camera.move is not None and camera.keys is not None:
        raise CameraError(
            f"shot {shot.id!r} sets BOTH camera.move={camera.move!r} and "
            f"camera.keys ({len(camera.keys)} keys). They are two front doors "
            "on one path, so a scene that sets both says two things about the "
            "same camera and there is no reading that is not a guess. Keep the "
            "one you meant."
        )
    duration = max(0.001, float(shot.duration))
    if camera.keys is not None:
        keys = list(camera.keys)
        _refuse_keys_that_cannot_play(shot, keys, duration)
        return keys
    if camera.move is None:
        return []
    # Normalise BEFORE the emptiness test, or the guard grows an arbitrary seam:
    # `move=""` fell through the falsiness check and was ignored, while
    # `move="  "` reached the lookup and raised. Same input, two behaviours.
    move = camera.move.strip()
    if not move:
        return []
    if move not in CAMERA_MOVES:
        raise CameraError(
            f"shot {shot.id!r} asks for camera.move={move!r}, which the cutout "
            f"renderer does not implement (it has: {sorted(CAMERA_MOVES)}). "
            "Write `camera.keys` if you need a move the presets do not name."
        )
    keys = CAMERA_MOVES[move](duration)
    # `_move_keys` carries a unit SIGN on its axis; the distance needs the
    # canvas, which only the caller knows.
    span = float(height if move in _VERTICAL_MOVES else width) * PAN_FRACTION
    return [k.model_copy(update={"x": k.x * span, "y": k.y * span}) for k in keys]


def _refuse_keys_that_cannot_play(shot: Shot, keys: list[CameraKey], duration: float) -> None:
    """Keys the compiler would emit as keyframes that never play.

    Raised rather than warned, and raised HERE rather than only reported by
    validate, because the alternative is a silent no-op: a keyframe past the
    shot's end is emitted and never reached, and an out-of-order key runs the
    camera backwards through a curve the author did not write. Both are
    `test_loud_discards.py`'s subject matter.

    Raising also makes validate's ERROR a prediction of a raise, which is
    `_check_camera`'s stated contract — before an#109's review, three of its
    four errors fired on scenes that compiled fine (M-2).
    """
    times = [float(k.at) for k in keys]
    if times != sorted(times):
        raise CameraError(
            f"shot {shot.id!r}: camera keys are not in time order ({times}). "
            "They are emitted as keyframes in list order, so an out-of-order "
            "key runs the camera backwards through a curve nobody wrote."
        )
    outside = [t for t in times if not (0.0 <= t <= duration)]
    if outside:
        raise CameraError(
            f"shot {shot.id!r}: camera keys at {outside} are outside the shot "
            f"(0 … {duration}). A keyframe past the end never plays; one before "
            "the start opens the shot mid-move."
        )
