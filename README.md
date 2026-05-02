# an

AI-driven structured animation in Python. The user is the **director**; an AI agent (Claude Opus 4.7) is the **assistant orchestrator**; existing animation libraries (a custom 2D-cutout runtime, Manim, Remotion) are the **executors**.

```bash
pip install an
```

Renamed from `anima` (PyPI conflict). Repo: <https://github.com/thorwhalen/an>.

---

## What works today

```bash
an check                                                  # diagnose backend system deps
an init my-scene                                          # create a fresh project
# edit scene.md ...
an validate my-scene                                      # schema + semantic validation
an render my-scene                                        # → output/main.mp4 (offline defaults)
an render my-scene --tts elevenlabs --lipsync whisper     # real speech, word-aligned visemes
an iterate my-scene "make Maya laugh longer and warmer"   # free-text → IR patch → invalidate caches
an render my-scene                                        # re-renders only the affected shot
```

The defaults run without any API keys: offline TTS produces silent audio of the right length, offline lip-sync deterministically generates viseme tracks. To get real audible speech, set `ELEVEN_API_KEY` and pass `--tts elevenlabs`. For word-aligned mouth shapes, `pip install faster-whisper` and pass `--lipsync whisper`. For free-text editing via `an iterate`, set `ANTHROPIC_API_KEY`.

---

## A 30-second tour

A project is a small directory:

```
my-scene/
├── scene.md            # human Markdown — what you and the agent edit
├── ir/scene.json       # Pydantic-validated SSOT (auto-synced)
├── assets/             # characters, environments, voices, styles
├── artifacts/          # audio, viseme tracks, per-shot mp4s (content-hash cached)
├── output/             # final mp4s
└── .an/                # decisions log + agent memory
```

Authoring happens in `scene.md`:

````markdown
# Park Bench

```yaml meta
title: Park Bench
duration: 12
fps: 24
resolution: { width: 640, height: 360 }
```

## Shot s1 (cutout)

```yaml shot
duration: 6
camera: { move: push_in }
```

```yaml entities
- { kind: environment, id: park_bg, store: environments, ref: park }
- { kind: character,   id: charlie, store: characters,   ref: charlie-v1 }
- { kind: character,   id: maya,    store: characters,   ref: maya-v1 }
```

```dialogue
charlie [thinking]: Did you ever wonder why we always meet here?
maya [amused]: Because the pigeons trust us.
```
````

`an render` produces an mp4 with two visually-distinct characters (per-id palette: skin/clothing/hair), animated mouths over the dialogue lines, eye-blinks every ~4 seconds, eyebrows tilted by emotion, a sky/grass park background, and a slow camera push-in.

---

## A 3-minute tour

`an` separates a scene into three layers:

1. **Narrative** — `scene.md`. Markdown with structured fenced blocks: `yaml meta`, `yaml shot`, `yaml entities`, `yaml actions`, `dialogue`. What you and the agent edit.
2. **Scene Graph** — `ir/scene.json`. Pydantic-validated, renderer-agnostic. The single source of truth. Diffable. Pipeline stages (audio synthesis, lip-sync) write into the JSON; `an sync` keeps it consistent with the Markdown using mtime-newer-wins.
3. **Render Code** — generated per-backend (cutout JSON for the JS runtime, Manim Python, Remotion TSX). Disposable; never edited by hand.

### Composition

Authoring is fluent in Python and flattens to a canonical timeline:

```python
from an import sequence, parallel, tween, delay, flatten

action = sequence(
    tween("charlie/torso", "rotation", to=10.0, duration=1.0),
    delay(0.5),
    tween("charlie/torso", "rotation", to=0.0, duration=1.0),
)
flat = flatten(action)
# [FlatAction(start=0.0, end=1.0, ...), FlatAction(start=1.5, end=2.5, ...)]
```

The same shape is also writable in markdown via a `yaml actions` block:

```yaml
- { kind: tween, target: charlie/torso, property: rotation, to: 10.0, duration: 1.0 }
- { kind: tween, target: charlie/torso, property: rotation, to: 0.0,  duration: 1.0, start: 1.5 }
```

### Persistence — the project mall

Everything long-lived (assets, artifacts, decisions, scene state) goes through a `dol`-backed `MutableMapping` mall:

```python
from an import build_project_mall

mall = build_project_mall("my-scene", ensure=True)
mall["voices"]["maya-warm"] = {"provider": "elevenlabs", "voice_id": "..."}
mall["scenes"]["main"]   # returns a SceneIR
```

### End-to-end orchestration

```python
from an.orchestrate import orchestrate

report = orchestrate("my-scene")
# report.success: bool
# report.output_path: Path
# report.validation:  ValidationReport
# report.verifications: list[VerificationReport] from each verifier
```

The default verifier chain runs `LayoutLintVerifier` (pre + post) and `MediaQualityVerifier` (post). Pass your own list to swap in `VisionLMVerifier` (Claude vision QA) or `HumanInTheLoopVerifier` (interactive approval).

### Free-text iteration (the spec's signature loop)

```python
from an.orchestrate import iterate

result = iterate("my-scene", "Make Maya's response a bit longer and more affectionate")
# result.summary:           "Extended Maya's reply..."
# result.patches:            [Patch(op='set', path='timeline/1/dialogue/0/text', value=...), ...]
# result.affected_shots:     ['s2']
# result.success / .error / .new_scene / .validation
```

The patches are validated against the schema and persisted; affected shots' cached mp4s are invalidated so the next `an render` regenerates only those.

---

## Architecture

| Subsystem | Implementation |
|---|---|
| **Renderer Protocol** | `an.adapters.Renderer` — Cutout (real), Manim (real if installed), Remotion (skeleton), Whiteboard (stub) |
| **Cutout backend** | `an.adapters.cutout.compile_shot` → `CutoutSceneJSON` → PixiJS v7 in headless Chromium → ffmpeg mux |
| **Character rig** | Ellipse head + per-id palette (skin/clothing/hair) + eyebrows (emotion-driven) + white-sclera eyes (procedural blinks) + bezier-curved mouth (9 viseme shapes) |
| **TTS Protocol** | `OfflineTTS` (silent placeholder), `ElevenLabsTTS` (real, needs `ELEVEN_API_KEY`) |
| **Lip-sync Protocol** | `OfflineLipSync` (char-distribution), `WhisperLipSync` (word-aligned via faster-whisper), `RhubarbLipSync` (phoneme-aligned) |
| **Verifier Protocol** | `LayoutLintVerifier`, `MediaQualityVerifier`, `VisionLMVerifier` (Claude vision), `HumanInTheLoopVerifier` |
| **Persistence** | dol-backed `MutableMapping`s organized into `build_project_mall(...)` |
| **CLI** | `argh` dispatch over `an.tools._dispatch_funcs` (init / validate / sync / check / render / iterate) |
| **Iterate loop** | `an.iterate` — Anthropic Opus 4.7 + adaptive thinking + structured JSON patches + path-based mutation + cache invalidation |

For a deeper as-built reference (module-by-module map, control flows, key invariants, content-hash caching strategy), see [`misc/docs/architecture_as_built.md`](misc/docs/architecture_as_built.md). The seven research reports next to it cover the design space the system was built against.

---

## What's not in v0.1

- 3D animation, generative video, interactive output, SaaS hosting, music/sound-effect generation, in-house GUI, or editing of pre-existing video footage. `an` synthesizes; it does not cut.
- The Manim backend works for placeholder title cards but isn't doing real shot-to-Manim translation; Remotion + whiteboard are skeleton implementations that respond correctly to `can_render` but can't produce video yet.
- Real character art (SVG, sprites) — characters today are stylized placeholder geometry. Real art is the next major upgrade; see the research prompt at `~/Downloads/an_character_art_research_prompt.md` for the open design questions.

---

## Reference

- [`misc/docs/architecture_as_built.md`](misc/docs/architecture_as_built.md) — module map, control flows, invariants, caching strategy.
- [`misc/docs/`](misc/docs/) — seven design-space research reports (~250 KB).
- [`misc/CHANGELOG.md`](misc/CHANGELOG.md) — phase-by-phase what shipped when.
- [`.claude/skills/`](.claude/skills/) — three skills (`an`, `an-spec`, `an-dev`) the agent uses to drive the package.
- [`examples/`](examples/) — `single_character/`, `walk_demo/`, `park_bench_cartoon/` — the canonical demo scenes.
