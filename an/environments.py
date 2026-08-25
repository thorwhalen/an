"""Environments: a stage made of planes, at declared depths.

An environment was three scalars — `sky_color`, `ground_color`, `ground_y` —
merged through an **intersection filter** that silently dropped everything
else. The test pinning that warning used `parallax_layers: 3` as its example,
which says what the shape was for.

An `EnvironmentDescriptor` declares `planes`, and **list order is draw
order**. There is deliberately no `z` integer: the runtime sets no `zIndex`, so
a `z` field would be a second source of truth it could not honour, and two
orderings that can disagree is how the intersecting override got here in the
first place.

**`depth` is the parallax factor**, in Godot's `Parallax2D.scroll_scale`
coordinates — a ratio, not a distance:

===========  ===============================================================
`depth`      meaning
===========  ===============================================================
`0.0`        infinitely far: frozen in frame, neither pans nor scales
`0 < d < 1`  background — Godot's own sanity range is 0.1 sky → 0.7 forest
`1.0`        the character plane; **emits nothing**, which is today's
             behaviour for everything and why this is byte-identity-free
`> 1.0`      foreground: nearer than the characters, moving faster
===========  ===============================================================

Sign trap, pinned here because every surveyed tool disagrees: **larger depth =
nearer = faster.** Unity's z-derived factor uses the INVERSE convention
(`f_unity ≡ 1 − f_godot`), so a Unity tutorial read while writing this code
will produce a stage that parallaxes backwards.

`characters_after` is how a plane gets IN FRONT of the characters — Rive's
relative-ordering shape. `None` reproduces the old two-loop behaviour exactly
(every plane behind every character) and dissolves the tie between two planes
that share a depth.
"""

from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from an.ir.assets import AssetSource
from an.ir.migrate import DocumentKind, register_kind, register_migration

__all__ = [
    "ENVIRONMENT_SCHEMA_VERSION",
    "ENVIRONMENT_DOCUMENT_KIND",
    "EnvironmentDescriptor",
    "Plane",
    "PlaneArt",
]

ENVIRONMENT_SCHEMA_VERSION = "0.1.0"

#: Its own versioned document, registered from the module that owns the schema
#: — the rule `CharacterDescriptor` and `PropDescriptor` both follow, and the
#: reason the registry is keyed per KIND (an#77).
ENVIRONMENT_DOCUMENT_KIND: DocumentKind = register_kind(
    DocumentKind(
        name="EnvironmentDescriptor",
        version_field="schema_version",
        current_version=ENVIRONMENT_SCHEMA_VERSION,
    )
)


class _EnvModel(BaseModel):
    """Forward-compatible reads at the DOCUMENT level."""

    model_config = ConfigDict(extra="allow", populate_by_name=True)


class PlaneArt(BaseModel):
    """What a plane is made of.

    >>> PlaneArt(kind="fill", color="#cfe9ff").color
    '#cfe9ff'
    >>> PlaneArt(kind="image", src="plates/forest.svg").src
    'plates/forest.svg'

    Two kinds ship, and the omission is deliberate rather than partial:
    `gradient` and `generated` would each need a runtime that can draw them,
    and this package's standing rule is that schema without a consumer is
    worse than an absent field — the `repeat`/`TilingSprite` decision in
    an#110 is the same call made the same way.
    """

    model_config = ConfigDict(extra="forbid")

    kind: Literal["fill", "image"] = "fill"
    #: `fill` only: a CSS colour.
    color: str = "#888888"
    #: `image` only: a path under the environment's own folder in the store,
    #: exactly as a character attachment's `path` is.
    src: Optional[str] = None


class Plane(BaseModel):
    """One layer of the stage, at one depth.

    >>> Plane(name="sky", art=PlaneArt(color="#cfe9ff"), depth=0.1).depth
    0.1
    >>> Plane(name="sky", parallax=(0.2, 0.0)).parallax
    (0.2, 0.0)

    **`extra="forbid"`, unlike the document that holds it.** An
    `EnvironmentDescriptor` is `extra="allow"` because the environments store
    is a free-form `meta.json` whose natural shape includes `name`,
    `description` and `tags` — refusing those would hard-fail ordinary data. A
    plane is not free-form: it is a precise instruction to draw something, and
    a misspelled key there is the exact failure an#110 exists to remove. The
    old override path *silently dropped* every key it did not know, and the
    test pinning that warning used `parallax_layers: 3` as its example.
    """

    model_config = ConfigDict(extra="forbid")

    #: Becomes the scene node's name, under the environment entity's id.
    name: str
    art: PlaneArt = Field(default_factory=PlaneArt)

    #: The parallax factor — a RATIO, in Godot's `Parallax2D.scroll_scale`
    #: coordinates. `1.0` is the character plane and emits nothing. See this
    #: module's docstring for the table and for the Unity sign trap.
    depth: float = Field(default=1.0, ge=0.0, allow_inf_nan=False)
    #: Per-axis override of `depth`, for a plane that scrolls horizontally but
    #: not vertically. `None` means `(depth, depth)`.
    parallax: Optional[tuple[float, float]] = None

    #: Where the plane sits, in scene pixels from the stage centre.
    offset: tuple[float, float] = (0.0, 0.0)
    #: The art's anchor within its own box, in 0..1 per axis.
    anchor: tuple[float, float] = (0.5, 0.5)
    #: `None` = the art's own extent. A `fill` with no size covers the canvas.
    size: Optional[tuple[float, float]] = None
    fit: Literal["stretch", "contain"] = "contain"

    def factors(self) -> tuple[float, float]:
        """The per-axis parallax factors this plane actually moves by.

        >>> Plane(name="p", depth=0.4).factors()
        (0.4, 0.4)
        >>> Plane(name="p", depth=0.4, parallax=(0.2, 0.0)).factors()
        (0.2, 0.0)
        """
        return self.parallax if self.parallax is not None else (self.depth, self.depth)


class EnvironmentDescriptor(_EnvModel):
    """A stage made of planes. Saved as ``meta.json`` in the environments store.

    >>> env = EnvironmentDescriptor(name="forest", planes=[
    ...     Plane(name="sky", depth=0.1),
    ...     Plane(name="trees", depth=0.6),
    ...     Plane(name="grass", depth=1.4),
    ... ], characters_after="trees")
    >>> [p.name for p in env.planes]
    ['sky', 'trees', 'grass']
    >>> env.characters_after
    'trees'

    List order is draw order, and `characters_after` names the plane the
    characters stand in front of — so `grass` above is a FOREGROUND plane,
    drawn over them. `None` puts every plane behind every character, which is
    what the two-loop builder did before an#110 and is why an environment that
    declares no planes compiles byte-identically.
    """

    schema_version: str = ENVIRONMENT_SCHEMA_VERSION
    kind: Literal["EnvironmentDescriptor"] = "EnvironmentDescriptor"

    name: str
    #: **LIST ORDER IS DRAW ORDER.** There is no `z` field: the runtime sets no
    #: `zIndex`, so a second ordering would be one it could not honour.
    planes: list[Plane] = Field(default_factory=list)
    #: The plane the characters are drawn in FRONT of. `None` = all planes
    #: behind all characters.
    characters_after: Optional[str] = None
    #: Named stage marks — a horizon is one of them. A dedicated `horizon`
    #: field would be two fields for one fact, which is how the intersecting
    #: override arrived.
    anchors: dict[str, tuple[float, float]] = Field(default_factory=dict)

    #: Where this art came from and what its licence obliges. Not decoration:
    #: `an credits` walked ONLY `mall["characters"]`, so the PR that gives
    #: environments art is the PR that closes that hole — otherwise
    #: `an credits` becomes an affirmative false statement about plates.
    source: AssetSource | None = None

    metadata: dict[str, Any] = Field(default_factory=dict)


@register_migration(ENVIRONMENT_DOCUMENT_KIND.name, "0.0.0", ENVIRONMENT_SCHEMA_VERSION)
def _adopt_the_free_form_environment(doc: dict[str, Any]) -> dict[str, Any]:
    """0.0.0 → 0.1.0: today's free-form entries become plane-less descriptors.

    Every environment on disk is a `JsonSidecarStore` `meta.json` with whatever
    keys its author wrote — commonly `name`, `description`, `tags`, and the
    three colour scalars the preset path reads. They are carried through
    untouched: `extra="allow"` keeps them, `planes` defaults to empty, and a
    plane-less descriptor takes the preset path, so the compiled document does
    not move.

    >>> _adopt_the_free_form_environment({"name": "park", "tags": ["outdoor"]})
    {'name': 'park', 'tags': ['outdoor'], 'schema_version': '0.1.0', 'kind': 'EnvironmentDescriptor'}
    """
    doc = dict(doc)
    doc["schema_version"] = ENVIRONMENT_SCHEMA_VERSION
    doc.setdefault("kind", ENVIRONMENT_DOCUMENT_KIND.name)
    return doc
