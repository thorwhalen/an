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

## The single largest risk, and how the answer changed

Every encode-side metric is `f(reference[i], decoded[i])`. Get the reference
wrong and all of them measure something other than the encoder — with
plausible, monotone numbers.

**The reference is the lossless encode, not the PNGs.** The obvious design
converts the source PNGs to YUV and compares against that. Two things make it
wrong, and the second was found by CI after the first had been fixed:

1. The conversion must be range- and matrix-pinned
   (`-vf scale=out_range=tv:out_color_matrix=bt709`) or it is off by ~5 code
   values. And the natural fix does not work on the natural spelling: ffmpeg
   **silently ignores** the `scale` filter's `out_color_matrix` / `out_range`
   options for `-pix_fmt gray`, so research §1.4's literal pseudocode applies a
   fix that does nothing. Read the luma out of a `yuv444p` decode.
2. **Even pinned, the conversion is build-dependent.** It reproduces the
   encoder's input exactly on ffmpeg 8.1 (0.0000, max 0) and misses by mean
   0.63 / max 5 on the Linux CI runner's older build — 42% of
   `coded_luma_edge_error`'s whole crf23 value. The first design asserted the
   agreement as a hard equality; it passed locally and failed on Linux.

The fix is not a tolerance. `-qp 0` is lossless, so **the qp0 decode's luma
plane IS the plane libx264 received**, on every build, by definition.
Referencing the metrics to it removes the assumption rather than widening it.

Two metrics still reference the PNG conversion, and each row says which:

- **the chroma metric**, because its subject *is* the 4:2:0 subsampling that
  happens during that conversion — referenced to a qp0 file, whose chroma is
  already subsampled, it would read ~0 and measure nothing;
- **`encode_ringing_excess`**, because it cancels a term that exists only when
  both its legs share that reference. Against the lossless leg its second term
  is 0 by construction and the metric degenerates into raw overshoot, which is
  the form the research refuted.

`chroma_edge_dY` exists for the same reason and is **not** a duplicate of
`coded_luma_edge_error`: the same expression on different references. They read
identically on a build where the conversion is exact, which the row says
outright via `references_coincide`.

**A counting encode-side witness must reference `lossless`** — a build-dependent
conversion term has no business inside the number a mutation test reads. There
is a test for that.

## Adding a metric

1. Add the computation to `an/bench/metrics.py`. **Pure numpy, no I/O, no
   subprocess** — that is what lets the whole panel run in the default CI leg,
   which is the only part of this work main CI can see.
2. Declare it in `an/bench/registry.py`: family letter, unit, one-sentence
   explanation, `Optimum`, and a `Prediction` for **every** mutation. The
   dataclass refuses a declaration that counts a tautology or a gated value.
3. Emit it in `an/bench/run.py::_scene_metrics`. The ledger builder refuses a
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

The four scenes an#38 added — `graded_field`, `saturated_outline`,
`multi_shot`, `aa_probe` — live in `misc/bench/corpus/`, **not** under
`examples/`, and are **committed whole with no `prepare` step**. Two reasons,
both load-bearing: `.gitignore` excludes every `examples/*/assets/`, and a
metrics fixture has to hold still — a fixture whose pixels depend on a generator
elsewhere in the repo needs a re-bless every time that generator changes.

New corpus scenes go there too. Give each one two `golden_frames` and a
`golden_note` saying what moves between them.

## The golden gate (an#38)

`an/bench/golden.py` compares today's render against committed PNGs.
`an/bench/png.py` is the codec: filter-0 writer, full-filter reader, numpy and
stdlib only. `misc/bench/golden/README.md` is the operator's guide; read it
before touching a golden.

Four things that are easy to get wrong here and still look like they work:

1. **The criterion is `sha256` of the decoded array, never the file bytes.**
   Chromium 1187 → 1223 changed 144 of 144 PNG files and zero pixels.
2. **The path keys on the Chromium build alone** — the platform and arch
   segments are measurably inert, and a Playwright bump should become a new
   path requiring a deliberate re-bless rather than a red test.
3. **Three absences are three gates**, plus a fourth for a bless run:
   `golden_frames_undeclared`, `golden_absent_for_chromium_build`,
   `chromium_build_unknown`, `blessed_this_run`. `probe_browser` never raises —
   it returns `{"error": ...}` — so without the third an un-probeable browser
   reads exactly like a scene nobody has blessed.
4. **A run that blessed must not also report a pass.** Comparing against a
   golden the same run wrote is a tautology, and the row would carry a perfect
   score no code could have failed.

`an bench --bless "<reason>"` takes the reason as the flag's **value**, so a
bless with no recorded reason cannot be typed. It refuses a blank reason, fewer
than two frames, a pixel-identical pair, a time past the end, and an unknown
build; and it removes any golden it no longer blesses.

**Choosing the second time is not mechanical.** `duration/2` is not a safe
default — measured on `promote_demo`, frame 0 and frame 36 differ by exactly
zero pixels. And a pair can be blind to a mutation the scene is not:
`graded_field`'s marker advances by a sub-pixel step, so on frames 0, 1, 6, 8
and 11 it lands on an exact pixel boundary and AA-off changes zero pixels there.
Its second golden moved from f0006 to f0004 for that reason.

## Multi-shot scenes pair by TIMELINE order, never by directory name

`an/render.py` concatenates `[r.mp4_path for r in shot_results]` built from
`list(scene.timeline)`. `an.bench.corpus.iter_shot_dirs` takes a **mandatory**
`order` argument for exactly this reason: a directory-name sort agrees with the
timeline only when the ids happen to sort that way, and when it does not, every
encode-side metric pairs source frame *i* of one shot against decoded frame *i*
of another and reports plausible numbers. The `multi_shot` fixture's ids are
`intro` then `beat` **deliberately** — they sort the other way, so the fixture
is what notices. A test pins that they still disagree.

The whole scene is measured as one concatenated sequence
(`_timeline_frames_dir`), so `scene_contract_sha256` covers every shot — but a
single-shot scene still hashes exactly as it did before, so rows written earlier
stay comparable.

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

## Comparing rows (an#40)

`an bench-compare` reads two rows. **Refusing is the feature** — two rows
measured on different scenes, resolutions or x264 builds are not "one better and
one worse"; every number in them is uninterpretable relative to the other.

Five things about it that are easy to get wrong:

1. **The two sides are scoped oppositely, and both are measured.** An x264 or
   ISA change refuses the **encode-side** metrics and leaves the render side
   comparable. A Chromium or Playwright change refuses the **render-side**
   metrics and leaves the encode side comparable. Refusing everything on either
   piece of evidence throws away the half of the panel that can still be read.
2. **A key absent from one row is unknown, not different.** The ledger grows
   additively (an#38 added `shot_order` without bumping `SCHEMA_VERSION`,
   correctly), so refusing on an absent field would make every future addition
   retroactively destroy comparability with every row already written. Absences
   are caveats; `schema_version` guards a genuinely unreadable row.
3. **Comparability keys are PARAMETERS, never measurements.** `masks.edge.threshold`
   is a key; `masks.edge.edge_px` is not — it changes precisely when the render
   changes, which is when the rows are most worth comparing. Same reason
   `today_sha256` sits in the golden diagnostics rather than in a key.
4. **A metric's own declaration is a comparability key.** If `family` or
   `optimum` moved between the rows, the metric means something different in
   each — refused for that metric alone. This is why every row carries its full
   `metric_declarations` block instead of trusting the installed registry.
5. **There is no tolerance band and none is needed.** Two consecutive runs on
   one machine are bit-identical across all six scenes. The report prints the
   relative delta so magnitude is visible without one.

Five per-metric verdicts under a mutation: `as_declared`, `contrary`,
`did_not_move` (the lever never reached it — a different fix from `contrary`),
`gated`, `unexpected_movement` (a metric declared orthogonal that moved).
The criterion is **families, not metrics**: two witnesses from one family count
once.

## Pulling a lever (an#41)

`an/bench/mutations.py` holds the two levers. **No production knob exists for
either, deliberately** — a knob would have to be documented, defended, and kept
from being switched on by accident. Each reaches an existing seam from outside:

- `high_crf` rebinds `render.DETERMINISTIC_X264_ARGS`. `_ffmpeg_mux` reads that
  name as a module global at call time so the rebinding reaches the delivered
  encode — and it does **not** reach `imageio.lossless_encode_command`, which
  bound the tuple at import. That is exactly right: the lossless reference must
  stay lossless, or every encode-side metric is measured against a moving target
  and the lever produces beautiful numbers about nothing.
- `disabled_aa` copies the staged runtime, flips PixiJS's `antialias` in the
  copy, and rebinds `render.runtime_dir`. The shipped `runtime.js` is untouched.

**Each lever verifies that it applied.** A lever that silently failed to take
produces a run in which nothing moved — indistinguishable from an instrument
that cannot see it, and it sends you to fix the wrong thing. The encode lever
checks the row (`x264_argv` is recorded); the AA lever cannot, because the
runtime is the code under test rather than a comparability key, so it pins the
literal it flips and raises if it is not there exactly once.

**Measured, both levers, all six scenes:**

| lever | criterion met on | why not everywhere |
|---|---|---|
| `high_crf` | all six (4/3 on five, 3/3 on `single_character`) | family E inverts on `single_character` only |
| `disabled_aa` | `aa_probe`, `multi_shot`, `saturated_outline` | family F's sign is scene-dependent; MSAA cannot reach axis-aligned art or an SVG sprite |

So the criterion is **per scene, met on at least one**, and the corpus has to
contain a scene the lever can reach. That is what makes `aa_probe` load-bearing
rather than decorative.

## Mutation-testing the guards, as a runnable artifact

`an bench-mutants` (registry in `an/bench/mutants.py`). "N mutants, all caught"
is unfalsifiable after the fact when the mutations lived in a scratch script;
these are declared data, so the proof re-runs. ~40 s for the full sweep.

Three properties of the declaration, each earned:

- **Each mutant names the guard it must break.** A mutation nobody expected to
  be caught is a fact about the code; one with a named catcher is a claim about
  a *test*.
- **The whole file runs, never `-k`.** A filter that happens to exclude the
  catching test reports "not caught".
- **The `old` text is pinned exactly**, and a default-leg test asserts every
  site still occurs exactly once — because a mutant whose source moved has
  silently stopped proving anything and nothing else would notice.

Add one whenever you add a guard. If it survives, the guard is decoration.

## Two measured facts that change what counts as a witness

Both found while building an#38, both by running the levers rather than by
reading the research:

- **`video_stream_bytes` under `disabled_aa` has a scene-dependent sign.** It is
  declared `increase` (+5.5%). Measured: **+6.1% on `aa_probe`** (diagonal edges
  → AA-off makes a high-frequency staircase that costs bits) and **−6.1% on
  `single_character`** (axis-aligned art → AA-off removes intermediate colours
  and the picture gets cheaper). So family F is an honest witness for that lever
  only on a scene with non-axis-aligned edges, and an#41's criterion has to be
  evaluated **per scene**, which `ledger.witnesses` already is.
- **`encode_flicker_on_held_pixels` under `high_crf` is non-monotone on the
  ladder, and scene-dependent at the step the lever uses.** Declared `increase`;
  the research's synthetic reference is 0.0321 / 0.0394 / 0.0848 at crf18/23/51.
  Measured on `single_character` across crf 18/23/28/33/40/51: 0.000648 /
  0.007018 / 0.000985 / 0.000916 / 0.001137 / 0.001685 — it **peaks at crf23**
  and is three orders of magnitude smaller than the reference. The mechanism is
  the one the registry already documents for the half-res-upscale case: at high
  CRF the whole frame flattens into large uniform skip regions, so held pixels
  stop moving.

  **But at the crf23 → crf40 step the lever actually uses, it moves as declared
  on five of six scenes** and contrary only on `single_character` (aa_probe
  +115%, graded_field +114%, multi_shot +103%, promote_demo +446%,
  saturated_outline +48%, single_character −84%). So E is **not** to be demoted
  — a first, two-scene reading of this said "falsified on the real corpus" and
  that was too strong, corrected once `an bench-compare` could evaluate the
  encode lever across the whole corpus. Record the non-monotonicity, do not
  count on E carrying a scene, and note that the criterion never needs it: C, D
  and F are monotone across the whole ladder and satisfy it on all six scenes
  alone.

Also measured: the **descriptor path is nearly blind to the AA lever** (96
differing pixels of 12.4M on `promote_demo`), because MSAA applies to WebGL
geometry and an SVG sprite is a pre-rasterised texture.

## Standing honesty rule

**Never write that a rendering behaviour is "verified in CI."** It is verified
on a developer machine, on a labelled PR, or on an on-demand run — say which.
To add the label: `gh api -X POST repos/thorwhalen/an/issues/<N>/labels -f
'labels[]=run-browser-tests'`. **Not** `gh pr edit --add-label`, which prints a
projects-classic error, exits 0, and applies nothing.
