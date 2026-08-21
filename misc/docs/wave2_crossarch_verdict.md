# Wave 2 — the cross-architecture verdict

**Status: settled, for the pinned build.** This closes open question 1 of
`misc/docs/wave2_research.md` §7, which was the wave's gating unknown and its
declared first task.

**Answer: the pixels are identical across CPU architecture and operating
system — and so are the PNG file bytes.** Both render paths, all four machines,
132 frames per machine, zero differing pixels, zero differing bytes.

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

## What this does NOT settle — and must not be read as settling

- **The encode side is untouched.** Every metric computed from the decoded mp4
  (`coded_luma_edge_error`, `chroma_edge_dCr`, `flat_field_deviation`,
  `encode_flicker_on_held_pixels`, `encode_ringing_excess`,
  `video_stream_bytes`) depends on the ffmpeg/x264 build, which **differed
  across these runners** (6.1.1 vs 8.1 vs 8.1.2) and cannot be pinned the way
  the browser can. This experiment deliberately did not compare them, because
  the x264 flags are not pinned yet: comparing an unpinned encode across three
  builds would have measured the absence of the pins, not the presence of a
  band. **Sequenced deliberately:** land the `-threads 1 -crf 23 -preset medium`
  + BT.709 pinning, then re-run this capture with mp4 decode included. Until
  then, treat the encode-side band as **unmeasured**, not as zero.
- **The mp4 itself is never cross-machine comparable** and no future run should
  try. Its x264 SEI carries the encoder build and thread count, and nothing
  strips it (`-x264-params sei=0` is silently ignored).
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
  picture. Filed separately.
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
