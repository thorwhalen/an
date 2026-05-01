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

#: Slash-delimited node path, e.g. ``"charlie/torso/left_arm"``.
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
