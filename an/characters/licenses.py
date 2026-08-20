"""DiceBear per-style licences, as data.

The DiceBear *software* licence (MIT) is a separate fact from each *style*
licence — DiceBear itself splits them under literal ``# Design`` and ``# Code``
headings inside every per-style licence file. Reading the repo's top-level MIT
and concluding the avatars are MIT is the trap this table exists to close.

Verified against the per-style licence files and style pages at the pinned API
major; the sources are recorded in ``misc/docs/wave1_verification.md`` §2.

Of the styles `an` can request: 11 are CC0 (no attribution duty), 12 are
CC BY 4.0 (a real duty), and the Pablo Stanley set carries bespoke
"free for personal and commercial use" terms that are **not** Creative Commons —
which is why they are their own code here rather than being rounded to one.
"""

from __future__ import annotations

from typing import NamedTuple

CC0 = "cc0-1.0"
CC_BY = "cc-by-4.0"
MIT = "mit"
#: Pablo Stanley's own terms. Permissive in effect, not a CC licence, and not
#: something to silently relabel as one.
FREE_PERSONAL_AND_COMMERCIAL = "free-personal-and-commercial"

CC0_URL = "https://creativecommons.org/publicdomain/zero/1.0/"
CC_BY_URL = "https://creativecommons.org/licenses/by/4.0/"


class StyleLicense(NamedTuple):
    """One style's design licence and the credit it obliges."""

    license: str
    license_url: str | None
    author: str | None
    author_url: str | None
    source_title: str | None
    source_page_url: str | None


#: style name -> its DESIGN licence. Absent from this table means "unverified",
#: which is refused rather than assumed permissive.
DICEBEAR_STYLE_LICENSES: dict[str, StyleLicense] = {
    # ---- CC0: no attribution duty.
    "lorelei": StyleLicense(
        CC0,
        CC0_URL,
        "Lisa Wischofsky",
        "https://www.instagram.com/lischi_art/",
        "Lorelei",
        "https://www.figma.com/community/file/1198749693280469639",
    ),
    "lorelei-neutral": StyleLicense(
        CC0,
        CC0_URL,
        "Lisa Wischofsky",
        "https://www.instagram.com/lischi_art/",
        "Lorelei Neutral",
        "https://www.figma.com/community/file/1198749693280469639",
    ),
    "notionists": StyleLicense(
        CC0,
        CC0_URL,
        "Zoish",
        "https://bio.link/heyzoish",
        "Notionists",
        "https://heyzoish.gumroad.com/l/notionists",
    ),
    "notionists-neutral": StyleLicense(
        CC0,
        CC0_URL,
        "Zoish",
        "https://bio.link/heyzoish",
        "Notionists",
        "https://heyzoish.gumroad.com/l/notionists",
    ),
    "open-peeps": StyleLicense(
        CC0,
        CC0_URL,
        "Pablo Stanley",
        "https://twitter.com/pablostanley",
        "Open Peeps",
        "https://www.openpeeps.com/",
    ),
    "pixel-art": StyleLicense(
        CC0,
        CC0_URL,
        "DiceBear",
        None,
        "Pixel Art",
        "https://www.figma.com/community/file/1198754108850888330",
    ),
    "pixel-art-neutral": StyleLicense(
        CC0,
        CC0_URL,
        "DiceBear",
        None,
        "Pixel Art Neutral",
        "https://www.figma.com/community/file/1198754108850888330",
    ),
    "identicon": StyleLicense(
        CC0, CC0_URL, "DiceBear", None, None, "https://www.dicebear.com"
    ),
    "shapes": StyleLicense(
        CC0, CC0_URL, "DiceBear", None, None, "https://www.dicebear.com"
    ),
    "thumbs": StyleLicense(
        CC0, CC0_URL, "DiceBear", None, None, "https://www.dicebear.com"
    ),
    # ---- MIT (no design/code split for these two).
    "icons": StyleLicense(
        MIT,
        "https://opensource.org/licenses/MIT",
        "The Bootstrap Authors",
        None,
        None,
        None,
    ),
    "initials": StyleLicense(
        MIT, "https://opensource.org/licenses/MIT", "Florian Körner", None, None, None
    ),
    # ---- CC BY 4.0: attribution required, and `an` produces a MODIFIED work,
    #      so "indicate if changes were made" is live rather than theoretical.
    "adventurer": StyleLicense(
        CC_BY,
        CC_BY_URL,
        "Lisa Wischofsky",
        "https://www.instagram.com/lischi_art/",
        "Adventurer",
        "https://www.figma.com/community/file/1184595184137881796",
    ),
    "adventurer-neutral": StyleLicense(
        CC_BY,
        CC_BY_URL,
        "Lisa Wischofsky",
        "https://www.instagram.com/lischi_art/",
        "Adventurer Neutral",
        "https://www.figma.com/community/file/1184595184137881796",
    ),
    "big-ears": StyleLicense(
        CC_BY,
        CC_BY_URL,
        "The Visual Team",
        "https://thevisual.team/",
        "Face Generator",
        "https://www.figma.com/community/file/986078800058673824",
    ),
    "big-ears-neutral": StyleLicense(
        CC_BY,
        CC_BY_URL,
        "The Visual Team",
        "https://thevisual.team/",
        "Face Generator",
        "https://www.figma.com/community/file/986078800058673824",
    ),
    "big-smile": StyleLicense(
        CC_BY,
        CC_BY_URL,
        "Ashley Seo",
        "http://www.ashleyseo.com/",
        "Custom Avatar",
        "https://www.figma.com/community/file/881358461963645496",
    ),
    "croodles": StyleLicense(
        CC_BY,
        CC_BY_URL,
        "vijay verma",
        "https://vjy.me/",
        "Croodles - Doodle your face",
        "https://www.figma.com/community/file/966199982810283152",
    ),
    "croodles-neutral": StyleLicense(
        CC_BY,
        CC_BY_URL,
        "vijay verma",
        "https://vjy.me/",
        "Croodles - Doodle your face",
        "https://www.figma.com/community/file/966199982810283152",
    ),
    "fun-emoji": StyleLicense(
        CC_BY,
        CC_BY_URL,
        "Davis Uche",
        "https://www.instagram.com/davedirect3/",
        "Fun Emoji Set",
        "https://www.figma.com/community/file/968125295144990435",
    ),
    "micah": StyleLicense(
        CC_BY,
        CC_BY_URL,
        "Micah Lanier",
        "https://dribbble.com/micahlanier",
        "Avatar Illustration System",
        "https://www.figma.com/community/file/829741575478342595",
    ),
    "miniavs": StyleLicense(
        CC_BY,
        CC_BY_URL,
        "Webpixels",
        "https://webpixels.io/",
        "Miniavs - Free Avatar Creator",
        "https://www.figma.com/community/file/923211396597067458",
    ),
    "personas": StyleLicense(
        CC_BY,
        CC_BY_URL,
        "Draftbit",
        "https://draftbit.com/",
        "Personas by Draftbit",
        "https://personas.draftbit.com/",
    ),
    # ---- Bespoke terms. Permissive in effect; NOT a CC licence.
    "avataaars": StyleLicense(
        FREE_PERSONAL_AND_COMMERCIAL,
        "https://avataaars.com/",
        "Pablo Stanley",
        "https://twitter.com/pablostanley",
        None,
        "https://avataaars.com/",
    ),
    "avataaars-neutral": StyleLicense(
        FREE_PERSONAL_AND_COMMERCIAL,
        "https://avataaars.com/",
        "Pablo Stanley",
        "https://twitter.com/pablostanley",
        None,
        "https://avataaars.com/",
    ),
    "bottts": StyleLicense(
        FREE_PERSONAL_AND_COMMERCIAL,
        "https://bottts.com/",
        "Pablo Stanley",
        "https://twitter.com/pablostanley",
        None,
        "https://bottts.com/",
    ),
    "bottts-neutral": StyleLicense(
        FREE_PERSONAL_AND_COMMERCIAL,
        "https://bottts.com/",
        "Pablo Stanley",
        "https://twitter.com/pablostanley",
        None,
        "https://bottts.com/",
    ),
}

#: Licences that need no credit from whoever ships the output.
NO_ATTRIBUTION_REQUIRED: frozenset[str] = frozenset(
    {CC0, MIT, FREE_PERSONAL_AND_COMMERCIAL}
)


def attribution_for(style: str) -> str | None:
    """DiceBear's own attribution template, filled in. ``None`` when none is owed.

    The wording is theirs, verbatim from the per-style package README, including
    the "Remix of the original" half — which is what discharges CC BY's
    "indicate if changes were made" clause. `an` wraps the avatar into a rig, so
    it genuinely produces a modified work.
    """
    lic = DICEBEAR_STYLE_LICENSES.get(style)
    if lic is None or lic.license in NO_ATTRIBUTION_REQUIRED:
        return None
    title = (
        f"{lic.source_title} ({lic.source_page_url})"
        if lic.source_title
        else lic.source_page_url
    )
    author = f"{lic.author} ({lic.author_url})" if lic.author_url else lic.author
    return (
        f"The avatar style is based on {title} by {author}, licensed under "
        f"CC BY 4.0 ({lic.license_url}). / Remix of the original."
    )


def dicebear_source(style: str, *, seed: str) -> "AssetSource":
    """Build the provenance record for a DiceBear avatar.

    Raises on an unlisted style rather than returning an empty record: "we did
    not check" and "there is nothing to discharge" must not look the same, and
    a `None` licence silently reads as the latter everywhere downstream.
    """
    from an.ir.assets import AssetSource

    lic = DICEBEAR_STYLE_LICENSES.get(style)
    if lic is None:
        raise ValueError(
            f"DiceBear style {style!r} has no verified licence entry, so its "
            "rights cannot be recorded. Add it to DICEBEAR_STYLE_LICENSES with "
            "the licence read from that style's own LICENSE file — the DiceBear "
            "software licence (MIT) is a separate fact from each style's design "
            f"licence. Known: {sorted(DICEBEAR_STYLE_LICENSES)}"
        )
    return AssetSource(
        provider="dicebear",
        id=f"{style}/{seed}",
        url=f"https://api.dicebear.com/{{version}}/{style}/svg?seed={seed}",
        license=lic.license,
        license_url=lic.license_url,
        attribution=attribution_for(style),
        source_page_url=lic.source_page_url,
        author=lic.author,
        author_url=lic.author_url,
        cost_usd=0.0,  # genuinely free, and we checked — not "unknown"
        extra={"dicebear_style": style, "dicebear_seed": seed},
    )


def requires_acknowledgement(style: str) -> bool:
    """Whether using ``style`` puts a duty on whoever ships the output.

    An unlisted style counts as requiring acknowledgement — an unverified
    licence is a refusal, not a warning.
    """
    lic = DICEBEAR_STYLE_LICENSES.get(style)
    return lic is None or lic.license not in NO_ATTRIBUTION_REQUIRED
