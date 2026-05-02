---
name: an
description: Use whenever the user wants to author, edit, render, or iterate on a structured animation, cartoon, explainer video, or motion graphic via the an Python package. Triggers on "make a cartoon", "animate", "render a scene", "let's build a video", "an init", "an validate", "an render", or any request that maps to the chat-driven director workflow.
---

# an — top-level orchestrator

`an` is a Python package that turns a directorial chat conversation into rendered video. The user is the **director**; you are the **assistant orchestrator**; backends (cutout, Manim, Remotion, whiteboard) are the **executors**.

**Read `misc/docs/architecture_as_built.md`** for the canonical, current-state map of the system (modules, control flows, invariants, caching strategy). This skill summarizes what to do; the architecture doc explains how the system actually works.

## What works today

The full pipeline is wired and runs **without API keys** by default (offline TTS produces silent audio, offline lip-sync deterministically generates viseme tracks). Real speech via `ElevenLabsTTS`, word-aligned visemes via `WhisperLipSync`, full phoneme alignment via `RhubarbLipSync`, and free-text editing via `an iterate` (Claude Opus 4.7) plug in once the user sets the relevant env vars.

CLI surface:

- `an init <dir>` — create a fresh project on disk.
- `an validate <dir>` — schema + semantic validation.
- `an sync <dir>` — reconcile `scene.md` ↔ `ir/scene.json` (newer file wins).
- `an render <dir> [--tts NAME] [--lipsync NAME] [--parallel auto|N]` — full pipeline: validate → audio → render per shot → ffmpeg-concat → `output/main.mp4`. TTS and lip-sync providers are pluggable: `--tts elevenlabs` (needs `ELEVEN_API_KEY`) for real speech, `--lipsync whisper` (needs `faster-whisper`) for word-aligned visemes, `--lipsync rhubarb` (needs the rhubarb binary) for full phoneme alignment. Defaults are offline. Switching providers auto-re-synthesizes the affected lines. `--parallel auto` runs each shot in its own thread (Phase 11c; ~N× wall-time speedup on N-shot scenes; capped at min(shots, cpu, 4)).
- `an iterate <dir> "<instruction>"` — free-text edit. Sends the current scene + instruction to Claude (Opus 4.7 + adaptive thinking), parses a structured patch list, validates against the schema, persists the new scene to disk, and invalidates affected shots' caches so the next render only redoes those shots. Needs `ANTHROPIC_API_KEY`. Pass `--no-apply-changes` for a dry run.
- `an character new <name> [--seed S] [--style adventurer] [--offline]` — create a character at `assets/characters/<name>` with parts/, the 9-shape mouth set, and `character.json`. By default fetches a DiceBear avatar; `--offline` uses a deterministic geometric fallback (no network).
- `an character mouths <name>` — regenerate the 9-shape default mouth set (`mouth_a` … `mouth_x`).
- `an character validate <name>` — check parts, mouth set, pivots, descriptor.
- `an character silhouette <name> [--other <name2>]` — render a black silhouette PNG; with `--other`, also computes IoU between the two silhouettes (Disney silhouette test).
- `an character preview <name> [--open-browser]` — write `preview.html` cycling all 9 visemes with breath/head-tilt animation.
- `an character record <name> [--duration 8] [--width 640] [--height 480]` — record `preview.html` to mp4 via Playwright + ffmpeg. Produces `<character_dir>/preview.mp4` (or `--output PATH`). Real video file showing the new SVG art animating.
- `an check` — diagnose system deps (ffmpeg, node, rhubarb, playwright, elevenlabs, manim).

Python surface (everything in `an.__all__`):

- Scene IR: `SceneIR`, `Shot`, `Dialogue`, `AssetRef`, `Camera`, `Resolution`, `Meta`.
- Composition: `sequence`, `parallel`, `delay`, `loop`, `tween`, `set_`, `play`, `flatten`.
- Project: `init`, `load`, `save`, `Project`, `build_project_mall`.
- Sync: `markdown_to_ir`, `ir_to_markdown`.
- Validation: `validate_schema`, `validate_semantic`.
- Diagnostics: `check_requirements`.
- Audio (in `an.audio`): `OfflineTTS`, `ElevenLabsTTS`, `OfflineLipSync`, `WhisperLipSync`, `RhubarbLipSync`, `produce_audio_for_scene`, `make_tts`, `make_lipsync`.
- Verify (in `an.verify`): `LayoutLintVerifier`, `HumanInTheLoopVerifier`, `MediaQualityVerifier`, `VisionLMVerifier`.
- Orchestrate (in `an.orchestrate`): `orchestrate(project_dir, ...) -> OrchestratorReport`, `iterate(project_dir, instruction) -> IterateResult`.

Backends registered: `cutout` (real, with face rig + emotion-driven eyebrows + procedural blinks + bezier mouth shapes per viseme + environment backdrops), `manim` (works if `manim` installed), `remotion` (skeleton), `whiteboard` (stub).

## Markdown surface

`scene.md` supports these fenced blocks:

- ` ```yaml meta ` — title, duration, fps, resolution, default_style, notes.
- ` ```yaml shot ` — duration, camera (with `move: hold | push_in | pull_out | zoom_in | zoom_out`), options.
- ` ```yaml entities ` — list of AssetRef-shaped dicts. `kind` ∈ `character | environment | voice | style | prop`. Environment refs: `park | indoor | night | sunset | default`.
- ` ```yaml actions ` — list of `tween` / `set` / `play` action dicts. Optional `start` (seconds) wraps a leaf in `sequence(delay(start), action)` so flatten gives correct absolute times.
- ` ```dialogue ` — `speaker [emotion]: text` per line. Emotion is one of `neutral | happy | sad | angry | surprised | skeptical | amused | thinking` and drives eyebrow tilt during the line.

## When the user wants to make a video right now

1. **Use the `an-spec` skill** to interview them and produce a draft `scene.md` (characters, dialogue, art style, voice intent, pacing, camera).
2. **Run `an init <dir>`** in their target directory (creates the project tree).
3. **Write `scene.md`** with `yaml meta`, `yaml shot`, `yaml entities`, `dialogue` blocks. Reference characters by `AssetRef`; placeholder rect/ellipse parts with per-id distinct color palette render automatically when the characters store has no rig info yet.
4. **Run `an validate <dir>`** and surface any findings (warnings about unresolved voice/character refs are fine if assets aren't promoted yet).
5. **Run `an render <dir>`** for the offline default, or `an render <dir> --tts elevenlabs --lipsync whisper` for real speech with word-aligned visemes (best quality without external binaries).
6. For iterative tweaks, prefer **`an iterate <dir> "<plain-English change>"`** over hand-editing scene.md — it's the spec's signature loop. Cache invalidation is automatic; the next `an render` regenerates only the affected shots.

## When to consult docs

- **Start here**: `misc/docs/architecture_as_built.md` — module map, the 3 control flows (render / iterate / validate), key invariants, content-hash caching strategy.
- For IR field semantics: `an/ir/schema.py` is the SSOT.
- For composition flatten semantics: `an/ir/compose.py` (has doctests).
- For audio pipeline: `an/audio/pipeline.py`.
- For cutout backend internals: `an/adapters/cutout/{compile,render,channel,clip,timeline}.py`.
- For the iterate loop's prompt design: `an/iterate.py` (the system prompt + IterateResponse schema).
- For backend research and design rationale (NOT current state): the seven reports in `misc/docs/report*.md`. Read the matching one before designing or extending a subsystem.

## What to write to `.an/decisions.jsonl`

Whenever you make a non-trivial design decision the user hasn't blessed (asset choice, default style, durations, voice pick), append a decision entry via `mall["decisions"].append(kind=..., body=...)` and surface it in your next reply.

## What to never do

- Never write directly to `ir/scene.json` — edit `scene.md` and run `sync`, OR use `mall["scenes"]["main"] = scene_ir` (which writes both files and equalizes mtimes).
- Never inline large assets into the IR; reference them by store key via `AssetRef`.
- Never claim a render produced something it didn't — `an render` returns the mp4 path; if it fails, surface the actual error from the renderer.
- Never bypass the audio pipeline by manually constructing viseme tracks unless the user is debugging — `produce_audio_for_scene` runs automatically inside `render` when needed.
