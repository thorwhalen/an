# Wave 7 / T5 — Measuring the stage

**Scope.** The pan golden, the ratio test, and what a camera / plane / prop / StylePack
does to `scene_contract_sha256` and to the determinism perimeter. Everything below is
cited `file:line` against the tree at `/Users/thorwhalen/Dropbox/py/proj/t/an` (HEAD
`9aa35f8`, branch `wave7-research`, clean tree; ledger row `misc/bench/ledger/2026-08-24-bca83b3.json`). Where a claim is
not code- or measurement-backed it is marked **UNVERIFIED**.

---

## 1. What the instrument is, code-verified

### 1.1 What `scene_contract_sha256` hashes

`an/bench/contract.py:53-71`:

```python
payload = json.dumps(scene_json, sort_keys=True, separators=(",",":"))
return hashlib.sha256(payload.encode("utf-8")).hexdigest()
```

It hashes the **entire compiled cutout document**, canonicalised. Not a projection, not a
field subset. The docstring's word "reduced" (`contract.py:56`) means *reduced relative to
the project directory* — the directory carries scene mtimes and a decisions log that move
on every load (`contract.py:9-13`) — **not** reduced relative to the compiled JSON. So:

> **Every key and every value that `to_dict` emits is inside the hash.**
> `to_dict` is `scene.model_dump(mode="json")` with **no None pruning**
> (`an/adapters/cutout/serialize.py:321-323`).

A new Pydantic field with a default therefore lands in *every* document and moves *every*
hash — unless a `model_serializer` pops it when unset. That trick is already in the tree,
and its comment says exactly why: `step_hz` and `gaze_seeds` are popped when
`None`/empty (`serialize.py:284-290`), because "the compiled document is the bench's scene
contract (`scene_contract_sha256`), so a `null` here would move every committed row's hash
for a knob nobody turned" (`serialize.py:275-279`).

The counter-example is in the same file: `VisualJSON.fit` (`serialize.py:111`) was added
"additive with a `stretch` default so no stored scene changes meaning" — but it has no omit
serializer, so it serialises on **every visual in every document**. That comment is about
*semantics*, not about the hash. Structural reading of the code; I did not diff historical
rows to confirm the hash movement (**UNVERIFIED as a historical event, certain as a
mechanism**).

Multi-shot: `scenes_contract_sha256` (`contract.py:74-97`) returns *exactly*
`scene_contract_sha256` for a single-shot scene, so single-shot rows written before the
corpus grew a multi-shot fixture stay comparable; a multi-shot scene hashes the ordered
list of per-shot digests. Two derived counts sit beside it in provenance and are **not**
in the hash: `count_drawable_entities` (`contract.py:30-41`) and `count_nodes`
(`contract.py:44-50`), emitted at `an/bench/run.py:837-845`.

### 1.2 What `bench-compare` refuses on

Two tiers.

**Scene tier** — if any of `SCENE_KEYS` differs, *every metric in that scene* is refused
(`an/bench/compare.py:339-359`):

```
scene_contract_sha256, resolution, fps, n_frames, shot_order, palette_hex, tolerances
```
(`compare.py:63-71`), plus the mask **parameters** addressed by path — operators and
thresholds only, never the counts (`compare.py:75-83`, and see `an/bench/masks.py:34-58`
for the operator strings).

**Row/environment tier**, per metric, keyed on the metric's own `comparison_scope`:
- render-side: `environment.render_side.{chromium_build,playwright,launch_argv}`
  (`compare.py:91-95`);
- encode-side: `environment.encode_side.{isa,x264_sei,x264_argv,pix_fmt}`,
  `encode_command_source`, `decode_commands` (`compare.py:98-105`);
- both: `render_kwargs` (`compare.py:108`).

**Metric tier** — the metric's own declaration is a comparability key
(`DECLARATION_KEYS = ("family","side","optimum","unit")`, `compare.py:112`), cross-checked
against the row's inline copy (`CROSS_CHECKED_FIELDS`, `compare.py:225-234`) and the
per-mutation prediction (`compare.py:243-...`). An absent `comparison_scope` is refused,
not defaulted (`compare.py:460-468`).

Three facts that matter for Wave 7 planning:

1. **A new corpus scene is free.** `compare` iterates `sorted(names_b & names_a)`
   (`compare.py:686-693`). A scene the older row never had is simply not compared.
2. **A new metric is cheap.** A metric present in one row only is refused as
   `metric_absent` **for that metric alone** (`compare.py:366-371`); the scene stays
   comparable.
3. **A moved contract hash is catastrophic.** It refuses the whole scene before a single
   family is examined. This is the recorded `step_hz` verdict — see
   `.claude/skills/an-dev-bench/SKILL.md` lever table: "*Stepping moves
   `scene_contract_sha256` on every scene with a tween … so `bench-compare` refuses a
   stepped row **before any family is examined** — unlike `pix_fmt`, which stayed
   comparable and failed its exam.*"

Absence is a caveat, not a mismatch (`compare.py:167-207`) — the ledger grows additively
and refusing on an absent field would retroactively destroy comparability with every row
already written.

### 1.3 How goldens are keyed

`an/bench/paths.py:65` + `an/bench/golden.py:16-22`: `misc/bench/golden/<scene>/<frame-key>-chromium<build>.png`.
**Chromium build alone** — no platform, no arch. Measured across arm64 macOS, x86-64 Linux
and arm64 Linux, across two SwiftShader JIT backends (LLVM and Subzero): zero differing
pixels *and* zero differing PNG bytes (`misc/docs/wave2_crossarch_verdict.md:60-90`).
Carrying the platform would force one committed copy per platform for no information;
what the convention keeps is that a Playwright bump becomes a **new path requiring a
deliberate re-bless**, not a red test.

The criterion is `sha256` of the **decoded RGB array**, never file bytes
(`golden.py:10-14`, `contract.py:100-118`): Chromium 1187 → 1223 changed 144/144 PNG files
and **zero** pixels.

Other mechanics: `frame_key(index)` is keyed on the frame **index**, not the pinned time
(`golden.py:93-104`) — an fps change moves the index, moves the path, makes the golden
absent and therefore *gated*, which is loud and points at the right cause.
`frame_index_for` rounds rather than truncating, because `int(0.25*24) == 5`
(`golden.py:107-120`). `REQUIRED_GOLDEN_FRAMES = 2` (`golden.py:57`), relaxed to
*at least* two since an#98. Four gates, three of them for different absences
(`golden.py:62-65`): `golden_frames_undeclared`, `golden_absent_for_chromium_build`,
`chromium_build_unknown`, `blessed_this_run`.

`bless_scene` (`golden.py:419-479`) refuses: a blank reason, an unknown Chromium build,
fewer than two frames, and — pairwise — **any pixel-identical pair** (`golden.py:469-479`).
It also deletes goldens it no longer blesses, so a moved pinned time cannot leave an
orphan PNG that reads like a live gate (`golden.py:483-495`).

### 1.4 What the determinism perimeter watches — and what it does not

The split is deliberate: `runtime.js` **observes**, `an/determinism.py` **judges**
(`determinism.py:19-22`), so the rule is a pure function of a plain dict.

Observed (`an/data/cutout_runtime/runtime.js:715-728`): `page`, `runtime_version`,
`pixi_version`, `auto_start`, `shared_ticker_started`, `stage_filter_count`,
`filtered_node_paths` (`runtime.js:706-713`), `node_count`.

Judged (`determinism.py:141-147`, `_REQUIRED_FIELDS`): exactly five of those —
`page`, `shared_ticker_started`, `auto_start`, `stage_filter_count`,
`filtered_node_paths`. A **missing** key is a violation, never a default
(`determinism.py:91-98`): "a report that does not carry a field cannot testify that the
field is fine". Enforcement is **on by default** (`determinism.py:53-70`); the escape hatch
is `AN_DETERMINISTIC=0`. Raised from `an/adapters/cutout/render.py:643-670`.

So the perimeter watches three things: **which page**, **whether a ticker is running**,
**whether a filter is attached**. That is it.

**Would a raster texture or a tiling sprite add an unwatched render-time input? Yes, on
both counts.**

- **Raster.** Today every corpus texture is SVG (`find misc/bench/corpus -type f` returns
  only `.svg`, `.json`, `.md`). SVG takes `SVGResource._loadSvg` — `new Image` →
  `canvas.getContext("2d").drawImage(...)` at `Math.round(w*scale)` (verified in the
  vendored bundle `an/data/cutout_runtime/vendor/pixi.min.js`). A PNG/JPG takes a
  **different** parser: `loadTextures`, config `{preferWorkers: true,
  preferCreateImageBitmap: …}`, calling
  `createImageBitmap(r, {premultiplyAlpha: this.alphaMode===null||this.alphaMode===UNPACK ? "premultiply" : "none"})`
  — again verified in the vendored bundle. Neither the decode path, the premultiply mode,
  nor the worker pool is in `anDeterminismReport`. The worker pool is sized from
  `navigator.hardwareConcurrency||4` (vendored bundle, `getWorker()`) — a machine
  property that the ledger's environment block does not record either.
- **Tiling sprite.** `applyProperty` (`runtime.js:456-494`) applies exactly
  `x, y, rotation, rotation_rad, scale_x, scale_y, skew_x, skew_y, pivot_x, pivot_y, alpha`
  plus declared swap sets, and **throws** on anything else (`runtime.js:485-491`). A
  `tilePosition` channel is therefore not expressible without a runtime change, and a
  `TilingSprite` brings a shader and wrap modes that no field of the report describes.

**Recommendation.** If Wave 7 introduces either, extend `anDeterminismReport` with
`texture_sources` (a count by loader kind — svg / imagebitmap / canvas) and
`hardware_concurrency`, and add them to `_REQUIRED_FIELDS`. Note the intended side effect:
adding a required field makes an older *staged* runtime report `missing`
(`determinism.py:91-98`), which is a loud, correct failure rather than a silent pass.

### 1.5 Family letters and the one-witness-per-family rule

`Family = Literal["A","B","C","D","E","F","G"]` (`registry.py:146`), sides at
`registry.py:180-189`, names at `registry.py:190-199`:

| | meaning | side |
|---|---|---|
| A | edge geometry | render |
| B | golden change (tripwire) | render |
| C | coded-plane edge fidelity | encode |
| D | flat-field fidelity | encode |
| E | temporal held-pixel fidelity | encode |
| F | rate cost | encode |
| G | ringing | encode |

The criterion is **families, not metrics**: `REQUIRED_FAMILIES = 3` (`compare.py:119`),
counted as `len(families)` after grouping witnesses by family
(`compare.py:565-570`). So two counting witnesses in one family count **once** — the rule
is a genuine constraint on evidence, not just on tidiness. Family B states it explicitly in
its own declaration: `min_ssim_win8_vs_golden` is "family B's single witness.
`golden_identity` is the tripwire beside it and counts ZERO — one family, at most one
witness, and a boolean change detector is not it" (`registry.py:1032-1037`).

The invariants are enforced in `__post_init__`, not by convention:
- a gated prediction must name its gate, and cannot count (`registry.py:219-233`);
- `expect` in `("no_change","not_applicable")` can never count — a tautology would let any
  pre-encode statistic pad the witness count for free (`registry.py:226-232`);
- an interior or guard `Optimum` cannot carry an `expect` direction (`registry.py:255-268`);
- every metric must declare a prediction for **every** mutation (`registry.py:296-302`);
- `tripwire=True` forces zero counts (`registry.py:303-309`);
- `METRICS` and `TRIPWIRES` may not share a key, checked at import (`registry.py:1315-1322`).

The ledger builder enforces completeness in **both** directions: a declared metric the row
omits, and a row key the registry does not declare, are both errors
(`an/bench/ledger.py:165-186`). **A new metric must therefore be emitted for every corpus
scene** — as `unavailable(detail)` where it cannot run.

### 1.6 The precedent: how `expression_min_pairwise_changed_px` was added

This is the exact shape a wave-specific diagnostic takes, and Wave 7 should copy it
literally.

- **Declaration** (`registry.py:1081-1135`): `family="B"`, `role="diagnostic"`,
  `unit="pixels"`, `Optimum(kind="guard", note="Not a quality dial: larger is not better
  beyond 'the presets are apart'…")`.
- **Predictions**: `high_crf` → `not_applicable` ("computed on the pre-encode PNGs; no
  encode change can reach it"); `disabled_aa` and `supersample` → **gated**, with the gate
  text recording that the first declaration was wrong and was corrected *by the lane*
  ("measured on the lane: it MOVED under `supersample` when first declared
  `not_applicable`"). It counts **zero** everywhere, so gating costs nothing.
- **Computation** (`an/bench/run.py:551-582`): decode this scene's pinned golden frames
  from *today's* render, take the minimum over every pair of the count of differing pixels;
  `unavailable`, never zero, when fewer than two distinct frames resolve. Returns
  `measured(changed, closest_pair=[…])` — the extra field names *which* pair, so a mover is
  attributable.
- **Emitted on every scene** (`run.py:809-811`), unconditionally, next to the golden
  comparison.
- **Paired with an offline test on the committed goldens**
  (`tests/test_expression_goldens.py`): a face-crop pairwise floor set to **half** the first
  bless's measured minimum (`MIN_PAIRWISE_CHANGED_PX = 53`, half of 106, at
  `tests/test_expression_goldens.py:24-26`), plus a test that the difference is *only* in
  the crop (`:58-68`). The ledger row reports the same quantity on the **live** render, "so
  the ledger sees it before a re-bless does" (`registry.py:1129-1135`).

Two more precedents worth naming, because they bracket the choice:
- a stage fact that **no lever moves** belongs in *provenance*, not the panel —
  `viseme_keyframes_per_second` says so in its own docstring (`run.py:683-700`), and
  `shot_policy_provenance` (`run.py:649-681`) is the "additive scene-provenance facts read
  off each staged `scene_json`" hook a stage block would use;
- a metric whose *magnitude* is a change detector must carry its sign ambiguity as data —
  see the `SIGN AMBIGUITY` note on `min_ssim_win8_vs_golden` (`registry.py:1017-1027`).

---

## 2. The pan test, precisely

### 2.0 What the epic's sentence actually distinguishes — and the trap in it

Epic #9, Wave 7 done-when: *"a pan across a multi-plane environment produces golden frames
in which the planes have measurably moved at **different** rates — a flat 2D zoom would give
identical ratios."*

Work the geometry against the code. The runtime's root container sits at canvas centre and
everything hangs off it (`runtime.js:648-656`: `root.x = width/2; root.y = height/2;
nodeIndex['root'] = root`). Today's camera is a `scale_x`/`scale_y` tween on that node
(`compile.py:2936-2960`, factors at `compile.py:2896-2903`). So for a scene point whose
local coordinate is `X`:

```
screen_x(t) = W/2 + s(t) · X(t)
```

- **Rigid pan** (root translates by `D`): `Δscreen_x = D₁ − D₀` — the *same* for every
  plane. Ratio between any two planes = **1**.
- **Zoom about centre** (today's camera): `Δscreen_x = (s₁ − s₀)·X`. Two planes at
  *different* `X` move by *different* amounts. **Ratio ≠ 1.**
- **Multiplane pan** (`X_i(t) = X₀ + p_i·D(t)`): `Δ_i = (s₁−s₀)·X₀ + s₁·p_i·D₁`.

So a naive "the planes' displacements differ ⇒ parallax" test is **satisfied by today's
zoom camera**, which translates nothing. That false positive is the real content of the
epic's sentence, and it is what the measurement has to design against. (The sentence's word
"zoom" is doing double duty; the honest statement of the null hypothesis is *"the camera
moves the whole stage as one rigid image"*, and a centre-anchored zoom has to be excluded
too, not assumed away.)

**The quantity to pick.** Probe each plane at scene-space **`X₀ = 0`** — the canvas centre
column. Then the zoom term vanishes *exactly*:

```
Δ_i = s₁ · p_i · D₁        ratio_ij = Δ_i / Δ_j = p_i / p_j
```

for **any** `s₁`. The ratio is exactly the parallax-factor ratio and is exactly immune to a
concurrent zoom. Under a rigid pan every `p_i = 1` and every ratio is exactly 1. That is the
sharpest available statement of "moved at different rates", and it is the one to write down.

Two additions the epic's sentence does not require and should get anyway:
- assert **`Δy ≈ 0`** on every plane, so "it panned" is distinguishable from "it zoomed";
- assert the **ordering** `p_far < p_mid < p_near`, not merely inequality. Planes moving at
  different rates in the *wrong order* is a real bug that a bare inequality passes.

### 2.1 Measurement (a) — from the compiled JSON, free, default CI leg

**What.** For each declared plane, the **composed screen-space x** of its probe point at
the two pinned golden times, and the pairwise ratios of the deltas.

**Composed, not per-channel.** `an/adapters/cutout/timeline.py:84
evaluate_timeline(timeline, t) -> Pose` returns a pose keyed by `(target, property)` — those
are **local** transform values. Reading a plane's own local `x` channel and calling it the
answer would fail the whole point: a rigid pan implemented as a `root.x` channel gives
plane-local `Δx = 0` for every plane, i.e. `0/0`, and would read as "no parallax" for the
wrong reason, or as a crash. So walk the path root → plane, composing `x` and `scale_x`
from the pose (falling back to the node's `transform` where no channel exists — the
compiler's own rest-value table `_PROPERTY_REST_VALUES` at `compile.py:269` is derived from
`TransformJSON`'s field defaults, `serialize.py:51-79`).

**Plumbing that does not exist yet.** The bench has each shot's staged document as a dict
(`ShotCapture.scene_json`, consumed at `run.py:833-845`), and `from_dict`
(`serialize.py:326`) rebuilds the Pydantic object — but the JSON→`Timeline` builder is a
**test helper**, `_python_timeline` in `tests/test_swap_channels.py:64-108` (imported by
`tests/test_expression_compose.py:27`). The bench must not import from `tests/`. **Promote
it** into `an/adapters/cutout/timeline.py` as `timeline_from_scene(scene)` and have the test
helper call it — a small, hash-free, independently-landable change that also removes a
duplicate.

**Where it goes in the row.** A `stage` block inside scene provenance, written by a sibling
of `shot_policy_provenance` (`run.py:649-681`): per shot, `{plane_id: {parallax, probe_x,
delta_x, delta_y}}` plus the pairwise ratios. Provenance, not the panel — measurements in
provenance are explicitly *not* comparability keys (`run.py:815-825` says so for the golden
block, and `compare.py:57-61` for the same reason).

**Compiler-level test shape** (offline, no browser, `tests/test_stage_parallax.py`):

```python
def test_a_pan_moves_planes_at_the_declared_ratios():
    js = compile_shot(_pan_shot(planes=[("far", 0.4), ("mid", 1.0), ("near", 1.6)]),
                      mall=mall, fps=24, strict_assets=True)
    tl = timeline_from_scene(js)
    d = {p: _screen_x(js, tl, f"bg/{p}", 1.0) - _screen_x(js, tl, f"bg/{p}", 0.0)
         for p in ("far", "mid", "near")}
    assert d["near"] / d["far"] == pytest.approx(1.6 / 0.4, rel=1e-9)
    assert abs(d["far"]) < abs(d["mid"]) < abs(d["near"])          # ordering, not just ≠
    assert all(abs(_screen_y_delta(...)) < 1e-9 for p in d)        # it panned, not zoomed

def test_a_concurrent_zoom_does_not_change_the_ratios():
    """The X0 = 0 probe is why: Δ_i = s1·p_i·D1, so s1 cancels."""
```

The second test is the one that earns its keep — it is the guard against the false positive
identified in §2.0, and it is free.

### 2.2 Measurement (b) — from pixels, family-B-style diagnostic

**Fixture: `misc/bench/corpus/stage_pan/`** (T2 owns the art). Three planes at distinct
depths, each carrying a **distinctly-coloured probe marker centred on the canvas centre
column at t₀** and fully inside the frame at **both** pinned times. Full-width bands are
wrong for the probe: a plane clipped at the frame edge changes visible extent as it moves,
which shifts any centroid or correlation estimate for a reason that is not parallax. Bands
for the *look*, a bounded marker for the *measurement*.

**The mask primitive — `an/bench/masks.py` is NOT reusable here.** That module is
edge / flat / held / ring over luma and RGB deltas (`masks.py:88-160`); it has no
colour-selection at all. The reusable primitive is one file over:
`an.bench.metrics.pack_rgb` (`metrics.py:109-120`) packs `(N,H,W,3) uint8` → `(N,H,W) uint32`,
and `off_palette_pixel_fraction` (`metrics.py:122-133`) already selects by exact packed
colour via `np.isin`. An exact-colour plane mask is therefore `pack_rgb(frame) == 0xRRGGBB`
— one line, no new module. Exact-colour matching also **excludes anti-aliased boundary
pixels by construction**, which is the property that makes the interior centroid stable.

Two details:
- the probe colours must be in the scene's declared palette or `off_palette_pixel_fraction`
  will move for a reason unrelated to quality (`palette_for_scene`,
  `an/bench/palette.py:161`; the `off_palette_top_colours` / `blend_of` diagnostic at
  `metrics.py:135-215` is what would surface the mistake);
- estimator: count-weighted mean of column indices over the mask (sub-pixel). If a marker
  can ever be partially occluded, prefer an integer-shift XOR minimisation over the mask
  instead — exact for a rigid translation, immune to centroid drift — but then the marker
  must be occlusion-free by fixture design anyway.

**The metric (name it): `stage_min_plane_ratio_gap`.**

```
family     = "B"          (render-side; golden change / stage tripwire)
role       = "diagnostic"
unit       = "ratio"
optimum    = Optimum(kind="guard", note="Not a quality dial: larger is not better beyond
             'the planes are apart'. It exists so a regression that flattens the stage
             moves a ledger number instead of waiting for someone to look.")
value      = min over ordered plane pairs of | Δx_i / Δx_j − 1 |, between the scene's
             first two pinned golden frames of TODAY'S render
unavailable when the scene declares no planes, or fewer than two pinned frames resolve
counts     = False under every lever
```

Predictions, declared **exactly** on the `expression_min_pairwise_changed_px` precedent
(`registry.py:1101-1128`):

| lever | prediction | reason |
|---|---|---|
| `high_crf` | `not_applicable` | computed on the pre-encode PNGs; the corpus is upstream of the encoder (`registry.py:1030-1031` uses the same wording for family B) |
| `disabled_aa` | **gated** | AA-off changes which pixels the exact-colour interior mask selects at every plane boundary, so a sub-pixel centroid shift with no declared sign is possible. Gated, not predicted: uninterpretable, not good or bad. Family B's witness remains `min_ssim_win8_vs_golden`. |
| `supersample` | **gated** | same mechanism; an edge-quality lever resamples every boundary. Not a stage lever, no declared sign. |

Declaring `no_change` instead would be a *tautology* the registry refuses to let count
(`registry.py:226-232`) and would be an unmeasured claim; since the metric counts zero
anyway, gating costs nothing and is honest. If a later lane run shows it invariant to within
a pinned epsilon, tighten the gate text — do not promote it to a witness, or family B gains
a second witness against its own declaration (`registry.py:1032-1037`).

**The tripwire (a separate block): `stage_planes_parallaxed`.**

```
family     = "B", tripwire=True (forces counts=0 everywhere, registry.py:303-309)
unit       = "boolean"
value      = stage_min_plane_ratio_gap >= STAGE_RATIO_GAP_FLOOR
sentence   = "At least one pair of the stage's planes moved at measurably different
              rates between the pinned frames. False means the stage panned flat."
```

Shaped after `golden_identity` (`registry.py:1259-1307`): `True` is the healthy state, and
the prediction under a lever that breaks it is spelled **`decrease`** (True → False), not
`no_change` — the registry's own recorded bug is that spelling it `no_change` made the row
report `unexpected_movement` on every scene for a tripwire doing its job
(`registry.py:1272-1284`). Both `METRICS` and `TRIPWIRES` blocks must carry a row for every
scene (`ledger.py:165-193`), and the two may not share a key (`registry.py:1315-1322`).

**The floor.** Set `STAGE_RATIO_GAP_FLOOR` to **half** the value measured at the first
bless, exactly as `MIN_PAIRWISE_CHANGED_PX = 53` is half of 106
(`tests/test_expression_goldens.py:24-26`). With `p = (0.4, 1.0, 1.6)` the pairwise
`|r − 1|` values are `1.5, 3.0, 0.6`, so the minimum is `0.6` and the floor is `0.3`. Size
the pan so the *smallest* plane displacement is tens of pixels, so a ±0.5 px centroid error
is <2 % of the ratio.

**The offline companion test**, mirroring `tests/test_expression_goldens.py`: read the
committed `stage_pan` goldens back, compute the same ratios on decoded pixels, assert the
floor and the ordering. That test is the one that can fail on a clean checkout with no
browser; the ledger row is the live-render view of the same quantity.

### 2.3 The negative: `pan_left` on a plane-less scene

**Today.** `pan_left` already raises at both ends — `an/ir/validate.py:340-347` (severity
`error`, because "Severity is `error` wherever the pipeline raises, so validate's verdict
and the pipeline's verdict agree", `validate.py:335-339`) and `compile.py:2927-2935`
(`CutoutCompileError` naming the implemented set and pointing at #9). The vocabulary is
deliberately duplicated rather than imported, to keep the IR layer off the adapter, and
pinned together by test (`validate.py:101-108`). **Wave 7 must keep both ends and re-point
them**, not delete them: the moment `pan_left` joins `_CAMERA_MOVES`, the current message
stops being true and the *new* failure has to take its place.

**Why extent is not the test.** The legacy backdrop draws 4000 px-wide rects
(`compile.py:810-812`, `huge = 4000.0`, with the comment "the runtime centers root at
canvas/2 and applies camera scale, so 4000px wide rects will always cover"). Panning across
a flat colour band that wide changes **zero pixels** — precisely the silent no-op the epic
forbids, dressed up as a plane that is wider than the canvas. So the rule must be about the
*stage*, not about geometry:

> **A translating `camera.move` requires the shot to resolve a multiplane environment —
> at least two planes with declared parallax factors. Anything else raises, naming what to
> add.**

**Test shape** (both ends, mutation-tested per the repo's standing rule):

```python
def test_a_translating_camera_raises_at_compile_without_planes():
    shot = _shot(camera=Camera(move="pan_left"),
                 entities=[AssetRef(kind="environment", id="bg",
                                    store="environments", ref="park")])   # legacy preset
    with pytest.raises(CutoutCompileError, match="planes"):
        compile_shot(shot, mall=mall, fps=24, strict_assets=True)

def test_validate_agrees_with_the_compiler_about_a_translating_camera():
    report = validate_semantic(SceneIR(meta=..., timeline=[shot]),
                               available_characters=mall["characters"],
                               available_environments=mall["environments"])
    assert any("planes" in f.description for f in report.findings if f.severity == "error")

def test_a_translating_camera_over_a_multiplane_environment_compiles():
    ...   # the positive half; without it the guard is satisfied by raising always
```

**Two mechanical consequences.**

1. `validate_semantic` has **no** `available_environments` parameter today — its signature
   is `(scene, *, available_voices=None, available_characters=None)`
   (`an/ir/validate.py:391-396`). Wave 7 must add one, additively, with the same
   `None`-means-skip contract — **and pin by test that the CLI passes it**, because the
   docstring's own warning applies verbatim: "skipping them is what it sounds like: a `play`
   or a swap the compiler will refuse passes silently without the store"
   (`validate.py:400-405`).
2. `_DRAWABLE_ENTITY_KINDS = frozenset({"character","environment"})` (`validate.py:113`)
   must gain `"prop"` in the same PR that makes `compile.py:717-724` stop raising, or
   validate will error on something the compiler now draws — breaking the stated
   agree-with-the-pipeline rule.

---

## 3. Contract-hash and identity strategy

### 3.1 The rule

> **A contract hash moves only when that scene's picture-contract actually moved.
> A knob nobody turned must never move it.**

The cost of breaking it is asymmetric and worth stating plainly:

| | what it retires | recovery |
|---|---|---|
| **golden re-bless** | family B, for that scene | the three-row protocol (`SKILL.md`: before / after-unblessed / after-blessed), and a recorded reason `bless_scene` refuses to omit (`golden.py:448-455`) |
| **contract-hash move** | **every metric in that scene**, against **every** committed row | none — `compare.py:348-359` refuses the scene before any family is examined |

And there is a guard that will go red: `tests/test_expression_compose.py:134-162`
(`test_every_corpus_contract_hash_equals_the_committed_ledger_row`) re-compiles every corpus
fixture and asserts its hash equals the newest clean ledger row's, with `assert checked >= 7`
(`:162`). Wave 6 kept it green by exempting **only its own new scene**
(`NEW_IN_WAVE_6 = {"expressions"}`, `:30-31`). Wave 7 should do the same and no more.

**One-time re-baseline: acceptable only for a change that genuinely alters the picture's
contract for the scenes it touches, and never as a convenience.** "Every document gains a
camera node" is the paradigm case of the forbidden kind: the picture did not change for a
scene with no camera, so the hash must not.

### 3.2 The four features, scored

| feature | can every pre-existing corpus document stay byte-identical? | why / what it costs |
|---|---|---|
| **translating camera** | **YES — free, and no runtime change either** | `_add_camera_clips` early-returns on `camera is None or move is None` (`compile.py:2917-2918`) and on `move == "hold"` (`compile.py:2925-2926`). Of the eight ledger scenes, only `promote_demo` declares a camera at all, and it declares `hold` (`examples/promote_demo/scene.md:22-23`) → no emission today, no emission after. And `x`/`y` are **already** runtime-applied properties on any node including `root` (`runtime.js:458-459`, `runtime.js:648-656`), so a pan is expressible as channels on existing nodes. **Condition: the camera must remain a channel emitter, never a new node.** A `__camera__` container inserted into the tree adds a level to every document and moves `count_nodes` (`contract.py:44`) and every hash. |
| **plane environments** | **YES if store-declared only; NO if legacy presets are re-expressed as planes** | The legacy path builds `sky` + `ground` rects under the entity node (`compile.py:815-830`). Two of the eight ledger scenes carry an environment entity (`misc/bench/corpus/multi_shot/scene.md:21-23,47-49` and `examples/promote_demo/scene.md:27-29`), so re-expressing the preset as two planes moves exactly those two hashes — a partial re-baseline, still needing an exemption set. **Take the free route:** build planes only when the store entry declares them, and leave `_ENV_PRESETS` output byte-identical. Cost: a second branch in `_build_environment_subtree`. Bonus: the unknown-key warning at `compile.py:795-802` currently says "Layered plates and parallax planes are planned; see #9" and the override filter `preset.update({k: v for k, v in override.items() if k in preset})` (`compile.py:804`) **silently drops** a `planes:` key today — both must change in the same pass, and the warning text is the reader's only clue that they did. |
| **props** | **YES — free** | `compile.py:717-724` raises on a `prop` entity, so no corpus scene can contain one; a new code path is reached only by scenes that declare props. Zero documents change. Must land together with `validate.py:113` (see §2.3). |
| **StylePack** | **ONLY IF "unset" is expressed by ABSENCE in the serialized document** | This is the one that can move every hash. `to_dict` does no None pruning (`serialize.py:321-323`), so a `style_pack: null` or a defaulted field on `CutoutSceneMetaJSON` / `NodeJSON` / `VisualJSON` lands in every document. The engineered exception already exists and must be copied: `_omit_unset_step_hz` pops `step_hz` when `None` and `gaze_seeds` when empty (`serialize.py:284-290`), for exactly this reason (`serialize.py:275-279`). The counter-example is `VisualJSON.fit` (`serialize.py:111`) — additive in *semantics*, unconditional in *serialization*. Write the omit serializer in the **first** commit, not as a follow-up. |

### 3.3 Recommended sub-PR order, sequenced by hash movement

Epic #9 budgets Wave 7 at two PRs (`gh issue view 9`, wave table row 7). Within them:

**7a — nothing moves a hash.**
1. **`Shot.style` → `Shot.renderer`** (epic Decision 3: "as its own small PR, early in 7b
   while the rename budget is free"). It is an IR field and a renderer selector; the compiled
   cutout document carries no `style` key, so the hash is untouched. Needs a registered
   migration (`an/ir/migrate.py`; CLAUDE.md: "Never bump `SCHEMA_VERSION` without registering
   a migration") and the corpus `scene.md` files updated in the same pass. Do it first.
2. **Promote `_python_timeline`** out of `tests/test_swap_channels.py:64-108` into
   `an/adapters/cutout/timeline.py`. Zero behaviour change; unblocks measurement (a).
3. **Props** (`compile.py:717-724` + `validate.py:113` together).
4. **Translating camera as channels on existing nodes**, plus the re-pointed validate/compile
   refusal for a plane-less pan.

**7b — one scene's hash moves, deliberately, and it is a scene that did not exist before.**
5. **Plane environments, store-declared only**; legacy preset output asserted byte-identical
   by extending `tests/test_expression_compose.py`'s check rather than exempting anything.
6. **The `stage_pan` corpus fixture + goldens + `stage_min_plane_ratio_gap` +
   `stage_planes_parallaxed` + `tests/test_stage_parallax.py`.** Free at the comparer: a new
   scene is dropped by `names_b & names_a` (`compare.py:686-693`) and a new metric is a
   per-metric `metric_absent` refusal (`compare.py:366-371`). Use the three-row bless
   protocol.
7. **StylePack**, last, written omit-if-unset from commit one. If it turns out that a
   StylePack *cannot* be no-op-when-unset, that is the moment to stop and ask — it is the
   only item here that would justify a whole-corpus re-baseline, and it should be argued in
   a PR body with the reason recorded, not slipped in.

---

## 4. Raster textures and determinism

### 4.1 What Chromium actually does with a PNG/JPG, and how it differs from today

Today's corpus is **entirely vector**: `find misc/bench/corpus -type f` returns only `.svg`,
`character.json` and `scene.md`. Those go through `SVGResource._loadSvg` (verified in
`an/data/cutout_runtime/vendor/pixi.min.js`): `new Image` → `source.getContext("2d")
.drawImage(t, 0, 0, e, s, r, n)` with `r,n = Math.round(w·scale), Math.round(h·scale)`. So
the corpus already exercises Chromium's decode + 2D-canvas rasterisation, and the cross-arch
verdict measured *that* path identical across ISA, OS and SwiftShader backend
(`misc/docs/wave2_crossarch_verdict.md:60-90`).

A raster plate takes a **different** parser — `loadTextures`, `config: {preferWorkers: true,
preferCreateImageBitmap: …}` — reaching
`createImageBitmap(r, {premultiplyAlpha: this.alphaMode===null||this.alphaMode===UNPACK ? "premultiply" : "none"})`
(vendored bundle). Three new inputs come with it:

1. **Premultiplied alpha is applied at decode, and it is lossy.** Pixi asks for
   `"premultiply"` explicitly, so every semi-transparent pixel is rounded in 8 bits at decode
   time. Any later change to `alphaMode` — or to Pixi — changes those pixels.
2. **Colour management is `"default"`, i.e. implementation-specific.** Pixi does **not**
   pass `colorSpaceConversion`. The HTML spec's default is `"default"`, which "indicates that
   implementation-specific behavior is used" (MDN), and the spec text says conversion is
   "implementation-specific and chosen per the implementation's typical approach for drawing
   images onto canvas", while `"none"` "must skip color profile conversions, disregarding
   both embedded metadata and device color profiles" (WHATWG HTML). `an` pins
   `--force-color-profile=srgb` (`an/adapters/cutout/render.py:93`), which pins the
   *destination*; the *source* space of a raster file comes from its embedded ICC profile,
   and whether the resulting transform is bit-stable across Chromium builds and ISAs is
   **UNVERIFIED**.
3. **The decode runs in a worker pool sized from `navigator.hardwareConcurrency || 4`**
   (vendored bundle, `getWorker()`). Content should not depend on it — `preloadAssets` awaits
   `PIXI.Assets.load(aliases)` on a `.sort()`ed alias list before any render
   (`runtime.js:555-574`) and each alias decodes independently — but it is a machine-derived
   number that is in the render path and in **neither** the determinism report
   (`runtime.js:715-728`) nor the ledger's environment block.

### 4.2 What a golden on a raster plate is sensitive to

Everything a vector golden is sensitive to, **plus**: the image decoder's version, the
premultiply rounding, the ICC-to-sRGB transform, the source file's exact encoding (a
re-export with different chroma subsampling or a different ICC tag is a different picture),
and — for a plane being *panned* — the sampler: a translated sprite at non-integer offsets is
resampled by the GPU/SwiftShader bilinear path, and a plate scaled to fit is resampled again
(`makeSvgSprite`'s fit policy, `runtime.js:188-215`).

### 4.3 Recommendation

**Keep the Wave 7 corpus procedural / vector, and measure raster separately.** Three
reasons, all of them from the record rather than from taste:

1. The cross-arch verdict is *explicitly scoped*. Its own "What this does NOT settle"
   section (`misc/docs/wave2_crossarch_verdict.md:149-166`) disclaims other Chromium builds,
   text, and "a production shot — both fixtures are small. Viseme swaps, camera tweens and a
   dense SVG texture population were not exercised." Raster is not even on that list; it is
   outside the measured perimeter entirely.
2. The golden gate is a **CI gate** now precisely because those pixels were measured
   invariant (`wave2_crossarch_verdict.md:68-72`). Putting an unmeasured decode path inside
   the same gate risks converting a green CI signal into a machine-dependent one, and the
   predictable response to *that* is widening a threshold — the decay the whole instrument
   exists to prevent (`wave2_research.md` §3, "Re-blessing").
3. It is cheap to separate. A `stage_plate` fixture can exist, be blessed, and be *excluded
   from the family-B criterion* until a cross-arch capture has been run for it — the corpus
   already carries per-scene structure (`DFLT_FIXTURES`, `an/bench/corpus.py:187-292`) and
   the criterion is already evaluated **per scene** (`SKILL.md`: "the criterion is per scene,
   met on at least one, and the corpus has to contain a scene the lever can reach").

Concretely: `stage_pan` uses flat vector planes (the parallax measurement needs exact-colour
masks anyway, §2.2, which raster plates would destroy); a raster plate gets its own scene,
its own goldens, and a run on the cross-arch lane before anyone calls it a gate.

---

## 5. The mutation levers

**Recommendation: do not register a `flat_camera` lever. The tripwire plus a declared guard
mutant is the right instrument, and a lever would fail at comparability before it could say
anything.**

The argument is mechanical, not aesthetic.

A `flat_camera` lever forces every parallax factor to 1. Under the design recommended in
§3.2, parallax is **baked at compile time** into per-plane `x` channels — there is no
runtime seam to reach from outside. So the lever would have to rebind a compiler constant,
which changes the emitted keyframes, which changes the compiled document, which moves
`scene_contract_sha256` — and `compare.py:348-359` then refuses the whole scene **before any
family is examined**. That is verbatim the `step_hz` verdict already recorded in the skill's
lever table:

> `step_hz` — **NOT REGISTERED — refused at comparability, and the instrument is blind to it
> by construction** … "Stepping moves `scene_contract_sha256` on every scene with a tween
> (the resampled keyframes ARE the contract), so `bench-compare` refuses a stepped row
> **before any family is examined** — unlike `pix_fmt`, which stayed comparable and *failed*
> its exam."

Manufacturing a runtime seam purely so a lever could exist would violate the levers' own
stated contract twice over: "a lever must be the change the product will ship"
(`an/bench/mutations.py:97-101`) and "a lever that reproduces the code it examines is
examining itself" (`mutations.py:25-27`). And the `Lever` dataclass would have nothing
honest to put in `verify_row` (`mutations.py:137-149`) — the recorded fingerprint for the two
render levers is `render_side.runtime_sha256`, which a compile-time change does not move.

**What to do instead — `an bench-mutants`.** The guard-mutant registry
(`an/bench/mutants.py:74`, `MUTANTS`) is exactly the artifact for "prove this guard can
fire": each entry is `(name, file, old, new, caught_by, why)` (`mutants.py:55-72`), the
whole named test file runs with no `-k` filter (`mutants.py:20-24`), and
`tests/test_bench_mutation.py` asserts every `old` still occurs exactly once, cheaply, in
the default CI leg (`mutants.py:26-30`, `check_sites` at `mutants.py:649`). Declare:

```python
Mutant(
    name="stage_parallax_flattened",
    file="an/adapters/cutout/compile.py",
    old="<the exact line that multiplies the camera delta by the plane's factor>",
    new="<the same line with the factor forced to 1.0>",
    caught_by="tests/test_stage_parallax.py",
    why=("flattens the stage: every plane translates at the camera's own rate. The "
         "picture still renders, the pan still looks like a pan, and only the ratio "
         "test can tell — which is the whole reason that test exists."),
)
```

Add a second one that breaks the *ordering* (swap far and near factors) so the ordering
assertion in §2.1 is proven live too — the module's own rule is "Add one whenever you add a
guard. If it survives, the guard is decoration" (`SKILL.md`).

**Record the refusal.** Add a `flat_camera` row to the skill's lever table beside `pix_fmt`
and `step_hz` — **NOT REGISTERED — refused at comparability** — with this reasoning. Both
existing rows exist precisely so the next person does not re-derive a rejected lever, and
this one has the same shape.

**One honest caveat.** If Wave 7 instead implements parallax as a *runtime* camera (a
`__camera__` node with per-plane depth), then a runtime-patch lever **would** be expressible
and would stay comparable — the two existing render levers both work by patching the staged
`runtime.js` and rebinding `render.runtime_dir` (`mutations.py:299-350`). But that
implementation costs every document a node and every row its hash (§3.2), which is a far
larger price than the lever is worth. The lever question is therefore *downstream* of the
implementation choice, and the implementation choice should be made on §3's grounds alone.
If it ever flips, note that `_verify_supersample` had to **recompute** the expected digest
and assert equality because a bare "not the shipped digest" check is satisfied by *either*
render lever (`mutations.py:233-275`, and `SKILL.md`: "If you add a third render lever, do
the same — the negative form is not extensible").

---

## 6. Risks and unknowns

1. **The false-positive geometry is the load-bearing risk.** A ratio test written without the
   `X₀ = 0` probe (§2.0) is satisfied by today's zoom-only camera, which translates nothing.
   That is a test that passes on a broken implementation, and it would pass silently.
   Mitigation: the `test_a_concurrent_zoom_does_not_change_the_ratios` case, plus the `Δy ≈ 0`
   assertion, plus the ordering assertion.
2. **Sub-pixel centroid stability under the two edge levers is unmeasured.** That is why both
   are gated in §2.2. It should be *measured on the lane* and the gate text updated with the
   number — the `expression_min_pairwise_changed_px` gate already records that its own first
   declaration was wrong and was corrected that way (`registry.py:1112-1119`).
3. **Cross-arch identity for raster decode: UNVERIFIED.** The verdict covers the SVG path, at
   one Chromium build, and its own §"What this does NOT settle"
   (`wave2_crossarch_verdict.md:149-166`) names other builds, text, and dense texture
   populations as open. Premultiply rounding, ICC transforms and the worker pool are all
   outside it.
4. **Cross-arch identity at a larger backbuffer: UNVERIFIED and named as such**
   (`misc/docs/wave3_research.md` §7: "The cross-arch verdict was measured at 1x with MSAA 4,
   and a larger backbuffer is not on its list of settled questions… it gates whether goldens
   rendered at k=2 can stay a CI gate"). Relevant if a plane scene is ever benched under
   `supersample`.
5. **`validate_semantic` cannot see environments today** (`validate.py:391-396`), and its own
   docstring records that a `None` store makes the check silently skip
   (`validate.py:400-405`). Adding `available_environments=` is additive and safe; *forgetting
   to pass it from the CLI* reproduces the exact failure the docstring warns about, so pin it
   by test.
6. **`promote_demo` declares `camera: move: hold`** (`examples/promote_demo/scene.md:22-23`).
   Any Wave 7 change that makes `hold` emit an identity camera clip instead of early-returning
   (`compile.py:2925-2926`) moves that scene's hash for a knob nobody turned.
7. **The legacy backdrop is 4000 px wide and flat** (`compile.py:810-812`), so an
   extent-based "can this scene be panned" check is satisfied by a stage that produces zero
   pixel change. §2.3's rule avoids this; a naive one would not.
8. **StylePack scope is undefined in the epic** beyond "art direction as data". It is the one
   feature that can move every hash, and the omit-if-unset serializer must be in its first
   commit, not retrofitted.
9. **Corpus cost.** `expressions` costs 11.93 s wall for 48 frames at 320×240
   (ledger `provenance.wall_seconds`). A three-plane pan scene of similar length is
   comparable. Golden storage is small (wave2 §3's trigger to revisit is "corpus > 20 MB, or a
   single re-bless > 5 MB"), but the epic's independent **throughput** track exists precisely
   because "it determines what the golden corpus costs per PR for every wave after Wave 2"
   — and its gate is unchanged: keep the screenshot path until the ledger proves the two
   paths pixel-equivalent, because "a faster path that changes the pixels silently invalidates
   every baseline recorded before it".
10. **Standing honesty rule** (`SKILL.md`, and `an/CLAUDE.md`): never write that a rendering
    behaviour is "verified in CI". The browser lane runs on demand or on a PR carrying
    `run-browser-tests`, added with
    `gh api -X POST repos/thorwhalen/an/issues/<N>/labels -f 'labels[]=run-browser-tests'`
    — **not** `gh pr edit --add-label`, which exits 0 and applies nothing.

---

## Sources

**Code (this repo, read directly).** `an/bench/contract.py`, `compare.py`, `golden.py`,
`registry.py`, `ledger.py`, `masks.py`, `metrics.py`, `corpus.py`, `run.py`, `mutations.py`,
`mutants.py`, `paths.py`, `capture.py`, `palette.py`; `an/determinism.py`;
`an/adapters/cutout/{compile,serialize,timeline,render}.py`; `an/ir/{schema,validate}.py`;
`an/data/cutout_runtime/runtime.js`; `an/data/cutout_runtime/vendor/pixi.min.js` (PixiJS
7.4.2, MIT — `vendor/pixi.LICENSE.txt`); `tests/test_expression_compose.py`,
`tests/test_expression_goldens.py`, `tests/test_swap_channels.py`;
`misc/bench/ledger/2026-08-24-bca83b3.json`; `misc/bench/corpus/*`.

**Repo documents.** `misc/docs/wave2_research.md` §2–§4;
`misc/docs/wave2_crossarch_verdict.md`; `misc/docs/wave3_research.md` §4, §7;
`.claude/skills/an-dev-bench/SKILL.md`; `CLAUDE.md`; epic `gh issue view 9`
(wave table, Wave 7 done-when, the independent throughput track, Decision 3).

**External, fetched and quoted.**
- WHATWG HTML Standard, *ImageBitmap and animations* —
  `enum PremultiplyAlpha { "none", "premultiply", "default" }` with
  `premultiplyAlpha = "default"`; `enum ColorSpaceConversion { "none", "default" }` with
  `colorSpaceConversion = "default"`; `"default"` means implementation-specific, and `"none"`
  "must skip color profile conversions, disregarding both embedded metadata and device color
  profiles". <https://html.spec.whatwg.org/multipage/imagebitmap-and-animations.html>
- MDN, `createImageBitmap()` — confirms both defaults and that `default` for
  `colorSpaceConversion` "indicates that implementation-specific behavior is used".
  <https://developer.mozilla.org/en-US/docs/Web/API/Window/createImageBitmap>

**Marked UNVERIFIED above.** Chromium's exact ICC→sRGB transform stability across builds and
ISAs; whether `VisualJSON.fit`'s introduction historically moved every committed hash (the
mechanism is certain, the historical row diff was not run); cross-arch identity for the
raster decode path and at a larger backbuffer.
