"""`an bench --compare`: read two ledger rows, and **refuse when they are not comparable**.

Refusing is the feature. Two rows measured on different scenes, at different
resolutions, or on different x264 builds are not "one better and one worse" —
every number in them is **uninterpretable** relative to the other, and a number
reported across incomparable rows is worse than no number at all.

Three things here that are easy to get wrong in a way that still produces a
plausible verdict:

**A single per-metric sign mis-reports one of the two mutations.** The sharpness
family moves in *opposite* directions for AA-off and high-CRF, so the direction
is read per metric *and per mutation*, from the ``under_mutation`` block the row
already carries. Where the optimum is interior (``edge_transition_width`` — under
1 is a staircase, 3+ is soft) there is no "better" direction at all, and the
comparison says ``changed``, never ``regression``. Inventing a band there would
be the silently-widened threshold this whole wave exists to prevent.

**The two sides have opposite comparison rules, and both are measured.**
Render-side rows compare across any machine: zero differing pixels across arm64
macOS, x86-64 Linux and arm64 Linux, across two SwiftShader JIT backends — *at a
pinned Chromium build*, which is why the browser build is itself a render-side
comparability key. Encode-side rows are **machine-scoped**: same ISA and x264
build is byte-identical, a different ISA moves the decoded stream a little, and a
different x264 build moves it by two orders of magnitude. A band wide enough to
absorb that would swallow ``flat_field_deviation``'s entire crf18->23 signal
(0.0003 -> 0.0005), so the answer is to refuse, not to widen.

**There is no tolerance, and none is needed.** Every comparison is exact.
Measured: two consecutive `an bench` runs on the same machine produce
**bit-identical** numbers for every metric on all six scenes, and identical
`source_pixels_sha256` — the render is pinned and the encode is pinned, so a
delta of exactly zero is the normal case and any nonzero delta is a real change.
An epsilon here would only hide small real movements.

A fourth, quieter one: **a metric's own declaration is a comparability key**.
If ``family`` or ``optimum`` changed between the two rows, the metric means
something different in each and the comparison is refused for that metric alone
— which is why every row carries its full ``metric_declarations`` block rather
than referencing the registry that happened to be installed.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from an.bench.ledger import SCHEMA_VERSION
from an.bench.registry import MUTATION_TOUCHES

#: Row schema versions this comparer understands. A row it cannot read is
#: refused rather than guessed at — the whole point of the version field.
SUPPORTED_SCHEMA_VERSIONS: tuple[int, ...] = (SCHEMA_VERSION,)

#: Scene-provenance fields that must match for ANY metric to be comparable.
#: Deliberately not the whole provenance block: that also carries per-run
#: *measurements* (mask pixel counts, `wall_seconds`, the golden diagnostics),
#: which change exactly when the render changes — i.e. when the two rows are
#: most worth comparing.
SCENE_KEYS: tuple[str, ...] = (
    "scene_contract_sha256",
    "resolution",
    "fps",
    "n_frames",
    "shot_order",
    "palette_hex",
    "tolerances",
)

#: Mask **parameters**, addressed by path into the scene's `masks` block. The
#: counts and fractions beside them are measurements and are excluded.
MASK_PARAM_PATHS: tuple[tuple[str, ...], ...] = (
    ("masks", "edge", "operator"),
    ("masks", "edge", "threshold"),
    ("masks", "flat", "operator"),
    ("masks", "flat", "dilate_k"),
    ("masks", "held", "operator"),
    ("masks", "ring", "operator"),
    ("masks", "render_edge", "operator"),
    ("masks", "render_edge", "threshold"),
)

#: Row-provenance paths that must match for a **render-side** metric.
#: The Chromium build is here and not merely informational: the cross-arch
#: verdict measured ISA- and OS-invariance *at a pinned build*, and an#38's
#: golden path keys on the build for the same reason. One bump has been measured
#: to move zero pixels (1187 -> 1223), so this refusal is precautionary rather
#: than a known break — and a deliberate re-bless is the intended response.
RENDER_ENV_PATHS: tuple[tuple[str, ...], ...] = (
    ("environment", "render_side", "chromium_build"),
    ("environment", "render_side", "playwright"),
    ("environment", "render_side", "launch_argv"),
)

#: Row-provenance paths that must match for an **encode-side** metric.
ENCODE_ENV_PATHS: tuple[tuple[str, ...], ...] = (
    ("environment", "encode_side", "isa"),
    ("environment", "encode_side", "x264_sei"),
    ("environment", "encode_side", "x264_argv"),
    ("environment", "encode_side", "pix_fmt"),
    ("encode_command_source",),
    ("decode_commands",),
)

#: Row-provenance paths that must match for **either** side.
COMMON_ENV_PATHS: tuple[tuple[str, ...], ...] = (("render_kwargs",),)

#: Per-metric declaration fields that must agree, or the metric means something
#: different in each row. `optimum` decides which way "better" points and
#: `family` decides what an#41's criterion counts.
DECLARATION_KEYS: tuple[str, ...] = ("family", "side", "optimum", "unit")

#: How many distinct causal families must move as declared for a mutation to
#: count as caught. an#41's criterion, restated from the research: ">=3 metrics
#: from >=3 distinct causal families, evaluated per mutation, with a per-metric
#: per-mutation sign declared in advance".
REQUIRED_FAMILIES: int = 3


class ComparisonError(ValueError):
    """The comparer was handed something it cannot read at all."""


#: Returned by :func:`_probe` when a path is not present at all. NOT ``None``:
#: a field that is absent and a field whose value is null are different facts,
#: and the whole of this module turns on that distinction.
_ABSENT = object()


def _probe(document: dict, path: tuple[str, ...]) -> Any:
    """The value at ``path``, or ``_ABSENT`` if any step is missing."""
    node: Any = document
    for step in path:
        if not isinstance(node, dict) or step not in node:
            return _ABSENT
        node = node[step]
    return node


def direction_of(before: Any, after: Any) -> str:
    """``increase`` / ``decrease`` / ``no_change`` — exactly, with no tolerance.

    Two consecutive runs on one machine are bit-identical, so zero is the normal
    delta and any nonzero one is real. Booleans compare as booleans: a tripwire
    that went ``True -> False`` has not "decreased".

    >>> direction_of(1.0, 1.0)
    'no_change'
    >>> direction_of(True, False)
    'decrease'
    """
    if before == after:
        return "no_change"
    return "increase" if after > before else "decrease"


def _delta(before: Any, after: Any) -> Any:
    if isinstance(before, bool) or isinstance(after, bool):
        return None
    if isinstance(before, (int, float)) and isinstance(after, (int, float)):
        return after - before
    return None


def _compare_keys(
    before: dict, after: dict, *, keys: tuple[str, ...] = (), paths: tuple = ()
) -> tuple[list[dict], list[dict]]:
    """``(mismatches, caveats)`` over a set of comparability keys.

    **A key absent from one row is unknown, not different.** The ledger grows
    additively — an#38 added `shot_order` and `shot_contract_sha256` to scene
    provenance without bumping `SCHEMA_VERSION`, correctly, because an older row
    stays interpretable. Treating an absent key as a mismatch would make every
    future field retroactively destroy comparability with every row already
    written, which is the opposite of why each row carries its own declarations.

    So absence is recorded as a **caveat** and surfaced, while
    present-and-different is a refusal. `schema_version` is what guards a
    genuinely unreadable row, and it is checked before any of this.
    """
    mismatches: list[dict] = []
    caveats: list[dict] = []
    unknown: list[str] = []
    probes = [((key,), key) for key in keys] + [
        (path, ".".join(path)) for path in paths
    ]
    for path, label in probes:
        b, a = _probe(before, path), _probe(after, path)
        if b is _ABSENT and a is _ABSENT:
            # Absent from BOTH rows is not a caveat about one of them — it is a
            # key neither row has ever carried, which says nothing about their
            # comparability. Reporting it as "absent from before" also leaked
            # the sentinel object into `value`, which made the whole report
            # un-serialisable and crashed `an bench-compare --raw` on any pair
            # of rows predating a key. Live on the committed rows today.
            unknown.append(label)
        elif b is _ABSENT or a is _ABSENT:
            caveats.append(
                {
                    "key": label,
                    "absent_from": "before" if b is _ABSENT else "after",
                    "value": a if b is _ABSENT else b,
                }
            )
        elif b != a:
            mismatches.append({"key": label, "before": b, "after": a})
    if unknown:
        caveats.append(
            {"key": ", ".join(unknown), "absent_from": "both rows", "value": None}
        )
    return mismatches, caveats


#: Every field a row stores TWICE — inline on the metric and in
#: `metric_declarations` — except `under_mutation`, whose nested shape gets
#: `_prediction_disagreements` instead. Both copies are written from one
#: registry at one moment, so a disagreement means the row was edited, and
#: `compare` reads the INLINE one.
#:
#: Three defects of this exact class were found in one review pass, each on a
#: field that had been left out: `family` (moved a witness between families and
#: took `criterion_met_on` from three scenes to five), `under_mutation` (flipped
#: `contrary` to `as_declared`), and `comparison_scope` (compared an encode-side
#: metric across a different ISA). The list is therefore checked for
#: COMPLETENESS by a test rather than maintained by hand — see
#: `tests/test_bench_compare.py::test_every_doubly_stored_field_is_cross_checked`.
CROSS_CHECKED_FIELDS: tuple[str, ...] = (
    "family",
    "side",
    "comparison_scope",
    "reference",
)

#: The fields of a per-mutation prediction that the verdict actually reads.
#: `reason` is prose and is deliberately NOT here — the declarations block
#: carries it and the inline block drops it, so requiring it would refuse every
#: real row.
SCORING_FIELDS: tuple[str, ...] = ("expect", "counts", "gate", "state")


def _prediction_disagreements(
    label: str, inline: dict | None, declared: dict | None
) -> list[dict]:
    """Where a metric's inline prediction disagrees with its own declaration.

    Two copies of one fact, written at the same moment by the same registry, so
    a disagreement on a scoring field means the row was edited.

    >>> _prediction_disagreements("after", {"m": {"expect": "increase"}},
    ...                           {"m": {"expect": "decrease"}})
    [{'key': 'after.under_mutation.m.expect', 'before': 'decrease', 'after': 'increase'}]

    Prose-only differences are not disagreements:

    >>> _prediction_disagreements("after", {"m": {"expect": "increase"}},
    ...                           {"m": {"expect": "increase", "reason": "why"}})
    []
    """
    if not isinstance(inline, dict) or not isinstance(declared, dict):
        return []
    out: list[dict] = []
    for mutation in sorted(set(inline) & set(declared)):
        a, b = inline[mutation], declared[mutation]
        if not isinstance(a, dict) or not isinstance(b, dict):
            continue
        for f in SCORING_FIELDS:
            if f in a and f in b and a[f] != b[f]:
                out.append(
                    {
                        "key": f"{label}.under_mutation.{mutation}.{f}",
                        "before": b[f],
                        "after": a[f],
                    }
                )
    return out


def _verdict_under_mutation(prediction: dict, direction: str) -> str:
    """What one metric's movement means under a declared prediction.

    Five outcomes rather than two, and the extra three are the interesting part:

    - ``as_declared`` — it moved the way the table said it would.
    - ``contrary`` — it moved the OTHER way. Evidence against the prediction.
    - ``did_not_move`` — it was predicted to move and did not. A different
      diagnosis from ``contrary``, and conflating them costs the reader the one
      distinction that matters here: "the lever pushed this the wrong way" and
      "the lever never reached this at all" call for opposite next steps.
    - ``gated`` — the prediction is null because the reference moves with the
      mutation, so the delta is uninterpretable rather than good or bad.
    - ``unexpected_movement`` — a metric declared ``not_applicable`` or
      ``no_change`` **moved**. News, not a pass: a pre-encode statistic moving
      under an encoder mutation means the render changed too, and the mutation
      was not the only variable.

    >>> _verdict_under_mutation({"expect": "increase"}, "no_change")
    'did_not_move'
    >>> _verdict_under_mutation({"expect": "increase"}, "decrease")
    'contrary'
    """
    if not prediction:
        # Distinct from `gated`. A gate is a DECLARED "we cannot tell"; an empty
        # block is "nobody wrote a prediction down", which is a fact about the
        # row rather than about the measurement. Both fail closed, but they send
        # a reader to different places.
        return "no_prediction"
    expect = prediction.get("expect")
    if expect is None:
        return "gated"
    if expect in ("not_applicable", "no_change"):
        return "as_declared" if direction == "no_change" else "unexpected_movement"
    if direction == expect:
        return "as_declared"
    return "did_not_move" if direction == "no_change" else "contrary"


def _verdict_by_optimum(optimum: dict, direction: str) -> str:
    """What one metric's movement means with no mutation declared.

    ``interior`` and ``guard`` optima report ``changed`` and never
    ``regression``: ``edge_transition_width``'s optimum is interior — under 1 is
    a jagged staircase and 3+ is a soft picture — so neither direction is
    "worse" without a target value, and this row carries none. Manufacturing one
    from the baseline is how a comparison starts asserting more than it knows.
    """
    if direction == "no_change":
        return "unchanged"
    if optimum.get("kind") != "one_sided":
        return "changed"
    if optimum.get("expect") == "minimize":
        return "regression" if direction == "increase" else "improvement"
    if optimum.get("expect") == "maximize":
        return "regression" if direction == "decrease" else "improvement"
    return "changed"


def _compare_scene(
    before: dict, after: dict, *, mutation: str | None, env_refusals: dict
) -> dict:
    """One scene's verdict, or the reason it has none."""
    scene_refusals, scene_caveats = _compare_keys(
        before["provenance"],
        after["provenance"],
        keys=SCENE_KEYS,
        paths=MASK_PARAM_PATHS,
    )
    if scene_refusals:
        return {
            "comparable": False,
            "refusals": scene_refusals,
            "detail": (
                "the two rows measured different things. Every metric in them is "
                "uninterpretable relative to the other — not better and not worse."
            ),
            "caveats": scene_caveats,
            "metrics": {},
        }

    metrics: dict[str, dict] = {}
    for key in sorted(set(before["metrics"]) | set(after["metrics"])):
        row_b = before["metrics"].get(key)
        row_a = after["metrics"].get(key)
        if row_b is None or row_a is None:
            metrics[key] = {
                "state": "refused",
                "refusal": "metric_absent",
                "detail": f"declared by the {'after' if row_b is None else 'before'} row only",
            }
            continue
        entry: dict[str, Any] = {
            "side": row_a.get("side"),
            "family": row_a.get("family"),
            "comparison_scope": row_a.get("comparison_scope"),
        }
        declaration, _ = _compare_keys(
            before["declarations"].get(key, {}),
            after["declarations"].get(key, {}),
            keys=DECLARATION_KEYS,
        )
        # The inline row and the row's own declarations block must agree. They
        # are two copies of the same fact, written at the same moment by the
        # same registry — so a disagreement means the row was edited, and the
        # criterion reads the INLINE one. Relabelling one metric's inline
        # `family` moved a witness into a new family and took `criterion_met_on`
        # from three scenes to five, with no refusal anywhere (an#41 review).
        # `comparison_scope` is the most load-bearing of the five and was the
        # last to be checked: it decides whether a metric may be compared
        # ACROSS MACHINES at all, and `compare` reads it from the after row's
        # inline block. Editing that one word from "machine" to "any_machine"
        # made an encode-side metric compare across a different ISA with no
        # refusal — defeating, from inside the row, the single invariant this
        # module exists to hold (an#41 review, defect 19).
        for field in CROSS_CHECKED_FIELDS:
            for label, row, declared in (
                ("before", row_b, before["declarations"].get(key, {})),
                ("after", row_a, after["declarations"].get(key, {})),
            ):
                if field in row and field in declared and row[field] != declared[field]:
                    declaration.append(
                        {
                            "key": f"{label}.{field} (inline vs declared)",
                            "before": declared[field],
                            "after": row[field],
                        }
                    )
        # The prediction gets its own check, and it matters more than the two
        # above: it is what the an#41 criterion is scored against, it is read
        # from the inline block of the AFTER row only, and flipping one
        # `expect` turns `contrary` into `as_declared` with nothing else in the
        # report moving. The cheapest possible way to fake a caught mutation.
        #
        # Compared field by field rather than as a whole dict, because the two
        # copies legitimately differ: the declarations block carries `reason`
        # and the inline block drops it to keep rows small. Whole-dict equality
        # refused 30 of 30 metrics on the two REAL committed rows — which is
        # how this shape was found, and why a guard is checked against real
        # data and not only against a fixture.
        for label, row, declared in (
            ("before", row_b, before["declarations"].get(key, {})),
            ("after", row_a, after["declarations"].get(key, {})),
        ):
            declaration.extend(
                _prediction_disagreements(
                    label, row.get("under_mutation"), declared.get("under_mutation")
                )
            )
        if declaration:
            entry.update(
                state="refused",
                refusal="declaration_changed",
                mismatches=declaration,
                detail=(
                    "the metric's own declaration moved, so it means something "
                    "different in each row"
                ),
            )
            metrics[key] = entry
            continue
        scope = row_a.get("comparison_scope")
        if scope not in env_refusals:
            # An unknown or ABSENT scope must not read as "no refusals apply".
            # It did: deleting the field let an encode-side metric from another
            # ISA and another x264 build compare cleanly and report a
            # regression. Every neighbouring absence in this module is a
            # surfaced caveat; this one was silently "compare anyway".
            entry.update(
                state="refused",
                refusal="comparison_scope_unknown",
                detail=(
                    f"the metric declares comparison_scope {scope!r}, which is "
                    f"not one of {sorted(env_refusals)}. Without it there is no "
                    "way to know which environment differences disqualify this "
                    "number, so it is refused rather than compared."
                ),
            )
            metrics[key] = entry
            continue
        blocking = env_refusals.get(scope) or []
        if blocking:
            entry.update(
                state="refused",
                refusal="environment_differs",
                mismatches=blocking,
                detail=(
                    "encode-side numbers are machine-scoped: a different x264 "
                    "build moves the decoded stream by up to 99.2% of samples, "
                    "and a band wide enough to absorb that would swallow the "
                    "signal being measured"
                    if scope == "machine"
                    else "render-side pixels are ISA- and OS-invariant only at a "
                    "pinned Chromium build; a bump is a deliberate re-bless"
                ),
            )
            metrics[key] = entry
            continue
        if row_b.get("state") != "measured" or row_a.get("state") != "measured":
            # A metric that WAS measured and now is not is a loss of coverage,
            # and it is the transition most worth shouting about: the panel got
            # narrower and every summary line still reads the same. The reverse
            # (a gate that started producing a number) is good news and is
            # reported at the same volume so neither hides.
            entry.update(
                state="not_measured",
                before_state=row_b.get("state"),
                after_state=row_a.get("state"),
                before_gate=row_b.get("gate"),
                after_gate=row_a.get("gate"),
                coverage=(
                    "lost"
                    if row_b.get("state") == "measured"
                    else "gained"
                    if row_a.get("state") == "measured"
                    else "still_absent"
                ),
                detail="at least one side is gated or unavailable, so there is nothing to compare",
            )
            metrics[key] = entry
            continue

        value_b, value_a = row_b["value"], row_a["value"]
        movement = direction_of(value_b, value_a)
        delta = _delta(value_b, value_a)
        entry.update(
            state="compared",
            before=value_b,
            after=value_a,
            delta=delta,
            # Reported because there is deliberately no tolerance band, and
            # without magnitude a 0.0001 wobble on a scene the mutation does not
            # reach reads with the same weight as a 30% move on one it does.
            # `promote_demo`'s `edge_transition_width` goes 5.6368 -> 5.6369
            # under the AA lever, because MSAA applies to WebGL geometry and an
            # SVG sprite is a pre-rasterised texture. That is a real `contrary`
            # and it is also nothing; the number is what says so.
            relative_delta=(
                round(delta / value_b, 6)
                if isinstance(delta, (int, float)) and value_b
                else None
            ),
            direction=movement,
        )
        if mutation is None:
            entry["optimum"] = after["declarations"].get(key, {}).get("optimum", {})
            entry["verdict"] = _verdict_by_optimum(entry["optimum"], movement)
        else:
            prediction = (row_a.get("under_mutation") or {}).get(mutation) or {}
            expect = prediction.get("expect")
            entry["expect"] = expect
            # `Prediction.__post_init__` refuses `counts=True` on a tautology,
            # but a ROW is data and can say anything — and `--compare` reads
            # rows, including hand-edited and foreign ones. Without this, three
            # metrics relabelled `{"expect": "no_change", "counts": true}` make
            # the criterion report MET while nothing moved at all.
            entry["counts"] = bool(prediction.get("counts")) and expect not in (
                None,
                "no_change",
                "not_applicable",
            )
            if bool(prediction.get("counts")) and not entry["counts"]:
                entry["counts_refused"] = (
                    f"the row declares counts=true with expect={expect!r}, which "
                    "can never be a satisfied prediction"
                )
            entry["verdict"] = _verdict_under_mutation(prediction, movement)
        metrics[key] = entry

    block: dict[str, Any] = {
        "comparable": True,
        "refusals": [],
        "caveats": scene_caveats,
        "coverage_lost": sorted(
            k for k, e in metrics.items() if e.get("coverage") == "lost"
        ),
        "coverage_gained": sorted(
            k for k, e in metrics.items() if e.get("coverage") == "gained"
        ),
        "metrics": metrics,
    }
    if mutation is not None:
        families: dict[str, list[str]] = {}
        for key, entry in metrics.items():
            if entry.get("counts") and entry.get("verdict") == "as_declared":
                families.setdefault(entry["family"], []).append(key)
        block["families_satisfied"] = {
            k: sorted(v) for k, v in sorted(families.items())
        }
        block["family_count"] = len(families)
        block["criterion_met"] = len(families) >= REQUIRED_FAMILIES
        block["contrary"] = sorted(
            k for k, e in metrics.items() if e.get("verdict") == "contrary"
        )
        block["did_not_move"] = sorted(
            k for k, e in metrics.items() if e.get("verdict") == "did_not_move"
        )
        block["unexpected_movement"] = sorted(
            k for k, e in metrics.items() if e.get("verdict") == "unexpected_movement"
        )
    else:
        block["regressions"] = sorted(
            k for k, e in metrics.items() if e.get("verdict") == "regression"
        )
        block["improvements"] = sorted(
            k for k, e in metrics.items() if e.get("verdict") == "improvement"
        )
        block["changed"] = sorted(
            k for k, e in metrics.items() if e.get("verdict") == "changed"
        )
    return block


def _scene_view(row: dict, name: str) -> dict:
    """One scene, flattened into what the comparer reads: metrics + tripwires + declarations."""
    block = row["scenes"][name]
    declarations = row.get("metric_declarations") or {}
    return {
        "provenance": block["provenance"],
        "metrics": {**block["metrics"], **block["tripwires"]},
        "declarations": {
            **(declarations.get("metrics") or {}),
            **(declarations.get("tripwires") or {}),
        },
    }


def compare(before: dict, after: dict, *, mutation: str | None = None) -> dict:
    """Compare two ledger rows. Returns a report; raises only on an unreadable row.

    ``mutation`` selects the question. With one, the report answers an#41's:
    did the declared witnesses move in the declared direction, and did at least
    three distinct causal families do so. Without one, it answers "is the second
    row worse", which only the one-sided metrics can answer at all.
    """
    for label, row in (("before", before), ("after", after)):
        version = row.get("schema_version")
        if version not in SUPPORTED_SCHEMA_VERSIONS:
            raise ComparisonError(
                f"the {label} row declares schema_version {version!r}; this "
                f"comparer understands {list(SUPPORTED_SCHEMA_VERSIONS)}. A row it "
                "cannot read is refused rather than guessed at."
            )
    known = set(after.get("mutations") or ()) | set(before.get("mutations") or ())
    if mutation is not None and mutation not in known:
        raise ComparisonError(
            f"neither row declares the mutation {mutation!r}; they declare "
            f"{sorted(known)}"
        )

    common, common_caveats = _compare_keys(
        before["provenance"], after["provenance"], paths=COMMON_ENV_PATHS
    )
    render, render_caveats = _compare_keys(
        before["provenance"], after["provenance"], paths=RENDER_ENV_PATHS
    )
    encode, encode_caveats = _compare_keys(
        before["provenance"], after["provenance"], paths=ENCODE_ENV_PATHS
    )
    env_refusals = {"any_machine": common + render, "machine": common + encode}
    env_caveats = common_caveats + render_caveats + encode_caveats

    # In mutation mode, the knob the lever pulls is the INDEPENDENT VARIABLE, not
    # a reason to refuse. Exempted by declaration, by path AND by the shape of
    # the change (a `-preset` edit is not the CRF lever's) — see
    # `MUTATION_TOUCHES`. And a declared knob that did NOT move is reported: it
    # is the cheapest available evidence that the mutation never applied, which
    # otherwise reads as "the instrument is blind".
    expected_changes: list[dict] = []
    unapplied: list[str] = []
    if mutation is not None:
        touched = {t.label for t in MUTATION_TOUCHES.get(mutation, ())}
        for scope, items in env_refusals.items():
            env_refusals[scope] = [i for i in items if i["key"] not in touched]
        # Probed DIRECTLY, not read off the comparability scan. A lever may
        # declare a knob that is not a comparability key at all — the AA lever's
        # `runtime_sha256` is provenance, because the runtime is the code under
        # test — and such a key is never compared, so it never appeared in the
        # scan and every run reported it as possibly-unapplied.
        #
        # Compared by VALUE rather than by "is this path in the mismatch list",
        # for the same reason: the exemption is for the knob the lever pulls,
        # and `x264_argv` is the whole encode command, so an unrelated flag
        # change inside it rode in on the lever's coat-tails.
        for touch in MUTATION_TOUCHES.get(mutation, ()):
            b = _probe(before["provenance"], touch.path)
            a = _probe(after["provenance"], touch.path)
            if b is _ABSENT or a is _ABSENT:
                unapplied.append(f"{touch.label} (absent from one of the rows)")
            elif b == a:
                unapplied.append(touch.label)
            elif touch.is_the_levers_change(b, a):
                expected_changes.append({"key": touch.label, "before": b, "after": a})
            else:
                # The declared path moved, but NOT in the way this lever moves
                # it — so the exemption does not apply and the difference is a
                # refusal like any other. Put back so the scan below sees it.
                env_refusals["machine"].append(
                    {"key": touch.label, "before": b, "after": a}
                )
                env_refusals["any_machine"].append(
                    {"key": touch.label, "before": b, "after": a}
                )
                unapplied.append(
                    f"{touch.label} (changed, but not the change {mutation!r} makes)"
                )
        unapplied = sorted(unapplied)

    names_b, names_a = set(before["scenes"]), set(after["scenes"])
    scenes = {
        name: _compare_scene(
            _scene_view(before, name),
            _scene_view(after, name),
            mutation=mutation,
            env_refusals=env_refusals,
        )
        for name in sorted(names_b & names_a)
    }
    report: dict[str, Any] = {
        "mutation": mutation,
        "coverage_lost": {
            n: s["coverage_lost"] for n, s in scenes.items() if s.get("coverage_lost")
        },
        # `blessed_scenes` is a CAVEAT, never a comparability key. A `--bless`
        # run WROTE the goldens it would otherwise have compared against, so its
        # family-B rows are gated `blessed_this_run` — and `format_comparison`
        # skips entries whose verdict is `unchanged`, so family B does not
        # appear as "unchanged", it does not appear AT ALL. "Family B agreed"
        # and "family B was never asked" are the same blank space in the table,
        # which is why this key stops being write-only.
        "before": {
            "git": before["provenance"].get("git"),
            "generated_at": before.get("generated_at"),
            "blessed_scenes": sorted(before["provenance"].get("blessed") or ()),
        },
        "after": {
            "git": after["provenance"].get("git"),
            "generated_at": after.get("generated_at"),
            "blessed_scenes": sorted(after["provenance"].get("blessed") or ()),
        },
        "environment_refusals": {k: v for k, v in env_refusals.items() if v},
        "environment_caveats": env_caveats,
        "expected_environment_changes": expected_changes,
        "mutation_may_not_have_applied": unapplied,
        "scenes_only_in_before": sorted(names_b - names_a),
        "scenes_only_in_after": sorted(names_a - names_b),
        "scenes": scenes,
    }
    # Did this comparison produce an ANSWER? Separate from whether the answer
    # was good. `--strict` used to read only `has_regressions` / `criterion_met`,
    # so a run in which every scene was REFUSED — different scene contract,
    # different machine, no shared scene at all — exited 0 while printing
    # "0 regression(s)", a zero this module's own docstring calls worse than no
    # number at all. The CI gate was passing comparisons that compared nothing.
    compared = sum(
        1
        for s in scenes.values()
        if s["comparable"]
        for e in s["metrics"].values()
        if e.get("state") == "compared"
    )
    report["metrics_compared"] = compared
    report["answered"] = bool(scenes) and compared > 0
    if mutation is not None:
        met = [n for n, s in scenes.items() if s.get("criterion_met")]
        report["criterion_met_on"] = sorted(met)
        report["criterion_met"] = bool(met)
    else:
        report["regressions"] = {
            n: s["regressions"] for n, s in scenes.items() if s.get("regressions")
        }
        report["has_regressions"] = bool(report["regressions"])
    return report


def load_row(path: Path | str) -> dict:
    """Read one ledger row from disk."""
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _row_instant(row: Any) -> float | None:
    """A row's ``generated_at`` as a POSIX timestamp, or ``None`` if unreadable.

    ``None`` for four separate reasons — not an object, no ``generated_at``, not
    a string, not an ISO-8601 instant — and deliberately for all four at once:
    none of them is a reason to abort a *listing*. A single malformed file in
    the ledger directory would otherwise crash every bare ``an bench-compare``,
    which is a worse failure than the ordering bug this exists to fix.

    ``build_ledger`` writes UTC with a ``Z`` suffix, which ``fromisoformat``
    only accepts from Python 3.11; this package's floor is 3.10, so the suffix
    is rewritten rather than relied on. A naive stamp is read as UTC, because
    mixing aware and naive datetimes raises on comparison and a sort must not.

    >>> _row_instant({"generated_at": "2026-08-21T19:02:31Z"})
    1787338951.0
    >>> _row_instant({"generated_at": "yesterday"}) is None
    True
    >>> _row_instant({}) is None
    True
    >>> _row_instant("not a row") is None
    True
    """
    if not isinstance(row, dict):
        return None
    stamp = row.get("generated_at")
    if not isinstance(stamp, str):
        return None
    try:
        moment = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.timestamp()


def _generated_at(path: Path) -> float | None:
    """:func:`_row_instant` for a file, with an unreadable file reading ``None``."""
    try:
        return _row_instant(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return None


def latest_rows(*, root: Path | None = None, count: int = 2) -> list[Path]:
    """The most recent committed ledger rows, newest last — **by `generated_at`**.

    Not by filename. Filenames are ``<date>-<sha7>[-dirty].json``, so a filename
    sort orders same-day rows by *sha hex*. On a Wave 3 PR the re-baseline and
    the after-run are plausibly the same day, and when the after-commit's sha
    sorts lower a bare ``an bench-compare`` silently swaps before and after and
    reports every improvement as a regression.

    A row whose ``generated_at`` cannot be read sorts **before** every dated
    one, so it is dropped as soon as two dated rows exist: a corrupt or
    pre-schema file must never become the ``after`` row a verdict is drawn from.
    The filename stays the tiebreak, which is what keeps a directory whose rows
    all lack the key ordered by date.

    ``-dirty`` rows are excluded: a row measured against uncommitted edits
    describes no commit, and comparing one is comparing against nothing
    nameable. `an bench --out` still lets a caller point at one explicitly.
    """
    from an.bench.paths import ledger_dir

    rows = [p for p in ledger_dir(root).glob("*.json") if "-dirty" not in p.name]
    stamps = {p: _generated_at(p) for p in rows}

    def key(path: Path) -> tuple[int, float, str]:
        stamp = stamps[path]
        return (0, 0.0, path.name) if stamp is None else (1, stamp, path.name)

    return sorted(rows, key=key)[-count:]


def format_comparison(report: dict) -> str:
    """The human-readable digest. Refusals first, because they are the verdict."""
    lines: list[str] = []
    mutation = report.get("mutation")
    lines.append(
        f"comparing {(report['before'].get('git') or {}).get('sha', '?')[:7]} -> "
        f"{(report['after'].get('git') or {}).get('sha', '?')[:7]}"
        + (f"  under mutation {mutation!r}" if mutation else "")
    )
    for side_name in ("before", "after"):
        blessed = (report.get(side_name) or {}).get("blessed_scenes") or []
        if blessed:
            lines.append(
                f"  caveat: the {side_name} row came from a --bless run, which "
                f"WROTE the goldens for {blessed} rather than comparing "
                "against them. Family B is gated `blessed_this_run` there, so "
                "it is ABSENT from the table below — not unchanged, never "
                "asked. The row that can fail family B is the unblessed one."
            )
    for scope, refusals in (report.get("environment_refusals") or {}).items():
        side = "render-side" if scope == "any_machine" else "encode-side"
        lines.append(f"  REFUSED for every {side} metric — the environment differs:")
        for item in refusals:
            lines.append(f"    {item['key']}: {item['before']!r} -> {item['after']!r}")
    for item in report.get("expected_environment_changes") or []:
        lines.append(
            f"  the lever moved {item['key']} — expected, and exempt from the "
            f"refusal ({item['before']!r} -> {item['after']!r})"
        )
    for key in report.get("mutation_may_not_have_applied") or []:
        lines.append(
            f"  WARNING: {mutation!r} declares that it changes {key}, and it did "
            "not. The mutation may never have applied, which reads exactly like "
            "an instrument that cannot see it."
        )
    for item in report.get("environment_caveats") or []:
        lines.append(
            f"  caveat: {item['key']} is absent from the {item['absent_from']} row "
            f"(the other says {item['value']!r}) — unknown, not different"
        )
    for name in report.get("scenes_only_in_before") or []:
        lines.append(f"  {name}: in the before row only")
    for name in report.get("scenes_only_in_after") or []:
        lines.append(f"  {name}: in the after row only")

    for name, scene in sorted(report["scenes"].items()):
        for item in scene.get("caveats") or []:
            lines.append(
                f"  {name}: caveat — {item['key']} is absent from the "
                f"{item['absent_from']} row (the other says {item['value']!r})"
            )
        if not scene["comparable"]:
            lines.append(f"{name}  REFUSED — {scene['detail']}")
            for item in scene["refusals"]:
                lines.append(
                    f"    {item['key']}: {item['before']!r} -> {item['after']!r}"
                )
            continue
        if mutation is not None:
            mark = "MET" if scene["criterion_met"] else "NOT MET"
            lines.append(
                f"{name}  criterion {mark}: {scene['family_count']}/"
                f"{REQUIRED_FAMILIES} families — {scene['families_satisfied']}"
            )
        else:
            lines.append(
                f"{name}  {len(scene['regressions'])} regression(s), "
                f"{len(scene['improvements'])} improvement(s), "
                f"{len(scene['changed'])} change(s) with no better direction"
            )
        for key, entry in sorted(scene["metrics"].items()):
            state = entry.get("state")
            if state == "compared":
                if entry["direction"] == "no_change" and entry.get("verdict") in (
                    "unchanged",
                    "as_declared",
                ):
                    continue
                rel = entry.get("relative_delta")
                magnitude = f"{rel:+.1%}" if isinstance(rel, float) else ""
                lines.append(
                    f"    [{entry['side'][:3]}/{entry['family']}] {key:32s} "
                    f"{entry['before']} -> {entry['after']}  {magnitude:>9s}  "
                    f"{entry['verdict']}"
                    + (f" (expected {entry['expect']})" if entry.get("expect") else "")
                )
            elif state == "refused":
                lines.append(f"    [refused] {key:32s} {entry['refusal']}")
            elif entry.get("coverage") in ("lost", "gained"):
                arrow = (
                    f"{entry['before_state']}({entry.get('before_gate') or ''}) -> "
                    f"{entry['after_state']}({entry.get('after_gate') or ''})"
                )
                mark = (
                    "COVERAGE LOST"
                    if entry["coverage"] == "lost"
                    else "coverage gained"
                )
                lines.append(f"    [{mark}] {key:32s} {arrow}")
    if not report.get("answered"):
        lines.append(
            "\nNO ANSWER: not one metric was compared. Every scene was refused, "
            "or the two rows share no scene at all — which is a fact about the "
            "rows and not about the code they measure."
        )
    if mutation is not None:
        lines.append(f"\ncriterion met on: {report['criterion_met_on'] or 'NO SCENE'}")
    elif report.get("has_regressions"):
        lines.append(f"\nregressions: {report['regressions']}")
    lines.append(f"metrics compared: {report.get('metrics_compared')}")
    if report.get("coverage_lost"):
        lines.append(f"COVERAGE LOST: {report['coverage_lost']}")
    return "\n".join(lines)
