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

__version__ = "0.1.0"

__all__ = [
    # Versions / constants
    "SCHEMA_VERSION",
    "COMPATIBLE_VERSION",
    "DEFAULT_FPS",
    "DEFAULT_RESOLUTION",
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
