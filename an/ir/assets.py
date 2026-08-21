"""Where a third-party asset came from, and what its licence obliges.

`an` composes work it did not create — avatar art from a generator, stock images,
fonts, commissioned SVG — into a video its user ships. A licence defect is the
only failure in this package that reaches *backwards* through completed work: a
video shipped with an unattributed CC BY asset cannot be un-shipped, whereas
every rendering bug can be fixed forward.

Until now there was nowhere to record any of it. The character descriptor's
`metadata` dict carried a comment saying it *could* hold a licence; nothing ever
put one there.

## Why the field names look borrowed

They are. The rights fields are spelled exactly as `illustration.ImageResult`
spells them — `license`, `license_url`, `attribution`, `source_page_url`,
`author`, `author_url`, `cacheable`, `provider`, `id`, `url` — because
`illustration` is the federation's image-retrieval package and its results are
the most likely thing to become an `AssetSource`. Identical names mean the
adapter is a dict copy rather than a rename table, and a rename table is where a
field quietly stops being carried.

`tests/` pins this literally. That is the precedent `artful` already set for
shared vocabulary across packages that must not depend on each other.

Two fields are `an`'s own, because `ImageResult` has no equivalent:

- `sha256` — the digest of the bytes as they entered the project. A licence
  attached to a URL is a licence attached to whatever that URL serves *today*;
  attached to a digest, it stays attached to the thing that was actually used.
- `cost_usd` — what acquiring it cost, if anything. **`None` means unknown, never
  free.** That is the federation's rule for costs and it matters here for the
  same reason it matters in a plan: a `0.0` that means "we did not check" reads
  as "this was free" to every consumer downstream.

## The long-term home

This is a local definition of something three packages need — `illustration`
retrieves third-party images, `an` composes them, `reelee` ships the result — and
that is the signature of a missing federation primitive rather than three missing
local fields. It is proposed upstream as `lacing.Artifact.rights` (lacing#34).
`Artifact` is frozen and holds real deployed data, so that change needs a
registered migration and a coordinated release; until it lands, this mirror is
what keeps `an` from shipping unattributed work in the meantime.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

__all__ = ["AssetSource", "ATTRIBUTION_REQUIRING_LICENSES", "requires_attribution"]

#: Licence codes that oblige the *user of the output* to credit someone.
#:
#: Matched case-insensitively against the licence code. Deliberately a small,
#: explicit set rather than a pattern: "does this oblige me" is a question with
#: a legal answer, and a regex that guesses is worse than a list that admits what
#: it does not know. An unrecognised licence is reported as UNKNOWN, which is
#: not the same as "no obligation".
ATTRIBUTION_REQUIRING_LICENSES: frozenset[str] = frozenset(
    {
        "cc-by-4.0",
        "cc-by",
        "by",
        "cc-by-sa-4.0",
        "cc-by-sa",
        "by-sa",
        "cc-by-nd",
        "cc-by-nc",
    }
)


class AssetSource(BaseModel):
    """Provenance and rights for one third-party asset.

    >>> s = AssetSource(provider="dicebear", id="lorelei/amy", license="cc0-1.0")
    >>> requires_attribution(s)
    False
    >>> s = AssetSource(provider="dicebear", id="adventurer/amy", license="cc-by-4.0")
    >>> requires_attribution(s)
    True
    """

    model_config = ConfigDict(extra="allow")

    # --- identity. Names match illustration.ImageResult exactly.
    provider: str = Field(description="Where it came from, e.g. 'dicebear'.")
    id: str | None = Field(default=None, description="Provider-native identifier.")
    url: str | None = Field(default=None, description="Where it was fetched from.")

    # --- rights. Names match illustration.ImageResult exactly.
    license: str | None = Field(
        default=None,
        description="Licence code, e.g. 'cc0-1.0'. None means UNKNOWN, not unencumbered.",
    )
    license_url: str | None = None
    attribution: str | None = Field(
        default=None, description="Ready-to-render attribution sentence."
    )
    source_page_url: str | None = None
    author: str | None = None
    author_url: str | None = None
    cacheable: bool = True

    # --- an's own.
    sha256: str | None = Field(
        default=None,
        description="Digest of the bytes as they entered the project.",
    )
    cost_usd: float | None = Field(
        default=None,
        description="Acquisition cost. None means UNKNOWN — never free.",
    )
    extra: dict[str, Any] = Field(default_factory=dict)


def requires_attribution(source: AssetSource) -> bool | None:
    """Whether shipping this asset obliges the user to credit someone.

    Returns ``None`` for an unrecognised or absent licence: "we do not know" is a
    distinct answer from "no", and collapsing them is how an obligation gets
    silently dropped.
    """
    if not source.license:
        return None
    code = source.license.strip().lower()
    if code in ATTRIBUTION_REQUIRING_LICENSES:
        return True
    if code.startswith(("cc0", "public-domain", "mit", "apache", "bsd")):
        return False
    return None
