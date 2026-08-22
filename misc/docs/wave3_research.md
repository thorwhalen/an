# Wave 3 research — the measured lever

Measured 2026-08-22 on this machine (macOS / arm64, Chromium 140.0.7339.16), against
the vendored post-Wave-1 runtime. Every number below came out of a real render on the
committed corpus; none is carried over from the phase-1 prototype, which measured a
CDN build.

**This document is fact, not design space.** Four of its findings contradict the Wave 3
brief in epic #9. Where they disagree, this document is right — it was measured and the
brief was written before the instrument existed.

---

## 1. Plumbing: `autoDensity` decides everything

The brief says "supersample through the engine's `resolution` (**not**
`device_scale_factor` — measured as a blind upscale)". That is right, and it is only
half the trap. `resolution` on its own reproduces the same failure through a different
door.

Neither `resolution` nor `autoDensity` appears in the `new PIXI.Application({...})`
options today, so the engine default `RESOLUTION: 1` applies silently. Both keys have
to be introduced. Measured, rendering `aa_probe` (declared 320x240):

| Application options | PNG on disk |
|---|---|
| today (neither key) | 320x240 |
| `resolution: 2, autoDensity: false` | **640x480** |
| `resolution: 2, autoDensity: true` | 320x240 |

With `autoDensity: true` Pixi sets the canvas CSS size to the *logical* size, so
Chromium composites the 2x backbuffer down to 1x before the screenshot is taken. That
is a **blind browser downscale with no filter choice and no record of having
happened** — the `device_scale_factor` failure mode wearing a different name, and it
is the option whose name most suggests it is the right one.

**`autoDensity: false` is the supersample path.** The supersampled frames land on disk
and the downscale is ours to choose.

Two consequences worth recording because they were open questions:

- **The viewport does not need to rise.** The Playwright viewport stayed at 320x240
  while the element screenshot came out 640x480, un-clipped. Capture beyond the
  viewport works.
- **The 2x render is a genuine render, not an upscale.** `aa_probe` measures
  `edge_transition_width` 2.8807 at k=1 and 2.8792 on the raw 2x frames — the
  transition stays ~1 logical pixel because it is MSAA-limited, exactly as it should
  be if the geometry is being re-rasterised rather than stretched.

---

## 2. The downscale filter: lanczos is refuted

The brief specifies "lanczos downscale at encode". **Do not use lanczos.** Measured
`edge_transition_width` at k=2, all six corpus scenes:

| scene | k=1 | box / area | lanczos | bicubic |
|---|---|---|---|---|
| `aa_probe` | 2.8807 | **3.0945** (+7.4%) | 4.1878 (+45.4%) | 3.9985 |
| `saturated_outline` | 2.3685 | **2.4921** (+5.2%) | **7.3134 (+208.8%)** | 6.8891 |
| `multi_shot` | 2.3307 | **2.4487** (+5.1%) | 4.1907 (+79.8%) | 4.0000 |
| `single_character` | 2.0919 | **2.1455** (+2.6%) | 4.0441 (+93.3%) | 4.0000 |
| `graded_field` | 2.6042 | **2.8125** (+8.0%) | 3.8906 (+49.4%) | 3.7677 |
| `promote_demo` | 5.6368 | **3.6775 (-34.8%)** | 5.5840 (-0.9%) | 5.1296 |

The metric's own docstring says "3+ means the picture has gone soft". Lanczos puts
`saturated_outline` — the scene deliberately chosen as the chroma stress test, and the
closest thing in the corpus to the target idiom — at **7.31 px**. Its negative lobes
ring on hard-edged flat fills; it is a photographic resampler and this is not
photographic content. Bicubic fails the same way and additionally pins at a suspiciously
flat 4.0000 on three scenes.

**`box` and `area` are the same filter here**, to four decimals on five scenes and to
three on the sixth — at an integer ratio, PIL's `Image.BOX` *is* the k x k block mean.
That mean is also the mathematically exact supersample resolve. So:

> **The correct downscale is a plain k x k block mean, computed in numpy.** `an` already
> depends on numpy at core. No PIL, no resampler, no new dependency, and no filter
> choice to defend — an exact resolve is not a filter.

It also settles *where* the downscale runs: in the frame stage, before the PNGs are
written, **not** as an ffmpeg `-vf scale`. An ffmpeg-side resample would move
`x264_argv` (refusing every encode-side metric), and would retire the cross-arch
verdict's load-bearing clause that "ffmpeg never touches a frame".

---

## 3. The factor: k=2, with the residual recorded

### 3a. The ceiling — Decision 6, answered by measurement

Epic #9's Decision 6 asked whether to commit a reference still. Answered instead by a
**computed ceiling**: render each scene at rising k, block-mean-resolve to the declared
size, and let `edge_transition_width` converge. That converged value is the "target
value" `compare.py` says an interior optimum lacks and explicitly refuses to
manufacture from the baseline.

It converges, and it honestly refuses where it cannot:

| scene | k=1 | k=2 | k=3 | k=4 | k=6 | k=8 | ceiling |
|---|---|---|---|---|---|---|---|
| `saturated_outline` | 2.3685 | 2.4921 | 2.5846 | 2.5848 | 2.5847 | 2.5846 | **2.5846** |
| `graded_field` | 2.6042 | 2.8125 | 2.9167 | 2.9167 | 2.9010 | 2.9167 | **2.9167** |
| `multi_shot` | 2.3307 | 2.4487 | 2.4520 | 2.4516 | 2.4537 | 2.4552 | **~2.452** |
| `single_character` | 2.0919 | 2.1455 | 2.1467 | 2.1415 | 2.1372 | 2.1409 | **~2.140** |
| `promote_demo` | 5.6368 | 3.6775 | 3.5482 | 3.5248 | 3.5101 | 3.5028 | **~3.503** |
| `aa_probe` | 2.8807 | 3.0945 | 3.3428 | 3.2848 | 3.1196 | 3.3001 | **none** |

**`aa_probe` has no ceiling, and that is a finding rather than a failure.** Its edges are
*diagonal*, so an integer block-mean resolve lands the sample grid differently against
the edge at every k; it oscillates +/-5-8% with no settling. It therefore gets **no
declared target**, on the same principle as the rest of this instrument: a value nobody
measured is not a value. Do not average the oscillation into a number.

### 3b. The factor

Distance travelled from k=1 toward the ceiling:

| scene | k=2 | k=3 | k=2 residual |
|---|---|---|---|
| `saturated_outline` | 57% | **100%** | 0.093 px |
| `graded_field` | 67% | **100%** | 0.104 px |
| `promote_demo` | 92% | 98% | 0.175 px |
| `multi_shot` | 97% | 100% | 0.003 px |
| `single_character` | 112% | 114% | 0.006 px |

**k=3 reaches the ceiling on every scene that has one. k=2 falls 43% short on
`saturated_outline` and 33% short on `graded_field`.** (`single_character` overshoots
slightly at both, but its entire span is 0.048 px — that is noise scale.)

An earlier draft of this document said "k=3 buys essentially nothing on edge geometry".
**That was wrong**, and wrong in an instructive way: it was read off `multi_shot` and
`single_character`, the two scenes where k=2 had already converged. Two scenes are not
the corpus, and the corpus exists precisely so that a claim has to hold on all of it.

Cost, measured at a real output size — `single_character` forced to 1920x1080, 60
frames:

| k | s/frame | vs k=1 | backbuffer |
|---|---|---|---|
| 1 | 0.126 | 1.00x | 1920x1080 |
| **2** | **0.319** | **2.54x** | 3840x2160 |
| 3 | 0.640 | 5.09x | 5760x3240 |

**Sub-quadratic, not k^2** — there is a real fixed per-frame cost, so the k^2 worry was
overstated.

**Decision: k=2**, taken deliberately with the residual above on the record. The trade
is 0.09-0.17 px of unconverged edge width against a doubling of render time on top of
an already-2.54x cost: 60 frames at 1080p goes 7.5 s -> 19.2 s at k=2 and -> 38.4 s at
k=3. Because supersampling ships **opt-in with the factor as a parameter**, anyone
rendering hero frames can set k=3 knowingly against this table.

**The corpus cannot inform the factor and must not be used to.** At 320x240 the same
ladder reads 1.0x / 1.3x / 1.8x, because fixed costs dominate: `graded_field` renders 12
frames in 1.99 s at k=2 and 2.08 s at k=3. Read cost off the 1080p ladder.

Memory is the remaining unpriced axis: renders fan out one browser context per shot at a
default cap of 4, and the backbuffer scales k^2 per context. At k=2 that is ~33 MB of
backbuffer per context; at k=3, ~75 MB. Nobody has costed `parallel x supersample`.

## 4. The prediction column, measured in advance

These are the directions the `supersample` lever will declare. They are recorded here
*before* any default changes, which is the point of measuring them.

### `edge_transition_width` (family A, interior optimum)

Scene-dependent sign, and the split is structural rather than noisy:

- **Procedural path** (five scenes): **+2.6% to +8.0%**. Small and positive.
- **Descriptor path** (`promote_demo`): **-34.8%**. Large and negative — the SVG sprite
  rasterises at 2x instead of being stretched up from a 1x texture.

This is the exact inverse of `disabled_aa`, which is nearly blind to the descriptor
path (96 differing pixels of 12.4M). **The two levers reach complementary scenes**,
which strengthens the harness rather than diluting it.

### `frame_distinct_colours` (family A, guard)

**Scene-dependent sign, and this refutes the wave's done-when.** k=2 box against k=1:

| scene | k=1 | k=2 box | |
|---|---|---|---|
| `aa_probe` | 7.6 | 21.7 | **+186%** |
| `single_character` | 34.2 | 101.7 | +197% |
| `multi_shot` | 36.4 | 103.3 | +184% |
| `graded_field` | 119.9 | 132.0 | +10% |
| `saturated_outline` | 478.2 | 392.3 | **-18%** |
| `promote_demo` | 1212.7 | 931.9 | **-23%** |

Down on two of six. On a scene that already carries a lot of colour, 1x MSAA emits many
one-off blend values; an exact 2x resolve replaces them with fewer, more regular ones.
**The picture gets better and the count goes down.**

So epic #9's "edge distinct-colour count materially up **on every corpus scene**" is not
achievable — not because the change is too small to see, but because the statistic has a
scene-dependent sign, the same shape as `video_stream_bytes` under `disabled_aa`. (The
metric it literally names, `edge_distinct_colours`, does not exist: it was refuted and
deleted in Wave 2.)

### Why the pair is the instrument, and neither half is

Nothing in the panel separates "edges gained gradation" (good) from "the frame got
blurrier" (bad). The two signatures in this data are unambiguous **as a pair**:

| | distinct colours | edge width | reading |
|---|---|---|---|
| k=2 box, `aa_probe` | +186% | +7.4% | gradation added |
| k=2 lanczos, `aa_probe` | +3600% | +45.4% | softened, with ringing |
| k=2 box, `promote_demo` | -23% | -34.8% | sharpened |

The an#41 criterion counts metrics **independently** and cannot express a conjunction,
so this pair is evidence for a human reader — it is not a gate, and must not be
described as one.

---

## 5. `disabled_aa` survives, and an#41's certificate holds

The concern was that a box-resolved supersample supplies the gradation MSAA was
supplying, weakening the AA lever below the criterion. Measured, MSAA on vs off at both
k:

| scene | lever at k=1 | lever at k=2 |
|---|---|---|
| `aa_probe` | -30.6% | **-9.3%** |
| `multi_shot` | -14.2% | **-14.8%** |
| `saturated_outline` | -0.1% | -0.1% |
| `promote_demo` | +0.0% | -0.0% |

`aa_probe` weakens about threefold but stays far outside noise — two consecutive bench
runs on one machine are bit-identical on every metric, so there is no band to clear.
`multi_shot` does not weaken at all and becomes the lever's strongest edge-width
witness. The two scenes where the lever never reached (`saturated_outline`'s
axis-aligned `drawRect` edges, `promote_demo`'s pre-rasterised sprite) are unchanged, as
declared.

The colour witness gets **stronger**: under the lever, `aa_probe` distinct colours drop
21.7 -> 6.9 at k=2 against 7.6 -> 3.0 at k=1 — a larger absolute move.

**Validity cross-check.** `promote_demo` at k=1 reads 5.6368 (MSAA on) vs 5.6369 (off).
That +0.0001 is the figure Wave 2 recorded for the same measurement, reproduced here by
an independently written harness. It is the best evidence available that the numbers in
this document are measuring what they claim to.

---

## 6. What this changes in the Wave 3 plan

| Brief said | Measured says |
|---|---|
| lanczos downscale | **block mean (box)**; lanczos triples the edge band on the most idiom-like scene |
| downscale "at encode" | **in the frame stage**; ffmpeg-side moves `x264_argv` and retires the cross-arch clause |
| (factor unstated) | **k=2**, chosen with the residual recorded; k=3 reaches the measured ceiling on every scene that has one, at 2x the cost of k=2 |
| Decision 6: commit a reference still? | **a computed ceiling instead** — it converges to 4 decimals on axis-aligned scenes and honestly refuses on `aa_probe`'s diagonals |
| "edge distinct-colour count materially up on every corpus scene" | **scene-dependent sign, down on two of six**; the named metric does not exist |
| supersample via `resolution` | correct, **and `autoDensity: false` is load-bearing** — `true` reintroduces the blind downscale |

---

## 7. Still unmeasured

- **Cross-arch pixel identity at a larger backbuffer.** The cross-arch verdict was
  measured at 1x with MSAA 4, and a larger backbuffer is not on its list of settled
  questions. Do not assume the guarantee carries; it gates whether goldens rendered at
  k=2 can stay a CI gate.
- **`parallel x supersample` memory.** One backbuffer per browser context, four
  contexts by default, k^2 per backbuffer.
- **Whether `display:none` on the stage canvas grows with k.** Compositing is
  per-pixel, so the measured 15x seek-loop win should grow with a supersampled
  backbuffer — which would pay for much of the 2.54x. Worth measuring when that lands.
- **`-f concat -c copy -movflags +faststart` on the pinned ffmpeg build.** If it forces
  a re-encode it would *create* the double encode the epic wrongly describes.
- **4:4:4 playback compatibility**, for the `-pix_fmt` knob's documentation.
