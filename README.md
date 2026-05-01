# an

AI-driven structured animation in Python. The user is the **director**; the AI agent is the **assistant orchestrator**; existing animation libraries (a custom 2D-cutout runtime, Manim, Remotion) are the **executors**.

```bash
pip install an
```

> Renamed from `anima` (PyPI conflict). The package and CLI are now `an`. Repo: <https://github.com/thorwhalen/an>.

---

## What works today (v0.1)

`an` lets you describe a scene in natural language inside a Claude Code session and have it rendered as an mp4 — including dialogue with offline lip-sync, **without any API keys** (offline TTS produces silent audio of the right length, offline lip-sync deterministically generates viseme tracks). Real speech via ElevenLabs and accurate phoneme alignment via Rhubarb plug in once you set them up.

```bash
an check                                # diagnose backend system deps
an init my-scene                        # create a fresh project
# edit scene.md ...
an validate my-scene                    # schema + semantic validation
an render my-scene                      # → output/main.mp4
```

## 30-second tour

A project is a small directory:

```
my-scene/
├── scene.md            # human Markdown — what you and the agent edit
├── ir/scene.json       # Pydantic-validated SSOT (auto-synced)
├── assets/             # characters, environments, voices, styles
├── artifacts/          # audio, viseme tracks, per-shot mp4s
├── output/             # final mp4s
└── .an/                # agent decisions log + memory
```

Authoring lives in `scene.md`:

```markdown
# Park Bench

```yaml meta
title: Park Bench
duration: 6
fps: 24
resolution: { width: 640, height: 360 }
```

## Shot s1 (cutout)

```yaml entities
- { kind: character, id: charlie, store: characters, ref: charlie-v1 }
- { kind: character, id: maya, store: characters, ref: maya-v1 }
```

```dialogue
charlie: Did you ever wonder why we always meet here?
maya: Because the pigeons trust us.
```
```

Then `an render my-scene` produces an mp4 with two characters and animated mouths over the dialogue lines. Defaults are entirely offline — to swap in real speech, set `ELEVEN_API_KEY` and use `ElevenLabsTTS`; to swap in real phoneme alignment, install `rhubarb-lipsync` and use `RhubarbLipSync`.

## 3-minute tour

`an` separates a scene into three layers:

1. **Narrative** — `scene.md`. Human Markdown with structured fenced blocks (`yaml meta`, `yaml shot`, `yaml entities`, `dialogue`). What you and the agent edit.
2. **Scene Graph** — `ir/scene.json`. Pydantic-validated, renderer-agnostic. The single source of truth for tooling. Diffable. Pipeline stages (audio synthesis, lip-sync) write into the JSON; `an sync` keeps it consistent with the Markdown using mtime-newer-wins.
3. **Render Code** — generated per-backend (cutout JSON for the JS runtime, Manim Python, Remotion TSX). Disposable; never edited by hand.

Composition is fluent in Python and flattens to a canonical timeline:

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

Persistence goes through a project mall — a dict of dol-backed `MutableMapping`s:

```python
from an import build_project_mall

mall = build_project_mall("my-scene", ensure=True)
mall["voices"]["maya-warm"] = {"provider": "elevenlabs", "voice_id": "..."}
scene = mall["scenes"]["main"]   # returns a SceneIR
```

End-to-end orchestration:

```python
from an.orchestrate import orchestrate

report = orchestrate("my-scene")
# report.success: bool, .output_path: Path, .validation, .verifications
```

## Architecture

| Subsystem | Implementation |
|---|---|
| Renderer Protocol | `an.adapters.Renderer` |
| Cutout backend | `an.adapters.cutout` — Python compiles to JSON; PixiJS in headless Chromium screenshots frames; ffmpeg muxes mp4 |
| Manim / Remotion / Whiteboard | Skeleton implementations (Phase 6) |
| TTS Protocol | `OfflineTTS` (default), `ElevenLabsTTS` |
| Lip-sync Protocol | `OfflineLipSync` (default), `RhubarbLipSync` |
| Verifier Protocol | `LayoutLintVerifier`, `HumanInTheLoopVerifier` |
| Persistence | dol-backed `MutableMapping`s organized into `build_project_mall(...)` |
| CLI | `argh`-based dispatch over `an.tools._dispatch_funcs` |

## What's not in v0.1

- 3D animation, generative video, interactive output, SaaS hosting, music/sound-effect generation, in-house GUI, or editing of pre-existing video footage. `an` synthesizes; it does not cut.
- The Manim / Remotion / Whiteboard backends are skeletons — they implement the Protocol and route correctly, but only the cutout backend produces real video at v0.1.
- The orchestrator's free-text iterative edit loop ("make Maya's laugh longer and warmer") is driven by the `an` Claude Code skill calling into the `orchestrate` primitives — there's no standalone implementation in pure Python.

## Reference

- Architectural pillars and per-subsystem reading order: `CLAUDE.md`.
- Deep design reports (~250 KB total): `misc/docs/`.
- The skills the agent uses to drive `an`: `.claude/skills/`.
- Examples: `examples/single_character/` (simplest renderable scene), `examples/park_bench_cartoon/` (two-character dialogue demo).
