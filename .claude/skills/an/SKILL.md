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
- `an render <dir> [--tts NAME] [--lipsync NAME] [--parallel auto|N] [--strict-assets] [--step-hz N]` — full pipeline: validate → audio → render per shot → ffmpeg-concat → `output/main.mp4`. TTS and lip-sync providers are pluggable: `--tts elevenlabs` (needs `ELEVEN_API_KEY`) for real speech, `--lipsync whisper` (needs `faster-whisper`) for word-aligned visemes, `--lipsync rhubarb` (needs the rhubarb binary) for full phoneme alignment — its recognizer follows the language: `an render --language en` (the default) uses `pocketSphinx` with the transcript, any other tag `phonetic` without one; `make_lipsync("rhubarb", language=…)` in Python (an#96). Defaults are offline. Switching providers auto-re-synthesizes the affected lines. `--parallel auto` runs each shot in its own thread (Phase 11c; ~N× wall-time speedup on N-shot scenes; capped at min(shots, cpu, 4)). `--strict-assets` refuses to draw a **stand-in** for an asset the project's stores don't supply — the placeholder rig for a missing character descriptor, the default backdrop for an unknown environment ref. Without it you get a warning and a plausible render of a *different* picture (an#33); use it whenever the output is going to be measured or compared. `--step-hz N` (an#89) renders authored tweens **stepped** — pose updates N times a second on a shot-wide grid (every tween in a shot shares it; a tween's own start and end are pose changes too, so an off-grid start or end changes pose on that frame as well), so at 30 fps `15` is "on twos" and `10` "on threes" (the Spider-Verse look: characters on twos, camera and simulation on ones); it overrides the scene's `meta.step_hz` for this render, a shot's own `step_hz` still wins, and the camera, blinks, `play` clips and swap channels are never stepped. Default: smooth.
- `an iterate <dir> "<instruction>"` — free-text edit. Sends the current scene + instruction to Claude (Opus 4.7 + adaptive thinking), parses a structured patch list, validates against the schema, persists the new scene to disk, and invalidates affected shots' caches so the next render only redoes those shots. Needs `ANTHROPIC_API_KEY`. Pass `--no-apply-changes` for a dry run.
- `an preview <dir> [--shot ID] [--no-browser]` — live preview in a browser. Spins up an HTTP server, compiles the chosen shot (default: first), polls `scene.md` / `ir/scene.json` for changes, and the browser auto-reloads via a 500 ms `Last-Modified` poll. Lossy: visuals only, no audio. Honours the scene's / shot's `step_hz` (no flag of its own). Blocks until Ctrl-C. Use it for quick iteration on layout / blocking before `an render`.
- `an character new <name> [--seed S] [--style adventurer] [--offline]` — create a character at `assets/characters/<name>` with parts/, the 9-shape mouth set, and `character.json`. By default fetches a DiceBear avatar; `--offline` uses a deterministic geometric fallback (no network). **For production scenes with dialogue, prefer `--offline` or hand-rig a character following the Pose Animator convention (see `examples/promote_demo/`).** DiceBear avatars have eyes/brows/mouth baked into the head SVG, so the cutout adapter suppresses the overlay mouth + viseme channel for them — audio plays but the mouth doesn't move. Treat DiceBear as a bootstrap path only.
- `an character mouths <name>` — regenerate the 9-shape default mouth set (`mouth_a` … `mouth_x`).
- `an character validate <name>` — check parts, mouth set, pivots, descriptor.
- `an character silhouette <name> [--other <name2>]` — render a black silhouette PNG; with `--other`, also computes IoU between the two silhouettes (Disney silhouette test).
- `an character preview <name> [--open-browser]` — write `preview.html` cycling all 9 visemes with breath/head-tilt animation.
- `an character record <name> [--duration 8] [--width 640] [--height 480]` — record `preview.html` to mp4 via Playwright + ffmpeg. Produces `<character_dir>/preview.mp4` (or `--output PATH`). Real video file showing the new SVG art animating.
- `an check` — diagnose system deps (ffmpeg, node, rhubarb, playwright, elevenlabs, manim).

Python surface (everything in `an.__all__`):

- Scene IR: `SceneIR`, `Shot`, `Dialogue`, `AssetRef`, `Camera`, `Resolution`, `Meta`.
- Composition: `sequence`, `parallel`, `delay`, `loop`, `tween`, `set_`, `play`, `flatten`. **`play` resolves against the target character's descriptor `animations`** (an#7): `play("maya", "idle_breath")` compiles the seeded breath — three sine tracks, torso bob (±2 view-box px), head tilt (±0.5°, a quarter cycle behind) and a slower weight shift (±1.5 px), all on the animation's **6 s** cycle — into channels around the rig's rest pose; because `idle_breath` loops, a play with no `duration` runs to the **shot end** (a bounded loop is `duration=…`). `play("maya", "blink")` swaps the eyelids through the same swap path as compiled blinks. `loop=None` uses the animation's own `loop`. Resolution is `an.characters.play`, shared by `an validate` and the compiler, so both refuse the same plays with the same words: an undeclared name (the declared ones listed), a bone with no slot of its own, an unknown bone property, a frame naming art that is not on disk, a face slot suppressed by `face_overlay: false`.
- Project: `init`, `load`, `save`, `Project`, `build_project_mall`.
- Sync: `markdown_to_ir`, `ir_to_markdown`.
- Validation: `validate_schema`, `validate_semantic`.
- Diagnostics: `check_requirements`.
- Audio (in `an.audio`): `OfflineTTS`, `ElevenLabsTTS`, `OfflineLipSync`, `WhisperLipSync`, `RhubarbLipSync`, `WordTimingsLipSync` + `StaticWordTimings` (inject pre-computed `(text, start, end)` tuples — skips transcription entirely), `produce_audio_for_scene`, `make_tts`, `make_lipsync`. The `WordTimingProvider` protocol is the structural contract; any object exposing `name: str` + `words_for(audio, transcript=)` works as a provider.
- Verify (in `an.verify`): `LayoutLintVerifier`, `HumanInTheLoopVerifier`, `MediaQualityVerifier`, `VisionLMVerifier`.
- Orchestrate (in `an.orchestrate`): `orchestrate(project_dir, *, tts="offline", lipsync="offline", parallel=None, ...) -> OrchestratorReport`, `iterate(project_dir, instruction) -> IterateResult`. `tts` and `lipsync` accept either provider-name strings or instances — pass a `WordTimingsLipSync(...)` to inject pre-computed timings instead of running whisper.

Backends registered: `cutout` (real, with face rig + emotion-driven eyebrows + compiled blinks (an eyelid swap where the rig has closed-eye art, a squash otherwise — an authored eye channel overrides them) + bezier mouth shapes per viseme + swap channels + environment backdrops), `manim` (works if `manim` installed), `remotion` (skeleton), `whiteboard` (stub).

## Markdown surface

`scene.md` supports these fenced blocks:

- ` ```yaml meta ` — title, duration, fps, resolution, default_style, notes, and optional `step_hz` (stepped timing for tweens: `0 < step_hz <= fps`; `15` at 30 fps = "on twos").
- ` ```yaml shot ` — duration, camera (with `move: hold | push_in | pull_out | zoom_in | zoom_out`), options, and optional `step_hz` (overrides the scene's for this shot).
- ` ```yaml entities ` — list of AssetRef-shaped dicts. `kind` ∈ `character | environment | voice | style`. Environment refs: `park | indoor | night | sunset | default`.
  - **`kind: prop` is declared by the IR but NOT rendered** — the compiler raises rather than dropping it. Props land in Wave 7 of #9. Do not put one in a scene.
  - An environment override may only carry keys the renderer reads (`sky_color`, `ground_color`, `ground_y`); anything else raises rather than being silently discarded.
  - **A ref the stores can't supply gets a stand-in, and says so.** A character with no descriptor renders the placeholder rig; an environment ref that is neither a store entry nor a built-in preset (`park`/`indoor`/`night`/`sunset`/`default`) renders the default backdrop. Both warn, and both are recorded per entity in the compiled scene's `asset_resolution`. Pass `--strict-assets` to make them fatal.
- ` ```yaml actions ` — list of `tween` / `set` / `play` action dicts. Optional `start` (seconds) wraps a leaf in `sequence(delay(start), action)` so flatten gives correct absolute times. `{kind: play, target: maya, animation: idle_breath}` plays a descriptor animation (`duration`/`loop`/`speed` optional): a non-looping one fills its natural duration, a looping one with no `duration` runs to the shot end. Inside a `sequence`, a play without `duration` has **zero width** — the next sibling starts at the same instant — so give it an explicit `duration` when something must follow it.
  - **Transform properties:** `x`, `y`, `rotation`, `rotation_rad`, `scale_x`, `scale_y`, `skew_x`, `skew_y`, `pivot_x`, `pivot_y`, `alpha`. Any other property names a **swap set** (next bullets) and is refused at compile unless the target's descriptor declares it.
  - **`alpha` is the entrance/exit primitive** — it cascades, so a tween on the character root fades every part of it. `{kind: tween, target: charlie, property: alpha, to: 0.0, duration: 1.0}`.
  - **A `tween` with no `from` starts from the property's *rest* value**, which is `1.0` for `scale_x` / `scale_y` / `alpha` and `0.0` for the rest — not `0.0` for everything.
  - **A property outside the transform vocabulary names a SWAP SET** (an#87): `{kind: set, target: gale/left_hand, property: hands, value: fist, at: 1.0}` swaps that node's art to the `fist` key of the character's declared `hands` asset set, holding until the next action (set or tween) on the same target/property, or the shot end. The set and key must be **declared in the descriptor's `asset_sets`** — an undeclared name or key is refused at compile with the declared ones listed. `viseme` is just such a set (lip-sync drives it automatically); a procedural (descriptor-less) rig supports only `viseme` on its mouth. Swap channels are always step-interpolated — an authored easing on a swap tween is forced to `step` with a warning.
- **`Shot.narration` is declared by the IR and NOT implemented** — a shot carrying it raises. For a narrator, use a dialogue line whose speaker is not an entity in the shot: it gets audio and no lip-sync, and warns to say so.
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
