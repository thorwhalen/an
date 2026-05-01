---
name: an
description: Use whenever the user wants to author, edit, render, or iterate on a structured animation, cartoon, explainer video, or motion graphic via the an Python package. Triggers on "make a cartoon", "animate", "render a scene", "let's build a video", "an init", "an validate", or any request that maps to the chat-driven director workflow.
---

# an — top-level orchestrator (Phase 1)

`an` is a Python package that turns a directorial chat conversation into rendered video. The user is the **director**; you are the **assistant orchestrator**; backends (cutout, Manim, Remotion, whiteboard) are the **executors**.

## Phase-1 reality check

The package is on Phase 1. **Today** you have:

- `an init <dir>` — create a fresh project on disk.
- `an validate <dir>` — schema + semantic validation of `scene.md` / `ir/scene.json`.
- `an sync <dir>` — reconcile the Markdown ↔ JSON pair.
- `an check` — diagnose system deps (ffmpeg, node, rhubarb, playwright, elevenlabs, manim).
- The full Scene IR (`an.SceneIR`, `an.Shot`, `an.Dialogue`, …).
- Composition combinators (`sequence`, `parallel`, `delay`, `loop`, `tween`, `set_`, `play`, `flatten`).
- A dol-backed project mall (characters, voices, environments, styles, scenes, artifacts, decisions).
- Renderer / TTS / LipSync / Verifier **protocols** but no concrete implementations yet.

You **cannot yet**: render an mp4, run TTS, run lip-sync. Those land in Phases 2–4.

## When the user wants to make a video right now

If the user says "make a cartoon" or similar, do the following in order:

1. **Use the `an-spec` skill** to interview them and produce a draft `scene.md`.
2. **Run `an init`** in their target directory.
3. **Edit the `scene.md`** to match the spec they approved.
4. **Run `an validate`** and surface any findings.
5. **Tell them honestly** that rendering is not in Phase 1 — show what the IR looks like, save it, and note that Phase 2 will wire up the cutout renderer.

Don't bluff a render. Don't write code that pretends to render. Phase 1 is about getting the substrate right.

## When to consult docs

- For IR field semantics: `an/ir/schema.py` is the SSOT.
- For composition flatten semantics: `an/ir/compose.py` (has doctests).
- For backend research: the seven docs in `misc/docs/`. Read the matching one before designing or extending a subsystem.

## What to write to `.an/decisions.jsonl`

Whenever you make a non-trivial design decision the user hasn't blessed (asset choice, default style, durations, voice pick), append a decision entry via `mall["decisions"].append(kind=..., body=...)` and surface it in your next reply.

## What to never do

- Never write directly to `ir/scene.json` — edit `scene.md` and run `sync` (or use `mall["scenes"]["main"] = scene_ir`).
- Never inline large assets into the IR; reference them by store key via `AssetRef`.
- Never invent a render — say "Phase 1 doesn't render yet" and stop.
