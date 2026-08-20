# Working in the an repo

This file orients an AI agent doing engineering work *on* an itself. If you're using an from a downstream project, see `.claude/skills/an/SKILL.md` instead.

**The canonical current-state map is `misc/docs/architecture_as_built.md`** — module map, the three control flows, load-bearing invariants, caching strategy. Read it before any non-trivial change. This file is only the orientation layer above it; when the two disagree, the code is authoritative and both get fixed.

## Where things live

- **Source:** `an/` (the package).
- **As-built architecture:** `misc/docs/architecture_as_built.md`. Sections 1–9 are current. Its §10 ("the four phases that haven't shipped yet") predates the character/parallel/preview work and is itself stale — three of its four items have since shipped, and it names `an.assets.promote`, which is really `an.characters.promote`. Fix that section when you touch it.
- **Reference research:** `misc/docs/` — seven deep reports plus the character-art upgrade plan. These describe the *design space*, not current state. Read the matching one before designing or extending a subsystem.
- **AI changelog:** `misc/CHANGELOG.md` — append a one-line entry under today's date when you finish a non-trivial chunk.
- **Project skills:** `.claude/skills/` — `an` (downstream orchestrator), `an-spec` (director interview), `an-dev` (dev-side; read it alongside this file).
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

## What never to do

- Never edit `ir/scene.json` by hand — edit `scene.md` and run `sync`, or assign through the store (`mall["scenes"]["main"] = scene_ir`, which writes both files and equalizes their mtimes).
- Never break the md/json mtime equalization in `ScenesStore.__setitem__`. `sync()`'s "newer wins" tolerance band depends on it; without it sync flip-flops on every load and pipeline-injected state (audio refs, viseme tracks) is silently stripped.
- Never claim a render produced something it didn't. `render_project()` returns the mp4 path; when it fails, surface the renderer's actual error.
- Never use `pip install <name>` for a local-ecosystem package; this is `pip install -e <path> --no-deps`.
- Never bump `SCHEMA_VERSION` without registering a migration in `an/ir/migrate.py`.
- Never introduce a bare `NotImplementedError` as a placeholder — there are currently zero in `an/`, and stubs carry typed, install-hinting errors instead. Keep it that way.

## Per-PR housekeeping

- Append a one-line entry under today's date in `misc/CHANGELOG.md`.
- If the change moves the system's actual shape, update `misc/docs/architecture_as_built.md` in the same PR — it is the doc everything else routes to.
- Update the matching skill in `.claude/skills/` if the user-facing surface changed.
- Log non-trivial unblessed design decisions: in the PR description for repo-level work, in `.an/decisions.jsonl` (via `mall["decisions"]`) for project-level work.
