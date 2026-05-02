---
name: an
description: Use whenever the user wants to author, edit, render, or iterate on a structured animation, cartoon, explainer video, or motion graphic via the an Python package. Triggers on "make a cartoon", "animate", "render a scene", "let's build a video", "an init", "an validate", "an render", or any request that maps to the chat-driven director workflow.
---

# an — top-level orchestrator (v0.1)

`an` is a Python package that turns a directorial chat conversation into rendered video. The user is the **director**; you are the **assistant orchestrator**; backends (cutout, Manim, Remotion, whiteboard) are the **executors**.

## What works today (v0.1)

The full pipeline is wired and runs **without API keys** by default (offline TTS produces silent audio, offline lip-sync deterministically generates viseme tracks). Real speech via `ElevenLabsTTS` and accurate alignment via `RhubarbLipSync` plug in once the user sets them up.

CLI surface:

- `an init <dir>` — create a fresh project on disk.
- `an validate <dir>` — schema + semantic validation.
- `an sync <dir>` — reconcile `scene.md` ↔ `ir/scene.json` (newer file wins).
- `an render <dir> [--tts NAME] [--lipsync NAME]` — full pipeline: validate → audio → render per shot → ffmpeg-concat → `output/main.mp4`. TTS and lip-sync providers are pluggable: `--tts elevenlabs` (needs `ELEVEN_API_KEY`) for real speech, `--lipsync rhubarb` (needs the rhubarb binary) for accurate phoneme alignment. Defaults are offline. Switching providers auto-re-synthesizes the affected lines (the IR's stamped `audio_ref` / `viseme_ref` are content-hashes that include the provider name; mismatch with the configured provider triggers fresh synthesis).
- `an check` — diagnose system deps (ffmpeg, node, rhubarb, playwright, elevenlabs, manim).

Python surface (everything in `an.__all__`):

- Scene IR: `SceneIR`, `Shot`, `Dialogue`, `AssetRef`, `Camera`, `Resolution`, `Meta`.
- Composition: `sequence`, `parallel`, `delay`, `loop`, `tween`, `set_`, `play`, `flatten`.
- Project: `init`, `load`, `save`, `Project`, `build_project_mall`.
- Sync: `markdown_to_ir`, `ir_to_markdown`.
- Validation: `validate_schema`, `validate_semantic`.
- Diagnostics: `check_requirements`.
- Audio (in `an.audio`): `OfflineTTS`, `ElevenLabsTTS`, `OfflineLipSync`, `RhubarbLipSync`, `produce_audio_for_scene`.
- Verify (in `an.verify`): `LayoutLintVerifier`, `HumanInTheLoopVerifier`.
- Orchestrate (in `an.orchestrate`): `orchestrate(project_dir, ...) -> OrchestratorReport`.

Backends registered: `cutout` (real), `manim` (works if `manim` installed), `remotion` (skeleton), `whiteboard` (stub).

## When the user wants to make a video right now

1. **Use the `an-spec` skill** to interview them and produce a draft `scene.md` (characters, dialogue, art style, voice intent, pacing, camera).
2. **Run `an init <dir>`** in their target directory (creates the project tree).
3. **Write `scene.md`** with `yaml meta`, `yaml shot`, `yaml entities`, `dialogue` blocks. Reference characters by `AssetRef` (`{kind: character, id: charlie, store: characters, ref: charlie-v1}`); placeholder rect parts render automatically when the characters store has no rig info yet.
4. **Run `an validate <dir>`** and surface any findings (warnings about unresolved voice/character refs are fine if assets aren't promoted yet).
5. **Run `an render <dir>`** — produces `output/main.mp4`.
6. If the user has `ELEVEN_API_KEY` set + `pip install elevenlabs`, swap `OfflineTTS` for `ElevenLabsTTS` in the orchestrator call. Same for Rhubarb.
7. For iteration ("make Maya's laugh longer"): edit `scene.md` (or `mall["scenes"]["main"] = updated_scene`), then `an render` again. Audio + visemes are content-hash-cached so unchanged lines don't re-synthesize.

## When to consult docs

- For IR field semantics: `an/ir/schema.py` is the SSOT.
- For composition flatten semantics: `an/ir/compose.py` (has doctests).
- For audio pipeline: `an/audio/pipeline.py`.
- For cutout backend internals: `an/adapters/cutout/{compile,render,channel,clip,timeline}.py`.
- For backend research: the seven docs in `misc/docs/`. Read the matching one before designing or extending a subsystem.

## What to write to `.an/decisions.jsonl`

Whenever you make a non-trivial design decision the user hasn't blessed (asset choice, default style, durations, voice pick), append a decision entry via `mall["decisions"].append(kind=..., body=...)` and surface it in your next reply.

## What to never do

- Never write directly to `ir/scene.json` — edit `scene.md` and run `sync`, OR use `mall["scenes"]["main"] = scene_ir` (which writes both files and equalizes mtimes).
- Never inline large assets into the IR; reference them by store key via `AssetRef`.
- Never claim a render produced something it didn't — `an render` returns the mp4 path; if it fails, surface the actual error from the renderer.
- Never bypass the audio pipeline by manually constructing viseme tracks unless the user is debugging — `produce_audio_for_scene` runs automatically inside `render` when needed.
