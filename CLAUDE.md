# Working in the an repo

This file orients an AI agent doing engineering work *on* an itself. If you're using an from a downstream project, see `.claude/skills/an/SKILL.md` instead.

**The canonical current-state map is `misc/docs/architecture_as_built.md`** — module map, the three control flows, load-bearing invariants, caching strategy. Read it before any non-trivial change. This file is only the orientation layer above it; when the two disagree, the code is authoritative and both get fixed.

## Where things live

- **Source:** `an/` (the package).
- **As-built architecture:** `misc/docs/architecture_as_built.md`. Sections 1–9 are current. Its §10 ("the four phases that haven't shipped yet") predates the character/parallel/preview work and is itself stale — three of its four items have since shipped, and it names `an.assets.promote`, which is really `an.characters.promote`. Fix that section when you touch it.
- **Reference research:** `misc/docs/` — seven deep reports plus the character-art upgrade plan. These describe the *design space*, not current state. Read the matching one before designing or extending a subsystem.
- **Wave records (fact, not design space):** `misc/docs/wave1_verification.md` and `misc/docs/wave2_research.md`. The Wave 2 record is the input to `an bench`, the golden corpus, `AN_DETERMINISTIC` and the vision-verifier cassettes — and it **contradicts epic #9 in six places**, each measured. Read it before building any of them; do not re-derive it.
- **AI changelog:** `misc/CHANGELOG.md` — append a one-line entry under today's date when you finish a non-trivial chunk.
- **Project skills:** `.claude/skills/` — `an` (downstream orchestrator), `an-spec` (director interview), `an-dev` (dev-side; read it alongside this file), `an-dev-bench` (the measurement instrument — `an bench`, the metrics ledger, the golden corpus; read it before adding a metric or a corpus scene).
- **Tests:** `tests/`. Doctests in module docstrings cover the public API; pytest covers cross-cutting and end-to-end checks. Run `pytest -q`, or `pytest -q --doctest-modules an/` for the full sweep. Some tests skip when an optional dependency or API key is absent. **Never write a test count into a doc** — that is precisely the number that goes stale.
- **Examples:** `examples/` — `single_character` (simplest render), `park_bench_cartoon` (two characters, dialogue, lip-sync), `walk_demo`, `character_gallery` (`build.py` generates characters then renders a cartoon end-to-end), `promote_demo` (hand-drawn SVG → mall character → render).

## Architectural pillars (locked in)

1. **Three-layer IR.** `scene.md` (Narrative, human-edited) ↔ `ir/scene.json` (Scene Graph, agent-edited, the SSOT) → render code (per-backend, disposable). Information flows downward; verification feedback flows upward.
2. **Schema evolution from day one.** Versioning envelope, `extra="allow"` on inbound, additive-only changes, the chained registry in `an/ir/migrate.py` (`register_migration(src, dst)`). Round-trip stability is tested.
3. **Composition combinators flatten to a canonical timeline.** Authoring is fluent (`sequence`, `parallel`, `tween`); the canonical form is the flat list of `FlatAction`s with absolute times. Verifiers and renderers see only the flat form — never reason about composition nesting at render time.
4. **Path-based property targeting.** `"charlie/left_arm:rotation"` so animation generalizes across renderers. (The rigs are FLAT — arms are siblings of the torso, not children. The old `charlie/torso/left_arm` example named a node nothing builds, which stopped being harmless once an unknown target began to raise.)
5. **Time in seconds (float)** at the IR boundary; rational time only where audio drift matters.
6. **Everything external behind a `Protocol`.** `Renderer` (`an/adapters/_base.py`), `TTSProvider` (`an/audio/tts.py`), `LipSyncProvider` and `WordTimingProvider` (`an/audio/lipsync.py`), `Verifier` (`an/verify/_base.py`). Several implementations now exist per protocol; they are selected by name through the factories in `an/audio/providers.py` or via `register_renderer(...)` at import time.
7. **Persistence via dol-backed `MutableMapping`s** organized into the project mall (`an.build_project_mall`). No ad-hoc file I/O outside the stores.
8. **Dispatch to interface.** Plain Python functions are the business logic; the CLI is a thin argh dispatcher over `an.tools._dispatch_funcs`, plus `an.tools._dispatch_namespaces` for the `an character ...` sub-namespace.
9. **Verification is a swappable `Verifier`.** Same interface for lint, media QA, vision-LM and human-in-the-loop.
10. **Typed error routing.** `Finding(severity, ir_path, description, suggested_fix)` so the orchestrator routes each fix to the lowest IR layer that can make it.
11. **Content-hash caching; invalidation by deletion.** Keys are content hashes (`Dialogue.audio_ref`, `Dialogue.viseme_ref`; `shot.id` for per-shot mp4s). Invalidation is `del mall["shots"][shot_id]`. There is no cache versioning — the keys are deterministic.

## Code conventions

- `an.__all__` is **curated** — it exposes the IR, composition, project and diagnostics surface. The pipeline entry points are deliberately *not* re-exported at top level: use `an.render.render_project`, `an.orchestrate.orchestrate`, `an.iterate.iterate`, `an.preview.preview_project`. Internals are underscore-prefixed.
- Keyword-only arguments past the 2nd or 3rd position; no magic numbers; defaults at module top.
- No globals, no service locators — pass the mall in.
- Functional over OOP; OOP only for orchestrators and stateful sessions.
- Errors are informative and wrap subprocess failures at the facade boundary as a typed error (`CutoutRenderError`, `ManimRenderError`, `MacSayTTSError`, …), never a bare `NotImplementedError`.
- Doctests for public API functions; pytest for cross-cutting and integration checks.
- Local packages have **no declared dependency versions** (e.g. `"dol"` not `"dol>=0.3"`).

## As-built capability map

What exists, and where. This replaces the old phase table on purpose — phase tables drift.

| Capability | Lives in | State |
|---|---|---|
| Scene IR: schema, composition, validate, migrate, md↔json sync | `an/ir/` | shipped |
| Project layout + dol-backed mall (characters, environments, voices, styles, scenes, artifacts, decisions) | `an/project.py`, `an/stores/` | shipped |
| CLI `an {init,validate,sync,render,iterate,preview,check}` + `an character {new,mouths,validate,silhouette,preview,record}` | `an/tools.py`, `an/__main__.py`, `an/characters/cli.py` | shipped |
| Cutout backend: transform math, easing, scene graph, channels, poses, clips, timeline, JSON contract, `compile_shot`, headless Playwright+ffmpeg render | `an/adapters/cutout/` | shipped — the real v0.1 renderer |
| JS runtime: PixiJS v7, procedural rig, SVG-sprite rig, viseme mouth shapes, procedural blinks | `an/data/cutout_runtime/{index.html,runtime.js,preview.html}` | shipped |
| TTS providers `offline` / `elevenlabs` / `mac_say` | `an/audio/{offline_tts,elevenlabs_tts,mac_say_tts}.py`, factories in `an/audio/providers.py` | shipped |
| Lip-sync providers `offline` / `whisper` / `rhubarb`, plus `WordTimingsLipSync` for injecting precomputed word timings | `an/audio/{offline_lipsync,whisper_lipsync,rhubarb_lipsync,injectable_lipsync}.py` | shipped |
| Audio pipeline with content-hash caching and provider-swap re-synthesis | `an/audio/pipeline.py` | shipped |
| Verifiers: layout lint, media quality, vision-LM, human-in-the-loop (+ ffmpeg/SSIM helpers) | `an/verify/{layout,media_quality,vision,human}.py`, `an/verify/media.py` | shipped |
| Project render: per-shot dispatch through the registry, shot cache, ffmpeg concat | `an/render.py` — `render_project()` / `render()` | shipped (no stub, no `NotImplementedError`) |
| Orchestration `validate → pre-verify → audio → render → post-verify` | `an/orchestrate.py` — `orchestrate()` → `OrchestratorReport` | shipped |
| Per-shot parallel rendering, `an render --parallel auto\|N` | `an/render.py` — `_resolve_parallel`, `_render_one` | shipped |
| Free-text edit loop: instruction → Claude → JSON patches → validated IR → selective cache invalidation | `an/iterate.py` — `iterate()` | shipped (needs `ANTHROPIC_API_KEY`) |
| Character authoring: descriptor schema, SVG utils, 9-shape mouth set, DiceBear client, idle/blink, silhouette test, factory, promote, preview recording | `an/characters/` | shipped |
| SVG-texture character rendering (descriptors drive real sprites, not procedural rects) | `an/adapters/cutout/compile.py` (`svg_sprite` visuals) + `runtime.js` `makeSvgSprite` | shipped |
| Live preview with file-watch reload, `an preview <dir>` | `an/preview.py` — `preview_project()`, `preview.html` | shipped (visuals only, no audio) |
| Metrics ledger: `an bench` renders a fixed corpus and writes one row per (date, commit) | `an/bench/` — `run_bench()`, `METRICS`, `build_scene_block()` | shipped (an#36); goldens are an#38, `--compare` is an#40 |
| Determinism perimeter: the runtime probes, `an/determinism.py` judges, enforced by default | `an/data/cutout_runtime/runtime.js` `anDeterminismReport` + `an/determinism.py` | shipped (an#37) |
| Camera moves (`hold / push_in / pull_out / zoom_in / zoom_out`) as root-scale tweens | `an/adapters/cutout/compile.py` — `_add_camera_clips` | shipped |
| Emotion-driven eyebrows, environment backdrops, per-character palettes | `an/adapters/cutout/compile.py` | shipped |
| Manim backend | `an/adapters/manim_adapter.py` | **title card only** — see gaps |
| Remotion backend | `an/adapters/remotion_adapter.py` | stub — raises `RemotionRenderError` documenting what a real impl needs |
| Whiteboard backend | `an/adapters/whiteboard.py` | stub — raises `WhiteboardRenderError` |

## Genuine gaps and sharp edges

Honest list. Don't let it rot either — delete a line when you close it.

- **The Manim adapter is not a compiler.** `_render_script` in `an/adapters/manim_adapter.py` emits a single `Text(title)` title card of the right duration and shells out to `manim -ql`. Nothing in the Shot — entities, actions, dialogue, camera — reaches the generated script. A real shot-to-Manim compiler is unbuilt design work.
- **Nothing ever *emits* a non-default `loop_mode`.** Both evaluators honour all three modes — `runtime.js`'s `wrapTime` and `clip.py`'s `_wrap_time`. This line used to claim the runtime ignored it, which was stale. The real gap is the inverse: no compiler code writes the field, so a looping clip is reachable only by hand-writing `CutoutSceneJSON`.
- **DiceBear-sourced characters don't lip-sync.** When a descriptor's `metadata.art_provenance` is `"dicebear"` or `"external_avatar"`, the compiler suppresses both the overlay mouth visual and the speaker's viseme channel (the face is baked into the head SVG). Audio plays; the mouth doesn't move. DiceBear is a bootstrap path — hand-rig for production dialogue, see `examples/promote_demo/`.
- **Multi-scene projects don't exist.** `"main"` is the only supported key in the scenes store.
- **No browser test runs on an unlabelled PR, and that is a decision rather than an accident** (#22, closed; the reasoning is `misc/docs/adr_ci_verification_perimeter.md`). Rendering tests need Playwright (in the `cutout` extra) and ffmpeg (not on the runner image): measured on Linux, 45 s of tests behind ~34 s of setup, against a ~50 s CI leg. So the lane lives in `.github/workflows/browser-tests.yml` and runs **on demand, or on any PR carrying the `run-browser-tests` label**.
  - **You can add that label yourself** — `gh api -X POST repos/thorwhalen/an/issues/<N>/labels -f 'labels[]=run-browser-tests'`, **not** `gh pr edit --add-label`, which on these repos prints a projects-classic error, exits 0, and applies nothing — and you should, whenever a PR can change a pixel: the runtime under `an/data/cutout_runtime/`, the cutout compiler or serializer, the render path, the vendored engine, the ffmpeg flags, or the character rig. Nothing else in CI can see any of that.
  - **Standing rule: never write that a rendering behaviour is "verified in CI".** It is verified on a developer machine, on a labelled PR, or on an on-demand run. Say which.
  - What the gate is *not* allowed to do: vanish. The previous arrangement put `pytest.importorskip("playwright...")` at module level in eleven files, which does not skip a browser test — it aborts the module import, so its tests are never collected. 472 tests collected with Playwright, 438 without, and **13** of the 34 casualties needed no browser at all (every `an.verify.media` SSIM test among them). Gating is now `@pytest.mark.browser` applied after collection, with a run-summary line reporting how many rendering tests actually ran — an observation, not `total - skipped`, which is a collection-time prediction and was wrong for `-m`, `-k` and `--collect-only`.
  - **The guard does not make that bug impossible, and must not claim to.** An adversarial review reintroduced it four ways past an earlier draft of the guard (an ffmpeg-keyed module skip, a `collect_ignore`, a class-body probe, markers swapped for hand-rolled skipifs). What holds the line is `tests/test_browser_gate.py::test_collection_does_not_depend_on_the_environment`, which shadows every optional import **and** strips the external binaries from `PATH`, then compares pytest's own node-id sets — a reference outside the guard, so it catches routes nobody enumerated. The AST scanner is a list of known spellings and is the weaker half. All 28 guards are mutation-tested, against 20 mutations including those four routes.
- **There is a second, unrendered scene evaluator.** `an/adapters/cutout/{scene,timeline,pose,clip,channel,transform}.py` form a closed cluster that nothing on the render path imports — the path is `compile.py → serialize.py → render.py → runtime.js`. Worse, `runtime.js` cites `clip.py::_wrap_time` as "the spec … must stay bit-identical to it" about a function that never executes. Resolve this BEFORE building swap channels, or the same capability gets implemented into two or three models at once.

## What never to do

- Never edit `ir/scene.json` by hand — edit `scene.md` and run `sync`, or assign through the store (`mall["scenes"]["main"] = scene_ir`, which writes both files and equalizes their mtimes).
- Never break the md/json mtime equalization in `ScenesStore.__setitem__`. `sync()`'s "newer wins" tolerance band depends on it; without it sync flip-flops on every load and pipeline-injected state (audio refs, viseme tracks) is silently stripped.
- Never claim a render produced something it didn't. `render_project()` returns the mp4 path; when it fails, surface the renderer's actual error.
- Never use `pip install <name>` for a local-ecosystem package; this is `pip install -e <path> --no-deps`.
- Never bump `SCHEMA_VERSION` without registering a migration in `an/ir/migrate.py`.
- Never introduce a bare `NotImplementedError` as a placeholder — there are currently zero in `an/`, and stubs carry typed, install-hinting errors instead. Keep it that way.

## CI: what a green tick now covers

- **Linux, both Python legs, and Windows** — all blocking, and Windows now gates
  the **release** too (`publish` has a `needs` edge on it). Both are deliberate
  deviations from the generated wads template, removed in #22.
- **What `continue-on-error: true` actually did**, stated precisely because the
  first attempt at this line got it wrong: it did **not** make GitHub misreport
  the job. On every failing run the job conclusion, the step conclusion and the
  check-run row in the PR checks list all read `failure`. It changed the
  **roll-up** — the workflow *run* concluded `success`, so the aggregate tick was
  green and nothing blocked the merge. The signal was non-blocking, not hidden,
  which is worse in practice because a reviewer reads the aggregate. That is how
  #21's path-separator bug and an unpinned `read_text()` encoding reached `main`.
- If `wads populate` ever regenerates `.github/workflows/ci.yml`, re-apply both
  deviations; the comments there say so, and the upstream knob is i2mint/wads#66.
- **Not covered by default: anything that renders a pixel.** Add the
  `run-browser-tests` label to the PR and it is. See the browser-lane entry in
  *Genuine gaps* above, and `misc/docs/adr_ci_verification_perimeter.md` for why
  the perimeter is drawn where it is.

## Per-PR housekeeping

- Append a one-line entry under today's date in `misc/CHANGELOG.md`.
- If the change moves the system's actual shape, update `misc/docs/architecture_as_built.md` in the same PR — it is the doc everything else routes to.
- Update the matching skill in `.claude/skills/` if the user-facing surface changed.
- Log non-trivial unblessed design decisions: in the PR description for repo-level work, in `.an/decisions.jsonl` (via `mall["decisions"]`) for project-level work.
