"""The ledger: three blocks that must never be mixed, and the guards that keep them apart.

``metrics`` — numbers, each labelled **render-side** or **encode-side**. The
two families are blind to each other's mutations by construction, and a
comparison that mixes them is comparing two different questions.

``tripwires`` — change detectors. They fire on improvements and regressions
alike, so they count **zero** toward any criterion. A tripwire in the metrics
block is a boolean wearing a measurement's clothes.

``provenance`` — never gated, never counted. Everything needed to decide
whether two rows may be compared *at all*: the scene contract hash, the
resolved encode and decode commands, the mask parameters, the palette, and the
environment tuple split into a render side (**comparable on any machine** — the
pixels are ISA- and OS-invariant at a pinned Chromium build) and an encode side
(**machine-scoped** — a different x264 build moves the decoded stream by two
orders of magnitude, and a band wide enough to absorb that would swallow
``flat_field_deviation``'s entire crf18->23 signal).

Four value states, not two. ``no change`` and ``null`` are famously easy to
conflate, and conflating them lets any pre-encode statistic pad the witness
count for free:

============= ===========================================================
``measured``  a number
``gated``     the comparison is impossible — the reference moved, or the
              source hash differs. **Uninterpretable, not good or bad.**
``unavailable`` the check could not run (ffmpeg absent, golden absent). A
              check that crashed is not evidence anything is fine.
``no change`` a *prediction*, never a value state, and one that can never
              count — see :mod:`an.bench.registry`.
============= ===========================================================
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from an.bench.registry import METRICS, MUTATIONS, TRIPWIRES

#: Bumped when a reader could misinterpret an older row. `an bench --compare`
#: (an#40) must refuse a version it does not understand rather than guess.
SCHEMA_VERSION: int = 1

State = Literal["measured", "gated", "unavailable"]


class LedgerSchemaError(ValueError):
    """A ledger row violates an invariant that would make it misreadable."""


@dataclass(frozen=True, slots=True)
class Value:
    """One measured (or deliberately absent) number."""

    value: float | int | bool | None
    state: State = "measured"
    gate: str | None = None
    detail: str = ""
    extra: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.state == "measured":
            if self.value is None:
                raise LedgerSchemaError(
                    "state='measured' with a null value. If the number could not "
                    "be produced, say which: 'gated' (the comparison is "
                    "impossible) or 'unavailable' (the check could not run)."
                )
        elif self.value is not None:
            raise LedgerSchemaError(
                f"state={self.state!r} must carry a null value, not "
                f"{self.value!r}. A substituted number — 0.0 especially — is "
                "read downstream as a measurement, which is exactly the "
                "unknown-is-not-zero failure this schema exists to prevent."
            )
        if self.state == "gated" and not self.gate:
            raise LedgerSchemaError(
                "a gated value must name its gate; a bare null is "
                "indistinguishable from 'nobody looked'"
            )
        if self.state == "unavailable" and not self.detail:
            raise LedgerSchemaError(
                "an unavailable value must say what could not run — 'unavailable' "
                "with no reason reads as 'fine, nothing to report'"
            )

    def to_dict(self) -> dict:
        out: dict = {"value": self.value, "state": self.state}
        if self.gate:
            out["gate"] = self.gate
        if self.detail:
            out["detail"] = self.detail
        out.update(self.extra)
        return out


#: Fields a per-scene row carries inline. Everything else about a metric — the
#: sentence, the notes, each prediction's reason and reference — is identical
#: for every scene in a row and for every row, so it lives once in the
#: ledger-level ``metric_declarations`` block.
#:
#: The split is not only size (it took a two-scene row from 54 KB to a third of
#: that, and an#38 quadruples the corpus). It is readability: a scene block you
#: can read is one where the numbers are not buried in the prose explaining
#: what the numbers are. What stays inline is exactly what
#: ``an bench --compare`` (an#40) keys on, so a comparison never has to consult
#: a second block to decide whether two rows may be compared at all.
INLINE_SPEC_FIELDS: tuple[str, ...] = (
    "side",
    "family",
    "comparison_scope",
    "reference",
    "counts",
)


def _inline(spec_dict: dict, value: Value) -> dict:
    """One scene's row for one metric: the value, plus what `--compare` keys on."""
    row = {k: spec_dict[k] for k in INLINE_SPEC_FIELDS if k in spec_dict}
    row["under_mutation"] = {
        mutation: {
            k: pred[k] for k in ("expect", "counts", "state", "gate") if k in pred
        }
        for mutation, pred in spec_dict["under_mutation"].items()
    }
    row.update(value.to_dict())
    return row


def metric_declarations() -> dict:
    """The full declaration of every metric and tripwire, once per ledger row.

    Carried IN the row rather than referenced, because ``--compare`` reads rows
    written by older registries: a row from six months ago has to be
    interpretable without checking out the commit that wrote it.
    """
    return {
        "metrics": {k: spec.to_dict() for k, spec in sorted(METRICS.items())},
        "tripwires": {k: spec.to_dict() for k, spec in sorted(TRIPWIRES.items())},
        "note": (
            "The full declaration of each metric, carried once. Per-scene rows "
            "inline only the value and the fields `an bench --compare` keys on."
        ),
    }


def build_scene_block(
    *,
    provenance: dict,
    metrics: dict[str, Value],
    tripwires: dict[str, Value],
) -> dict:
    """Assemble one scene's three blocks, refusing anything unreadable.

    Completeness is enforced in both directions. A metric the registry declares
    but the row omits is a silently narrower panel; a metric the row carries
    but the registry does not declare has no family, no side and no predicted
    direction, so nothing downstream can count it.
    """
    unknown = sorted(set(metrics) - set(METRICS))
    if unknown:
        raise LedgerSchemaError(
            f"the metrics block carries undeclared keys {unknown}. A metric with "
            "no registry entry has no family, no side and no per-mutation "
            "direction, so an#41's criterion cannot see it."
        )
    absent = sorted(set(METRICS) - set(metrics))
    if absent:
        raise LedgerSchemaError(
            f"the metrics block is missing {absent}. Every declared metric gets a "
            "row — as 'unavailable' if it could not run. An absent row and a "
            "null row look the same to a reader and mean opposite things."
        )
    unknown_tw = sorted(set(tripwires) - set(TRIPWIRES))
    if unknown_tw:
        raise LedgerSchemaError(f"undeclared tripwires {unknown_tw}")
    absent_tw = sorted(set(TRIPWIRES) - set(tripwires))
    if absent_tw:
        raise LedgerSchemaError(
            f"the tripwires block is missing {absent_tw}. A tripwire that stopped "
            "being computed vanishes from the row silently, and a change detector "
            "nobody notices has stopped detecting is worse than not having one."
        )
    overlap = sorted(set(metrics) & set(tripwires))
    if overlap:
        raise LedgerSchemaError(
            f"{overlap} appears in both blocks. A tripwire counts zero and a "
            "metric may count; a key that is both is a criterion nobody can evaluate."
        )
    for key in ("scene_contract_sha256", "resolution", "fps", "n_frames"):
        if key not in provenance:
            raise LedgerSchemaError(
                f"the scene provenance is missing {key!r}, which decides whether "
                "two rows may be compared at all"
            )
    return {
        "provenance": provenance,
        "metrics": {
            k: _inline(METRICS[k].to_dict(), v) for k, v in sorted(metrics.items())
        },
        "tripwires": {
            k: _inline(TRIPWIRES[k].to_dict(), v) for k, v in sorted(tripwires.items())
        },
    }


def build_ledger(*, provenance: dict, scenes: dict[str, dict]) -> dict:
    """The whole row."""
    return {
        "schema_version": SCHEMA_VERSION,
        "tool": "an.bench",
        "generated_at": datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z"),
        "mutations": list(MUTATIONS),
        "metric_declarations": metric_declarations(),
        "provenance": provenance,
        "scenes": scenes,
    }


def write_ledger(ledger: dict, path: Path) -> Path:
    """Write a row. ``sort_keys=True`` so two rows diff line-for-line."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(ledger, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def witnesses(ledger_scene: dict, mutation: str) -> dict[str, list[str]]:
    """Which metrics would count for ``mutation``, grouped by family.

    Reads the row rather than the registry, so an#41's criterion is evaluated
    against what was actually written down.

    >>> witnesses({"metrics": {"m": {"family": "A",
    ...     "under_mutation": {"high_crf": {"counts": True}}}}}, "high_crf")
    {'A': ['m']}
    """
    out: dict[str, list[str]] = {}
    for key, row in (ledger_scene.get("metrics") or {}).items():
        pred = (row.get("under_mutation") or {}).get(mutation) or {}
        if pred.get("counts"):
            out.setdefault(row.get("family", "?"), []).append(key)
    return {k: sorted(v) for k, v in sorted(out.items())}


def unavailable(detail: str) -> Value:
    """Shorthand: this check could not run, and here is why."""
    return Value(None, state="unavailable", detail=detail)


def gated(gate: str, detail: str = "") -> Value:
    """Shorthand: this number would be uninterpretable, and here is the gate."""
    return Value(None, state="gated", gate=gate, detail=detail)


def measured(value: Any, **extra: Any) -> Value:
    """Shorthand: a real number.

    ``nan`` is refused rather than serialized: `json.dumps` emits the
    non-standard literal ``NaN``, which several strict readers reject, and a
    metric whose mask was empty is an ``unavailable``, not a number.
    """
    if isinstance(value, float) and value != value:
        raise LedgerSchemaError(
            "refusing to record NaN as a measured value — an empty mask means "
            "the metric could not run, which is 'unavailable' with a reason"
        )
    return Value(value, extra=extra)
