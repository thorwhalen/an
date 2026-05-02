# Next-session handoff

> Read this first when starting a fresh session on the `an` repo. Then delete this file or move it to `misc/` — it's a one-shot pointer, not a permanent reference.

---

## Where things stand

The package was renamed from `anima` to `an` (PyPI conflict). Repo: <https://github.com/thorwhalen/an>. Local dir is still `/Users/thorwhalen/Dropbox/py/proj/t/anima` — the dir name doesn't match the package name; pip looks at `pyproject.toml` so it works fine. Don't waste a session renaming the local dir.

**340 tests pass, 4 conditionally skipped.** Live API tests run when `ELEVEN_API_KEY` and `ANTHROPIC_API_KEY` are set. The `/Users/thorwhalen/.keys` file the user has at home exports both via `source ~/.keys`.

Phase 11a (character authoring) shipped: `an.characters` package + `an character {new,mouths,validate,silhouette,preview}` CLI. Renderer-side integration (Pixi SVG-texture path in `runtime.js`) is the next phase — characters can be authored and previewed but the existing procedural rig is still what `an render` uses.

## What works end-to-end today

| Feature | How to use it |
|---|---|
| Project init | `an init <dir>` |
| Render with offline defaults (silent audio + char-distributed visemes) | `an render <dir>` |
| Real ElevenLabs speech | `an render <dir> --tts elevenlabs` |
| Word-aligned visemes via faster-whisper | `an render <dir> --lipsync whisper` |
| Free-text edit ("make Maya laugh longer") | `an iterate <dir> "<instruction>"` |
| Validate before render | `an validate <dir>` |
| Diagnose missing system deps | `an check` |
| Markdown blocks: `yaml meta`, `yaml shot`, `yaml entities`, `yaml actions`, `dialogue` (with optional `[emotion]` per line) |

Three example projects in `examples/` all render with audible speech + visible characters: `single_character/`, `walk_demo/`, `park_bench_cartoon/`.

## How to come up to speed quickly

1. Read `README.md` (under 5 minutes — gives the user-facing surface).
2. Read `misc/docs/architecture_as_built.md` (~10 minutes — gives the as-built map: modules, control flows, invariants, caching strategy).
3. Skim `misc/CHANGELOG.md` for what shipped when (Phase 1 → Phase 10).
4. If working on the `an` repo itself: load the `an-dev` skill. If using `an` from a downstream project: load the `an` skill.

You do NOT need to read the seven research reports in `misc/docs/report*.md` unless you're extending a specific subsystem. They cover the design space the system was built against, not what was built.

## What I'd build next (priority order)

The user said keep going as far as possible. The post-Phase-10 priorities I'd recommend:

1. **Real character art pipeline.** Replace the placeholder stick-figure rig with SVG character art loaded from `mall["characters"]/<id>/`. The user has saved a focused research prompt at `~/Downloads/an_character_art_research_prompt.md` and wants to feed external research (Vancouver-style, deep-dive) into the design before building. Wait for that research; don't start building character art without it.

2. **Per-shot parallel rendering.** Currently shots render serially (~3-5s per shot). Parallelizing across shots could 4-8× throughput. The cutout renderer is already pure (each shot is independent given mall reads); the bottleneck is Playwright/Chromium boot time.

3. **Live preview.** A small `an preview <dir>` that opens the runtime HTML in a browser pointing at the current scene JSON, with hot-reload on `scene.md` save. Lets the director iterate visually without re-rendering for every tweak.

4. **Asset promotion.** `an.assets.promote(scene='park.md', entity='maya', as_='maya-v1')` — copy a scene-inlined character into `mall["characters"]/maya-v1/` with a stable id so it's reusable across scenes. The descriptor shape needs to grow to support real art (mouth set, eye-blink set, hair, optional skin variants). Likely co-developed with #1.

## Useful one-liners

```bash
# Source API keys (user has these in ~/.keys)
source ~/.keys

# Activate the local-package-ecosystem editable install (already done if pip show an works)
pip show an

# Quick smoke test
an check                                                      # all deps
an validate examples/park_bench_cartoon                       # IR is clean
an render examples/park_bench_cartoon --tts elevenlabs --lipsync whisper

# Run the full test suite
python -m pytest tests/ --doctest-modules an/ -q

# Test specific subsystems
python -m pytest tests/test_iterate.py -v
python -m pytest tests/test_media_quality_verifier.py -v

# Iterate via CLI
an iterate examples/park_bench_cartoon "Make Maya's response more affectionate"
```

## Risks / known fragilities

- **`anima*` JS API names**: the global API in `an/data/cutout_runtime/runtime.js` was renamed `anima*` → `an*` during the package rename. Python (`render.py`) and JS (`runtime.js`) must stay synced. Both currently use `anLoadScene` / `anSetTime` / `anCanvasReady`.
- **`scene.md` ↔ `ir/scene.json` sync**: `ScenesStore.__setitem__` equalizes mtimes so the next `sync()` doesn't flip-flop. If you write to either file outside the store, this can break.
- **Audio cache extension**: cached audio files end in `.wav` even when the bytes are mp3 (ElevenLabs returns mp3). The renderer sniffs format from magic bytes. If you rename or normalize cache filenames, keep this behavior.
- **CI auto-version-bump**: every push to main triggers a wads CI commit that bumps the version. Local `pyproject.toml` lags one commit behind; rebase before pushing or you get rejection on every push.
- **Dropbox**: the project lives in Dropbox, which sometimes locks files mid-write. Rare but happens. If a write fails inexplicably, retry once.

## What the user will probably ask next

- "Ingest this character-art research and start building the art pipeline" — wait for the research result the user is going to feed back from the prompt at `~/Downloads/an_character_art_research_prompt.md`.
- "Make the render parallel / faster" — Phase 11 work.
- "Add an `an preview` command" — small but high UX value.

## Recent commit history (top of `main`)

```
769cd5a  Phase 10 followup: skill doc lists --lipsync whisper + an iterate
21d00d4  Phase 10: iterative edit loop — `an iterate "<free-text instruction>"`
28d5cf3  Phase 9: WhisperLipSync + MediaQualityVerifier + VisionLMVerifier
9580536  Phase 8: better characters (faces, blinking, emotions, backgrounds) + media QA
54fd968  Add --tts and --lipsync CLI flags with auto-resynth on provider change
9111665  Mux audio into rendered mp4 (silent base + dialogue overlays)
ceb1b75  Fix park_bench static-image bug: 3 root causes
```

Goodnight.
