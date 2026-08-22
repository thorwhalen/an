"""Core types, constants, and re-exports for an.

This module is the *type vocabulary* shared across the package: schema versions,
default render parameters, easing presets, type aliases for paths and time. Heavy
data classes (the Pydantic IR models, the Renderer/Verifier protocols) live in
their own subpackages and are re-exported from `an` itself.

Keep this module small and dependency-light. Everything here should import in a
fraction of a second so the CLI is snappy.
"""

from __future__ import annotations

from typing import Literal, TypeAlias

# -- Versioning ---------------------------------------------------------------

#: Current Scene IR schema version. Bump on additive changes; on breaking
#: changes, also bump COMPATIBLE_VERSION and add a migration in `ir.migrate`.
SCHEMA_VERSION: str = "0.1.0"

#: Minimum Scene IR version this code can still read without migration.
COMPATIBLE_VERSION: str = "0.1.0"


# -- Render defaults ----------------------------------------------------------

DEFAULT_FPS: int = 30
DEFAULT_RESOLUTION: tuple[int, int] = (1920, 1080)
DEFAULT_DURATION: float = 5.0  # seconds, used when a shot omits one


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


# -- Style enum ---------------------------------------------------------------

#: The renderer-style of a shot. The orchestrator uses this to pick an adapter.
StyleName: TypeAlias = Literal[
    "cutout",
    "manim",
    "motion_graphics",
    "whiteboard",
]

SUPPORTED_STYLES: tuple[str, ...] = (
    "cutout",
    "manim",
    "motion_graphics",
    "whiteboard",
)
