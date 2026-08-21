"""an#41 — the wave's actual deliverable: pull a lever, watch the numbers move.

Everything else in Wave 2 is scaffolding for this. A deliberately degraded
pipeline must move the ledger in a direction **declared in advance**, and the
criterion is the research's corrected one:

> >=3 metrics from >=3 distinct causal families, evaluated **per mutation**,
> with a per-metric per-mutation sign declared in advance.

Not ">=3 metrics", which is satisfiable dishonestly by shipping one signal under
three names, and which fails for the CRF lever for a reason that has nothing to
do with the instrument: the golden corpus sits *upstream* of the encoder, so no
encode change can reach family B, and that failure would be misdiagnosed as the
harness being wrong.

The structural half runs in the **default** CI leg. The half that renders is
opt-in, because it renders the whole corpus three times.
"""

from __future__ import annotations

import pytest

from an.bench.compare import REQUIRED_FAMILIES, compare
from an.bench.mutants import MUTANTS, check_sites, format_results, run_mutants
from an.bench.mutations import LEVERS, MutationError, mutated_row
from an.bench.registry import METRICS, MUTATIONS, TRIPWIRES


# ------------------------------------------------------- the declaration table


def test_every_declared_mutation_has_a_lever_that_pulls_it():
    """MUTATION: delete an entry from `LEVERS`.

    A mutation with a full column of predictions and no way to apply it is a
    criterion nobody can ever evaluate — and it looks, in the ledger, exactly
    like one that is being evaluated.
    """
    assert set(LEVERS) == set(MUTATIONS)
    for name, lever in LEVERS.items():
        assert lever.name == name
        assert lever.side in ("render", "encode")
        assert lever.what and lever.why


def test_the_two_levers_are_on_opposite_sides_of_the_encoder():
    """MUTATION: point both levers at the same side.

    Two disjoint levers are mandatory. An encoder lever cannot touch a
    golden-frame metric, because the corpus is UPSTREAM of the encoder; a render
    lever cannot be judged by an encode-side metric, because that metric's
    reference moves with the mutation. One lever can therefore never exercise
    the whole panel, and a single-lever criterion silently tests half of it.
    """
    assert {lever.side for lever in LEVERS.values()} == {"render", "encode"}


@pytest.mark.parametrize("mutation", MUTATIONS)
def test_each_mutation_declares_at_least_three_counting_families(mutation):
    """MUTATION: set `counts=False` on any single-witness family's metric.

    The criterion cannot be met by a panel that does not even *declare* three
    independent witnesses, and that is checkable without rendering anything.
    """
    families = {
        spec.family for spec in METRICS.values() if spec.predictions[mutation].counts
    }
    assert len(families) >= REQUIRED_FAMILIES, (
        f"{mutation} declares counting witnesses in only {sorted(families)}; the "
        f"criterion needs {REQUIRED_FAMILIES} distinct causal families"
    )


@pytest.mark.parametrize("mutation", MUTATIONS)
def test_no_metric_family_supplies_two_witnesses(mutation):
    """MUTATION: set `counts=True` on `off_palette_pixel_fraction` (family A).

    §1c's redundancy rule: **count at most one per family**. Family A's three
    edge metrics all answer "how many pixels are not one of the flat colours",
    co-move on every AA change, and all move the *wrong* way together on a blur
    regression — so counting them separately is one signal wearing three names,
    and it is the specific dishonest way to satisfy this criterion.
    """
    counting: dict[str, list[str]] = {}
    for key, spec in METRICS.items():
        if spec.predictions[mutation].counts:
            counting.setdefault(spec.family, []).append(key)
    crowded = {family: keys for family, keys in counting.items() if len(keys) > 1}
    assert not crowded, (
        f"{mutation}: {crowded} — one causal family, more than one counted "
        "witness. Count at most one per family."
    )


def test_a_tripwire_counts_zero_toward_every_criterion():
    """MUTATION: `counts=True` on `golden_identity`'s `disabled_aa` prediction.

    A tripwire fires on improvements and regressions alike, so it is a change
    detector rather than evidence of quality. `MetricSpec.__post_init__` refuses
    the combination; this asserts the refusal rather than the table's contents,
    which would be satisfied either way.
    """
    from an.bench.registry import MetricSpec, Optimum, Prediction, RegistryError

    for spec in TRIPWIRES.values():
        assert all(not p.counts for p in spec.predictions.values())
    with pytest.raises(RegistryError, match="counts ZERO"):
        MetricSpec(
            key="pretend",
            family="B",
            unit="boolean",
            tripwire=True,
            sentence="x",
            optimum=Optimum(kind="guard"),
            predictions={m: Prediction("decrease", counts=True) for m in MUTATIONS},
        )


def test_golden_identity_is_declared_to_fire_under_the_render_lever():
    """The declaration must say what the tripwire actually does.

    MUTATION: set it back to `no_change`.

    It was `no_change` while its own `reason` said "it FAILS", so every scene
    reported `unexpected_movement` for a tripwire doing exactly its job — found
    by running `an bench-compare` against a real AA-off row. A prediction that
    contradicts its own stated reason is worse than no prediction: it teaches a
    reader to ignore the column.
    """
    prediction = TRIPWIRES["golden_identity"].predictions["disabled_aa"]
    assert prediction.expect == "decrease"
    assert prediction.counts is False


@pytest.mark.parametrize(
    "key",
    ["flat_field_deviation", "flat_field_p99_dev", "encode_ringing_excess"],
)
def test_a_metric_whose_reference_moves_with_the_render_lever_is_gated(key):
    """MUTATION: declare any of these `no_change` under `disabled_aa` again.

    All three were declared orthogonal to AA — `flat_field_deviation`'s note
    called that orthogonality "the metric's whole value" — and all three move
    when the real MSAA lever is pulled. The reason is structural, not
    surprising: their masks and references are derived from the SOURCE frames,
    which a render mutation changes, so there is no fixed reference and the
    delta is uninterpretable. `gated`, not `no_change`: the difference is
    exactly "we cannot tell" versus "we predict nothing happens", and only one
    of those is true.
    """
    prediction = METRICS[key].predictions["disabled_aa"]
    assert prediction.expect is None
    assert prediction.gate == "source_hash_differs"
    assert prediction.counts is False


# --------------------------------------------------- the guard mutants, as data


def test_every_declared_mutant_still_applies():
    """The cheap half of "I mutation-tested it", and the half that rots.

    MUTATION: rename any function or reflow any line a `Mutant.old` quotes.

    A mutant whose source text has moved silently stops proving anything, and
    nothing else in the suite would notice. Runs no pytest subprocesses — it
    reads each file once — so it can afford to be in the default leg, where the
    full sweep cannot.
    """
    assert MUTANTS, "the registry is empty, so this asserts nothing"
    problems = check_sites()
    assert not problems, "\n".join(problems)


def test_a_representative_mutant_is_really_caught():
    """Proves the runner itself works, without paying for the full sweep.

    MUTATION: in `run_mutants`, `caught = completed.returncode != 0` -> `!= 999`.

    One mutant rather than the whole registry: the full sweep takes ~40s of pytest
    subprocesses and belongs behind `an bench-mutants`, but a harness that
    reports "all caught" because it never actually ran anything is precisely the
    failure this module exists to end.
    """
    results = run_mutants(["compare_gains_a_tolerance_band"])
    assert len(results) == 1
    assert results[0]["caught"] is True, results[0]["summary"]
    assert "1 failed" in results[0]["summary"]
    assert "CAUGHT" in format_results(results)


def test_the_runner_restores_the_tree_even_when_a_mutant_survives(monkeypatch):
    """MUTATION: move the restore out of the `finally`.

    A sweep that leaves the tree broken is worse than any mutant surviving —
    and the natural shape (reverse the substitution afterwards) fails exactly
    when the run raised partway through.
    """
    from pathlib import Path

    from an.bench.mutants import MUTANTS as declared
    from an.bench.paths import repo_root

    victim = next(m for m in declared if m.name == "compare_gains_a_tolerance_band")
    path = repo_root() / victim.file
    before = path.read_text(encoding="utf-8")

    def explode(*args, **kwargs):
        raise RuntimeError("pytest could not start")

    monkeypatch.setattr("an.bench.mutants.subprocess.run", explode)
    with pytest.raises(RuntimeError, match="could not start"):
        run_mutants([victim.name])
    assert path.read_text(encoding="utf-8") == before, "the mutant was left in the tree"
    assert Path(path).exists()


# --------------------------------------------------------- the levers, for real


@pytest.mark.browser
@pytest.mark.ffmpeg
@pytest.mark.parametrize("mutation", MUTATIONS)
def test_a_deliberate_degradation_moves_three_causal_families(mutation):
    """**The wave's deliverable.** Renders the corpus twice per mutation.

    MUTATION: revert any declaration correction this issue made, or break a
    lever so it no longer applies.

    Asserted per scene, and met on *at least one*, because both levers are
    measurably scene-dependent and that is a property of the pipeline rather
    than of the instrument:

    - `high_crf` is met on all six (C + D + F everywhere, plus E on five).
    - `disabled_aa` is met on the three scenes with non-axis-aligned edges —
      `aa_probe`, `multi_shot`, `saturated_outline`. MSAA applies to WebGL
      geometry, so an SVG sprite is nearly blind to it and axis-aligned
      `drawRect` edges are bit-identical with it on or off. `aa_probe` exists in
      the corpus for exactly this reason, which makes it load-bearing for this
      test rather than decorative.
    """
    from an.bench.run import run_bench

    baseline = run_bench(write=False)
    mutated = mutated_row(mutation)
    report = compare(baseline, mutated, mutation=mutation)

    assert not report["mutation_may_not_have_applied"], (
        f"the {mutation} lever declares a knob it did not move: "
        f"{report['mutation_may_not_have_applied']}. Every 'nothing moved' below "
        "would be about the lever, not about the instrument."
    )
    assert report["criterion_met"], (
        f"{mutation} moved fewer than {REQUIRED_FAMILIES} causal families on every "
        "scene: "
        + "; ".join(
            f"{name} {blk['family_count']}/{REQUIRED_FAMILIES} {blk['families_satisfied']}"
            for name, blk in sorted(report["scenes"].items())
        )
    )
    for name, block in sorted(report["scenes"].items()):
        assert not block["unexpected_movement"], (
            f"{name}: {block['unexpected_movement']} are declared orthogonal to "
            f"{mutation} and moved. Either the declaration is wrong or the lever "
            "is not the only variable — both are findings, neither is a pass."
        )


@pytest.mark.browser
@pytest.mark.ffmpeg
def test_a_lever_that_did_not_apply_is_refused_rather_than_reported_as_blindness():
    """MUTATION: in `mutations.py`, drop the `verify_row` call from `mutated_row`.

    A lever that silently failed to take produces a run in which nothing moved,
    which reads exactly like an instrument that cannot see it — and sends the
    reader to fix the wrong thing.
    """
    from an.bench.corpus import DFLT_FIXTURES
    from an.bench.mutations import LEVERS

    lever = LEVERS["high_crf"]
    one = {"aa_probe": DFLT_FIXTURES["aa_probe"]}
    row = mutated_row("high_crf", scenes=one)
    lever.verify_row(row)  # the real thing passes

    row["provenance"]["environment"]["encode_side"]["x264_argv"] = ["-crf", "23"]
    with pytest.raises(MutationError, match="did not reach the encode"):
        lever.verify_row(row)
