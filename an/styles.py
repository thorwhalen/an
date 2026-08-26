"""StylePack: art direction as a document, and the first reader the styles store has had.

The `styles` store was nine lines with no consumer — `rg '\\["styles"\\]'` found
only the mall wiring — while colour lived in three disconnected places: the
compiler's `_CHARACTER_PALETTES`, six literals inside `runtime.js`, and the
character factory, which carried *two disagreeing* palette tables. an#106
retired `AssetRef(kind="style")` because it selected nothing. This is what the
word was reserved for.

**A pack does not recolour SVG art at compile time.** Four code-backed reasons,
and they are the reason this module is small:

1. It would break `src` content addressing and the asset-resolution ledger.
2. The only substitution precedent in this package is a regex
   (`_skin_fill_of`) — which is exactly what `bench/palette.py` had to abandon
   for XML parsing.
3. `CharacterDescriptor` has no role tagging, so a pack would have to infer a
   role from a pixel; inferring a role from a pixel is what produced an#99's
   wrong-tone lid.
4. `tint` occurs zero times in the runtime.

**Nothing recolours SVG art today.** A pack seam in the character factory —
which already owns the colour seams — is the obvious home for it and is NOT
built; the compiler **warns**, naming the entities it could not reach, and says
so rather than pointing at a flag that does not exist. Until then, an SVG rig
is recoloured by editing its art or generating it in the colours you want. A pack that silently did nothing to an SVG rig would be the
worst of the options.

**A pack must not declare a role it cannot change.** `lip`, `mouth_fill`,
`teeth`, `tongue` and the eye's white are literals inside `runtime.js`; a role
that resolves to nothing is worse than an absent one, and :data:`UNREACHABLE_ROLES`
plus its test is what keeps the list honest.

**No `line.width`.** A first draft carried one, and nothing read it: the
procedural rig's stroke is a `runtime.js` literal and an SVG rig's is inside
its drawing, so the field was written, serialized, and consumed nowhere — the
same shape this module refuses in `UNREACHABLE_ROLES`, and a rule is not a rule
if it exempts the module that states it. It comes back when something reads it.

Colours are **hex strings**, deliberately not DTCG colour objects:
`bench/palette.py` mirrors `runtime.js` verbatim, and a second colour
representation doubles the surface on which the two can silently diverge.
"""

from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

from an.ir.assets import AssetSource
from an.ir.migrate import DocumentKind, register_kind

__all__ = [
    "STYLE_SCHEMA_VERSION",
    "STYLE_DOCUMENT_KIND",
    "REACHABLE_ROLES",
    "UNREACHABLE_ROLES",
    "StylePack",
    "resolve_palette",
]

STYLE_SCHEMA_VERSION = "0.1.0"

STYLE_DOCUMENT_KIND: DocumentKind = register_kind(
    DocumentKind(
        name="StylePack",
        version_field="schema_version",
        current_version=STYLE_SCHEMA_VERSION,
    )
)

#: Roles a pack can actually change, because the COMPILER decides them and
#: stamps them into the document the runtime draws.
#:
#: `skin`, `clothing` and `hair` are `_CHARACTER_PALETTES`' three components;
#: `leg` and `pupil` are the compiler's own literals (`DFLT_LEG_COLOUR`,
#: `DFLT_PUPIL_COLOUR`); `sky` and `ground` are the environment presets'.
#:
#: Every one of these is compiled with a marker colour and asserted to reach
#: the document by `tests/test_styles.py`. `pupil` shipped in this set wired to
#: NOTHING (an#112 review) because the guard checked set membership against the
#: set it was checking — a declared-reachable role that reaches nothing is the
#: same defect as an unreachable one, and it needs the same kind of test.
REACHABLE_ROLES: frozenset[str] = frozenset(
    {"skin", "clothing", "hair", "leg", "pupil", "sky", "ground"}
)

#: Roles a pack must NOT declare, with what makes each unreachable. These are
#: `runtime.js` literals: `_LIP_COLOR`, `_MOUTH_FILL`, `_TEETH_COLOR`,
#: `_TONGUE_COLOR`, and the eye white's `0xffffff` — none of them read anything
#: from the compiled document, so a pack that named them would be accepted,
#: change nothing, and say nothing. `tests/test_styles.py` reads the literals
#: out of `runtime.js` so this list cannot quietly stop matching it.
UNREACHABLE_ROLES: dict[str, str] = {
    "lip": "runtime.js `_LIP_COLOR`, drawn by makeMouth and never read from the document",
    "mouth_fill": "runtime.js `_MOUTH_FILL`",
    "teeth": "runtime.js `_TEETH_COLOR`",
    "tongue": "runtime.js `_TONGUE_COLOR`",
    "eye_sclera": "runtime.js draws the eye white as a literal 0xffffff in makeEye",
}


class StylePack(BaseModel):
    """Art direction for a project. Saved in the `styles` store.

    >>> pack = StylePack(name="noir", roles={"skin": "#d8d8d8", "clothing": "#202028"})
    >>> pack.colour_for("skin")
    '#d8d8d8'
    >>> pack.colour_for("hair") is None
    True

    Per-entity overrides win over roles, which is what makes a pack usable on a
    scene where one character must stay off-palette:

    >>> pack = StylePack(name="noir", roles={"skin": "#d8d8d8"},
    ...                  entities={"maya": {"skin": "#f4c89a"}})
    >>> pack.colour_for("skin", entity="maya"), pack.colour_for("skin", entity="bob")
    ('#f4c89a', '#d8d8d8')

    A role the renderer cannot reach is refused at construction, not ignored:

    >>> StylePack(name="x", roles={"lip": "#800000"})
    Traceback (most recent call last):
    ...
    pydantic_core._pydantic_core.ValidationError: ...
    """

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    schema_version: str = STYLE_SCHEMA_VERSION
    kind: Literal["StylePack"] = "StylePack"

    name: str
    #: ``{role: "#rrggbb"}``. Hex strings, not colour objects — see the module
    #: docstring for why a second representation is a liability here.
    roles: dict[str, str] = Field(default_factory=dict)
    #: ``{entity id: {role: "#rrggbb"}}`` — a per-entity override of `roles`.
    entities: dict[str, dict[str, str]] = Field(default_factory=dict)
    source: AssetSource | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _every_role_is_reachable(self) -> "StylePack":
        """Refuse a role the renderer cannot change.

        A role that silently does nothing is worse than an absent one: the
        author sees a field they set, a render that ignores it, and nothing
        anywhere connecting the two. The same rule an#110 applied to
        `repeat`/`TilingSprite` — ship the whole thing or none of it.
        """
        for where, mapping in [("roles", self.roles), *self.entities.items()]:
            for role in mapping:
                if role in UNREACHABLE_ROLES:
                    raise ValueError(
                        f"{where}.{role} is not reachable by a style pack: "
                        f"{UNREACHABLE_ROLES[role]}. Declaring it would change "
                        "nothing and say nothing. Reachable roles are "
                        f"{sorted(REACHABLE_ROLES)}."
                    )
                if role not in REACHABLE_ROLES:
                    raise ValueError(
                        f"{where}.{role} is not a role this renderer knows. "
                        f"Reachable roles are {sorted(REACHABLE_ROLES)}."
                    )
        return self

    def colour_for(self, role: str, *, entity: str | None = None) -> Optional[str]:
        """The colour for ``role``, or ``None`` when the pack does not set it.

        ``None`` rather than a default: the caller holds today's literal, and a
        pack that does not mention a role must leave it exactly as it was —
        which is what keeps a scene with no pack byte-identical.
        """
        if entity is not None:
            override = self.entities.get(entity, {}).get(role)
            if override is not None:
                return override
        return self.roles.get(role)


def resolve_palette(
    pack: "StylePack | None",
    entity: str,
    default: tuple[str, str, str],
) -> tuple[str, str, str]:
    """``(skin, clothing, hair)`` for one entity under ``pack``.

    A **lookup with a default**, not a rewrite: with no pack, or with a pack
    that mentions none of the three, the caller's own literals come back
    unchanged and the compiled document does not move a byte.

    >>> resolve_palette(None, "maya", ("#f4c89a", "#3a6ea5", "#3b2a1a"))
    ('#f4c89a', '#3a6ea5', '#3b2a1a')
    >>> pack = StylePack(name="noir", roles={"clothing": "#202028"})
    >>> resolve_palette(pack, "maya", ("#f4c89a", "#3a6ea5", "#3b2a1a"))
    ('#f4c89a', '#202028', '#3b2a1a')
    """
    if pack is None:
        return default
    skin, clothing, hair = default
    return (
        pack.colour_for("skin", entity=entity) or skin,
        pack.colour_for("clothing", entity=entity) or clothing,
        pack.colour_for("hair", entity=entity) or hair,
    )
