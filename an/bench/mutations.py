"""The levers: deliberate, declared changes through seams the shipped code already has.

This is the other half of the instrument. `an bench` records numbers; these
change the pipeline on purpose so a test can check the numbers move the way the
registry declared **in advance**. A metric that never moves under any lever is
decoration, and the only way to know which is which is to pull one.

**Two of the three are degradations and the third is an improvement**, and that
asymmetry is the point rather than an untidiness. ``high_crf`` and
``disabled_aa`` make the picture worse; ``supersample`` makes it better. A panel
that has only ever been shown things getting worse cannot tell an improvement
from a regression — run as a plain commit-to-commit diff, a k=2 supersample
reports **2 false regressions** (``off_palette_pixel_fraction`` rises as blends
multiply, ``min_ssim_win8_vs_golden`` falls away from the golden) and **7
unearned improvements** (every family C/D/E/G metric whose mask derives from the
source frames: gates live inside ``Prediction``, which exists only per declared
mutation, so with ``mutation=None`` no gate is consulted and a softer source
shrinks each mask to its easiest members). Declared as a lever, none of that
happens. So what a lever has to be is **declared in advance**, not bad — and the
word "degradations" in the old first line was quietly making the stronger, false
claim (an#56).

**No production knob exists for any of this, and that is deliberate.** Each
lever reaches an existing seam from the outside:

- ``high_crf`` rebinds ``an.adapters.cutout.render.DETERMINISTIC_X264_ARGS``.
  ``_ffmpeg_mux`` reads that name as a module global at call time, so the
  rebinding reaches the delivered encode. It does **not** reach
  ``an.bench.imageio.lossless_encode_command``, which bound the tuple at import
  — and that is exactly right: the lossless reference must stay lossless, or
  every encode-side metric would be measured against a moving target and the
  lever would produce beautiful numbers about nothing.
- ``disabled_aa`` copies the staged runtime, flips PixiJS's ``antialias`` in the
  copy, and rebinds ``an.adapters.cutout.render.runtime_dir``. The shipped
  ``runtime.js`` is never written to.
- ``supersample`` reaches the SAME runtime seam — ``resolution: k,
  autoDensity: false`` in the Pixi application options — and then a second one
  it cannot do without: it rebinds
  ``an.adapters.cutout.render._capture_frames`` so the k-times PNGs are
  block-mean-resolved back to the declared size **in the frame stage**, before
  ffmpeg or the metrics or the golden gate read them. That is not tidiness. A
  lever must measure what the product will produce, and everything downstream
  reads the declared resolution off the STAGED SCENE, never off the files.

A knob in the product would be worse than any of them: it would have to be
documented, defended, and kept from being switched on by accident. (an#58 ships
exactly such a knob for supersampling, opt-in — when it lands, this paragraph
and the "no production knob exists for any of this" sentence above it stop being
true and must be rewritten rather than left to rot.)

**Each lever verifies that it applied.** A lever that silently failed to take
produces a run in which nothing moved — which reads exactly like an instrument
that cannot see it, and sends the reader to fix the wrong thing. So
``verify_applied`` is part of the declaration rather than a courtesy, and the
two levers verify it in different places for a structural reason: the encode
argv is recorded in the ledger row, and the runtime is not (the runtime is the
code under test, not a comparability key — see
:data:`an.bench.registry.MUTATION_TOUCHES`).
"""

from __future__ import annotations

import shutil
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterator

from an.bench.registry import MUTATIONS

#: The CRF the encoder lever raises to. 40 rather than 51: measured on the CRF
#: ladder, 40 gives C x8.4, D x8.0 and F -42% on `single_character` — large,
#: unambiguous, and still a rate a human might plausibly ship. 51 is
#: pathological, and a lever nobody would ever pull by accident is a weaker
#: proxy for the regressions this instrument exists to catch.
HIGH_CRF: str = "40"

#: The pixel format the encoder lever switches to. 4:4:4 is an IMPROVEMENT, like
#: `supersample` and unlike the other encode lever — measured on 30 real 1080p
#: frames, the edge-band mean error goes 11.35 -> 3.79, where a mathematically
#: lossless 4:2:0 only reaches 10.15. Losslessness buys 8%; dropping chroma
#: subsampling buys 66%.
PIX_FMT_444: str = "yuv444p"

#: The exact text the AA lever flips, and where. Pinned as a literal so a
#: rename in `runtime.js` fails here — loudly, at the lever — rather than
#: producing a "mutation" that changes nothing.
AA_ON: str = "antialias: true"
AA_OFF: str = "antialias: false"

#: The supersample lever's factor, read at call time so a test can move it. 2
#: rather than 3, deliberately and with the residual on the record: research §3
#: renders each corpus scene at rising k and lets `edge_transition_width`
#: converge, and k=3 reaches that ceiling on every scene that has one while k=2
#: falls 43% short on `saturated_outline` and 33% short on `graded_field`
#: (0.09-0.17 px). It is 2 because **the lever must be the change the product
#: will ship** — an#58 ships k=2 — and a lever measuring a factor nobody will
#: run is a beautiful number about nothing. Cost, read off 1080p because the
#: corpus cannot inform it: 0.126 s/f at k=1, 0.319 at k=2 (2.54x), 0.640 at k=3.
SUPERSAMPLE_K: int = 2

#: The exact text the supersample lever anchors to, and what it inserts. Pinned
#: for the same reason `AA_ON` is: a reformat of the Pixi options object must
#: fail here rather than produce a "mutation" that renders at 1x and then reads
#: as an instrument that cannot see a supersample. `app = new PIXI.Application({`
#: and NOT `new PIXI.Application(`: the shorter form occurs twice in
#: `runtime.js` — the second is inside a comment — and a two-hit anchor would
#: patch prose.
#:
#: **`autoDensity: false` is load-bearing and is the whole plumbing finding.**
#: With it `true`, Pixi sets the canvas CSS size to the LOGICAL size and
#: Chromium composites the k-times backbuffer down before the screenshot — a
#: blind browser downscale with no filter choice and no record of having
#: happened, i.e. the `device_scale_factor` failure wearing the name that most
#: suggests it is the right one. Measured on `aa_probe`, declared 320x240:
#: neither key -> 320x240 PNGs; `resolution: 2, autoDensity: false` -> 640x480;
#: `resolution: 2, autoDensity: true` -> 320x240. Neither key is in the shipped
#: options today, so the engine default `RESOLUTION: 1` applies silently and
#: both have to be introduced.
#: Since an#58 the product owns `resolution` / `autoDensity: false` and reads
#: the factor from an injected global, so the lever **overrides the line that
#: reads it** rather than writing a second copy of the product's Pixi options.
#: A lever that reproduces the code it is examining is examining itself.
#:
#: Injecting the global does NOT work and the reason is worth keeping: the
#: product sets `window.anSupersample` from `ctx.supersample` immediately before
#: `anLoadScene`, so it would overwrite whatever the lever put there. Caught by
#: an#54's shape guard — 160x120 frames against a 320x240 declaration — which is
#: what that guard is for.
APP_OPEN: str = "const resolution = Math.max(1, (NS.anSupersample | 0) || 1);"
SUPERSAMPLE_OPTIONS: str = "        const resolution = {k};  // supersample lever"


class MutationError(RuntimeError):
    """A lever could not be applied, or applied and left no trace."""


@dataclass(frozen=True, slots=True)
class Lever:
    """One deliberate, declared change to the pipeline, with the evidence that it took."""

    name: str
    side: str
    what: str
    why: str
    apply: Callable[[], Any]
    #: Given the ledger row the mutated run produced, raise unless the lever's
    #: fingerprint is in it. ``None`` when the row cannot carry one — see the
    #: module docstring.
    verify_row: Callable[[dict], None] | None = None


@contextmanager
def _pix_fmt_444() -> Iterator[None]:
    """Encode the delivered mp4 at 4:4:4, leaving the lossless reference alone.

    Rebinds `render.DEFAULT_PIX_FMT`, which `_ffmpeg_mux` reads as a module
    global at call time and `environment_record` reads for the row — so the
    encode and the recorded environment move together and cannot disagree.
    Exactly the seam `_high_crf` uses for `DETERMINISTIC_X264_ARGS`, and it
    breaks the same way if either is hoisted into a default argument.

    It does NOT reach `an.bench.imageio.lossless_encode_command`, which spells
    `yuv420p` itself: the lossless reference must stay a fixed target, or every
    encode-side metric is measured against something that moved with the lever.
    """
    from an.adapters.cutout import render

    original = render.DEFAULT_PIX_FMT
    render.DEFAULT_PIX_FMT = PIX_FMT_444
    try:
        yield
    finally:
        render.DEFAULT_PIX_FMT = original


def _verify_pix_fmt_444(row: dict) -> None:
    """The row must record the 4:4:4 format, not merely a different one."""
    recorded = (
        ((row.get("provenance") or {}).get("environment") or {}).get("encode_side")
        or {}
    ).get("pix_fmt")
    if recorded != PIX_FMT_444:
        raise MutationError(
            f"the mutated row records pix_fmt={recorded!r}, not {PIX_FMT_444!r}. "
            "The lever did not reach the encode, so every 'nothing moved' below "
            "is about the lever and not about the instrument."
        )


@contextmanager
def _high_crf() -> Iterator[None]:
    """Raise the delivered encode's CRF, leaving the lossless reference alone."""
    from an.adapters.cutout import render

    original = render.DETERMINISTIC_X264_ARGS
    args = list(original)
    try:
        index = args.index("-crf")
    except ValueError as e:  # pragma: no cover - the pin would have to be removed
        raise MutationError(
            "`-crf` is not in DETERMINISTIC_X264_ARGS, so the encoder lever has "
            "nothing to pull. The rate control moved; re-point this lever at it "
            "rather than letting a mutation quietly become a no-op."
        ) from e
    args[index + 1] = HIGH_CRF
    render.DETERMINISTIC_X264_ARGS = tuple(args)
    try:
        yield
    finally:
        render.DETERMINISTIC_X264_ARGS = original


def _verify_disabled_aa(row: dict) -> None:
    """The row must record a runtime digest that is NOT the shipped runtime's.

    The encode lever's fingerprint is in the row already (`x264_argv`); this one
    had none, so `assert not report["mutation_may_not_have_applied"]` asserted
    nothing for it and a lever that silently failed to take would have read as
    an instrument that could not see it (an#41 review). Compared against the
    pristine runtime rather than a stored constant, so a legitimate change to
    `runtime.js` does not turn this into a re-baselining chore.
    """
    from an.bench.environment import runtime_sha256

    recorded = (
        ((row.get("provenance") or {}).get("environment") or {}).get("render_side")
        or {}
    ).get("runtime_sha256")
    if recorded is None:
        raise MutationError(
            "the row records no `render_side.runtime_sha256`, so there is no way "
            "to tell whether the AA lever reached the renderer."
        )
    if recorded == runtime_sha256():
        raise MutationError(
            "the row's runtime digest is the SHIPPED runtime's, so the AA lever "
            "did not reach the render. Every 'nothing moved' below is about the "
            "lever and not about the instrument."
        )


def _expected_runtime_sha256(patch: Callable[[str], str]) -> str:
    """The digest `environment.runtime_sha256` reports for a run staged under ``patch``.

    Recomputed from the SHIPPED tree rather than stored as a constant, for the
    reason `_verify_disabled_aa` gives: a legitimate change to `runtime.js` must
    not turn this into a re-baselining chore. Mirrors
    `an.bench.environment.runtime_sha256` exactly — relative path, then bytes, in
    sorted order — with `runtime.js`'s bytes substituted and `STAGING_IGNORE`
    excluded on both sides.
    """
    import hashlib

    from an.adapters.cutout.runtime_files import runtime_dir

    root = runtime_dir()
    digest = hashlib.sha256()
    for path in sorted(
        p
        for p in root.rglob("*")
        if p.is_file() and not set(p.parts) & set(STAGING_IGNORE)
    ):
        digest.update(str(path.relative_to(root)).encode("utf-8"))
        body = path.read_bytes()
        if path.name == "runtime.js":
            body = patch(body.decode("utf-8")).encode("utf-8")
        digest.update(body)
    return digest.hexdigest()


def _verify_supersample(row: dict) -> None:
    """The row must record the digest a RESOLUTION-patched runtime produces.

    Positive, where `_verify_disabled_aa` is negative, and the difference is
    load-bearing rather than pedantic. **Both render levers stage through one
    seam and both move `render_side.runtime_sha256`**, so "the digest is not the
    shipped runtime's" is satisfied by EITHER of them — copy that check here and
    a `supersample` row rendered with `antialias: false` verifies clean, the
    lever table gets written from AA-off numbers, `mutation_may_not_have_applied`
    stays empty, and nothing anywhere goes red. That is the same failure
    `_verify_disabled_aa` was itself introduced to close (a lever with no
    fingerprint in the row), one level along.
    """
    from an.bench.environment import runtime_sha256

    recorded = (
        ((row.get("provenance") or {}).get("environment") or {}).get("render_side")
        or {}
    ).get("runtime_sha256")
    if recorded is None:
        raise MutationError(
            "the row records no `render_side.runtime_sha256`, so there is no way "
            "to tell whether the supersample lever reached the renderer."
        )
    if recorded == runtime_sha256():
        raise MutationError(
            "the row's runtime digest is the SHIPPED runtime's, so the "
            "supersample lever did not reach the render. Every 'nothing moved' "
            "below is about the lever and not about the instrument."
        )
    expected = _expected_runtime_sha256(
        lambda source: _supersample_patch(source, k=SUPERSAMPLE_K)
    )
    if recorded != expected:
        raise MutationError(
            f"the row records runtime digest {recorded[:12]}..., but a runtime "
            f"patched to resolution {SUPERSAMPLE_K} hashes to {expected[:12]}... "
            "Some OTHER render-side lever staged that runtime. 'Not the shipped "
            "one' is satisfied by any of them, which is why this check is an "
            "equality and not an inequality."
        )


def _verify_high_crf(row: dict) -> None:
    argv = (
        ((row.get("provenance") or {}).get("environment") or {}).get("encode_side")
        or {}
    ).get("x264_argv") or []
    if "-crf" not in argv or argv[argv.index("-crf") + 1] != HIGH_CRF:
        raise MutationError(
            f"the mutated row records x264_argv={argv}, which is not crf {HIGH_CRF}. "
            "The lever did not reach the encode, so every 'nothing moved' below "
            "is about the lever and not about the instrument."
        )


#: Excluded from the staged copy so the staged tree is a pure function of the
#: shipped source and the patch — which is what lets `_verify_supersample`
#: RECOMPUTE the digest it expects instead of settling for "not the shipped
#: one". `runtime_sha256()` walks whatever `render.runtime_dir()` returns, so
#: with this excluded on the staging side and on the recompute side, the two
#: hash byte-identical file sets.
STAGING_IGNORE: tuple[str, ...] = ("__pycache__",)


@contextmanager
def _patched_runtime(patch: Callable[[str], str], *, prefix: str) -> Iterator[Path]:
    """Stage a copy of the runtime with ``runtime.js`` patched, and point the renderer at it.

    The seam both render-side levers reach through. The shipped ``runtime.js``
    is never written to. ``render.runtime_dir`` is a module attribute rebound
    for the duration, and ``an.bench.environment.runtime_sha256`` re-imports it
    at call time, so the staged digest is what lands in the row.
    """
    from an.adapters.cutout import render
    from an.adapters.cutout.runtime_files import runtime_dir

    staged = Path(tempfile.mkdtemp(prefix=prefix)) / "runtime"
    shutil.copytree(
        runtime_dir(), staged, ignore=shutil.ignore_patterns(*STAGING_IGNORE)
    )
    try:
        source = (staged / "runtime.js").read_text(encoding="utf-8")
        (staged / "runtime.js").write_text(patch(source), encoding="utf-8")
    except BaseException:
        # Clean up before refusing: the refusal path is loud and rare, but a
        # lever that leaks half a megabyte every time it declines is a lever
        # nobody wants to run in a loop.
        shutil.rmtree(staged.parent, ignore_errors=True)
        raise
    original = render.runtime_dir
    render.runtime_dir = lambda: staged
    try:
        yield staged
    finally:
        render.runtime_dir = original
        shutil.rmtree(staged.parent, ignore_errors=True)


def _disable_aa_patch(source: str) -> str:
    """``runtime.js`` with PixiJS multisampling off."""
    if source.count(AA_ON) != 1:
        raise MutationError(
            f"expected exactly one {AA_ON!r} in runtime.js, found "
            f"{source.count(AA_ON)}. The AA lever is pinned to that literal, and "
            "a rename must fail here rather than produce a mutation that changes "
            "nothing."
        )
    return source.replace(AA_ON, AA_OFF)


@contextmanager
def _disabled_aa() -> Iterator[None]:
    """Turn PixiJS multisampling off, in a copy of the runtime."""
    with _patched_runtime(_disable_aa_patch, prefix="an-mutation-runtime-"):
        yield


def _supersample_patch(source: str, *, k: int) -> str:
    """``runtime.js`` with the Pixi application built at ``k`` times the declared size."""
    if source.count(APP_OPEN) != 1:
        raise MutationError(
            f"expected exactly one {APP_OPEN!r} in runtime.js, found "
            f"{source.count(APP_OPEN)}. The supersample lever is pinned to that "
            "literal, and a rename must fail here rather than produce a mutation "
            "that renders at 1x — which would read as an instrument that cannot "
            "see a supersample."
        )
    return source.replace(APP_OPEN, SUPERSAMPLE_OPTIONS.format(k=k).strip())


@contextmanager
def _supersample() -> Iterator[None]:
    """Render at ``SUPERSAMPLE_K`` times the declared size and resolve back exactly.

    **TWO seams, and the second is not optional.** Patching `runtime.js` alone
    leaves k-times PNGs in `frames_dir`, and nothing downstream reads a
    resolution off the files: `capture.resolution` comes from the STAGED
    SCENE's `meta.width/height`. So ffmpeg would mux a 640x480 video against a
    320x240 declaration, the golden gate would report `shape_mismatch` and
    withhold family B's number instead of comparing it, and family A would be
    computed on a k^2-larger buffer. Since an#54 that last one is a loud
    refusal (`run._assert_declared_resolution`) rather than a plausible number,
    which is exactly why an#54 was sequenced first.

    So the lever resolves in the frame stage, between `_capture_frames` and
    `_ffmpeg_mux`, exactly where an#58 will put the product's resolve. **A lever
    must measure what the product will produce**: one that measured the raw
    supersampled buffer would be declaring directions for a picture nobody will
    ever see.

    `render._capture_frames` is called as a module global from
    `CutoutRenderer.render`, and per-shot fan-out is a `ThreadPoolExecutor` in
    this process, so the rebinding reaches every shot at any `parallel`.
    """
    from an.adapters.cutout import render

    with _patched_runtime(
        lambda source: _supersample_patch(source, k=SUPERSAMPLE_K),
        prefix="an-mutation-supersample-",
    ):
        original = render._capture_frames

        def _capture_then_resolve(page, total_frames, fps, frames_dir, _factor=None):
            # Forces the PRODUCT's own `supersample` parameter (an#58) rather
            # than resolving separately, so this lever runs the exact path a
            # user gets from `an render --supersample 2` — same function, same
            # place in the pipeline. It has to be forced here because the bench
            # cannot pass it through `BENCH_RENDER_KWARGS`: that dict is a
            # comparability key, and a factor in it would refuse every metric in
            # the row rather than measure one.
            original(page, total_frames, fps, frames_dir, SUPERSAMPLE_K)

        render._capture_frames = _capture_then_resolve
        try:
            yield
        finally:
            render._capture_frames = original


#: The levers, keyed by the mutation name the registry declares. At least one
#: per SIDE is mandatory and the two sides are **disjoint on purpose**: an
#: encoder lever cannot touch a golden-frame metric, because the corpus sits
#: UPSTREAM of the encoder. So requiring three families from a CRF change alone
#: would fail for a reason that has nothing to do with the instrument being
#: blind — and that failure would be misdiagnosed as the harness being wrong.
#:
#: The two RENDER levers are not redundant: measured, they reach complementary
#: scenes. `disabled_aa` is nearly blind to the descriptor path (96 differing
#: pixels of 12.4M on `promote_demo`, because MSAA applies to WebGL geometry and
#: an SVG sprite is a pre-rasterised texture); `supersample` hits that same
#: scene hardest of all six (-34.8% edge width, because the sprite rasterises AT
#: 2x rather than being stretched up from a 1x texture).
LEVERS: dict[str, Lever] = {
    "high_crf": Lever(
        name="high_crf",
        side="encode",
        what=f"raise the delivered encode from the pinned CRF to {HIGH_CRF}",
        why=(
            "moves only post-encode metrics. The golden corpus is upstream of the "
            "encoder, so family B cannot see this by construction — which is the "
            "reason two disjoint levers are mandatory."
        ),
        apply=_high_crf,
        verify_row=_verify_high_crf,
    ),
    "disabled_aa": Lever(
        name="disabled_aa",
        side="render",
        what="build the PixiJS application with multisampling off",
        why=(
            "moves the render-side families and fires the golden tripwire. Its "
            "effect is scene-dependent by measurement, not by accident: MSAA "
            "applies to WebGL geometry, so an SVG sprite is nearly blind to it "
            "(96 differing pixels of 12.4M) and axis-aligned `drawRect` edges are "
            "bit-identical with it on or off. `aa_probe` exists so the corpus "
            "contains edges this lever can actually change."
        ),
        apply=_disabled_aa,
        verify_row=_verify_disabled_aa,
    ),
    "supersample": Lever(
        name="supersample",
        side="render",
        what=(
            f"build the PixiJS application at resolution {SUPERSAMPLE_K} with "
            "`autoDensity: false`, and resolve the frames back to the declared "
            f"size with an exact {SUPERSAMPLE_K}x{SUPERSAMPLE_K} block mean "
            "before anything reads them"
        ),
        why=(
            "the instrument's exam against a change somebody WANTS to ship "
            "(an#56). Run instead as a plain commit-to-commit diff, "
            "`_verdict_by_optimum` reports it as 2 false regressions and 7 "
            "unearned improvements, plus 7 unscored `changed`s including the "
            "metric the wave's done-when names — a table that looks like "
            "evidence and is not. Its effect is scene-dependent BY MEASUREMENT "
            "and in the exact inverse of `disabled_aa`: +2.6% to +8.0% edge "
            "width on the five procedural scenes and -34.8% on `promote_demo`. "
            "The two render levers therefore reach complementary scenes, which "
            "strengthens the harness rather than diluting it."
        ),
        apply=_supersample,
        verify_row=_verify_supersample,
    ),
    "pix_fmt": Lever(
        name="pix_fmt",
        side="encode",
        what=f"encode the delivered mp4 at {PIX_FMT_444} instead of 4:2:0",
        why=(
            "the encoder's one FIRST-ORDER lever, and the second improvement in "
            "this set. Measured on 30 real 1080p frames the edge-band mean error "
            "goes 11.35 -> 3.79, where a mathematically lossless 4:2:0 only "
            "reaches 10.15 — losslessness buys 8%, dropping chroma subsampling "
            "buys 66%. Inside the panel it is the one lever whose subject and "
            "whose witness are the same thing: `chroma_edge_dCr` measures chroma "
            "error at an edge, and this removes chroma subsampling. It is also "
            "the lever that shows family A is blind to the encoder BY "
            "MEASUREMENT rather than by argument: exactly +0.0% on all six "
            "scenes, every family-A metric."
        ),
        apply=_pix_fmt_444,
        verify_row=_verify_pix_fmt_444,
    ),
}


def _check_registered() -> None:
    missing = [m for m in MUTATIONS if m not in LEVERS]
    if missing:  # pragma: no cover - an import-time invariant
        raise MutationError(
            f"the registry declares {missing} but no lever pulls them. A mutation "
            "with a full column of predictions and no way to apply it is a "
            "criterion nobody can ever evaluate."
        )
    extra = [name for name in LEVERS if name not in MUTATIONS]
    if extra:  # pragma: no cover - an import-time invariant
        raise MutationError(
            f"{extra} has a lever but no predictions. Every metric must declare "
            "what it will do under every mutation, in advance — that is the whole "
            "of the criterion."
        )


_check_registered()


def mutated_row(name: str, **run_kwargs: Any) -> dict:
    """Render the corpus with one lever pulled, and prove the lever took.

    Deliberately returns a row rather than a comparison: what to do with it is
    :mod:`an.bench.compare`'s job, and keeping the two apart is what lets the
    criterion be evaluated against a row written months ago.
    """
    from an.bench.run import run_bench

    lever = LEVERS[name]
    with lever.apply():
        row = run_bench(write=False, **run_kwargs)
    if lever.verify_row is not None:
        lever.verify_row(row)
    return row
