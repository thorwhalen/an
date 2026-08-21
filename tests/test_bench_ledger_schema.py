"""The ledger's three blocks, and the guards that keep them readable (an#36).

Every assertion here is about a way a row could be *misread*, not about a way
it could be malformed. Three of the schema's fields had to exist before the
first row was written, because retrofitting any of them invalidates every prior
entry — so these tests are the thing that stops the first row from being wrong
in a way nobody notices until there are fifty of them.
"""

from __future__ import annotations

import json

import pytest

from an.bench.ledger import (
    SCHEMA_VERSION,
    LedgerSchemaError,
    Value,
    build_ledger,
    build_scene_block,
    gated,
    measured,
    unavailable,
    witnesses,
)
from an.bench.registry import (
    FAMILY_SIDE,
    METRICS,
    MUTATIONS,
    TRIPWIRES,
    Prediction,
    RegistryError,
)

MIN_PROVENANCE = {
    "scene_contract_sha256": "0" * 64,
    "resolution": [320, 240],
    "fps": 24,
    "n_frames": 60,
}


def _full_metrics(**overrides) -> dict:
    out = {k: measured(1.0) for k in METRICS}
    out.update(overrides)
    return out


def _full_tripwires() -> dict:
    return {k: gated("golden_absent") for k in TRIPWIRES}


def _block(**kwargs) -> dict:
    return build_scene_block(
        provenance=dict(MIN_PROVENANCE),
        metrics=kwargs.pop("metrics", _full_metrics()),
        tripwires=kwargs.pop("tripwires", _full_tripwires()),
    )


# ------------------------------------------------------------ the three blocks


def test_the_three_blocks_exist_and_are_disjoint():
    block = _block()
    assert set(block) == {"provenance", "metrics", "tripwires"}
    assert not (set(block["metrics"]) & set(block["tripwires"]))


def test_no_key_is_declared_as_both_a_metric_and_a_tripwire():
    """A key that both counts and counts zero cannot be evaluated at all.

    Asserted against the registries rather than the ledger builder: the
    builder's own overlap check is a backstop that the undeclared-key check
    reaches first, so this is where the invariant actually lives.
    """
    assert not (set(METRICS) & set(TRIPWIRES))


def test_the_builder_refuses_a_tripwire_smuggled_into_the_metrics_block():
    key = next(iter(TRIPWIRES))
    with pytest.raises(LedgerSchemaError):
        build_scene_block(
            provenance=dict(MIN_PROVENANCE),
            metrics={**_full_metrics(), key: measured(1.0)},
            tripwires=_full_tripwires(),
        )


def test_every_metric_row_is_labelled_render_or_encode():
    """The two families are blind to each other's mutations by construction."""
    for key, row in _block()["metrics"].items():
        assert row["side"] in ("render", "encode"), key
        assert row["side"] == FAMILY_SIDE[row["family"]], key


#: Pinned by literal, deliberately. Asserting `row["side"] == FAMILY_SIDE[...]`
#: reads the same table twice and passes for any mapping, including a wrong one.
EXPECTED_FAMILY_SIDE = {
    "A": "render",  # edge geometry, computed on the pre-encode PNG
    "B": "render",  # golden change, and the corpus is upstream of the encoder
    "C": "encode",  # coded-plane edge fidelity: decoded vs its own source
    "D": "encode",  # flat-field fidelity: decoded vs its own source
    "E": "encode",  # temporal held-pixel fidelity: decoded vs its own source
    "F": "encode",  # rate cost: a property of the encoded file
    "G": "encode",  # ringing: two encodes of the same frames
}


def test_the_family_to_side_mapping_is_what_each_metric_actually_measures():
    """A relabelled family changes `--compare`'s rule for that metric.

    Render-side rows compare across any machine; encode-side rows are
    machine-scoped, because a different x264 build moves the decoded stream by
    two orders of magnitude. Calling an encode-side metric render-side would
    let an#40 compare two rows that are not comparable.
    """
    assert FAMILY_SIDE == EXPECTED_FAMILY_SIDE


@pytest.mark.parametrize(
    "key,side",
    [
        ("edge_transition_width", "render"),  # reads only the PNG
        ("min_ssim_win8_vs_golden", "render"),  # reads only PNGs
        ("flat_field_deviation", "encode"),  # decoded vs its own source
        ("coded_luma_edge_error", "encode"),
        ("encode_flicker_on_held_pixels", "encode"),
        ("video_stream_bytes", "encode"),  # a property of the encoded file
    ],
)
def test_named_metrics_are_on_the_side_their_definition_requires(key, side):
    assert METRICS[key].side == side


def test_render_and_encode_rows_carry_different_comparison_scopes():
    """Measured, and opposite: pixels are ISA-invariant, encodes are not.

    A band wide enough to absorb an x264 build change would swallow
    `flat_field_deviation`'s entire crf18->23 signal, so the encode side is
    scoped rather than banded.
    """
    rows = _block()["metrics"]
    scopes = {r["side"]: r["comparison_scope"] for r in rows.values()}
    assert scopes["render"] == "any_machine"
    assert scopes["encode"] == "machine"


# ------------------------------------------------------------ the value states


def test_a_gated_metric_is_never_a_number():
    """`0.0` for "we could not compare" is read downstream as a measurement."""
    with pytest.raises(LedgerSchemaError, match="must carry a null value"):
        Value(0.0, state="gated", gate="reference_moved")


def test_a_gated_metric_must_name_its_gate():
    with pytest.raises(LedgerSchemaError, match="name its gate"):
        Value(None, state="gated")


def test_unavailable_is_not_gated():
    """Two different nulls, and folding them loses the one that matters.

    `gated` means the comparison is impossible; `unavailable` means the check
    did not run. A check that crashed is not evidence anything is fine, and a
    row that says "gated" about it is claiming a reason it does not have.
    """
    g = gated("reference_moved").to_dict()
    u = unavailable("ffmpeg not on PATH").to_dict()
    assert g["state"] == "gated" and "gate" in g
    assert u["state"] == "unavailable" and "gate" not in u
    assert u["detail"], "an unavailable value must say what could not run"


def test_an_unavailable_value_without_a_reason_is_refused():
    with pytest.raises(LedgerSchemaError, match="what could not run"):
        Value(None, state="unavailable")


def test_a_measured_value_may_not_be_null():
    with pytest.raises(LedgerSchemaError, match="state='measured' with a null"):
        Value(None)


def test_nan_is_refused_as_a_measured_value():
    """An empty mask means the check could not run, which is not a number."""
    with pytest.raises(LedgerSchemaError, match="NaN"):
        measured(float("nan"))


# ------------------------------------------------------ the prediction states


def test_a_no_change_prediction_can_never_count():
    """"No change by construction" is a tautology.

    Counting it lets any pre-encode statistic pad the witness count for free,
    which is precisely how ">=3 metrics moved" is satisfied dishonestly.
    """
    with pytest.raises(RegistryError, match="tautology"):
        Prediction("no_change", counts=True)


def test_a_not_applicable_prediction_can_never_count():
    with pytest.raises(RegistryError, match="tautology"):
        Prediction("not_applicable", counts=True)


def test_a_gated_prediction_can_never_count():
    with pytest.raises(RegistryError, match="cannot count"):
        Prediction(None, gate="reference_moved", counts=True)


def test_a_gated_prediction_must_name_its_gate():
    with pytest.raises(RegistryError, match="name its gate"):
        Prediction(None)


def test_every_metric_declares_every_mutation():
    """An absent prediction is not the same as a null one, and only one is honest."""
    for key, spec in {**METRICS, **TRIPWIRES}.items():
        assert set(spec.predictions) >= set(MUTATIONS), key


def test_a_metric_that_skips_a_mutation_is_refused():
    """The check, not just the data — the table above satisfies it either way."""
    from an.bench.registry import MetricSpec, Optimum

    with pytest.raises(RegistryError, match="no prediction declared"):
        MetricSpec(
            key="half_declared",
            family="A",
            unit="px",
            sentence="x",
            optimum=Optimum(kind="guard"),
            predictions={MUTATIONS[0]: Prediction("increase")},
        )


def test_a_tripwire_may_never_count():
    """It fires on improvements and regressions alike."""
    for key, spec in TRIPWIRES.items():
        assert not any(p.counts for p in spec.predictions.values()), key
    for key, row in _block()["tripwires"].items():
        assert row["counts"] == 0, key


def test_a_counting_tripwire_is_refused():
    """A change detector is not evidence of quality, and the check says so."""
    from an.bench.registry import MetricSpec, Optimum

    with pytest.raises(RegistryError, match="counts ZERO"):
        MetricSpec(
            key="greedy_tripwire",
            family="B",
            unit="boolean",
            sentence="x",
            tripwire=True,
            optimum=Optimum(kind="guard"),
            predictions={m: Prediction("increase", counts=True) for m in MUTATIONS},
        )


# --------------------------------------------------------------- completeness


def test_a_missing_metric_row_is_refused():
    """An absent row and a null row look the same and mean opposite things."""
    partial = _full_metrics()
    partial.pop(next(iter(METRICS)))
    with pytest.raises(LedgerSchemaError, match="missing"):
        build_scene_block(
            provenance=dict(MIN_PROVENANCE),
            metrics=partial,
            tripwires=_full_tripwires(),
        )


def test_an_undeclared_metric_row_is_refused():
    """A metric with no registry entry has no family and no predicted direction."""
    with pytest.raises(LedgerSchemaError, match="undeclared"):
        build_scene_block(
            provenance=dict(MIN_PROVENANCE),
            metrics={**_full_metrics(), "invented_metric": measured(1.0)},
            tripwires=_full_tripwires(),
        )


@pytest.mark.parametrize("field", sorted(MIN_PROVENANCE))
def test_the_fields_that_decide_comparability_are_required(field):
    provenance = dict(MIN_PROVENANCE)
    provenance.pop(field)
    with pytest.raises(LedgerSchemaError, match=field):
        build_scene_block(
            provenance=provenance,
            metrics=_full_metrics(),
            tripwires=_full_tripwires(),
        )


# ------------------------------------------------------------- the whole row


def test_the_row_round_trips_through_json_with_sorted_keys():
    """So two rows diff line-for-line rather than by dict insertion order."""
    ledger = build_ledger(provenance={"git": {}}, scenes={"s": _block()})
    text = json.dumps(ledger, indent=2, sort_keys=True)
    assert json.loads(text) == ledger
    assert ledger["schema_version"] == SCHEMA_VERSION


def test_the_witness_query_reads_the_row_and_not_the_registry():
    """an#41's criterion is a query over what was actually written down."""
    block = _block()
    for mutation in MUTATIONS:
        families = witnesses(block, mutation)
        assert all(isinstance(v, list) and v for v in families.values())
        for family, keys in families.items():
            for key in keys:
                assert block["metrics"][key]["family"] == family


def test_each_mutation_has_witnesses_from_at_least_three_families():
    """The criterion an#41 will evaluate, asserted against the shipped table.

    Restated from the epic's ">=3 metrics" for a measured reason: an encoder
    lever cannot touch a golden-frame metric because the corpus is UPSTREAM of
    the encoder, so counting bare metrics would fail for a reason that has
    nothing to do with the instrument being blind.
    """
    block = _block()
    for mutation in MUTATIONS:
        families = witnesses(block, mutation)
        assert len(families) >= 3, (
            f"{mutation} has witnesses from only {sorted(families)}; the "
            "criterion is >=3 metrics from >=3 DISTINCT causal families"
        )


def test_family_c_supplies_at_most_one_witness_per_mutation():
    """Correlated r=0.990 in their broken forms; the decorrelation is unmeasured.

    Counting two members of one family is shipping one signal under two names,
    which is how the criterion is satisfied dishonestly.
    """
    block = _block()
    for mutation in MUTATIONS:
        c_witnesses = witnesses(block, mutation).get("C", [])
        assert len(c_witnesses) <= 1, c_witnesses


# --------------------------------------------------------------- an#38 additions


def test_a_single_shot_scene_hashes_exactly_as_it_did_before_multi_shot_support():
    """History must stay comparable.

    MUTATION: in `scenes_contract_sha256`, drop the `len(digests) == 1` branch.

    `an bench --compare` refuses rows whose contract hash differs, so a
    gratuitous change here would retire every already-committed row as evidence
    about the scenes it measured — for a reason that never reached a pixel.
    """
    from an.bench.contract import scene_contract_sha256, scenes_contract_sha256

    scene = {"meta": {"fps": 24}, "scene": {"children": [{}, {}]}}
    assert scenes_contract_sha256([scene]) == scene_contract_sha256(scene)


def test_a_change_to_any_shot_moves_the_scene_contract_hash():
    """MUTATION: in `scenes_contract_sha256`, hash only `scene_jsons[0]`.

    Hashing the first shot alone lets a change to the second one pass as "the
    same scene", which is precisely the claim this digest exists to deny.
    """
    from an.bench.contract import scenes_contract_sha256

    a = {"scene": {"children": [{}]}}
    b = {"scene": {"children": [{}, {}]}}
    assert scenes_contract_sha256([a, a]) != scenes_contract_sha256([a, b])
    assert scenes_contract_sha256([a, b]) != scenes_contract_sha256([b, a]), (
        "shot ORDER is part of the contract: the same shots concatenated the "
        "other way round are a different video"
    )


def test_a_tripwire_that_stopped_being_computed_is_refused():
    """MUTATION: in `build_scene_block`, drop the `absent_tw` check.

    A change detector that quietly stopped being computed vanishes from the row
    and reads exactly like one that fired and found nothing — the same
    absent-versus-null confusion the metrics block already refuses.
    """
    with pytest.raises(LedgerSchemaError, match="tripwires block is missing"):
        build_scene_block(
            provenance=dict(MIN_PROVENANCE),
            metrics={k: measured(1.0) for k in METRICS},
            tripwires={},
        )
