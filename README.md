# an

AI-driven structured animation in Python. The user is the **director**; the AI agent is the **assistant orchestrator**; existing animation libraries (Manim, a custom 2D-cutout runtime, Remotion) are the **executors**.

```bash
pip install an
```


---

## What it does (and what it doesn't, yet)

`an` lets you describe a scene in natural language inside a Claude Code session and — when the cutout backend lands in Phase 2 — have it rendered as an mp4. The package itself is small: a Pydantic-validated **Scene IR** that is the single source of truth, a **dol-backed project mall** for persisting characters / voices / environments / artifacts, **protocols** for renderers / TTS / lip-sync / verification, and a **skill suite** that teaches the agent how to drive it all.

**Phase 1 (current):** the substrate. Scene IR, composition combinators, stores, CLI, project init, dev skills. **No rendering yet.**

**Phase 2–7 (roadmap):** cutout renderer, audio pipeline (ElevenLabs + Rhubarb), the v0.1 dialogue cartoon demo, verifier implementations, the iterative edit loop, and adapter smoke tests for Manim / Remotion / whiteboard.

---

## 30-second tour

```bash
# Diagnose backend deps before you start
an check

# Create a fresh project
an init my-scene
cd my-scene

# Edit scene.md in your editor of choice...
# Then validate
an validate .
```

A project is a small directory: a human-editable `scene.md`, a Pydantic-validated `ir/scene.json` (the SSOT), `assets/` (characters, environments, voices, styles), `artifacts/` (intermediate audio, viseme tracks, per-shot mp4s), and `output/` for finished renders.

## 3-minute tour

`an` separates a scene into three layers:

1. **Narrative** — `scene.md`. Human Markdown with structured fenced blocks (`yaml meta`, `yaml shot`, `dialogue`). This is what you and the agent edit.
2. **Scene Graph** — `ir/scene.json`. Pydantic-validated, renderer-agnostic. The single source of truth for tooling. Diffable.
3. **Render Code** — generated per-backend. Disposable. Never edited by hand.

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

Persistence goes through a project mall, a dict of dol-backed `MutableMapping`s:

```python
from an import build_project_mall

mall = build_project_mall("my-scene", ensure=True)
mall["voices"]["maya-warm"] = {"provider": "elevenlabs", "voice_id": "..."}
mall["scenes"]["main"]   # returns a SceneIR
```

Backends register against a `Renderer` Protocol; the orchestrator picks one per shot based on the shot's `style` (`"cutout" | "manim" | "motion_graphics" | "whiteboard"`).

## Designed-in but not yet shipped

- A `Verifier` Protocol with `HumanInTheLoopVerifier` and `LayoutLintVerifier` (Phase 5). Future verifiers (MoVer-style formal checks, vision-LM eyeballing) implement the same interface.
- A `TTSProvider` and `LipSyncProvider` Protocol with `ElevenLabsTTS` and `RhubarbLipSync` defaults (Phase 3).
- The `an` orchestrator skill that runs the spec → IR → render → verify → iterate loop (Phase 5).

## Reference

- Architectural pillars and per-subsystem reading order: `CLAUDE.md`.
- Deep design reports (~250 KB total): `misc/docs/`.
- The skills the agent uses to drive an: `.claude/skills/`.

## Non-goals

No 3D, no prompt-to-video generative models as primary renderer, no real-time / interactive output, no SaaS hosting, no music or sound-effect generation, no in-house GUI, no editing of pre-existing video footage. an synthesizes; it does not cut.
