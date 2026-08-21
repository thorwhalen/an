# Changelog

AI-maintained record of substantive changes to the an codebase. One entry per
day per chunk of work; keep entries terse.

- **The vision verifier can no longer launder a failure into a pass, and its one paid call is cassetted** (an#39). Two defects, and the second made the first invisible. (1) **Every failure path reported `passed=True`** — `VerificationReport.add` flips `passed` only on `"error"` and every handler used `info`, so a dead model id, a 500, a refusal and an unparseable reply all came back byte-identical to a clean bill of health. (2) **`_parse_issues` collapsed "no verdict" into "empty verdict"**, so a refusal and a literal `{"issues": []}` produced the same Finding. It now returns `None` vs `[]`, and "configured and broken" reports at `FAILURE_SEVERITY` (`warning`) rather than at the *not-configured* severity. The paid call moved behind an injectable seam, `judge_frames(frames: Sequence[bytes]) -> str` — **bytes** because the real frames live in a `TemporaryDirectory` so a key over paths misses 100% of the time, and **raw text** so `_parse_issues` stays outside the recording and parser fixes are testable against it for free. `judge_key` derives from the seam's **signature** with `apply_defaults()`, not from an allowlist: an allowlist sets the default to *exclude*, so a parameter added later collides with the base key and serves a stale reply forever (a false miss is red CI; a false hit is silent). `CassetteMiss` derives from **`BaseException`** because `except Exception` appears twice on the way out of a `verify()` call — here and in `orchestrate`'s post-render loop, which guards every verifier and must stay broad. **One spend switch** (`AN_LIVE_API_TESTS`); replay is the default and a miss is an ERROR, never a fallthrough. Frames come from a **frozen** `tests/fixtures/vision_frames/`, deliberately NOT the golden corpus, which has a re-bless lifecycle that would redden a free hermetic node on an unrelated renderer PR. Also: a `vision = ["anthropic<1"]` extra (it was lazy-imported and declared nowhere, so `pip install an` gave you a verifier that could only ever skip), and `tests/test_licence_perimeter.py`, which reads installed `dist-info` rather than the PyPI licence field — **and immediately found that `argh` is LGPL-3.0** (an#45; recorded with its reasoning, and the copyleft set is pinned at exactly one so a second cannot arrive unnoticed). 12 mutants, all caught.
- **`an bench` writes a metrics ledger** (an#36) — Wave 2's instrument. Renders the fixed corpus into a throwaway copy and emits one row to `misc/bench/ledger/<date>-<sha>[-dirty].json`, in three blocks that must never be mixed: `metrics` (each labelled render-side or encode-side), `tripwires` (`golden_identity`, counting **zero**), `provenance` (never gated). The metric set is the research's corrected one and is **not** the epic's draft — all twelve originally-proposed metrics were refuted, and `mean_adjacent_frame_ssim` is out because it moves the *wrong way*. Three schema fields exist before the first row because retrofitting them invalidates every prior entry: a per-metric **per-mutation** direction (two-sided where the optimum is interior), a `gated`/`unavailable`/`no_change` distinction where two of the three are null and only one of those is a comparison failure, and the environment tuple split by comparison scope (render-side `any_machine`, encode-side `machine` carrying the x264 SEI verbatim). New `an/bench/` — pure-numpy metrics that run **unmarked in the default CI leg**, which is the only part of this wave main CI can see. Three corrections to the plan, each found by running it: the pinned decode is asserted by a hard equality against a lossless encode (unpinned reads 5.33 code values of luma residual and calls it encoder damage; `-pix_fmt gray` *silently ignores* the range/matrix options, so §1.4's literal pseudocode applies a fix that does nothing), and two metrics turned out to be one measurement under two names (`chroma_edge_dY` IS `coded_luma_edge_error`; `edge_band_mae` over the edge mask is too — the latter is now `ring_band_mae` over the ring band, where it is a genuine rival to `encode_ringing_excess`). **And a fourth correction, which CI found rather than a local run**: the first design referenced every encode-side metric to an explicit RGB->YUV conversion of the source PNGs, and asserted as a hard equality that it reproduces what libx264 received. It does — on ffmpeg 8.1, exactly — and does **not** on the Linux runner's older build (mean 0.63, max 5, i.e. 42% of `coded_luma_edge_error`'s whole crf23 value). The fix is not a tolerance: `-qp 0` is lossless, so its decoded luma **is** the encoder's input on any build, and referencing to it removes the assumption instead of widening it. The conversion is still measured and its distance recorded as `png_to_encoder_input_luma` — that number is the build dependence, and it belongs in provenance rather than inside a gate. Two metrics still reference the PNG conversion deliberately and each row says which: the chroma metric (its subject IS the subsampling that happens during that conversion) and `encode_ringing_excess` (its cancellation exists only when both legs share that reference). `chroma_edge_dY` came back for the same reason — it is `coded_luma_edge_error`'s expression on a different reference, so the two coincide exactly when the conversion does. Also: `misc/bench/crossarch.py` now imports its fixtures from `an.bench.corpus` rather than keeping a second copy — which repaired a real break, since an#33's `strict_assets=True` made its `single_character` fixture refuse to render. 27 mutants of the guards, all caught.

- **The determinism perimeter is watched, not assumed** (an#37). The two things the epic named were already deterministic before the issue was written (the blink phase is a djb2 of `(entity id, scene time)`; the palette hash is `sum(ord(c)) % 5`, so `PYTHONHASHSEED` is irrelevant), and the rasteriser/encoder pins landed unconditionally in an#31/an#34. What was left is the part nobody can pin: machinery that is deterministic **by accident**. The vendored PixiJS carries 4 `Math.random`, 2 `Date.now`, 6 `performance.now` and 3 `rAF` calls and `NoiseFilter` seeds itself from `Math.random()`; all dormant only because the app is built with `autoStart:false` + explicit `app.render()` and nothing attaches a filter — neither fact written anywhere that would go red. New `runtime.js` probe `anDeterminismReport` **observes** and new `an/determinism.py` **judges**, so the rule is a pure function of a dict, testable with no browser. It runs on every render and lands in `RenderResult.provenance` alongside the launch argv and the x264 argv. **Enforcement is ON by default** — a deliberate deviation from the issue's `AN_DETERMINISTIC=1` framing, on an#31's own reasoning: an assertion nobody runs is worse than none, because the perimeter reads as guarded. `AN_DETERMINISTIC=0` is the escape hatch and still records the breach. Also: both unpinned `Object.keys` iterations in `runtime.js` are now `.sort()`ed with the contract written beside them (guarded by a sweep, so a *new* one is caught too); the blink phase per entity is stamped, turning "someone renamed a corpus character and every metric moved" into a visible diff; and the browser-cache probe in `check_requirements.py` is no longer macOS-only — on Linux it told you to install a Chromium that was already there. 13 mutants of the guard, all caught.
- **A stand-in asset is no longer indistinguishable from the real one** (an#33). A character whose descriptor is missing from the store, or an environment ref that names neither a store entry nor a built-in preset, was substituted with the placeholder rig / default backdrop and rendered happily with **zero** diagnostics — the failure that nearly invalidated the cross-architecture experiment, where three CI runners agreed perfectly about a picture that was not the picture. The fallback *stays* (it is the only reason an asset-less project renders, and `examples/single_character` reaches it deliberately); what changed is that it is audible in three places: a `CutoutCompileWarning` naming the entity, the store and what was drawn instead; a new `asset_resolution` block in the scene JSON the browser actually loads, carrying one record per drawable entity; and `strict_assets=True` (`compile_shot` / `render` / `an render --strict-assets` / `RenderContext`), which refuses outright and which `misc/bench/crossarch.py` now passes. The ambiguity is asserted as the ambiguity it is: a missing descriptor and a deliberately-procedural character compile to the **same scene tree**, so the test proves no pixel-level assertion could ever separate them. Seven mutants of the guard, all caught.

- **The x264 encode and the BT.709 tags are pinned** (an#34), the Wave 2 prerequisite that had to land before the first ledger row because it re-baselines every mp4. Two measured corrections to the research: `-colorspace bt709` is **not a tag but a conversion** — it sets the matrix of ffmpeg's RGB->YUV step, so the encoded planes change (proven: forcing `scale=out_color_matrix=bt601` reproduces the untagged output byte-for-byte, i.e. `an` has been encoding BT.601 while a >=576-line player decodes 709); and ffmpeg's `-color_primaries`/`-color_trc` **never reach the bitstream** — `-x264-params` is what lands all three, and a half-tagged file is worse than an untagged one. `-threads 1 -crf 23 -preset medium -color_range tv` are verified no-ops today, pinned against a runner that differs. **And the encode half of the cross-architecture question is now answered, opposite to the render half**: same ISA + same x264 build is byte-identical, a different ISA moves the decoded stream slightly (luma <=2.66% of samples, mean |d| <=0.034) and a different x264 build moves it by two orders of magnitude (up to 99.2%, mean |d| 3.94, max 36). So encode-side ledger metrics are **machine-scoped, not banded** — a band that wide would swallow `flat_field_deviation`'s whole crf18->23 signal — and `--compare` must refuse rows whose x264 build or ISA differ.

- **The cross-architecture verdict is in: the pixels are identical** (`misc/docs/wave2_crossarch_verdict.md`, an#31) — Wave 2's gating unknown, closed. Both render paths, four machines (local arm64 macOS, `macos-latest`, `ubuntu-latest` x86-64, `ubuntu-24.04-arm`), 132 frames each: **zero differing pixels, zero differing PNG bytes**. Stronger than the question asked — the x86-64 runner uses a *different SwiftShader JIT backend* (Subzero, not LLVM), so the JIT-specialisation hypothesis is refuted at the backend level. So the golden corpus can be a **CI gate**, its filenames key on the **Chromium build alone** (the platform segments are measurably inert), and the render-side ledger needs **no cross-machine band column**. The *encode* side stays open on purpose: ffmpeg differed 6.1.1/8.1/8.1.2 across those runners and the x264 flags are not pinned yet, so comparing it would have measured the absence of the pins. **And the first run of the experiment got it wrong**: every `examples/*/assets/` is gitignored, and a missing character descriptor makes the compiler fall back to the procedural rig with zero warnings, so three runners agreed perfectly on a picture that was not the picture. Fixtures now declare a `prepare` step and the render path they must exercise; the compiler bug is an#33.

- **The rasteriser is pinned, unconditionally** (an#31): `--disable-gpu`, `--enable-unsafe-swiftshader`, `--force-color-profile=srgb` alongside the pre-existing `--no-sandbox`, plus an explicit `headless=True` and `playwright==1.55.0`. Not env-gated — a render whose rasteriser depends on `AN_DETERMINISTIC` is non-reproducible by default, which is the property Wave 2 exists to remove. Verified here rather than inherited from the research: a 0-pixel, 0-PNG-byte no-op across both fixtures (132 frames), with a same-machine repeat capture as the control. `--use-angle=swiftshader` and `--disable-frame-rate-limit` stay OUT and their absence is now a test. New `misc/bench/crossarch.py` captures `sha256(decoded RGBA)` per frame — never file bytes — and `.github/workflows/crossarch-capture.yml` runs it on x86-64 Linux, arm64 Linux and arm64 macOS so a difference can be attributed to ISA rather than merely observed. Also corrects `architecture_as_built.md`: the per-shot mp4 cache it documents has no read path.

- **The rendering lane is now opt-in per PR, via the `run-browser-tests` label**,
  and the decision behind it is written down as
  `misc/docs/adr_ci_verification_perimeter.md` — an ADR answering the question
  an#22 actually asked, which was "which failures is this repo allowed to not
  notice?". Adding the label is explicitly open to agents
  (`gh pr edit <N> --add-label run-browser-tests`), and both `CLAUDE.md` and the
  `an-dev` skill say so, with the trigger list: anything touching the runtime,
  the cutout compiler/serializer, the render path, the vendored engine, the
  ffmpeg flags, or the rig. An unlabelled PR never starts the job, so the
  default cost stays zero. First Linux dispatch, cold cache: Chromium 24 s,
  ffmpeg 10 s, the lane itself 45 s, whole job 103 s — and it passed first try.
- **MIT-CMU is ruled inside the licence perimeter** (Pillow 11.3.0, read at its
  own `dist-info/licenses/LICENSE`): MIT's grant plus BSD-3's no-endorsement
  clause, no copyleft, no field-of-use limit. The perimeter is now stated as
  "the four named licences **plus explicit dated rulings**", because read
  literally it disqualified permissive licences nobody meant to exclude. The
  ledger and the procedure for adding a row are Rule 6 of the
  `an-dev-licensing` skill; a row without an obligations column is a chip with
  extra steps.

- **Wave 2's research record (`misc/docs/wave2_research.md`).** Twenty-one agents
  across five surveys and four adversarial passes, plus a separate pass on §4.
  **All twelve originally-proposed metrics were refuted** and replaced with
  corrected forms. It contradicts epic #9 in six places, each measured:
  `mean adjacent-frame SSIM` moves the WRONG way under degradation (0.958 at
  crf18 → 0.977 at crf51 — a crushed video is smoother); there is no blink phase
  to pin (already a pure function of `(entity id, t)`, and `PYTHONHASHSEED` is
  irrelevant because palette hashing is `sum(ord(c)) % 5`); "first and mid frame"
  is often a byte-identical duplicate (blinks occupy 3.5% of frames); the
  ">=3 metrics move" criterion is unsatisfiable as written because the
  pre-encode and post-encode families are disjoint; `architecture_as_built.md`
  documents a shot-cache read path that does not exist; and four bench-prototype
  conclusions were invalidated by Wave 1's vendoring (it measured canvas-2D; `an`
  renders WebGL).
- It also **un-hedges** the epic where the epic was too cautious: frames are
  already byte-identical same-machine (144 frames x 3 renders; `parallel=4`
  matches serial), so the determinism test is an equality assertion, not a band.
  But goldens must key on `sha256(decoded pixels)`, never file bytes — Chromium
  1187 → 1223 changes 144/144 PNG files and **zero** pixels.

- **Browser tests were not being skipped in CI — they were not being collected
  (#22).** Eleven modules opened with a module-level
  `pytest.importorskip("playwright.sync_api")`, which aborts the module import
  rather than skipping a test. Measured: 472 tests collected with Playwright
  installed, 438 without. **13 of the 34 casualties needed no browser at all** —
  every `an.verify.media` SSIM test (the primitives Wave 2's ledger is built
  on), two `skip_render=True` orchestrator tests, six JSON-parser tests, and a
  paid Anthropic call gated on nothing but "is a key set", a `live_api`
  violation that was invisible rather than absent. A test that is not collected
  appears in neither the pass count nor the skip count, so nothing reported the
  hole. A *separate* `importorskip("nw")` in `test_genre.py` was hiding the
  guard that `import an` does not drag in `nw` — found by the new guard, not by
  the sweep. CI's own numbers: 423 passed on `main`, 460 on the branch, which
  reconciles exactly as 28 new guard tests + 13 collateral + 1 nw + 2 new
  conftest doctests, minus the guard-file growth after the review.
- **The gate is a marker applied after collection**, so collection is invariant:
  `browser` and `ffmpeg` in `tests/conftest.py`, one cached Chromium probe
  instead of eleven browser launches at import time (collection 6.0s → 1.2s),
  and a run-summary line — `browser tests: 24 collected, 0 ran, 24 did not: …` —
  so a green run is never silent about having checked zero pixels. That count is
  an **observation**: `total - skipped` is a collection-time prediction and it
  printed "24 ran" for `-m`, `-k` and `--collect-only` runs in which nothing
  ran, including a step of the workflow this change adds. **An explicit
  `AN_BROWSER_TESTS=1` that cannot be honoured is an ERROR, not a skip** — but
  scoped to a lane the invocation actually selected, because the `cutout` extra
  ships `ffmpeg-python`, not the ffmpeg binary, so the unscoped form killed
  every run on a machine that followed this repo's own install hint.
  `tests/test_browser_gate.py` holds all of it: 28 guards, mutation-tested
  20/20, including the four routes an adversarial review used to reintroduce the
  bug past an earlier draft. **It does not make the bug impossible and no longer
  claims to** — what holds the line is a guard that shadows every optional
  import, strips the external binaries from `PATH`, and compares pytest's own
  node-id sets. The rendering lane lives in
  `.github/workflows/browser-tests.yml`, dispatched on demand.
- **The Windows CI leg can fail the build again — and now blocks the release.**
  Precisely: `continue-on-error: true` never made GitHub misreport the job. On
  every failing run the job conclusion, the step conclusion and the check-run
  row all read `failure`; what the flag changed was the **roll-up**, so the
  workflow *run* concluded `success` and nothing blocked the merge. The signal
  was non-blocking, not hidden — worse in practice, because a reviewer reads the
  aggregate. That is how #21's path-separator bug and an unpinned `read_text()`
  encoding reached `main` (5 of the 30 runs before this change record a failing
  Windows job under a green run). `publish` also gained a `needs` edge on the
  Windows job: without it, blocking Windows reddens the tick but a Windows-only
  failure on `main` still uploads to PyPI. Windows was green on `main` when the
  flag was removed. Both deviations from the wads template are commented in
  place; upstream knob filed as i2mint/wads#66.
- **Pillow is a declared `cutout` dependency.** `an.verify.media.ssim` and
  `an/characters/silhouette.py` import it, and the only two tests in this repo
  that assert on *pixels* were doing `importorskip("PIL.Image")` in their
  bodies — the rendering-verification version of the same silent hole.

- **Truthed up the docs (#16), and the audit found five false claims, not one.**
  Both `CLAUDE.md` and `architecture_as_built.md` said the runtime *ignores*
  `loop_mode` — it has honoured it since #5, and the real gap is the inverse: no
  compiler code ever emits a non-default value, so looping is reachable only by
  hand-writing scene JSON. The architecture doc still described live-API tests as
  "skip-if-key-missing", which is exactly the gate replaced in #4 *because* a key
  being present is not consent to spend. Both docs still listed the CDN
  dependency closed in #12. And two separate places pinned "seven research
  reports" / "three skills", counts that were right when written.
- **Added `tests/test_docs_are_true.py`**, because a gap list that outlives its
  gaps is worse than none — a reader trusts it and works around a problem that no
  longer exists. It guards the two failure modes that actually recurred: a count
  attached to a directory that grows, and a gap line that survived its gap. Both
  of its first drafts were too broad and produced false positives; the narrowed
  version then found a *second* "seven research reports" I had missed.
- Recorded the gaps this session created or exposed: no browser test has ever run
  in CI (#22), the Windows leg is `continue-on-error` so a green tick can hide a
  failure, and the unrendered second scene evaluator that Wave 5 must resolve
  before swap channels land.
### #14 rework after adversarial review

Four blockers, all reproduced, and the licence one could not have been fixed
forward once shipped:

- **The table was incomplete at the pinned major.** Upstream publishes 31 styles
  at 9.x; the table had 27. Missing: `dylan` and `toon-head` — both **CC BY 4.0**,
  i.e. attribution-bearing — plus `glass` and `rings` (CC0). Verified from each
  style's own 9.x LICENSE file.
- **The completeness test was circular.** It compared `DICEBEAR_STYLES` against
  `DICEBEAR_STYLE_LICENSES` — two hand-maintained lists, checked against each
  other — so a style missing from BOTH was invisible, which is exactly what
  happened. It now checks against a committed snapshot of upstream's own package
  listing, with an opt-in `live` test that catches the snapshot itself drifting.
- **The gate was at the wrong layer.** `an character new` had no
  `--acknowledge-attribution` flag, so every CC BY style became unreachable from
  the CLI with a raw traceback — while its own `--style` help still recommended
  one of them.
- **`an credits` told the users most at risk that they owe nothing.** Every
  character created before this feature used the CC BY default and has no
  `source` field, and was reported as "no third-party assets recorded" — an
  affirmative false compliance statement, when `metadata.dicebear_style` was
  sitting in the same file. Now reconstructed.
- **The producer had no test at all.** Deleting `source=source` from
  `new_character` left all 446 tests green: every credits test hand-built a
  descriptor, so the vocabulary was asserted and the path was not.

Also: `conftest` now distinguishes `live` (reaches the network, costs nothing)
from `live_api` (spends money). Collapsing them would have been convenient and
would have quietly eroded what `live_api` promises.
- **Third-party art carries its rights now (#14).** New `an/ir/assets.py`
  `AssetSource`, with the rights field names pinned literal-for-literal to
  `illustration.ImageResult` so an adapter is a dict copy rather than a rename
  table — a rename table is where a field quietly stops being carried, which is
  exactly what `illustration`'s own persistence layer does today
  (illustration#14). `cost_usd=None` means unknown, never free.
- **The default avatar style moved to a CC0 one.** It was `adventurer`, CC BY
  4.0 — so `an character new <name>` with no flags produced art whose licence
  obliged the *user* to credit an artist they had never heard of, recorded
  nowhere and displayed nowhere. `lorelei` is not merely "a CC0 one": it is the
  only CC0 human style shaped like a bust, which is what the rig needs, and it is
  by the same artist so the demo art barely shifts. All 27 styles stay
  requestable; a CC BY one now needs `acknowledge_attribution=True`, and the
  refusal shows the exact text you would owe.
- **`an credits <project>`** walks the recorded sources and prints what must be
  displayed. A licence recorded and never displayed is not compliance. It keeps
  three lists, never two: owed, UNVERIFIED, and clear — folding "unknown" into
  either one is how an obligation goes missing.
- `an/characters/licenses.py` is the per-style table, verified against each
  style's own licence file. The DiceBear *software* licence (MIT) is a separate
  fact from each *style* licence; DiceBear splits them itself.
- Corrected a count I had pinned in `wave1_verification.md` — "11 CC0, 12 CC BY"
  did not match the per-style rows beneath it. The rows are the fact.

### Second review round (#24)

The consolidating verdict returned **merge-after-fixes** with both blockers already
addressed, and surfaced one finding no individual lens had:

- **`an/ir/sync.py` — the `scene.md` authoring surface, which CLAUDE.md calls the
  SSOT — still documented, accepted and round-tripped `play`.** A `play` written
  there survived the IR, survived `an validate`, and died at compile, having
  looked valid the entire way. Refused at parse time now: the earliest layer that
  can see the mistake reports it.
- `charlie/torso/left_arm` — the project's own canonical targeting example — names
  a node no rig builds (the rigs are flat; arms are siblings of the torso). It was
  harmless while an unknown target was skipped and is a trap now that it raises.
- `camera.move=""` was ignored while `move="  "` raised: falsiness was tested
  before normalisation, so the same input had two behaviours.
- `_capture_frames` labelled every browser-side failure "the JS runtime failed",
  including a Playwright timeout or a crashed target. It now says what it can
  actually know and names the exception type.

One of my own "corrections" was itself wrong and is reverted in spirit: the
verdict counted, and there really are exactly nine direct `RuntimeError`
subclasses, so the doc claim was true rather than stale. The rewritten wording
generalises it to cover `CutoutCompileError` being a `ValueError`.

### Rework after adversarial review (#24)

The first version of #15 put each guard where the SYMPTOM lived rather than where
the author's mistake lived, and three independent reviewers found what that costs:

- **`an preview` froze permanently and silently.** `tick()` called `anSetTime`
  unguarded and re-armed rAF on the next statement, so the first throw killed the
  loop for the life of the page with the status bar still reading "ok". Guarded;
  a reload now restarts the loop it stopped.
- **The narration guard was unreachable from `an render`** for exactly the scenes
  it names — the audio pipeline was gated on `_has_any_dialogue`, so a
  narration-only project skipped it and rendered silent.
- **Widening `pose.py`'s allow-list made things worse.** `TransformParams` has no
  `alpha` / `viseme`, so `apply_pose` accepted them and died with a raw dataclass
  `TypeError` instead of its own informative `KeyError`. The list is now derived
  from what it can actually apply, and the gap to the runtime is DECLARED.
- **The off-screen-speaker fix was itself a new silence.** It could not tell a
  narrator from a typo. It now checks the scene's real node paths and WARNS,
  naming the actual mouths — which also covers a case entity-membership missed:
  an on-screen character whose rig has no head.
- **The environment perimeter was drawn in the wrong place** — it hard-failed on
  ordinary store metadata (`name`, `description`) while an unknown environment
  ref stayed silent. Now warns.
- **`an validate` learns all of it.** This is where the checks belonged: every
  scene the pipeline refuses used to report `passed`, so the author paid for TTS
  or a Chromium launch to discover a pure-IR mistake.

Three of the tests were vacuous and one guard had none at all:

- the off-screen-speaker test never reached the branch it named (a missing
  `viseme_track` short-circuits two guards earlier) — and it was the test
  defending the change's headline claim;
- the schema-advertisement test keyed on the literal `e.g.`, which the rewritten
  comment removed — fixing the thing a test guards disarmed the test;
- the "applies every known property" test asserted only that nothing threw, so a
  mutant writing to the wrong field passed;
- deleting the `CutoutRenderError` wrapper left all 408 tests green.

Also: errors name the shot, user-facing messages link to a real issue instead of
an internal wave number, and `iterate`'s prompt plus the `an` skill no longer
advertise `play`, `prop` or `narration` as usable.

**BEHAVIOUR CHANGE:** `apply_pose` raises on an unknown target where it used to
skip. That reverses a pre-existing test (`test_apply_pose_skips_unknown_target`),
deliberately — the JS runtime made the same choice for the same stated reason and
has been changed too. Two evaluators should agree, and silence is the wrong thing
for them to agree on.

- **Seven silent discards now raise typed errors that name the wave implementing
  them (#15).** Each had the same shape: the IR declares a capability, the
  compiler or runtime quietly declines it, and the author gets a render missing
  something with no diagnostic — the worst failure mode this package has, because
  it surfaces days later as "the animation looks wrong". An unrecognised
  `camera.move`; `PlayAction` (which used to fabricate an empty, channel-less
  clip, so `play` looked wired while animating nothing); `Shot.narration`;
  `prop` entities; environment-store keys the renderer never reads; the
  runtime's unknown-property branch; and its unknown-*target* sibling.
- `hold` is exempt — it early-returned through the same branch as an unknown
  move and is a correct no-op. `voice` and `style` entities likewise: they
  configure the render rather than appearing in it.
- **The loud target guard immediately found a real bug** in a passing test: the
  compiler emitted a viseme channel for a speaker who is not an entity in the
  shot, aimed at a node that was never built. An off-screen speaker is the
  standing workaround for unimplemented narration, so the fix is to emit no
  channel — the same reasoning already applied to face-baked characters.
- `pan_left` is no longer advertised. It was named in the IR's own comment on
  `Camera.move` and dead in the compiler; an error that contradicts the schema is
  worse than no error.
- The JS throw is wrapped as `CutoutRenderError` naming the frame and time —
  otherwise this trades a silent discard for a raw Playwright traceback.
- `pose.py`'s allow-list had drifted (no `viseme`, no `alpha`) because nothing on
  the render path calls `apply_pose`. It is now pinned to the runtime's switch.
- `an iterate`'s prompt enumerates the legal property names. A hallucinated
  `opacity` tween used to be silently inert; it is now a hard render failure.

- **Wave 1 verification record (#10)** at `misc/docs/wave1_verification.md`: the vendored
  engine's licence and provenance with digests, the DiceBear per-style licence table (11 of
  27 styles are CC0, the current default is CC BY 4.0), the network-guard design, and the
  silent-discard inventory with its empirical safety result. Fact with sources, not design
  space.
- **Two dev skills (#11).** `an-dev-licensing` — the chip is not evidence in either
  direction; code, weights and editor are three separate licences; vendoring is a licensing
  act; enforce in code, not in prose. `an-dev-runtime-assets` — verify packaging by building
  a wheel rather than reasoning about the backend, make a missing asset audible, and suspect
  the `__pycache__` mtime trap before the code.
- **`an-dev` truthed up.** Its pillar 13 named `animaLoadScene`, a leftover from the
  `anima` → `an` rename (the global is `anLoadScene`), and it claimed `runtime.js` "still
  draws the procedural rig" after the SVG-texture path had shipped. Added the testing
  contract — offline/hermetic, paid-API opt-in plus cassette, and mutation-test every guard,
  with the two mechanical traps that have already produced false "restores" and false
  "not caught" results.

- **`alpha` is an animatable node property (#13).** Set on the node's container,
  so it cascades — a tween on the character root fades every part of it. This is
  the entrance/exit primitive three later waves of #9 assume. The engine was
  never the obstacle: the pinned PixiJS 7.4.2 already supports alpha, tint,
  blendMode and sortableChildren; the ceiling was `runtime.js`'s.
- **`tint` was cut from that change after adversarial review, deliberately.** A
  `tween` on it compiled to `[(0.0, 0.0), (1.0, '#ff0000')]`; the runtime's
  mixed-type branch snaps to the first value, so the subject rendered **solid
  black for the whole shot** and flipped to red on the last frame — silently.
  `#f00` also parsed to a different, darker red, and `red` killed the render with
  a raw Playwright error. Tint needs cascade semantics, colour validation and a
  discrete-vs-interpolated decision; it lands with the shadow model that needs it.
- **Tween rest values are derived from `TransformJSON`'s field defaults**, not
  restated. A tween with no `from_value` used to start at 0.0 for *every*
  property — so a fade-out began already invisible (a silent no-op) and a scale
  tween popped in from nothing. A property with no rest value is now **refused**
  with a typed `CutoutCompileError` naming the property and the alternative,
  because 0.0 does not mean "unchanged", it means zero.
- Pose application is deterministic and shallowest-target-first. Object key order
  is insertion order, i.e. a function of channel emission order, which is not a
  contract — and the golden-frame work downstream depends on determinism.

## 2026-08-20

- **The engine ships with the package (#12).** `index.html` and `preview.html`
  fetched PixiJS from a CDN at render time, so a cold render needed the network
  and the per-shot content-hash cache was unsound — a third party could change
  the renderer without changing any cache key. PixiJS 7.4.2 is now vendored at
  `an/data/cutout_runtime/vendor/`, taken from the npm tarball (whose sha512
  matches the registry's published `dist.integrity`) and pinned by sha256, with
  its MIT notice beside it: the minified banner names the licence but carries
  neither the copyright line nor the permission text, so it does not discharge
  the obligation alone. `.gitattributes` marks both `-text` or the Windows CI leg
  CRLF-converts them and the digest goes red there only.
- **The offline network guard is armed**, adapted from `illustration`'s rather
  than invented a third time. Refusal and *recording* are separate mechanisms on
  purpose: this package swallows network failures in its own code, so a guard
  that only raised would be absorbed into a passing test.
- Arming it found two real ones. `test_promote_falls_back_to_new` was calling the
  DiceBear API on every run — and passing identically either way, because
  `new_character` catches the failure and generates geometry instead. Three
  whisper tests were resolving models over the network. `promote()` gained the
  `use_dicebear` passthrough `new_character` already had, since there was no way
  to make that fallback offline.
- **A socket guard cannot see Chromium.** It fetches from another process, so the
  render tests passed while the browser downloaded the engine. The new
  `hermetic_browser` fixture aborts every non-loopback browser request, and the
  render test that uses it is the only thing that can tell "we vendored the
  engine" from "we vendored it and the page actually uses it".
- **`_stage_character_assets` → `_stage_scene_assets`.** It skipped, silently, any
  texture whose `src` did not start with `characters/` and any file not on disk.
  Both now warn, naming the alias, the declared `src` and where it was looked
  for. The renderer's fallback for a missing texture is a white rectangle, which
  is indistinguishable from art. Resolution goes through a prefix→store table, so
  environment and style textures reach the screen instead of being dropped.
- The `force-include` list is now a complete inventory of the runtime assets, and
  a test keeps it that way. Its previous partial version omitted `preview.html`
  and read as though preview.html was excluded from the wheel — the opposite of
  the truth, which a wheel build settles in seconds.

## 2026-05-02

- Promotion fixes (driven by building a `~/Downloads/an_examples` gallery):
  `extract_part` now (a) preserves the source SVG's `<defs>` so promoted parts
  can resolve `fill="url(#gradient_id)"` references, (b) prefers the
  illustration `<g>` over a same-id skeleton `<circle>` so head / hip / etc.
  no longer get sliced as the pivot dot, and (c) crops each part's viewBox to
  the bounding box of its primitive content (rect / circle / ellipse / path)
  plus a small padding, so a part drawn in a small region of a 1024×1024
  character canvas now fills its allotted Pixi sprite rectangle.
- `silhouette.render_silhouette` now `page.goto(file://…)` the SVG directly
  instead of loading via `<img src="file://…">` from a `set_content()` page.
  The latter is blocked by Chromium's cross-origin policy and silently
  produced an empty screenshot. Hand-rigged characters now render real
  silhouettes (DiceBear-wrapped avatars still produce body-only silhouettes
  due to the nested-`<svg>` rasterization issue — separate follow-up).
- `audio.pipeline.produce_audio_for_scene`: the `already_done` idempotency
  check now also requires that the audio + viseme cache actually contain
  the expected refs. Previously a stale `audio_ref` in `scene.json` (left
  from a prior render) would short-circuit re-synthesis even after the
  artifact cache had been cleared, silently leaving the rendered mp4
  with no dialogue audio.
- New `MacSayTTS` provider (`an.audio.mac_say_tts`) — wraps macOS's free
  built-in `say` command into the `TTSProvider` Protocol. Registered in
  `TTS_FACTORIES` under `"mac_say"` and re-exported from `an.audio`.
  Audible, deterministic, fully offline, no API keys; `say -v ?` lists
  voices; default voice is "Samantha". macOS-only — non-macOS callers
  get a clear `MacSayTTSError`.
- Re-rendered the full gallery with `--tts elevenlabs --lipsync whisper`
  + a per-character voice mapping in `~/Downloads/an_examples/_lib/voices.py`
  (Maya/Theo/Sage/etc. → distinct ElevenLabs voice IDs, stamped onto
  each `Dialogue.voice_ref` before render). Word-aligned visemes track
  the real speech.
- `audio.pipeline.produce_audio_for_scene`: when a line is being
  re-synthesized (its prior `audio_ref` was set but no longer matches),
  reset `line.start` to the running cursor instead of keeping the stale
  value. Stale starts were computed against different audio durations
  and reusing them caused dialogue to overlap neighbours when a
  provider switch produced longer speech (offline → ElevenLabs).
  First-time synth still respects a user-supplied start.
- All 296 tests still pass.

## 2026-05-01

- Phase 1 substrate landed: Scene IR (Pydantic + composition combinators + flatten + validate + migrate + sync), dol-backed project mall (characters, environments, voices, styles, scenes, artifacts, decisions), Renderer / TTSProvider / LipSyncProvider / Verifier protocols, project init/load/save, argh-based CLI (`an init / validate / sync / check`), `check_requirements` diagnostics, three project skills (`an`, `an-spec`, `an-dev`), example `park_bench_cartoon/` skeleton, tests with doctests + pytest.
- Phase 2A — Python-side cutout subsystem: `an/adapters/cutout/` ships transform math (`Matrix3x3`, `TransformParams`, decompose/compose), easing (named presets + cubic-Bézier dispatcher), scene graph (`Node` tree as `MutableMapping[str, Node]`, lazy world-transform with dirty propagation, slot machinery), animation channels (`Keyframe`, `Channel`, binary-search evaluation), poses (`Pose: dict[(target, prop), value]`, `apply_pose`, `merge_poses` with override semantics), clips (`Clip` with `LoopMode.ONCE/LOOP/PING_PONG`), timeline (`Track`, `PlacedClip`, `Timeline`, `evaluate_timeline` with track-order override), JSON contract for the JS runtime (`CutoutSceneJSON` + nested Pydantic models, `to_dict`/`from_dict` round-trip), `compile_shot(Shot, mall)` bridge from authoring `an.ir` types into runtime JSON, and `CutoutRenderer` skeleton self-registering on import. 175/175 tests pass.
- Phase 2B — JS/PixiJS runtime: `an/data/cutout_runtime/{index.html, runtime.js, README.md}` consume `CutoutSceneJSON` and render via PixiJS v7 (CDN-loaded). Exposes `window.animaLoadScene`, `window.animaSetTime`, `window.animaCanvasReady`. Channel/timeline evaluation mirrors the Python side (linear lerp + named easings + cubic-Bézier). `an.adapters.cutout.runtime_files` Python helpers locate the bundled assets. Updated `pyproject.toml` to ship the runtime files in the wheel.
- Phase 2C — Headless rendering: `an/adapters/cutout/render.py` replaces the stub `CutoutRenderer`. Spins headless Chromium via Playwright, injects scene via `animaLoadScene`, drives `animaSetTime` per frame, screenshots the canvas to PNGs, ffmpeg-muxes to mp4 (H.264 + yuv420p + faststart). Wraps subprocess errors as `CutoutRenderError` with install hints. Smoke test: 1s shot @ 12fps → 320×240 mp4 in ~3s.
- Phase 2D — Project rendering: `an/render.py` orchestrates per-shot rendering through the registry, persists each shot mp4 to `mall["shots"]`, and concatenates via ffmpeg's concat demuxer to `output/<name>.mp4`. New CLI subcommand `an render <project_dir>`. New example `examples/single_character/` ships as the simplest renderable smoke. End-to-end: `an init demo && (write scene) && an render demo` produces a real mp4 (~3 KB for a 2s @ 24fps placeholder). 185/185 tests pass.
- Fix white-screen render: `markdown_to_ir` now parses ` ```yaml entities ``` blocks per shot (was silently dropping any entities declared in scene.md); `ir_to_markdown` round-trips them. `compile_shot` lays out placeholder character parts in a stick-figure shape with distinct colors (head peach, torso/arms blue, legs dark) instead of stacking them all at (0,0). The example single_character now renders a visible character. New regression test `test_rendered_character_is_visible` extracts a frame and asserts ≥5% non-white pixel coverage. 190/190 tests pass.
- Renamed `anima` → `an` (PyPI conflict); bumped to 0.1.0. Word-boundary regex rename across 77 files; package dir `anima/` → `an/`; CLI `anima` → `an`; skills `.claude/skills/anima*` → `an*`; on-disk project layout `.anima/` → `.an/`, `anima.toml` → `an.toml`; JS runtime API `animaLoadScene` → `anLoadScene` etc.; new GitHub repo at `thorwhalen/an`.
- Phase 3 — audio pipeline (TTS + lip-sync) with offline defaults: `OfflineTTS` (silent WAV proportional to text, ~10 cps proxy), `ElevenLabsTTS` (lazy SDK import, honors `ELEVEN_API_KEY`), `OfflineLipSync` (deterministic char-to-Rhubarb-viseme mapping with collapsed duplicates), `RhubarbLipSync` (subprocess wrapper with `--dialogFile`). `produce_audio_for_dialogue` / `produce_audio_for_scene` walk the IR, synthesize, content-hash-cache to `mall["audio"]` / `mall["visemes"]`, stamp viseme tracks back onto the IR. Idempotent.
- Phase 4 — dialogue → visemes drive cutout mouth slot (the v0.1 demo): each character's head gets a real `mouth` child node; `compile_shot` emits a step-easing `<speaker>/head/mouth:viseme` channel per dialogue line; JS runtime reshapes the mouth Graphics per viseme code. `render()` auto-runs `produce_audio_for_scene` when needed. `ScenesStore.__setitem__` equalizes md/json mtimes so subsequent `sync()` doesn't lose pipeline-injected JSON state. `sync()` "newer file wins" instead of always preferring Markdown. The park_bench_cartoon example renders end-to-end (~13 KB mp4 with two characters and animated mouths over offline lip-sync, no API keys).
- Phase 5 — verifier impls + orchestrator: `LayoutLintVerifier` (IR-only checks: duplicate shot ids, zero/negative durations, dialogue overflowing its shot, meta.duration ↔ timeline sum mismatch, invalid resolution); `HumanInTheLoopVerifier` (opens mp4 in OS default app, prompts y/N/r on stdin, skips silently on no-TTY). `orchestrate(project_dir, ..., verifiers=[...], skip_render=False)` returns `OrchestratorReport` with phases: validate → pre-render verify → render (auto-audio inside) → post-render verify. Verifier crashes are isolated and reported, not propagated.
- Phase 6 — Manim / Remotion / Whiteboard adapter skeletons: all four backends (cutout + manim + remotion + whiteboard) self-register and respond to `can_render()` correctly. `ManimRenderer` generates a minimal title-card scene and runs `manim -ql` via subprocess (works when manim is installed; clear error with install hint otherwise). `RemotionRenderer` and `WhiteboardRenderer` are skeletons that raise informative errors documenting what their full implementations need.
- Phase 7 — polish: README rewritten to reflect the working state (no API keys required for default offline pipeline, end-to-end CLI flow, current architectural pillars). 238 passed, 4 skipped.

## 2026-05-02

- Phase 8 Tier 1 — visual quality. Replace rect-only character art with a fleshier rig: ellipse head, hair as a rounded band (per-character color), white-sclera eyes with dark pupils, angled eyebrows, curved bezier mouth shapes per viseme code (drawn in PIXI Graphics, not flat rects). New ``VisualJSON.kind`` literals: ``"ellipse"``, ``"mouth"``, ``"eye"``. The runtime's ``drawMouthShape`` builds quad-arc lens shapes with optional teeth (G viseme) and tongue (H viseme).
- Phase 8 Tier 1 — procedural eye blinking. Runtime applies a sine-pulse scale_y squash on every ``*/head/{left_eye,right_eye}`` node every ~4 seconds, phase-offset deterministically by entity name so multi-character scenes don't blink in unison. No IR involvement.
- Phase 8 Tier 1 — emotion-driven eyebrows. ``compile_shot`` reads the existing ``Dialogue.emotion`` field and emits step-easing rotation channels on ``<speaker>/head/{left_brow,right_brow}`` for the line's duration. Eight presets (neutral / happy / sad / angry / surprised / skeptical / amused / thinking) tilt the brows symmetrically or asymmetrically. The dialogue parser now accepts ``speaker [emotion]: text`` syntax (round-trips through ``ir_to_markdown``).
- Phase 8 Tier 1 — environment entities. Previously ignored by the compiler; now ``entity.kind == "environment"`` produces a backdrop sub-tree (sky band + ground band) behind characters. Built-in presets: park (default), indoor, night, sunset; the environments store can override sky/ground/horizon by ref.
- Phase 8 Tier 1 — less twitchy lipsync. ``_add_viseme_clips`` caps adjacent viseme keyframes to a 0.14s minimum gap (~7Hz). With the new larger curved mouth shapes this prevents the rapid flicker of per-character viseme distribution.
- Phase 8 Tier 2 — media verification. New ``an.verify.media`` module: ``detect_silence`` (ffmpeg silencedetect parser), ``audio_volume`` (mean/max dB), ``ssim`` (numpy-only, no scikit-image), ``ssim_image_files``, ``extract_frames`` helper, optional ``transcribe`` (lazy-imports faster-whisper, raises informative error if missing). 7 new tests verifying SSIM behavior, audio level on real renders, silencedetect on dialogue and silent shots, frame perceptual diff at adjacent vs. distant timestamps.
- All three example mp4s re-rendered with --tts elevenlabs. park_bench_cartoon now has a sky/grass background, two visually-distinct characters with proper faces, blinking eyes, dialogue-driven eyebrows ("thinking" → furrowed for Charlie's question, "amused" → raised outer corners for Maya's answer). 268 passed, 4 skipped.
- Phase 9 — closing the verifier loop + word-aligned lip-sync.
  - `WhisperLipSync` (`an.audio.whisper_lipsync`): faster-whisper transcribes the rendered audio with word_timestamps=True; visemes get distributed within each word's [start, end] span (collapsed-duplicates), with rest visemes inserted in silent gaps wider than 0.2s. Class-level model cache so repeated align() calls in one process share the model. `--lipsync whisper` is now a CLI option. Skip-tests when faster-whisper isn't installed. Tested end-to-end with real ElevenLabs speech: ~14 kf/s vs. previous offline density, but locked to actual word boundaries — far less twitchy.
  - `MediaQualityVerifier` (`an.verify.media_quality`): post-render Verifier wired into the orchestrator's default chain. Runs three signals — max audio dB above floor (catches "AAC stream present but silent"), silence-vs-dialogue ratio (catches "speech missing in a dialogue scene"), mean adjacent-frame SSIM (catches "render is frozen"). Renders that misbehave (e.g. dialogue + offline TTS) get flagged automatically.
  - `VisionLMVerifier` (`an.verify.vision`): the spec's signature feature — Claude vision looks at sampled frames and reports issues. Lazy-imports `anthropic`; honors `ANTHROPIC_API_KEY`; skips with an info finding (passed=True) when either is missing so it can sit in the default chain. Sends 4 frames + a focused prompt to claude-haiku-4-5 (~$0.005 per call). Lenient JSON parser pulls findings out of fenced or prose-wrapped replies.
  - 282 passed, 4 skipped (was 268; +14 across whisper, media-quality, vision tests including a live Claude API call).
- Phase 10 — iterative edit loop. `an iterate <dir> "<instruction>"` realizes the spec's signature user story: free-text instruction → Claude (Opus 4.7 + adaptive thinking) returns a structured JSON patch list → patches validated against the schema → applied to the IR → persisted → affected shots' caches invalidated for cheap re-render. Patches use JSON-pointer-style paths (`timeline/1/dialogue/0/text`) with `set` / `append` / `delete` ops. The orchestrator records each iteration in `mall["decisions"]` for audit. Demo: `an iterate examples/park_bench_cartoon "Make Maya's response a bit longer and more affectionate"` → Claude emits 2 patches (text + emotion), only s2's cache is invalidated, next `an render` regenerates only that shot. Falls back gracefully when `ANTHROPIC_API_KEY` is missing. 295 passed, 4 skipped (was 282; +13 across path-walking unit tests + 2 live Claude API calls).

- Phase 11a — character authoring tools. New `an.characters` package implements the Spine-shaped descriptor / Pose Animator SVG convention / 9-shape Rhubarb mouth set / sine-wave breath idle from the Real-Character-Art research report (`misc/docs/Real Character Art for an — A 2D Cutout Pipeline Upgrade Plan.md`). Modules: `schema.py` (CharacterDescriptor, Bone, Slot, Skin, Attachment, AnimationTrack, IdleAnimation; `extra="allow"` for forward compat), `svg_utils.py` (stdlib lxml-style: normalize_svg, extract_part, extract_pivots, promote_inkscape_labels_to_ids), `mouth_set.py` (parametric A-H+X mouth generator — 9 shapes per character, no network), `dicebear.py` (HTTP API client + wrap_dicebear_for_an envelope), `idle.py` (breath_animation 4s/±2px sine, blink_animation 0.13s closure, random_blink_schedule), `silhouette.py` (Playwright-rasterized + PIL-thresholded IoU comparator for the Disney silhouette test), `factory.py` (new_character end-to-end), `promote.py` (assets.promote per research §5.3). New CLI sub-namespace `an character {new,mouths,validate,silhouette,preview}` mounted via argh `group_name`. The `preview` command writes a self-contained HTML viewer that cycles through all 9 visemes and applies the breath/head-tilt animation in JS — useful for eyeballing a character before wiring it into the renderer. Renderer integration (Pixi SVG-texture path) is the next phase. 27 new tests. 340 passed, 4 skipped (was 295 + 13 doctests; +27 test_characters.py + 18 character module doctests).
- Phase 11a follow-up — preview→mp4 recording. New `an.characters.record` (record_preview_to_mp4 + record_character) uses Playwright video recording and ffmpeg webm→mp4 to produce a real video file from a character's preview HTML. New CLI `an character record <name>`. `examples/character_gallery/build.py` now writes three videos to `videos/<name>.mp4` (committed; ~19 KB each); the gallery's index.html embeds them inline. Closes the gap from issue #1 between authoring tooling and a visible deliverable; the proper Pixi runtime SVG-texture path is Phase 11b. 341 passed, 4 skipped.
- Phase 11b — Pixi SVG-texture rendering. The cutout adapter now renders Phase-11a CharacterDescriptors as SVG-textured Pixi Sprites instead of procedural Graphics. Detection is automatic: if `mall["characters"][ref]` returns a dict with `kind == "CharacterDescriptor"`, the compiler emits `kind: "svg_sprite"` visuals + populates `AssetsJSON.textures` with per-part aliases (e.g. `maya.head`, `maya.mouth_a`); otherwise the procedural rig is used unchanged (no regression). `an.adapters.cutout.render._stage_character_assets` copies the SVG files from `mall["characters"]._root` into the per-shot runtime dir at the declared paths. The cutout renderer now serves the runtime via a tiny in-process `http.server` (PIXI.Assets can't `fetch()` file:// URLs in headless Chromium). `anLoadScene` is now `async` — it awaits `PIXI.Assets.load(aliases)` before walking the scene tree. `makeSvgSprite` builds a Pixi Sprite from a pre-loaded texture; `setVisemeOnMouth` swaps the mouth Sprite's `texture` via the per-character viseme map carried on the visual. Eye blink continues to work via the existing `scale.y` squash on Sprite nodes (eyes named `left_eye`/`right_eye` to match the runtime regex). `CharactersStore.META_NAME = "character.json"` so the existing `mall["characters"][ref]` API surfaces Phase-11a descriptors. SVG character art now drives the actual mp4 render: `examples/park_bench_cartoon` re-rendered with two SVG characters (Charlie offline + Maya DiceBear adventurer) — head art baked into the texture; lip-sync via attachment swap; arms/legs/torso as separate Sprites composing into the rig. `an.characters.factory` rewritten: each body part is a self-contained content-centered SVG (no canvas-relative slicing). Compiler suppresses overlay eyes/brows when art_provenance is "dicebear" so DiceBear's baked features don't double up. 344 passed, 4 skipped (was 341; +2 new svg-character compile tests + 1 doctest).
- Phase 11c — per-shot parallel rendering. `an render <dir> --parallel auto` (or `--parallel N`) runs each shot in its own thread via `concurrent.futures.ThreadPoolExecutor`. The cutout renderer is already self-contained (per-shot work dir, own Chromium, own http.server) so the calls are thread-safe. Auto = `min(n_shots, cpu_count, DEFAULT_PARALLEL_CAP=4)`. Measured on examples/park_bench_cartoon (2 shots, offline TTS/lipsync, 12 s output): 25.2 s serial → 12.7 s parallel (1.98× speedup, near-perfect linear scaling on 2 shots). New `_render_one` and `_resolve_parallel` helpers in `an.render`. CLI exposes via `an render --parallel auto` (or a number); `render_project()` and `render()` accept the same kwarg. 349 passed, 4 skipped (was 344; +4 _resolve_parallel unit tests + 1 doctest).

- Phase 11d — proper cartoon demo. Replaced the character_gallery's misleading preview-recording mp4s with a single `videos/cartoon.mp4`: a 9-second 2-shot scene rendered via `an render --parallel auto` using two procedurally-generated offline characters speaking with lip-sync over a park backdrop. The whole thing is built by `examples/character_gallery/build.py` end-to-end (generate characters → validate → silhouette test → preview pages → render cartoon → write index.html). The previous preview recordings showed the dev-tool gallery panel rather than the new SVG-texture pipeline; this fixes that conceptual mismatch by exercising the real `an render` flow that any production scene uses.

- Phase 11d hotfix — three issues with the gallery cartoon. (1) "Four eyes" per character: the offline fallback face baked eyes/brows/mouth into the head SVG, doubling up with the overlay slots. Fixed by stripping facial features from `_fallback_face_svg` (just skin disc + hair tuft remain); the overlays are the only animated facial features now, so blink and lip-sync look right. (2) Silent audio: the gallery build script always passed `tts="offline"`; now auto-detects `ELEVEN_API_KEY` (env) → elevenlabs and `faster-whisper` (importable) → whisper, with offline fallback and a status print. (3) Color drift across runs: `_palette_for_seed` and `_fallback_face_svg` used Python's salt-randomized `hash()`, so colors changed every render. Replaced with `hashlib.md5`-based `_stable_hash`. Skin tones now come from a hand-curated palette of 8 illustrative tones (pale warm → deep brown) instead of the busted `(h & 0xCFCFCF | 0x808080)` formula that constrained everything to grayscale. Cartoon mp4 regenerated with real ElevenLabs speech: 9.0 s, 184 KB, two visually-distinct characters with audio.

- Phase 12 — live preview, promote example, DiceBear lock. (1) New `an preview <dir>` (`an.preview.preview_project`) spins up a local HTTP server pointed at a freshly-compiled cutout scene JSON, polls `scene.md` / `ir/scene.json` for changes and recompiles; the browser polls `Last-Modified` on `scene.json` every 500 ms and re-calls `anLoadScene`. Lossy by design (visuals only, no audio mux) — for fast iteration on layout/blocking before `an render`. New `preview.html` next to the existing runtime, separate from `index.html`. (2) `examples/promote_demo/` exercises `an.characters.promote`: a hand-drawn `raw_maya.svg` with `<g id="skeleton">` + `<g id="illustration">` part groups → `build.py` calls `promote(entity="raw_maya", as_="maya-promoted")` → renders a one-shot scene to mp4. Demonstrates the hand-drawn → mall-character flow end-to-end. Top-level `.gitignore` re-includes `examples/promote_demo/assets/characters/raw_maya/*.svg` and re-excludes the regenerated promote target. (3) DiceBear mouth overlay locked off: when a CharacterDescriptor has `metadata.art_provenance` of `"dicebear"` or `"external_avatar"`, `_build_svg_character_subtree` now skips the overlay mouth visual *and* `_add_viseme_clips` skips the speaker's viseme channel (the audio still plays, but the mouth doesn't move). Documented in the `an` skill and the park_bench_cartoon README — production scenes with dialogue should hand-rig characters following the Pose Animator convention (see `examples/promote_demo/`). 357 passed, 4 skipped (was 349; +5 preview tests + 2 face-baked-suppression tests + 1 doctest).
