---
name: an-dev-render-pipeline
description: The frame path in the `an` repo, end to end — Pixi rasterisation, Playwright element capture, the PNG stage, the x264 mux, concat and delivery — and what each stage can lose. Use when changing anything that touches a pixel or an encode flag - supersampling, resolution, antialias, `device_scale_factor`, `autoDensity`, downscale filters, `-pix_fmt` / CRF / preset / colour tags, `_capture_frames`, `_ffmpeg_mux`, `_ffmpeg_concat`, `runtime.js`'s PIXI.Application options, or the per-shot mp4 store. Triggers on "supersample", "why is the render soft", "add an encoder flag", "make it render bigger", "downscale", "4:4:4", "faststart", "the frames look wrong", "speed up the render".
---

# The frame path, and what each stage can lose

Authorities, in order: `misc/docs/wave3_research.md` (measured 2026-08-22),
`misc/docs/wave2_research.md`, `misc/docs/wave2_crossarch_verdict.md`, then this
file. **Do not re-derive them.** Four of `wave3_research.md`'s findings are the
*opposite* of the obvious answer, and two of them are the opposite of what epic
#9's brief asks for.

`an-dev-bench` is the sibling skill: this one is about the pixels, that one is
about measuring them. Any change here that could move a pixel needs the
`run-browser-tests` label on its PR —
`gh api -X POST repos/thorwhalen/an/issues/<N>/labels -f 'labels[]=run-browser-tests'`
(`gh pr edit --add-label` silently no-ops on this repo).

---

## 1. The path, stage by stage

```
an.render.render(project, …)
  └ RenderContext(fps, resolution, work_dir, mall, strict_assets)   ← per-render knobs live HERE
     │
     ├ per shot (thread pool, DEFAULT_PARALLEL_CAP=4, one Chromium each)
     │   CutoutRenderer.render(shot, ctx)                 an/adapters/cutout/render.py
     │    1. compile_shot(...)          → CutoutSceneJSON   ← the wire contract; its digest is
     │                                                        `scene_contract_sha256`
     │    2. _stage_job(...)            → <project>/.an/render_work/<shot>/{runtime,frames}
     │    3. _serve_dir + Playwright Chromium, DETERMINISTIC_CHROMIUM_ARGS, headless=True,
     │       viewport = ctx.resolution
     │    4. window.anLoadScene(scene)  → new PIXI.Application({view:#stage, width, height,
     │                                     backgroundColor, antialias:true, autoStart:false,
     │                                     preserveDrawingBuffer:true})
     │    5. _determinism_report(page)  → raises on a breached perimeter (enforced by default)
     │    6. _capture_frames            → per frame: anSetTime(t); locator('#stage').screenshot()
     │                                     → frames/frame_%06d.png   (Chromium's PNG bytes)
     │    7. _ffmpeg_mux                → silent.mp4  (libx264, yuv420p, DETERMINISTIC_X264_ARGS,
     │                                                 -movflags +faststart)
     │    8. _ffmpeg_add_audio          → <shot>.mp4  (-c:v copy + AAC, silent if no dialogue)
     │
     ├ _render_one → project.mall["shots"][shot.id] = mp4 bytes        ← WRITE-ONLY, see §5
     ├ _ffmpeg_concat  1 shot: shutil.copy  |  ≥2 shots: concat demuxer -c copy
     └ project.mall["output"][name] = bytes
```

**Where each stage can lose something, and how much:**

| stage | loss | measured |
|---|---|---|
| 4 rasterise | AA is MSAA-limited: the edge transition stays ~1 logical pixel however good the geometry is | `aa_probe` `edge_transition_width` 2.8807 at k=1 |
| 6 capture | **nothing, if you leave it alone.** Every documented way of making it capture more pixels *except* `resolution` + `autoDensity:false` loses them again silently — §2 | — |
| 7 encode | **chroma subsampling is FIRST-order**; quantiser damage is second | edge-band error 11.35 (4:2:0 crf23) → 3.79 (4:4:4 crf18); mathematically lossless 4:2:0 only reaches 10.15 |
| 8 audio mux | nothing (`-c:v copy`) | — |
| concat | no pixels, but **`+faststart` is silently dropped** — `-c copy` does not re-apply it | affects multi-shot only; 5 of 6 corpus scenes take the `shutil.copy` path, so the corpus is internally inconsistent on this and `file_bytes` cannot see it |

---

## 2. The measured negatives — do not re-attempt any of these

Each was tried, measured, and refused. They are recorded here so the next
session does not spend the afternoon rediscovering them.

**`device_scale_factor` on the browser context** — a blind *upscale*. The scene
still rasterises at 1x and Chromium stretches it. Refuted in Wave 2.

**`resolution: k` with `autoDensity: true`** — the same failure through the door
whose name most suggests it is the right one. `autoDensity` sets the canvas *CSS*
size to the logical size, so Chromium composites the k-times backbuffer down
**before** the screenshot: a blind browser downscale with no filter choice and no
record that it happened. Measured on `aa_probe` (declared 320x240):

| Application options | PNG on disk |
|---|---|
| today (neither key present) | 320x240 |
| `resolution: 2, autoDensity: false` | **640x480** |
| `resolution: 2, autoDensity: true` | 320x240 |

> **`autoDensity: false` is the supersample path**, and it is load-bearing.
> Neither key is in the options object today, so PixiJS's `RESOLUTION: 1` applies
> silently and *both* must be introduced together.

**Lanczos (or bicubic) as the downscale filter** — refuted. It is a photographic
resampler and this is not photographic content; its negative lobes ring on
hard-edged flat fills. `edge_transition_width` at k=2 on `saturated_outline`, the
scene closest to the target idiom: box **2.4921 (+5.2%)**, lanczos **7.3134
(+208.8%)**. The metric's own docstring says "3+ means the picture has gone soft".
Bicubic fails the same way and additionally pins at a suspiciously flat 4.0000 on
three scenes.

> **The correct downscale is a plain k x k block mean, in numpy.** At an integer
> ratio it *is* the supersample resolve, not an approximation — and it measured
> identical to PIL's `Image.BOX` to four decimals on five scenes. No PIL, no
> resampler, no new dependency, and no filter choice to defend.
> ```python
> blocks = frames.reshape(n, h // k, k, w // k, k, c).astype(np.float64)
> resolved = np.rint(blocks.mean(axis=(2, 4))).clip(0, 255).astype(np.uint8)
> ```

**Downscaling in ffmpeg (`-vf scale=…`)** — refused for two independent reasons,
either of which is sufficient: it moves `x264_argv`, which refuses **every**
encode-side metric against every existing ledger row; and it retires the
cross-arch verdict's load-bearing clause that *"ffmpeg never touches a frame"*.
**The downscale runs in the frame stage**, before the PNGs are written.

**Raising the Playwright viewport to match a supersampled backbuffer** —
unnecessary. The viewport stayed at 320x240 while the element screenshot came out
640x480, un-clipped. Capture beyond the viewport works.

**`-tune animation`** — measured at **0.8%**. Dropped: it is not a wave, and
adding it moves `x264_argv` and refuses every encode-side metric for nothing.

**Reading the factor off the corpus.** At 320x240 the k ladder reads 1.0x / 1.3x /
1.8x because fixed costs dominate. Read cost off the 1080p ladder: **k=1 0.126
s/frame, k=2 0.319 (2.54x), k=3 0.640 (5.09x)** — sub-quadratic, so the k² worry
is overstated, but the corpus cannot inform the choice.

---

## 3. Every encoder flag, and why it is there

`DETERMINISTIC_X264_ARGS` in `an/adapters/cutout/render.py`, plus three literals
`_ffmpeg_mux` spells inline. **None of these is a default someone liked** — each
is a named constant with a recorded reason.

| flag | why | note |
|---|---|---|
| `-threads 1` | x264's frame-threading is nondeterministic; one thread is what makes an encode reproducible | |
| `-crf 23` | libx264's compiled-in default, **pinned so a build cannot change it silently** | passing it changes nothing today — which is why "an SSIM test fails when CRF is removed" is unsatisfiable |
| `-preset medium` | same: the compiled-in default, pinned | |
| `-colorspace bt709` | **changes the encoded planes.** Untagged, `an` converted with BT.601 all along; forcing `scale=out_color_matrix=bt601` reproduces the old output byte-for-byte | the one colour flag that is not a no-op |
| `-color_primaries` / `-color_trc bt709` | **do not reach the bitstream** on their own — ffprobe reports `unknown` for both | kept so the ffmpeg-level intent is explicit |
| `-color_range tv` | a **no-op today** (limited range is already the yuv420p default), pinned so a differently-defaulting build cannot change the output silently | |
| `-x264-params colorprim=…:transfer=…:colormatrix=…` | **this** is what lands all three in the VUI, and it leaves the decoded stream identical | a half-tagged file is worse than an untagged one: the player stops guessing the matrix but still guesses the primaries |
| `-pix_fmt yuv420p` (literal in `_ffmpeg_mux`) | **the first-order quality lever, and the default is a product constraint, not an encoder-tuning one.** High 4:4:4 Predictive is refused by many hardware decoders, browsers and platforms — flipping the default would hand a design partner a file they cannot play | 4:4:4 is the opt-in |
| `-c:v libx264` (literal) | | |
| `-movflags +faststart` (literal) | moov atom first, so a browser can start playing before the file finishes downloading | **lost on the concat path** — see §1 |

Why the colour tags matter at all: untagged, the *player* picks its matrix by a
height heuristic (BT.601 below ~576 lines). Every shipped `an` example is 320x240
to 640x360, so encode and playback agree **by luck**; at 1080p the same code would
encode BT.601 and be displayed BT.709 — a silent, resolution-dependent colour error.

### There is a third, undeclared x264 site

`an/characters/record.py` hand-builds `libx264 / yuv420p / -crf <param> /
+faststart` and does **not** use `DETERMINISTIC_X264_ARGS`. Any "one mux call, no
literals" refactor that only touches `adapters/cutout/render.py` leaves that
divergent copy behind. `an/bench/imageio.py::lossless_encode_command` is a
fourth site, but a deliberate one — it derives from the tuple at *import* time
precisely so the lossless reference cannot be moved by a lever (see §4).

---

## 4. Two bench levers are pinned to the exact shape of this code

The measurement instrument reaches this pipeline **from the outside**, through
seams the product code has by accident of style. Break the style, disarm the
instrument — and it disarms *quietly* in one case.

- **`high_crf` needs `_ffmpeg_mux` to keep reading `DETERMINISTIC_X264_ARGS` as a
  module global at call time.** The lever rebinds the module attribute. Hoisting
  the tuple into a default argument (`def _ffmpeg_mux(..., x264=DETERMINISTIC_X264_ARGS)`)
  binds it at *def* time and the lever silently stops reaching the encode — which
  reads exactly like an instrument that cannot see a CRF change.
- **`disabled_aa` string-matches the literal `antialias: true`** in `runtime.js`
  and refuses unless it finds **exactly one**. Reformatting the PixiJS options
  object breaks it *loudly*, by design — but you will hit it, so expect it.

When adding options to that object, add them **beside** `antialias: true` without
reflowing the line, and keep the count at one.

---

## 5. There is no shot cache — `mall["shots"]` is write-only

`CLAUDE.md` says "Content-hash caching; invalidation by deletion … `shot.id` for
per-shot mp4s". The store exists (`an/stores/__init__.py` →
`<project>/artifacts/shots`) and `an/render.py::_render_one` writes every shot
into it, and `an/iterate.py` deletes from it to invalidate. **Nothing reads it.**
`_render_one` renders unconditionally; there is no cache-hit branch anywhere on
the render path.

Two consequences, and neither is theoretical:

- **There is no cache key to design.** A proposal to "add the supersample factor
  to the shot cache key" (epic #9's struck item (e)) is a false premise — there is
  no key, because there is no lookup.
- **`artifacts/shots` is the bench's stale-render landmine.** `an/bench/capture.py`
  copies the fixture with `IGNORED_ON_COPY = (".an", "output", ".anima")`, which
  keeps `artifacts/` on purpose (it is the audio cache, whose warm/cold state is
  *recorded* rather than destroyed) — and therefore carries the previous render's
  shot mp4s into a module whose docstring is "Do not inherit a stale render". It
  is gitignored, so it is a per-developer landmine that does not reproduce on a
  clean checkout.

---

## 6. Where a per-render knob goes, and why it matters more than it looks

Simulated against a real committed ledger row, for a supersample factor:

| placement | consequence |
|---|---|
| `render_kwargs` | it becomes a `COMMON_ENV_PATHS` key → **all 96 metrics refused, no answer at all** |
| a field on the compiled scene JSON | `scene_contract_sha256` moves → **every scene incomparable** |
| **a `RenderContext` field, outside the scene document** | only `runtime_sha256` moves, which is deliberately *not* a comparability key → **30 render-side entries still compare** |

**Put product knobs on `RenderContext`.** It needs no `SCHEMA_VERSION` migration,
and the knob **must** also be written into per-shot provenance (the dict returned
by `CutoutRenderer.render`, beside `resolution` / `x264_args` / `chromium_args`) —
a row that does not record it cannot be read back later.

The same rule holds for `-pix_fmt`: it is a per-render product knob, not a scene
property.

---

## 7. `an preview` is not the render path

`an/preview.py` reuses the runtime in a live-reloading page. It **needs the
`#stage` canvas composited** — anything the render path does to stop Chromium
compositing a canvas nobody is looking at must be scoped to the render path only.
The two share `runtime.js`, so the scoping has to be explicit in the runtime, not
implied by which Python module called it.

---

## 8. Cost and what is still unmeasured

Costs above are per frame at 1080p. Memory is the unpriced axis: renders fan out
one browser context per shot at a default cap of 4, and the backbuffer scales k²
per context — ~33 MB per context at k=2, ~75 MB at k=3. **Nobody has costed
`parallel x supersample`.**

Also still unmeasured, from `wave3_research.md` §7 — do not assume any of these:

- **Cross-arch pixel identity at a larger backbuffer.** The cross-arch verdict was
  measured at 1x with MSAA 4. It gates whether goldens rendered at k=2 can stay a
  CI gate.
- Whether the compositing win grows with k.
- `-f concat -c copy -movflags +faststart` on the pinned ffmpeg build. **If it
  forces a re-encode it would *create* the double encode epic #9 wrongly describes
  as already existing.**
- 4:4:4 playback compatibility, for the `-pix_fmt` knob's documentation.

## 9. Standing rules for anything in this file's scope

1. **Never write that a rendering behaviour is "verified in CI."** Say which lane:
   a developer machine, a labelled PR, or an on-demand run.
2. A change that moves a pixel needs the `run-browser-tests` label (§ header).
3. A default chosen by taste ships **opt-in**, with a committed A/B, and the flip
   is its own one-line PR.
4. A knob that affects output and is not recorded in provenance is a
   cache-poisoning vector, not merely an imprecision.
