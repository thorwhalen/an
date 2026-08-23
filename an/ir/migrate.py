"""Schema-version migrations, per document kind.

Each migration is a function ``(doc: dict) -> dict`` registered against a
``(kind, from_version, to_version)`` triple. :func:`migrate` walks the registry,
chaining migrations to bring an old document up to date.

Why the kind is part of the key
-------------------------------
This repo versions **two** document kinds independently — the scene IR
(``version``, :data:`an.base.SCHEMA_VERSION`) and the character descriptor
(``schema_version``, ``an.characters.schema.CHARACTER_SCHEMA_VERSION``) — and
both currently sit at ``"0.1.0"``.

Keying on ``(from, to)`` alone conflated them. A character migration registered
as ``("0.1.0", "0.2.0")`` was a well-formed entry in a registry that could not
tell the kinds apart, so :func:`migrate` would happily apply it to a *scene*.
That is namespace conflation rather than a key collision — nothing overwrites
anything, the wrong function simply runs — which is the harder failure to spot.
See ``misc/docs/wave4_research.md`` §5.

Registering a kind
------------------
:mod:`an.ir.migrate` owns the mechanism; each package registers its own kind, so
that this module never has to import the packages it serves. (It cannot:
``an.characters.schema`` imports from ``an.ir.assets``, so an import the other
way would close a cycle.)

>>> from an.ir.migrate import DocumentKind, register_kind
>>> _ = register_kind(DocumentKind("Widget", "widget_version", "2.0"))
>>> migrate({"kind": "Widget", "widget_version": "2.0"})["widget_version"]
'2.0'
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from an.base import SCHEMA_VERSION

Migration = Callable[[dict[str, Any]], dict[str, Any]]


@dataclass(frozen=True, slots=True)
class DocumentKind:
    """A schema-versioned document kind.

    ``version_field`` differs between kinds (``version`` for the scene IR,
    ``schema_version`` for the character descriptor), which is exactly why a
    migrator cannot simply reach for ``doc["version"]``.
    """

    name: str
    version_field: str
    current_version: str

    def version_of(self, doc: dict[str, Any]) -> str:
        """The document's declared version, defaulting to this build's."""
        return doc.get(self.version_field, self.current_version)


#: Registered document kinds, by their ``kind`` tag.
KINDS: dict[str, DocumentKind] = {}

#: Registry: (kind, from_version, to_version) -> migration function.
MIGRATIONS: dict[tuple[str, str, str], Migration] = {}

#: The kind assumed for a document that declares none. The scene IR predates the
#: kind dimension and its persisted documents are not guaranteed to carry a
#: ``kind`` key, so this keeps them migratable — it is not a general default.
DFLT_KIND: str = "SceneIR"


def register_kind(kind: DocumentKind) -> DocumentKind:
    """Register a document kind. Returns it, so callers can bind the result."""
    KINDS[kind.name] = kind
    return kind


SCENE_IR: DocumentKind = register_kind(
    DocumentKind("SceneIR", "version", SCHEMA_VERSION)
)


def kind_of(doc: dict[str, Any], *, kind: str | None = None) -> DocumentKind:
    """Resolve a document's kind, by explicit name or from its ``kind`` tag.

    Raises ``ValueError`` for an unregistered kind rather than guessing — a
    document whose kind nobody declared has no known version field, so any
    answer would be a fabrication.
    """
    name = kind or doc.get("kind") or DFLT_KIND
    if name not in KINDS:
        raise ValueError(
            f"unknown document kind {name!r}; registered: {sorted(KINDS)}. "
            "Call register_kind() from the package that owns the schema."
        )
    return KINDS[name]


def register_migration(
    kind: str, from_version: str, to_version: str
) -> Callable[[Migration], Migration]:
    """Decorator: register a migration for one kind in :data:`MIGRATIONS`.

    >>> @register_migration("SceneIR", "0.0.99", "0.1.0")
    ... def _bump(doc):
    ...     doc["version"] = "0.1.0"
    ...     return doc
    >>> ("SceneIR", "0.0.99", "0.1.0") in MIGRATIONS
    True
    """

    def deco(fn: Migration) -> Migration:
        MIGRATIONS[(kind, from_version, to_version)] = fn
        return fn

    return deco


@register_migration(SCENE_IR.name, SCHEMA_VERSION, SCHEMA_VERSION)
def _identity(doc: dict[str, Any]) -> dict[str, Any]:
    """Identity migration — proves the registry path works."""
    return doc


def migrate(
    doc: dict[str, Any],
    target_version: str | None = None,
    *,
    kind: str | None = None,
) -> dict[str, Any]:
    """Migrate a document to ``target_version`` (default: its kind's current).

    Walks the migration registry one step at a time, considering only the
    migrations registered for this document's kind. Raises ``ValueError`` if no
    path exists between the source and target versions.

    >>> migrate({"version": "0.1.0", "kind": "SceneIR"})["version"]
    '0.1.0'
    """
    doc_kind = kind_of(doc, kind=kind)
    target = target_version if target_version is not None else doc_kind.current_version
    current = dict(doc)  # shallow copy; migrations may mutate
    src = doc_kind.version_of(current)

    steps = {(s, t): fn for (k, s, t), fn in MIGRATIONS.items() if k == doc_kind.name}

    if src == target:
        if (src, target) in steps:
            return steps[(src, target)](current)
        return current

    # Greedy chain: find any registered step from src and follow it.
    visited: set[str] = {src}
    while src != target:
        next_step = next(
            ((s, t) for (s, t) in steps if s == src and t not in visited),
            None,
        )
        if next_step is None:
            raise ValueError(
                f"No migration path for {doc_kind.name!r} from {src!r} to "
                f"{target!r}; registered: {sorted(steps)}"
            )
        current = steps[next_step](current)
        src = next_step[1]
        visited.add(src)
    return current
