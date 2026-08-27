# `an` — As-Built Architecture

> **What this is.** A snapshot of the system as it exists in the repo today, intended as the canonical reference for both human contributors and AI agents working in `an`. The seven research reports next to this file (`report 0...`, `report 1...`, etc.) describe the *design space*; this doc describes *what was actually built*. When the two disagree, the code is authoritative — fix this doc.
>
> Currency: written 2026-05-02, after Phase 10 (iterate loop). Update on each substantive change.

---

## 1. The story in 30 seconds

You write a `scene.md`. You run `an render <dir>`. An mp4 lands in `output/main.mp4` with audible dialogue, a sky/grass background, two distinct cartoon characters, animated mouths over real ElevenLabs speech aligned by Whisper word-timestamps, eye-blinks, faces driven by the expression solver (each line's `[emotion]`, or an `expression` action — brows, lids, mouth form, and the pupils' gaze with ambient saccades), and a slow camera push-in.

You say `an iterate <dir> "make Maya's response more affectionate"`. Claude (Opus 4.7) returns surgical JSON patches against the IR, validates them against the schema, persists, and invalidates only the affected shot's cache so the next render only redoes that shot.

Both flows pass through the same Scene IR — the single source of truth.

---

## 2. Three-layer IR (the architectural pillar)

Information flows downward. Verification feedback flows upward. Render Code is disposable.

```
Narrative Layer  scene.md                ← human-edited markdown (yaml meta, yaml shot, yaml entities, yaml actions, dialogue)
                       ↕ (an sync — newer-mtime wins, equalize on write)
Scene Graph      ir/scene.json           ← Pydantic-validated, the SSOT
                       ↓ (an.adapters.cutout.compile_shot)
Render Code      cutout JSON for JS      ← regenerated per render, never edited
                       ↓ (an.adapters.cutout.render — Playwright + ffmpeg)
                  output/main.mp4
```

The `iterate` loop closes the cycle: free-text → Claude → patches against `ir/scene.json` → re-render only affected shots.

---

## 3. Module map

```
an/
├── __init__.py              public API (curated __all__)
├── __main__.py              typer CLI entry point, wired programmatically
├── base.py                  type aliases, version constants, easing presets
├── util.py                  internal helpers (hashing, file I/O, time math)
├── tools.py                 user-facing CLI funcs + _dispatch_funcs
├── project.py               init / load / save Project + on-disk layout
├── render.py                project-level render orchestration + ffmpeg concat
├── orchestrate.py           validate → audio → render → verify; thin re-export of iterate
├── iterate.py               free-text → Claude (Opus 4.7) → JSON patches → IR mutation
├── check_requirements.py    diagnose ffmpeg/node/playwright/elevenlabs/manim/rhubarb/etc.
├── determinism.py           judges the runtime's determinism probe; enforced by default
├── live_api.py              the ONE "yes, this run may spend money" switch (an#63)
├── props.py                 PropDescriptor — a prop is NOT a character with a
│                            different kind: an unresolvable prop RAISES where a
│                            character falls back to the placeholder rig (an#108)
├── environments.py          EnvironmentDescriptor + Plane: list order is draw
│                            order, `depth` is Godot's parallax RATIO and governs
│                            TRANSLATION only, `characters_after` names the plane
│                            the characters stand in front of (an#110)
├── styles.py                StylePack — the styles store's first reader ever;
│                            REACHABLE_ROLES / UNREACHABLE_ROLES, checked against
│                            the literals read out of runtime.js (an#112)
├── preview.py               live-reloading browser preview; compiles WITH the pack
├── genre.py                 genre descriptors
├── credits.py               credits rendering
│
├── bench/                   the measurement instrument (an#36) — never imported by __init__
│   ├── corpus.py            fixtures + pinned render knobs; the render-path assertion
│   ├── capture.py           render one fixture into a throwaway copy
│   ├── imageio.py           the four PINNED ffmpeg decodes + the lossless re-encode
│   ├── masks.py             edge / flat / held / ring, all from the REFERENCE frames
│   ├── metrics.py           pure numpy, no I/O — runs unmarked in the default CI leg
│   ├── palette.py           derive the declared colour set; mirrors runtime.js's rule
│   ├── registry.py          the metric declaration table: family, side, per-mutation sign
│   ├── ledger.py            the three blocks, and the guards that keep them readable
│   ├── contract.py          scene_contract_sha256 — the comparability key
│   ├── png.py               filter-0 writer + full-filter reader; numpy + stdlib only
│   ├── golden.py            the golden gate and `--bless`; compares DECODED pixels
│   ├── compare.py           two rows in, a verdict or a REFUSAL out (an#40)
│   ├── mutations.py         the levers, through seams the shipped code has
│   ├── mutants.py           guard mutants as DATA, so the proof re-runs; a killed
│   │                        sweep restores (SIGTERM raises) and the next run names
│   │                        a leftover as one (SIGKILL cannot be caught) — an#67
│   ├── environment.py       the environment tuple, split by comparison scope
│   └── run.py               capture -> panel -> row
│
├── ir/                      Scene IR (the SSOT)
│   ├── schema.py            Pydantic models: SceneIR, Shot, Action, Dialogue, AssetRef, ...
│   ├── compose.py           sequence/parallel/delay/loop/tween/set_/play + flatten
│   ├── camera.py            camera_keys: the NINE moves and Camera.keys resolved
│   │                        by ONE function, which validate calls too — one table,
│   │                        not two reconciled by a test (an#109)
│   ├── validate.py          schema + semantic validation, ValidationReport
│   ├── migrate.py           versioned migration registry (chained); scenes are
│   │                        migrated on read by sync.scene_from_json_doc (an#105)
│   └── sync.py              markdown_to_ir / ir_to_markdown / sync (mtime-newer-wins)
│
├── stores/                  dol-backed project mall (MutableMapping facades)
│   ├── __init__.py          build_project_mall(project_dir) factory
│   ├── _common.py           JsonDirStore, JsonSidecarStore, _BlobStore base classes
│   ├── characters.py        sidecar-folder store (character.json + per-part art)
│   ├── props.py             sidecar-folder store (prop.json + per-part art) — the
│   │                        same shape, a different store, because the rig builder
│   │                        takes the store as an argument (an#108)
│   ├── environments.py      sidecar-folder store
│   ├── voices.py            JSON-only store
│   ├── styles.py            JSON-only store
│   ├── scenes.py            wraps scene.md + ir/scene.json pair (mtime equalization)
│   ├── artifacts.py         BlobStore: audio (.wav), visemes (.json), shots (.mp4),
│   │                        previews (.mp4), output (.mp4) — content-hash keyed
│   └── decisions.py         append-only JSONL log
│
├── adapters/                Renderer Protocol implementations
│   ├── _base.py             Renderer Protocol, RendererRegistry, RenderContext, RenderResult
│   ├── cutout/              the v0.1 backend (real)
│   │   ├── easing.py        named presets + cubic-Bézier + dispatcher
│   │   ├── channel.py       Keyframe, Channel, binary-search evaluation
│   │   ├── clip.py          Clip + LoopMode + Pose/merge_poses, evaluate(clip, t) -> Pose
│   │   ├── timeline.py      Track, PlacedClip, Timeline, evaluate_timeline -> Pose,
│   │   │                    timeline_from_scene (compiled doc -> evaluable Timeline)
│   │   │                    (these four are the EXECUTABLE SPEC of the runtime's
│   │   │                    evaluation — application is runtime.js only; the Python
│   │   │                    applier and scene graph were deleted in an#86, with
│   │   │                    node-backed parity tests pinning evaluateChannel+wrapTime)
│   │   ├── serialize.py     Pydantic models for the JS-runtime JSON contract
│   │   │                    (VisualJSON.asset_sets = per-node swap-set projection, an#87)
│   │   ├── compile.py       Shot -> CutoutSceneJSON (the bridge); projects asset_sets
│   │   │                    onto slots, validates authored swaps, sets -> hold channels
│   │   ├── render.py        Playwright headless capture + ffmpeg mux + audio overlay
│   │   │                    (rasteriser PINNED — `DETERMINISTIC_CHROMIUM_ARGS`, an#31)
│   │   └── runtime_files.py importlib.resources locator for the bundled JS runtime
│   ├── manim_adapter.py     real (when manim installed) — generates a title-card scene
│   ├── remotion_adapter.py  skeleton — clear NotImplementedError pending Phase 6+
│   └── whiteboard.py        stub
│
├── audio/                   TTS + lip-sync providers
│   ├── tts.py               TTSProvider Protocol + AudioClip, VoiceMeta
│   ├── lipsync.py           LipSyncProvider Protocol + Viseme, VisemeTrack
│   ├── offline_tts.py       OfflineTTS — silent WAV proportional to text length
│   ├── elevenlabs_tts.py    ElevenLabsTTS — needs ELEVEN_API_KEY
│   ├── offline_lipsync.py   OfflineLipSync — char→viseme distribution
│   ├── rhubarb_lipsync.py   RhubarbLipSync — wraps the rhubarb binary
│   ├── whisper_lipsync.py   WhisperLipSync — faster-whisper word timestamps
│   ├── pipeline.py          produce_audio_for_dialogue / _scene; content-hash caching
│   └── providers.py         make_tts / make_lipsync factories (string → instance)
│
├── verify/                  Verifier Protocol implementations
│   ├── _base.py             Verifier Protocol, Finding, VerificationReport, Severity
│   ├── layout.py            LayoutLintVerifier (IR-only structural checks)
│   ├── human.py             HumanInTheLoopVerifier (opens mp4, stdin y/N/r)
│   ├── media.py             helpers: detect_silence, audio_volume, ssim, extract_frames, transcribe
│   ├── media_quality.py     MediaQualityVerifier (silent audio, dialogue gaps, frozen frames)
│   └── vision.py            VisionLMVerifier (Claude vision QA)
│
└── data/                    bundled non-Python resources
    └── cutout_runtime/
        ├── index.html       loads PixiJS v7 + runtime.js
        ├── runtime.js       applySwap (the ONE swap path — any declared set, viseme incl.),
        │                    drawMouthShape, channel/timeline eval (blinks are compiled
        │                    channels since an#88 — no runtime blink pass)
        └── README.md
```

---

## 4. The six `Protocol`s and their implementations

| Protocol | Purpose | Implementations |
|---|---|---|
| `Renderer` | per-shot mp4 production | `CutoutRenderer` (real), `ManimRenderer` (real when manim installed), `RemotionRenderer` (skeleton), `WhiteboardRenderer` (stub) |
| `TTSProvider` | text → audio | `OfflineTTS` (silent placeholder), `ElevenLabsTTS` (real, needs `ELEVEN_API_KEY`) |
| `LipSyncProvider` | audio → viseme track (+ `words` when the provider has them, an#96) | `OfflineLipSync` (char-distribution), `WhisperLipSync` (word-aligned, needs `faster-whisper`), `RhubarbLipSync` (phoneme-aligned, needs `rhubarb` binary; recognizer follows the language). The compiler runs `an/adapters/cutout/coarticulate.py` over the raw track before emission (an#97): merge, suppress sub-frame tongue shapes, two-frame lead, decay before rest, and a minimum hold that votes |
| `Verifier` | verify IR ± render | `LayoutLintVerifier`, `MediaQualityVerifier`, `VisionLMVerifier`, `HumanInTheLoopVerifier` |

All four protocols are runtime-checkable; new implementations register via factories or `register_renderer(...)`.

---

## 5. The three control flows

### 5.1 `an render <dir>` (validate → audio → render → verify)

```
Project.load(dir)
├─ sync()                                        ← reconcile scene.md / ir/scene.json
├─ load SceneIR from mall["scenes"]["main"]
│
└─ render() in an/render.py
   ├─ if any dialogue & auto_audio:
   │     produce_audio_for_scene(scene, mall, tts=…, lipsync=…)
   │     ↳ stamps dialogue.audio_ref + dialogue.viseme_ref + dialogue.start + dialogue.duration + dialogue.word_timings (the provider's words, line-relative, when it has any — an#96)
   │     ↳ persists wav bytes to mall["audio"][hash], visemes JSON to mall["visemes"][hash]
   │     ↳ writes scene back to mall["scenes"]["main"] (mtime equalized)
   │
   ├─ for each shot in scene.timeline:
   │     renderer = RendererRegistry.find_for(shot)        ← matches on shot.renderer
   │     result = renderer.render(shot, ctx)
   │     ↳ cutout: compile_shot(shot, mall) → CutoutSceneJSON
   │              → spin Chromium via Playwright
   │              → load runtime + JSON
   │              → for each frame: anSetTime(t) + screenshot canvas
   │              → ffmpeg mux PNG sequence → silent.mp4
   │              → ffmpeg overlay dialogue audio (anullsrc base + adelay+amix per line)
   │              → shot.mp4
   │     mall["shots"][shot.id] = mp4 bytes
   │
   ├─ ffmpeg concat per-shot mp4s → output/<name>.mp4
   └─ mall["output"][name] = mp4 bytes
```

Default verifier chain (when called via `orchestrate()`): `LayoutLintVerifier` (pre + post), `MediaQualityVerifier` (post). `VisionLMVerifier` and `HumanInTheLoopVerifier` are opt-in.

### 5.2 `an iterate <dir> "<instruction>"` (free-text → IR patches)

```
Project.load(dir)
└─ iterate(dir, instruction) in an/iterate.py
   ├─ build IterateResponse JSON schema as the reply contract
   ├─ Anthropic.messages.create(
   │     model="claude-opus-4-7",
   │     thinking={"type": "adaptive"},
   │     system=<stable IR-shape primer>,
   │     messages=[scene_json_dump (cached), schema_hint (cached), instruction]
   │   )
   ├─ parse reply leniently → IterateResponse
   ├─ apply patches to deep-copy of ir.json (set / append / delete by JSON-pointer path)
   ├─ SceneIR.model_validate(new_dict) + validate_schema + validate_semantic
   ├─ if valid:
   │     for shot_id in affected_shots: del mall["shots"][shot_id]   ← cache invalidation
   │     mall["scenes"]["main"] = new_scene
   │     mall["decisions"].append({kind: "iterate", instruction, summary, patches})
   └─ return IterateResult(success, summary, patches, affected_shots, new_scene, validation)
```

Then `an render` regenerates only the invalidated shots, reusing the rest from `mall["shots"]`.

### 5.3 `an validate <dir>` (cheap pre-flight)

`load(dir)` → `validate_schema(scene)` + `validate_semantic(scene, available_voices=…, available_characters=…)` → `ValidationReport`. No side effects.

---

## 6. Caching: content-hash everywhere, cache invalidation by deletion

The system caches at every boundary that's expensive to recompute. Cache keys are content hashes — never timestamps, never counters.

| Cache | Key | Computed by |
|---|---|---|
| TTS audio | `_stable_hash({text, voice_id, tts.name})` | `pipeline._load_or_synthesize` |
| Viseme tracks | `_stable_hash({audio_key, lipsync.name, transcript})` | `pipeline._load_or_align` |
| Per-shot mp4s | `shot.id` (the IR slice IS the input) | `render.render` — **write-only, see below** |
| Final mp4 | `output_name` | `render.render` |
| Anthropic prompt cache | scene JSON + schema hint (`cache_control: ephemeral`) | `iterate._call_claude` |

The hash is stamped onto the IR (`Dialogue.audio_ref`, `Dialogue.viseme_ref`) so the orchestrator can detect provider changes — when you swap `--tts elevenlabs` for the offline default, the new expected hash mismatches the stored one, triggering re-synthesis without an explicit force flag.

Cache invalidation is by **deletion** (`del mall["shots"][shot_id]`). There is no cache versioning; the keys are deterministic so collisions across versions are impossible.

**Correction (an#31): the per-shot mp4 cache has no read path.** `mall["shots"]` is written at `an/render.py:222` and deleted at `an/iterate.py:268`, and nothing in the package reads it — so "re-render misses the cache and recomputes" describes a miss that every render already takes. This paragraph previously said otherwise, and the consequence is load-bearing for Wave 2: a benchmark harness needs **no cache-busting machinery for pixel metrics**, because every render is already cold. Either wire the read or drop the store — but do not build against the cache described here until one of those happens. (The *audio* caches two rows above are real, are read, and do warm between runs, so they affect wall-time measurements.)

---

## 7. Key invariants to preserve

These are load-bearing. Breaking them breaks the system in subtle ways.

1. **`scene.md` and `ir/scene.json` mtimes are equalized after every store write.** Otherwise `sync` flip-flops between md and json on each load and pipeline-injected state (viseme tracks, audio_refs) gets stripped. See `ScenesStore.__setitem__` and `sync()`'s "newer wins" tolerance band.
2. **The synthetic root container in the JS runtime is not indexed.** `compile.py` emits target paths starting with the entity name (`charlie/head/mouth`), not `root/charlie/head/mouth`. The runtime's `animaLoadScene` skips the synthetic root when populating `nodeIndex`.
3. **Multiple characters are spread along x in `_layout_character_positions(n)`.** A previous bug placed every character at (0, 0) so they overlapped. The default spread is 220px; characters with the same `entity.id` between renders keep their position because the spread is index-based.
4. **Composition trees flatten to canonical FlatActions for verification and rendering.** The DSL (`sequence`, `parallel`, …) is for authoring; `flatten()` produces absolute-time `FlatAction`s that downstream stages consume. Don't reason about composition nesting at render time.
5. **`extra="allow"` on every Pydantic IR model.** Forward-compat: an older reader of a newer document survives. The cost: typos in field names don't error.
6. **`anima*` JS API names were renamed to `an*` during the package rename.** `window.anLoadScene`, `anSetTime`, etc. The Python side calls these via `page.evaluate`; both must stay synced.
7. **The ScenesStore's `"main"` key is the only supported key.** Multi-scene projects are a future feature.
8. **A substituted asset is recorded, never merely substituted.** A character whose ref is not in `mall["characters"]` gets the placeholder rig; an environment ref that names neither a store entry nor a built-in preset gets the default backdrop. Both are legitimate — an asset-less project must render — and both used to be *silent*, which is not (an#33). `compile_shot` now appends one `AssetResolutionJSON` per drawable entity to `CutoutSceneJSON.asset_resolution`, warns (`CutoutCompileWarning`) on any `fallback=True`, and raises under `strict_assets=True`. The record is load-bearing rather than decorative: a missing descriptor and a deliberately-procedural character compile to the **same scene tree**, so nothing downstream of the compiler can tell them apart. `strict_assets` threads `an render --strict-assets` → `render_project` → `render` → `RenderContext.strict_assets` → `compile_shot`; `misc/bench/crossarch.py` sets it, because a pixel measurement of the wrong picture is worse than no measurement.
9. **A ledger row's comparability is decided by its provenance, not by its numbers.** Two rows measured on different scenes, or on different x264 builds, are not "one better and one worse" — every metric in them is mutually uninterpretable. Render-side metrics are comparable across machines (pixels are ISA- and OS-invariant at a pinned Chromium build); encode-side metrics are **machine-scoped and must be refused rather than banded**, because a band wide enough to absorb an x264 build change would swallow `flat_field_deviation`'s entire crf18->23 signal. The deciding fields are `scene_contract_sha256`, `environment.encode_side.x264_sei` (verbatim) and `.isa`. Two of the four value states are null and they mean different things: `gated` (the comparison is impossible) is not `unavailable` (the check did not run), and neither is "no change", which is a prediction that can never count.
10. **A verifier that could not run must not report `passed=True`.** `VerificationReport.add` flips `passed` only on `"error"`, so an `info` Finding on a failure path is byte-identical to a clean review — which is how a dead model id, a 500, a refusal and an unparseable reply all came back as "vision LM reported no issues" (an#39). `info` is reserved for *not configured* (no key, no SDK, no render); configured-and-broken reports at `an.verify.vision.FAILURE_SEVERITY`. Relatedly, `_parse_issues` returns `None` for "no verdict" and `[]` for "empty verdict", because collapsing them is what made a refusal indistinguishable from a pass.
11. **The determinism perimeter is observed on every render and enforced by default.** `runtime.js`'s `anDeterminismReport` reports the capture page, whether any PixiJS ticker is running, every node carrying a filter, and the per-entity blink phases; `an/determinism.py::capture_violations` judges — a pure function of that dict, so the rule is testable with no browser. The report lands in `RenderResult.provenance["determinism"]` beside the verbatim Chromium and x264 argv. A breach raises `CutoutRenderError`; `AN_DETERMINISTIC=0` downgrades it to a recorded fact. The three things it watches are deterministic *by accident* today: the app is built `autoStart: false`, nothing attaches a filter, and the capture page is `index.html` while `preview.html` (seven clock calls) is staged into the same directory. **The blink phase is a pure function of the entity NAME** — renaming a corpus character re-phases every blink and moves every pixel metric — which is why the phases are stamped rather than merely correct.

---

## 8. The CLI surface

```
an init <dir>                 — create a fresh project
an validate <dir>             — schema + semantic validation
an sync <dir>                 — reconcile scene.md ↔ ir/scene.json
an render <dir>               — full pipeline → output/main.mp4
   --tts {offline,elevenlabs}
   --lipsync {offline,whisper,rhubarb}
   --output-name NAME
   --parallel {N,auto}
   --strict-assets           (fail instead of drawing a stand-in — an#33)
an iterate <dir> "<instruction>"   — free-text edit via Claude (needs ANTHROPIC_API_KEY)
   --no-apply-changes        (dry run)
   --model claude-opus-4-7   (override)
an check                      — diagnose system deps
an bench                      — render the fixed corpus, write a metrics ledger row
   --scenes NAME,NAME
   --out PATH
   --keep-render PATH        (keep the throwaway render tree instead of deleting it)
   --quiet                   (print only the ledger path)
   --bless "<reason>"        (re-write the golden frames, recording this reason)
   --compare PATH            (compare this run against a baseline row)
an bench-compare              — two ledger rows in, a verdict or a REFUSAL out
   --before PATH --after PATH   (default: the two newest committed rows,
                                 ordered by `generated_at`, not by filename)
   --mutation NAME           (evaluate the per-mutation predictions instead)
   --strict                  (exit nonzero on a regression, an unmet criterion,
                              or a row it cannot read at all)
   --raw                     (JSON instead of the human digest)
an bench-mutants              — break each guard on purpose; the named test must go red
   --names A,B
   --quiet
```

All built via `typer` over the SSOT list `an.tools._dispatch_funcs` — wired
programmatically in `an/__main__.py`, never as decorators on the functions, so
the business layer carries no CLI types. Typer (MIT) replaced argh (LGPL-3.0)
in an#45; `argcomplete` went with it, since it hooks argparse specifically —
shell completion is now `an --install-completion`.

---

## 9. The `scene.md` markdown contract

```markdown
# <title>

```yaml meta
title: ...
duration: 12
fps: 24
resolution: { width: 640, height: 360 }
default_renderer: cutout
```

## Shot s1 (cutout)

```yaml shot
duration: 6
camera:
  move: push_in        # hold | push_in | pull_out | zoom_in | zoom_out
```

```yaml entities
- { kind: environment, id: park_bg, store: environments, ref: park }   # park | indoor | night | sunset | default
- { kind: character,   id: charlie, store: characters,   ref: charlie-v1 }
- { kind: character,   id: maya,    store: characters,   ref: maya-v1 }
```

```yaml actions
- { kind: tween, target: charlie, property: x, from: -110, to: -80, duration: 2.0, easing: ease_in_out }
- { kind: tween, target: charlie/torso, property: rotation, to: 0.05, duration: 0.5, start: 1.0 }
- { kind: set,   target: maya/head, property: y, value: -10, at: 3.0 }
```

```dialogue
charlie [thinking]: Did you ever wonder why we always meet here?
maya [amused]: Because the pigeons trust us.
```
```

The `[emotion]` brackets on dialogue lines are sugar for an `expression` leaf over the line (an#98): `an/expression/presets.py` holds the presets (`neutral / happy / sad / angry / surprised / afraid / disgusted / thinking / skeptical / amused`), the face solver `_add_face_clips` in compile.py sums them into one channel per `(node, property)` — brows, lids, and the mouth's `viseme@<form>` set — and an unknown name is a validate error, not a silent neutral.

---

## 10. What hasn't shipped

This section previously listed four "phases that haven't shipped yet" — real
character art, per-shot parallel rendering, live preview, and asset promotion.
**All four have since shipped** (`svg_sprite` visuals in `compile.py` +
`makeSvgSprite` in `runtime.js`; `an render --parallel auto|N` via
`_resolve_parallel`; `an preview` via `preview_project()`; and promotion as
`an.characters.promote` — not `an.assets.promote`, which never existed). They
are described in their own sections above.

What genuinely remains, in rough priority order:

1. **A real shot-to-Manim compiler.** `_render_script` in
   `an/adapters/manim_adapter.py` emits a single `Text(title)` title card of the
   right duration. No entity, action, dialogue or camera information from the
   Shot reaches the generated script. Translating the flat timeline into Manim
   constructs is unstarted design work, not a wiring job.
2. **`ping_pong` has no emitter, and placements cannot override a clip's
   loop.** Both evaluators honour all three `loop_mode`s (`runtime.js`
   `wrapTime`, `clip.py` `_wrap_time`), and since an#7 a `play` of a looping
   descriptor animation compiles to `loop_mode="loop"` (`_resolve_play`) — so
   the old line here, "nothing ever emits a non-default loop_mode", is closed.
   What remains: no compiler path writes `ping_pong`, and `PlacedClipJSON` has
   no `loop_mode` of its own (issue #7's step 2, deferred until clip dedup
   exists; per-instance `__play__{n}` clips make it unnecessary today —
   tracked as an#94).
3. **Lip-sync for face-baked characters.** A descriptor declaring
   `face_overlay: false` (DiceBear avatars; the 0.3.0 migration derives it from
   the old provenance string) has the face baked into the head SVG, so the
   compiler suppresses both the overlay mouth and the viseme channel. Those characters speak without
   moving their mouths. Hand-rigging (see `examples/promote_demo/`) is the
   production path today.
4. **Multi-scene projects.** `"main"` is the only key the scenes store supports.
5. **The default timing is smooth, and the ledger cannot argue otherwise.**
   `step_hz` (an#89) steps authored tweens on demand (`Meta.step_hz` /
   `Shot.step_hz` / `an render --step-hz`), camera and blinks exempt by
   construction. Whether to flip the default to "on twos" is a temporal,
   aesthetic judgement: measured, stepping moves the scene contract hash on
   every scene with a tween, so `bench-compare` refuses a stepped row before
   any family is examined, and outside the comparer the per-frame families
   move with the pose content in both directions — no lever could be
   registered (the `pix_fmt` precedent, one step earlier). Epic #9's Decision
   5 asked for "the ledger and a human A/B agreeing"; the ledger half is
   withdrawn on that measurement. The A/B is the `stepped-timing` demo, with
   its frame strip committed at `misc/docs/step_hz_side_by_side.png` (smooth
   left, 6 Hz right); the flip is a one-line PR that only the maintainer
   makes, and it has not been made.
6. **A real `an validate` for everything the renderer refuses.** The pre-flight
   reports the IR-level refusals (unknown `camera.move`, `prop` entities,
   `narration`; `play` is resolved against the target's descriptor animations
   since an#7), and since an#109 it no longer duplicates the compiler's camera
   list — both call `an.ir.camera.camera_keys`, so a move that validates cannot
   then raise at compile. It still cannot see rig-level problems: a speaker
   whose character has no head is discovered at compile time. Since an#111 it
   also **warns** when a camera translates over a stage with no depth — the
   render is correct, the whole picture slides, and that is also exactly what a
   flattened parallax looks like.

---

## 11. Test architecture

Run `pytest -q` for the current count — a number written here only goes stale.
The suite is layered:

- **Doctests** in module docstrings cover the public API of each module (composition flatten times, channel snap semantics, easing endpoints, etc.).
- **Pytest** for cross-cutting checks: store roundtrips, IR migration chaining, sync flip-flop regression, mall conformance, multi-shot concat audio, multi-character render distinct.
- **Live API tests** are gated on an explicit positive opt-in — `AN_LIVE_API_TESTS=1` **and** `CI` unset — not on a key being present. That distinction is the whole point: the previous "skip-if-key-missing" gate was satisfied by every developer machine and every agent session that had sourced a shell profile, so a plain `pytest -q` once made real, billed ElevenLabs calls and reported PASSED. The switch is defined in the **package** (`an/live_api.py`) rather than in `conftest.py`, because the test suite is not the only thing that must refuse to spend unasked: `examples/character_gallery/build.py` reads the same predicate before choosing ElevenLabs (an#63 — it chose on key-presence alone, and an example is the first thing a new user runs, on a clean checkout where the audio cache is cold), and an example cannot import a conftest.
- **The suite is offline and hermetic, and a guard enforces it.** `tests/conftest.py` refuses *and records* non-loopback socket use; `hermetic_browser` does the same at the Playwright layer, because a socket patch cannot see Chromium.
- **Silent discards raise.** Seven places that accepted something and produced nothing now raise typed errors; `an validate` reports the IR-level ones before any money or browser is spent. See `misc/docs/wave1_verification.md` §4.
- **End-to-end render tests** (skip-if-ffmpeg-or-chromium-missing) that produce real mp4s and assert structural properties (audio stream present, frames change, characters distinct).

The project's CI runs the offline subset; a developer machine with all dependencies installed runs the full suite (~70s).

---

## 12. References to the design space

The seven research reports next to this file describe the design space:

- `report 0 - Text-to-Structured-Animation.md` — orchestrator architecture, IR layering, MoVer-style verification (the spec's spine)
- `report 1 - The 2D cutout animation ecosystem...md` — JS-side ecosystem survey (PixiJS chosen)
- `report 2 - Animation interchange formats...md` — schema-level analysis of 12 animation formats (informed the IR shape)
- `report 3 - Facial Animation, Lip Sync & Expression Systems...md` — viseme conventions, Rhubarb, Cohen-Massaro
- `report 5 - Scene Graph Architecture & Animation System Design Patterns.md` — Python sketch for the cutout backend (closest to what was actually built)
- `dsl_design_patterns_report.md` — DSL design (informed the Pydantic IR + composition primitives)
- `Annotation systems...md` — interval data structures, rational time, A/V sync

When a subsystem is being extended, read the matching report before designing.
