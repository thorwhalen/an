# Wave 2 research — the instrument

## What this answers, and what it does not

This document is the synthesis of five surveys (metrics, determinism, corpus practice, cassettes, prototype audit) and four adversarial passes over them. Its subject is the Wave 2 deliverable: `an bench` writing a metrics ledger to `misc/bench/ledger/<date>-<sha>.json`, plus a mutation test proving a deliberately degraded pipeline moves the ledger in a predicted direction.

Four things it settles: which metrics survive a hostile read (none of the originally proposed twelve did — every recommendation below is a **corrected** form); that same-machine byte-identical rendering is already achieved and needs only to be asserted; that the golden corpus should be committed PNGs, gated on decoded pixels, at each scene's **native** resolution; and that the vision judge must be cassetted but must never be a ledger metric.

Five things it does **not** settle, each named again in §7 with the experiment that would close it. (a) ~~**Cross-architecture determinism.**~~ **CLOSED 2026-08-21 by an#31 — the pixels are identical**, across ISA, OS and SwiftShader JIT backend, for the pinned Chromium build. `misc/docs/wave2_crossarch_verdict.md` is the record; §7 Q1 carries the consequences. The *encode* side remains open and is sequenced behind the x264 pinning. (b) **Whether `coded_luma_edge_error` and `chroma_edge_dCr` remain independent after their corrections** — they correlated r=0.990 in their *broken* forms, and the fix is expected to decorrelate them, but that must be measured, not assumed. (c) **Whether `encode_ringing_excess` beats plain edge-band MAE** — a cheap comparison nobody has run. (d) **Whether any of this holds on a real `an` shot.** Every number came from either the preserved `/tmp/anbench` artifacts, a synthetic flat-cutout scene, or the shipped examples; a production shot with SVG-textured sprites and viseme swaps may have a different edge population. (e) §4's design is now contested rather than merely careful — its adversarial pass ran separately and **inverted its headline claim**: `claude-haiku-4-5-20251001` is the documented, Active Claude API *ID* (the bare `claude-haiku-4-5` is the alias), so "the vision verifier may be doing nothing today" is unsupported by the model id. The verifier's blindness is real but has a different cause — `vision.py:123-129` returns `passed=True` on any exception — and 15 findings against §4 were confirmed. What remains genuinely unsettled there is the licence closure of the `vision` extra (§4) and the corpus/cassette re-bless coupling.

---

## Where this research contradicts the epic

Six places. These are the most valuable output of the exercise and are stated here rather than buried.

1. **The epic's Wave-2 issue 1 names "mean adjacent-frame SSIM" as a default ledger metric. It moves the wrong way.** Measured on the preserved encode ladder it *increases* with degradation: 0.95809 (crf18) → 0.95866 (crf23) → 0.96171 (crf35) → 0.96829 (crf45) → 0.97651 (crf51), because a crushed video is smoother so consecutive frames resemble each other more. Committing it would put a number in the ledger that rewards the exact degradation the gate exists to catch. Keep its existing use at `an/verify/media_quality.py:111` as a *frozen-render detector*, which is a different and legitimate job. Replace it in the ledger with `encode_flicker_on_held_pixels` (§1).

2. **The epic's Wave-2 issue 2 says `AN_DETERMINISTIC=1` should "pin the blink phase". There is nothing to pin.** The blink phase is already a pure function of `(entity id, t)` via a hand-rolled djb2 hash — `an/data/cutout_runtime/runtime.js:563-597`. Likewise palette hashing is `sum(ord(c)) % 5` (`an/adapters/cutout/compile.py:78-92`), not Python's `hash()`, so `PYTHONHASHSEED` is irrelevant (verified across seeds 0/1/12345/random, including the stricter `sort_keys=False` form). The determinism-mode budget must be spent on the browser rasteriser, the browser build, and the x264 thread count instead. The one real hazard the blink introduces is different and unwritten: the phase is a function of the entity **name**, so renaming a corpus character silently re-phases every blink and moves every metric.

3. **The epic's Wave-2 issue 3 says "commit first-frame and mid-frame PNGs". Mid-frame as `duration/2` is frequently a byte-identical duplicate of the first frame.** Measured on `examples/single_character`: both frames are 9,688 B with 0 differing pixels, because at 24 fps the mid frame lands at t=1.25 s, outside charlie's blink window [0.952, 1.092]. Blinks occupy 0.14 s of every 4.0 s = 3.5% of frames. The corpus manifest must pin a per-scene second-frame time, and `--bless` must refuse a scene whose two frames are identical.

4. **The epic's mutation criterion — ">=3 metrics move in the predicted direction" — is not honestly satisfiable as written, for two independent reasons.** First, the pre-encode and post-encode metric families are **disjoint**: a CRF change cannot move any golden-frame metric, and an AA change cannot be seen by any metric that compares a variant to its own reference. Second, the obvious metrics are highly collinear — ship `edge_distinct_colours`, `blend_pixel_fraction` and `hard_step_fraction` and you have one signal with three names, all three of which move together on AA-off and all three of which move the *wrong* way together on a blur regression. §1c restates the criterion as ">=3 metrics from >=3 distinct causal families, counted per mutation".

5. **`misc/docs/architecture_as_built.md:196-208` describes a shot cache read path that does not exist.** `mall["shots"]` is written at `an/render.py:222` and deleted at `an/iterate.py:268`; there is no read anywhere in the package. `an bench` therefore needs no cold-render forcing for pixel metrics — every run is already cold. Fix the doc; do not build cache-busting machinery for a cache that is not there. (The *audio* cache at `an/audio/pipeline.py:182-227` **is** read, and does warm between runs, so it affects wall-time metrics only.)

6. **Four of the bench prototype's headline conclusions were invalidated by Wave 1's vendoring of PixiJS 7.4.2.** The prototype measured a canvas-2D fixture; `an` renders WebGL. Attribution is clean — re-running the prototype's own `page.html` today reproduces its published numbers within noise, so every divergence is caused by the page. See §5.

---

## 1. The metric ledger — what `an bench` should record

**Every one of the twelve metrics the metrics survey proposed was refuted.** What follows is the corrected set. Where the correction changed the metric's identity, the original appears in §1b with what killed it. One metric — `video_stream_bytes` — went through **no** adversarial pass and is marked as such.

Two families that must never be mixed:

- **Render-side, no-reference** — computed on the pre-encode PNG. These are the only metrics that can see a render mutation. They are blind to the encoder **by construction**, which is a scope hole, not a strength: `an` muxes to `yuv420p` and 4:2:0 destroys black outlines in the file the user actually watches.
- **Encode-side, reference** — decoded frame vs its own pre-encode PNG. These are the only metrics that see an encode mutation, and they are structurally blind to render regressions because the reference moves with the mutation.

A third thing, which is neither: the **golden tripwire**, a change detector that fires on improvements and regressions alike.

| ledger key | one-sentence explanation | failure mode caught | inputs | numpy-only? | deterministic or band | sign under high-CRF | sign under disabled-AA |
|---|---|---|---|---|---|---|---|
| `edge_transition_width` | The average thickness, in pixels, of the fuzzy band between two flat colour areas — under 1 is a jagged staircase, ~1 is clean AA, 3+ means the picture has gone soft. | Lost AA **and** accidental softening (blur, non-integer canvas scale, LINEAR texel filtering) — two-sided | pre-encode PNG | yes | bit-exact same-build | n/a (pre-encode; not a satisfied prediction) | **DOWN** 3.07 → 1.65 |
| `off_palette_pixel_fraction` | The fraction of the frame whose pixels are not exactly one of the colours the compiler declared for this shot. | AA presence, bounded 0..1; also "someone added a gradient/shadow/texture" | pre-encode PNG + compiler-derived palette | yes | bit-exact same-build | NOT MEASURED (say so; do not report "no change") | **DOWN** to a scene-dependent floor (not 0) |
| `frame_distinct_colours` | How many different colours are in the frame at all. | Palette discipline / flatness drift. **No predicted AA direction** — it is a guard, not a dial. | pre-encode PNG | yes | integer, bit-exact | n/a (pre-encode) | DOWN, but not counted as a witness |
| `coded_luma_edge_error` | How much the encoder's quantiser roughens the brightness step at a line's edge, measured on the codec's own luma plane. | Bitrate starvation / quantiser damage on outlines | decoded Y plane vs source Y plane, source-derived edge mask | yes + ffmpeg subprocess | bit-exact for fixed (browser, ffmpeg, `-threads 1`) | **UP** 0.419 (qp0) → 1.875 (crf23) → 41.2 (crf51) | **gated null** (reference moves) |
| `chroma_edge_dCr` (+ `chroma_edge_dY` control) | How much the colour shifts on the pixels straddling a hard outline once the video is encoded. | 4:2:0 chroma subsampling on line art. `dCr/dY` large ⇒ real chroma bleed; ≈1 ⇒ generic damage | decoded yuv444p vs source, same mask | yes + ffmpeg | 0.48% band across thread counts, one build | **UP**, but read the ratio: at crf51 dY 37.99 ≈ dCr 36.38, i.e. generic | **gated null** (measured −0.7% on a faithful AA-off simulation) |
| `flat_field_deviation` (+ `flat_field_p99_dev`) | Of the pixels the renderer painted inside a large flat colour field, what fraction came back more than 6 code values off. | Banding and blocking in the flat fields — the ~90% of the frame no edge metric touches | decoded vs source, source-derived flat mask | yes + ffmpeg | bit-exact for fixed builds | **UP**, monotone: 0.0003 → 0.0005 → 0.0035 → 0.0127 → 0.0399 (crf18→51), 133x | **≈flat** (0.0014 → 0.0019; inside the preset-axis spread) — this is what makes it orthogonal |
| `encode_flicker_on_held_pixels` | The fraction of pixels the animator held perfectly still that moved by at least 2 code values in the delivered video. | Held-pose "boiling" — the worst artefact for limited-motion animation | 2 consecutive decoded frames + the 2 source PNGs | yes + ffmpeg | bit-exact with `-threads 1`; 1.7% spread without | **UP** 0.0321 → 0.0394 → 0.0848 (crf18/23/51) | **gated null** (source hash differs) |
| `encode_ringing_excess` *(provisional)* | How much more the encoder overshoots around outlines than a mathematically lossless encode of the same frames does. | Ringing / mosquito noise, with the source-hardness term cancelled | decoded lossy AND decoded `-qp 0`, both vs source | yes + ffmpeg (2 encodes) | as above | **UP** ~2x | ≈flat by construction (both legs rise together) |
| `video_stream_bytes` (+ `file_bytes`) *(unreviewed)* | How large the encoded video stream is. | Encoder-config drift; free cross-check that the metrics read the file they think they do | the mp4 | stdlib | needs a band across ffmpeg/x264 builds | **DOWN** hard, 8x | **UP** ~5.5% (hard edges cost more to code) |
| `golden_identity` **(tripwire, counts zero)** | Today's frame is byte-for-byte the committed golden frame. | ANY render-side change, including ones nobody predicted | decoded pixels of today's PNG vs the golden PNG, **full frame** | yes | boolean, exact | n/a (pre-encode) | **FAILS** |
| `min_ssim_win8_vs_golden` **(diagnostic + render-mutation witness)** | The worst 8x8 patch in the frame scored 0.28 against the golden — something vanished there. | Magnitude and *location* of a golden failure; graded render-mutation signal | today's PNG vs golden PNG | yes | float, report 4 dp | n/a (pre-encode) | **moves away from 1.0** (a total blink is 0.9999 → 0.279 at 1080p, 0.063 at native) |

**Provenance fields, not metrics** (never gated, never counted): `scene_contract_sha256` (sha256 of the serialized `CutoutSceneJSON` + `n_entities` + `n_frames` + `resolution`), `edge_px`, `frame_px`, `resolution`, `shot_count`, `palette` (as a hex list), the mask parameters, the resolved encode command, and the environment tuple from §2.

### 1.1 `edge_transition_width`

Replaces `edge_distinct_colours`. Measure geometry, not palette cardinality: a pixel is "flat" if it equals both horizontal neighbours within a tolerance `tol` (~4/255 — with `> 0` instead, ±3-LSB dither sends the metric to 255.0); take horizontal run-lengths of non-flat pixels; report the trimmed mean or median. ~10 lines of numpy, one constant.

Measured on the synthetic flat-cutout fixture: aa_off 1.65, aa_on 3.07, +3x3 blur 5.83, +5x5 blur 7.71. Both degradation directions are expressible from one number, which cardinality could not do — cardinality reads aa_off as 4, aa_on as 45, and a 3x3 blur as **416**, so "the number went up" means "AA restored" and "the picture went soft" indiscriminately. It is also immune to the content confound that moved cardinality +84%: adding one flat prop in a new colour leaves the width at 3.07 (aa_on) / 1.66 (aa_off), and a background gradient leaves it unchanged.

Caveat to record in the ledger: the absolute value is scene-dependent (a deliberately 3px black outline is legitimately non-flat), so compare deltas on a fixed bench scene, never absolutes across scenes.

### 1.2 `off_palette_pixel_fraction`

Replaces `blend_pixel_fraction`. The palette is **derived, never pinned by hand**: enumerate the colour literals the compiler already emits into `CutoutSceneJSON` — the shot background (`an/adapters/cutout/compile.py:251-258`), every `visualSpec.color` consumed by the `PIXI.Graphics` builders (`runtime.js:143, 179, 192, 205`), and the `fill=`/`stroke=` hex literals of each referenced SVG. Record the resolved palette **as a list of hex strings** in the ledger so a reviewer can see what changed when the number moves.

```
p = (a[...,0].astype(np.uint32)<<16) | (a[...,1].astype(np.uint32)<<8) | a[...,2]
frac = 1.0 - np.isin(p, palette_uint32).mean()
```

Never `np.unique(..., axis=0)`: measured 1.833 s/frame at 1080p vs 0.019 s for the uint32-packed form — 94x, identical result.

What the refutation changed, and it is load-bearing: the original claimed "goes to EXACTLY 0.000 when AA is off". That is false on the shipped SVG-sprite path. The `antialias` flag at `runtime.js:517` controls WebGL MSAA on geometry edges only; it does not touch Chromium's rasterisation of SVG textures before upload, LINEAR texel filtering when a sprite is scaled to a non-integer size (`runtime.js:164-165`), camera scale tweens (`compile.py:1098-1120`), or any element with alpha<1. The 0.0000 was measured on procedural `PIXI.Graphics` rects, which is the least representative path available. **Publish the per-scene floor and assert a ratio against the same scene's previous entry, never an absolute zero.**

Also honest: a blur, drop shadow, gradient or sub-pixel offset regression moves this metric UP, indistinguishably from "better AA". State that in its ledger docstring. It is a change detector on that axis.

### 1.3 `frame_distinct_colours`

`len(np.unique(frame.reshape(-1,3), axis=0))` — three lines, no parameters. Empirically within 6% of the elaborate masked version on every case tested, because for flat cutout art essentially all colour variety lives at edges, so the edge mask is a no-op by construction. Ship it labelled as what it is: a flatness/palette-discipline indicator with **no predicted direction on AA changes**, guarding against "someone added a gradient, a texture, or a soft shadow and the look stopped being flat". Do not count it alongside `off_palette_pixel_fraction`.

### 1.4 `coded_luma_edge_error`

Replaces `luma_edge_error`. **Read the coded luma plane; never recompute Y from decoded RGB.**

```
Y_dec: ffmpeg -i out.mp4 -vf select=eq(n\,{i}) -vsync 0 -frames:v 1 -pix_fmt gray -f rawvideo -
Y_src: ffmpeg -i frames/f{i}.png                                    -pix_fmt gray -f rawvideo -
value = np.abs(Y_dec - Y_src)[mask].mean()
```

The defect the refutation found: YUV→RGB→Y cancels analytically (0.2126·1.5748 − 0.7152·0.4681 = −1e-5), but the RGB round trip **clips to [0,255]**, and clipping breaks the cancellation exactly at saturated edges. Measured, a *mathematically lossless* `-qp 0` encode read **5.081** under the broken form and **0.419** under the fix — i.e. ~83% of the baseline number was chroma leakage. And 100% of that leakage sat on gamut-extreme pixels, which is precisely what flat 2D line art is made of.

The fix inverts the discrimination claim into the right orientation. Pixel-format knob (crf18, 420p→444p): 3.44x → **1.16x** (correctly near-flat; the luma plane is untouched by subsampling). CRF knob (420p, crf23→crf18): 1.12x → **1.80x**. Dynamic range against the lossless floor goes from 1.2x (noise) to 4.5x (signal).

### 1.5 `chroma_edge_dCr` and its `dY` control

Replaces `chroma_edge_error`. Commit **one** scalar, `mean |dCr|` over the edge mask — the composite `(dCb+dCr)/2` correlates r=0.9996 with dCr alone across the whole encoder ladder, so three ledger rows would be one measurement with three names. Read the chroma directly with `-pix_fmt yuv444p` rather than round-tripping through clipped rgb24.

Commit `dY` on the **same mask** as a control, not as a quality metric: the 4:2:0 claim is only credible when dCr/dY is large. On `qp0 yuv420p` it is 17.24/5.26 = 3.3 (real chroma bleed); at crf51 it collapses to 36.38/37.99 = 0.96 (generic blocking wearing the metric's name).

Three things the refutation removed from this metric's story. A 3x3 blur regression **improves** it by 14.9% (12.367 → 10.526) — larger than the claimed AA effect and in the wrong direction. A faithful AA-off simulation moves it **−0.7%**, not the claimed +10.7%. And hardening edges by 2x nearest decimation moves it **−82.6%**, because whether hard edges raise or lower chroma error depends on where they land relative to the 2x2 chroma grid, i.e. on the scene. Conclusion: this is an **encoder-fidelity** metric only. Predict no direction for any render-side mutation.

Pin the mask in the ledger row and derive it **only from the reference PNG**, e.g. `max(|Y[:,2:]-Y[:,:-2]|, |Y[2:,:]-Y[:-2,:]|) > 40`, with the operator and threshold written into the record. The prototype's original region-box script is gone (`bench_prototype/enc.sh` only runs ffmpeg psnr/ssim), and a re-derivation with a generic gradient mask reproduced the **ordering** but differed by up to 12% in absolute value. **Treat the prototype's absolute numbers (edgeMax 161/154/70) as ordinal evidence and never write a threshold from them.**

### 1.6 `flat_field_deviation`

Replaces `colour_count_inflation`. The strongest metric in the set, and the only one measured to be genuinely orthogonal to the edge/AA axis.

```
flat = ~dilate(any 4-neighbour colour change in the SOURCE, k=3)
dev  = |decoded - source|.max(axis=-1)[flat]
value = (dev > 6).mean();  p99 = np.percentile(dev, 99)
```

Measured, CRF ladder (crf18/23/35/45/51): 0.0003 / 0.0005 / 0.0035 / 0.0127 / **0.0399** with p99 2/2/4/8/14 — strictly monotone, 133x span, bounded in [0,1] with a natural target of 0 and a second reading in human units. AA matrix with the encoder held fixed: 0.0019 / 0.0014 / 0.0014 / 0.0011 — flat. Preset axis: 0.0000 / 0.0027 / 0.0005 / 0.0004, i.e. the config swing sits *inside* the CRF signal rather than 2.3x outside it (as the ratio form did).

What it replaced was worse than useless: `colour_count_inflation`'s render-axis sign was manufactured entirely by its shrinking denominator (the *best* render produced the *most* decoded colours and the *lowest* ratio), and 98.9% of its numerator came from the edge band, so it never measured flat fields at all.

### 1.7 `encode_flicker_on_held_pixels`

Replaces `static_region_flicker`, with four fixes, three of them mandatory.

- **Cast before subtracting.** `np.abs(dec[i+1]-dec[i])` on uint8 is the identity on unsigned dtypes; the literal proposed code measured the *sign* of the change, giving 8.02/9.64/11.73/16.52/18.14 across the ladder against 0.56/0.65/1.02/1.48/1.63 for the int16 version. It stayed monotone, which is exactly why it would have shipped unnoticed.
- **Report a rate, not a mean.** The median held-pixel delta is 0 at every CRF (95–96% of held pixels are bit-identical even at crf51); a frame-wide mean is carried by a 4–10% minority and diluted by the rest. `(d[static] >= 2).mean()` gives 0.0321 / 0.0394 / 0.0848 for crf18/23/51.
- **Gate on the source hash.** Record `sha256` of the concatenated pre-encode PNG sequence; when two ledger rows have different source hashes, write `null`, not a number. Without this gate the metric reports a **7.1x improvement** for half-res-then-nearest-upscale — the most visible possible flat-art regression — because a flattened render gives x264 large uniform skip regions.
- **Exclude it from every renderer mutation's witness count.** Its sign is degradation-agnostic only within the encoder axis.

### 1.8 `encode_ringing_excess` (provisional)

Replaces `edge_ringing`. Encode the same PNG sequence twice at the same pix_fmt — once at the bench CRF, once at `-qp 0` — and report the difference of the overshoot means. Both legs share the source, so the source-spectrum term cancels. The consequence is the whole fix: AA-off raises both legs together (excess flat, correctly reporting "the encoder did not get worse"); a genuine Wave-3 crispness improvement also raises both legs (no false regression); a CRF change raises only the lossy leg.

**Provisional** because the cheap redundancy check has not been run: compute plain edge-band MAE over the identical mask on the same mutation matrix. If it moves within ~15% on both arms, ship the MAE instead — it is simpler, needs no local min/max envelope, and its one sentence is directly checkable by eye.

### 1.9 `golden_identity` and `min_ssim_win8_vs_golden`

Replaces `golden_edge_ssim`. **One boolean gate on the FULL frame**, plus a diagnostic.

```
changed_px = int((np.abs(today.astype(np.int16) - golden.astype(np.int16)).max(axis=2) > 0).sum())
pass = (changed_px == 0)
```

Full-frame, not edge-masked, is what makes the one sentence true and what catches the flat-interior regressions an edge mask is blind to — and for the target look the flat fields are most of the picture. `changed_px` and `max_delta` belong in the **failure message**, not in the metrics block.

The diagnostic `min_ssim_win8_vs_golden` is the corrected survivor of the SSIM argument. The metrics survey concluded SSIM should be excluded because whole-frame SSIM scores a total eye-blink at 0.9989 and both perceptual hashes at Hamming 0. That conclusion was **refuted**: only the *global-moment* reduction is blind. With the window matched to feature size, min-over-windows SSIM scores the same blink at **0.279** (1080p) and **0.063** (native, 320x240). Killing SSIM outright would have discarded the best numpy-only detector available.

Two rules that come with it. Do **not** replace `an/verify/media.py:125` in place — add `ssim_map(a, b, *, r=3)` beside it so `MediaQualityVerifier`'s 0.999 frozen-frame threshold (`media_quality.py:36`) moves independently — but **do** fix that function's docstring, which claims Wang et al. while `media.py:129` admits it uses global means and variances. And do not cite ffmpeg's `ssim` filter as a cross-check: `vf_ssim.c` at tag n8.1 states it "uses the standard approximation of overlapped 8x8 block sums, rather than the original gaussian weights" and dispatches at 4-pixel stride [18]; it disagrees with a stride-1 r=3 window by up to 0.0201, and the disagreement **grows** with degradation.

Sign ambiguity, to be written into the ledger row: a Wave-3 SSAA improvement moves the golden metrics away from 1.0 exactly as an AA regression does, and in changed-pixel terms reports the improvement as the *larger* change (4796 px vs 4376 px). These are change detectors. They must be paired with the no-reference family to say whether a change was good.

---

## 1b. Metrics considered and rejected

| candidate | why rejected |
|---|---|
| **`mean_adjacent_frame_ssim`** *(named by the epic)* | Moves the wrong way: 0.95809 (crf18) → 0.97651 (crf51). Structural, not a tuning problem — a compressed video is smoother. Keep at `media_quality.py:111` as a frozen-render detector. |
| **`an.verify.media.ssim` as a committed metric** | Global-moment, not Wang et al. despite the docstring. Whole range for a crf18→51 catastrophe is 0.99948 → 0.97638. Add `ssim_map` beside it; do not replace it in place; fix the docstring. |
| **whole-frame SSIM** | 90% flat fill drags the mean to 1.0 and it ranks the AA **mutant higher** (0.9979 vs 0.9974). Diagnostic companion at most. |
| **`edge_max_abs_error` (the epic's cited "edgeMax")** | Single-pixel order statistic on an 8-bit integer: saturates (248/255/251 on three ladder rungs; exactly 255 on every supersampled variant). Keep as a recorded companion for continuity with the prototype; never gate on it. The mean over the same mask separates the ladder monotonically. |
| **`edge_distinct_colours`** | Two-sided optimum recorded as one-sided: a 3x3 blur raises it 9.2x, ±3-LSB noise 55x — both degradations reading as "AA restored". One extra flat prop moves it +84% with the renderer untouched. ≥94% collinear with a parameter-free whole-frame colour count. Claimed parameter invariance is false on the blur case (collapses to 0 at thresh=96). Superseded by `edge_transition_width` + `frame_distinct_colours`. |
| **`blend_pixel_fraction`** | `K` has no source in the IR — there is no scene-level palette field — so it is a per-scene hand-tuned magic number. A 12-flat-fill, **anti-aliasing-free** frame reads 2.46% at K=10, i.e. 4.8x the entire claimed AA signal. "Exactly 0.000" is false on the shipped SVG-sprite path. Superseded by `off_palette_pixel_fraction`. |
| **`hard_step_fraction` / `aliased_edge_px_per_kpx`** | The `>200` gate is palette-*contrast* dependent (recolour #000→#444 and a real edge drops below it); axis-aligned `drawRect` edges (`runtime.js:186`) are AA-invariant, so the value tracks pose, not quality; the denominator moves independently. A corrected contrast-relative form exists but is same-family with `edge_transition_width`. Re-open only if `edge_transition_width` proves insensitive on the `aa_probe` fixture. |
| **`jaggy_snap_rate` / `edge_flip_rate`** | Dominated by displacement x edge length: separation collapses from 25x (0.2 px/frame drift) to 1.15x on a fast pan, and a correctly-AA'd fast pan scores 20x *worse* than a broken AA-off slow drift. Visemes snap by design (`runtime.js:107`), so a talking shot scores ~200x a silent one at identical render quality. Threshold cliff: 126 → 99.3, 127 → 55.2, 128 → 0.0. Its stated justification is also false — AA-off is trivially visible in one still frame. Earns a slot only if the mutation set contains a degradation a still frame genuinely cannot see. |
| **`edge_ssim` vs the same variant's pre-encode PNG** | On the AA mutation Δ = +0.0004 (≈11x the machine band) because the reference moves with the mutation — invisible, not merely wrong-signed. Blind to chroma: `chroma_qp_offset=12` raised on-outline chroma error 32% and moved this metric +0.0001, i.e. "better". Superseded by the golden-referenced version plus the two coded-plane metrics. |
| **`colour_count_inflation`** | Best render → most decoded colours (9106) → *lowest* ratio (97.9); worst render → fewest (6914) → highest (1152.3). The verdict is manufactured by the denominator. 98.9% of the numerator lies outside the flat interior, so it does not measure what it claims. A lossless encode already inflates 87 → 471. Superseded by `flat_field_deviation`. |
| **`static_region_flicker` (mean form)** | Half-res + nearest upscale reports a **7.1x improvement**; 4-bit posterize a 6.6% improvement. Median held-pixel delta is 0 at every CRF, so a frame-wide mean is dilution. Superseded by the rate form with a source-hash gate. |
| **`edge_ringing` (raw overshoot)** | A joint function of source hardness and encoder fidelity, with only one degree of freedom. Any move toward the stated art target (crisper outlines) raises it under an unchanged encoder. Its credential — "moves correctly for BOTH mutations" — was produced by that bug. Superseded by `encode_ringing_excess`. |
| **`golden_edge_ssim` as a metric** | Edge-masked, so a palette or backdrop regression returns 1.00000 on a visibly wrong frame. Three ledger rows for one boolean. Reports the SSAA **improvement** as a larger change (4796 px) than the AA regression (4376 px). Superseded by `golden_identity` + `min_ssim_win8_vs_golden`. |
| **`edge_pixel_fraction`** | The sign of its AA response depends entirely on an unstated threshold relative to each boundary's contrast, and an `an` frame contains both regimes at once. Its two claims collide: a band wide enough to tolerate the legitimate −7.8% AA move cannot catch a vanished character. Demoted to provenance; the exact guard is `scene_contract_sha256`. |
| **perceptual hashes (dHash64 / pHash64)** | Engineered to be invariant to exactly what we measure. A total eye-blink gives Hamming distance **0** at both 1080p and native. Reasonable for compressing a golden library; useless as a quality metric. |
| **ssimulacra2** | Licence clean — BSD-3-Clause verified at libjxl v0.11.1 [20] and the pip port [21]. Rejected on cost and the one-sentence rule: pulls scipy + pillow into a six-dependency package, 330 ms/frame vs 191 ms for the whole numpy panel, third-party port unverified against the reference, and one opaque perceptual scalar tuned for photographic JPEG distortion that gives no diagnosis. |
| **butteraugli** | Apache-2.0 [22]. No PyPI package (404, checked 2026-08-21), so use means building C++ or vendoring libjxl; psychovisual model for photographs with a viewing-distance parameter. |
| **DSSIM** | **REFUSAL** — `kornelski/dssim` is AGPL-3.0 [23]. Trap worth recording: PyPI `dssim` 1.3.0 declares `Apache-2.0` and is a completely unrelated discrete-event simulation framework. Anyone checking the PyPI licence field, as the house rules forbid, would install the wrong library under a wrong licence belief. |
| **FLIP** | BSD-3-Clause [24]. Rejected on fit and shape: compiled per-platform binary wheels, and designed for photorealistic rendered content at a specified viewing distance. Not measured against our mutations — rejected on cost, and said so. |
| **VMAF** | BSD-2-Clause-Patent [25], and reachable through the existing subprocess boundary on this machine's build. Rejected because `--enable-libvmaf` is an optional ffmpeg build flag, so the metric would silently vanish on a runner built without it, and the model is trained on natural video. |
| **MS-SSIM, PSNR** | MS-SSIM's coarse scales are ~1.0 by construction on flat fields; PSNR is a global log-MSE dominated by the flat fill. Free to record as companions in the same ffmpeg pass; not ledger rows. |
| **scikit-image, OpenCV** | Licence-clean (scikit-image BSD/MIT [38]; OpenCV Apache-2.0). Rejected purely on cost: every recommended metric is 3–20 lines of numpy, and either would be the largest dependency change in the repo's history. |
| **Pillow for the bench path** | MIT-CMU [28] — permissive but not literally one of the four perimeter names, so it needs an explicit ruling rather than an assumption. Unnecessary regardless: `ffmpeg -f rawvideo -pix_fmt rgb24 pipe:1` into `np.frombuffer` is byte-identical to PIL and reads a whole 24-frame mp4 into an `(24,540,960,3)` array in 62 ms in one subprocess. |
| **The vision-LM judge as a ledger column** | Nondeterministic input (the pixels), sampled paid output. Satisfies neither half of the wave's done-when. `an/orchestrate.py:109` already keeps it out of the default verifier chain; make the exclusion an explicit recorded decision. |

---

## 1c. Redundancy — and the three that are genuinely independent

The mutation criterion is satisfiable dishonestly by shipping one signal under several names. Grouped by **cause**, the recommended set is:

- **Family A — edge geometry (render-side).** `edge_transition_width`, `off_palette_pixel_fraction`, `frame_distinct_colours`. All three answer "how many pixels are not one of the flat colours, and how wide is the band". They co-move on every AA change and they all move the wrong way together on a blur regression. **Count at most one.**
- **Family B — golden change (render-side).** `golden_identity`, `min_ssim_win8_vs_golden`, `changed_px`, `max_delta`. Four renderings of "the bytes differ". **Count at most one, and `golden_identity` itself counts zero** — a tripwire is not evidence of quality.
- **Family C — coded-plane edge fidelity (encode-side).** `coded_luma_edge_error`, `chroma_edge_dCr`, `chroma_edge_dY`. Correlated r=0.990 in their broken forms; the corrections are *expected* to decorrelate them (the pixel-format knob now moves chroma 3.44x and luma 1.16x), but that is **unmeasured**. **Count as one family until the correlation across the A–H encoder matrix is measured and committed to the ledger as the redundancy guard.**
- **Family D — flat-field fidelity (encode-side).** `flat_field_deviation` + `flat_field_p99_dev`. Measured orthogonal to the AA axis, monotone 133x on CRF.
- **Family E — temporal held-pixel fidelity (encode-side).** `encode_flicker_on_held_pixels`. Different mechanism from D (temporal vs spatial), but plausibly correlated on the CRF axis; measure it.
- **Family F — rate cost.** `video_stream_bytes`. Free, and mechanistically unlike everything above.
- **Family G — ringing.** `encode_ringing_excess`, provisional.

**The three independent witnesses, per mutation:**

*High-CRF encode:* `coded_luma_edge_error` (C) + `flat_field_deviation` (D) + `encode_flicker_on_held_pixels` (E), with `video_stream_bytes` (F) as a free fourth cross-check and `chroma_edge_dCr` as the diagnostic that names *which* knob moved. If D and E measure as correlated above r=0.9, F takes E's slot.

*Disabled AA:* `edge_transition_width` (A) + the golden change (B) + `video_stream_bytes` (F, measured +5.5% — hard edges are more expensive to code). **There are only three, and F is the weakest of them.** `chroma_edge_dCr` must not be counted here: its claimed +10.7% was refuted (−0.7% on a faithful AA-off simulation).

**Recommendation: restate the epic's criterion as ">=3 metrics from >=3 distinct causal families, evaluated per mutation, with a per-metric per-mutation sign declared in advance."** Two corollaries the ledger schema must carry before the first row is written, because retrofitting them invalidates every prior entry:

- A per-metric `direction` field that is **two-sided** for the metrics whose optimum is interior (`edge_transition_width`), expressed as "expected value ± width", not "must not decrease". The sharpness family moves in *opposite* directions for the two mutations, so a single per-metric sign mis-reports one of them.
- A distinction between `null` (gated — reference moved, or source hash differs) and `no change`. "No change by construction" is a tautology and must never count as a satisfied prediction; otherwise any pre-encode statistic pads the count for free.

---

## 2. Determinism — what is pinnable and what needs a band

| source | file:line | pinnable? | how | band (width, reason) |
|---|---|---|---|---|
| Procedural blink phase | `runtime.js:563-597` | **already pinned** | djb2 of entity id x scene time; pure function | none. Real hazard: renaming a corpus entity re-phases it. Freeze entity ids. |
| Palette hashing | `compile.py:78-92` | **already pinned** | `sum(ord(c)) % 5`, not `hash()` | none. `PYTHONHASHSEED` verified irrelevant, incl. `sort_keys=False`. |
| Python dict/set ordering | `compile.py:339,348,1002`; `stores/_common.py:61,124` | pinned | every escaping set goes through `sorted()` | none. Record as audited — this is the class of bug that returns the moment someone writes `for k in some_set:` and emits JSON. |
| JS pose-key ordering | `runtime.js:245-259` | pinned | explicitly sorted, with a comment citing "the golden-frame work downstream" | none |
| JS blink-node iteration | `runtime.js:570` | unpinned, safe by accident | `Object.keys(nodeIndex)` unsorted; safe only because each iteration writes an independent node | sort it; the invariant is unwritten |
| Texture alias load order | `runtime.js:448-462` | unpinned | `Object.keys(textures)` unsorted into `PIXI.Assets.load` | `.sort()` it. Never observed to move a pixel; make it a contract. |
| Vendored PixiJS internals | `vendor/pixi.min.js` (456,133 B, v7.4.2) | **assert, don't stub** | 4 `Math.random`, 2 `Date.now`, 6 `performance.now`, 3 `rAF`; `NoiseFilter` default seed is `Math.random()` | Dormant today (`autoStart:false` + explicit `app.render()`). Assert at capture time: no filter on the stage, `Ticker.shared.started === false`. Wave 3 adding a grain filter would silently randomise every frame with nothing red. |
| `preview.html` clocks | `preview.html:38,52,57,63,74,96,102` | scoped out | `index.html:18-19` loads only `vendor/pixi.min.js` + `runtime.js` | none — but assert the capture page is `index.html`, and note `render.py:209` copies `preview.html` into every work dir. |
| GPU vs software rasteriser | `render.py:97` (`args=["--no-sandbox"]`) | **PIN** | add `--disable-gpu` | Unpinned it is a 1.9% / max-57 pixel difference (descriptor scene 2.94%, procedural 0.24%). A band that wide hides any Wave 3/6 regression. **Pin, do not band.** |
| SwiftShader WebGL fallback | inherited Playwright default | **PIN explicitly** | add `--enable-unsafe-swiftshader` | Chrome removed automatic fallback in Desktop 137 [1][2]; failure mode is a hard context-creation error, not a visual shift. Do not inherit it. |
| Colour profile | inherited Playwright default | **PIN explicitly** | add `--force-color-profile=srgb` | `display-p3-d65` shifts pixels (maxDelta 20, mean 4.006) while leaving quality metrics unmoved — a red tripwire with every quality number reporting "fine". Byte-identical to no-flag on this machine, so adding it is a no-op today and pure insurance. |
| headless vs headed binary | `render.py:97` (no `headless=`) | **PIN** | pass `headless=True` explicitly | Full Chromium renders on the real GPU (`ANGLE Metal Renderer: Apple M1 Max`) and differs by 1.91%. Reachable today by a one-word local edit. |
| Playwright / Chromium build | `pyproject.toml:79` (bare `"playwright"`) | **PIN** | `playwright==1.55.0`; record `playwright.__version__`, chromium revision (1187), `browser.version` (140.0.7339.16), `UNMASKED_RENDERER_WEBGL`, `gl.SAMPLES`, and the **launch argv verbatim** | 1.55.0 → 1187/Chrome 140; PyPI latest is 1.62.0; ≥1.57 ships Chrome for Testing [5]. Pixels survived 1187→1223 **under the pin**; PNG bytes did not (144/144 files differ, 0 pixels). |
| ffmpeg / x264 build | `render.py:186-191` (`shutil.which`) | record, cannot pin | stamp `ffmpeg -version` and the x264 SEI `core NNN rNNNN <sha>` into every row | Untested across builds. This is the metric-side band candidate. |
| x264 thread count | `render.py:358-374` (no `-threads`) | **PIN** | `-threads 1` | **Corrected:** `-threads 1/4/11` give bit-identical decoded pixels; auto raises `lookahead_threads` above 1 only at `-threads >= 12` (roughly >=8–10 physical cores). Forced `lookahead-threads=4` changes 86.2% of bytes, max 80. The survey's "97.9%" and "threads=11 is dangerous" were both wrong — that Mac was never in the danger zone. A CI runner crosses the line; a 4-core dev box never will, which is how this ships. |
| x264 CRF and preset | `render.py:358-374` (neither passed) | **PIN** | `-crf 23 -preset medium` explicitly | Today both are compiled-in libx264 defaults. Preset swings colour counts 2.3x **non-monotonically** (ultrafast 3141, veryfast 7296, medium 6064, slower 5393) against a crf18→23 signal of 1.35x. |
| Colour tags on the mp4 | `render.py:358-374` (none) | **PIN, deliberately** | `-colorspace bt709 -color_primaries bt709 -color_trc bt709 -color_range tv` | Untagged today, so the decode matrix is chosen by a height heuristic (BT.601 below ~576 lines). Adding tags **moves every RGB-derived number**; do it in Wave 2 *before* the first ledger row, or record `color_tags: none` and accept a Wave-3 re-baseline. |
| mp4 container bytes | — | **not pinnable** | — | Every mp4 embeds `Lavf62.12.100`, `Lavc62.28.100 libx264`, and an x264 SEI carrying `core 165 r3222 b35605a ... threads=N`. `-fflags +bitexact -flags +bitexact` strips the first two; **nothing** strips the SEI (`-x264-params sei=0` is silently ignored). |
| Project-dir mutation | `render.py:140, 202-203, 222` | avoid | `an bench` renders into a **copy** | Otherwise the tree is dirty by the time `<sha>` is computed and the ledger filename is a lie. |
| Scene mtimes / decisions log | `stores/scenes.py:63` (`time.time()`), `stores/decisions.py:41` | avoid | never hash the project directory; hash the scene JSON **content** | Also: `render` calls `sync()`; keep it outside any measured region. |
| Browser-cache probe | `check_requirements.py:111-118` | fix | macOS path only; Linux default is `~/.cache/ms-playwright` | Not a determinism bug, but it lands on Wave 2's path — the wave that first makes CI launch a browser. |
| SwiftShader across CPU arch | — | **UNKNOWN** | — | The one open band question. Reactor JIT-specialises per ISA [31] and Chromium's docs make no determinism claim [1]. Measure on the CI runner before choosing any band width. |
| V8 transcendentals | — | pinned (likely) | V8 uses an in-tree fdlibm port in `src/base/ieee754.cc` rather than platform libm [32] | Cross-platform stable, so the cross-arch experiment tests the rasteriser, not the JS. |

### Verdict on the epic's own done-when

The epic flags "byte-identical values across two runs on the same commit" as possibly physically unachievable and writes the tolerance-band fallback into the criterion on purpose. **That hedge is over-cautious for frames and correct for the mp4 container.** Directly:

**Frames: YES, achievable, and already achieved.** Measured, at HEAD, on this machine: three renders of the SVG-sprite descriptor scene (144 frames) and two of the procedural scene (60 frames) gave byte-identical PNGs and byte-identical mp4s; `parallel=4` produced the same final mp4 sha256 as serial; two independent browser sessions gave identical sha256 per frame; GPU-default was byte-identical to `--use-gl=swiftshader`; the wire JSON is stable under `PYTHONHASHSEED=random`. **Write the determinism test as an equality assertion, not a band, and make any future band a deliberate argued regression.**

**Frames across Chromium builds: pixels yes, PNG file bytes no.** 1187 vs 1223 under the pin: 144/144 PNG files differ, **0 pixels** differ; the chunk layout is identical and only the deflate output moves (IDAT lengths `[4096,4096,4096,2482,6]` vs `[...,2487,6]`). **The golden criterion must be `sha256(decoded RGB array)`, never `sha256(file bytes)`.** A PNG-byte assertion goes red on the first Playwright bump for a reason unrelated to animation quality, and the predictable response to that is widening a threshold — the exact decay the epic exists to prevent.

**The mp4: byte-identical same-machine, never cross-machine.** Two encodes 1.3 s apart with `an`'s exact flags are byte-identical (identical md5) — there is no creation timestamp to strip. But five thread counts produce five distinct bitstreams, and the x264 SEI carries the encoder build and the thread count with no way to remove them. **So: assert mp4 sha256 equality only within a run pair on one machine; make every cross-machine assertion a decoded-pixel digest; and record the container's version strings as a separate informational field** — they are worthless as an equality criterion and are exactly the fingerprint that will explain a future decoded-pixel change.

**Metrics: bit-exact for a fixed (browser build, ffmpeg build) with `-threads 1`.** No band needed same-machine. The only measured cross-config spread is 0.48% for `chroma_edge_dCr` across thread counts on one build — comfortably inside a ~7x Wave-3 signal — against an 8.9% per-frame content spread within a single encode, which is why the number is not comparable across any change to the bench scene.

**The remaining band, if any, is a cross-architecture band, and its width cannot be guessed.** Run the corpus once on the CI runner with the pins in place, diff decoded pixels against the Mac capture, and record the band's provenance as "measured on `<runner>`, `<chromium build>`, `<ffmpeg build>`". A band with no recorded provenance is the decay this wave exists to prevent.

### Concrete flags to add

At `an/adapters/cutout/render.py:97`, as a module constant (no magic values inline):

```
_DETERMINISTIC_CHROMIUM_ARGS = [
    "--no-sandbox",                  # already passed; also a Playwright default
    "--disable-gpu",                 # pins SOFTWARE rasterisation (verified 0/144 byte diff vs today)
    "--enable-unsafe-swiftshader",   # pins the ability to create a WebGL context at all, post-Chrome-137
    "--force-color-profile=srgb",    # pins the compositor screenshot path's colour management
]
# and pass headless=True explicitly, to pin the headless-shell binary
```

**Do not** add `--use-gl=swiftshader` (an ignored legacy value) or `--use-angle=swiftshader`. This is a correction to the determinism survey's recommendation, which proposed all three together: measured, `--disable-gpu` alone reproduces production byte-exactly, while `--use-angle=swiftshader` alone — and Chromium's own documented form `--use-gl=angle --use-angle=swiftshader` [1] — moves 1.55% of pixels by up to 58/255. The three-flag pile lands on the right output only because `--disable-gpu` wins an undocumented precedence fight. Worse, all four configurations report the **byte-identical** `UNMASKED_RENDERER_WEBGL` string, so the renderer string the survey proposed as the ledger's guard is demonstrably blind to this flip. Record the string (it does catch GPU-vs-software) but **record the launch argv verbatim** as the actual guard.

**Do not** add `--disable-frame-rate-limit`: measured 1.05x on `an`'s real WebGL runtime versus the prototype's 2.3x on canvas-2D. **Do not** add `--deterministic-mode`: measured a no-op (144/144 identical), because the runtime uses `autoStart:false` plus explicit `app.render()`.

At `_ffmpeg_mux` (`render.py:358-374`): add `-threads 1 -crf 23 -preset medium -colorspace bt709 -color_primaries bt709 -color_trc bt709 -color_range tv`. On the **decode** side for metrics, use `-pix_fmt gray` for luma and `-pix_fmt yuv444p` for chroma; do not round-trip through `rgb24`, whose clipping lands precisely on the saturated-fill-against-black-outline pixels being measured.

---

## 3. The golden corpus — what to commit, and how to compare

**What is committed.** Plain PNGs, in git, no LFS, no external storage, no hashes-only library. Every technique in that space solves a problem `an` does not have: Skia Gold exists for >500,000 images per commit [10]; the canonical LFS scaling argument starts at 1,000 screenshots and 5 GB of history [17]; GitHub LFS bandwidth is charged to the repository owner for every CI fetch and fork, LFS objects are **excluded from source archives**, and LFS cannot be used with GitHub Pages [15][16] — which `an` publishes to. Measured corpus cost, at each scene's native resolution: 16 frames = **132,035 B**. Twenty re-blessings each rewriting every frame grew `.git` by **~1,080 KiB** after `gc` (a corrected figure; the survey's 588 KiB understated it ~1.8x). `an` already commits a 188,732 B example mp4, and one second of 1080p video costs about as much as the whole corpus. The trigger to revisit this advice, recorded in the skill: corpus > 20 MB, or a single re-bless > 5 MB.

**Format.** Write the golden through `an`'s own filter-0 PNG writer (numpy + stdlib `zlib`/`struct`, ~50 lines), not by copying Chromium's bytes. Measured: filter-0 re-encode is **−0.30%** bytes (9,688 → 9,659 B on a real frame — a correction; the survey asserted both "+4.5%" and "smaller" in one sentence, and measurement favours smaller), and pure-numpy decode drops from ~3,300 ms/frame to ~12 ms — Chromium emits Paeth on 1,049 of 1,080 rows. Round-trip is exact (`np.array_equal` True). This makes the committed golden a function of the **pixel data only**, so a future change to Chromium's libpng settings breaks nothing. Assert the round trip at bless time against the in-memory screenshot pixels, so a bug in `an`'s own encoder cannot hide.

**Which frames.** Two per scene, with the second frame's time **pinned per scene in the manifest** and chosen so something has actually moved. `--bless` must refuse a scene whose two frames are byte-identical (see the contradiction in the header section). For the long-hold scene specifically, pick a time inside a blink window or the scene tests one static image twice.

**Which scenes.** The epic's procedural/descriptor split is **mandatory and load-bearing**: the descriptor path is 12x more sensitive to a rasteriser flip than the procedural path (2.94% vs 0.24% of pixels under GPU-vs-software). Four additions the epic's scene list does not have:

- **A large flat or gently-graded field.** Every edge metric is masked to ~5–10% of the frame; only `flat_field_deviation` and `encode_flicker_on_held_pixels` cover the rest. The prototype's own test pattern included a sky gradient that "loses ~15 of 76 distinct levels in every 8-bit variant including lossless" — a banding failure with no edge in it. Without such a scene, banding regressions are invisible.
- **A saturated-fill-under-black-outline scene.** The real example frames are 31 colours on white, and the measured 4:2:0 edge error is ~3x smaller than on the prototype's saturated pattern. Until the corpus has one, the chroma metric under-reports exactly the artefact class the epic cares about.
- **A multi-shot project.** `an/render.py:263-267` short-circuits a single-shot project's final mp4 to `shutil.copy`, so `_ffmpeg_concat` is never exercised by a single-shot corpus, and `file_bytes` measures two different things depending on shot count.
- **An `aa_probe` fixture** with edges at fixed non-axis angles (7°, 23°, 45°) and a pinned pose. Axis-aligned `drawRect` edges (`runtime.js:186`) are bit-identical with MSAA on or off, so a corpus of axis-aligned art cannot validate an AA metric at all.

**Resolution: render each scene at its DECLARED native resolution.** This corrects the corpus survey's framing. Measured: ink is 10,955 px and the character bbox 130x124 at **both** 320x240 and 1920x1080 — the procedural rig geometry is absolute pixels (`compile.py:70-75`) and does not scale with the canvas, so rendering at 1080p adds 27x the pixels, zero information, and dilutes every ratio-form metric 27x. All five shipped examples declare 320x240–640x360; 1920x1080 is only the schema fallback (`an/base.py:29`). Stamp the resolution into every ledger row; a tolerance measured at one resolution silently means something else at another.

**The comparison rule.** `sha256(decoded RGB array)` equality, environment-gated by filename. Adopt the Playwright/Chromium/pytest-mpl convention of putting the environment in the path [3][9][12]:

```
misc/bench/golden/<scene>/<frame-key>-chromium140.0.7339.16-darwin-arm64.png
```

Off-environment, `an bench` **skips loudly**, naming the expected tuple and the observed one — never fails. A red tick that means "different laptop" trains people to ignore the tripwire.

**Tolerance-band discipline.** The band, if a scene ever needs one, is the **WPT two-number pair**: `maxDifference` (a per-channel cap) **and** `totalPixels` (a count) [8], opt-in per scene with a recorded reason, absent by default. A single scalar cannot distinguish "AA jitter on many pixels by ±1" from "one feature vanished across 172 pixels by ±162". **Ratio-form tolerances are wrong at every resolution `an` uses.** The measured worked example: a total eye-blink changes 172 pixels with max channel delta **162** (a correction; the survey said 123). Against the common 1% ratio [4][7]: at 1920x1080 that permits 20,736 px, 120x blind; at 640x360, 13x blind; at 320x240, still 4.5x blind. Only an absolute count in the low hundreds works. Note also that pixelmatch — Playwright's comparator [3] — ignores anti-aliased pixels by default; if a pixel comparator is ever added here, `includeAA: true` is mandatory, because AA edges over flat fields are the entire signal.

**Cross-platform verdict — SUPERSEDED by measurement (an#31).** This paragraph said "nothing in this research establishes that a Linux x86-64 CI runner will produce the same pixels", and prescribed skipping the pixel gate off-environment. **It has since been measured and the pixels are identical** — across ISA, OS, and even SwiftShader JIT backend; see `misc/docs/wave2_crossarch_verdict.md`. So the pixel gate **can run in CI** for the pinned Chromium build, and the environment key in the golden filename should be the **browser build alone** (`<frame-key>-chromium140.0.7339.16.png`) — the platform and arch segments below are measurably inert and would force one committed copy per platform for no information. The rest of this section stands. The original text, for the record: CI runs the cheap half — the ledger, its metrics, and `--compare` — and the pixel gate **skips** with an explicit environment message until the cross-arch experiment has been run. The PR reviewer still sees the change, because the PNGs are in the diff and GitHub renders 2-up, swipe, and **onion skin**, which its own docs describe as for "when elements move around by small, hard to notice amounts" [14]. That affordance is the single strongest argument for committing PNGs rather than hashes or an external service — and it works only for PNG/JPG/GIF/SVG, which also rules out WebP as a golden format despite better compression on flat art.

**Re-blessing.** `an bench --bless` must require a `--reason` string, written into the ledger beside the changed frames. A re-bless with no recorded reason is exactly the same failure as a silently widened threshold — the named failure mode this wave exists to prevent — and the industry's answer is a human triage step [10]. Ours is the reason string plus the onion-skin diff.

**Two smaller things.** `pyproject.toml` has no `[tool.hatch.build.targets.sdist]` section, so goldens stay out of the wheel automatically but reach the sdist; make that a decision, not an oversight. And the "environment in the filename" convention is what turns a Playwright bump from a red test with no explanation into a **new path** requiring a deliberate re-bless.

---

## 4. Cassettes for the vision verifier

**This section has now had its adversarial pass.** 23 findings adjudicated: **15 confirmed** (1 blocker, 7 major, 6 minor, 1 nit), **8 refuted**. Survived attack: the `judge_frames` seam and its three properties; `CassetteMiss(BaseException)`; the one-spend-switch ruling (attacked as a regression to pre-#260 — refuted, the pinned `replay_only=True` node is a drift detector reelee structurally cannot have); whitespace-collapsing the prompt; the `an bench` ruling, attacked twice. Did not: **item 1 was inverted**, the key was fail-*open*, the envelope was unwritable under the stated mechanism, the frames came from a corpus with a re-bless lifecycle, and old items 2–3 describe a revision open PR #28 already replaced. *`an/verify/*` and `an/orchestrate.py` are identical on `main` and `cf0d818` (PR #28 tip); `tests/*` cites the tip.*

**The paid surface** is one Anthropic SDK call: `frame_count` base64 PNG image blocks plus one static text block, one user message, no streaming, no tools (`an/verify/vision.py:85, 102-122`). Reply parsed leniently by `_parse_issues` (`:139, 156-176`). Key from `ANTHROPIC_API_KEY` (`:74`).

**The memoization point is a new extracted seam:**

```python
an.verify.vision.judge_frames(
    frames: Sequence[bytes], *, prompt: str = _PROMPT, model: str = _DEFAULT_MODEL,
    max_tokens: int = _DEFAULT_MAX_TOKENS, api_key: str | None = None,
) -> str            # the model's RAW TEXT reply
```

`verify()` becomes: extract → read bytes → `self._judge(...)` → `_parse_issues(reply)` → Findings, judge injected via the constructor per "no globals, no service locators". Three properties, all of which survived attack: **`bytes` not `Path`** (frames are `Path`s in a `TemporaryDirectory`, `vision.py:91-97`, so a key seeing them hashes a fresh random path and misses 100% of the time); **raw text**, keeping `_parse_issues` outside the cassette so parser fixes are testable against the recording for free; **injection**, so *record-vs-replay* drift is impossible — call-spelling drift is closed by the key.

**Corrected — memoize one level in.** `store_cached` hands the store only `(key, return_value)` (`key = key_func(*args, **kwargs)` … `store[key] = output`) and `value_encoder` sees the value alone — measured, a bare `str` — so four of the envelope's seven fields were unwritable. Memoize an envelope-returning `_judge_envelope`; `judge_frames` returns `envelope["reply"]`. All seven land; raw-text seam and `dol` mechanism unchanged.

**The key — corrected.** Derive it from the seam's signature, not a hand-written allowlist:

```python
IGNORED = frozenset({"api_key"})   # a cassette filename must never be a function of a credential

def judge_key(*args, **kwargs) -> str:
    b = inspect.signature(judge_frames).bind(*args, **kwargs)
    b.apply_defaults()                                    # store_cached passes RAW args
    a = {k: v for k, v in b.arguments.items() if k not in IGNORED}
    return robust_key("vision_judge", {
        "frames": [sha256(bytes(f)).hexdigest() for f in a.pop("frames")],  # ordered; order is semantic
        "prompt": " ".join(a.pop("prompt").split()),                        # whitespace-collapsed
        **a,                                              # model, max_tokens — and anything added later
    })
```

The old allowlist plus `**ignored` set the default to *exclude*: measured, `system=`, `thinking=` and `temperature=` all **collide with the base key**, so a changed request replays a stale reply and stays green forever. A false miss is red CI; a false hit is silent and unrecoverable. `**a` inverts the default to *include*; `apply_defaults()` makes `judge_frames(frames)` and the fully-spelled call one key, which `store_cached` will not do for you; and it removes a second declaration site of `_PROMPT`/`_DEFAULT_MODEL`/`_DEFAULT_MAX_TOKENS`. Verified to keep every property the two-ended guard asks for, plus a new-parameter case the allowlist cannot express. `inspect` is stdlib; reelee's `memoize_calls` is reused unchanged.

**The trap, sharpened — and the frame source corrected.** In `an` **the pixels are the prompt**, a joint function of Chromium, Playwright, the rasteriser and ffmpeg, so a key over fresh frames misses on the *machine* and, before §2's pins, on every run. The cassetted path must not render — **and the frames must not be goldens.** They come from a **frozen cassette fixture**, `tests/fixtures/vision_frames/*.png`: two small PNGs that are a cassette *key*, never re-blessed, never re-encoded. §3 gives the corpus a bless lifecycle (`--bless --reason`, wave2:306; twenty re-blessings budgeted, :279; a Wave-3 SSAA re-bless already scheduled, :153) and requires goldens be re-written through `an`'s own filter-0 writer, not Chromium's bytes (:281). Measured: that re-encode moves `sha256(file bytes)` — what this key hashes — at **zero pixel change**, reddening the free hermetic node, repairable only by a credentialled human spending a real Haiku call on an unrelated renderer PR. It is the fragility §2 already rejected for goldens ("`sha256(decoded RGB array)`, **never** `sha256(file bytes)`", :247). *Committed* is the benefit; *golden* only imports a foreign lifecycle. So §7's ordering note becomes unconditional: **issue 4 has no dependency on issue 3**, not on completion and not on its path convention. A render→judge test is a spending test by construction; mark it, do not cassette it.

**How a miss raises.** reelee's `CassetteMiss(RuntimeError)` would be **silently swallowed** here. Re-verified (anthropic 0.75.0, 2026-08-21): a `RuntimeError`-derived exception raised where the API call sits is caught and reported `info`/`passed=True`; a `BaseException`-derived one propagates. **Corrected:** the remedies are *not* interchangeable — `BaseException` is the only one surviving every frame on the path. Each fix below gets its own guard, with the mutation that must turn it red; a guard green inside its own failure mode is decoration.

| file:line | what it does | fix + its mutation test |
|---|---|---|
| `vision.py:117-129` | `except Exception` → `info`, falling through to **`passed=True`** | `CassetteMiss(BaseException)` **and** an `an`-owned `VisionJudgeError` raised by the seam and caught here. **Do not narrow to `anthropic.APIError`**: `issubclass(anthropic.NotFoundError, anthropic.APIError)` is `True` (verified), so a dead model id is still swallowed into a pass, and it puts a vendor class at the catch site. Guards: `cassette_miss_is_not_catchable_as_an_exception` and `a_failed_vision_call_is_not_reported_as_a_clean_pass` / rebase `CassetteMiss` on `RuntimeError`; revert the severity to `info`. |
| `vision.py:140-142` | a **successful** call whose reply does not parse reports `"vision LM reported no issues"`, `passed=True` — byte-identical to a clean bill of health (verified: a refusal, an empty reply and `{"issues": []}` give the same report), because `_parse_issues` collapses "no verdict" (`:162, 170, 174`) into the `[]` of "empty verdict" (`:176`) | Return a verdict (`None` vs `[]`), split the branch, emit a distinct non-`info` Finding quoting the reply head. No exception reaches here, so neither fix above touches it. Guard: `an_unparseable_reply_is_not_reported_as_no_issues` / revert the verdict split. |
| `orchestrate.py:151-165` | post-render loop: `except Exception` → `warning`; `add` flips `passed` only on `"error"` (`_base.py:56-57`), so `orchestrate()` returns `success=True` | Unfixable by narrowing — it guards *every* verifier and must stay broad. Only `CassetteMiss(BaseException)` survives it. (`orchestrate.py:129` is **not** on this path: `verify(ir, None)` returns at `vision.py:78-80` before a client exists.) Guard: `..._not_swallowed_by_the_orchestrator` / the same rebase, under which a `verify()`-scoped guard stays **green** (measured). |

Precedent in-repo states the same reasoning: `OutboundNetworkAttempt(BaseException)`, `tests/conftest.py:126-133`, whose docstring names "the verifiers' broad handlers".

**How it composes with the existing conftest** — unchanged, and it survived attack. `an` already implements the positive-opt-in half more strictly than reelee: `AN_LIVE_API_TESTS` truthy **and** `CI` unset (`conftest.py:37, 57-65`), two markers `live_api` (spends) and `live` (free but networked) (`:52`), and an autouse guard that refuses **and records**, derives from `BaseException`, and fails at teardown even if something swallows it (`:171-233`). So `recording_enabled()` is literally `return live_api_enabled()`. **Do not port reelee's `REELEE_RECORD_CASSETTES` / `--record-cassettes` pair** — reelee needed a second switch because its gate was key-presence, which is not consent; a second env var here is a second SSOT for "may this run spend?". Both nodes pin `replay_only`, so `recording_enabled()` has no call site — delete it or name the unpinned caller.

**Two test nodes, from one `memoized_judge(replay_only=...)` factory:**

- `test_the_judge_replays_from_its_cassette` — **no** live marker, `replay_only=True`. Runs in CI with the guard **armed**: free, hermetic, milliseconds, no ffmpeg, no Chromium, no key. It *proves* it did not spend rather than asserting it via a marker, and it is the drift detector that fires on a plain `pytest -q`. Possible because the frames are committed **and frozen**.
- `test_record_the_judge_cassette` — `live_api` + `requires_live_api` + an `anthropic` importorskip, `replay_only=False`. Replays on a hit, spends one Haiku call on a miss. It calls `judge_frames` **directly**, never through `verify()`, so no fail-soft handler sits between a failure and the test — and it asserts the reply *parses to a verdict* before committing it, the one path where a paid call can catch a refusal.

Plus `record_and_replay_share_one_key_function`; `every_parameter_that_reaches_the_api_is_in_the_key` (mutation: add a throwaway `temperature=0.0`); `the_cassette_fixture_frames_are_frozen` (flip one fixture byte); and the two-ended key guard — **ignore**: two api_keys and `None`, re-indent/dedent/CRLF `_PROMPT`, `bytearray` vs `bytes`; **distinguish**: a flipped pixel, frames 1↔2 swapped, a dropped frame, another model, another `max_tokens`, **alias vs dated id**, **a new parameter**. Give the memoizer conftest the swallow-proof second end too — append the missed key, assert at teardown, mirroring `conftest.py:183-184` + `:211-217` + `:233` — which holds even if every handler above is later widened.

**Cassette value shape.** A JSON envelope `{"reply", "frames": [<sha256>…], "model", "max_tokens", "prompt", "recorded_at", "recorded_with": {"anthropic": "0.75.0"}}`, `indent=2, sort_keys=True`. Only `reply` is read by code; not one pixel is stored. Two corrections: the envelope diffs line-by-line but `reply` is one escaped line either way; and `recorded_with` is enforcement only if something reads it — add a replay-time major-version check raising `CassetteMiss`. Have the miss message list the fixture dir's `(filename, digest)` pairs inline; a corpus-derived `index.json` would be stale in exactly the case it exists for. **No new dependencies:** `dol` is already hard and supplies `wrap_kvs(value_encoder=…, value_decoder=…)` and `store_cached(store, key_func)`. Installed dol 0.3.63.

**Four things to fix in the same pass.**

1. **Keep `_DEFAULT_MODEL = "claude-haiku-4-5-20251001"` (`vision.py:29`) — the old item 1 was inverted.** It is the documented **Full ID** for Claude Haiku 4.5 under *Current Models (recommended)*, status **Active**; the bare `claude-haiku-4-5` is the **Alias** column (`claude-api` skill, `shared/models.md:58, 68`, read 2026-08-21). Live confirmation (Model deprecations, read 2026-08-21): Active, Deprecated N/A, retirement *not sooner than 15 Oct 2026*, and Anthropic's own **recommended replacement** for retired Haiku 3 and 3.5. Reference [36] must be re-stated: "never append a date suffix" forbids *constructing* one for a model that has none (4.6-generation ids are dateless *and* pinned), not using a published one. Hence the rule — **the key's `model` holds a pinned snapshot, never an alias**: an alias re-point changes the model and leaves the key identical, serving the recorded reply forever. `client.models.retrieve` is optional record-time hygiene, not blocking step zero, and needs a key.
2. **Rebase on PR #28 before touching `tests/`.** `feat/22-browser-test-gate` (cf0d818 — **OPEN**, unmerged, and what the shared checkout has checked out, 7 worktrees live) already did old items 2 and 3: the module-level `pytest.importorskip("playwright.sync_api")` is gone (`main:89`, where it deleted the whole module wherever the `cutout` extra is absent — i.e. CI: `playwright` is declared only there, `pyproject.toml:78-79`, and `[tool.wads.ci]` requests no extras), and the live test carries `live_api` + `requires_live_api` + `browser`/`ffmpeg` (`:103-106`). Confirm #28 merged, then branch from `main`; else branch from cf0d818 and say so. Two corrections to what that recovers: only **four** of the six restored tests are `_parse_issues` tests (`main:32, 39, 44, 51`) — `:56` and `:63` are the **only tests in the repo executing `VisionLMVerifier.verify()`** (`:135` is the billed one; `orchestrate.py:109` keeps the verifier out of the default chain), so on `main` the vision verifier has **zero executed tests in CI**. And **do not delete `test_vision_lm_full_pipeline_returns_findings`**, as an earlier draft said: it is the only node exercising `extract_frames` (`vision.py:93`), the dedup (`:95-97`) and block assembly (`:102-114`), which a `frames: Sequence[bytes]` cassette cannot reach, and this section's own ruling is "mark it, do not cassette it". Add the cassette nodes **alongside** it, plus the still-missing guard that a test constructing a paid client carries the marker.
3. **Declare a `vision = ["anthropic"]` extra.** Imported lazily at `vision.py:85`, declared nowhere. **Licence perimeter adjudicated over the whole 16-distribution closure** (the previous claim rested on 2), read from installed `dist-info`, 2026-08-21. Only **four** are new to `an` as shipped — anthropic 0.75.0 **MIT** ("Copyright 2023 Anthropic, PBC." + the verbatim grant), distro 1.9.0 **Apache-2.0**, docstring-parser 0.17.0 **MIT**, jiter 0.11.0 **MIT** — because `tts = ["elevenlabs"]` (`pyproject.toml:93-95`, shipped) already pulls httpx/httpcore/h11/idna/anyio/sniffio **and certifi**. Two recorded exceptions, neither introduced here: `typing-extensions` 4.15.0 is **PSF-2.0**, already *hard* via pydantic; `certifi` 2025.10.5 is **MPL-2.0** — file-level weak copyleft over an unmodified CA bundle consumed as data, so nothing `an` ships attracts reciprocity. Pin `anthropic<1`, and land the ~25-line closure walk as `tests/test_licence_perimeter.py` naming both exceptions, so this is a guard rather than a sentence.
4. **Rewrite the module docstring at `vision.py:8-10`.** It claims the skip-with-`info` behaviour exists "so the orchestrator can keep this verifier in its default chain". Nothing in `an/` puts it in any chain (`orchestrate.py:109`), and the canonical map says the opposite (`misc/docs/architecture_as_built.md:162`: "opt-in"). Say: opt-in; the lazy import and key check skip cleanly so a caller without the extra gets an informational Finding rather than an `ImportError`; a call that *fails* is a failure, not a pass.

**Two decisions for you, not fixes.** **D1 — severity for "configured, called, no verdict"** (a failed call; an unparseable reply): `warning` leaves `passed=True` (`_base.py:56-57`), `error` fails the orchestration. *Recommendation and safe default: `warning`* — a transient 529 must not red a whole render, while any non-`info` Finding makes a dead verifier visible; choose `error` only if a refusal should fail a render. `info` is wrong either way, being the *not-configured* severity (`:79, 82, 87`), and reusing it for "configured and broken" is what made this verifier invisible. **D2 — jiter 0.11.0 declares `License-Expression: MIT` but ships no LICENSE file and no Project-URL** in its installed metadata; the house rule reads an unverifiable licence as a refusal, and refusing means no `vision` extra at all. *Recommendation and safe default: accept on the declared PEP 639 expression, record it as a named exception in the perimeter test, verify upstream once at the pin* — a packaging omission, not an unknown licence.

**And the ruling** (attacked twice, upheld twice): `an bench` never invokes the vision judge. The strongest reason is *not* that its input is nondeterministic — over frozen frames it is perfectly reproducible — but that a cassetted judge is then a **constant**, invariant to the code under test, so it can never move under a deliberate degradation. Live it fails reproducibility; cassetted it fails the mutation half. `orchestrate.py:109` already excludes it from the default chain; make that an explicit recorded decision in the `an-dev-bench` skill (which does not yet exist) rather than an inference. (Fix in passing: `orchestrate.py:98`'s docstring says the default chain is `[LayoutLintVerifier()]` while `:109` adds `MediaQualityVerifier()`.)

---

## 5. What to reuse from the existing prototype

The `bench_prototype` folder is 14 files / 514 lines. About 120 lines are portable.

**Directly reusable, with named changes:**

| what | where | change required |
|---|---|---|
| `bench()` timing harness | `bench.py:17-23` | Add repeat counts and a percentile — Wave 2 requires "byte-identical across two runs OR a recorded band". |
| POST sink `Hdl.do_POST` | `bench3.py:11-15` | **Two real bugs**: no lock (ThreadingTCPServer + concurrent POSTs interleave into ffmpeg's stdin = silent corruption) and no frame index (nothing enforces ordering). Both must be fixed before the throughput track means anything. |
| The encode ladder's eight argument lists | `enc.sh:9-16` | Reuse as **data**; rewrite the shell (`stat -f%z` is macOS-only, and it needs the missing `cart/` frames). |
| `rss_mb` / `total_chrome_rss` | `par2.py:13-18` | `par2.py:17` greps `Chromium|chrome` and misses `headless_shell`, which is the binary that actually launches. |
| Colour-profile probe logic | `color.py:23-43` | Use `an`'s runtime as the fixture, not `color.html`. |

**Must be rewritten or discarded:** `page.html` (the canvas-2D fixture responsible for the four wrong numbers below); `bench3.py`'s raw-RGBA arm (**dead code** — it calls `getContext('2d')` on `an`'s stage, which carries a WebGL context and returns `null`); the entire pixi family (`pixi.html`, `pixi_cdn.html`, `pixi2.py`, `pixitest.py` — all four use the PixiJS **v8** API against a v8 CDN bundle and cannot run on the vendored 7.4.2, and `pixi_cdn.html:3` fetches from `cdn.jsdelivr.net` so it must never enter the repo); `par.py` (superseded, contains an `if False` line); `bench.py:26-27`'s `channel='chromium'` leg (a different binary from the one `an` uses).

**Three experiments cited in the research record have no surviving code at all**: the `cart/` frame generator `enc.sh` depends on; the pixel-diff analysis that produced the famous edgeMax 161/154/70 numbers; and the network-blocked render. The last is already superseded by committed code — `tests/test_vendored_engine.py:145-192` renders a real project with every non-loopback request aborted and asserts `vendor/pixi.min.js` was actually requested. Reuse the existing `hermetic_browser` fixture; do not grow a second guard.

**What the vendoring of PixiJS 7.4.2 invalidated.** Attribution is clean: re-running the prototype's own `page.html` today reproduces its published numbers within noise (locator 100.0 vs 99.9 ms/f; CDP 50.1 vs 50.0; toDataURL 15.9 vs 15.5; the flag speedup 2.3x), so every divergence below is caused by the page — i.e. by the WebGL runtime.

| prototype conclusion | corrected |
|---|---|
| "The render is 0.8 ms/f; >99% of frame time is capture overhead" | Seek-only on the real runtime is **10.2–11.4 ms/f** at 1080p. It is ~90%, and the implied floor is 13x too optimistic. |
| "In-page capture is 6.4x faster (15.4 ms/f, 64.8 fps)" | **3.5x** (35.94 ms/f, 27.8 fps end-to-end through the POST sink into ffmpeg). Still worth doing; state 3.5x in the issue. |
| "`--disable-frame-rate-limit` gives 2.3x" | **1.05x** on the WebGL runtime (1.17x for the locator path). The 2.3x is a canvas-2D artefact. Do not ship the flag. |
| "~86 MB per parallel context; 3.6x at k=4" | **~216–335 MB** marginal per context; **2.09x** at k=4 (29.6 → 47.9 → 61.9 fps). Note `an/render.py:163,185` fans out one **browser** per shot, so `--parallel 4` is ~4 x 700 MB. |
| "Batching frames into one `page.evaluate` should be faster" | **Slower** — 43.1 vs 35.9 ms/f. Record as a negative result so nobody re-derives it. |

**Two new results worth more than anything the prototype recommended.** First: ~~setting the stage canvas to `display:none` after `anLoadScene` drops the seek loop from 10.16 to **0.66 ms/f** — 15x — with **bit-identical pixels** (maxdiff 0 over a full 1920x1080 frame). Roughly 9.5 of the 10.2 ms is Chromium compositing a canvas nobody looks at. Scope it to the render path only; `an preview` needs the canvas composited.~~

> **SETTLED AGAINST, 2026-08-22 (an#57).** The seek-loop number reproduces exactly — 16.44 → 0.69 ms/f here, and 0.69 ms *is* the bare `page.evaluate` round trip, so the seek itself becomes free. **The inference from it does not survive.** `_capture_frames` takes a Playwright *element* screenshot, which Playwright implements as a page capture clipped to the element's document rect: it awaits visibility, then reads the compositor. So `display:none` and `visibility:hidden` make it **time out**, and the two spellings Playwright does accept — `opacity:0` and off-screen positioning — return an **all-white frame** (1 distinct RGBA over the whole picture). Toggling hide-for-seek / show-for-shot per frame measured **116.11 ms/f against a 115.30 ms/f baseline**: no win at all, because the screenshot re-pays the composite the seek avoided. "Bit-identical pixels" was measured on the *seek*, not on the *capture*, and the win is only reachable together with a change to the capture path. `tests/test_cutout_runtime_files.py::test_the_capture_page_never_stops_compositing_the_stage_canvas` now forbids every such spelling in `index.html`. Second: capture cost decomposes ~50/50 into GPU readback (`gl.readPixels` of 8.3 MB ≈ 13.4 ms) and PNG encode (≈14.1 ms), which caps any zero-cost-transport idea at about 2x further, not the 20x the transport bug suggested — and kills WebP outright (82.4 ms/f, 3x worse than PNG).

Finally: **the encode ladder's structural conclusion holds and is now measurable on real `an` output.** Over 30 real 1080p frames, edge-band mean error: `an`'s current flags 11.35; crf18 yuv420p 11.05; crf18 `-tune animation` 10.96; crf18 **yuv444p** 3.79; mathematically lossless yuv420p 10.15; lossless yuv444p **0.49**. Lossless 4:2:0 barely improves on the default; 4:4:4 at crf18 is 3x better. Bitrate is second-order, pixel format is first-order — which is exactly the Wave 3 lever. Note the magnitudes are ~3x smaller than the prototype's because the real frames are 31 colours on white; see the corpus recommendation for a saturated-fill scene.

---

## 6. Recommendations for the Wave 2 issues

**Issue 1 — `an bench`: render a fixed corpus, emit a ledger.**
*Changed:* the metric list is entirely different from the epic's draft — `mean adjacent-frame SSIM` is out (moves the wrong way), and the ledger splits into three blocks that must never be mixed: `metrics` (render-side and encode-side, separately labelled), `tripwires` (golden identity, counting zero toward any criterion), and `provenance`. Add three schema fields **before the first row exists**, because retrofitting them invalidates every prior entry: a per-metric per-mutation `direction` (two-sided where the optimum is interior), a `null`-vs-`no change` distinction, and the environment tuple from §2. Bench must render into a **copy** of the corpus (render mutates the project dir), record `parallel=1` for timing metrics, and record `audio_cache: warm|cold`. `misc/bench/` and the `bench` command are greenfield — one entry at `an/tools.py:182`.
*Re-scope:* split the ffmpeg/x264 pinning (`-threads 1 -crf 23 -preset medium` + colour tags) out as a prerequisite PR; it changes committed baselines and must land before the first ledger, not with it.

**Issue 2 — Determinism mode (`AN_DETERMINISTIC=1`).**
*Changed, substantially:* the two things the epic names (blink phase, palette hashing) are **already deterministic**, verified. The budget goes to the Chromium launch flags, `headless=True`, `playwright==1.55.0`, the ffmpeg flags, and the metadata stamping. **Recommend making the flags unconditional rather than env-gated** — they are a verified 0/144-byte no-op on today's output, and a render whose rasteriser depends on an env var is non-reproducible by default, which is the property this wave exists to remove. Spend the env var on stamping and on the browser-side assertions (no filter on the stage; `Ticker.shared.started === false`; the capture page is `index.html`). Fold in the one-line `check_requirements.py:111-118` Linux path fix — this is the wave that first makes CI launch a browser.

**Issue 3 — The golden corpus.**
*Changed:* the procedural/descriptor split is confirmed as mandatory (12x sensitivity difference) and **four scenes must be added** (flat/graded field, saturated fill under black outline, multi-shot project, `aa_probe` with non-axis-aligned edges). Render at **native** resolution, not 1080p. The second frame's time is pinned per scene, and `--bless` refuses identical pairs. The assertion is on **decoded pixels**, never PNG bytes. Environment in the filename; skip loudly off-environment. `--bless --reason` required.
*Re-scope:* the cross-architecture experiment (render the corpus once on the CI runner, diff decoded pixels) is the **first task of the wave**, not a follow-up — it decides whether the corpus is a CI gate or a local one, and therefore whether the ledger schema needs a cross-machine band column at all. It is gated on an#22.

**Issue 4 — Cassette the vision verifier.**
*Changed:* the memoization point is a **new extracted seam** (`judge_frames`), not the existing `verify()`; `CassetteMiss` must derive from `BaseException`; there is **one** spend switch (`AN_LIVE_API_TESTS`) and adding a second is the failure the skill exists to prevent; frames come from the golden corpus, never a fresh render. Step zero is verifying the model id, because a dead id is currently a silent `passed=True`. Also: delete the key-presence-gated live test, move the module-level importorskip, and declare a `vision` extra.
*Ordering:* this issue does **not** need to wait for issue 3 — seed two PNGs at `misc/bench/golden/<scene>/` and let issue 3 grow the set. The dependency is on the path convention, not on completion.

**Issue 5 — `an bench --compare` and the PR delta requirement.**
*Changed:* `--compare` cannot flag regressions by a single per-metric sign — the sharpness family moves in opposite directions for the two mutations. It needs the sign table from issue 1, evaluated **per mutation**. It must also refuse to compare rows whose provenance differs: different `scene_contract_sha256` means every metric in the row is uninterpretable rather than good or bad; different resolution, mask parameters, palette, encode command, or environment tuple likewise. In CI, `--compare` runs; the pixel gate skips.

**Issue 6 — Mutation-test the harness (the wave's real deliverable).**
*Changed, most of all:* **two disjoint levers are mandatory, and the criterion must be counted per family.** An encoder lever (raise CRF) moves only post-encode metrics and cannot touch any golden-frame metric, because the corpus is upstream of the encoder — requiring ">=3 metrics" from a CRF change alone would fail not because the instrument is blind but because the corpus is upstream, and that failure would be misdiagnosed as the harness being wrong. A render lever (AA off at `runtime.js:517`) moves the render-side family and the golden tripwire. Restate the criterion as **">=3 metrics from >=3 distinct causal families, per mutation, in a direction declared in advance"** and publish the family table (§1c) alongside it. Note honestly that the AA lever has exactly three families available today and the third (`video_stream_bytes`) is the weakest and the only unreviewed metric in the set — so consider adding a third lever whose failure a still frame genuinely cannot see (per-frame re-rasterisation jitter, or a temporal-sampling bug in `anSetTime`), which would also give `edge_flip_rate` a reason to exist.
*Also:* mutation-test the **guards**, not only the metrics. A guard test that stays green when the bug is reintroduced is the failure this wave is about; the `test_a_cassette_miss_is_not_swallowed_by_the_verifier` and `test_no_metric_family_supplies_two_witnesses` tests both need to be shown failing before they are trusted.

**Two documentation fixes to fold in**, since this wave touches both files: `misc/docs/architecture_as_built.md:196-208` (nonexistent shot cache) and `an/verify/media.py:126-135` (docstring claims Wang et al. SSIM for a global-moment implementation).

---

## 7. Open questions

1. ~~**Does SwiftShader produce identical pixels across CPU architectures?**~~ **SETTLED — YES, for the pinned build.** See `misc/docs/wave2_crossarch_verdict.md` (an#31). Both render paths, four machines (local arm64 macOS, `macos-latest`, `ubuntu-latest` x86-64, `ubuntu-24.04-arm`), 132 frames each: **zero differing pixels and zero differing PNG bytes**. Stronger than the question asked — the x86-64 runner uses a *different SwiftShader JIT backend* (Subzero, not LLVM 10.0.0), so the JIT-specialisation hypothesis is refuted at the backend level, not merely across CPUs. Consequences: the golden corpus can be a **CI gate**; the golden filename should key on the **Chromium build only**, not the platform; and the render-side ledger metrics need **no cross-machine band column**. **The *encode* side is now measured too** (an#34, after the x264 pinning made it answerable) and its answer is the OPPOSITE: same-ISA/same-x264-build is byte-identical, but a different ISA moves the decoded stream slightly (luma 0.36–2.66% of samples, mean |d| <=0.034) and a different x264 build moves it by two orders of magnitude more (up to 99.2% of samples, mean |d| 3.94, max 36). **Do not band it — scope it**: a band wide enough to absorb an x264 build change would swallow `flat_field_deviation`'s entire crf18->23 signal. `--compare` must REFUSE rows whose x264 build or ISA differ, and the provenance row must carry the x264 SEI verbatim and the ISA. Full table in the verdict record.

2. **Which canonical SwiftShader configuration?** `--disable-gpu` alone is verified byte-exact against today's output, so the pin and the corpus can land in one PR. Chromium's *documented* form (`--use-gl=angle --use-angle=swiftshader` [1]) is supported and stable against future flag-precedence changes but re-baselines by 1.55% of pixels. *Settled by:* a decision, not an experiment — but it must be made before the corpus is blessed.

3. **Are `coded_luma_edge_error` and `chroma_edge_dCr` independent after their corrections?** They were r=0.990 broken. *Settled by:* computing both across the eight-arm encoder matrix and committing the correlation to the ledger as the redundancy guard. If still >0.9, drop one.

4. **Does `encode_ringing_excess` beat plain edge-band MAE?** *Settled by:* computing edge-band MAE over the identical mask on the same 2x2 mutation matrix. Within ~15% on both arms, ship the MAE.

5. **Is `flat_field_deviation` independent of `encode_flicker_on_held_pixels`?** Both are "encoder damage away from the moving parts", with very different magnitudes (133x vs 2.6x over the CRF ladder) but a shared sign. *Settled by:* the same correlation pass as (3). If correlated, `video_stream_bytes` takes the third encode slot.

6. **What does the panel do on a real `an` shot?** Every number came from synthetic scenes or the shipped examples. A production shot has SVG-textured sprites, procedural mouth beziers, viseme swaps and camera tweens, and its edge population may be dominated by texture-sampling edges rather than vector outlines — which changes the mask's character. *Settled by:* running the panel over `examples/single_character` and `examples/park_bench_cartoon` and printing the mask fraction **before any threshold is written down**.

7. **Does byte-exactness survive a Playwright/Chromium bump?** Verified for 1187→1223 under the pin (pixels identical, PNG bytes not). *Settled by:* installing a second Playwright version in a throwaway venv and re-rendering. This decides whether the two-number fuzz band is needed on day one or only at Wave 8, when text arrives and fonts — the universally-reported top cause of visual-test flakiness [3][7] — become an input for the first time. Today `runtime.js` instantiates no `PIXI.Text` or `BitmapText` and no committed SVG (390 of them) contains a `<text>` element, so fontconfig is genuinely not applicable; record that in the ledger as *checked and closed*, so Wave 8 is told it just acquired a new determinism input.

8. **Is MIT-CMU inside the licence perimeter?** Pillow 11.3.0 [28] is MIT-CMU, permissive but not literally one of the four named. It matters because Pillow is imported at `an/verify/media.py:157` and `an/characters/silhouette.py:45`. *Settled by:* an explicit ruling. The bench path itself needs no Pillow (the ffmpeg rawvideo pipe is byte-identical and faster), so this is a pre-existing question the wave surfaces rather than creates.

9. **Should `an bench` fail on a golden mismatch, or record and report?** The WebKit/Chromium answer is a first-class rebaseline command (`--reset-results` [9]), which Wave 2 has not scoped. *Settled by:* a decision, made when issue 3 is written.

10. **Ordering and back-pressure in the throughput track's POST sink** remain entirely unvalidated — the prototype's sink has neither a frame index nor a lock, and a dropped or reordered frame is silent corruption that a per-frame pixel comparison would never catch. *Settled by:* a long-run test with a per-frame index asserted at the ffmpeg side. Note the pixel half of the throughput gate is **already satisfied**: the two capture paths are byte-identical in RGB at 1080p (maxdiff 0), and the only residual differences are the alpha channel and ordering.

---

## References

1. [Chromium — SwiftShader](https://chromium.googlesource.com/chromium/src/+/main/docs/gpu/swiftshader.md) — read 2026-08-21. Documents `--use-gl=angle --use-angle=swiftshader`, `--use-angle=swiftshader-webgl`, `--use-vulkan=swiftshader`; states automatic SwiftShader WebGL fallback is deprecated and context creation will fail instead. `--use-gl=swiftshader` is not among the documented values.
2. [Chromium blink-dev — Intent to Remove: SwiftShader WebGL fallback](https://groups.google.com/a/chromium.org/g/blink-dev/c/yhFguWS_3pM/m/YUEOoPeUAAAJ) — read 2026-08-21. Deprecation Chrome 130, removal Chrome Desktop 137.
3. [Playwright — Visual comparisons / test snapshots](https://playwright.dev/docs/test-snapshots) — read 2026-08-21. Snapshot naming `{snapshotName}-{browser}-{platform}.png`; "Screenshots differ between browsers and platforms due to different rendering, fonts and more"; uses the pixelmatch library.
4. [Playwright — PageAssertions API](https://playwright.dev/docs/api/class-pageassertions) — read 2026-08-21. `threshold` (YIQ, default 0.2), `maxDiffPixels` (unset by default), `maxDiffPixelRatio` (unset by default).
5. [Playwright — Release notes](https://playwright.dev/docs/release-notes) — read 2026-08-21. v1.57 switched to Chrome for Testing builds. PyPI latest 1.62.0 as of the same date.
6. [Playwright (Python) — Browsers](https://playwright.dev/python/docs/browsers) — read 2026-08-21. Headless uses `chromium-headless-shell`; headed uses full Chromium.
7. [Vitest v4.1.11 — Visual regression testing](https://vitest.dev/guide/browser/visual-regression-testing) — read 2026-08-21. "Visual regression tests are inherently unstable across different environments"; `allowedMismatchedPixelRatio`; stricter limit wins.
8. [web-platform-tests — Writing reftests](https://web-platform-tests.org/writing-tests/reftests.html) — read 2026-08-21. Pixel-exact within an 800x600 window; `<meta name=fuzzy content="maxDifference=15;totalPixels=300">`, short and range forms.
9. [Chromium — Web test expectations](https://chromium.googlesource.com/chromium/src/+/HEAD/docs/testing/web_test_expectations.md) — read 2026-08-21. `-expected.{txt,png,wav}` baselines checked into `web_tests`; platform fallback directories; `run_web_tests.py --reset-results`.
10. [Skia — Gold](https://skia.org/docs/dev/testing/skiagold/) — read 2026-08-21. Baselines managed outside Git in lockstep with commits; human triage as positive/negative; >500,000 images per commit. **Note:** this page does *not* state exact-digest matching, per-test fuzzy opt-in, or a binary-file-history rationale; an earlier survey claim to that effect was refuted.
11. [Chromium — GPU pixel testing with Gold](https://chromium.googlesource.com/chromium/src/+/HEAD/docs/gpu/gpu_pixel_testing_with_gold.md) — read 2026-08-21.
12. [pytest-mpl — Hash mode](https://pytest-mpl.readthedocs.io/en/stable/hash_mode.html) — read 2026-08-21. Hash-only loses visual inspection; encode library versions in the filename (`mpl35_ft261.json`).
13. [pytest-mpl v0.17.0 `plugin.py`](https://raw.githubusercontent.com/matplotlib/pytest-mpl/v0.17.0/pytest_mpl/plugin.py) — read 2026-08-21, lines 74-82: SHA-256. Default RMS tolerance is 2/255.
14. [GitHub Docs — Working with non-code files](https://docs.github.com/en/repositories/working-with-files/using-files/working-with-non-code-files) — read 2026-08-21. PNG/JPG/GIF/PSD/SVG; 2-up, swipe, and onion skin ("elements move around by small, hard to notice amounts").
15. [GitHub Docs — About billing for Git LFS](https://docs.github.com/en/billing/concepts/product-billing/git-lfs) — read 2026-08-21. 10 GiB free tier; bandwidth charged to the repo owner including forks and CI; $0 budget blocks LFS for the month.
16. [GitHub Docs — About Git Large File Storage](https://docs.github.com/en/repositories/working-with-files/managing-large-files/about-git-large-file-storage) — read 2026-08-21. Cannot be used with GitHub Pages or template repos; objects excluded from source archives.
17. [Screenshotbot — Can Git LFS scale?](https://screenshotbot.io/blog/can-git-lfs-scale) — read 2026-08-21. The 50 MB x 100 commits ≈ 5 GB argument; slow clones; impractical bisection.
18. [FFmpeg n8.1 — `libavfilter/vf_ssim.c`](https://raw.githubusercontent.com/FFmpeg/FFmpeg/n8.1/libavfilter/vf_ssim.c) — read 2026-08-21, lines 28-29, 131, 307. "uses the standard approximation of overlapped 8x8 block sums, rather than the original gaussian weights"; 4-pixel stride. LGPL-2.1+ header.
19. [pixelmatch v7.1.0 — LICENSE](https://raw.githubusercontent.com/mapbox/pixelmatch/v7.1.0/LICENSE) — read 2026-08-21. ISC. README at the same tag: OKLab/HyAB metric, `includeAA` false by default.
20. [libjxl v0.11.1 — LICENSE](https://raw.githubusercontent.com/libjxl/libjxl/v0.11.1/LICENSE) — read 2026-08-21. BSD 3-Clause; carries the reference `tools/ssimulacra2.cc`.
21. [Pacidus/py-ssimulacra2 — LICENSE](https://raw.githubusercontent.com/Pacidus/py-ssimulacra2/main/LICENSE) — read 2026-08-21. BSD 3-Clause; PyPI `ssimulacra2` 0.3.0.
22. [google/butteraugli — LICENSE](https://raw.githubusercontent.com/google/butteraugli/master/LICENSE) — read 2026-08-21. Apache-2.0. No PyPI package (404, 2026-08-21).
23. [kornelski/dssim — LICENSE](https://raw.githubusercontent.com/kornelski/dssim/main/LICENSE) — read 2026-08-21. **AGPL-3.0** — refusal. PyPI `dssim` 1.3.0 is an unrelated discrete-event simulation framework declaring Apache-2.0.
24. [NVlabs/flip — LICENSE](https://raw.githubusercontent.com/NVlabs/flip/main/LICENSE) — read 2026-08-21. BSD 3-Clause, no field-of-use restriction. PyPI `flip-evaluator` 1.7, compiled per-platform wheels.
25. [Netflix/vmaf v3.0.0 — LICENSE](https://raw.githubusercontent.com/Netflix/vmaf/v3.0.0/LICENSE) — read 2026-08-21. BSD-2-Clause-Patent.
26. [oxipng v9.1.5 — LICENSE](https://raw.githubusercontent.com/oxipng/oxipng/v9.1.5/LICENSE) — read 2026-08-21. MIT.
27. [pyoxipng v9.1.1 — LICENSE](https://raw.githubusercontent.com/nfrasser/pyoxipng/v9.1.1/LICENSE) — read 2026-08-21. MIT.
28. [Pillow 11.3.0 — LICENSE](https://raw.githubusercontent.com/python-pillow/Pillow/11.3.0/LICENSE) — read 2026-08-21. MIT-CMU (HPND variant with a no-endorsement clause); also read from the installed `dist-info`.
29. [PixiJS v7.4.2 — LICENSE](https://raw.githubusercontent.com/pixijs/pixijs/v7.4.2/LICENSE) — read 2026-08-21. MIT, "Copyright (c) 2013-2023 Mathew Groves, Chad Engler". In-repo copy at `an/data/cutout_runtime/vendor/pixi.LICENSE.txt`, sha256-pinned by `tests/test_vendored_engine.py:37`; bundle 456,133 B, banner "pixi.js - v7.4.2, Compiled Wed, 20 Mar 2024 19:55:28 UTC".
30. anthropic-python 0.75.0 — LICENSE, read from the installed distribution at `.../anthropic-0.75.0.dist-info/licenses/LICENSE`, 2026-08-21. MIT, "Copyright 2023 Anthropic, PBC." httpx 0.28.1: BSD-3-Clause.
31. [google/swiftshader — Reactor documentation](https://github.com/google/swiftshader/blob/master/docs/Reactor.md) — read 2026-08-21. Per-ISA JIT specialisation; silent on determinism.
32. [V8 `src/base/ieee754.cc`](https://denolib.github.io/v8-docs/ieee754_8cc_source.html) — read 2026-08-21. fdlibm port, giving platform-independent `Math.sin`/`cos`/`tan`/`exp`/`log`.
33. numpy 2.2.6 / 2.5.0 — LICENSE.txt, read from the installed `dist-info`, 2026-08-21. BSD 3-Clause.
34. [jest-image-snapshot — LICENSE.txt](https://raw.githubusercontent.com/americanexpress/jest-image-snapshot/master/LICENSE.txt) — read 2026-08-21. Apache-2.0.
35. [odiff — LICENSE.txt](https://raw.githubusercontent.com/dmtrKovalenko/odiff/main/LICENSE.txt) — read 2026-08-21 (`main`, not a pinned tag). MIT. Uses the YIQ NTSC algorithm.
36. Anthropic model catalogue and pricing — `claude-api` skill, § Current Models, cached 2026-06-24. `claude-haiku-4-5`, 200K context, $1.00/1M input, $5.00/1M output; guidance to use the exact bare id and never append a date suffix. **Not verified against the live Models API** — no key in this environment.
37. WebKit/WebKit — `LayoutTests/platform/` listing and GitHub code search `extension:checksum repo:WebKit/WebKit` (total_count 0) — read 2026-08-21. The `-expected.checksum` sidecar is dead practice; surviving hits are in an archived 2010-era fork. The platform-directory convention is alive (glib, gtk, ios-26, mac-tahoe, wpe-wk2, win, …).
38. scikit-image v0.25.2 — LICENSE.txt, read 2026-08-21. BSD-2-Clause + BSD-3-Clause + MIT, no copyleft. (OpenCV: Apache-2.0 since 4.5.)

*All in-repo file:line citations refer to `thorwhalen/an` between commits `a7a0c87` and `cf0d818` (branch `feat/22-browser-test-gate`), 2026-08-21. The render path is byte-identical across that range (`git diff --stat a7a0c87..cf0d818 -- an/adapters/ an/data/cutout_runtime/ an/render.py` is empty), so every pixel measurement applies to both. Note that `236cb56` in that range replaced module-level `pytest.importorskip` gating with `@pytest.mark.browser` / `@pytest.mark.ffmpeg` markers and moved `pillow` into the `cutout` extra — Wave 2 tests must use the new markers, because a module-level `importorskip` silently deletes a whole module from collection, which is exactly how a determinism test could report green while never running.*