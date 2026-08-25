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

import copy
import warnings
from dataclasses import dataclass
from typing import Any, Callable

from an.base import COMPATIBLE_VERSION, SCHEMA_VERSION

Migration = Callable[[dict[str, Any]], dict[str, Any]]


class DocumentMigrationError(ValueError):
    """A stored document could not be brought to this build's schema.

    One name for both boundaries (scene and descriptor), so a caller can catch
    "this file is from another build" without catching every ``ValueError`` a
    validate might raise.
    """


def version_tuple(version: Any) -> tuple[int, ...] | None:
    """``"0.2.10"`` → ``(0, 2, 10)``; ``None`` when it is not a version at all.

    Returning ``None`` rather than raising is what lets a caller tell a
    *malformed* version field (``null``, a float, ``"draft"``) from one that is
    merely older than any registered migration — two different messages for two
    different repairs.

    >>> version_tuple("0.2.10"), version_tuple(0.1), version_tuple(None)
    ((0, 2, 10), None, None)
    """
    if not isinstance(version, str):
        return None
    parts = version.split(".")
    if not all(p.isdigit() for p in parts) or not parts:
        return None
    return tuple(int(p) for p in parts)


def readable_without_migration(version: Any, kind: DocumentKind | None = None) -> bool:
    """Whether this build reads ``version`` as-is, per the declared compat floor.

    ``an/base.py`` promises exactly this: ``COMPATIBLE_VERSION`` is *"the
    minimum Scene IR version this code can still read **without migration**"*.
    A loader that demands an exact version match ignores that promise and turns
    the next additive bump into a refusal of every project on disk (an#105
    review). Only the scene kind declares a floor; any other kind must migrate.

    >>> from an.base import SCHEMA_VERSION
    >>> readable_without_migration(SCHEMA_VERSION)
    True
    >>> readable_without_migration("0.1.0")  # below the floor since an#106
    False
    """
    v = version_tuple(version)
    if v is None:
        return False
    name = kind.name if kind is not None else DFLT_KIND
    if name != DFLT_KIND:
        return False
    return version_tuple(COMPATIBLE_VERSION) <= v <= version_tuple(SCHEMA_VERSION)


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

    Registered against the throwaway ``Widget`` kind from the module docstring,
    deliberately: this registry is process-wide, so a doctest that registered a
    step for a REAL kind would leave a second path through the ladder for every
    test that ran afterwards — which is exactly what happened once, and it
    presented as one unrelated test failing only in a full run (an#106).

    >>> @register_migration("Widget", "1.0", "2.0")
    ... def _bump(doc):
    ...     doc["widget_version"] = "2.0"
    ...     return doc
    >>> ("Widget", "1.0", "2.0") in MIGRATIONS
    True
    """

    def deco(fn: Migration) -> Migration:
        MIGRATIONS[(kind, from_version, to_version)] = fn
        return fn

    return deco


def _warn_dropped(before: list, after: list, *, where: str) -> None:
    """Say what a migration removed. A silent drop of something the author
    wrote is the failure `tests/test_loud_discards.py` exists for, even when
    the thing dropped did nothing (an#106 review)."""
    if len(after) == len(before):
        return
    gone = [
        e.get("id") for e in before if isinstance(e, dict) and e.get("kind") == "style"
    ]
    warnings.warn(
        f"migrating to 0.2.0 dropped {len(before) - len(after)} `style` "
        f"entit{'y' if len(before) - len(after) == 1 else 'ies'} ({gone}) from {where}: "
        "the kind was retired in an#106 because it selected nothing — the compiler "
        "skipped it and no reader of the styles store existed. Art direction returns "
        "as a StylePack (see https://github.com/thorwhalen/an/issues/112).",
        # 1 = here, 2 = the migration step, 3 = `migrate()`'s dispatch loop,
        # 4 = whoever called `migrate()`. Measured: 3 attributed the warning to
        # `an/ir/migrate.py` itself, so a `-W` filter keyed on module would have
        # aimed at the library instead of the caller (an#106 review).
        stacklevel=4,
    )


#: Camera fields an#109 removed, and why each was dead. They were written into
#: every `scene.md` this package ever generated — five committed documents in
#: this repo alone — and read by NOTHING: `rg focal_length an/ tests/` found
#: the schema line and no consumer.
RETIRED_CAMERA_FIELDS: tuple[str, ...] = ("position", "target", "focal_length")


@register_migration(SCENE_IR.name, "0.2.0", "0.3.0")
def _drop_dead_camera_fields(doc: dict[str, Any]) -> dict[str, Any]:
    """0.2.0 → 0.3.0 (an#109): the camera stops carrying a lens it never had.

    `position`, `target` and `focal_length` described a 3D camera this package
    has never had — the cutout camera is `root.pivot` plus `root.scale`, which
    is two dimensions and a zoom. They defaulted, serialized, round-tripped
    through every `scene.md`, and were read by nothing.

    Dropped silently, unlike an#106's `kind="style"` entities: a defaulted
    field nobody set is not something the author wrote, and warning about it
    on every stored document would be noise on exactly the documents that had
    nothing to do with it. A NON-default value is a different matter and is
    reported, because that one someone typed.

    >>> doc = {"version": "0.2.0", "kind": "SceneIR", "timeline": [
    ...     {"id": "s1", "camera": {"focal_length": 50.0, "move": "hold"}}]}
    >>> _drop_dead_camera_fields(doc)["timeline"][0]["camera"]
    {'move': 'hold'}
    """
    # DEEP, because this migration POPS from nested camera dicts: a shallow
    # copy leaves `timeline`, each shot and each camera shared with the
    # caller, so `camera.pop(...)` strips the input the caller still holds.
    # The `return doc` shape reads as pure and was not (an#109 review, M-4).
    doc = copy.deepcopy(dict(doc))
    doc["version"] = "0.3.0"
    if "compatible_version" in doc:
        doc["compatible_version"] = "0.3.0"
    shots = doc.get("timeline")
    if not isinstance(shots, list):
        return doc
    defaults = {"position": [0.0, 0.0, 0.0], "target": [0.0, 0.0, 0.0], "focal_length": 50.0}
    for shot in shots:
        if not isinstance(shot, dict):
            continue
        camera = shot.get("camera")
        if not isinstance(camera, dict):
            continue
        def _is_default(field: str, value: Any) -> bool:
            want = defaults[field]
            if isinstance(want, list):
                return isinstance(value, (list, tuple)) and list(value) == want
            return value == want

        authored = {
            f: camera[f]
            for f in RETIRED_CAMERA_FIELDS
            if f in camera and not _is_default(f, camera[f])
        }
        for f in RETIRED_CAMERA_FIELDS:
            camera.pop(f, None)
        if authored:
            warnings.warn(
                f"migrating to 0.3.0 dropped camera {sorted(authored)} from shot "
                f"{shot.get('id')!r}: an#109 removed them because they described a "
                "3D camera this package never had — the cutout camera is "
                "`root.pivot` plus `root.scale`. A non-default value was set, so "
                "this one is said out loud; the defaults are dropped in silence.",
                stacklevel=4,
            )
    return doc


@register_migration(SCENE_IR.name, "0.1.0", "0.2.0")
def _rename_style_to_renderer(doc: dict[str, Any]) -> dict[str, Any]:
    """0.1.0 → 0.2.0 (an#106): the renderer selector stops being called "style".

    ``Shot.style`` → ``Shot.renderer``, ``Meta.default_style`` →
    ``Meta.default_renderer``, and ``AssetRef(kind="style")`` entities are
    **dropped**. Dropping is honest here and only here: such an entity selected
    nothing — the compiler skipped it and no reader of the styles store existed
    — so it was a declaration with no effect, and carrying it forward under a
    name the schema no longer accepts would fail validation on a document that
    renders identically. Art direction returns as a StylePack (#112), which is
    a different thing referenced a different way.

    This is the first migration in the repo that actually runs (an#105 wired
    the read path); before that, it would have been decoration and the rename
    would have landed as a silent default.

    >>> _rename_style_to_renderer({"version": "0.1.0", "meta": {"default_style": "manim"},
    ...     "timeline": [{"id": "s", "style": "cutout"}],
    ...     "assets": [{"kind": "style", "id": "x", "store": "styles", "ref": "x"}]})
    {'version': '0.2.0', 'meta': {'default_renderer': 'manim'}, 'timeline': [{'id': 's', 'renderer': 'cutout'}], 'assets': []}
    """
    meta = doc.get("meta")
    if isinstance(meta, dict) and "default_style" in meta:
        # The NEW key wins. A document carrying both was hand-repaired by
        # someone who read the changelog and forgot to delete the old key;
        # overwriting would silently revert their fix (an#106 review).
        stale = meta.pop("default_style")
        meta.setdefault("default_renderer", stale)
    for shot in doc.get("timeline") or []:
        if isinstance(shot, dict):
            if "style" in shot:
                stale = shot.pop("style")
                shot.setdefault("renderer", stale)
            # Only rewrite a key the document actually has: a migration that
            # ADDS `entities: []` to a shot that declared none changes the
            # document for no reason, and every such gratuitous key is one more
            # diff for a reader to explain.
            if isinstance(shot.get("entities"), list):
                kept = [
                    e
                    for e in shot["entities"]
                    if not (isinstance(e, dict) and e.get("kind") == "style")
                ]
                _warn_dropped(shot["entities"], kept, where=f"shot {shot.get('id')!r}")
                shot["entities"] = kept
    # `isinstance(..., list)`, not `or []`: a truthy dict would iterate its KEYS
    # and a null would become an empty list — inventing a key the document did
    # not have (an#106 review).
    if isinstance(doc.get("assets"), list):
        kept = [
            a
            for a in doc["assets"]
            if not (isinstance(a, dict) and a.get("kind") == "style")
        ]
        _warn_dropped(doc["assets"], kept, where="the scene's assets")
        doc["assets"] = kept
    doc["version"] = "0.2.0"
    # The pair moves together: `compatible_version` is what a *future* build
    # reads to decide whether it may skip migrating, so leaving it at 0.1.0
    # would advertise that a 0.1.0-shaped document is still readable.
    if "compatible_version" in doc:
        doc["compatible_version"] = "0.2.0"
    return doc


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

    >>> from an.base import SCHEMA_VERSION
    >>> migrate({"version": SCHEMA_VERSION, "kind": "SceneIR"})["version"] == SCHEMA_VERSION
    True
    >>> migrate({"version": "0.1.0", "kind": "SceneIR"})["version"] == SCHEMA_VERSION
    True
    """
    doc_kind = kind_of(doc, kind=kind)
    target = target_version if target_version is not None else doc_kind.current_version
    # DEEP copy: the shallow one protected the top level only, so a migration
    # written the way the old comment invited — `doc["meta"]["title"] = …` —
    # silently rewrote the CALLER's document while leaving its version key
    # untouched, which is precisely the shape that looks safe (an#105 review).
    # Wave 7's rename is a nested rename.
    current = copy.deepcopy(doc)
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
            raise DocumentMigrationError(
                f"No migration path for {doc_kind.name!r} from {src!r} to "
                f"{target!r}; registered: {sorted(steps)}"
            )
        current = steps[next_step](current)
        src = next_step[1]
        visited.add(src)
    return current
