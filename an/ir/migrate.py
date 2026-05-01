"""Schema-version migrations.

Each migration is a function ``(doc: dict) -> dict`` registered against the
``(from_version, to_version)`` pair. ``migrate(doc, target)`` walks the
registry, chaining migrations to bring an old document up to date.

We ship one identity migration in v0.1 to prove the wiring; real migrations
get added when fields change.
"""

from __future__ import annotations

from typing import Any, Callable

from an.base import SCHEMA_VERSION


#: Registry: (from_version, to_version) -> migration function.
MIGRATIONS: dict[tuple[str, str], Callable[[dict[str, Any]], dict[str, Any]]] = {}


def register_migration(
    from_version: str, to_version: str
) -> Callable[
    [Callable[[dict[str, Any]], dict[str, Any]]],
    Callable[[dict[str, Any]], dict[str, Any]],
]:
    """Decorator: register a migration in MIGRATIONS.

    >>> @register_migration("0.0.99", "0.1.0")
    ... def _bump(doc):
    ...     doc["version"] = "0.1.0"
    ...     return doc
    >>> ("0.0.99", "0.1.0") in MIGRATIONS
    True
    """

    def deco(fn: Callable[[dict[str, Any]], dict[str, Any]]):
        MIGRATIONS[(from_version, to_version)] = fn
        return fn

    return deco


@register_migration(SCHEMA_VERSION, SCHEMA_VERSION)
def _identity(doc: dict[str, Any]) -> dict[str, Any]:
    """Identity migration — proves the registry path works."""
    return doc


def migrate(
    doc: dict[str, Any], target_version: str = SCHEMA_VERSION
) -> dict[str, Any]:
    """Migrate an IR dict to ``target_version``.

    Walks the migration registry one step at a time. Raises ``ValueError`` if
    no path exists between the source and target versions.

    >>> migrate({"version": "0.1.0", "kind": "SceneIR"})["version"]
    '0.1.0'
    """
    current = dict(doc)  # shallow copy; migrations may mutate
    src = current.get("version", SCHEMA_VERSION)
    if src == target_version:
        if (src, target_version) in MIGRATIONS:
            return MIGRATIONS[(src, target_version)](current)
        return current

    # Greedy chain: find any registered step from src and follow it.
    visited: set[str] = {src}
    while src != target_version:
        next_step = next(
            ((s, t) for (s, t) in MIGRATIONS if s == src and t not in visited),
            None,
        )
        if next_step is None:
            raise ValueError(
                f"No migration path from {src!r} to {target_version!r}; "
                f"registered: {sorted(MIGRATIONS.keys())}"
            )
        current = MIGRATIONS[next_step](current)
        src = next_step[1]
        visited.add(src)
    return current
