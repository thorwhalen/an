"""Core types, constants, and re-exports for an.

This module is the *type vocabulary* shared across the package: schema versions,
default render parameters, easing presets, type aliases for paths and time. Heavy
data classes (the Pydantic IR models, the Renderer/Verifier protocols) live in
their own subpackages and are re-exported from `an` itself.

Keep this module small and dependency-light. Everything here should import in a
fraction of a second so the CLI is snappy.
"""

from __future__ import annotations

from typing import Literal, TypeAlias, get_args

# -- Versioning ---------------------------------------------------------------

#: Current Scene IR schema version. Bump on additive changes; on breaking
#: changes, also bump COMPATIBLE_VERSION and add a migration in `ir.migrate`.
#: 0.2.0 renamed `Shot.style` -> `Shot.renderer` and `Meta.default_style` ->
#: `Meta.default_renderer`, and retired `AssetRef(kind="style")` (an#106).
#: 0.3.0 removed `Camera.position` / `.target` / `.focal_length`, which
#: described a 3D camera this package never had, and gave `Camera` a `keys`
#: list so it can translate (an#109).
SCHEMA_VERSION: str = "0.3.0"

#: Minimum Scene IR version this code can still read without migration.
COMPATIBLE_VERSION: str = "0.3.0"


# -- Render defaults ----------------------------------------------------------

DEFAULT_FPS: int = 30
DEFAULT_RESOLUTION: tuple[int, int] = (1920, 1080)
DEFAULT_DURATION: float = 5.0  # seconds, used when a shot omits one

#: Render at this many times the declared resolution, then resolve back with an
#: exact block mean. **1 is off, and off is free**: the un-supersampled path
#: keeps Chromium's own PNG bytes and pays nothing.
#:
#: Here rather than in `an.adapters.cutout.supersample`, where the rest of the
#: mechanism lives, because `an/adapters/_base.py` needs it for
#: `RenderContext`'s default and importing the cutout package from there is a
#: real cycle (`_base` -> `cutout` -> `render` -> `_base`), not a hypothetical
#: one. `an/base.py` imports nothing from `an`.
#:
#: The default stays 1 deliberately. Supersampling ships OPT-IN with its A/B
#: committed (an#58, discussion #52), per the standing rule that a default
#: chosen by taste ships opt-in and the flip is its own one-line change.
DEFAULT_SUPERSAMPLE: int = 1


# -- Delivery -----------------------------------------------------------------

#: Put the mp4's `moov` atom in front of `mdat`, so a player can start before
#: the file has finished downloading.
#:
#: Here, and not beside one of the ffmpeg calls, because **three separate
#: commands build the one file a user receives** -- the frame mux
#: (`_ffmpeg_mux`), the audio mux (`_ffmpeg_add_audio`) and the concat
#: (`_ffmpeg_concat`) -- and each of the last two re-lays the container with
#: `-c copy`, which writes `moov` LAST. The flag was on the first of those
#: alone, which reads as done and delivers nothing: it applied only to
#: `silent.mp4`, a per-shot intermediate that is never handed to anyone.
#: Measured on a local example render (these mp4s are gitignored build
#: products; `git ls-files` tracks exactly one, and it is moov-last too):
#: `.an/render_work/shot_s1/silent.mp4` is `ftyp moov free mdat`, while the
#: per-shot mp4, `artifacts/shots/*.mp4` and `output/main.mp4` are all
#: `ftyp free mdat moov`. Single-shot and multi-shot alike; an#57's
#: "single-shot ones keep it" is wrong, because the `shutil.copy` branch
#: copies a file that already lost it.
#:
#: Deliberately NOT part of `DETERMINISTIC_X264_ARGS`. That tuple is an
#: `ENCODE_ENV_PATHS` comparability key (an/bench/compare.py:98), and this flag
#: moves no number the panel reads: with `-c copy` it is a container rewrite,
#: not a re-encode. Verified on ffmpeg 8.1 -- elementary-stream sha256
#: identical, video/audio packet totals identical, file size identical,
#: decoded YUV sha256 identical, wall time unchanged.
MP4_FASTSTART_ARGS: tuple[str, ...] = ("-movflags", "+faststart")


# -- Easing -------------------------------------------------------------------

#: Named easing presets. Renderers should accept these and the cubic-Bézier
#: 4-tuple form `[cx1, cy1, cx2, cy2]`. Names follow the GSAP / CSS convention.
EASING_PRESETS: tuple[str, ...] = (
    "linear",
    "ease",
    "ease_in",
    "ease_out",
    "ease_in_out",
    "step",
)


# -- Animatable transform vocabulary -----------------------------------------

#: The property names the cutout runtime animates NUMERICALLY. Any other
#: property on a set/tween names a swap SET declared by the target entity's
#: descriptor (an#87). This is the SSOT the three consumers share so the IR
#: validator, the character validator and the compiler cannot drift: the
#: compiler derives its rest-value table from ``TransformJSON`` and a test
#: asserts that derivation equals this set. Lives here because ``an.base`` is
#: the one module all three layers may import.
#: The colour multiply as an AUTHOR spells it. Not in
#: :data:`TRANSFORM_PROPERTIES` because nothing downstream of the compiler ever
#: sees it — `_expand_tint_actions` rewrites each leaf into the three numeric
#: components before the swap-set dispatch, which would otherwise read it as an
#: asset-set name (an#62).
COLOUR_PROPERTY: str = "tint"

TRANSFORM_PROPERTIES: frozenset[str] = frozenset(
    {
        "x",
        "y",
        "rotation",
        "rotation_rad",
        "scale_x",
        "scale_y",
        "skew_x",
        "skew_y",
        "pivot_x",
        "pivot_y",
        # an#62. Three numeric channels rather than one colour value, because
        # `channel.evaluate` lerps numbers and SNAPS everything else, and it has
        # a `runtime.js` twin held in step by a parity test. Authors write
        # `tint: "#rrggbb"` and the compiler expands it into these.
        "tint_r",
        "tint_g",
        "tint_b",
        "alpha",
    }
)

#: Characters within which a swap-set name is not addressable: ``/`` would read
#: as a path segment and ``::`` is the runtime's pose-key separator.
SWAP_SET_NAME_FORBIDDEN_SUBSTRINGS: tuple[str, ...] = ("/", "::")


#: What a set/tween may name: a compiled transform channel, or the authored
#: colour spelling the compiler expands. Validate checks against this, and the
#: compiler's rest table against `TRANSFORM_PROPERTIES` — the difference between
#: the two sets is exactly `tint`, and a test pins that rather than trusting it.
AUTHORABLE_PROPERTIES: frozenset[str] = TRANSFORM_PROPERTIES | {COLOUR_PROPERTY}


def swap_set_name_problem(name: str) -> str | None:
    """Why ``name`` cannot be a swap-set name, or ``None`` if it can.

    >>> swap_set_name_problem("hands") is None
    True
    >>> swap_set_name_problem("alpha")
    "'alpha' is a transform property; the runtime's static switch would shadow the set"
    >>> swap_set_name_problem("a::b")
    "'a::b' contains '::', which is reserved"
    """
    if name in TRANSFORM_PROPERTIES:
        return (
            f"{name!r} is a transform property; the runtime's static switch "
            "would shadow the set"
        )
    for bad in SWAP_SET_NAME_FORBIDDEN_SUBSTRINGS:
        if bad in name:
            return f"{name!r} contains {bad!r}, which is reserved"
    if not name:
        return "a swap-set name may not be empty"
    return None


# -- Type aliases -------------------------------------------------------------

#: Slash-delimited node path, e.g. ``"charlie/head/mouth"``.
#:
#: The example matters: an unknown target now RAISES rather than being skipped,
#: and the long-standing ``"charlie/torso/left_arm"`` illustration names a node
#: no rig actually builds — the cutout rigs are flat, so arms are siblings of
#: the torso, not children of it.
PathStr: TypeAlias = str

#: Time in seconds. Floats at the IR boundary; rational time is used internally
#: only inside the audio pipeline where drift matters.
Seconds: TypeAlias = float

#: Either an easing preset name or a 4-tuple cubic-Bézier control [cx1,cy1,cx2,cy2].
EasingSpec: TypeAlias = str | tuple[float, float, float, float] | list[float]


# -- Renderer enum ---------------------------------------------------------------

#: Which renderer draws a shot. The orchestrator uses this to pick an adapter.
RendererName: TypeAlias = Literal[
    "cutout",
    "manim",
    "motion_graphics",
    "whiteboard",
]

#: The same vocabulary as :data:`RendererName`, as a runtime tuple — DERIVED
#: from it, because a hand-typed second copy is a second SSOT that drifts on
#: the day a renderer is added and nothing fails.
SUPPORTED_RENDERERS: tuple[str, ...] = get_args(RendererName)
