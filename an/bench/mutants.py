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
