# Where things stand (overnight session summary)

> Read this once, then delete it (or move to `misc/`). It's a one-shot
> handoff doc for the morning after the unattended Phase 3-7 build.

## TL;DR

The package has been renamed `anima` → `an` (PyPI conflict), bumped to 0.1.0,
pushed to the new repo at <https://github.com/thorwhalen/an>, and Phases 3-7
are all in. The CLI works end-to-end without any API keys via the offline
TTS/lip-sync defaults.

```bash
an init mything           # create project
# edit mything/scene.md
an validate mything       # schema + semantic checks
an render mything         # → mything/output/main.mp4
an check                  # diagnose system deps
```

**238 tests pass, 4 conditionally skipped** (rhubarb-binary + ELEVEN_API_KEY).
CI bumped the live PyPI version to 0.1.1.

## What got shipped tonight

| Commit | What |
|---|---|
| `f829343` | Rename `anima` → `an`, version 0.1.0, new repo `thorwhalen/an` |
| `54554fe` | Cleanup: stray `.anima/` artifacts removed |
| `743d9b2` | **Phase 3** — audio pipeline (offline TTS + offline lip-sync defaults; ElevenLabs + Rhubarb when configured) |
| `6818543` | **Phase 4** — visemes drive cutout mouth slot; park_bench_cartoon renders |
| `6c82d5e` | **Phase 5** — `LayoutLintVerifier`, `HumanInTheLoopVerifier`, `orchestrate()` |
| `48d7906` | **Phase 6** — Manim/Remotion/Whiteboard adapter skeletons (Manim actually renders) |
| `f113c59` | (CI) black-formatted + bumped to 0.1.1 |
| `6abaadf` | **Phase 7** — README rewrite, skill update, empty-timeline warning |

## Verifications you can run cold

```bash
# from the repo root
python -m pytest tests/ --doctest-modules an/ -q   # 238 pass, 4 skip

an check                                            # all but rhubarb installed
an render examples/single_character                 # 3 KB mp4
an render examples/park_bench_cartoon               # 13 KB mp4 (2 chars + dialogue)
```

The two example mp4s in `examples/*/output/` (gitignored) show:
- `single_character/output/main.mp4` — one stick-figure character, 2s, no dialogue.
- `park_bench_cartoon/output/main.mp4` — two characters, 12s, dialogue lines with animated mouths over offline lip-sync.

## What I deliberately did NOT change

1. **Local directory name** stays `/Users/thorwhalen/Dropbox/py/proj/t/anima`. The git remote and package name are `an`, but moving the parent dir while files were active risked breaking the live editable install. Rename it manually with `mv anima an` plus updating `~/Dropbox/py/proj/my_packages.pth` if you want the names to match.
2. **No PyPI publish.** The CI pushed version 0.1.1 (auto-bump). If PyPI rejected because it's a new project, you may need to do the first publish manually.
3. **No real ElevenLabs / Rhubarb output.** The ElevenLabsTTS implementation is complete and will work the moment you `export ELEVEN_API_KEY=...`. RhubarbLipSync needs `brew install rhubarb-lipsync` then drop in `RhubarbLipSync()` instead of `OfflineLipSync()` in calls to `produce_audio_for_scene`.

## Interesting decisions I made without asking

| Decision | Reason | Easy to change |
|---|---|---|
| Default offline pipeline (silent WAV + deterministic visemes) | So the system is functional without API keys; honest about being silent. | Pass real providers to `produce_audio_for_scene(scene, mall, tts=ElevenLabsTTS(), lipsync=RhubarbLipSync())`. |
| Visemes mapped per character of transcript | Simplest correct algorithm; produces visible mouth motion. | Replace `OfflineLipSync._mapping` or write a smarter offline aligner. |
| Mouth shapes baked into JS runtime (`VISEME_SHAPES`) | Keeps the JSON contract small; runtime handles the geometry. | Edit `an/data/cutout_runtime/runtime.js` `VISEME_SHAPES`. |
| Stick-figure placeholder character (head/torso/arms/legs) | So renders are always *visible* without art assets. | Provide a real character via `mall["characters"]["charlie-v1"] = {...}` with custom `parts` list. |
| Sync mtime-newer-wins | Pipeline-injected JSON state would be lost otherwise. | `an/ir/sync.py:sync()` `if json_mtime > md_mtime`. |
| Loop-mode enum in JS runtime not yet wired | Cutout backend respects `loop_mode` in Python eval; JS side just plays once. Visemes don't loop, so harmless for v0.1. | Add `LoopMode` handling in `an/data/cutout_runtime/runtime.js` `evaluateTimeline`. |
| Manim renderer produces a "title card" only | Real shot-to-Manim translation is a Phase 7+ design effort. | Replace `_render_script` in `an/adapters/manim_adapter.py` with a fuller compiler. |

## What I'd do next (if I were you)

1. **Smoke the user journey from a totally cold start** — fresh shell, `pip install -e .`, `an init`, render. Should work.
2. **Try a real ElevenLabs voice.** Set `ELEVEN_API_KEY`, then in Python:
   ```python
   from an.audio import ElevenLabsTTS
   from an.audio.pipeline import produce_audio_for_scene
   from an.project import load
   p = load("examples/park_bench_cartoon")
   produce_audio_for_scene(p.scene, p.mall, tts=ElevenLabsTTS())
   p.mall["scenes"]["main"] = p.scene
   # then `an render examples/park_bench_cartoon`
   ```
3. **Pick the cutout demo's next visual upgrade.** A few low-cost wins:
   - Make characters move (add `tween("charlie/torso", "x", to=...)` actions).
   - Add a background environment (the entity kind exists in the schema; the cutout compiler ignores non-character entities today — easy add).
   - Wire camera moves (`shot.camera.move = "push_in"` is currently parsed but doesn't affect render).
4. **The orchestrator skill iteration loop** ("make Maya's laugh longer and warmer") is sketched in the `an` skill but isn't tied to a Python `iterate()` function — that's the next big interactive UX win.

## Risks / known fragilities

- The CI auto-pushes version bumps. Each merge to main triggers another bump; the local `pyproject.toml` lags behind by one number until `git pull`.
- `an/data/cutout_runtime/runtime.js` loads PixiJS from a CDN. First render needs internet for Chromium to fetch it; subsequent renders may use the browser cache. For fully offline rendering, vendor a copy of `pixi.min.js` under `vendor/` and update the `<script src>` in `index.html`.
- Rendering a single 1s shot at 12fps takes ~3s on this machine (mostly Chromium boot). A 12s scene took ~9s; longer scenes scale roughly linearly with frame count.
- The `wads` CI also pushes formatting changes. You'll see commits like `**CI** Formatted code + Updated version to X.Y.Z [skip ci]` in the history — they're harmless.

— Goodnight.
