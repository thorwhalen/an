"""`an bench --mutation <lever>` — the CLI routing, with nothing rendered.

Split from `test_bench_mutation.py` on purpose: that module runs the real lever
machinery and takes two minutes, and every one of these is a registered
mutant's `caught_by` file, which `an bench-mutants` runs in full once per
mutant. A guard nobody can afford to run is a guard nobody runs.

Defect 6 of an#54: `bench()` took no `mutation=`, so the `--compare` artifact
epic #9's standing rule 2 makes procedurally mandatory was ALWAYS the
`mutation=None` path — which asks "is this worse" of a run that was broken on
purpose.
"""

from __future__ import annotations

# ------------------------------ an#54 defect 6: `an bench --mutation <lever>`


def test_bench_routes_a_named_lever_through_mutated_row(monkeypatch):
    """MUTATION: `compare_rows(load_row(compare), ledger, mutation=mutation or None)`
    -> drop the `mutation=`.

    Without it `compare` answers "is the second row worse" of a run degraded on
    purpose, so the declared per-mutation predictions are never scored and the
    an#41 criterion cannot appear in the `--compare` artifact epic #9's standing
    rule 2 makes procedurally mandatory.
    """
    from an import tools

    seen: dict = {}

    def fake_mutated_row(name, **kw):
        seen["lever"] = name
        return {"scenes": {}, "provenance": {}, "generated_at": "now"}

    def fake_compare(before, after, mutation=None):
        seen["mutation"] = mutation
        return {"before": {}, "after": {}, "scenes": {}}

    monkeypatch.setattr("an.bench.mutations.mutated_row", fake_mutated_row)
    monkeypatch.setattr("an.bench.compare.compare", fake_compare)
    monkeypatch.setattr("an.bench.compare.load_row", lambda p: {})
    monkeypatch.setattr("an.bench.compare.format_comparison", lambda r: "TABLE")
    monkeypatch.setattr("an.bench.run.format_panel", lambda ledger: "PANEL")

    out = tools.bench(mutation="high_crf", compare="baseline.json")
    assert seen["lever"] == "high_crf"
    assert seen["mutation"] == "high_crf", (
        "the compare must be asked the PER-MUTATION question, not 'is it worse'"
    )
    assert "MUTATED RUN" in out and "NOT written" in out


def test_bench_refuses_to_bless_under_a_lever():
    """MUTATION: `if bless:` -> `if False:` in the mutation branch.

    Blessing under a lever commits the deliberately degraded picture as the
    reference every future run is measured against — a permanent, silent
    re-baseline, and the one bless failure a recorded reason cannot undo.
    """
    from an import tools

    out = tools.bench(mutation="high_crf", bless="because")
    assert out.startswith("refusing --bless with --mutation")


def test_bench_refuses_an_undeclared_lever_before_rendering_anything():
    """The corpus takes minutes; a typo must not cost them.

    Also the exact state a `--strict --mutation supersample` run is in before
    the lever is registered — which is why defect 2's `--strict` fix and this
    refusal belong in one PR.
    """
    from an import tools

    out = tools.bench(mutation="supersample")
    assert out.startswith("unknown mutation 'supersample'")
    assert "high_crf" in out and "disabled_aa" in out


def test_a_mutated_row_may_not_be_written_into_the_ledger_directory(
    tmp_path, monkeypatch
):
    """MUTATION: drop the `path.parent == ledger dir` refusal.

    `latest_rows` would then hand a deliberately-degraded row to a bare
    `an bench-compare` as a baseline — evidence about a broken pipeline,
    presented as a commit's evidence.
    """
    from an import tools
    from an.bench.paths import LEDGER_DIRNAME, repo_root

    monkeypatch.setattr(
        "an.bench.mutations.mutated_row",
        lambda name, **kw: {"scenes": {}, "provenance": {}},
    )
    target = repo_root() / LEDGER_DIRNAME / "2026-01-01-deadbee.json"
    out = tools.bench(mutation="high_crf", out=str(target))
    assert out.startswith(f"refusing to write a mutated row into {LEDGER_DIRNAME}")
    assert not target.exists()
