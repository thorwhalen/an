"""What a rendered video owes, and to whom.

`an` composes work it did not create into a video its user ships. Recording where
that work came from (`an.ir.assets.AssetSource`) is half the job; the other half
is being able to *produce* the credits, because **a licence recorded and never
displayed is not compliance** — it is a note to oneself.

So this module walks a project's reachable assets and answers three questions a
user actually has:

- what third-party work is in this video?
- what must I display, verbatim, to ship it?
- is anything in here unverified?

The third is the one that matters most and is easiest to lose. An asset with no
licence is reported as **UNKNOWN**, never as "nothing owed": those are different
answers, and collapsing them is exactly how an obligation goes missing.
"""

from __future__ import annotations

import warnings

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from an.ir.assets import AssetSource, requires_attribution

__all__ = ["CreditsReport", "collect_credits", "credits_for_project"]


@dataclass(frozen=True)
class CreditEntry:
    """One third-party asset and what it obliges."""

    asset: str
    source: AssetSource

    @property
    def attribution_required(self) -> bool | None:
        """``None`` means unknown — which is not the same as ``False``."""
        return requires_attribution(self.source)


@dataclass
class CreditsReport:
    """Everything a project owes, split by whether we actually know."""

    entries: list[CreditEntry] = field(default_factory=list)

    @property
    def owed(self) -> list[CreditEntry]:
        """Entries that definitely require an attribution."""
        return [e for e in self.entries if e.attribution_required is True]

    @property
    def unverified(self) -> list[CreditEntry]:
        """Entries whose licence we could not classify.

        Deliberately its own list rather than folded into :attr:`owed`. Folding
        them in cries wolf; folding them into "nothing owed" hides a real
        obligation. Neither is honest, so they are counted separately — the same
        reason `priv`'s upkeep keeps `unavailable` apart from `findings`.
        """
        return [e for e in self.entries if e.attribution_required is None]

    def to_dict(self) -> dict[str, Any]:
        return {
            "assets": [
                {"asset": e.asset, **e.source.model_dump(exclude_none=True)}
                for e in self.entries
            ],
            "attribution_required": [
                {"asset": e.asset, "text": e.source.attribution} for e in self.owed
            ],
            "unverified": [e.asset for e in self.unverified],
        }

    def format(self) -> str:
        """Human-readable, and honest about what it does not know."""
        if not self.entries:
            return "credits: no third-party assets recorded."
        lines = [f"credits: {len(self.entries)} third-party asset(s)."]
        if self.owed:
            lines.append("")
            lines.append("MUST BE DISPLAYED to ship this video:")
            for e in self.owed:
                text = e.source.attribution or (
                    f"(licence {e.source.license!r} requires attribution but none "
                    "was recorded — the record is incomplete)"
                )
                lines.append(f"  {e.asset}: {text}")
        if self.unverified:
            lines.append("")
            lines.append(
                "UNVERIFIED — licence unknown or unrecognised. Unknown is not "
                "the same as unencumbered; check before shipping:"
            )
            for e in self.unverified:
                lines.append(f"  {e.asset}: license={e.source.license!r}")
        clear = [e for e in self.entries if e.attribution_required is False]
        if clear:
            lines.append("")
            lines.append(f"No attribution required ({len(clear)}):")
            for e in clear:
                lines.append(f"  {e.asset}: {e.source.license}")
        return "\n".join(lines)


class CreditsWarning(UserWarning):
    """A credits walk could not read something it was asked to read."""


def collect_credits(mall: Mapping[str, Any]) -> CreditsReport:
    """Walk a project mall and gather every recorded :class:`AssetSource`.

    Three stores carry provenance: characters, **props** (an#108) and
    **environments** (an#110). Each was added by the PR that gave that store
    real art, which is the rule rather than a coincidence — a walk that skips a
    store holding third-party plates does not return less information, it
    returns an affirmative false statement to exactly the people who need the
    opposite. Styles will join when a StylePack has art (#112).

    Legacy reconstruction runs on characters only: it recovers a DiceBear
    record from `metadata.dicebear_*`, which no other store has ever written.
    """
    report = CreditsReport()
    for store_name in ("characters", "props", "environments"):
        store = mall.get(store_name)
        if store is None:
            continue
        try:
            keys = sorted(store)
        except Exception:  # noqa: BLE001 — see below
            # The ITERATION, not just the per-key read. an#110 took this walk
            # from one store to three, so an unreadable backing store went from
            # "characters are missing" to "`an credits` raises" — and a credits
            # report that cannot run is the one output whose absence is
            # indistinguishable from "no third-party assets", which is the
            # false compliance statement this module exists to avoid.
            warnings.warn(
                f"the {store_name!r} store could not be listed, so its assets are "
                "absent from this report. That is a GAP, not a clean bill: "
                "re-run when the store is readable.",
                CreditsWarning,
                stacklevel=2,
            )
            continue
        for key in keys:
            try:
                descriptor = store[key]
            except Exception:  # noqa: BLE001 — an unreadable entry is not a credit
                continue
            source = getattr(descriptor, "source", None)
            if source is None and isinstance(descriptor, Mapping):
                raw = descriptor.get("source")
                source = AssetSource.model_validate(raw) if raw else None
            if source is None and store_name == "characters":
                source = _reconstruct_legacy_source(descriptor)
            if source is not None:
                report.entries.append(
                    CreditEntry(asset=f"{store_name}/{key}", source=source)
                )
    return report


def _reconstruct_legacy_source(descriptor: Any) -> AssetSource | None:
    """Recover provenance from a descriptor written before ``source`` existed.

    **The users most at risk are the ones with no ``source`` field**, because
    every character created before it existed used a CC BY default. Reporting
    those as "no third-party assets recorded" is not an absence of information —
    it is an affirmative, false compliance statement, made to exactly the people
    who need the opposite.

    The evidence is right there in the same file: `new_character` has always
    written ``metadata.dicebear_style`` and ``metadata.dicebear_seed``. So this
    reconstructs the record rather than shrugging.

    Returns ``None`` only when the art genuinely was not third-party (the
    offline geometric fallback), which is the one case where "nothing owed" is
    the true answer.
    """
    metadata = getattr(descriptor, "metadata", None)
    if metadata is None and isinstance(descriptor, Mapping):
        metadata = descriptor.get("metadata")
    if not isinstance(metadata, Mapping):
        return None
    style = metadata.get("dicebear_style")
    if not style:
        return None  # fallback_geometric — we made it, nothing is owed
    from an.characters.licenses import DICEBEAR_STYLE_LICENSES, dicebear_source

    seed = str(metadata.get("dicebear_seed") or "")
    if style in DICEBEAR_STYLE_LICENSES:
        return dicebear_source(style, seed=seed)
    # An unrecognised style is UNKNOWN, never "nothing owed".
    return AssetSource(provider="dicebear", id=f"{style}/{seed}", license=None)


def credits_for_project(project_dir: str | Path) -> CreditsReport:
    """Credits for the project at ``project_dir``."""
    from an.project import load

    return collect_credits(load(Path(project_dir)).mall)
