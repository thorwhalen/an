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

from pathlib import Path

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


def test_both_sides_of_the_encoder_have_a_lever():
    """MUTATION: point every lever at the same side.

    At least one lever per side is mandatory. An encoder lever cannot touch a
    golden-frame metric, because the corpus is UPSTREAM of the encoder; a render
    lever cannot be judged by an encode-side metric, because that metric's
    reference moves with the mutation. One lever can therefore never exercise
    the whole panel, and a single-lever criterion silently tests half of it.

    A per-SIDE assertion rather than a per-LEVER one since an#56, which added a
    second render lever. The two are not redundant: measured, `disabled_aa` is
    nearly blind to the descriptor path (96 differing pixels of 12.4M on
    `promote_demo`) and `supersample` reaches that scene hardest of all six
    (-34.8% edge width).
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


# ------------------------------ an#41 adversarial-review hardening


def test_a_mutant_that_breaks_collection_is_not_reported_as_caught():
    """MUTATION: `caught = completed.returncode != 0` in `run_mutants`.

    Not hypothetical: one declared mutant produced UNPARSEABLE Python (its `old`
    ended in a newline and its `new` did not), pytest exited nonzero on a
    collection error, and the sweep printed `CAUGHT ... 1 error` and `16/16`.
    A mutant that cannot be imported has demonstrated nothing about its guard,
    so `ERRORED` is a third answer rather than a flavour of the first.
    """
    from an.bench.mutants import MUTANTS as declared
    from an.bench.mutants import Mutant, verdict_of

    probe = Mutant(
        name="probe_import_time_failure",
        file="an/bench/png.py",
        old='PNG_SIGNATURE: bytes = b"\\x89PNG\\r\\n\\x1a\\n"',
        new="PNG_SIGNATURE: bytes = _undefined_name_at_import_time",
        caught_by="tests/test_bench_png.py",
        why="parses fine, explodes on import, so the guard never runs",
    )
    import an.bench.mutants as module

    original = module.MUTANTS
    module.MUTANTS = (probe,)
    try:
        results = run_mutants()
    finally:
        module.MUTANTS = original
    assert verdict_of(results[0]) == "ERRORED"
    assert results[0]["caught"] is False
    assert "ERRORED" in format_results(results)
    assert "0/1 caught" in format_results(results)
    assert declared, "the real registry survived the swap"


def test_every_declared_mutant_produces_parseable_python():
    """MUTATION: declare a mutant whose `new` breaks indentation.

    Checked at declaration time, for free, because the alternative is finding
    out from a sweep that reports 16/16.
    """
    from pathlib import Path

    from an.bench.paths import repo_root

    root = repo_root()
    for mutant in MUTANTS:
        if not mutant.file.endswith(".py"):
            # Gated the same way `check_sites` is, and for the same reason: the
            # registry reaches `runtime.js` and `index.html` since an#58, and
            # those are exactly the files where a pixel-affecting mutation hides.
            # `compile()` refuses them, so an unconditional check here would make
            # a renderer guard unregisterable rather than unparseable.
            continue
        source = (root / mutant.file).read_text(encoding="utf-8")
        compile(source.replace(mutant.old, mutant.new, 1), mutant.file, "exec")


@pytest.mark.browser
@pytest.mark.ffmpeg
def test_the_aa_lever_can_prove_it_applied():
    """MUTATION: `verify_row=None` for `disabled_aa`, as it was.

    The encode lever's fingerprint was in the row already (`x264_argv`); this one
    had NONE, so `assert not report["mutation_may_not_have_applied"]` asserted
    nothing for it and a lever that silently failed to take would have read as an
    instrument that could not see it. The row now records a digest of the staged
    runtime — provenance, not a comparability key, because the runtime is the
    code under test.
    """
    from an.bench.corpus import DFLT_FIXTURES
    from an.bench.environment import runtime_sha256
    from an.bench.run import run_bench

    one = {"aa_probe": DFLT_FIXTURES["aa_probe"]}
    lever = LEVERS["disabled_aa"]
    assert lever.verify_row is not None

    baseline = run_bench(scenes=one, write=False)
    assert (
        baseline["provenance"]["environment"]["render_side"]["runtime_sha256"]
        == runtime_sha256()
    )
    with pytest.raises(MutationError, match="did not reach the render"):
        lever.verify_row(baseline)

    row = mutated_row("disabled_aa", scenes=one)
    lever.verify_row(row)
    assert (
        compare(baseline, row, mutation="disabled_aa")["mutation_may_not_have_applied"]
        == []
    )


@pytest.mark.parametrize("mutation", MUTATIONS)
def test_an_unpulled_lever_is_reported_rather_than_read_as_blindness(mutation):
    """MUTATION: drop the direct `MUTATION_TOUCHES` probe in `compare`.

    Comparing a row against ITSELF is a lever that provably did not apply. Both
    levers must say so — and before the probe read the touched paths directly,
    the AA lever's key was never scanned at all (it is provenance, not a
    comparability key), so it was reported as unapplied on every run including
    the ones where it had applied.
    """
    import sys

    sys.path.insert(0, "tests")
    from test_bench_compare import _row

    report = compare(_row(), _row(), mutation=mutation)
    assert report["mutation_may_not_have_applied"], (
        f"{mutation}: a row compared against itself must report its lever as "
        "possibly unapplied"
    )


def test_a_non_python_mutant_is_still_checked_for_everything_but_syntax():
    """MUTATION: gate `check_sites`'s uniqueness check on `.py` too.

    The `.py` gate excuses **one** check — `compile()` — for files Python cannot
    parse. It must not excuse the others: a `runtime.js` mutant whose text is
    absent, or occurs twice, or is a no-op, or names a `caught_by` that does not
    exist, is exactly as useless as a Python one, and is now the more likely
    kind because those files are edited by hand.
    """
    from an.bench.mutants import Mutant, check_sites

    js = [m for m in MUTANTS if not m.file.endswith(".py")]
    assert js, (
        "no non-Python mutant is registered, so this test asserts nothing — "
        "delete it or register the renderer guard it exists for"
    )

    import an.bench.mutants as mutants_mod

    original = mutants_mod.MUTANTS
    victim = js[0]
    try:
        mutants_mod.MUTANTS = (
            Mutant(
                name="synthetic-absent",
                file=victim.file,
                old="a string that is certainly not in the runtime",
                new="nor is this",
                caught_by=victim.caught_by,
                why="synthetic",
            ),
        )
        problems = check_sites()
        assert any("occurs 0 times" in p for p in problems), problems
    finally:
        mutants_mod.MUTANTS = original


# ------------------------------------ an#67: a killed sweep must not leave one


#: The mutated spelling used by the interruption fixtures. Plausible on
#: purpose — that is the property that makes a leftover dangerous.
_VICTIM_OLD = 'VALUE = "original"'
_VICTIM_NEW = 'VALUE = "mutated"'
#: The checkout these tests belong to, handed to every child process so a
#: bare `import an` cannot silently resolve to another one.
REPO_ROOT = str(Path(__file__).resolve().parents[1])

_VICTIM_ORIGINAL = _VICTIM_OLD + "\n"
_VICTIM_MUTATED = _VICTIM_NEW + "\n"
#: How long the parent waits for the child to write the mutation / to die.
_INTERRUPT_TIMEOUT_S = 60.0
#: How long the fixture's guard file blocks for. Long enough that the kill
#: always lands mid-run (the parent signals within ~0.1 s of the mutation
#: appearing), short enough that a leaked grandchild cannot outlive the suite.
_GUARD_SLEEP_S = 30


def _victim_mutant(**overrides):
    """One synthetic mutant over a throwaway tree, so no real file is touched."""
    from an.bench.mutants import Mutant

    fields = dict(
        name="victim",
        file="victim.py",
        old=_VICTIM_OLD,
        new=_VICTIM_NEW,
        caught_by="test_victim.py",
        why="a fixture, not a claim about any guard",
    )
    fields.update(overrides)
    return Mutant(**fields)


def _victim_tree(tmp_path, *, mutated: bool = False):
    """A minimal root `run_mutants` can operate on: one victim, one slow guard."""
    (tmp_path / "victim.py").write_text(
        _VICTIM_MUTATED if mutated else _VICTIM_ORIGINAL, encoding="utf-8"
    )
    (tmp_path / "test_victim.py").write_text(
        f"import time\n\n\ndef test_slow():\n    time.sleep({_GUARD_SLEEP_S})\n",
        encoding="utf-8",
    )
    return tmp_path


@pytest.mark.parametrize("signame", ["SIGTERM", "SIGINT", "SIGHUP"])
def test_a_killed_sweep_restores_the_tree(tmp_path, signame):
    """MUTATION: drop `restore_on_termination` from `run_mutants` (SIGTERM only).

    Ctrl-C was always fine — SIGINT raises `KeyboardInterrupt` and the `finally`
    runs. SIGTERM stops the interpreter without raising, so nothing ran and the
    mutated file stayed on disk; a real `pkill -f bench-mutants` left one there
    (an#67). Both are asserted, because the SIGINT case is what pins that the
    fix for the other one did not break the path that already worked.

    A real signal to a real process, not a simulated one: what is being tested
    is precisely whether the interpreter's teardown reaches the restore.
    """
    import os
    import signal
    import subprocess
    import sys
    import time

    if sys.platform == "win32":
        pytest.skip(
            "Windows has no POSIX signal delivery: SIGTERM terminates without "
            "running handlers and os.kill refuses SIGINT, so this asserts "
            "nothing there"
        )

    root = _victim_tree(tmp_path)
    driver = tmp_path / "_drive.py"
    # `.format`, not an f-string: an f-string containing a backslash is 3.12+
    # syntax and this package supports 3.10, where the whole FILE would then
    # fail to parse — the collection-time failure `test_browser_gate.py` exists
    # to keep out of this suite.
    driver.write_text(
        (
            "import sys\n"
            "from pathlib import Path\n"
            # KEEP THIS LINE FIRST. Without it the child, whose `cwd` is a
            # tmp dir, resolves `import an` through the EDITABLE INSTALL —
            # so what this test measures is decided by the environment
            # rather than by the code. The trap is that the editable path
            # *happens* to be the tree under test for anyone working
            # directly in it, and for CI, which installs the checkout it
            # tests; it is wrong for a clone or a worktree, which is what
            # every parallel-agent setup on this repo uses. Measured:
            # dropping SIGHUP from `RESTORE_ON_SIGNALS` left all three
            # cases green from a clone and red from the primary checkout —
            # the same mutation, two answers. So the person for whom this
            # line looks redundant is exactly the person it is invisible
            # to, and they are the one most likely to tidy it away.
            f"sys.path.insert(0, {REPO_ROOT!r})\n"
            "from an.bench import mutants as M\n"
            "\n"
            "M.MUTANTS = (M.Mutant(name='victim', file='victim.py', "
            "old={old!r}, new={new!r}, caught_by='test_victim.py', "
            "why='a fixture'),)\n"
            "M.run_mutants(root=Path(sys.argv[1]))\n"
        ).format(old=_VICTIM_OLD, new=_VICTIM_NEW),
        encoding="utf-8",
    )
    victim = root / "victim.py"
    child = subprocess.Popen(
        [sys.executable, str(driver), str(root)],
        cwd=root,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        deadline = time.monotonic() + _INTERRUPT_TIMEOUT_S
        while victim.read_text(encoding="utf-8") != _VICTIM_MUTATED:
            assert child.poll() is None, (
                "the child exited before it mutated anything, so the kill below "
                f"would prove nothing: {child.communicate()}"
            )
            assert time.monotonic() < deadline, "the child never wrote the mutation"
            time.sleep(0.05)
        os.kill(child.pid, getattr(signal, signame))
        child.communicate(timeout=_INTERRUPT_TIMEOUT_S)
    finally:
        if child.poll() is None:  # pragma: no cover - only on a failing run
            child.kill()
            child.communicate()
    assert victim.read_text(encoding="utf-8") == _VICTIM_ORIGINAL, (
        f"{signame} left the tree mutated — this is the an#67 defect, and the "
        "mutation is plausible enough to be committed unnoticed"
    )


@pytest.mark.parametrize("signame", ["SIGINT", "SIGTERM"])
def test_the_cli_says_the_tree_survived_however_it_was_interrupted(tmp_path, signame):
    """MUTATION: narrow the CLI clause back to `except MutantRunInterrupted`.

    `MutantRunInterrupted` IS a `KeyboardInterrupt` — that is how it survives an
    `except Exception` on the way out — but the relationship does not run the
    other way, so a plain Ctrl-C raised the base class and escaped the clause
    entirely. The tree was restored (the `finally` had already run), and the
    operator got a raw traceback ending in `selectors.py` and rc=-2 with no way
    to know that. an#67 names Ctrl-C as the normal way this sweep is stopped, so
    the reassurance was missing on the path people actually take.

    Driven as a real subprocess taking a real signal, because what is asserted
    is the interpreter's exit path — the exit code and the absence of a
    traceback are the whole claim, and neither survives being simulated.
    """
    import os
    import signal
    import subprocess
    import sys
    import time

    from an.bench.mutants import INTERRUPTED_EXIT_CODE

    if sys.platform == "win32":
        pytest.skip("no POSIX signal delivery: see the sibling test's note")

    root = _victim_tree(tmp_path)
    driver = tmp_path / "_drive_cli.py"
    # `.format` for the same 3.10 reason as the sibling driver above.
    driver.write_text(
        (
            "import sys\n"
            "from pathlib import Path\n"
            # KEEP THIS LINE FIRST. Without it the child, whose `cwd` is a
            # tmp dir, resolves `import an` through the EDITABLE INSTALL —
            # so what this test measures is decided by the environment
            # rather than by the code. The trap is that the editable path
            # *happens* to be the tree under test for anyone working
            # directly in it, and for CI, which installs the checkout it
            # tests; it is wrong for a clone or a worktree, which is what
            # every parallel-agent setup on this repo uses. Measured:
            # dropping SIGHUP from `RESTORE_ON_SIGNALS` left all three
            # cases green from a clone and red from the primary checkout —
            # the same mutation, two answers. So the person for whom this
            # line looks redundant is exactly the person it is invisible
            # to, and they are the one most likely to tidy it away.
            f"sys.path.insert(0, {REPO_ROOT!r})\n"
            "from an.bench import mutants as M\n"
            "from an.bench import paths as P\n"
            "import an.tools as T\n"
            "\n"
            "root = Path(sys.argv[1])\n"
            "M.MUTANTS = (M.Mutant(name='victim', file='victim.py', "
            "old={old!r}, new={new!r}, caught_by='test_victim.py', "
            "why='a fixture'),)\n"
            "P.repo_root = lambda: root\n"
            "T.bench_mutants()\n"
        ).format(old=_VICTIM_OLD, new=_VICTIM_NEW),
        encoding="utf-8",
    )
    victim = root / "victim.py"
    child = subprocess.Popen(
        [sys.executable, str(driver), str(root)],
        cwd=root,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        deadline = time.monotonic() + _INTERRUPT_TIMEOUT_S
        while victim.read_text(encoding="utf-8") != _VICTIM_MUTATED:
            assert child.poll() is None, (
                f"the child exited before it mutated anything: {child.communicate()}"
            )
            assert time.monotonic() < deadline, "the child never wrote the mutation"
            time.sleep(0.05)
        os.kill(child.pid, getattr(signal, signame))
        out, err = child.communicate(timeout=_INTERRUPT_TIMEOUT_S)
    finally:
        if child.poll() is None:  # pragma: no cover - only on a failing run
            child.kill()
            child.communicate()

    assert victim.read_text(encoding="utf-8") == _VICTIM_ORIGINAL
    assert "the tree was restored" in out, (
        f"{signame} left the operator without the one sentence that answers "
        f"'is a mutated file still on disk?'\nstdout={out!r}\nstderr={err!r}"
    )
    assert "Traceback" not in err, err
    assert child.returncode == INTERRUPTED_EXIT_CODE, (
        f"expected {INTERRUPTED_EXIT_CODE}, got {child.returncode} — a negative "
        "code means the interpreter died on the signal rather than exiting"
    )


def test_a_file_left_mutated_is_reported_as_an_interrupted_run(tmp_path, monkeypatch):
    """MUTATION: fold the leftover branch back into the `occurs 0 times` message.

    SIGKILL cannot be handled, so the next run's report is the only thing left.
    "occurs 0 times" sends the reader looking for a refactor that never
    happened; the message has to name the kill, the file and the repair.
    """
    from an.bench.mutants import check_sites

    _victim_tree(tmp_path, mutated=True)
    monkeypatch.setattr("an.bench.mutants.MUTANTS", (_victim_mutant(),))

    problems = check_sites(tmp_path)

    assert len(problems) == 1, problems
    (problem,) = problems
    assert "LEFT MUTATED" in problem
    assert "victim.py" in problem
    assert _VICTIM_OLD in problem, "the repair must be spelled out"
    assert "occurs 0 times" not in problem


def test_a_moved_site_is_not_reported_as_an_interrupted_run(tmp_path, monkeypatch):
    """The other direction: the two diagnoses must stay distinct.

    MUTATION: report every absent site as a leftover.

    A refactor that moved the code and a kill that left the mutation are
    different failures with different repairs, and calling the first one a
    leftover would send a reader to `git checkout` a file they had just
    legitimately edited.
    """
    from an.bench.mutants import check_sites

    root = _victim_tree(tmp_path)
    (root / "victim.py").write_text('VALUE = "moved on"\n', encoding="utf-8")
    monkeypatch.setattr("an.bench.mutants.MUTANTS", (_victim_mutant(),))

    (problem,) = check_sites(root)

    assert "occurs 0 times" in problem
    assert "LEFT MUTATED" not in problem


def test_every_declared_mutant_is_recoverable_from_a_kill():
    """MUTATION: re-anchor `mux_argv_...` so its `new` merely prefixes its `old`.

    The leftover branch in `check_sites` recognises "the mutation is present and
    the original is gone", which quietly assumes every substitution REMOVES its
    own `old` text. One declared mutant did not: `mux_argv_is_checked_by_subset_
    not_equality` inserted two flags *before* the argv lines it matched, so the
    mutated file still contained `old`, neither branch fired, and `check_sites`
    returned `[]` on a tree whose shipped ffmpeg argv carried `-tune animation`.
    A subsequent `an bench-mutants` then read that file as its `original` and
    restored to it — the instrument laundering the damage into the baseline
    while reporting health.

    Asserted over the WHOLE registry rather than over the one mutant that had
    the bug, because the property is what matters and a new mutant can rejoin
    the blind set for free. Run against the real files, in memory: it is the
    real `old`/`new` pair against the real source that decides this, not the
    declaration read on its own.
    """
    from an.bench.paths import repo_root

    base = repo_root()
    blind = []
    for mutant in MUTANTS:
        source = (base / mutant.file).read_text(encoding="utf-8")
        mutated = source.replace(mutant.old, mutant.new, 1)
        if mutated.count(mutant.old) != 0 or mutant.new not in mutated:
            blind.append(mutant.name)

    assert not blind, (
        f"{blind} survive their own mutation: applying one leaves its `old` "
        "text on disk, so a tree left mutated by a SIGKILL is invisible to "
        "`check_sites` and the next sweep restores to the leftover"
    )


def test_a_mutant_that_hides_its_own_leftover_is_refused(tmp_path, monkeypatch):
    """MUTATION: delete the `mutant.old in mutated` branch from `check_sites`.

    The registry-wide test above pins today's declarations; this pins the
    *check* that keeps a future one honest — without it, the property is a fact
    about the current table rather than an invariant, and the next mutant to
    extend its `old` instead of replacing it silently goes blind again.
    """
    from an.bench.mutants import check_sites

    _victim_tree(tmp_path)
    monkeypatch.setattr(
        "an.bench.mutants.MUTANTS",
        (_victim_mutant(new=f"PREFIX = 1\n{_VICTIM_OLD}"),),
    )

    problems = check_sites(tmp_path)

    assert len(problems) == 1, problems
    (problem,) = problems
    assert "leaves its own `old` text" in problem
    assert "SIGKILL" in problem, "the message must name what it would cost"
    assert "LEFT MUTATED" not in problem, "the tree here is pristine, not dirty"


def test_an_inherited_ignore_is_left_ignored():
    """MUTATION: install the raising handler unconditionally.

    `nohup an bench-mutants > sweep.log &` leaves SIGHUP ignored so that closing
    the terminal does not stop the sweep — the operator has already answered
    this question, deliberately. Taking the signal there converts a run someone
    detached to protect into a partial sweep exiting 130. Nothing is risked by
    standing aside: an ignored signal is never delivered, so it cannot stop the
    interpreter mid-mutation in the first place.
    """
    import signal

    if not hasattr(signal, "SIGHUP"):  # pragma: no cover - Windows
        pytest.skip("no SIGHUP on this platform")

    from an.bench.mutants import restore_on_termination

    before = signal.getsignal(signal.SIGHUP)
    signal.signal(signal.SIGHUP, signal.SIG_IGN)
    try:
        with restore_on_termination() as took:
            assert signal.getsignal(signal.SIGHUP) is signal.SIG_IGN, (
                "an inherited ignore was overridden; a detached sweep now dies "
                "when the terminal closes"
            )
            assert signal.SIGHUP not in took, "`took` must report what it took"
        assert signal.getsignal(signal.SIGHUP) is signal.SIG_IGN
    finally:
        signal.signal(signal.SIGHUP, before)


def test_the_signal_boundary_puts_the_previous_handlers_back():
    """MUTATION: drop the `finally` that restores the previous handlers.

    `an.bench.mutants` is importable, and a library that permanently rewires
    SIGTERM for its process is a worse defect than the one this fixes.
    """
    import signal

    from an.bench.mutants import RESTORE_ON_SIGNALS, restore_on_termination

    before = {sig: signal.getsignal(sig) for sig in RESTORE_ON_SIGNALS}
    with restore_on_termination() as took:
        assert set(took) == set(RESTORE_ON_SIGNALS), "nothing was actually taken"
        for sig in took:
            assert signal.getsignal(sig) not in (before[sig], signal.SIG_DFL)
    assert {sig: signal.getsignal(sig) for sig in RESTORE_ON_SIGNALS} == before


def test_the_leftover_message_does_not_claim_more_than_a_text_test_can_prove():
    """MUTATION: restore "This is not declaration rot" to the message.

    `check_sites` reports a leftover when the mutation is present and the
    original is gone. The FALSE-NEGATIVE direction is refused by declaration
    (a mutant whose substitution leaves its own `old` behind). The other
    direction cannot be: **five of the declarations have a `new` that occurs in
    the unmutated file** — three because `new` is a substring of `old`, two
    because the replacement text appears elsewhere — so for those, a refactor
    that moved the site reads exactly like a killed sweep. Telling that reader
    "this is not declaration rot" sends them to `git checkout` away an edit they
    had just made. The message says `git diff` first instead.
    """
    from an.bench.mutants import MUTANTS, _left_mutated_message
    from an.bench.paths import repo_root

    base = repo_root()
    ambiguous = [
        m.name for m in MUTANTS if m.new in (base / m.file).read_text(encoding="utf-8")
    ]
    assert ambiguous, (
        "if no declaration has this shape any more the caveat can go — but "
        "delete it deliberately, with this test"
    )

    message = _left_mutated_message(MUTANTS[0])

    assert "git diff" in message, "the reader must be told to look before restoring"
    assert "not declaration rot" not in message, (
        f"the text test cannot prove that, and is wrong for {ambiguous}"
    )


def test_the_restore_runs_with_the_terminating_signals_blocked(tmp_path, monkeypatch):
    """MUTATION: drop `_signals_deferred()` from `_run_one`'s finally.

    The handler `restore_on_termination` installs RAISES, and the restore is a
    `write_text` — mode "w", which truncates at open and flushes at close. A
    signal delivered in that window raises out of the restore and leaves the
    source file EMPTY, which is strictly worse than the leftover the whole
    mechanism exists to prevent. The window cannot be hit deterministically, so
    the block is asserted at the exact call site instead.
    """
    import signal
    from pathlib import Path as _Path

    from an.bench import mutants as mutants_mod

    if not hasattr(signal, "pthread_sigmask"):  # pragma: no cover - Windows
        pytest.skip("no pthread_sigmask: the restore has only its `finally` here")

    root = _victim_tree(tmp_path)
    (root / "test_victim.py").write_text(
        "def test_x():\n    assert True\n", encoding="utf-8"
    )
    mutant = _victim_mutant()
    monkeypatch.setattr(mutants_mod, "MUTANTS", (mutant,))

    real_write = _Path.write_text
    blocked = []

    def spy(self, data, *args, **kwargs):
        if self.name == "victim.py" and data == _VICTIM_ORIGINAL:
            blocked.append(signal.pthread_sigmask(signal.SIG_BLOCK, set()))
        return real_write(self, data, *args, **kwargs)

    monkeypatch.setattr(_Path, "write_text", spy)
    mutants_mod._run_one(root, mutant)
    monkeypatch.setattr(_Path, "write_text", real_write)

    assert blocked, "the restore never ran"
    assert set(mutants_mod.RESTORE_ON_SIGNALS) <= blocked[0]
    assert (root / "victim.py").read_text(encoding="utf-8") == _VICTIM_ORIGINAL
