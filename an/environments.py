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
`0.0`        infinitely far: does not PAN — pinned against a camera
             translation. It still scales and rotates with the camera; see
             the limit below.
`0 < d < 1`  background — Godot's own sanity range is 0.1 sky → 0.7 forest
`1.0`        the character plane; **emits nothing**, which is today's
             behaviour for everything and why this is byte-identity-free
`> 1.0`      foreground: nearer than the characters, moving faster
===========  ===============================================================

**`depth` governs translation only, and that is a stated limit rather than an
oversight.** The compensation is on `x`/`y`; `root.scale` and `root.rotation`
multiply the whole composed expression, so no per-plane factor can cancel
them. Measured: a `depth = 0` plane under `push_in` grows 1.0 → 1.25× and
drifts, exactly like the character plane.

Depth-aware zoom is the **dolly**, and the design of record defers it with its
reason: a true dolly grows the foreground ×1.40 while the moon grows ×1.02,
where today's `push_in` grows both ×1.25 — precisely the uniform zoom the 1937
multiplane camera was built to replace. Until `dolly_in` exists, pair a
`depth = 0` plate with a pan, not a zoom.

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

from pydantic import BaseModel, ConfigDict, Field, model_validator

from an.ir.assets import AssetSource
from an.ir.migrate import DocumentKind, register_kind

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
#:
#: **No migration ladder, deliberately.** The obvious one — "today's free-form
#: `meta.json` entries become plane-less descriptors" — would migrate nothing,
#: because a free-form entry ALREADY validates as an `EnvironmentDescriptor`
#: with no planes: `extra="allow"` carries `description`/`tags`/the colour
#: scalars through untouched, and every field this model adds has a default.
#: There is no shape change to make.
#:
#: It was written and then removed in review (an#110): `_environment_descriptor`
#: gates on the `kind` tag before migrating, so only a document already written
#: in the post-an#110 shape could ever have reached it — a registered migration
#: that runs on nothing, which is the decoration `CLAUDE.md`'s "never register
#: a migration without a read path that runs it" rule exists to prevent. The
#: KIND stays registered: it declares where the version field lives, which is
#: what a future real migration will need.
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

    @model_validator(mode="after")
    def _plane_names_are_unique(self) -> "EnvironmentDescriptor":
        """Two planes with one name is a silent loss, not a duplicate drawing.

        A plane's name becomes its node name AND its parallax animation id, so
        the second of a pair overwrites the first in the animations dict while
        `tracks` still holds two clips pointing at the survivor: both planes
        draw, one depth is honoured, nothing warns (an#110 review, M3). It also
        makes `characters_after` ambiguous — the split matches the first.
        """
        seen: dict[str, int] = {}
        for plane in self.planes:
            seen[plane.name] = seen.get(plane.name, 0) + 1
        dupes = sorted(n for n, c in seen.items() if c > 1)
        if dupes:
            raise ValueError(
                f"environment {self.name!r} declares planes with duplicate names "
                f"{dupes}. A plane's name is its node name and its parallax "
                "animation id, so one of each pair would draw with the other's "
                "depth and nothing would say so."
            )
        return self
