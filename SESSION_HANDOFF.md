# Next-session handoff

> Read this first when starting a fresh session on the `an` repo. Then delete this file or move it to `misc/` — it's a one-shot pointer, not a permanent reference.

---

## Where things stand

The package was renamed from `anima` to `an` (PyPI conflict). Repo: <https://github.com/thorwhalen/an>. Local dir is still `/Users/thorwhalen/Dropbox/py/proj/t/an` — everything works, don't waste time renaming.

**349 tests pass, 4 conditionally skipped.** Live API tests run when `ELEVEN_API_KEY` and `ANTHROPIC_API_KEY` are set (in `~/.keys`; `source ~/.keys`).

### Phase 11 status (just shipped this session)

| Phase | What shipped | Commit |
|---|---|---|
| 11a | Character authoring tools: `an.characters` package + `an character {new,mouths,validate,silhouette,preview,record}` | `9ab119c`, `8d43db5` |
| 11b | Pixi SVG-texture runtime: `CharacterDescriptor` drives renders via Sprites + lip-sync texture swap | `e5fab5b` |
| 11c | Per-shot parallel rendering: `an render --parallel auto` (1.98× wall-time speedup measured) | `0bc0ae9` |

`examples/park_bench_cartoon` re-renders with two SVG characters (Charlie offline + Maya DiceBear); `examples/character_gallery/` ships three preview-recording videos at `videos/<name>.mp4`. The new `an.characters` is documented in the `an` and `an-dev` skills.

## What works end-to-end today

| Feature | How to use it |
|---|---|
| Project init | `an init <dir>` |
| Render with offline defaults | `an render <dir>` |
| Real ElevenLabs speech | `an render <dir> --tts elevenlabs` |
| Word-aligned visemes | `an render <dir> --lipsync whisper` |
| **Per-shot parallel rendering** | `an render <dir> --parallel auto` |
| Free-text edit | `an iterate <dir> "<instruction>"` |
| **Generate a character** | `an character new <name> [--seed S] [--style adventurer] [--offline]` |
| **Preview a character** | `an character preview <name> --open-browser` |
| **Record preview to mp4** | `an character record <name>` |
| **Silhouette test** | `an character silhouette <name> --other <name2>` |

## How to come up to speed quickly

1. `README.md` (5 min — user-facing surface).
2. `misc/docs/architecture_as_built.md` (10 min — module map, control flows, invariants).
3. Skim `misc/CHANGELOG.md` for what shipped when (Phase 1 → Phase 11c).
4. If working on the repo: load the `an-dev` skill. If using `an` from a downstream: load the `an` skill.
5. **Phase 11 implementation context**: read `misc/docs/Real Character Art for an — A 2D Cutout Pipeline Upgrade Plan.md` only if you're extending the character system. The architectural decisions (Spine-shaped IR, Pose Animator SVG convention, 9 Rhubarb mouths, sine-wave breath defaults) all came from there.

You do NOT need to read the other research reports unless you're extending a specific subsystem.

---

## Next-session work (decisions captured, prioritized)

### 1. Live preview — `an preview <dir>` (decided: Option A — cheapest)

A small command that lets the director iterate on `scene.md` without re-rendering an mp4 each time.

**Approach (decided):**
- `an preview <dir>` writes the per-shot runtime HTML to a temp dir under `<dir>/.an/preview/`, opens it in the default browser pointing at the current scene's JSON.
- Browser **polls** `scene.json` mtime every ~500 ms; reloads on change. No WebSocket.
- Lossy: shows the runtime view (PixiJS canvas with current scene), not the final muxed mp4.

**Why this and not WS:** directors care about iteration speed more than fidelity at the design stage; the existing `an render --parallel auto` already covers "see the actual output".

**Implementation sketch:**
- Re-use `_serve_dir` from `an/adapters/cutout/render.py` for the local HTTP server.
- Write a small JS poll loop in a new `preview.html` (separate from `runtime/index.html`): `fetch('scene.json')` every 500 ms, if mtime changed → call `anLoadScene(json)`.
- Wire as a top-level CLI: `an preview <dir> [--port N] [--shot <id>]`.
- Skip the audio mux; just play the visual.

**Effort:** ~1 hour. Add a single test that the command starts the server and writes the preview page.

### 2. Asset-promotion example (decided: just build it)

`an.assets.promote()` works in code (`an/characters/promote.py`) but no example exercises it. Build one:

- Add `examples/promote_demo/` with a hand-drawn SVG that follows the Pose Animator skeleton convention (`<g id="skeleton">` of named circles + `<g id="illustration">` with named part groups).
- A `build.py` that calls `an.assets.promote(project_dir, entity="raw_maya", as_="maya-v1")` and renders before/after.
- README explains the promote flow: hand-drawn → mall character.

**Effort:** ~1-2 hours. The hand-drawn SVG can be generated from the existing `wrap_dicebear_for_an` envelope as a starting point so you don't have to actually draw.

### 3. DiceBear overlay mismatch (decided: bootstrap-only, lock overlays off)

DiceBear avatars have eyes/brows/mouth baked into the head SVG. Phase 11b already suppresses the overlay eyes/brows when `metadata.art_provenance == "dicebear"`. The mouth overlay still attaches (it carries the lip-sync channel) but sits below the avatar's natural mouth which looks awkward during dialogue.

**Decision:** Treat DiceBear as a bootstrap path only. Document the limitation, lock the mouth overlay off too for DiceBear chars (lip-sync stays audio-only). The proper fix is to use hand-rigged characters for production work.

**What to change:**
- `an/adapters/cutout/compile.py` `_build_svg_character_subtree`: when `head_has_face` is True, also skip emitting the mouth visual + viseme channel. Add a comment pointing to this handoff doc.
- Update `an` skill to call out: "for production scenes with dialogue, hand-rig characters following the Pose Animator convention (see `examples/promote_demo/`)."
- Update `examples/park_bench_cartoon` to use offline characters (which DO render mouth animation) for both Charlie and Maya, OR add a dialogue-free shot that uses Maya as the DiceBear character.

**Effort:** ~30 min once #2 ships.

---

## Thresholds that change the call (parking lot — don't pre-build)

These are research-flagged improvements with explicit trigger conditions. Don't ship them until the trigger fires, otherwise they're premature.

| Improvement | Trigger condition | Effort |
|---|---|---|
| `mouth_smile` / `mouth_neutral` overlays (Adobe-style) | Lip-sync still looks wooden after a real-world scene with the 9 Rhubarb visemes | ~half day |
| Multi-skin support (different outfits/hairstyles per char) | A scene needs the same character in two outfits | ~day |
| TexturePacker atlas (one upload per character vs. per-part) | Frame rate drops below 24 fps in headless Chromium with 2+ characters | ~half day |
| Better silhouette test (vary body geometry) | Two characters in the same scene get IoU > 0.7 and read as visually similar | ~day; needs real hand-rigged chars first |
| Live2D-style mesh deformation (mouth) | Viseme texture-swap looks too jumpy at 30 fps | several days |
| Vision-LM face-landmark detection for DiceBear mouth alignment | User requests DiceBear lip-sync as production-grade | several days, hacky |

The research report (`misc/docs/Real Character Art for an — A 2D Cutout Pipeline Upgrade Plan.md`) §5.3 documents these in more detail.

---

## Useful one-liners

```bash
source ~/.keys                                              # API keys
pip show an                                                 # confirm editable install

# Smoke
an check
an validate examples/park_bench_cartoon
PYENV_VERSION=p12 an render examples/park_bench_cartoon --parallel auto

# Tests
python -m pytest tests/ --doctest-modules an/ -q

# Character authoring
PYENV_VERSION=p12 an character new maya --seed maya-warm
PYENV_VERSION=p12 an character preview maya --open-browser
PYENV_VERSION=p12 an character record maya       # writes preview.mp4

# Iterate
an iterate examples/park_bench_cartoon "Make Maya's response more affectionate"
```

`PYENV_VERSION=p12` is needed when you `cd` outside the project root because pyenv resolves a different version at `/tmp/`.

---

## Risks / known fragilities

- **`anima*` JS API names**: the global API in `an/data/cutout_runtime/runtime.js` was renamed `anima*` → `an*` during the package rename. Python (`render.py`) and JS (`runtime.js`) must stay synced.
- **`anLoadScene` is now async**: Phase 11b changed it. Python calls it via `page.evaluate("async (s) => { await window.anLoadScene(s); }", scene_dict)`. Don't drop the `async`/`await` or asset preloading silently fails.
- **PIXI.Assets needs HTTP, not file://**: the cutout renderer spins up a per-shot http.server in `_serve_dir`. file:// fetch is blocked in headless Chromium. If you change the render flow, keep the http server.
- **`scene.md` ↔ `ir/scene.json` sync**: `ScenesStore.__setitem__` equalizes mtimes so the next `sync()` doesn't flip-flop.
- **Audio cache extension**: cached audio files end in `.wav` even when the bytes are mp3 (ElevenLabs returns mp3). The renderer sniffs format from magic bytes.
- **CI auto-version-bump**: every push to main triggers a wads CI commit that bumps the version. Local `pyproject.toml` lags one commit; rebase before pushing.
- **CharactersStore filename**: `META_NAME = "character.json"` (Phase 11b). If you ever read characters via `mall["characters"][ref]` and get an empty/wrong dict, check that the on-disk file is `character.json`, not `meta.json`.
- **Dropbox**: the project lives in Dropbox, which sometimes locks files mid-write. Rare but happens. Retry once.

## What the user will probably ask next

In rough order of likelihood:

1. "Build the live preview." (Option A above; ~1 hour.)
2. "Build the promote example." (~1-2 hours.)
3. "Lock the DiceBear mouth overlay off and clean up the park_bench example." (~30 min.)
4. Something user-driven — a new scene, a real character they want hand-rigged.

## Recent commit history (top of `main`)

```
0bc0ae9  Phase 11c: per-shot parallel rendering — `an render --parallel auto`
e5fab5b  Phase 11b: Pixi SVG-texture runtime — CharacterDescriptors drive renders
8d43db5  Phase 11a follow-up: record character preview to mp4
9ab119c  Phase 11a: character authoring tools
0cb6bb5  Docs refresh: README + skills + as-built architecture + session handoff
769cd5a  Phase 10 followup: skill doc lists --lipsync whisper + an iterate
21d00d4  Phase 10: iterative edit loop — `an iterate "<free-text instruction>"`
```

Goodnight.
