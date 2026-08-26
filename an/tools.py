"""User-facing utility functions, plus the SSOT list for CLI dispatch.

Each function here is meant to be callable from Python *and* from the shell
via `an <funcname>`. Keep their signatures dispatch-friendly: positional args
become required arguments, defaults become optional flags, and the docstring
becomes the command's help. `an/__main__.py` projects this list onto typer
without touching these functions, so they stay plain Python.
"""

from __future__ import annotations

from pathlib import Path

from an.check_requirements import check_requirements as _check_requirements
from an.check_requirements import format_report
from an.ir.sync import sync as _sync
from an.orchestrate import render_project as _render_project
from an.orchestrate import validate_project
from an.project import init as _init
from an.iterate import iterate as _iterate
from an.preview import preview_project as _preview_project
from an.characters.cli import (
    _dispatch_funcs as _character_dispatch_funcs,
)


def init(project_dir: str, name: str | None = None, force: bool = False) -> str:
    """Create a fresh an project at ``project_dir``.

    project_dir: where to create the project (created if missing)
    name: project display name (defaults to the directory name)
    force: overwrite an existing scene.md
    """
    path = _init(project_dir, name=name, force=force)
    return f"initialized an project at {path}"


def validate(project_dir: str) -> str:
    """Validate the scene at ``project_dir``. Prints findings, exit 0 on pass."""
    report = validate_project(project_dir)
    if report.passed and not report.findings:
        return "validation: passed, no findings"
    lines = ["validation: " + ("passed" if report.passed else "FAILED")]
    for f in report.findings:
        lines.append(f"  [{f.severity}] {f.ir_path}: {f.description}")
    return "\n".join(lines)


def sync(project_dir: str) -> str:
    """Reconcile scene.md and ir/scene.json inside ``project_dir``."""
    result = _sync(project_dir)
    parts = []
    if result.wrote_json:
        parts.append("regenerated ir/scene.json from scene.md")
    if result.wrote_md:
        parts.append("regenerated scene.md from ir/scene.json")
    if result.drift_warning:
        parts.append(f"warning: {result.drift_warning}")
    return "; ".join(parts) if parts else "no changes"


def check() -> str:
    """Print a status report of all backend system + Python deps."""
    return format_report(_check_requirements())


def render(
    project_dir: str,
    output_name: str = "main",
    tts: str = "offline",
    lipsync: str = "offline",
    parallel: str = "",
    strict_assets: bool = False,
    supersample: int = 1,
    pix_fmt: str = "",
    step_hz: float = 0.0,
    language: str = "en",
) -> str:
    """Render the project at ``project_dir`` to a single mp4.

    project_dir: path to an an project (must contain scene.md / ir/scene.json)
    output_name: filename stem under output/ (default: "main")
    tts: TTS provider — "offline" (silent) or "elevenlabs" (needs ELEVEN_API_KEY)
    lipsync: lip-sync provider — "offline" (deterministic), "rhubarb"
        (needs the rhubarb binary), or "whisper" (needs faster-whisper)
    parallel: per-shot concurrency. "" or "1" = serial (default); "auto" =
        min(shots, cpu, 4); a number ≥ 2 caps the thread pool.
    strict_assets: fail instead of drawing a stand-in (the placeholder rig, the
        default backdrop) for an asset the project's stores don't supply
    supersample: render at N times the resolution and resolve back with an exact
        N x N block mean. Opt-in; 1 (the default) costs nothing at all. Measured
        on the shipped path at 1920x1080: 125.5 ms/frame at 1, 508.6 at 2 —
        4.05x, which is NOT the 2.54x the research reports for the render alone
    pix_fmt: the delivered encode's pixel format — "yuv420p" (default) or
        "yuv444p". The ONE first-order quality lever in the encoder: 4:4:4 cuts
        the edge-band error 11.35 -> 3.79, where a mathematically lossless 4:2:0
        only reaches 10.15. It is opt-in for a PRODUCT reason and not an encoder
        one: High 4:4:4 Predictive is refused by many hardware decoders,
        browsers and platforms, so a 4:4:4 file is one some viewers cannot play
    step_hz: stepped timing for authored tweens — pose updates per second, on a
        shot-wide grid (15 at 30 fps is "on twos", 10 "on threes"). 0 (the
        default) uses the scene's own `meta.step_hz`, which is unset (smooth)
        unless the author declared one. The camera, blinks, `play` clips and
        swap channels are never stepped by this
    language: the dialogue's language (BCP-47) for providers that select
        behaviour by it — Rhubarb's recognizer today: English (the default)
        uses `pocketSphinx` with the transcript, anything else `phonetic`
        without one (an#96)
    """
    parallel_arg: int | str | None
    if not parallel:
        parallel_arg = None
    elif parallel == "auto":
        parallel_arg = "auto"
    else:
        try:
            parallel_arg = int(parallel)
        except ValueError:
            return f"invalid --parallel value: {parallel!r}; use a number or 'auto'"
    output_path = _render_project(
        project_dir,
        output_name=output_name,
        tts=tts,
        lipsync=lipsync,
        parallel=parallel_arg,
        strict_assets=strict_assets,
        supersample=supersample,
        pix_fmt=pix_fmt or None,
        step_hz=step_hz or None,
        language=language,
    )
    return f"rendered: {output_path}"


def iterate(
    project_dir: str,
    instruction: str,
    apply_changes: bool = True,
    model: str = "claude-opus-4-7",
) -> str:
    """Apply a free-text instruction to the scene. Needs ANTHROPIC_API_KEY.

    project_dir: path to an an project
    instruction: what to change in plain English (e.g. "make Maya's laugh longer and warmer")
    apply_changes: persist the new scene to disk + invalidate affected shot caches (default True)
    model: Anthropic model id (default claude-opus-4-7)
    """
    result = _iterate(project_dir, instruction, apply=apply_changes, model=model)
    lines: list[str] = []
    lines.append(f"summary: {result.summary}")
    lines.append(f"affected shots: {', '.join(result.affected_shots) or '(none)'}")
    lines.append(f"patches ({len(result.patches)}):")
    for p in result.patches:
        v = "" if p.op == "delete" else f" = {p.value!r}"
        lines.append(f"  [{p.op}] {p.path}{v}")
    if result.error:
        lines.append(f"ERROR: {result.error}")
        if result.validation:
            for f in result.validation.findings:
                lines.append(f"  [{f.severity}] {f.ir_path}: {f.description}")
    elif apply_changes:
        lines.append("status: applied + persisted; re-run `an render` to see changes")
    else:
        lines.append("status: dry-run (apply_changes=False); nothing persisted")
    return "\n".join(lines)


def preview(
    project_dir: str,
    shot: str = "",
    no_browser: bool = False,
) -> str:
    """Live-preview the project's scene in a browser; reloads on edit.

    Spins up a local HTTP server pointed at the runtime canvas. The
    browser polls ``scene.json`` for changes and re-loads when you save
    ``scene.md``. Lossy: visuals only, no audio. Blocks until Ctrl-C.

    project_dir: path to an an project (must contain scene.md / ir/scene.json)
    shot: shot id to preview (default: first shot in the timeline)
    no_browser: don't auto-open the default browser
    """
    base_url = _preview_project(
        project_dir,
        shot_id=shot or None,
        open_browser=not no_browser,
    )
    return f"preview: stopped (was at {base_url})"


# SSOT for the CLI dispatcher (per the python-dispatching skill convention).


def credits(project_dir: str, json_out: str | None = None) -> str:
    """Show what third-party work is in ``project_dir`` and what it obliges.

    project_dir: the an project
    json_out: also write the machine-readable record to this path

    A licence recorded and never displayed is not compliance, so this is the
    consumer that makes the provenance field worth having.
    """
    from an.credits import credits_for_project

    report = credits_for_project(project_dir)
    if json_out:
        import json as _json

        _p = Path(json_out)
        _p.parent.mkdir(parents=True, exist_ok=True)
        _p.write_text(_json.dumps(report.to_dict(), indent=2), encoding="utf-8")
    return report.format()


def bench(
    scenes: str = "",
    out: str = "",
    keep_render: str = "",
    quiet: bool = False,
    bless: str = "",
    compare: str = "",
    mutation: str = "",
) -> str:
    """Render the fixed bench corpus and write a metrics ledger.

    The instrument, not the verdict: it records numbers whose predicted
    direction under each deliberate degradation is declared in advance, so a
    future regression is caught by something other than someone noticing.

    scenes: comma-separated corpus scene names (default: all of them)
    out: ledger path (default: misc/bench/ledger/<date>-<sha>[-dirty].json)
    keep_render: keep the throwaway render tree here instead of deleting it
    quiet: print only the ledger path
    bless: (re)write the golden frames, recording THIS STRING as the reason
    compare: after the run, compare it against this baseline ledger row
    mutation: pull one declared lever for this run, and ask --compare the
        per-mutation question instead of "is the second row worse"

    Rendering knobs are deliberately NOT flags: a bench whose render knobs vary
    per invocation produces incomparable rows, so they are a module constant
    recorded verbatim into the ledger. ``--mutation`` is not one of them, and is
    the exception that states the rule: a lever is the **independent variable**,
    it is named in the report, it is exempted by declaration in
    ``MUTATION_TOUCHES`` rather than by widening anything, and the row it
    produces is never filed under a commit's name. Without it the ``--compare``
    artifact is always the ``mutation=None`` path, which asks "is this worse" of
    a run that was broken on purpose — the wrong question, answered
    confidently.

    ``--bless`` takes the reason as its value rather than pairing with a
    separate ``--reason``, so a bless with no recorded reason cannot be typed.
    A re-bless with no recorded reason is the same failure as a silently
    widened threshold, and it is the failure this wave exists to prevent — so
    look at the PNG diff (GitHub renders 2-up, swipe and onion-skin) before
    writing one.
    """
    from an.bench.corpus import DFLT_FIXTURES
    from an.bench.run import format_panel, run_bench

    chosen = None
    if scenes:
        wanted = [s.strip() for s in scenes.split(",") if s.strip()]
        unknown = [w for w in wanted if w not in DFLT_FIXTURES]
        if unknown:
            return (
                f"unknown corpus scene(s) {unknown}; available: {sorted(DFLT_FIXTURES)}"
            )
        chosen = {w: DFLT_FIXTURES[w] for w in wanted}

    if mutation:
        # Validated BEFORE anything renders. The corpus takes minutes, and an
        # undeclared name would otherwise surface as a bare `KeyError` from
        # inside `mutated_row` after all of it.
        from an.bench.mutations import LEVERS, mutated_row

        if mutation not in LEVERS:
            return (
                f"unknown mutation {mutation!r}; declared: {sorted(LEVERS)}. A"
                " lever is registered in `an.bench.mutations.LEVERS` and"
                " predicted for in `an.bench.registry.MUTATIONS`; the two are"
                " checked equal at import."
            )
        if bless:
            return (
                "refusing --bless with --mutation: a lever renders a"
                " DELIBERATELY DEGRADED picture, and blessing it would commit"
                " that picture as the reference every future run is measured"
                " against. Bless from an unmutated run."
            )
        ledger = mutated_row(
            mutation,
            scenes=chosen,
            keep_render=Path(keep_render) if keep_render else None,
        )
        # `mutated_row` writes nothing (`run_bench(write=False)`), and that is
        # right: a mutated row is evidence about a pipeline broken on purpose,
        # so filing it as `<date>-<sha>.json` would claim it is the commit's
        # evidence — the `-dirty` failure one level up. `--out` is the only way
        # to keep one, and it may not point into the ledger directory, where
        # `latest_rows` would hand it to a bare `an bench-compare` as a
        # baseline.
        if out:
            from an.bench.ledger import write_ledger
            from an.bench.paths import LEDGER_DIRNAME, repo_root

            path = Path(out).resolve()
            if path.parent == (repo_root() / LEDGER_DIRNAME).resolve():
                return (
                    f"refusing to write a mutated row into {LEDGER_DIRNAME}: "
                    "`latest_rows` would hand it to a bare `an bench-compare` "
                    "as a baseline, and it measures a pipeline that was broken "
                    "on purpose. Write it anywhere else."
                )
            write_ledger(ledger, path)
            ledger["_written_to"] = str(path)
    else:
        ledger = run_bench(
            scenes=chosen,
            out=Path(out) if out else None,
            keep_render=Path(keep_render) if keep_render else None,
            bless=bless,
        )

    unfiled = (
        "\nthis row was NOT written: a mutated row is evidence about a"
        " pipeline broken on purpose, never a commit's. Pass --out <path> to"
        " keep it, then gate it with"
        f" `an bench-compare --mutation {mutation} --strict`."
    )
    if quiet:
        panel = str(ledger.get("_written_to", "")) or (
            unfiled.strip() if mutation else ""
        )
    else:
        panel = format_panel(ledger)
        if mutation:
            panel = f"MUTATED RUN: lever {mutation!r} pulled\n" + panel
            if not out:
                panel += unfiled
    if not compare:
        return panel
    from an.bench.compare import compare as compare_rows
    from an.bench.compare import format_comparison, load_row

    return (
        panel
        + "\n\n"
        + format_comparison(
            compare_rows(load_row(compare), ledger, mutation=mutation or None)
        )
    )


def bench_compare(
    before: str = "",
    after: str = "",
    mutation: str = "",
    strict: bool = False,
    raw: bool = False,
) -> str:
    """Compare two ledger rows — and refuse when they are not comparable.

    before: baseline ledger row (default: the second-newest committed row)
    after: the row to judge (default: the newest committed row)
    mutation: evaluate the per-mutation predictions instead of asking whether
        the second row is worse. One of the mutations the rows declare.
    strict: exit nonzero when the answer is bad — a regression without a
        mutation, an unmet criterion with one, a comparison that answered
        nothing, or a row that could not be read at all. For CI.
    raw: print the report as JSON instead of the human digest

    Refusing is the feature. Two rows measured on different scenes, at
    different resolutions, or on different x264 builds are not "one better and
    one worse" — every number in them is uninterpretable relative to the other,
    and a number reported across incomparable rows is worse than none.

    ``-dirty`` rows are excluded from the defaults: a row measured against
    uncommitted edits describes no commit. Name one explicitly to compare it.
    """
    import json as _json
    import sys as _sys

    from an.bench.compare import (
        ComparisonError,
        compare as compare_rows,
        format_comparison,
        latest_rows,
        load_row,
    )

    if not before or not after:
        rows = latest_rows()
        if len(rows) < 2:
            return (
                f"need two committed ledger rows to compare; found {len(rows)}. "
                "Run `an bench` on two different commits, or name the rows "
                "explicitly with --before and --after."
            )
        before = before or str(rows[0])
        after = after or str(rows[1])

    try:
        report = compare_rows(
            load_row(before), load_row(after), mutation=mutation or None
        )
    except ComparisonError as e:
        # `--strict` documents itself as "exit nonzero when the answer is bad",
        # and a row the comparer cannot read at all is the worst answer there
        # is. This handler used to `return` here, ahead of the `if strict:`
        # block below, so an unreadable `schema_version` — or an undeclared
        # `--mutation`, which is the state a `--strict --mutation supersample`
        # run is in before the lever is registered — exited 0. Same failure
        # class an#51 closed for the refusal path and left open on the raise
        # path.
        refusal = f"refused: {e}"
        if strict:
            print(refusal)
            _sys.exit(1)
        return refusal

    text = (
        _json.dumps(report, indent=2, sort_keys=True)
        if raw
        else format_comparison(report)
    )
    if strict:
        # Four ways for the answer to be bad, and only one of them is a
        # regression. A comparison that REFUSED every scene, or that lost
        # coverage, has not "passed" — it has produced no answer, and a CI gate
        # that reads that as success is worse than no gate.
        bad = (
            not report.get("answered")
            or bool(report.get("coverage_lost"))
            or (
                not report.get("criterion_met")
                if mutation
                else bool(report.get("has_regressions"))
            )
        )
        if bad:
            print(text)
            _sys.exit(1)
    return text


def bench_mutants(names: str = "", quiet: bool = False) -> str:
    """Break each guard on purpose and check the test that names it goes red.

    names: comma-separated mutant names (default: every declared one)
    quiet: print only the tally

    "N mutants, all caught" is unfalsifiable after the fact when the mutations
    lived in a scratch script. These are declared data, so the proof is
    re-runnable — which matters because Wave 1 shipped three guards that stayed
    green while the bug they guarded was present.

    The WHOLE guard file runs for each mutant, never a `-k` filter: a filter that
    happens to exclude the catching test reports "not caught" and sends you to
    write a test that already exists. Takes about forty seconds.
    """
    import sys as _sys

    from an.bench.mutants import (
        INTERRUPTED_EXIT_CODE,
        MUTANTS,
        MutantError,
        MutantRunInterrupted,
        format_results,
        run_mutants,
    )

    wanted = [n.strip() for n in names.split(",") if n.strip()] or None
    if wanted:
        unknown = sorted(set(wanted) - {m.name for m in MUTANTS})
        if unknown:
            print(
                f"unknown mutant(s) {unknown}; declared: "
                f"{sorted(m.name for m in MUTANTS)}"
            )
            _sys.exit(1)
    try:
        results = run_mutants(wanted)
    except MutantError as e:
        # Nonzero. Declaration rot is the failure this command exists to
        # surface — a mutant whose source text has moved has silently stopped
        # proving anything — and returning a string exits 0, so a CI job would
        # print the warning and pass (an#41 review).
        print(e)
        _sys.exit(1)
    except MutantRunInterrupted as e:
        # A sweep is slow enough that interrupting it is normal (an#67). Say
        # that the tree survived, because the alternative — a traceback — leaves
        # a reader wondering whether a mutated file is still on disk.
        print(f"{e}\nthe tree was restored; nothing was left mutated")
        _sys.exit(INTERRUPTED_EXIT_CODE)
    survivors = [r for r in results if not r["caught"]]
    text = (
        f"{len(results) - len(survivors)}/{len(results)} caught"
        if quiet
        else format_results(results)
    )
    if survivors:
        print(text)
        _sys.exit(1)
    return text


_dispatch_funcs = [
    init,
    validate,
    sync,
    check,
    render,
    iterate,
    preview,
    credits,
    bench,
    bench_compare,
    bench_mutants,
]


# Sub-namespaces. ``__main__`` mounts each as a sub-app so
# the CLI looks like ``an character new <name> ...``.
_dispatch_namespaces: dict[str, list] = {
    "character": _character_dispatch_funcs,
}
