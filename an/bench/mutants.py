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

**A killed sweep must not leave the tree mutated** (an#67), and that is two
mechanisms rather than one, because they cover different kills. SIGTERM is
turned into an exception for the duration (:func:`restore_on_termination`) so
the restoring ``finally`` runs; SIGKILL cannot be caught by anything, so
:func:`check_sites` — which every sweep runs first, and which the default CI leg
runs too — recognises a file whose mutated text is present and whose original is
gone, and reports it as an interrupted run with the exact repair. The recovery
path is the load-bearing half: what makes a leftover dangerous is that every
mutation here is chosen to be *plausible*, so the tree compiles, renders and
stays green apart from one test, and a developer can commit it without noticing.
That recovery reads "the mutation is present and the original is gone", which is
an assumption about the DECLARATION — so ``check_sites`` also refuses a mutant
whose substitution leaves its own ``old`` text behind, because such a mutant is
invisible to the recovery and a later sweep would restore *to* the leftover.
"""

from __future__ import annotations

import contextlib
import os
import shutil
import signal
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Iterator

#: pytest flags for a mutant run. No `-k`: see the module docstring. No
#: `--cache-provider` so a failed mutant cannot leave a `--lf` trail behind.
PYTEST_ARGS: tuple[str, ...] = (
    "-q",
    "--no-header",
    "--tb=no",
    "-p",
    "no:cacheprovider",
)


#: Terminating signals turned into an exception for the duration of a sweep, so
#: the restoring ``finally`` runs. SIGINT is deliberately absent: it already
#: raises ``KeyboardInterrupt``, and re-handling it would only replace a working
#: mechanism. Built from what the platform actually has — Windows has no SIGHUP,
#: and asking for one is an ``AttributeError`` at import time.
RESTORE_ON_SIGNALS: tuple[int, ...] = tuple(
    sig
    for sig in (getattr(signal, name, None) for name in ("SIGTERM", "SIGHUP"))
    if sig is not None
)


#: What the CLI exits with after an interrupted sweep: the shell convention of
#: 128 + SIGINT. Nonzero, and distinguishable from the 1 a surviving mutant
#: gives — "you stopped it" and "a guard is decoration" are different answers.
INTERRUPTED_EXIT_CODE: int = 130


#: Left out of a sweep's throwaway tree. Caches and build output only —
#: **`.git` IS copied**, because six of the thirteen guard files call
#: `dirty_paths` or `repo_root`, and a tree with no history fails them for the
#: wrong reason. Measured at 0.26 s for this repository, once per sweep.
SWEEP_COPY_IGNORE: tuple[str, ...] = (
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "node_modules",
    "out",
)


@contextlib.contextmanager
def sweep_tree(source: Path) -> Iterator[Path]:
    """A throwaway copy of `source` for a sweep to do its damage in.

    **A sweep mutates real source files, and every mutation here is chosen to be
    plausible** — it compiles, it renders, and the suite stays green apart from
    the one test that names it. So a concurrent reader of the working tree does
    not get an error, it gets a believable wrong answer. That reader is not
    hypothetical: `test_a_representative_mutant_is_really_caught` runs in the
    DEFAULT leg, so a plain ``pytest -q`` mutates `an/bench/compare.py` for a few
    seconds, and a second suite, an editor re-indexing, a `git status` from
    another shell or a lint job sees it (an#124 — three pytest processes against
    one checkout, and which of them owned the file's contents was unanswerable).

    Copying is what makes that structurally impossible rather than merely
    coordinated. A lock would order the *writers*; it cannot reach a reader that
    never took it.

    It also retires an#67's hazard for this path: a sweep killed by SIGKILL can
    now only leave a mutated file inside a temp directory that nothing reads.
    """
    with tempfile.TemporaryDirectory(prefix="an-mutant-sweep-") as tmp:
        dest = Path(tmp) / source.name
        shutil.copytree(
            source,
            dest,
            ignore=shutil.ignore_patterns(*SWEEP_COPY_IGNORE),
            symlinks=True,
        )
        yield dest


class MutantError(RuntimeError):
    """A declared mutant no longer applies, or the tree was left dirty."""


class MutantRunInterrupted(KeyboardInterrupt):
    """A terminating signal arrived mid-sweep, raised so the restore can run.

    Derived from ``KeyboardInterrupt`` — a ``BaseException`` — rather than from
    ``Exception``, on purpose: an ``except Exception`` anywhere between here and
    the top would swallow it and the sweep would carry on with a mutated file on
    disk, which is the outcome the whole mechanism exists to prevent.
    """


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
        name="compare_trusts_an_edited_prediction",
        file="an/bench/compare.py",
        old="    if not isinstance(inline, dict) or not isinstance(declared, dict):\n        return []",
        new="    if True:\n        return []",
        caught_by="tests/test_bench_compare.py",
        why=(
            "the prediction IS the criterion, and it is read from the after "
            "row's inline block alone. Flipping one `expect` turns `contrary` "
            "into `as_declared` with nothing else in the report moving — the "
            "cheapest possible way to fake a caught mutation."
        ),
    ),
    Mutant(
        name="compare_lets_a_row_forge_its_own_scope",
        file="an/bench/compare.py",
        old='    "comparison_scope",\n    "reference",',
        new='    "reference",',
        caught_by="tests/test_bench_compare.py",
        why=(
            "`comparison_scope` decides whether a metric may be compared ACROSS "
            "MACHINES, and `compare` reads the row's INLINE copy. Editing that "
            "one word compared an encode-side metric across a different ISA "
            "with no refusal — the single invariant this module exists to hold, "
            "defeated from inside the row."
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
    # ---------------------------------------------------------------- an#54
    Mutant(
        name="reshape_checks_divisibility_not_shape",
        file="an/bench/imageio.py",
        old="    if frames is not None and len(buf) != per_frame * frames:",
        new="    if False:",
        caught_by="tests/test_bench_shape_guard.py",
        why=(
            "a k-times supersample makes the decoded buffer exactly k**2 "
            "larger, so a divisibility check ALWAYS passes and family A is "
            "computed over k**2 as many scrambled frames — plausibly, because "
            "at k=2 most horizontal runs survive the wrong reshape."
        ),
    ),
    Mutant(
        name="bench_measures_a_supersampled_render",
        file="an/bench/run.py",
        old="        if sizes != {capture.resolution}:",
        new="        if False:",
        caught_by="tests/test_bench_shape_guard.py",
        why=(
            "`capture.resolution` comes from the staged scene's meta and never "
            "from a file, so without an independent read of the PNGs' own "
            "IHDRs nothing in the pipeline ever compares the declared size to "
            "the size on disk."
        ),
    ),
    Mutant(
        name="png_dimensions_trusts_a_non_ihdr_first_chunk",
        file="an/bench/png.py",
        old='    if data[_IHDR_TAG] != b"IHDR":',
        new="    if False:",
        caught_by="tests/test_bench_png.py",
        why=(
            "without the tag check the four bytes that happen to sit at offset "
            "16 are returned as a resolution — a plausible number fed straight "
            "into the shape guard, which is the failure class an#54 closes."
        ),
    ),
    Mutant(
        name="read_png_dimensions_reads_the_whole_file",
        file="an/bench/png.py",
        old="        return png_dimensions(handle.read(PNG_HEADER_BYTES))",
        new="        return png_dimensions(handle.read())",
        caught_by="tests/test_bench_png.py",
        why=(
            "the answer stays right and the cost stops being free: the bench "
            "reads one of these per frame of every shot, and a 1080p frame is "
            "megabytes against a 24-byte header."
        ),
    ),
    Mutant(
        name="strict_exits_zero_on_a_row_it_cannot_read",
        file="an/tools.py",
        old="        if strict:\n            print(refusal)",
        new="        if False:\n            print(refusal)",
        caught_by="tests/test_bench_compare.py",
        why=(
            "the documented CI gate exited 0 on an unreadable schema_version "
            "or an undeclared --mutation — precisely the state a `--strict "
            "--mutation supersample` run is in before the lever is registered. "
            "Same class an#51 closed for the refusal path."
        ),
    ),
    Mutant(
        name="latest_rows_orders_by_filename",
        file="an/bench/compare.py",
        old="    return sorted(rows, key=key)[-count:]",
        new="    return sorted(rows, key=lambda p: p.name)[-count:]",
        caught_by="tests/test_bench_compare.py",
        why=(
            "filenames are <date>-<sha7>.json, so within one date the order is "
            "sha HEX order. A re-baseline and its after-run on the same day "
            "swap silently when the after-commit's sha sorts lower, and every "
            "improvement is then reported as a regression."
        ),
    ),
    Mutant(
        name="compare_hides_that_a_row_was_blessed",
        file="an/bench/compare.py",
        old='            "blessed_scenes": sorted(after["provenance"].get("blessed") or ()),',
        new='            "blessed_scenes": [],',
        caught_by="tests/test_bench_compare.py",
        why=(
            "a bless run gates family B `blessed_this_run`, and "
            "`format_comparison` skips `unchanged` entries — so family B "
            "vanishes from the table entirely. 'Family B agreed' and 'family B "
            "was never asked' are the same blank space."
        ),
    ),
    Mutant(
        name="capture_inherits_the_previous_renders_shots",
        file="an/bench/capture.py",
        old='IGNORED_RELPATHS_ON_COPY: tuple[str, ...] = ("artifacts/shots",)',
        new="IGNORED_RELPATHS_ON_COPY: tuple[str, ...] = ()",
        caught_by="tests/test_bench_corpus.py",
        why=(
            "`mall['shots']` is `<project>/artifacts/shots`, and it is "
            "gitignored — so a previous render's per-shot mp4s cross into every "
            "bench run on a developer machine and on no clean checkout, in the "
            "module whose docstring is 'do not inherit a stale render'."
        ),
    ),
    Mutant(
        name="capture_excludes_shots_by_basename_at_any_depth",
        file="an/bench/capture.py",
        old="            n for n in names if prefix + n in IGNORED_RELPATHS_ON_COPY",
        new=(
            "            n\n"
            "            for n in names\n"
            '            if n in {p.rsplit("/", 1)[-1] '
            "for p in IGNORED_RELPATHS_ON_COPY}"
        ),
        caught_by="tests/test_bench_corpus.py",
        why=(
            "the obvious `shutil.ignore_patterns('shots')` spelling, restated. "
            "It fnmatches BASENAMES against the names in every directory, so it "
            "also deletes a character rig's `assets/.../shots` — and the other "
            "obvious spelling, `'artifacts/shots'` as a pattern, matches "
            "NOTHING, because no name contains a separator. Both fail silently."
        ),
    ),
    Mutant(
        name="bless_names_its_row_after_the_tree_it_did_not_leave",
        file="an/bench/run.py",
        old="    return git_state(root) if blessed else git",
        new="    return git",
        caught_by="tests/test_bench_bless_protocol.py",
        why=(
            "`git_state` is read before the corpus loop and a `--bless` run "
            "writes inside it, so a bless on a clean tree filed itself as "
            "`<date>-<sha>.json` — a filename naming a commit whose tree that "
            "very run then modified, which is what the `-dirty` suffix exists "
            "to prevent."
        ),
    ),
    Mutant(
        name="golden_trusts_a_frame_its_own_record_disowns",
        file="an/bench/golden.py",
        old='        if expected is not None and expected != record["golden_sha256"]:',
        new="        if False:",
        caught_by="tests/test_bench_golden.py",
        why=(
            "the bless record and the committed PNG carry the same digest of "
            "the same file, written by two different calls. A disagreement "
            "means the golden is not the picture a human blessed — an edited "
            "file, a half-finished re-bless — and every one of those read as a "
            "clean PASS."
        ),
    ),
    Mutant(
        name="bench_asks_a_mutated_run_the_unmutated_question",
        file="an/tools.py",
        old="            compare_rows(load_row(compare), ledger, mutation=mutation or None)",
        new="            compare_rows(load_row(compare), ledger)",
        caught_by="tests/test_bench_mutation_cli.py",
        why=(
            "without the mutation, `compare` answers 'is the second row worse' "
            "of a run degraded on purpose — so the declared per-mutation "
            "predictions are never scored and the an#41 criterion cannot appear "
            "in the mandated `--compare` artifact at all."
        ),
    ),
    Mutant(
        name="bench_blesses_a_deliberately_degraded_picture",
        file="an/tools.py",
        old='        if bless:\n            return (\n                "refusing --bless with --mutation: a lever renders a"',
        new='        if False:\n            return (\n                "refusing --bless with --mutation: a lever renders a"',
        caught_by="tests/test_bench_mutation_cli.py",
        why=(
            "blessing under a lever commits the degraded picture as the "
            "reference every future run is measured against — a permanent, "
            "silent re-baseline, and the one bless refusal that cannot be "
            "recovered by reading the recorded reason."
        ),
    ),
    # ---------------------------------------------------------------- an#55
    # ---------------------------------------------------------------- an#59
    Mutant(
        name="pix_fmt_knob_cannot_reach_the_encode",
        file="an/adapters/cutout/render.py",
        old="    resolved = pix_fmt or DEFAULT_PIX_FMT",
        new='    resolved = pix_fmt or "yuv420p"',
        caught_by="tests/test_encode_pins.py",
        why=(
            "reading the literal instead of the module global severs the seam "
            "any outside caller pulls — the same shape hoisting "
            "`DETERMINISTIC_X264_ARGS` into a default argument would sever for "
            "`high_crf`. The recorded row would still say 4:4:4 (because "
            "`environment_record` reads the global) while the file stayed "
            "4:2:0: a row that lies about its own file. That is why the seam is "
            "kept even though an#59 ships no lever — see the note there."
        ),
    ),
    Mutant(
        name="mux_argv_is_checked_by_subset_not_equality",
        file="an/adapters/cutout/render.py",
        # Anchored ACROSS the insertion point — `"-pix_fmt",` is in the `old`
        # text purely so the mutation *splits* it rather than prefixing it. An
        # earlier spelling put the two new flags in front of `-c:v`, which made
        # `old` a substring of `new`: the mutated file still contained the
        # original text, so the SIGKILL recovery path in `check_sites` was blind
        # to this one mutant of the 43 and a subsequent sweep laundered the
        # leftover into its `original`. `check_sites` now refuses any mutant
        # with that shape, so this anchor is load-bearing rather than cosmetic.
        old='        "-c:v",\n        "libx264",\n        "-pix_fmt",',
        new=(
            '        "-c:v",\n        "libx264",\n'
            '        "-tune",\n        "animation",\n        "-pix_fmt",'
        ),
        caught_by="tests/test_encode_pins.py",
        why=(
            "`-tune animation` is a measured-and-rejected flag (0.8%) and this "
            "is what adding it looks like. A SUBSET check passes — every pin is "
            "still present — and the encode moves and every encode-side metric "
            "is silently refused against every committed row. Only argv "
            "equality notices."
        ),
    ),
    # ------------------------------------------------- an#57, registerable
    # since an#58 gated the parse check on `.py`. Before that these two had to
    # be mutation-tested by hand, with the proof living in a docstring instead
    # of in `an bench-mutants` — for the two files where a pixel-affecting
    # mutation is hardest to catch by reading.
    Mutant(
        name="capture_page_stops_compositing_the_canvas",
        file="an/data/cutout_runtime/index.html",
        old="#stage { display: block; }",
        new="#stage { display: none; }",
        caught_by="tests/test_cutout_runtime_files.py",
        why=(
            "an#57's proposal. The element screenshot is a PAGE capture clipped "
            "to the element, so hiding the canvas does not make it cheaper — it "
            "makes `Locator.screenshot` time out after 30 s per frame. The two "
            "spellings Playwright does accept return an all-white frame."
        ),
    ),
    # ---------------------------------------------------------------- an#56
    Mutant(
        name="supersample_autodensity_true",
        file="an/data/cutout_runtime/runtime.js",
        old="            autoDensity: false,",
        new="            autoDensity: true,",
        caught_by="tests/test_bench_supersample_lever.py",
        why=(
            "`autoDensity: true` makes Chromium composite the k-times backbuffer "
            "down before the screenshot — a blind downscale with no filter "
            "choice and no record. The PNGs come out the DECLARED size, so every "
            "shape check passes and the whole knob silently measures nothing. It "
            "is the option whose name most suggests it is the right one. Lives "
            "on the PRODUCT's file since an#58, because the product owns the key."
        ),
    ),
    Mutant(
        name="supersample_skips_the_frame_stage",
        file="an/bench/mutations.py",
        old="        render._capture_frames = _capture_then_resolve",
        new="        render._capture_frames = original",
        caught_by="tests/test_bench_supersample_lever.py",
        why=(
            "drops the resolve, leaving k-times PNGs on disk. Before an#54 that "
            "was silent — `_reshape` checked byte-count divisibility and k**2 "
            "always divides — and family A was computed on k**2 scrambled frames "
            "that still produced a believable `edge_transition_width`. It is a "
            "loud refusal now, which is what makes this lever safe to run."
        ),
    ),
    Mutant(
        name="supersample_verify_is_merely_not_shipped",
        file="an/bench/mutations.py",
        old="    if recorded != expected:",
        new="    if False:",
        caught_by="tests/test_bench_supersample_lever.py",
        why=(
            "reduces the supersample fingerprint to `disabled_aa`'s inequality, "
            "which ANY render lever satisfies — both stage through one seam and "
            "both move `render_side.runtime_sha256`. A row rendered with "
            "`antialias: false` then verifies as a supersample row and the whole "
            "lever table is written from the wrong lever's numbers."
        ),
    ),
    Mutant(
        name="edge_masked_colour_count_is_not_masked",
        file="an/bench/metrics.py",
        old="    per_frame = [len(np.unique(f[m])) for f, m in zip(packed, edge) if m.any()]",
        new="    per_frame = [len(np.unique(f)) for f, m in zip(packed, edge) if m.any()]",
        caught_by="tests/test_bench_metrics.py",
        why=(
            "unmasked it is `frame_distinct_colours` under a second name, and "
            "the one property the mask does buy — that an interior-only change "
            "cannot reach the number — is gone with no other symptom."
        ),
    ),
    Mutant(
        name="empty_edge_mask_reads_as_zero_colours",
        file="an/bench/metrics.py",
        old='        return float("nan"), 0',
        new="        return 0.0, 0",
        caught_by="tests/test_bench_metrics.py",
        why=(
            "a substituted zero is the largest possible DOWNWARD move in the "
            "one metric that exists to notice a downward move, on exactly the "
            "scenes where the number means nothing at all."
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


def _left_mutated_message(mutant: Mutant) -> str:
    """A leftover from a killed sweep, named as such and with its exact repair.

    ``check_sites`` already refused this state — the ``old`` text occurs zero
    times, not once — but "occurs 0 times" reads like ordinary declaration rot,
    and the reader goes looking for the refactor that moved the code. There was
    no refactor: a previous run was killed between the write and the restore,
    and the file on disk is **deliberately plausible**. Every mutant here is
    chosen to compile, render, and leave the suite green apart from the one test
    that names it — so this is a defect a developer can commit without noticing,
    and the only thing standing between it and a commit is this sentence.
    """
    return (
        f"{mutant.name}: {mutant.file} looks LEFT MUTATED by an interrupted run "
        f"— the mutation is present and the original is gone. That is USUALLY a "
        f"killed sweep rather than declaration rot, and it matters because the "
        f"mutation is plausible by design (it compiles, it renders, the suite "
        f"stays green apart from {mutant.caught_by}), so it can be committed "
        f"unnoticed.\n    But this is a TEXT test and cannot prove it: a refactor "
        f"that moved this site while leaving the replacement text somewhere in "
        f"the file looks identical, and five of the declarations have a `new` "
        f"that occurs in the unmutated file. CHECK `git diff` FIRST. Then, if it "
        f"really is a leftover, restore with `git checkout -- {mutant.file}` when "
        f"nothing else in that file is yours, or replace"
        f"\n      {mutant.new!r}\n    with\n      {mutant.old!r}"
    )


@contextlib.contextmanager
def restore_on_termination(
    signals: Iterable[int] = RESTORE_ON_SIGNALS,
) -> Iterator[tuple[int, ...]]:
    """Turn a terminating signal into an exception for the duration.

    ``run_mutants`` restores in a ``finally``, which covers everything that
    *raises* — an exploding pytest, Ctrl-C — and covers nothing about SIGTERM,
    which stops the interpreter without raising, so no ``finally`` runs and the
    mutated file stays on disk. A `kill`, a timeout, an agent harness reaping a
    background task and a closing terminal are all SIGTERM, and the sweep is
    slow enough that interrupting it is the normal thing to do.

    The previous handlers are restored on the way out, because this module is
    importable and a library that permanently rewires SIGTERM is a worse defect
    than the one it fixes. Yields the signals it actually took, which is empty
    off the main thread (``signal.signal`` refuses there), on a platform that
    will not have them, and for any signal arriving already ``SIG_IGN`` — a
    caller that wants to *report* the coverage can, and the restore itself never
    depends on it.

    An inherited ``SIG_IGN`` is left alone. ``nohup`` and most detach wrappers
    ignore SIGHUP so a long job survives the terminal closing, and a sweep is
    exactly the job someone detaches; taking the signal there would convert a
    deliberately-protected run into a partial one exiting 130. There is nothing
    to protect against either way — a signal that is ignored is never delivered,
    so it cannot leave a mutation on disk.

    **SIGKILL cannot be handled at all**, which is why the recovery path in
    :func:`check_sites` is the load-bearing half of this fix and this is the
    convenience.
    """

    def _raise(signum, frame):  # noqa: ARG001 — the frame is not ours to use
        name = getattr(signal.Signals(signum), "name", signum)
        raise MutantRunInterrupted(
            f"{name} arrived mid-mutant; restoring the tree before exiting"
        )

    previous: dict[int, object] = {}
    try:
        for sig in signals:
            try:
                if signal.getsignal(sig) is signal.SIG_IGN:
                    # An INHERITED ignore is a decision somebody already made,
                    # and overriding it changes behaviour in the one case where
                    # the operator was explicit: `nohup an bench-mutants &`
                    # leaves SIGHUP ignored precisely so closing the terminal
                    # does not stop the sweep. Taking it would turn that into a
                    # partial sweep exiting 130 — a safe tree, but not the run
                    # that was asked for. An ignored signal also cannot leave a
                    # mutation on disk, because it is never delivered.
                    continue
                previous[sig] = signal.signal(sig, _raise)
            except (ValueError, OSError):
                # Not the main thread, or the platform refuses this signal.
                # Neither is a reason to refuse the sweep; the `finally` still
                # covers every raising path.
                continue
        yield tuple(previous)
    finally:
        for sig, handler in previous.items():
            # `TypeError` too: `getsignal` returns None for a handler installed
            # from C, and `signal.signal(sig, None)` raises it. Restoring is
            # impossible in that case, and letting it out would REPLACE an
            # in-flight `MutantRunInterrupted` — turning "the tree was restored"
            # into a traceback about the restore of a handler nobody asked
            # about, after the tree had already been put back.
            with contextlib.suppress(ValueError, OSError, TypeError):
                signal.signal(sig, handler)


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
        if count == 0 and mutant.new in source:
            problems.append(_left_mutated_message(mutant))
        elif count != 1:
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
            mutated = source.replace(mutant.old, mutant.new, 1)
            if mutant.old in mutated:
                # The recovery path recognises a leftover as "the mutation is
                # present and the original is gone". That second half is an
                # ASSUMPTION about the declaration, and one mutant broke it: its
                # `new` prefixed its `old` rather than replacing it, so the
                # mutated file still contained the original text, `check_sites`
                # returned no problems at all on a tree carrying it, and the
                # next sweep read the mutation as the `original` it restores to
                # — laundering the damage into the baseline while reporting
                # health. Asserted from the REAL substitution rather than from
                # `mutant.old in mutant.new`, which misses the case where the
                # replacement re-creates `old` across its own boundary.
                problems.append(
                    f"{mutant.name}: applying it leaves its own `old` text in "
                    f"{mutant.file}, so a tree left mutated by a SIGKILL would "
                    "be invisible to the recovery path above. Re-anchor `old` "
                    "so the mutation replaces it rather than extending it."
                )
        if count == 1 and mutant.file.endswith(".py"):
            # A mutant that produces unparseable Python breaks COLLECTION, and
            # a collection error is not a guard catching anything. Checked here,
            # at declaration time and for free, because the alternative is
            # finding out from a sweep that says 16/16.
            #
            # **Gated on `.py`, and that gate is the whole reason this registry
            # can reach the renderer at all.** Unconditionally, `compile()`
            # refuses `an/data/cutout_runtime/runtime.js` and `index.html` —
            # which are precisely the files where a pixel-affecting mutation
            # hides, and where a guard is hardest to prove by argument. Before
            # an#58 the registry silently could not hold one, so those guards
            # had to be mutation-tested by hand and the proof lived in a
            # docstring rather than in `an bench-mutants`.
            try:
                compile(mutated, mutant.file, "exec")
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

    **With no `root`, the sweep runs against a COPY of the repository** and the
    real working tree is never written (an#124). See :func:`sweep_tree` for why
    that is not merely tidiness. An explicit `root` is swept IN PLACE, because a
    caller who names a tree has already chosen a throwaway.

    Restoration is in a ``finally`` and rewrites the ORIGINAL text rather than
    reversing the substitution: a reversal that itself failed would leave the
    tree broken, which is a worse outcome than any mutant surviving.

    A ``finally`` covers everything that *raises*, which is why the loop runs
    inside :func:`restore_on_termination`: SIGTERM does not raise. What no
    handler can cover is SIGKILL, so ``check_sites`` — which runs first, here —
    also recognises a file left mutated by a previous kill and says so in those
    words rather than as declaration rot.
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

    def sweep(where: Path) -> None:
        with restore_on_termination():
            for mutant in MUTANTS:
                if wanted is not None and mutant.name not in wanted:
                    continue
                results.append(_run_one(where, mutant))

    if root is not None:
        # An explicit root is ALREADY the caller's throwaway — the interruption
        # tests hand one over precisely so they can watch the tree the sweep
        # mutates. Copying it would move the damage somewhere they cannot see
        # and turn those guards green for the wrong reason.
        sweep(base)
    else:
        with sweep_tree(base) as copied:
            sweep(copied)
    return results


#: Suffix for the sibling file the restore is staged through. Same directory, so
#: `os.replace` is a rename within one filesystem and therefore atomic.
RESTORE_TMP_SUFFIX: str = ".an-restore-tmp"


def _restore_atomically(path: Path, original: str) -> None:
    """Put ``original`` back in a way no kill can interrupt half-way.

    ``Path.write_text`` opens mode ``"w"``, which TRUNCATES at open and flushes
    at close. Anything that stops the process in that window — SIGTERM, SIGHUP,
    a plain Ctrl-C, SIGKILL, a crash, the power — leaves the real source file
    empty or half-written, which is **strictly worse** than the leftover this
    whole mechanism exists to prevent, and is the "a reversal that itself failed
    would leave the tree broken" hazard the module docstring argues against.

    A ``pthread_sigmask`` around that write was the first attempt, and it is the
    wrong tool for a reason this module of all modules should have seen: it
    cannot cover SIGKILL, and "SIGKILL cannot be handled at all" is the premise
    the recovery path is built on. It also silently excluded SIGINT — the
    interruption an#67 itself calls the normal one — because it reused
    :data:`RESTORE_ON_SIGNALS`, whose exclusion of SIGINT is correct for
    *handlers* (SIGINT already raises) and exactly wrong for *blocking*. A mask
    narrows the window; it does not close it, and it invites prose that says the
    restore is safe when it is only safer.

    Staging through a sibling and calling ``os.replace`` closes it instead. The
    rename is atomic within a filesystem, so an observer sees the old bytes or
    the new bytes and never a truncated file, whatever kills the process. A kill
    before the rename leaves a stray ``*.an-restore-tmp`` and the source file
    untouched — which is the ordinary leftover case the marker already covers.
    """
    tmp = path.with_name(path.name + RESTORE_TMP_SUFFIX)
    tmp.write_text(original, encoding="utf-8")
    os.replace(tmp, path)


def _child_env(base: Path) -> dict[str, str]:
    """Environment for the guard subprocess, with `base` FIRST on the path.

    `cwd=base` is not enough and the difference is the whole point of sweeping a
    copy. A bare ``import an`` in the child resolves through the **editable
    install**, which points at whichever checkout was installed — so without
    this the sweep would mutate the copy and then test the real tree, and every
    mutant would survive while reporting that the guards are decoration.

    The same trap, from the other direction, made the an#67 interruption tests
    measure a tree the branch had not touched. `PYTHONPATH` beats a `.pth` in
    site-packages, so this makes the answer unconditional rather than dependent
    on where the process happens to have been started.
    """
    existing = os.environ.get("PYTHONPATH")
    return {
        **os.environ,
        "PYTHONPATH": str(base) + (os.pathsep + existing if existing else ""),
    }


def _run_one(base: Path, mutant: Mutant) -> dict:
    """Apply one mutant, run its guard file, restore, and judge the outcome."""
    path = base / mutant.file
    original = path.read_text(encoding="utf-8")
    try:
        path.write_text(original.replace(mutant.old, mutant.new, 1), encoding="utf-8")
        completed = subprocess.run(
            [sys.executable, "-m", "pytest", mutant.caught_by, *PYTEST_ARGS],
            cwd=base,
            capture_output=True,
            text=True,
            env=_child_env(base),
        )
    finally:
        _restore_atomically(path, original)
    summary = [
        line
        for line in completed.stdout.splitlines()
        if "passed" in line or "failed" in line or "error" in line
    ]
    last = summary[-1] if summary else ""
    failed = _count(last, "failed")
    errored = _count(last, "error")
    # A mutant that breaks COLLECTION proves nothing. `returncode != 0` alone
    # reads a SyntaxError in the mutated module as "the guard caught it" — and
    # one declared mutant really did produce unparseable Python and really was
    # reported as CAUGHT for nine commits. So the verdict is "at least one test
    # FAILED", and a run that only errored is reported as `errored` rather than
    # folded into either answer.
    return {
        "name": mutant.name,
        "file": mutant.file,
        "caught_by": mutant.caught_by,
        "caught": failed > 0,
        "errored": errored > 0 and failed == 0,
        "returncode": completed.returncode,
        "summary": last,
        "why": mutant.why,
    }


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
