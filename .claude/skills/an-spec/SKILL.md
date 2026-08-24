---
name: an-spec
description: Use when the user is starting a new an scene and needs help dialogically developing a scene specification — clarifying questions about characters, dialogue, art style, voices, pacing, and camera. Triggers on "let's design a scene", "I want to make a video about…", "help me sketch a cartoon", or any open-ended creative request handed to an.
---

# an-spec — interviewing the director

You're helping the user develop a scene specification that will end up as `scene.md` (Narrative Layer) inside an an project. Your job is to ask the right questions, propose sensible defaults, and confirm before writing.

## Sequence

1. **Clarify the goal in one sentence.** "A 45-second 2D cartoon, two characters on a park bench" — get this nailed before anything else.
2. **Ask only what you can't reasonably default.** Don't quiz the user on fps unless they have a strong opinion. Defaults: 30 fps, 1920×1080, cutout style, 30–60s duration.
3. **Walk the structural questions.**
   - Cast: how many characters, named or generic, art-style hint
   - Setting: one environment or several, time of day
   - Beats: what happens in order
   - Dialogue: do characters speak, what about, voice tone
   - Camera: static or moves
4. **Check the asset library** before generating new things. If `mall["characters"]` already has a "Maya", offer to reuse her instead of inventing a new character. Same for environments, voices, styles.
   - **Also check each reused character's `asset_sets`** — declared swap sets (`hands`, `body_facing`, `eyelid`, …) turn direction like "Maya turns to face left" or "she points" into a NAMED-KEY change rather than a transform the director has to invent: `{kind: set, target: maya/torso, property: body_facing, value: left, at: 3.5}`. Offer the declared keys by name; never author a swap on a set or key the descriptor does not declare (compile refuses it, listing the declared ones).
5. **Echo the spec back as a draft `scene.md`** for approval before writing anything to disk.

## What to ask only when relevant

- **Voice:** ask only if dialogue is in scope. Defer to Phase 3 for actual TTS — at Phase 1, just record voice intent in the spec.
- **Timing per shot:** propose totals; offer to break into shots only if the user wants control.
- **Style:** assume cutout unless the request is mathematical (lean Manim) or purely typographic / motion-graphics (lean Remotion).

## What NOT to ask

- File paths, fps, resolution — defaults work.
- Backend choice — the orchestrator picks based on style.
- Whether to render — Phase 1 doesn't render. Don't promise it.

## Confirm-before-write

Always:

> "Here's the spec I have. Should I write this to `scene.md` and run `an validate`?"

If the user pushes for more detail, recurse on whichever section needs it. If they push to render, explain Phase 1 status and offer to wait.
