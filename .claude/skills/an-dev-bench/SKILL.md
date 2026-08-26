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

New corpus scenes go there too. Give each one at least two `golden_frames` (the
rule is at-least-two since an#98: `expressions` pins eight, one per preset, and
the bless refusal of pixel-identical pairs is pairwise) and a
`golden_note` saying what moves between them — and if the scene **speaks**,
commit its `ir/scene.json` with the offline visemes stamped, through a
per-scene carve-out from `.gitignore`'s `misc/bench/corpus/*/ir/` rule: the
bench renders with `auto_audio=False`, so without it the scene is mute on a
clean checkout (`dialogue`, an#96, is the precedent). Every scene also feeds
`expression_min_pairwise_changed_px` (family B, diagnostic, counts nothing; gated
under the two edge levers because it moves with every edge): the smallest pixel
distance between any two pinned frames of today's render — on a two-frame scene
that pair's own change — the ledger's view of `tests/test_expression_goldens.py`. A fixture under
`examples/` that gains dialogue gets the opposite treatment: its `prepare`
regenerates the staged IR from the md, so a developer who ran the example
(`auto_audio=True` persists visemes) cannot move the fixture's contract hash.

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
6. **The defaults are ordered by `generated_at`, not by filename** (an#54).
   Rows are named `<date>-<sha7>.json`, so a filename sort orders same-day rows
   by *sha hex* — and a re-baseline plus its after-run on one day is the normal
   shape of a wave. A row whose stamp is missing or unparseable sorts first, so
   it is dropped rather than becoming the `after` a verdict is drawn from; it
   never raises, because one bad file must not break the listing.
7. **`--strict` exits nonzero on a REFUSAL too** (an#54). It used to `return`
   from the `ComparisonError` handler ahead of the strict block, so an
   unreadable `schema_version` — or a `--mutation` no row declares, which is the
   state every `--strict --mutation <new-lever>` run is in before the lever is
   registered — passed green.
8. **A blessed row is surfaced as a caveat** (an#54). A `--bless` run WROTE the
   goldens it would otherwise have compared against, so its family B is gated
   `blessed_this_run` — and `format_comparison` skips `unchanged` entries, so
   family B does not appear as "unchanged", it does not appear AT ALL.

Five per-metric verdicts under a mutation: `as_declared`, `contrary`,
`did_not_move` (the lever never reached it — a different fix from `contrary`),
`gated`, `unexpected_movement` (a metric declared orthogonal that moved).
The criterion is **families, not metrics**: two witnesses from one family count
once.

## Re-blessing inside a wave: the three-row protocol (an#54)

A `--bless` run cannot be its own evidence, so a wave that moves the goldens
files **three** rows, in this order:

| row | how | what it is for |
|---|---|---|
| **before** | `an bench` on the base commit | the baseline |
| **after-unblessed** | `an bench` on the change, **no `--bless`** | the PR's evidence — the only row that can fail family B |
| **after-blessed** | `an bench --bless "<why>"` | the new baseline |

Two consequences worth knowing before you meet them:

- **A bless row always lands `-dirty`**, because the state that NAMES it is
  re-read *after* the loop the bless writes in (`run_bench`'s
  `naming_git_state`). It therefore drops out of `latest_rows`, which excludes
  `-dirty` — correct, since its family B was never asked.
- **A committed golden is cross-checked against its own bless record** on every
  run, on DECODED PIXELS. A disagreement is `unavailable`, not a pass: the file
  is not the picture a human blessed.

## Pulling a lever (an#41)

`an bench --mutation <lever>` pulls one for a whole run, and asks `--compare`
the per-mutation question instead of "is the second row worse". A mutated row
is **never** filed in the ledger directory — the CLI refuses — because it
measures a pipeline broken on purpose, and `--bless` under a lever is refused
outright.


`an/bench/mutations.py` holds the levers. Two of the three have **no production
knob**, deliberately — a knob would have to be documented, defended, and kept
from being switched on by accident. The third, `supersample`, has one since
an#58 (`an render --supersample N`, opt-in), and the lever now *forces the
product's own parameter* rather than carrying a second copy of the resolve —
a lever that reproduces the code it examines is examining itself. Each lever
reaches an existing seam from outside:

- `high_crf` rebinds `render.DETERMINISTIC_X264_ARGS`. `_ffmpeg_mux` reads that
  name as a module global at call time so the rebinding reaches the delivered
  encode — and it does **not** reach `imageio.lossless_encode_command`, which
  bound the tuple at import. That is exactly right: the lossless reference must
  stay lossless, or every encode-side metric is measured against a moving target
  and the lever produces beautiful numbers about nothing.
- `disabled_aa` copies the staged runtime, flips PixiJS's `antialias` in the
  copy, and rebinds `render.runtime_dir`. The shipped `runtime.js` is untouched.
- `supersample` reaches that **same** runtime seam — `resolution: k,
  autoDensity: false` in the Pixi application options — and then a second one it
  cannot do without: it rebinds `render._capture_frames` so the k-times PNGs are
  block-mean-resolved back to the declared size **in the frame stage**. Not
  tidiness: nothing downstream reads a resolution off the files.
  `capture.resolution` comes from the staged scene's `meta`, so unresolved
  frames would mux a 640x480 video against a 320x240 declaration and put family
  B's number out of reach. **A lever must measure what the product will
  produce.**

**Each lever verifies that it applied.** A lever that silently failed to take
produces a run in which nothing moved — indistinguishable from an instrument
that cannot see it, and it sends you to fix the wrong thing. The encode lever
checks the row (`x264_argv` is recorded); the render levers check the recorded
`runtime_sha256`.

**And with two render levers, "not the shipped digest" stopped being enough.**
Both stage through one seam, so `disabled_aa`'s *inequality* check is satisfied
by either of them: copy it for `supersample` and a row rendered with
`antialias: false` verifies clean, the lever table gets written from AA-off
numbers, and nothing goes red. `_verify_supersample` therefore **recomputes**
the digest a resolution-patched runtime produces and asserts EQUALITY. If you
add a third render lever, do the same — the negative form is not extensible.

**Two of the three are degradations and the third is an improvement**, and that
is the point rather than an untidiness. A panel that has only ever been shown
things getting worse cannot tell an improvement from a regression: run as a
plain commit-to-commit diff, a k=2 supersample reports **2 false regressions**
and **7 unearned improvements** (every family C/D/E/G metric whose mask derives
from the source frames — gates live inside `Prediction`, which exists only per
declared mutation, so with `mutation=None` no gate is consulted). Declared as a
lever, none of that happens. What a lever has to be is **declared in advance**,
not bad.

**Measured, all three levers, all six scenes:**

| lever | criterion met on | why not everywhere |
|---|---|---|
| `high_crf` | all six (4/3 on five, 3/3 on `single_character`) | family E inverts on `single_character` only |
| `disabled_aa` | `aa_probe`, `multi_shot`, `saturated_outline` | family F's sign is scene-dependent; MSAA cannot reach axis-aligned art or an SVG sprite |
| `pix_fmt` | **NOT REGISTERED — it failed its exam** (an#72). Three families on four scenes here, **none** in CI: family D is as-declared on all six on macOS/arm64 and contrary on three on Linux/x86-64. An encode-side ROW is `comparison_scope: "machine"`; an encode-side PREDICTION is not, and is asserted wherever the exam runs. **Family C cannot supply a witness here**, which is a real limit rather than an oversight: `chroma_edge_dCr` is the lever's headline (-21% to -75%, every scene) and references `source_png`, a build-dependent conversion — and it cannot reference the lossless leg instead, because a qp0 file's chroma is already subsampled and the metric would read ~0. **The one lever whose subject is chroma structurally cannot count a chroma witness.** It is also the lever that shows family A is blind to the encoder BY MEASUREMENT — `chroma_edge_dCr` measures chroma error at an edge and 4:4:4 removes chroma subsampling. Also the lever that shows family A is blind to the encoder BY MEASUREMENT: exactly +0.0% on all six scenes, every family-A metric |
| `supersample` | `multi_shot`, `saturated_outline`, `single_character` | C/D/E/G are gated for **every** render lever, so the criterion is forced to A + B + F with no substitute; family A inverts on `promote_demo` (-34.8%, the sprite rasterises at 2x) and family F's three up-moves are the small ones (+0.8% to +2.8% against -4.0% to -12.2%) |
| `step_hz` | **NOT REGISTERED — refused at comparability, and the instrument is blind to it by construction** (an#89, measured 2026-08-24: `step_hz=12` — "on twos" at the corpus's **24 fps** — forced through `render.compile_shot` against a baseline row, six scenes; a first pass at 15 Hz was a 1.6-frame grid, the "naive quantisation" mode epic #9 warns about, and is superseded). Stepping moves `scene_contract_sha256` on every scene with a tween (the resampled keyframes ARE the contract), so `bench-compare` refuses a stepped row **before any family is examined** — unlike `pix_fmt`, which stayed comparable and *failed* its exam; the numbers below were necessarily taken outside the comparer. The two scenes with no authored tween (`promote_demo`, `single_character`) are **identical to the pixel** — `source_pixels_sha256` unchanged, every metric +0.00% — which is the camera/blink/`play` exemption at the pixel level. On the four with tweens, **every golden frame is byte-identical** (`changed_px` 0 on all six; the goldens sit on even frames, which are grid points, and at a grid instant the stepped pose IS the smooth one), so family B cannot see it; family A has no direction (`edge_transition_width` +0.02% `aa_probe`, −5.2% `graded_field`, −3.4% `multi_shot`, −0.0% `saturated_outline`); family F moves one way (`video_stream_bytes` −5.2/−5.4/−6.2/−5.9%, `file_bytes` −3.3/−0.9/−4.1/−3.7%) — held frames are cheap — and would count, but it is **one** family; C/D/E/G move with the *content* (the reference frames changed pose: `encode_ringing_excess` +158% `graded_field`, −22% `multi_shot`; `flat_field_deviation` +39% `aa_probe`, −68% `saturated_outline`) and stay gated, as for every render lever. **What the ledger can honestly say about stepping: per-frame quality is untouched (two scenes bit-for-bit, every golden bit-for-bit) and the encode is 5–6% cheaper.** Whether "on twos" looks *better* is a temporal, aesthetic judgement no per-frame metric carries; the human instrument is the `stepped-timing` demo (`misc/demos/build_demos.py`, smooth ∥ stepped; frame strip committed at `misc/docs/step_hz_side_by_side.png`). The default stays smooth; a flip is a separate one-line PR the maintainer makes on that verdict alone |

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

**A killed sweep does not leave the mutation on disk** (an#67), and that is two
mechanisms because they cover different kills:

- **SIGTERM is turned into an exception** for the duration
  (`restore_on_termination`), so the restoring `finally` runs. Ctrl-C never
  needed this — SIGINT raises — but `kill`, a timeout, an agent harness reaping
  a background task and a closing terminal do not raise, and the sweep is slow
  enough that interrupting it is the normal thing to do. The previous handlers
  go back on the way out: this module is importable, and a library that
  permanently rewires SIGTERM is a worse defect than the one it fixes.
- **SIGKILL cannot be handled at all**, so the load-bearing half is the
  recovery: `check_sites` — first thing in every sweep, and in the default CI
  leg through `test_every_declared_mutant_still_applies` — recognises a file
  whose mutated text is present and whose original is gone, and reports it as an
  interrupted run naming the file, the mutant and the exact restoring edit.

Why that matters more here than in an ordinary tool: **every mutation in this
registry is chosen to be plausible.** A leftover compiles, renders, produces
frames of the declared size and leaves the suite green apart from the one test
that names it — so it is a defect a developer can commit without noticing, and
the next run's message is the only thing standing in the way. If you see
`LEFT MUTATED` in a `check_sites` report, do not go looking for the refactor
that moved the code; there was none.

**Declaration rule that makes the recovery work: the mutation must REPLACE its
`old` text, never extend it.** The leftover branch recognises "the mutation is
present and the original is gone", so a mutant whose `new` contains its `old` is
invisible to it. One of the 43 had exactly that shape —
`mux_argv_is_checked_by_subset_not_equality` inserted `-tune animation` *before*
the argv lines it matched — and on a tree carrying that leftover `check_sites`
returned no problems at all, while the next `an bench-mutants` read the mutated
file as its `original` and restored to it: the instrument laundering the damage
into the baseline and reporting health. `check_sites` now refuses that shape by
applying the substitution and checking `old` is really gone (which also catches
a replacement that re-creates `old` across its own boundary, where comparing the
two strings would not), and `test_every_declared_mutant_is_recoverable_from_a_kill`
asserts the property across the whole registry. If it fires on a mutant you are
adding, re-anchor `old` so the change lands in the middle of it.

**An inherited `SIG_IGN` is left alone.** `nohup an bench-mutants > sweep.log &`
ignores SIGHUP so the sweep survives the terminal closing; taking that signal
would turn a deliberately-detached run into a partial one exiting 130. An
ignored signal is never delivered, so there is nothing to protect against.

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
