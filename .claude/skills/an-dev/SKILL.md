---
name: an-dev
description: Use when working *inside* the an repo itself — writing or modifying an's own Python code, tests, schemas, adapters, or skills. Triggers when CWD is the an repo and the task is implementation work (adding a renderer, fixing a schema bug, updating a store). Not for using an from a downstream project.
---

# an-dev — working inside the an repo

This skill orients you for engineering work *on* an. If you're using an from a project, see the `an` skill instead.

## Read these before designing

**Always start with `misc/docs/architecture_as_built.md`** — the canonical, current-state map of the system (modules, control flows, invariants, caching strategy). Read it before any non-trivial change.

`misc/docs/wave1_verification.md` is the **verification record** for Wave 1 of epic #9:
the vendored engine's licence and provenance, the DiceBear per-style licence table, the
network-guard design, and the silent-discard inventory.

`misc/docs/wave2_research.md` is the **research record for Wave 2** — the metric ledger,
the determinism audit, the golden-corpus rules and the vision-verifier cassette design.
Read it before touching `an/bench/`, the corpus, `AN_DETERMINISTIC`, or
`an/verify/vision.py`. It contradicts the epic in six places and says so up front; where
they disagree, it measured and the epic did not.

Both are fact with sources, not design space — prefer them over re-deriving.

Two sibling skills carry rules that are easy to violate and expensive to get wrong:
**`an-dev-licensing`** (before adding *any* dependency, model weight, font or bundle) and
**`an-dev-runtime-assets`** (before touching anything under `an/data/`).

For deeper subsystem design history, the research reports next to it cover the design space
(NOT current state):

  - `report 0 ...md` — overall architecture, IR layering, agent-tool boundary, verification
  - `dsl_design_patterns_report.md` — IR schema design, evolution rules
  - `report 2 ...md` — interchange formats, channel/keyframe representation
  - `report 5 ...md` — cutout scene-graph + director architecture (closest to as-built)
  - `report 1 ...md` — JS-side cutout ecosystem (PixiJS + GSAP recommended)
  - `report 3 ...md` — viseme standards, lip-sync pipeline
  - `Annotation systems ...md` — interval data structures, rational time, A/V sync

**The master spec** is the prompt that bootstrapped this project; if you don't have it in context, the user will paste it. Plan files for current phases live under `~/.claude/plans/`.

## Architectural pillars (locked in — do not re-litigate)

1. Three-layer IR (Narrative `scene.md` / Scene Graph `ir/scene.json` / Render Code generated). Render Code is disposable.
2. Top-level versioning envelope on the IR; `extra="allow"` on inbound; additive-only field changes; migration registry chained through `an.ir.migrate`.
3. Composition primitives are Python-side combinators that flatten to a canonical timeline. The flat form is what verifiers and renderers operate on.
4. Path-based property targeting (`"charlie/left_arm:rotation"`) for renderer-portability. The rigs are FLAT: arms are siblings of the torso. An unknown target raises, so a path example that names nothing is now a trap.
5. Setup pose plus deltas; slot/skin/animation separation. (Cutout adapter, Phase 2.)
6. Time in seconds (float) at the IR boundary; rational time only inside the audio pipeline where drift matters.
7. All external systems behind `Protocol`s (`Renderer`, `TTSProvider`, `LipSyncProvider`, `Verifier`).
8. All persistence via dol-backed `MutableMapping`s organized into the project mall.
9. Dispatch to interface: business logic is plain Python; CLI (argh) is dispatch only.
10. Verification is a swappable Protocol; same interface for human, lint, vision-LM, MoVer.
11. Caches are content-hash keyed (audio_ref, viseme_ref). Cache invalidation is by deletion (`del mall["shots"][shot_id]`). No cache versioning — keys are deterministic so collisions across versions are impossible.
12. **Equalize mtimes after writing both `scene.md` and `ir/scene.json`** in `ScenesStore.__setitem__`. Sync's "newer wins" tolerance band depends on this. Without it, sync flip-flops on every load and pipeline-injected state (viseme tracks, audio_refs) gets stripped.
13. The synthetic root container in the JS runtime is **not indexed** in `nodeIndex`. `compile_shot` emits target paths starting with the entity name (`charlie/head/mouth`); the runtime's `anLoadScene` skips the root when populating `nodeIndex`.

## Module map (current state)

Read `misc/docs/architecture_as_built.md` for the full map. The pieces that didn't exist in the original spec:

- `an/iterate.py` — free-text → Claude (Opus 4.7) → IR patches (Phase 10)
- `an/audio/whisper_lipsync.py` — faster-whisper word timestamps → visemes (Phase 9)
- `an/audio/providers.py` — make_tts / make_lipsync factories (Phase 8)
- `an/verify/media.py` — ssim, detect_silence, audio_volume, extract_frames, transcribe (Phase 8)
- `an/verify/media_quality.py` — MediaQualityVerifier (Phase 9)
- `an/verify/vision.py` — VisionLMVerifier (Claude vision QA, Phase 9)
- `an/characters/` — character authoring tools (Phase 11a): Spine-shaped `CharacterDescriptor`, SVG utils, parametric 9-shape mouth generator, DiceBear client + envelope, idle-animation factories, silhouette test, `assets.promote`. Powers `an character {new,mouths,validate,silhouette,preview}`. The Pixi SVG-texture path has since shipped: `makeSvgSprite` builds a real `PIXI.Sprite` from a preloaded texture, and `preloadAssets` stages them. The procedural rig is the fallback for characters with no descriptor art, not the only path.

## How to wire a new TTS / LipSync / Verifier / Renderer

The four Protocols live in `an.audio.tts.TTSProvider`, `an.audio.lipsync.LipSyncProvider`, `an.verify._base.Verifier`, and `an.adapters._base.Renderer`. To add a new one:

1. Implement the Protocol in a new file under the matching subpackage.
2. Register it in the factory: `an.audio.providers.TTS_FACTORIES` / `LIPSYNC_FACTORIES` (a LipSync factory must accept the keyword `language` — `lambda **_: MyLipSync()` if it ignores it — because `make_lipsync(name, language=)` passes it to every factory, an#96), or for renderers via `an.adapters._base.register_renderer(MyRenderer())` at module import.
3. Export it from the subpackage's `__init__.py`.
4. Add skip-if-deps-missing tests under `tests/test_<my>.py`.

## Code conventions

- Public API in `an.__all__` is **curated**. Internals get an underscore prefix.
- Keyword-only arguments past the 2nd or 3rd positional. No magic numbers — defaults at the top of the module they belong to.
- No globals, no service locators. Pass the mall in.
- Functional over OOP; OOP only for orchestrators and stateful sessions.
- Errors are informative and specific. Wrap subprocess errors at the facade boundary.
- Doctests for the public API; pytest for cross-cutting checks.

## Testing rules — all three are enforced, not aspirational

**The suite is offline and hermetic, and a guard proves it.** `tests/conftest.py` refuses
*and records* every non-loopback socket use; the recording half is load-bearing, because
this package swallows network failures in its own code (`new_character` catches the
`RuntimeError` from `fetch_dicebear` and generates geometry instead), so a guard that only
raised would be absorbed into a passing test. Arming it found two tests that had been
reaching the network on every run while passing identically either way.

**A Python socket guard cannot see Chromium.** It fetches from another process. Anything
asserting the renderer does not reach out must use the `hermetic_browser` fixture, which
aborts non-loopback requests at the Playwright layer. Nothing under `an/data/` may fetch at
render time; `tests/test_vendored_engine.py` enforces it statically *and* by rendering.

**A paid API needs an explicit positive opt-in AND a cassette.** `AN_LIVE_API_TESTS=1` plus
the `live_api` marker. A key being present is not consent to spend — that gate exists
because a plain `pytest -q` in this repo once made real, billed ElevenLabs calls and
reported PASSED. A cassette miss is an ERROR, never a fallthrough to a real call.

**Never skip at module level. Gate the TEST, with a marker.** This is the rule that #22
was, and it is not about browsers — it is about the difference between a test that is
*skipped* and a test that does not *exist*.

```python
# WRONG — aborts the module import, so NONE of its tests are collected. They are
# absent from the pass count AND from the skip count, so nothing reports the hole.
playwright = pytest.importorskip("playwright.sync_api")

# RIGHT — collection always succeeds; the gate is applied afterwards, by marker.
pytestmark = [pytest.mark.browser, pytest.mark.ffmpeg]   # whole module needs it
@pytest.mark.browser                                      # or just this test
```

Measured cost of getting it wrong here: 472 tests collected with Playwright installed,
438 without — and **13 of the 34 casualties needed no browser at all**,
because they merely lived below an `importorskip` aimed at something else. Among them:
every SSIM test for `an.verify.media` (the primitives Wave 2's ledger is built on), and a
paid Anthropic call whose only gate was "is a key set" — a `live_api` violation that was
invisible rather than absent. A **separate** module-level `importorskip("nw")` in
`tests/test_genre.py` was hiding the guard that `import an` does not drag in `nw` — same
bug, different axis, found by the new guard rather than by the sweep.

The available markers and what they mean:

| Marker | Requires | Gated by |
|---|---|---|
| `browser` | headless Chromium via Playwright | `AN_BROWSER_TESTS`; off in CI |
| `ffmpeg` | the `ffmpeg` binary | same |
| `live_api` | a real, billed call | `AN_LIVE_API_TESTS`; never in CI |
| `live` | the network, but free | skipped in CI |

Three properties of the browser gate worth knowing before you touch it
(`tests/conftest.py`; `tests/test_browser_gate.py` mutation-tests all 28 guards
against 20 mutations):

1. **Collection is invariant** — which tests exist must not depend on what is installed.
   The load-bearing guard is `test_collection_does_not_depend_on_the_environment`, which
   shadows every optional import **and** strips the external binaries from `PATH`, then
   compares pytest's own node-id sets. Its reference is outside itself, so it catches
   routes nobody enumerated. The AST scanner beside it is a list of known spellings and is
   the weaker half — an adversarial review got past an earlier, scanner-only version four
   different ways. **Do not write that this bug is "structurally impossible".** It is not.
2. **A gated run says so out loud** — `browser tests: 24 collected, 0 ran, 24 skipped: …`
   in the run summary. A green run must never be silent about having checked zero pixels.
   That line is deliberately **ASCII**: the Windows console renders an em dash as a
   replacement character, and the one line whose whole job is to be read should be legible
   on the platform whose leg now blocks the build.
3. **An explicit opt-in that cannot be honoured is an ERROR, not a skip.**
   `AN_BROWSER_TESTS=1` with no browser aborts the run. A CI job whose `playwright install`
   quietly failed has to go red; green-with-24-skips is the exact failure #22 existed to end.

To actually run them: `pytest -q` on a machine with the `cutout` extra **and the ffmpeg
binary** — the extra ships `ffmpeg-python`, a wrapper, not the binary, so
`pip install -e '.[cutout]' && playwright install chromium` alone runs 2 of the 24
(`test_preview_reload.py`, the only browser tests that need no ffmpeg). Add
`brew install ffmpeg` / `apt-get install ffmpeg`.

**On a PR, ask for the lane by labelling it — this is yours to do:**

```bash
gh api -X POST repos/thorwhalen/an/issues/<N>/labels -f 'labels[]=run-browser-tests'
```

**Not `gh pr edit --add-label`** — on this owner's repos that call goes through the
GraphQL path and dies on the projects-classic deprecation, **printing an error but
exiting 0 and applying no label**. Verified on PR #30. Always read the labels back:
`gh pr view <N> --json labels -q '.labels[].name'`.

Add it whenever your PR can change a pixel: the runtime under
`an/data/cutout_runtime/`, the cutout compiler or serializer, the render path, the
vendored engine, the ffmpeg flags, or the character rig. An unlabelled PR gets **no**
pixel verification and the job never starts, so nothing in the checks list will tell
you it was missing. Measured cost when you do add it: 103 s, cold cache.

`misc/docs/adr_ci_verification_perimeter.md` is the decision record — read it before
changing what CI does or does not verify, and before writing any sentence of the form
"verified in CI", which is the one claim this repo has agreed never to make about a
rendering behaviour.

**Every regression guard is mutation-tested.** Delete the fix, confirm the test goes red.
An unproven guard is decoration, and this is not theoretical here — a guard that tested an
ordering *helper* in isolation passed while `applyPose` never called it, and a meta-test for
an autouse fixture passed on the mutant because requesting the fixture by name installs it
regardless of `autouse`. Both were caught only by mutating.

Two mechanical traps when mutating:

- **Clear `__pycache__` and `touch` the file after restoring.** A restore that rewinds mtime
  (`mv`, `cp -p`) leaves a `.pyc` newer than its source, so Python keeps the stale bytecode
  and the mutation silently stays live. This has already produced a "passing" restore that
  was not.
- **Run the whole test file, not a `-k` filter.** A filter that misses the one test which
  would have caught the mutation reads as "not caught".

## Per-PR housekeeping

- Append a one-line entry under today's date in `misc/CHANGELOG.md`.
- If a non-trivial design decision was made without explicit user blessing, log it (in the PR description for repo-level work; in `.an/decisions.jsonl` for project-level work).
- Update the matching skill in `.claude/skills/` if the user-facing surface changed.

## When unsure about prior art

The user maintains a large local Python ecosystem (`~/Dropbox/py/proj/`). Before reinventing storage / dispatch / etc., check:

- `dol` — for any `MutableMapping`-shaped persistence.
- `argh` — for CLI dispatch (see `~/.claude/skills/python-dispatching/SKILL.md`).
- The `python-storage`, `python-iterables`, `python-project-structure`, `python-dispatching` skills under `~/.claude/skills/`.
