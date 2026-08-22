"""`an bench --compare` (an#40): what it refuses, and what it dares to conclude.

Every test here runs in the **default** CI leg. The rows are built from
`an.bench.ledger`'s own constructors rather than rendered, which is the point:
the comparer's job is to read a row, and the bugs live in *which differences it
treats as which kind of fact* — none of which needs a browser or ffmpeg.

Each test names the one-line production mutation it exists to catch.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from an.bench.compare import (
    DECLARATION_KEYS,
    ENCODE_ENV_PATHS,
    MASK_PARAM_PATHS,
    REQUIRED_FAMILIES,
    RENDER_ENV_PATHS,
    SCENE_KEYS,
    ComparisonError,
    compare,
    direction_of,
    format_comparison,
    latest_rows,
    load_row,
)
from an.bench.ledger import build_ledger, build_scene_block, gated, measured
from an.bench.registry import METRICS, MUTATIONS, TRIPWIRES

#: The repo root, so a guard can be checked against the committed ledger
#: rows rather than only against a fixture built to agree with it.
REPO_ROOT = Path(__file__).resolve().parents[1]


def _declare(row: dict, key: str, mutation: str, prediction: dict) -> None:
    """Write a per-mutation prediction into BOTH copies the row carries.

    A row holds each prediction twice — inline on the metric and in
    `metric_declarations` — and `build_scene_block` writes both from one
    registry at one moment, so a real row's copies always agree. A test that
    edits only the inline copy builds a row that cannot exist, and since the
    review added a cross-check between them (an edited inline `expect` is the
    cheapest way to fake a caught mutation) such a row is refused before it
    reaches the behaviour under test.
    """
    row["scenes"]["s"]["metrics"][key]["under_mutation"][mutation] = dict(prediction)
    declared = row["metric_declarations"]
    for section in ("metrics", "tripwires"):
        block = (declared.get(section) or {}).get(key)
        if block is not None:
            block.setdefault("under_mutation", {})[mutation] = dict(prediction)


_PROVENANCE = {
    "scene_contract_sha256": "a" * 64,
    "resolution": [320, 240],
    "fps": 24,
    "n_frames": 12,
    "shot_order": ["only"],
    "palette_hex": ["#000000", "#ffffff"],
    "tolerances": {"edge_flat_tol": 4},
    "masks": {
        "edge": {"operator": "edge-op", "threshold": 40, "edge_px": 100},
        "flat": {"operator": "flat-op", "dilate_k": 3, "flat_px": 900},
        "held": {"operator": "held-op", "pairs": 11},
        "ring": {"operator": "ring-op", "ring_px": 50},
    },
}

_ROW_PROVENANCE = {
    "git": {"sha": "0" * 40, "branch": "main", "dirty": False},
    "render_kwargs": {"parallel": 1},
    "encode_command_source": "an.adapters.cutout.render._ffmpeg_mux",
    "decode_commands": {"source_rgb": ["ffmpeg", "-i", "<frames>"]},
    "environment": {
        "render_side": {
            "chromium_build": "140.0.7339.16",
            "playwright": "1.55.0",
            "launch_argv": ["--disable-gpu"],
        },
        "encode_side": {
            "isa": "arm64",
            "x264_sei": "core 165 r3222 abc",
            "x264_argv": ["-crf", "23"],
        },
    },
}


def _row(
    values: dict | None = None,
    *,
    scene: str = "s",
    provenance: dict | None = None,
    row_provenance: dict | None = None,
) -> dict:
    """One valid ledger row, with every declared metric measured at 1.0 by default."""
    numbers = {key: 1.0 for key in METRICS}
    numbers.update(values or {})
    metrics = {
        key: value if hasattr(value, "state") else measured(value)
        for key, value in numbers.items()
    }
    tripwires = {key: measured(True) for key in TRIPWIRES}
    block = build_scene_block(
        provenance=copy.deepcopy(provenance or _PROVENANCE),
        metrics=metrics,
        tripwires=tripwires,
    )
    return build_ledger(
        provenance=copy.deepcopy(row_provenance or _ROW_PROVENANCE),
        scenes={scene: block},
    )


def _scope_of(key: str) -> str:
    return "machine" if METRICS[key].side == "encode" else "any_machine"


def _one(side: str) -> str:
    return next(k for k, spec in METRICS.items() if spec.side == side)


# ------------------------------------------------------------------ direction


def test_a_direction_is_exact_with_no_tolerance():
    """MUTATION: in `direction_of`, `if before == after` -> `if abs(a - b) < 1e-9`.

    Measured: two consecutive `an bench` runs on one machine produce
    bit-identical numbers for every metric on all six scenes, so zero is the
    normal delta and any nonzero one is real. An epsilon would only hide small
    true movements — and it is exactly the silently-widened threshold this wave
    exists to prevent.
    """
    assert direction_of(1.0, 1.0) == "no_change"
    assert direction_of(1.0, 1.0 + 1e-12) == "increase"
    assert direction_of(1.0, 1.0 - 1e-12) == "decrease"


def test_a_boolean_tripwire_compares_as_a_boolean():
    """MUTATION: in `_delta`, drop the `isinstance(..., bool)` short-circuit.

    `True - False` is `1` in Python, and reporting a tripwire's "delta" as 1
    invites it to be read as a measurement — which is the one thing the
    metrics/tripwires split exists to prevent.
    """
    before, after = _row(), _row()
    after["scenes"]["s"]["tripwires"]["golden_identity"] = {
        **after["scenes"]["s"]["tripwires"]["golden_identity"],
        "value": False,
    }
    entry = compare(before, after)["scenes"]["s"]["metrics"]["golden_identity"]
    assert entry["direction"] == "decrease"
    assert entry["delta"] is None


# ------------------------------------------------------------------- refusals


def test_the_comparability_key_tables_are_pinned_by_literal():
    """MUTATION: remove any entry from any of the four key tables.

    The parametrised tests below iterate over these constants, so dropping an
    entry silently drops its own test case — the "a guard that asserts data
    passes for any data" shape the an#36 sweep lost two mutants to. Spelled out
    here so a deletion has to be deliberate, and so the reason each key is a
    comparability key is written next to it.
    """
    assert SCENE_KEYS == (
        "scene_contract_sha256",  # a different scene entirely
        "resolution",  # every ratio-form metric means something else
        "fps",  # the frame a pinned time names moves
        "n_frames",  # the sequences are different lengths
        "shot_order",  # the concat, and therefore the pairing, differs
        "palette_hex",  # off_palette_pixel_fraction's denominator moved
        "tolerances",  # the thresholds inside the numbers moved
    )
    assert MASK_PARAM_PATHS == (
        ("masks", "edge", "operator"),
        ("masks", "edge", "threshold"),
        ("masks", "flat", "operator"),
        ("masks", "flat", "dilate_k"),
        ("masks", "held", "operator"),
        ("masks", "ring", "operator"),
    )
    assert RENDER_ENV_PATHS == (
        ("environment", "render_side", "chromium_build"),
        ("environment", "render_side", "playwright"),
        ("environment", "render_side", "launch_argv"),
    )
    assert ENCODE_ENV_PATHS == (
        ("environment", "encode_side", "isa"),
        ("environment", "encode_side", "x264_sei"),
        ("environment", "encode_side", "x264_argv"),
        ("encode_command_source",),
        ("decode_commands",),
    )
    assert DECLARATION_KEYS == ("family", "side", "optimum", "unit")


@pytest.mark.parametrize("key", SCENE_KEYS)
def test_a_scene_whose_provenance_moved_is_refused_by_name(key):
    """MUTATION: remove any entry from `SCENE_KEYS`.

    Two rows measured on different scenes are not one better and one worse —
    every number in them is uninterpretable relative to the other. The refusal
    names the key so a reader can act on it.
    """
    changed = copy.deepcopy(_PROVENANCE)
    changed[key] = "moved" if isinstance(changed[key], str) else [999]
    report = compare(_row(), _row(provenance=changed))
    scene = report["scenes"]["s"]
    assert scene["comparable"] is False
    assert [item["key"] for item in scene["refusals"]] == [key]
    assert key in format_comparison(report)


@pytest.mark.parametrize("path", MASK_PARAM_PATHS)
def test_a_scene_whose_mask_parameters_moved_is_refused(path):
    """MUTATION: remove any entry from `MASK_PARAM_PATHS`.

    Every encode-side metric is computed over these masks, so a threshold change
    makes the numbers mean something else — with no visible sign that they do.
    """
    changed = copy.deepcopy(_PROVENANCE)
    node = changed
    for step in path[:-1]:
        node = node[step]
    node[path[-1]] = "moved"
    report = compare(_row(), _row(provenance=changed))
    assert report["scenes"]["s"]["comparable"] is False
    assert [i["key"] for i in report["scenes"]["s"]["refusals"]] == [".".join(path)]


def test_a_mask_measurement_is_not_a_comparability_key():
    """MUTATION: compare the whole `masks` block instead of its parameter paths.

    `edge_px` and `flat_px` are per-run MEASUREMENTS: they change precisely when
    the render changes, which is when the two rows are most worth comparing.
    Comparing the block wholesale makes every interesting pair uncomparable.
    """
    changed = copy.deepcopy(_PROVENANCE)
    changed["masks"]["edge"]["edge_px"] = 999999
    changed["masks"]["flat"]["flat_px"] = 1
    assert (
        compare(_row(), _row(provenance=changed))["scenes"]["s"]["comparable"] is True
    )


def test_an_absent_key_is_a_caveat_and_not_a_refusal():
    """MUTATION: in `_compare_keys`, treat `_ABSENT` as a mismatch.

    The ledger grows additively — an#38 added `shot_order` to scene provenance
    without bumping `SCHEMA_VERSION`, correctly, because an older row stays
    interpretable. Refusing on an absent key would make every future field
    retroactively destroy comparability with every row already written, which is
    the opposite of why each row carries its own declarations. It is not
    hypothetical: it is what the two committed rows in this repo actually differ
    by.
    """
    trimmed = copy.deepcopy(_PROVENANCE)
    del trimmed["shot_order"]
    report = compare(_row(provenance=trimmed), _row())
    scene = report["scenes"]["s"]
    assert scene["comparable"] is True
    assert [c["key"] for c in scene["caveats"]] == ["shot_order"]
    assert scene["caveats"][0]["absent_from"] == "before"
    assert "caveat" in format_comparison(report)


def test_an_unreadable_schema_version_is_refused_rather_than_guessed():
    """MUTATION: drop the `SUPPORTED_SCHEMA_VERSIONS` check.

    The version field's whole purpose is to let a reader refuse. Guessing at a
    row it does not understand produces a verdict nobody can trace.
    """
    future = _row()
    future["schema_version"] = 99
    with pytest.raises(ComparisonError, match="schema_version 99"):
        compare(_row(), future)


def test_an_undeclared_mutation_is_refused():
    """MUTATION: drop the membership check on `mutations`.

    Without it, an unknown name silently produces empty predictions for every
    metric and the criterion reports 0/3 as though the instrument were blind.
    """
    with pytest.raises(ComparisonError, match="neither row declares"):
        compare(_row(), _row(), mutation="not_a_lever")


# --------------------------------------------- the two sides, scoped opposite


@pytest.mark.parametrize("path", ENCODE_ENV_PATHS)
def test_an_encode_environment_change_refuses_only_the_encode_side(path):
    """MUTATION: point `ENCODE_ENV_PATHS`'s refusal at every metric.

    Encode-side rows are machine-scoped: a different x264 build moves the
    decoded stream by up to 99.2% of samples, and a band wide enough to absorb
    that would swallow `flat_field_deviation`'s entire crf18->23 signal. But
    render-side pixels were measured ISA- and OS-invariant, so refusing them on
    the same evidence throws away the half of the panel that CAN be compared.
    """
    changed = copy.deepcopy(_ROW_PROVENANCE)
    node = changed
    for step in path[:-1]:
        node = node[step]
    node[path[-1]] = "moved"
    metrics = compare(_row(), _row(row_provenance=changed))["scenes"]["s"]["metrics"]
    assert metrics[_one("encode")]["refusal"] == "environment_differs"
    assert metrics[_one("render")]["state"] == "compared", (
        "render-side pixels are ISA- and OS-invariant; an encoder change cannot "
        "reach them"
    )


@pytest.mark.parametrize("path", RENDER_ENV_PATHS)
def test_a_browser_change_refuses_only_the_render_side(path):
    """MUTATION: drop `RENDER_ENV_PATHS` entirely.

    The cross-arch verdict measured invariance *at a pinned Chromium build*, and
    an#38's golden path keys on the build for the same reason. A bump is a
    deliberate re-bless, not a silent comparison.
    """
    changed = copy.deepcopy(_ROW_PROVENANCE)
    node = changed
    for step in path[:-1]:
        node = node[step]
    node[path[-1]] = "moved"
    metrics = compare(_row(), _row(row_provenance=changed))["scenes"]["s"]["metrics"]
    assert metrics[_one("render")]["refusal"] == "environment_differs"
    assert metrics[_one("encode")]["state"] == "compared"


def test_a_render_knob_change_refuses_both_sides():
    """MUTATION: move `render_kwargs` out of `COMMON_ENV_PATHS` into one side.

    `parallel` and `strict_assets` change what was rendered, so they reach every
    metric on both sides of the encoder.
    """
    changed = copy.deepcopy(_ROW_PROVENANCE)
    changed["render_kwargs"] = {"parallel": 4}
    metrics = compare(_row(), _row(row_provenance=changed))["scenes"]["s"]["metrics"]
    for side in ("render", "encode"):
        assert metrics[_one(side)]["refusal"] == "environment_differs"


@pytest.mark.parametrize("key", DECLARATION_KEYS)
def test_a_metric_whose_own_declaration_moved_is_refused_alone(key):
    """MUTATION: remove any entry from `DECLARATION_KEYS`.

    If `family` or `optimum` changed, the metric means something different in
    each row — but the rest of the panel is still comparable, so the refusal is
    per metric rather than per scene. This is why each row carries its full
    `metric_declarations` block instead of trusting the installed registry.
    """
    victim = _one("encode")
    before = _row()
    before["metric_declarations"]["metrics"][victim][key] = "was-different"
    metrics = compare(before, _row())["scenes"]["s"]["metrics"]
    assert metrics[victim]["refusal"] == "declaration_changed"
    assert metrics[_one("render")]["state"] == "compared"


def test_a_metric_present_in_one_row_only_is_refused_alone():
    """MUTATION: skip the `row_b is None or row_a is None` branch.

    Reading a missing metric as an unchanged one would report a narrower panel
    as a clean comparison.
    """
    after = _row()
    victim = _one("encode")
    del after["scenes"]["s"]["metrics"][victim]
    entry = compare(_row(), after)["scenes"]["s"]["metrics"][victim]
    assert entry["refusal"] == "metric_absent"


# --------------------------------------------------------- coverage and state


def test_a_metric_that_stopped_being_measured_is_reported_as_lost_coverage():
    """MUTATION: drop the `coverage` field from the `not_measured` branch.

    A metric that went from a number to a gate makes the panel narrower while
    every summary line still reads the same. That is the shape of an instrument
    quietly going blind, and it must be as loud as a regression.
    """
    after = _row()
    victim = _one("encode")
    after["scenes"]["s"]["metrics"][victim] = {
        **after["scenes"]["s"]["metrics"][victim],
        "state": "gated",
        "value": None,
        "gate": "reference_moved",
    }
    report = compare(_row(), after)
    assert report["coverage_lost"] == {"s": [victim]}
    assert "COVERAGE LOST" in format_comparison(report)


def test_a_gate_that_started_producing_a_number_is_reported_as_gained():
    """MUTATION: report every state transition as `lost`."""
    before = _row()
    victim = _one("encode")
    before["scenes"]["s"]["metrics"][victim] = {
        **before["scenes"]["s"]["metrics"][victim],
        "state": "gated",
        "value": None,
        "gate": "reference_moved",
    }
    report = compare(before, _row())
    assert report["coverage_lost"] == {}
    assert report["scenes"]["s"]["coverage_gained"] == [victim]


# ------------------------------------------------------------ mutation verdicts


@pytest.mark.parametrize("mutation", MUTATIONS)
def test_two_identical_rows_move_nothing_under_either_mutation(mutation):
    """MUTATION: in `direction_of`, return `increase` when the values are equal.

    The instrument must be silent when nothing happened, or every real movement
    arrives inside noise.
    """
    report = compare(_row(), _row(), mutation=mutation)
    scene = report["scenes"]["s"]
    assert scene["family_count"] == 0
    # `did_not_move`, NOT `contrary`: nothing moved the wrong way, because
    # nothing moved. The two are separate verdicts precisely so this case reads
    # as "the lever never reached these" rather than as evidence against the
    # table.
    assert scene["contrary"] == []
    for entry in scene["metrics"].values():
        assert entry.get("direction") in (None, "no_change")
        assert entry.get("verdict") not in ("as_declared", "contrary") or entry.get(
            "expect"
        ) in ("no_change", "not_applicable")


def test_a_witness_that_moved_as_declared_satisfies_its_family():
    """MUTATION: in `_verdict_under_mutation`, `direction == expect` -> `!=`."""
    mutation = "high_crf"
    counting = {
        key: spec for key, spec in METRICS.items() if spec.predictions[mutation].counts
    }
    assert counting, "high_crf must have counting witnesses, or this asserts nothing"
    moved = {}
    for key, spec in counting.items():
        moved[key] = 2.0 if spec.predictions[mutation].expect == "increase" else 0.5
    report = compare(_row(), _row(moved), mutation=mutation)
    scene = report["scenes"]["s"]
    assert scene["family_count"] == len({METRICS[k].family for k in counting})
    assert scene["criterion_met"] is True


def test_a_witness_that_did_not_move_is_distinguished_from_one_that_moved_wrongly():
    """MUTATION: in `_verdict_under_mutation`, drop the `did_not_move` branch.

    "The lever pushed this the wrong way" and "the lever never reached this at
    all" call for opposite next steps — correct the declaration, or check that
    the mutation applied. Collapsing them into `contrary` costs the reader the
    one distinction that matters when a criterion comes up short.
    """
    mutation = "high_crf"
    key = next(k for k, s in METRICS.items() if s.predictions[mutation].counts)
    expect = METRICS[key].predictions[mutation].expect
    still = compare(_row(), _row(), mutation=mutation)["scenes"]["s"]
    wrong_way = 0.5 if expect == "increase" else 2.0
    moved = compare(_row(), _row({key: wrong_way}), mutation=mutation)["scenes"]["s"]
    assert still["metrics"][key]["verdict"] == "did_not_move"
    assert moved["metrics"][key]["verdict"] == "contrary"
    assert key in still["did_not_move"] and key not in still["contrary"]


def test_a_witness_that_moved_the_wrong_way_is_contrary_and_counts_for_nothing():
    """MUTATION: report `contrary` as `as_declared`.

    The whole criterion is "in a direction declared in advance"; a metric that
    moved the other way is evidence against the prediction, not for it.
    """
    mutation = "high_crf"
    key = next(k for k, s in METRICS.items() if s.predictions[mutation].counts)
    wrong = 0.5 if METRICS[key].predictions[mutation].expect == "increase" else 2.0
    scene = compare(_row(), _row({key: wrong}), mutation=mutation)["scenes"]["s"]
    assert scene["metrics"][key]["verdict"] == "contrary"
    assert key not in sum(scene["families_satisfied"].values(), [])


def test_a_gated_prediction_never_counts_however_it_moved():
    """MUTATION: in `_verdict_under_mutation`, drop the `expect is None` branch.

    A gated prediction means the reference moves with the mutation, so the delta
    is uninterpretable — not good, not bad, and never a witness.
    """
    mutation = "disabled_aa"
    key = next(k for k, s in METRICS.items() if s.predictions[mutation].expect is None)
    for value in (0.5, 2.0):
        scene = compare(_row(), _row({key: value}), mutation=mutation)["scenes"]["s"]
        assert scene["metrics"][key]["verdict"] == "gated"
        assert key not in sum(scene["families_satisfied"].values(), [])


def test_a_metric_declared_orthogonal_that_moved_is_reported_as_news():
    """MUTATION: report `unexpected_movement` as `as_declared`.

    A metric declared `no_change` or `not_applicable` that MOVES is a finding,
    not a pass: a pre-encode statistic moving under an encoder mutation means the
    render changed too, and the mutation was not the only variable. This is not
    hypothetical — running the real AA lever showed `flat_field_deviation`
    moving on all six corpus scenes against a declaration that calls its
    orthogonality "the metric's whole value".
    """
    mutation = "high_crf"
    key = next(
        k
        for k, s in METRICS.items()
        if s.predictions[mutation].expect in ("no_change", "not_applicable")
    )
    scene = compare(_row(), _row({key: 2.0}), mutation=mutation)["scenes"]["s"]
    assert scene["metrics"][key]["verdict"] == "unexpected_movement"
    assert scene["unexpected_movement"] == [key]
    unmoved = compare(_row(), _row(), mutation=mutation)["scenes"]["s"]
    assert unmoved["metrics"][key]["verdict"] == "as_declared"


def test_two_witnesses_from_one_family_count_once():
    """MUTATION: `family_count = sum(len(v) for v in families.values())`.

    The criterion is ">=3 metrics from >=3 distinct causal FAMILIES". Counting
    bare metrics is satisfiable by shipping one signal under three names — which
    is exactly what family A's three edge metrics would do, and what §1c calls
    the dishonest way to pad a witness count.

    The row is built with two same-family witnesses **declared in it**, rather
    than looked up in today's registry: the comparer reads `counts` from the row
    (so an old row stays interpretable), and today's registry happens to declare
    one counting witness per family — which would make a registry-derived version
    of this test vacuous.
    """
    mutation = "high_crf"
    family_a = sorted(k for k, spec in METRICS.items() if spec.family == "A")
    assert len(family_a) >= 2, "family A must have at least two metrics"
    first, second = family_a[0], family_a[1]
    before, after = _row(), _row({first: 2.0, second: 2.0})
    for row in (before, after):
        for key in (first, second):
            _declare(row, key, mutation, {"expect": "increase", "counts": True})
    scene = compare(before, after, mutation=mutation)["scenes"]["s"]
    assert scene["families_satisfied"] == {"A": [first, second]}
    assert scene["family_count"] == 1, (
        "two witnesses from one causal family are one family, not two"
    )
    assert scene["criterion_met"] is False


def test_the_criterion_needs_three_distinct_families():
    """MUTATION: `REQUIRED_FAMILIES = 1`.

    Restated from the research because the epic's ">=3 metrics" is not honestly
    satisfiable: the obvious metrics are collinear, so three of them can be one
    signal under three names.
    """
    assert REQUIRED_FAMILIES == 3
    mutation = "high_crf"
    counting = [k for k, s in METRICS.items() if s.predictions[mutation].counts]
    families: dict[str, str] = {}
    for key in counting:
        families.setdefault(METRICS[key].family, key)
    two = list(families.values())[: REQUIRED_FAMILIES - 1]
    moved = {
        key: (2.0 if METRICS[key].predictions[mutation].expect == "increase" else 0.5)
        for key in two
    }
    scene = compare(_row(), _row(moved), mutation=mutation)["scenes"]["s"]
    assert scene["family_count"] == REQUIRED_FAMILIES - 1
    assert scene["criterion_met"] is False


def test_the_lever_may_change_the_knob_it_pulls_but_only_in_mutation_mode():
    """MUTATION: drop the `MUTATION_TOUCHES` exemption from `compare`.

    Without it the `high_crf` lever is unevaluable: it works by changing the
    encode command, `x264_argv` is an encode-side comparability key, and every
    encode-side metric is therefore refused — so the encoder lever, which is half
    of an#41's deliverable, reports 0/3 families and reads exactly like an
    instrument that cannot see it. Verified against a real crf23 -> crf40 pair
    while building this: 0/3 before the exemption, 4/3 after.

    The exemption is per lever, by exact path, and **only in mutation mode** — a
    blanket "ignore the environment when a mutation is given" would let a row
    from another machine in through the same door, which is the one thing this
    module exists to refuse.
    """
    from an.bench.registry import MUTATION_TOUCHES, Touch

    assert MUTATION_TOUCHES["high_crf"] == (
        Touch(
            path=("environment", "encode_side", "x264_argv"),
            differs_only_in=("-crf",),
        ),
    ), (
        "the exemption must name the FLAG, not just the path: `x264_argv` is "
        "the whole encode command, so a path-only exemption let an unrelated "
        "`-preset` change ride in as the lever's own."
    )
    assert MUTATION_TOUCHES["disabled_aa"] == (
        Touch(path=("environment", "render_side", "runtime_sha256")),
    ), (
        "the AA lever patches runtime.js, and the row records a digest of the "
        "staged runtime so the lever can prove it applied. That digest is "
        "PROVENANCE and not a comparability key — the runtime is the code under "
        "test — so listing it here exempts nothing; it is what lets "
        "`mutation_may_not_have_applied` answer for this lever at all. Before "
        "it existed, the an#41 assertion asserted nothing for the AA lever."
    )

    changed = copy.deepcopy(_ROW_PROVENANCE)
    changed["environment"]["encode_side"]["x264_argv"] = ["-crf", "40"]
    before, after = _row(), _row(row_provenance=changed)

    exempt = compare(before, after, mutation="high_crf")
    assert exempt["environment_refusals"] == {}
    assert exempt["scenes"]["s"]["metrics"][_one("encode")]["state"] == "compared"
    assert [i["key"] for i in exempt["expected_environment_changes"]] == [
        "environment.encode_side.x264_argv"
    ]
    assert "expected, and exempt" in format_comparison(exempt)

    refused = compare(before, after)
    assert refused["scenes"]["s"]["metrics"][_one("encode")]["refusal"] == (
        "environment_differs"
    ), "without a mutation the same difference must still refuse"


def test_the_exemption_does_not_open_the_door_to_a_different_machine():
    """MUTATION: exempt every environment path whenever a mutation is given.

    The knob the lever pulls is the independent variable. The ISA is not.
    """
    changed = copy.deepcopy(_ROW_PROVENANCE)
    changed["environment"]["encode_side"]["x264_argv"] = ["-crf", "40"]
    changed["environment"]["encode_side"]["isa"] = "x86_64"
    report = compare(_row(), _row(row_provenance=changed), mutation="high_crf")
    keys = [i["key"] for i in report["environment_refusals"]["machine"]]
    assert keys == ["environment.encode_side.isa"]
    assert report["scenes"]["s"]["metrics"][_one("encode")]["refusal"] == (
        "environment_differs"
    )


def test_the_exemption_matches_the_change_the_lever_makes_not_just_its_path():
    """MUTATION: `Touch.is_the_levers_change` returns True on any difference.

    Found by review, in already-merged code. `x264_argv` is the WHOLE encode
    command, so exempting it by path exempted every flag in it: a
    `-preset medium` -> `-preset veryslow` change — which moves every
    encode-side number there is — rode in under `--mutation high_crf` and was
    reported as "the lever moved it, expected", with zero refusals. The
    exemption has to match the change the lever actually makes.

    This is the same class as the defect the exemption itself was written to
    avoid (`test_the_exemption_does_not_open_the_door_to_a_different_machine`),
    one level down: that one refused a blanket exemption across KEYS, this one
    refuses a blanket exemption across the VALUES behind one key.
    """
    unrelated = copy.deepcopy(_ROW_PROVENANCE)
    unrelated["environment"]["encode_side"]["x264_argv"] = [
        "-crf",
        "23",
        "-preset",
        "veryslow",
    ]
    before = _row()
    before["provenance"]["environment"]["encode_side"]["x264_argv"] = [
        "-crf",
        "23",
        "-preset",
        "medium",
    ]
    report = compare(before, _row(row_provenance=unrelated), mutation="high_crf")

    assert report["expected_environment_changes"] == [], (
        "a -preset change is not the change `high_crf` makes"
    )
    assert [i["key"] for i in report["environment_refusals"]["machine"]] == [
        "environment.encode_side.x264_argv"
    ]
    assert report["scenes"]["s"]["metrics"][_one("encode")]["refusal"] == (
        "environment_differs"
    )
    assert any(
        "not the change" in u for u in report["mutation_may_not_have_applied"]
    ), "and the operator is told the lever's own knob is not what moved"

    # The lever's real change still passes, alongside an unrelated flag that
    # is IDENTICAL on both sides — the check is on the difference, not on the
    # command being a bare two-element list.
    applied = copy.deepcopy(_ROW_PROVENANCE)
    applied["environment"]["encode_side"]["x264_argv"] = [
        "-crf",
        "40",
        "-preset",
        "medium",
    ]
    ok = compare(before, _row(row_provenance=applied), mutation="high_crf")
    assert [i["key"] for i in ok["expected_environment_changes"]] == [
        "environment.encode_side.x264_argv"
    ]
    assert ok["environment_refusals"] == {}


def test_an_edited_prediction_refuses_rather_than_scoring_itself_right():
    """MUTATION: `_prediction_disagreements` returns [] unconditionally.

    Found by review. The an#41 criterion is scored against `under_mutation`,
    which `compare` reads from the AFTER row's INLINE block only — so flipping
    one metric's `expect` from `increase` to `decrease` turns a `contrary`
    verdict into `as_declared`, with nothing else in the report moving. That is
    the cheapest available way to make a mutation look caught. Same class as
    the inline `family` relabelling the review found, on the field that
    actually decides the verdict.

    The check is field by field and not whole-dict equality, because the two
    copies legitimately differ: the declarations block carries `reason` and the
    inline block drops it to keep rows small. Whole-dict equality refused 30 of
    30 metrics on the two REAL committed ledger rows — which is why the last
    assertion here runs against those rows and not against a fixture.
    """
    after = _row()
    metrics = after["scenes"]["s"]["metrics"]
    name = next(
        k for k, v in metrics.items() if (v.get("under_mutation") or {}).get("high_crf")
    )
    metrics[name]["under_mutation"]["high_crf"]["expect"] = "decrease"

    entry = compare(_row(), after, mutation="high_crf")["scenes"]["s"]["metrics"][name]
    assert entry["state"] == "refused"
    assert entry["refusal"] == "declaration_changed"
    assert any(
        m["key"].endswith("under_mutation.high_crf.expect") for m in entry["mismatches"]
    ), "and the refusal names the field that moved"

    # Unedited, the same metric compares.
    clean = compare(_row(), _row(), mutation="high_crf")["scenes"]["s"]["metrics"][name]
    assert clean["state"] == "compared"


def test_the_prediction_check_does_not_refuse_the_real_committed_rows():
    """The guard above, run against real data rather than a fixture.

    A whole-dict version of it passed every test in this file and refused
    30 of 30 metrics on the two rows in `misc/bench/ledger/`. A fixture agrees
    with whatever shape the fixture was written to have; the committed rows do
    not.
    """
    rows = sorted((REPO_ROOT / "misc" / "bench" / "ledger").glob("*.json"))
    if len(rows) < 2:
        pytest.skip("needs two committed ledger rows")
    before, after = (json.loads(p.read_text(encoding="utf-8")) for p in rows[:2])
    report = compare(before, after)
    refused = [
        (scene, key)
        for scene, block in report["scenes"].items()
        for key, m in block["metrics"].items()
        if m.get("state") == "refused"
    ]
    assert refused == [], f"the committed rows must compare cleanly, got {refused}"
    assert report["metrics_compared"] > 0


def test_a_lever_whose_declared_knob_did_not_move_is_reported_as_maybe_unapplied():
    """MUTATION: drop the `unapplied` computation.

    The cheapest available evidence that a mutation never applied. Without it, a
    lever that silently failed to take reports "0/3 families" — which reads as
    "the instrument is blind" and sends the reader to fix the wrong thing.
    """
    report = compare(_row(), _row(), mutation="high_crf")
    assert report["mutation_may_not_have_applied"] == [
        "environment.encode_side.x264_argv"
    ]
    assert "may never have applied" in format_comparison(report)

    changed = copy.deepcopy(_ROW_PROVENANCE)
    changed["environment"]["encode_side"]["x264_argv"] = ["-crf", "40"]
    applied = compare(_row(), _row(row_provenance=changed), mutation="high_crf")
    assert applied["mutation_may_not_have_applied"] == []


# ---------------------------------------------------------- regression verdicts


def test_an_interior_optimum_is_a_change_and_never_a_regression():
    """MUTATION: in `_verdict_by_optimum`, read an interior optimum as `minimize`.

    (The bare `if optimum.get("kind") != "one_sided"` guard is not enough on its
    own to mutate: an interior `Optimum` carries `expect=None`, so deleting the
    guard falls through to the same `changed`. The mutation that bites is
    treating `interior` as a direction.)

    `edge_transition_width`'s optimum is interior — under 1 is a jagged
    staircase, 3+ is a soft picture — so neither direction is "worse" without a
    target value, and no row carries one. Manufacturing one from the baseline is
    how a comparison starts asserting more than it knows.
    """
    key = next(k for k, s in METRICS.items() if s.optimum.kind == "interior")
    for value in (0.5, 2.0):
        entry = compare(_row(), _row({key: value}))["scenes"]["s"]["metrics"][key]
        assert entry["verdict"] == "changed"


def test_a_one_sided_metric_moving_away_from_its_optimum_is_a_regression():
    """MUTATION: in `_verdict_by_optimum`, swap `minimize` and `maximize`."""
    minimised = next(
        k
        for k, s in METRICS.items()
        if s.optimum.kind == "one_sided" and s.optimum.expect == "minimize"
    )
    worse = compare(_row(), _row({minimised: 2.0}))
    better = compare(_row(), _row({minimised: 0.5}))
    assert worse["scenes"]["s"]["metrics"][minimised]["verdict"] == "regression"
    assert worse["has_regressions"] is True
    assert better["scenes"]["s"]["metrics"][minimised]["verdict"] == "improvement"
    assert better["has_regressions"] is False


def test_a_guard_metric_never_reports_a_regression():
    """MUTATION: give `guard` optima a direction.

    A guard has no predicted direction by declaration — `frame_distinct_colours`
    is "a guard, not a dial". Reporting one as a regression invents a verdict the
    table explicitly refuses to give.
    """
    key = next(k for k, s in METRICS.items() if s.optimum.kind == "guard")
    entry = compare(_row(), _row({key: 2.0}))["scenes"]["s"]["metrics"][key]
    assert entry["verdict"] == "changed"


# ----------------------------------------------------------- the committed rows


def test_the_committed_ledger_rows_compare_without_crashing():
    """The real thing, in the default leg, with no render.

    MUTATION: any change that makes `compare` assume a field the older row does
    not carry.
    """
    rows = latest_rows()
    if len(rows) < 2:
        pytest.skip("fewer than two committed ledger rows")
    report = compare(load_row(rows[0]), load_row(rows[1]))
    assert report["scenes"], "the two rows share no scene"
    assert isinstance(format_comparison(report), str)


def test_latest_rows_skips_a_row_that_describes_no_commit(tmp_path):
    """MUTATION: drop the `-dirty` filter in `latest_rows`.

    A row measured against uncommitted edits describes no commit, so comparing
    one is comparing against nothing nameable — the same reason the filename
    carries the suffix at all.
    """
    ledger = tmp_path / "misc" / "bench" / "ledger"
    ledger.mkdir(parents=True)
    for name in (
        "2026-01-01-aaaaaaa.json",
        "2026-01-02-bbbbbbb-dirty.json",
        "2026-01-03-ccccccc.json",
    ):
        (ledger / name).write_text("{}", encoding="utf-8")
    assert [p.name for p in latest_rows(root=tmp_path)] == [
        "2026-01-01-aaaaaaa.json",
        "2026-01-03-ccccccc.json",
    ]


# ------------------------------ an#40 adversarial-review hardening


def test_a_key_absent_from_BOTH_rows_is_neither_a_caveat_about_one_nor_a_sentinel():
    """MUTATION: fold the absent-from-both branch back into the one-sided one.

    Absent from both rows says nothing about their comparability — it is a key
    neither row has ever carried. Reporting it as "absent from before" also
    leaked the `_ABSENT` sentinel object into `value`, which made the whole
    report un-serialisable and crashed `an bench-compare --raw` on any pair of
    rows predating a key. Live on the committed rows today, via `shot_order`.
    """
    import json

    trimmed = copy.deepcopy(_PROVENANCE)
    del trimmed["shot_order"]
    report = compare(_row(provenance=trimmed), _row(provenance=trimmed))
    caveats = report["scenes"]["s"]["caveats"]
    assert [c["absent_from"] for c in caveats] == ["both rows"]
    assert caveats[0]["value"] is None
    json.dumps(report)  # must not raise


def test_a_metric_with_an_unknown_comparison_scope_is_refused():
    """MUTATION: `if scope not in env_refusals:` -> `if False:`.

    Deleting the field let an encode-side metric from another ISA and another
    x264 build compare cleanly and report a `regression`. Every neighbouring
    absence in this module is a surfaced caveat; this one was silently "compare
    anyway", which is the one thing the module exists not to do.
    """
    changed = copy.deepcopy(_ROW_PROVENANCE)
    changed["environment"]["encode_side"]["isa"] = "x86_64"
    changed["environment"]["encode_side"]["x264_sei"] = "a different build"
    before, after = _row(), _row(row_provenance=changed)
    victim = _one("encode")
    for row in (before, after):
        row["scenes"]["s"]["metrics"][victim].pop("comparison_scope", None)
    entry = compare(before, after)["scenes"]["s"]["metrics"][victim]
    assert entry["state"] == "refused"
    assert entry["refusal"] == "comparison_scope_unknown"


def test_a_row_cannot_pad_the_criterion_with_a_tautology():
    """MUTATION: `entry["counts"] = bool(prediction.get("counts"))`.

    `Prediction.__post_init__` refuses `counts=True` on a `no_change`
    prediction — but a ROW is data, and `--compare` reads rows, including
    hand-edited and foreign ones. Relabelling three metrics from three families
    as `{"expect": "no_change", "counts": true}` made the criterion report MET
    on two byte-identical rows, with the padded witnesses INVISIBLE in the
    digest (a `no_change` that holds prints nothing) and `--strict` exiting 0.
    """
    mutation = "high_crf"
    before, after = _row(), _row()
    families = {}
    for key, spec in METRICS.items():
        families.setdefault(spec.family, key)
    padded = list(families.values())[:REQUIRED_FAMILIES]
    for row in (before, after):
        for key in padded:
            _declare(row, key, mutation, {"expect": "no_change", "counts": True})
    scene = compare(before, after, mutation=mutation)["scenes"]["s"]
    assert scene["family_count"] == 0, scene["families_satisfied"]
    assert scene["criterion_met"] is False
    for key in padded:
        assert scene["metrics"][key]["counts"] is False
        assert "counts_refused" in scene["metrics"][key]


def test_a_missing_prediction_is_not_reported_as_a_gate():
    """MUTATION: drop the `if not prediction:` branch in `_verdict_under_mutation`.

    A gate is a DECLARED "we cannot tell"; an empty block is "nobody wrote a
    prediction down". Both fail closed, so no wrong answer — but they send a
    reader to opposite places, and one of them is a row-format problem rather
    than a measurement problem.
    """
    mutation = "high_crf"
    after = _row({_one("encode"): 2.0})
    victim = _one("encode")
    after["scenes"]["s"]["metrics"][victim].pop("under_mutation")
    entry = compare(_row(), after, mutation=mutation)["scenes"]["s"]["metrics"][victim]
    assert entry["verdict"] == "no_prediction"


def test_strict_refuses_to_pass_a_comparison_that_compared_nothing():
    """MUTATION: `not report.get("answered")` -> `False` in `an.tools.bench_compare`.

    The documented CI gate — "exit nonzero when the answer is bad" — exited 0 on
    a run in which EVERY scene was refused, while printing
    "0 regression(s), 0 improvement(s), 0 change(s)". That is a zero this
    module's own docstring calls worse than no number at all, presented as a
    pass. Four ways to produce it, and only one of them is a regression.
    """
    clean = compare(_row(), _row())
    assert clean["answered"] is True
    assert clean["metrics_compared"] > 0

    moved = copy.deepcopy(_PROVENANCE)
    moved["scene_contract_sha256"] = "b" * 64
    refused = compare(_row(), _row(provenance=moved))
    assert refused["answered"] is False
    assert refused["metrics_compared"] == 0
    assert "NO ANSWER" in format_comparison(refused)

    assert compare(_row(), _row(scene="other"))["answered"] is False


def test_the_strict_flag_fails_closed_end_to_end(tmp_path):
    """The gate itself, through the CLI, on every bad outcome.

    MUTATION: any weakening of `bad` in `an.tools.bench_compare`.
    """
    import json
    import subprocess
    import sys

    (tmp_path / "a.json").write_text(json.dumps(_row()), encoding="utf-8")
    moved = copy.deepcopy(_PROVENANCE)
    moved["scene_contract_sha256"] = "b" * 64
    cases = {
        "clean": (_row(), 0),
        "different scene": (_row(provenance=moved), 1),
        "no shared scene": (_row(scene="other"), 1),
    }
    for label, (row, expected) in cases.items():
        (tmp_path / "b.json").write_text(json.dumps(row), encoding="utf-8")
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "an",
                "bench-compare",
                "--before",
                str(tmp_path / "a.json"),
                "--after",
                str(tmp_path / "b.json"),
                "--strict",
            ],
            capture_output=True,
            text=True,
        )
        assert result.returncode == expected, f"{label}: {result.stdout[-400:]}"
