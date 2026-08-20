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


def collect_credits(mall: Mapping[str, Any]) -> CreditsReport:
    """Walk a project mall and gather every recorded :class:`AssetSource`.

    Only the characters store carries provenance today. Environments, styles and
    props will as they gain real art; this returns what exists rather than
    pretending the walk is complete.
    """
    report = CreditsReport()
    characters = mall.get("characters")
    if characters is None:
        return report
    for key in sorted(characters):
        try:
            descriptor = characters[key]
        except Exception:  # noqa: BLE001 — an unreadable entry is not a credit
            continue
        source = getattr(descriptor, "source", None)
        if source is None and isinstance(descriptor, Mapping):
            raw = descriptor.get("source")
            source = AssetSource.model_validate(raw) if raw else None
        if source is not None:
            report.entries.append(CreditEntry(asset=f"characters/{key}", source=source))
    return report


def credits_for_project(project_dir: str | Path) -> CreditsReport:
    """Credits for the project at ``project_dir``."""
    from an.project import load

    return collect_credits(load(Path(project_dir)).mall)
