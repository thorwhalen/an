---
name: an-dev
description: Use when working *inside* the an repo itself — writing or modifying an's own Python code, tests, schemas, adapters, or skills. Triggers when CWD is the an repo and the task is implementation work (adding a renderer, fixing a schema bug, updating a store). Not for using an from a downstream project.
---

# an-dev — working inside the an repo

This skill orients you for engineering work *on* an. If you're using an from a project, see the `an` skill instead.

## Read these before designing

**Always start with `misc/docs/architecture_as_built.md`** — the canonical, current-state map of the system (modules, control flows, invariants, caching strategy). Read it before any non-trivial change.

For deeper subsystem design history, the seven research reports next to it cover the design space (NOT current state):

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
4. Path-based property targeting (`"charlie/torso/left_arm:rotation"`) for renderer-portability.
5. Setup pose plus deltas; slot/skin/animation separation. (Cutout adapter, Phase 2.)
6. Time in seconds (float) at the IR boundary; rational time only inside the audio pipeline where drift matters.
7. All external systems behind `Protocol`s (`Renderer`, `TTSProvider`, `LipSyncProvider`, `Verifier`).
8. All persistence via dol-backed `MutableMapping`s organized into the project mall.
9. Dispatch to interface: business logic is plain Python; CLI (argh) is dispatch only.
10. Verification is a swappable Protocol; same interface for human, lint, vision-LM, MoVer.
11. Caches are content-hash keyed (audio_ref, viseme_ref). Cache invalidation is by deletion (`del mall["shots"][shot_id]`). No cache versioning — keys are deterministic so collisions across versions are impossible.
12. **Equalize mtimes after writing both `scene.md` and `ir/scene.json`** in `ScenesStore.__setitem__`. Sync's "newer wins" tolerance band depends on this. Without it, sync flip-flops on every load and pipeline-injected state (viseme tracks, audio_refs) gets stripped.
13. The synthetic root container in the JS runtime is **not indexed** in `nodeIndex`. `compile_shot` emits target paths starting with the entity name (`charlie/head/mouth`); the runtime's `animaLoadScene` skips the root when populating `nodeIndex`.

## Module map (current state)

Read `misc/docs/architecture_as_built.md` for the full map. The pieces that didn't exist in the original spec:

- `an/iterate.py` — free-text → Claude (Opus 4.7) → IR patches (Phase 10)
- `an/audio/whisper_lipsync.py` — faster-whisper word timestamps → visemes (Phase 9)
- `an/audio/providers.py` — make_tts / make_lipsync factories (Phase 8)
- `an/verify/media.py` — ssim, detect_silence, audio_volume, extract_frames, transcribe (Phase 8)
- `an/verify/media_quality.py` — MediaQualityVerifier (Phase 9)
- `an/verify/vision.py` — VisionLMVerifier (Claude vision QA, Phase 9)
- `an/characters/` — character authoring tools (Phase 11a): Spine-shaped `CharacterDescriptor`, SVG utils, parametric 9-shape mouth generator, DiceBear client + envelope, idle-animation factories, silhouette test, `assets.promote`. Powers `an character {new,mouths,validate,silhouette,preview}`. Renderer integration (Pixi SVG-texture path) is the next phase — characters live but `runtime.js` still draws the procedural rig.

## How to wire a new TTS / LipSync / Verifier / Renderer

The four Protocols live in `an.audio.tts.TTSProvider`, `an.audio.lipsync.LipSyncProvider`, `an.verify._base.Verifier`, and `an.adapters._base.Renderer`. To add a new one:

1. Implement the Protocol in a new file under the matching subpackage.
2. Register it in the factory: `an.audio.providers.TTS_FACTORIES` / `LIPSYNC_FACTORIES`, or for renderers via `an.adapters._base.register_renderer(MyRenderer())` at module import.
3. Export it from the subpackage's `__init__.py`.
4. Add skip-if-deps-missing tests under `tests/test_<my>.py`.

## Code conventions

- Public API in `an.__all__` is **curated**. Internals get an underscore prefix.
- Keyword-only arguments past the 2nd or 3rd positional. No magic numbers — defaults at the top of the module they belong to.
- No globals, no service locators. Pass the mall in.
- Functional over OOP; OOP only for orchestrators and stateful sessions.
- Errors are informative and specific. Wrap subprocess errors at the facade boundary.
- Doctests for the public API; pytest for cross-cutting checks.

## Per-PR housekeeping

- Append a one-line entry under today's date in `misc/CHANGELOG.md`.
- If a non-trivial design decision was made without explicit user blessing, log it (in the PR description for repo-level work; in `.an/decisions.jsonl` for project-level work).
- Update the matching skill in `.claude/skills/` if the user-facing surface changed.

## When unsure about prior art

The user maintains a large local Python ecosystem (`~/Dropbox/py/proj/`). Before reinventing storage / dispatch / etc., check:

- `dol` — for any `MutableMapping`-shaped persistence.
- `argh` — for CLI dispatch (see `~/.claude/skills/python-dispatching/SKILL.md`).
- The `python-storage`, `python-iterables`, `python-project-structure`, `python-dispatching` skills under `~/.claude/skills/`.
