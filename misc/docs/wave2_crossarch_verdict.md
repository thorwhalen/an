# Wave 2 — the cross-architecture verdict

**Status: settled, for the pinned build.** This closes open question 1 of
`misc/docs/wave2_research.md` §7, which was the wave's gating unknown and its
declared first task.

**Answer: the pixels are identical across CPU architecture and operating
system — and so are the PNG file bytes.** Both render paths, all four machines,
132 frames per machine, zero differing pixels, zero differing bytes.

**The encode is not** — see "The encode side" below, added after an#34 pinned
the x264 flags and made the question answerable. Frames and mp4 have different
answers, which is exactly why the ledger must know which family a metric is in.

Where this was measured, stated per the standing rule from an#22: a **local
capture on the author's Mac** plus a **label-triggered run** of
`.github/workflows/crossarch-capture.yml` (run `32495142808`, PR #32). Nothing
here is "verified in CI" in the sense of running on every push, and nothing here
should be described that way.

---

## What was measured

`misc/bench/crossarch.py capture` renders each fixture through `an`'s real
render path into a throwaway copy, and digests **`sha256` of the decoded RGBA
array** of every frame. Never the file bytes: Chromium 1187 → 1223 changes
144/144 PNG files and zero pixels, so a byte criterion goes red on the first
Playwright bump for a reason unrelated to animation quality. (The file bytes
matching here as well is a bonus observation, not the criterion.)

| machine | platform | ISA | Chromium | SwiftShader backend | MSAA | ffmpeg | python |
|---|---|---|---|---|---|---|---|
| local | macOS 15.7.4 | arm64 | 140.0.7339.16 | **LLVM 10.0.0** | 4 | 8.1 | 3.12.12 |
| `macos-latest` | macOS 26.5.2 | arm64 | 140.0.7339.16 | LLVM 10.0.0 | 4 | 8.1.2 | 3.12.14 |
| `ubuntu-latest` | Linux 6.17 / glibc 2.39 | **x86_64** | 140.0.7339.16 | **Subzero** | 4 | 6.1.1 | 3.12.14 |
| `ubuntu-24.04-arm` | Linux 6.17 / glibc 2.39 | aarch64 | 140.0.7339.16 | LLVM 10.0.0 | 4 | 6.1.1 | 3.12.14 |

Launch argv, recorded verbatim because the WebGL renderer string cannot witness
the rasteriser choice:

```
--no-sandbox --disable-gpu --enable-unsafe-swiftshader --force-color-profile=srgb
```

with `headless=True`, `playwright==1.55.0`, `auto_audio=False`, `parallel=1`.

**Fixtures — both paths, because they are not equally sensitive** (the
descriptor path is 12x more sensitive to a rasteriser flip):

| fixture | path exercised | size |
|---|---|---|
| `examples/single_character` | procedural rig (`rect`, `ellipse`, `eye`, `mouth`) | 320x240, 60 frames |
| `examples/promote_demo` | SVG sprites (`svg_sprite`, `rect`) | 480x360, 72 frames |

**Result: `IDENTICAL` on every pairing.** 0/72 and 0/60 frames differ in pixels;
0/72 and 0/60 differ in PNG bytes; max channel delta 0.

The strongest part of the result is the third row of the table: the x86-64
runner runs a **different SwiftShader JIT backend** (Subzero, not LLVM). The
hypothesis that motivated this experiment — Reactor JIT-specialising per ISA
and thereby moving pixels — is refuted at a stronger level than "same backend,
different CPU". macOS 15 vs 26, glibc, and three different ffmpeg builds also
varied without effect, the last trivially so since ffmpeg never touches a frame.

---

## What this settles

1. **The golden corpus can be a CI gate, not a local-only instrument.** §3's
   "CI runs the cheap half and the pixel gate skips with an environment message"
   was written as provisional pending this run; it is now unnecessary for these
   three platforms at the pinned build.

2. **The environment key in the golden filename should be the browser build,
   not the platform.** §3 proposed
   `<frame-key>-chromium140.0.7339.16-darwin-arm64.png`, following the
   Playwright/pytest-mpl convention. The platform and arch segments are
   measurably inert, and carrying them would force one committed copy per
   platform for no information. Key on the Chromium build alone —
   `<frame-key>-chromium140.0.7339.16.png` — which keeps the convention's real
   benefit: a Playwright bump becomes a **new path requiring a deliberate
   re-bless**, rather than a red test with no explanation.

3. **The ledger schema needs no cross-machine band column for the render-side
   metrics.** They are computed on the pre-encode PNG, and the PNG is now known
   to be machine-invariant under the pins. Write the render-side determinism
   assertion as **equality**; any future band there is a deliberate, argued
   regression.

---

## The encode side — measured after the pinning, and the answer is the opposite

**Settled by an#34**, once `-threads 1 -crf 23 -preset medium` + BT.709 were
pinned. It could not be answered before: comparing an unpinned encode across
three ffmpeg builds would have measured the absence of the pins.

The frames stayed identical on every pairing throughout. Only the mp4 moved.

| pair | ISA | x264 build | decoded mp4 | luma differing / mean \|d\| / max |
|---|---|---|---|---|
| local vs `macos-latest` | same (arm64) | same (`165 r3222`) | **identical** | — |
| `ubuntu-latest` vs `ubuntu-24.04-arm` | **different** | **same** (`164 r3108`) | differs | 0.36% / 0.007 / 15 · 2.66% / 0.034 / 19 |
| local vs `ubuntu-24.04-arm` | same (arm64) | **different** (`165` vs `164`) | differs | 15.87% / 0.70 / 36 · 99.24% / 3.94 / 19 |
| local vs `ubuntu-latest` | different | different | differs | 15.88% / 0.70 / 36 · 99.14% / 3.92 / 17 |

(Two figures per cell: `single_character` · `promote_demo`. Chroma is reported
separately by the tool and is the **more** affected plane on the ISA axis —
4.7% and 38.3% of samples against luma's 0.36% and 2.66% — which is why the
tool splits the planes rather than pooling them.)

Read in order, the rows isolate each variable:

1. **Same ISA, same x264 build → byte-identical decoded stream**, across macOS
   15 vs 26 and ffmpeg 8.1 vs 8.1.2. (The *file* bytes still differ — same
   length, different digest — because the container records the Lavf version.
   This is the concrete demonstration that a file digest can never be the
   criterion.)
2. **ISA alone, at a fixed x264 build, is a real but small effect.** x264 ships
   hand-written SIMD per architecture, so the same source produces different
   rounding on SSE/AVX and NEON.
3. **The x264 build change dominates by two orders of magnitude** — and row 3
   isolates it at *fixed* ISA, so it is not confounded.

### So: do not band the encode side. Scope it.

A band wide enough to absorb an x264 build change would have to tolerate a mean
luma delta near 4 and a max of 36. Several of the ledger's own encode-side
signals are far smaller than that — `flat_field_deviation` moves 0.0003 → 0.0005
across crf18 → crf23 — so such a band would swallow the signal it exists to
protect. The honest design is therefore:

- **Encode-side metrics are machine-scoped.** `an bench --compare` must
  **refuse** to compare two rows whose x264 build or ISA differ, in the same way
  §6 already requires it to refuse rows with a different `scene_contract_sha256`:
  the number is uninterpretable, not good or bad.
- **The provenance row must carry the x264 SEI verbatim** (`core NNN rNNNN
  <sha>`, which also encodes the thread count) **and the ISA.** The research
  proposed stamping the SEI as a nice-to-have; this measurement makes it
  load-bearing — it is the field that decides whether two rows may be compared
  at all.
- **Render-side and encode-side metrics therefore have different comparison
  rules**, and the ledger schema has to say which family a metric belongs to.
  That distinction was already required for a different reason (§1: the two
  families are blind to each other's mutations); this is a second, independent
  reason it cannot be dropped.

## What this does NOT settle — and must not be read as settling

- **The mp4 file bytes are never comparable at all**, and no future run should
  try. The x264 SEI carries the encoder build and thread count and nothing
  strips it (`-x264-params sei=0` is silently ignored) — and even at a fixed
  build the container records the muxer version, as row 1 above shows.
- **Other Chromium builds.** This is one build, 140.0.7339.16, pinned. Nothing
  here says 1223 would also be cross-arch identical — only that 1187 → 1223 was
  pixel-identical *on one machine*.
- **Text.** `runtime.js` instantiates no `PIXI.Text` and no committed SVG
  contains a `<text>` element, so fontconfig — the universally-reported top
  cause of visual-test flakiness — is genuinely not an input yet. Wave 8 adds
  it. This verdict expires for any scene containing text.
- **A production shot.** Both fixtures are small. Viseme swaps, camera tweens
  and a dense SVG texture population were not exercised.

---

## The finding that nearly invalidated the experiment

**The first run of this experiment produced a confident, wrong answer, and
nothing flagged it.** It is recorded here because the mechanism is exactly the
class of failure Wave 2 exists to catch.

All of `examples/*/assets/` is gitignored. `examples/promote_demo` commits only
the hand-drawn `raw_maya.svg`; the promoted character its scene references is a
build product regenerated by `build.py`. The capture did not run that step, so
on a clean CI checkout the character was simply **absent** — and the cutout
compiler **falls back to the procedural rig with zero warnings** (verified: no
warning raised, `svg_sprite` merely missing from the compiled scene).

So three runners rendered a *different character*, agreed with each other
perfectly, and that agreement read as "the descriptor path is deterministic
across architectures" while the descriptor path had never run. The local Mac
disagreed only because it had a stale local build product — which is the sole
reason the discrepancy surfaced at all. Had the author's machine also been
clean, the wrong answer would have been unanimous.

Two consequences, both now in force:

- A fixture declares `expect_visual_kinds`, checked against the scene JSON the
  browser actually loaded. A capture that does not exercise its declared render
  path fails loudly. Mutation-tested by removing the prepare step.
- A fixture may declare a `prepare` step that rebuilds gitignored build products
  from committed sources.

And two that belong to other issues:

- **The silent fallback is a bug** in its own right (the "loud discards"
  class): a missing character descriptor should be an error, not a different
  picture. Filed as an#33 and **fixed 2026-08-21**. The fix is not "make it an
  error" — the fallback has to stay, because it is the only reason an
  asset-less project renders at all, and `examples/single_character` reaches it
  deliberately. What changed is that it is no longer *indistinguishable*: the
  compiler warns, records one `asset_resolution` entry per drawable entity in
  the scene JSON the browser loads, and refuses outright under
  `strict_assets=True` — which this capture now passes. The ambiguity is real
  and is asserted as such: a missing descriptor and a deliberately-procedural
  character compile to the **same scene tree**, so no assertion over pixels,
  visual kinds or node paths can separate them. `expect_visual_kinds` stays as
  the independent second check, because it reads the staged artifact rather
  than trusting the compiler that produced it.
- **Wave 2's golden corpus cannot be built out of `examples/` as they stand.**
  §3's scene list assumes usable example projects; four of the five ship no
  assets at all. The corpus needs committed fixtures, or explicit prepare steps
  with a rendered-path assertion like this one. `single_character` is
  reproducible today only because it is purely procedural and has nothing to be
  missing.

---

## Reproducing it

```bash
# locally
python misc/bench/crossarch.py capture --out /tmp/crossarch/local

# on the runners (or add the `run-crossarch-capture` label to a PR)
gh workflow run crossarch-capture.yml
gh run download <run-id> -D /tmp/crossarch

python misc/bench/crossarch.py compare \
    /tmp/crossarch/local /tmp/crossarch/crossarch-ubuntu-latest
```

`compare` exits non-zero on `DIFFERS`, and reports differing-frame counts,
differing-pixel counts and max channel delta per scene — so a future
disagreement arrives with its magnitude attached rather than as a bare failure.
