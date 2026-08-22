"""an — AI-driven structured animation.

Public API surface (curated). See `an.ir` for the Scene IR, `an.adapters`
for renderer plumbing, `an.audio` for TTS/lip-sync protocols, `an.verify`
for verification, and `an.stores` for the project mall.

>>> import an
>>> 'SceneIR' in an.__all__
True
"""

from an.base import (
    SCHEMA_VERSION,
    COMPATIBLE_VERSION,
    DEFAULT_FPS,
    DEFAULT_RESOLUTION,
    DEFAULT_SUPERSAMPLE,
    SUPPORTED_STYLES,
)
from an.ir import (
    SceneIR,
    Meta,
    AssetRef,
    Shot,
    Action,
    Dialogue,
    Camera,
    Resolution,
    set_,
    tween,
    play,
    sequence,
    parallel,
    delay,
    loop,
    flatten,
    FlatAction,
    validate_schema,
    validate_semantic,
    markdown_to_ir,
    ir_to_markdown,
)
from an.project import init, load, save, Project
from an.check_requirements import check_requirements
from an.stores import build_project_mall

# Read from installed distribution metadata rather than written literally here:
# CI bumps the version in pyproject.toml and pushes back, so a literal in this
# file drifts silently — this one had been reading "0.1.0" while the package was
# on 0.1.9. `"unknown"` is the honest answer for a source tree that was never
# installed (not even editable).
from importlib.metadata import PackageNotFoundError, version as _dist_version

try:
    __version__ = _dist_version("an")
except PackageNotFoundError:  # pragma: no cover - source tree, not installed
    __version__ = "unknown"

__all__ = [
    # Versions / constants
    "__version__",
    "SCHEMA_VERSION",
    "COMPATIBLE_VERSION",
    "DEFAULT_FPS",
    "DEFAULT_RESOLUTION",
    "DEFAULT_SUPERSAMPLE",
    "SUPPORTED_STYLES",
    # Scene IR
    "SceneIR",
    "Meta",
    "AssetRef",
    "Shot",
    "Action",
    "Dialogue",
    "Camera",
    "Resolution",
    # Composition
    "set_",
    "tween",
    "play",
    "sequence",
    "parallel",
    "delay",
    "loop",
    "flatten",
    "FlatAction",
    # Validation
    "validate_schema",
    "validate_semantic",
    # Sync
    "markdown_to_ir",
    "ir_to_markdown",
    # Project
    "init",
    "load",
    "save",
    "Project",
    "build_project_mall",
    # System diagnostics
    "check_requirements",
]
