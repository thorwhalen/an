"""Scene IR — the single source of truth for a scene.

Three layers, per the architectural spec:

- **Narrative** (`scene.md`) — human Markdown with structured fenced blocks.
- **Scene Graph** (`ir/scene.json`) — Pydantic-validated JSON. The SSOT.
- **Render Code** — generated per-backend, disposable.

This subpackage owns layer 2 (the Pydantic schema), the Markdown↔JSON sync that
keeps layer 1 and layer 2 in lock-step, the validators (schema + semantic),
the version migration registry, and the composition combinators that flatten
authoring-time DSL to canonical-form actions.
"""

from an.ir.schema import (
    SceneIR,
    Meta,
    AssetRef,
    Shot,
    Action,
    Dialogue,
    Camera,
    Resolution,
)
from an.ir.compose import (
    set_,
    tween,
    play,
    expression,
    sequence,
    parallel,
    delay,
    loop,
    flatten,
    FlatAction,
)
from an.ir.validate import (
    validate_schema,
    validate_semantic,
    ValidationReport,
    ValidationFinding,
)
from an.ir.migrate import (
    migrate,
    register_migration,
    register_kind,
    kind_of,
    DocumentKind,
    MIGRATIONS,
    KINDS,
)
from an.ir.sync import markdown_to_ir, ir_to_markdown, sync

__all__ = [
    "SceneIR",
    "Meta",
    "AssetRef",
    "Shot",
    "Action",
    "Dialogue",
    "Camera",
    "Resolution",
    "set_",
    "tween",
    "play",
    "expression",
    "sequence",
    "parallel",
    "delay",
    "loop",
    "flatten",
    "FlatAction",
    "validate_schema",
    "validate_semantic",
    "ValidationReport",
    "ValidationFinding",
    "migrate",
    "register_migration",
    "register_kind",
    "kind_of",
    "DocumentKind",
    "KINDS",
    "MIGRATIONS",
    "markdown_to_ir",
    "ir_to_markdown",
    "sync",
]

# Document kinds self-register on import of the package that owns their schema,
# the same way renderers self-register in `an.adapters`. Imported here so a bare
# `import an.ir` is enough to make every shipped kind migratable — otherwise
# `migrate()` on a descriptor raises "unknown document kind" in any process that
# happened not to import the character schema. Last in the file, and a submodule
# import, so the `an.characters.schema -> an.ir.assets` edge does not close a
# cycle through this partially-initialised package.
from an.characters import schema as _character_schema  # noqa: F401,E402
