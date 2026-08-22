"""The guard mutants: "I mutation-tested it" as a runnable artifact, not a claim.

Wave 1 shipped three guards that stayed green when the bug they guarded was
reintroduced — one flagged the sentence *explaining* a deprecation, one
exonerated everything via neighbouring prose, and one keyed on a string the fix
itself removed. Wave 2's own an#36 sweep lost five mutants, two of them to
guards that asserted a table's *contents* rather than the check that reads it.

Every commit in this wave has said "N mutants, all caught". That sentence is
unfalsifiable after the fact: the mutations lived in a scratch script and were
thrown away. This module is the correction — each one is **declared data**, so
a future reader can re-run the proof instead of trusting the commit message.

Three properties the declaration is shaped to have:

**Each mutant names the guard it must break.** A mutation nobody expected to be
caught is a fact about the code; a mutation with a named catcher is a claim
about a *test*, and that is what is worth pinning.

**The whole file runs, never a ``-k`` filter.** A filter that happens to exclude
the catching test reports "not caught" and sends the reader to write a test
that already exists.

**The ``old`` text is pinned exactly.** A mutant whose source text has moved is
a mutant that silently stops proving anything, so
``tests/test_bench_mutation.py`` asserts every site still exists — cheaply, in
the default CI leg, with no pytest subprocesses at all. The full sweep is
``an bench-mutants``: it is a deliberate act, and it takes about half a minute.
"""

from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

#: pytest flags for a mutant run. No `-k`: see the module docstring. No
#: `--cache-provider` so a failed mutant cannot leave a `--lf` trail behind.
PYTEST_ARGS: tuple[str, ...] = (
    "-q",
    "--no-header",
    "--tb=no",
    "-p",
    "no:cacheprovider",
)


class MutantError(RuntimeError):
    """A declared mutant no longer applies, or the tree was left dirty."""


@dataclass(frozen=True, slots=True)
class Mutant:
    """One deliberate defect, and the guard that must notice it."""

    name: str
    #: Repo-relative path of the file to break.
    file: str
    #: Exact source text to replace. Must occur **exactly once**.
    old: str
    new: str
    #: The test file that must go red. The whole file runs.
    caught_by: str
    why: str


#: A representative sweep rather than an exhaustive one, chosen so each entry
#: pins a *different* class of failure: a silently widened comparison, a guard
#: that reads the same table twice, a refusal that stops refusing, an
#: unknown-is-not-zero substitution, a decoder that only works on its own
#: output, and an instrument that goes blind without saying so.
MUTANTS: tuple[Mutant, ...] = (
    Mutant(
        name="png_paeth_tiebreak",
        file="an/bench/png.py",
        old="if (pa <= pb and pa <= pc) else (b if pb <= pc else c)",
        new="if (pa <= pb and pa <= pc) else (b if pb < pc else c)",
        caught_by="tests/test_bench_png.py",
        why=(
            "the Paeth predictor's tie-break. Wrong, it still decodes this "
            "module's own filter-0 output perfectly and corrupts every real "
            "Chromium frame — the exact asymmetry that makes an encoder "
            "validating its own decoder worthless."
        ),
    ),
    Mutant(
        name="png_first_idat_only",
        file="an/bench/png.py",
        old="            idat.append(payload)",
        new="            idat = [payload]",
        caught_by="tests/test_bench_png.py",
        why=(
            "Chromium splits the stream: a real frame has 2-9 IDAT chunks and "
            "our own output has one, so a first-chunk-only reader passes its own "
            "tests and fails on everything else."
        ),
    ),
    Mutant(
        name="png_no_write_verification",
        file="an/bench/png.py",
        old="    if not np.array_equal(decode_png(out.read_bytes()), np.asarray(rgb)):",
        new="    if False:",
        caught_by="tests/test_bench_png.py",
        why=(
            "the only thing between a bug in this module's own encoder and a "
            "committed golden that silently disagrees with the frame it was "
            "blessed from."
        ),
    ),
    Mutant(
        name="golden_criterion_becomes_file_bytes",
        file="an/bench/golden.py",
        old='    digest.update(f"{arr.dtype.str}:{arr.shape}|".encode("ascii"))',
        new="    pass",
        caught_by="tests/test_bench_golden.py",
        why=(
            "`ndarray.tobytes()` carries no shape, so a transposed frame hashes "
            "identically and satisfies the criterion an#38 literally states."
        ),
    ),
    Mutant(
        name="golden_blesses_a_blank_reason",
        file="an/bench/golden.py",
        old="    if not reason.strip():",
        new="    if reason is None:",
        caught_by="tests/test_bench_golden.py",
        why=(
            "a re-bless with no recorded reason is the same failure as a silently "
            "widened threshold — the named failure mode this wave exists to end."
        ),
    ),
    Mutant(
        name="golden_blesses_an_identical_pair",
        file="an/bench/golden.py",
        old="            if np.array_equal(decoded[i], decoded[j]):",
        new="            if False:",
        caught_by="tests/test_bench_golden.py",
        why=(
            "measured on `promote_demo`: frame 0 and the duration/2 frame differ "
            "by ZERO pixels, so the obvious second time blesses one picture twice "
            "and the second golden tests nothing forever after."
        ),
    ),
    Mutant(
        name="compare_gains_a_tolerance_band",
        file="an/bench/compare.py",
        old='    if before == after:\n        return "no_change"',
        new='    if abs(float(before) - float(after)) < 1e-9:\n        return "no_change"',
        caught_by="tests/test_bench_compare.py",
        why=(
            "two consecutive runs on one machine are bit-identical, so a band can "
            "only ever hide a true movement."
        ),
    ),
    Mutant(
        name="compare_refuses_on_an_absent_key",
        file="an/bench/compare.py",
        old="        elif b is _ABSENT or a is _ABSENT:",
        new="        elif False:",
        caught_by="tests/test_bench_compare.py",
        why=(
            "the ledger grows additively, so treating absence as difference makes "
            "every future field retroactively destroy comparability with every row "
            "already written."
        ),
    ),
    Mutant(
        name="compare_counts_metrics_not_families",
        file="an/bench/compare.py",
        old='        block["family_count"] = len(families)',
        new='        block["family_count"] = sum(len(v) for v in families.values())',
        caught_by="tests/test_bench_compare.py",
        why=(
            "counting bare metrics is satisfiable by shipping one signal under "
            "three names, which is exactly what family A's three edge metrics "
            "would do."
        ),
    ),
    Mutant(
        name="compare_exempts_the_whole_environment",
        file="an/bench/compare.py",
        old="        touched = {t.label for t in MUTATION_TOUCHES.get(mutation, ())}",
        new='        touched = {i["key"] for i in common + render + encode}',
        caught_by="tests/test_bench_compare.py",
        why=(
            "the knob the lever pulls is the independent variable; the ISA is not. "
            "A blanket exemption lets a row from another machine in through the "
            "same door."
        ),
    ),
    Mutant(
        name="compare_exempts_by_path_not_by_value",
        file="an/bench/registry.py",
        old="        if self.differs_only_in is None:\n            return True",
        new="        if True:\n            return True",
        caught_by="tests/test_bench_compare.py",
        why=(
            "`x264_argv` is the WHOLE encode command, so exempting the path "
            "exempts every flag in it. A `-preset medium` -> `-preset veryslow` "
            "change moves every encode-side number and rode in as 'the lever "
            "moved it — expected'. The exemption must match the change the "
            "lever actually makes."
        ),
    ),
    Mutant(
        name="ledger_substitutes_zero_for_unknown",
        file="an/bench/ledger.py",
        old='        if self.state == "measured":\n            if self.value is None:',
        new='        if self.state == "measured":\n            if False:',
        caught_by="tests/test_bench_ledger_schema.py",
        why=(
            "a substituted number — 0.0 especially — is read downstream as a "
            "measurement, which is the unknown-is-not-zero failure the whole "
            "schema exists to prevent."
        ),
    ),
    Mutant(
        name="ledger_lets_a_tripwire_vanish",
        file="an/bench/ledger.py",
        old="    absent_tw = sorted(set(TRIPWIRES) - set(tripwires))",
        new="    absent_tw = []",
        caught_by="tests/test_bench_ledger_schema.py",
        why=(
            "a change detector that quietly stopped being computed reads exactly "
            "like one that fired and found nothing."
        ),
    ),
    Mutant(
        name="registry_counts_a_tautology",
        file="an/bench/registry.py",
        old='        if self.expect in ("no_change", "not_applicable") and self.counts:',
        new="        if False:",
        caught_by="tests/test_bench_ledger_schema.py",
        why=(
            "'no change by construction' is a tautology; counting it lets any "
            "pre-encode statistic pad the witness count for free."
        ),
    ),
    Mutant(
        name="golden_fabricates_a_zero_pixel_count",
        file="an/bench/golden.py",
        old='"changed_px": max((int(f["changed_px"]) for f in compared), default=None),',
        new='"changed_px": max((int(f["changed_px"] or 0) for f in frames), default=0),',
        caught_by="tests/test_bench_golden.py",
        why=(
            "a shape mismatch has no per-pixel comparison to count, and turning "
            "that into 0 printed 'GOLDEN MISMATCH: 0 px changed' — a fabricated "
            "number in the one schema whose whole premise is that unknown is not "
            "zero."
        ),
    ),
    Mutant(
        name="compare_scope_absence_fails_open",
        file="an/bench/compare.py",
        old="        if scope not in env_refusals:",
        new="        if False:",
        caught_by="tests/test_bench_compare.py",
        why=(
            "an absent `comparison_scope` read as 'no refusals apply', so an "
            "encode-side metric from another ISA and another x264 build compared "
            "cleanly and reported a regression."
        ),
    ),
    Mutant(
        name="strict_passes_a_comparison_that_compared_nothing",
        file="an/tools.py",
        old='            not report.get("answered")',
        new="            False",
        caught_by="tests/test_bench_compare.py",
        why=(
            "the documented CI gate exited 0 on a run in which every scene was "
            "refused, while printing '0 regression(s)' — a zero the compare "
            "module's own docstring calls worse than no number at all."
        ),
    ),
    Mutant(
        name="cli_returns_nothing_to_the_terminal",
        file="an/__main__.py",
        old="        if result is not None:\n            typer.echo(result)",
        new="        pass",
        caught_by="tests/test_cli_dispatch.py",
        why=(
            "typer discards return values and every `an.tools` function returns "
            "its report as a string, so the CLI would run correctly and print "
            "NOTHING — the worst possible failure for a diagnostic tool."
        ),
    ),
    Mutant(
        name="cli_loses_the_signature_that_is_the_command_line",
        file="an/__main__.py",
        old="    @functools.wraps(func)\n    def run(",
        new="    def run(",
        caught_by="tests/test_cli_dispatch.py",
        why=(
            "`inspect.signature` follows `__wrapped__`, and that signature IS the "
            "command line. Without it typer sees `(*args, **kwargs)` and every "
            "flag on all 17 commands disappears at once, while `--help` still "
            "renders."
        ),
    ),
    Mutant(
        name="corpus_reads_shot_order_from_the_directory",
        file="an/bench/corpus.py",
        old='    for shot_id in order:\n        shot_dir = root / f"shot_{shot_id}"',
        new='    for shot_dir in sorted(root.glob(SHOT_DIR_GLOB)):\n        shot_id = shot_dir.name[len("shot_") :]',
        caught_by="tests/test_bench_corpus.py",
        why=(
            "`an/render.py` concatenates in timeline order; a directory sort "
            "agrees only by luck, and when it does not every encode-side metric "
            "pairs one shot's source frames against another's decode."
        ),
    ),
)


def _sites(root: Path) -> dict[str, tuple[Path, str]]:
    """Read every mutant's file once, and check its site exists exactly once."""
    cache: dict[str, tuple[Path, str]] = {}
    for mutant in MUTANTS:
        if mutant.file not in cache:
            path = root / mutant.file
            cache[mutant.file] = (path, path.read_text(encoding="utf-8"))
    return cache


def check_sites(root: Path | None = None) -> list[str]:
    """Problems with the declarations themselves, as a list of sentences.

    Separate from running them because it is nearly free and catches the failure
    that matters most: a refactor moved the code, so a mutant no longer applies
    and has silently stopped proving anything.
    """
    from an.bench.paths import repo_root

    base = root or repo_root()
    cache = _sites(base)
    problems: list[str] = []
    for mutant in MUTANTS:
        _, source = cache[mutant.file]
        count = source.count(mutant.old)
        if count != 1:
            problems.append(
                f"{mutant.name}: its source text occurs {count} times in "
                f"{mutant.file} (needs exactly 1), so applying it would prove "
                "nothing or break something else"
            )
        if mutant.new == mutant.old:
            problems.append(f"{mutant.name}: the mutation is a no-op")
        if not (base / mutant.caught_by).is_file():
            problems.append(f"{mutant.name}: {mutant.caught_by} does not exist")
        if count == 1:
            # A mutant that produces unparseable Python breaks COLLECTION, and
            # a collection error is not a guard catching anything. Checked here,
            # at declaration time and for free, because the alternative is
            # finding out from a sweep that says 16/16.
            try:
                compile(source.replace(mutant.old, mutant.new, 1), mutant.file, "exec")
            except SyntaxError as e:
                problems.append(
                    f"{mutant.name}: applying it makes {mutant.file} unparseable "
                    f"({type(e).__name__}: {e}). A mutant that breaks collection "
                    "proves nothing about its guard."
                )
    return problems


def run_mutants(
    names: Iterable[str] | None = None, *, root: Path | None = None
) -> list[dict]:
    """Apply each mutant, run its whole guard file, restore, and report.

    Restoration is in a ``finally`` and rewrites the ORIGINAL text rather than
    reversing the substitution: a reversal that itself failed would leave the
    tree broken, which is a worse outcome than any mutant surviving.
    """
    from an.bench.paths import repo_root

    base = root or repo_root()
    problems = check_sites(base)
    if problems:
        raise MutantError(
            "the mutant declarations no longer match the source:\n  "
            + "\n  ".join(problems)
        )
    wanted = set(names) if names is not None else None
    results: list[dict] = []
    for mutant in MUTANTS:
        if wanted is not None and mutant.name not in wanted:
            continue
        path = base / mutant.file
        original = path.read_text(encoding="utf-8")
        try:
            path.write_text(
                original.replace(mutant.old, mutant.new, 1), encoding="utf-8"
            )
            completed = subprocess.run(
                [sys.executable, "-m", "pytest", mutant.caught_by, *PYTEST_ARGS],
                cwd=base,
                capture_output=True,
                text=True,
            )
        finally:
            path.write_text(original, encoding="utf-8")
        summary = [
            line
            for line in completed.stdout.splitlines()
            if "passed" in line or "failed" in line or "error" in line
        ]
        last = summary[-1] if summary else ""
        failed = _count(last, "failed")
        errored = _count(last, "error")
        # A mutant that breaks COLLECTION proves nothing. `returncode != 0`
        # alone reads a SyntaxError in the mutated module as "the guard caught
        # it" — and one declared mutant really did produce unparseable Python
        # and really was reported as CAUGHT for nine commits. So the verdict is
        # "at least one test FAILED", and a run that only errored is reported as
        # `errored` rather than folded into either answer.
        results.append(
            {
                "name": mutant.name,
                "file": mutant.file,
                "caught_by": mutant.caught_by,
                "caught": failed > 0,
                "errored": errored > 0 and failed == 0,
                "returncode": completed.returncode,
                "summary": last,
                "why": mutant.why,
            }
        )
    return results


def _count(summary: str, word: str) -> int:
    """``N`` from a pytest summary like ``2 failed, 51 passed in 0.6s``.

    >>> _count("2 failed, 51 passed in 0.57s", "failed")
    2
    >>> _count("53 passed in 0.55s", "failed")
    0
    """
    import re

    match = re.search(rf"(\d+)\s+{word}", summary)
    return int(match.group(1)) if match else 0


def verdict_of(result: dict) -> str:
    """``CAUGHT`` / ``SURVIVED`` / ``ERRORED``, as three separate answers.

    ``ERRORED`` is not a third flavour of caught: a mutant that stops the guard
    file from being collected has demonstrated nothing about the guard.
    """
    if result.get("errored"):
        return "ERRORED"
    return "CAUGHT" if result["caught"] else "SURVIVED"


def format_results(results: list[dict]) -> str:
    """The digest, with the survivors and errors last because they are the finding."""
    order = {"CAUGHT": 0, "SURVIVED": 1, "ERRORED": 2}
    lines = [
        f"{verdict_of(r):8s}  {r['name']:44s} {r['caught_by']:34s} {r['summary']}"
        for r in sorted(results, key=lambda r: order[verdict_of(r)])
    ]
    caught = [r for r in results if verdict_of(r) == "CAUGHT"]
    survivors = [r for r in results if verdict_of(r) == "SURVIVED"]
    errored = [r for r in results if verdict_of(r) == "ERRORED"]
    lines.append("")
    lines.append(f"{len(caught)}/{len(results)} caught")
    if survivors:
        lines.append(
            "\nSURVIVORS — each one is a guard that stays green while the bug it "
            "names is present:\n"
            + "\n".join(f"  {r['name']}: {r['why']}" for r in survivors)
        )
    if errored:
        lines.append(
            "\nERRORED — the mutated file could not be collected, so these prove "
            "NOTHING about their guard. Fix the mutant, not the test:\n"
            + "\n".join(f"  {r['name']}: {r['summary']}" for r in errored)
        )
    return "\n".join(lines)
