---
name: an-dev-bench
description: Use when working on `an bench`, the metrics ledger, the golden corpus, or anything that measures rendered output in the `an` repo. Triggers on "add a metric", "the ledger", "an bench", "golden frames", "mutation test the harness", "why is this number moving", "bench corpus", or any change to `an/bench/`, `misc/bench/`, or the encode/decode flags in `an/adapters/cutout/render.py`.
---

# Working on `an`'s measurement instrument

`an bench` renders a fixed corpus and writes one ledger row to
`misc/bench/ledger/<date>-<sha>.json`. Its job is **not** to say whether the
animation is good. Its job is to make a deliberate degradation move a number in
a direction declared in advance.

Authorities, in order: `misc/docs/wave2_research.md` §1/§1b/§1c/§2/§3,
`misc/docs/wave2_crossarch_verdict.md`, then this file. Do not re-derive either
— both were adversarially reviewed and several of their conclusions are the
*opposite* of the obvious answer.

## Five things that are not what you would guess

1. **The metric list is not the epic's.** All twelve originally-proposed
   metrics were refuted. `mean_adjacent_frame_ssim` in particular is **out**: it
   moves the wrong way (0.958 at crf18 → 0.977 at crf51, because a crushed video
   is smoother), so shipping it would put a number in the ledger that rewards
   the degradation the gate exists to catch. Its use as a frozen-render detector
   in `an/verify/media_quality.py` is a different, legitimate job — leave it.
2. **Render-side and encode-side metrics are blind to each other by
   construction**, and their *comparison rules differ*. Render-side pixels are
   ISA- and OS-invariant at a pinned Chromium build, so those rows compare on
   any machine. Encode-side rows are **machine-scoped and must be refused, not
   banded** — a band wide enough to absorb an x264 build change would swallow
   `flat_field_deviation`'s entire crf18→23 signal.
3. **`null` and "no change" are different, and the difference is load-bearing.**
   "No change by construction" is a tautology; counting it lets any pre-encode
   statistic pad the witness count for free. Four states:
   `measured` / `gated` (the comparison is impossible) / `unavailable` (the
   check did not run) / `no_change` (a prediction, never a value, never counts).
4. **The criterion is families, not metrics.** ">=3 metrics from >=3 distinct
   causal families, per mutation, in a direction declared in advance". An
   encoder lever cannot touch a golden-frame metric — the corpus is *upstream*
   of the encoder — so counting bare metrics fails for a reason that has
   nothing to do with the instrument being blind.
5. **The vision judge is deliberately not a ledger column.** Not because its
   input is nondeterministic (over frozen frames it is perfectly reproducible)
   but because a cassetted judge is a **constant**, invariant to the code under
   test, so it can never move under a deliberate degradation. Recorded as a
   decision, not an oversight.

## The single largest risk, and the assertion that closes it

Every encode-side metric is `f(source[i], decoded[i])`. If the two legs are
decoded in different colour ranges or matrices, all of them measure that
mismatch and report it as encoder damage — with plausible, monotone numbers.

Measured on this repo, against a `-qp 0` lossless encode of the same PNGs:

| source decode | luma residual |
|---|---|
| `-pix_fmt yuv444p` (the obvious form) | mean 5.33, max 20 |
| `-vf scale=out_range=tv:out_color_matrix=bt709 -pix_fmt yuv444p` | **0.0000, max 0** |

And the natural fix does not work on the natural spelling: ffmpeg **silently
ignores** the `scale` filter's `out_color_matrix` / `out_range` options for
`-pix_fmt gray`, so research §1.4's literal pseudocode reintroduces the defect
with a fix applied that does nothing. Read the luma out of the pinned `yuv444p`
decode instead — one subprocess serves both the luma and the chroma metrics.

`decode_calibration()` runs on every bench invocation and **raises** on any
nonzero residual. The recorded zero is the evidence that a row's numbers mean
what they say.

## Adding a metric

1. Add the computation to `an/bench/metrics.py`. **Pure numpy, no I/O, no
   subprocess** — that is what lets the whole panel run in the default CI leg,
   which is the only part of this work main CI can see.
2. Declare it in `an/bench/registry.py`: family letter, unit, one-sentence
   explanation, `Optimum`, and a `Prediction` for **every** mutation. The
   dataclass refuses a declaration that counts a tautology or a gated value.
3. Emit it in `an/bench/run.py::_shot_metrics`. The ledger builder refuses a
   row that omits any declared metric — an absent row and a null row look the
   same to a reader and mean opposite things.
4. Add a test in `tests/test_bench_metrics.py` against arrays whose answer is
   arithmetic, and **mutation-test it**: revert the fix, confirm the test goes
   red. A guard green inside its own failure mode is decoration.

## Adding a corpus scene

`an/bench/corpus.py`. Every fixture declares `expect_visual_kinds`, checked
against the scene JSON **the browser actually loaded**. This is not
belt-and-braces: the first cross-architecture capture had three CI runners agree
perfectly about a picture that was not the picture, and the agreement read as a
clean positive result.

The bench renders with `strict_assets=True` (an#33), so a fixture that *intends*
the built-in placeholder rig must **declare** it — see `_declare_procedural_rig`,
whose store entry lists exactly `_PLACEHOLDER_PARTS` and therefore renders a
byte-identical picture. Falling into the rig and declaring it are the same
pixels and different records, which is the whole point of an#33.

Four scenes the corpus still lacks, and each is missing for a measured reason
(an#38 builds them): a large flat or gently-graded field (every edge metric is
masked to 5–10% of the frame); a saturated fill under a black outline (the real
example frames are 31 colours on white, and the measured 4:2:0 edge error is
~3x smaller than on a saturated pattern); a multi-shot project (a single-shot
render short-circuits the concat to `shutil.copy`, so `_ffmpeg_concat` is never
exercised); and an `aa_probe` with edges at non-axis angles (axis-aligned
`drawRect` edges are bit-identical with MSAA on or off, so a corpus of
axis-aligned art cannot validate an AA metric at all).

## The palette, and why it has a permanent diagnostic

`off_palette_pixel_fraction` means "not one of the colours the compiler
declared". A derivation that under-collects turns it into a large, plausible
number with no error anywhere. Three traps:

- `parse_color` mirrors the runtime's rule (`hex.padEnd(6,'0').slice(0,6)`),
  **not CSS**. `"#222"` is `0x222000`, not `0x222222`.
- Some painted colours are **runtime constants** never present in the JSON —
  the eye whites, the four mouth colours. A source-scanning test cross-checks
  the declared table against `runtime.js` itself.
- Some `visual.color` values are **inert**: `drawMouthShape` never reads its
  node's colour, and every `svg_sprite` carries the `#888888` schema default.

Every row records `off_palette_top_colours`, each classified with `blend_of` —
the two declared colours it sits between. All-blends means the metric is
reporting anti-aliasing correctly; a non-blend near the top means the palette
missed a literal and the number is inflated. That is a recorded field rather
than an eyeball check, on purpose.

## Standing honesty rule

**Never write that a rendering behaviour is "verified in CI."** It is verified
on a developer machine, on a labelled PR, or on an on-demand run — say which.
To add the label: `gh api -X POST repos/thorwhalen/an/issues/<N>/labels -f
'labels[]=run-browser-tests'`. **Not** `gh pr edit --add-label`, which prints a
projects-classic error, exits 0, and applies nothing.
