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
an render my-scene --parallel auto                        # per-shot threads (~N× speedup on N-shot scenes)
an render my-scene --step-hz 15                           # tweens "on twos" (15 pose updates/s at 30 fps); camera stays smooth
an preview my-scene                                       # live in-browser preview; reloads on edit
an iterate my-scene "make Maya laugh longer and warmer"   # free-text → IR patch → invalidate caches
an render my-scene                                        # re-renders only the affected shot

# Character authoring (Phase 11a)
an character new maya --seed maya-warm                    # generate a DiceBear-backed character
an character new bob --offline                            # offline-only: deterministic geometric fallback
an character mouths maya                                  # regenerate the 9-shape mouth set + its viseme@happy / viseme@sad variants
an character add-gaze maya                                # give an older character the sclera/pupil/lid eye stack (new ones have it)
an character validate maya                                # check parts, mouth set, pivots
an character silhouette maya --other bob                  # silhouette test (IoU score)
an character preview maya --open-browser                  # HTML viewer cycling all 9 visemes
```

The defaults run without any API keys: offline TTS produces silent audio of the right length, offline lip-sync deterministically generates viseme tracks. To get real audible speech, set `ELEVEN_API_KEY` and pass `--tts elevenlabs`. For word-aligned mouth shapes, `pip install faster-whisper` and pass `--lipsync whisper`. For free-text editing via `an iterate`, set `ANTHROPIC_API_KEY`.

---

## A 30-second tour

A project is a small directory:

```
my-scene/
├── scene.md            # human Markdown — what you and the agent edit
├── ir/scene.json       # Pydantic-validated SSOT (auto-synced)
├── assets/             # characters, props, environments, voices, styles
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

`an render` produces an mp4 with two visually-distinct characters (per-id palette: skin/clothing/hair), animated mouths over the dialogue lines, eye-blinks every ~4 seconds, brows, lids and mouth form driven by each line's `[emotion]` (the face solver), a sky/grass park background, and a slow camera push-in.

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
mall["scenes"]["main"]  # returns a SceneIR
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
| **Character rig** | Ellipse head + per-id palette (skin/clothing/hair) + eyebrows and eyelids driven by the compile-time face solver (`expression` actions, ten presets, the dialogue `[emotion]` sugar; `viseme@<form>` mouth-set variants) + eyes as a sclera/pupil/lid stack (the pupils follow `gaze_x`/`gaze_y` and make seeded ambient saccades; blinks compiled as channels — an eyelid swap where the rig has closed-eye art, a squash otherwise; `an character add-gaze` gives an older rig the stack) + bezier-curved mouth (9 viseme shapes) |
| **TTS Protocol** | `OfflineTTS` (silent placeholder), `ElevenLabsTTS` (real, needs `ELEVEN_API_KEY`) |
| **Lip-sync Protocol** | `OfflineLipSync` (char-distribution), `WhisperLipSync` (word-aligned via faster-whisper), `RhubarbLipSync` (phoneme-aligned), `WordTimingsLipSync` (driven by an injected `WordTimingProvider` — skip transcription entirely when the caller already has authoritative word timings) |
| **Verifier Protocol** | `LayoutLintVerifier`, `MediaQualityVerifier`, `VisionLMVerifier` (Claude vision), `HumanInTheLoopVerifier` |
| **Persistence** | dol-backed `MutableMapping`s organized into `build_project_mall(...)` |
| **CLI** | `typer` dispatch over `an.tools._dispatch_funcs` (init / validate / sync / check / render / iterate / bench) |
| **Iterate loop** | `an.iterate` — Anthropic Opus 4.7 + adaptive thinking + structured JSON patches + path-based mutation + cache invalidation |

For a deeper as-built reference (module-by-module map, control flows, key invariants, content-hash caching strategy), see [`misc/docs/architecture_as_built.md`](misc/docs/architecture_as_built.md). The research reports next to it cover the design space the system was built against — they are not current state, and `wave1_verification.md` is (verified fact, with the URLs each licence was read at).

### Injecting word timings (no whisper redundancy)

When the caller already has authoritative word-level alignment data
(e.g. from a separate lyric-alignment pipeline), `an` can skip its
own transcription pass entirely:

```python
from an.audio import StaticWordTimings, WordTimingsLipSync
from an.orchestrate import orchestrate

timings = [("hello", 0.5, 1.0), ("world", 1.2, 1.8), ...]
lipsync = WordTimingsLipSync(StaticWordTimings(timings, label="my-aligner"))

orchestrate("my-scene", lipsync=lipsync)
```

`orchestrate(..., tts=, lipsync=, parallel=)` accepts either provider
name strings or instances. Plug in any `WordTimingProvider`
(structural protocol with `name: str` and `words_for(audio,
transcript=)` returning `(text, start, end)` tuples). `muvid` uses
this hook to feed `lacing` alignment-store timings straight into the
cutout pipeline.

---

## What's not in v0.1

- 3D animation, generative video, interactive output, SaaS hosting, music/sound-effect generation, in-house GUI, or editing of pre-existing video footage. `an` synthesizes; it does not cut.
- The Manim backend works for placeholder title cards but isn't doing real shot-to-Manim translation; Remotion + whiteboard are skeleton implementations that respond correctly to `can_render` but can't produce video yet.
- Lip-sync for face-baked characters. Characters whose descriptor declares
  `face_overlay: false` (DiceBear avatars, or any hand-declared baked face)
  carry their face inside the head SVG, so the
  compiler suppresses both the overlay mouth and the viseme channel — they speak
  without moving their mouths. Hand-rigged characters lip-sync normally; see
  `examples/promote_demo/`.
- Multi-scene projects — `"main"` is the only scene key supported today.

SVG character art *has* shipped — descriptors drive real sprites through
`svg_sprite` visuals, not the placeholder geometry an earlier version of this
list described.

---

## Reference

- [`misc/docs/architecture_as_built.md`](misc/docs/architecture_as_built.md) — module map, control flows, invariants, caching strategy.
- [`misc/docs/`](misc/docs/) — the design-space research reports, plus
  [`wave1_verification.md`](misc/docs/wave1_verification.md), which is *verified current
  fact* rather than design space: licences with the URLs they were read at, and the
  silent-discard inventory.
- [`misc/CHANGELOG.md`](misc/CHANGELOG.md) — phase-by-phase what shipped when.
- [`.claude/skills/`](.claude/skills/) — the skills the agent uses to drive the package
  (`an` to use it, `an-spec` to interview a director, `an-dev` to work on it, plus
  `an-dev-licensing` and `an-dev-runtime-assets` for the two things that fail silently).
- [`examples/`](examples/) — the canonical demo scenes. `single_character/` is the smallest
  thing that renders; `character_gallery/build.py` goes end to end from character creation.
