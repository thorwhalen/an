---
name: an-dev-stage
description: The stage in the `an` repo — the camera (which is `root.pivot`, and already exists), parallax as one compile-time factor per plane, the multiplane environment descriptor, props, the StylePack, and the rename `Shot.style` → `Shot.renderer`. Load before touching `_add_camera_clips`, `_build_environment_subtree`, `_build_scene_root`, `Camera`, `AssetRef.stage`, the environments/styles/props stores, or any scene that pans. Triggers on "camera", "pan", "parallax", "multiplane", "plane", "environment", "backdrop", "prop", "style pack", "art direction", "renderer selector".
---

# an-dev-stage — the camera, the planes, the props

Design of record: `misc/docs/wave7_research.md` (epic #9, Wave 7). This skill is the part an
agent must not re-derive. Where this skill and the code disagree, the code wins and both get
fixed.

## 0. Six facts that change what you would otherwise write

1. **The camera already exists — and since an#109 it translates.** `runtime.js` indexes the
   centre container as `"root"` (`:648-656`) and PixiJS composes
   `world = position + M·(local − pivot)`, so `root.pivot_x/y` *is* a 2D camera. A translating
   camera was a **compiler** change with **zero runtime change**, which is why it is checked on
   every PR rather than only on a labelled one. **Landed:** `Camera.keys` + `CameraKey`,
   `pan_left`/`pan_right`/`tilt_up`/`tilt_down`, the `an.ir.camera.camera_keys` resolver validate and compile literally
   share, the collision rule, and the migration dropping `position`/`target`/`focal_length`
   (schema `0.3.0`). Note **no corpus fixture emits a camera clip**, so the contract-hash guard is
   vacuously green for the camera: `tests/test_camera.py` pins the emitted document directly.
2. **`migrate()` runs on a SceneIR — since an#105, and only since then.** Before it, every call
   in the tree passed `kind="CharacterDescriptor"` and a registered scene migration was decoration.
   `scene_from_json_doc` is now the single choke point every read goes through (an AST test fails
   on any other), and an#106 and an#109 both ship real ladders. What has NOT changed is why it
   mattered: with `extra="allow"`, a renamed or removed field lands as a **silent default**, not an
   error — and a document already at the current version is never migrated again, so a retired key
   that reaches one is permanent. That last route is `an.ir.validate`'s `RETIRED_KEYS` /
   `RETIRED_CAMERA_KEYS`, because no migration can see it.
3. **Environment art in front of the characters is structurally unreachable today**, not merely
   missing: `_build_scene_root` runs environments and characters in two separate loops
   (env `:701-707`, characters `:708-724`), so entity order cannot interleave them. (A *node*
   after the characters is reachable — `kind="character", store="environments"` — but it draws
   the placeholder rig, not a plate.)
4. **An environment override silently drops what it does not know**
   (`compile.py:804`, an intersection filter) — a `planes:` key would vanish with a warning. The
   `an` skill used to claim it raises; it does not.
5. **`CharacterDescriptor(name="sword")` is a seven-bone humanoid with a face and a blink**
   (`model_post_init` re-seeds from an empty list). "A prop is just a character" is the option
   with the most landmines, not the cheapest.
6. **The styles store has no reader**, and colours live in three disconnected places (compiler,
   runtime literals, factory — which has *two* disagreeing palette tables). `AssetRef(kind="style")`
   was validated and then skipped; an#106 retired it (the schema no longer accepts it, and the
   0.1.0 → 0.2.0 migration drops it from stored documents) because it selected nothing and the
   word belonged to the renderer. Art direction returns as a StylePack (#112).

## 1. The model — one camera, one factor per plane

```
root.pivot = (cam_x, cam_y)                 # the camera pose
plane_i.x  = x0_i + (1 − f_i) · cam_x       # the compensation; f = 1 emits NOTHING
⇒ screen_i = (W/2, H/2) + S · (x0_i − f_i · cam)
```

`depth` is the wire scalar, a **ratio** (Godot's `Parallax2D.scroll_scale` — *not* `ParallaxLayer`, which spells it `motion_scale` and is deprecated): `0` frozen, `1` the character
plane, `>1` foreground. `parallax: (fx, fy)` is the per-axis override. `z`/`focal_z`
(`f = focal_z/z`, which buys apparent size and a true dolly) is **7b sugar, not 7a**.

- **Sign trap:** Unity's z-derived factor is the *inverse* convention (`f_unity ≡ 1 − f_godot`).
  Do not read a Unity tutorial into this code without converting.
- **Paint order is authored, never derived from depth.** A foreground plane happens via
  `characters_after: str | None` (Rive's relative-ordering shape); `None` reproduces today
  byte-for-byte and dissolves the `depth == 1.0` tie.
- **Zoom composes through the pivot**, so a push-in during a pan zooms toward what the camera is
  looking at. That is the correct default and it costs nothing.
- **The collision rule:** a compensation channel targets a node an author may also target, and
  the evaluators are later-wins with camera clips appended last — so **raise**, naming both. It is
  not hypothetical: `set root scale_x 3.0` + `push_in` silently evaluates to 1.25 today. This is a
  deliberate divergence from `_add_face_clips` (warn, authored wins); additive folding is the
  eventual answer and is not Wave 7.
- **`step_hz`:** the camera is exempt *by construction* because it is its own emission site
  (`tests/test_step_hz.py:235` pins two keyframes and non-`step` easing under `step_hz=10`).
  Plane compensation is a **third** emission site — put it on the camera's side of that fence, or
  a stepped plane judders under a smooth camera. Generalise the test to it.

## 2. Byte-identity is the acceptance, again

> A contract hash moves only when that scene's picture-contract actually moved.

A golden re-bless retires family B for one scene, with a recorded-reason protocol. A
contract-hash move retires **every metric in that scene against every committed row**, with no
recovery. So:

- the camera must stay a **channel emitter** and never become a node (a node moves
  `count_nodes` and every hash);
- planes are built **only when the store entry declares them**; `_ENV_PRESETS` output stays
  byte-identical, and a richer default look ships as a *new* preset;
- props are free (the path is reachable only by a scene that declares one) — but the
  rig-builder extraction is the highest-risk edit in the wave and lands **alone**;
- a StylePack must be **omit-if-unset in the serialized document from its first commit**
  (`_omit_unset_step_hz` is the precedent; `VisualJSON.fit` is the counter-example).

`tests/test_expression_compose.py::test_every_corpus_contract_hash_equals_the_committed_ledger_row`
is the guard. Wave 6 exempted only its own new scene; Wave 7 does the same and no more.

## 3. Measuring a pan

The epic's sentence has a trap: **today's centre-anchored zoom already gives unequal per-plane
displacements**, so "the displacements differ" is satisfied by a scene with no parallax at all.

Probe each plane at scene-space **`x = 0`**. Then `Δ_i = s₁·p_i·D₁` and `ratio = p_i/p_j` for any
zoom; a rigid pan gives exactly 1. Also assert `Δy ≈ 0` (zoom-free pans only — a pan+zoom measured
`Δy = −75/−45/−15/+15`), and assert the **ordering** `p_far < p_mid < p_near`. The ordering is a
bonus, **not a second gate**: the zoom false positive satisfies it too (−10 < +10 < +30). Only the
x = 0 probe excludes a zoom.

- **JSON measurement** uses the *composed screen-space* x, not a per-node local channel (a rigid
  pan on `root` leaves every plane's local `Δx = 0`). `evaluate_timeline` returns a **pose**, not a
  position, and nothing in `an/` composes `world = position + M·(local − pivot)`. The prerequisite
  **landed as an#107**: `an.adapters.cutout.timeline.timeline_from_scene` turns a compiled document
  into an evaluable `Timeline` (it was a private helper inside `tests/test_swap_channels.py`).
  **Writing the compositor is the work**, and it is still unwritten.
- **Pixel measurement** is per-plane centroids over exact-colour masks; `an/bench/masks.py` is
  *not* reusable (no colour selection) — the primitive is `metrics.pack_rgb` plus equality. A
  centroid is **not** at x = 0, so the fixture must hold zoom constant (measured: at offset −60
  under a zoom the ratios read 0.2/0.6/1.0/1.8), and assert each mask's pixel count is unchanged
  between frames — a plane panning off-canvas biased a `depth = 2` ratio to 1.975.
- One diagnostic ledger row (family B, `role="diagnostic"`, guard optimum, counts zero, **gated**
  under both edge levers) plus a tripwire, floor at half the first bless's measured minimum —
  the `expression_min_pairwise_changed_px` precedent, followed literally.
- **No `flat_camera` mutation lever.** A compile-time parallax change moves the contract hash and
  is refused at comparability — the recorded `step_hz` verdict, verbatim. The proof belongs in
  `an bench-mutants` as declared guard mutants.
- **Corpus plates stay vector.** Raster takes a different loader (premultiplied alpha,
  implementation-defined colour-space conversion, a worker pool) that the determinism report does
  not watch, and the cross-arch verdict does not cover it.

## 3b. Planes, and props

**The environment descriptor** is versioned, `extra="allow"`, `DocumentKind`-registered, and
carries `planes: [{name, art, depth, parallax, offset, anchor, size, fit, repeat}]` where **list
order is draw order** (the runtime has no `zIndex`, so a `z` field would be a second SSOT it could
not honour). `characters_after: str | None` is how a foreground plane happens; `anchors` are named
stage marks (a horizon is one of them); `source: AssetSource` is mandatory in spirit because
`an credits` walks only `mall["characters"]` and would otherwise be a false compliance statement
the day plates arrive. Build planes **only when the store entry declares them** — re-expressing
`_ENV_PRESETS` as planes moves two ledger hashes for no picture change; a richer default look
ships as a *new* preset. `repeat` ships with `TilingSprite` wired or not at all.

**A prop** is a thin profile of `CharacterDescriptor` sharing `Bone`/`Slot`/`Attachment`/`Skin`
and one extracted rig builder, with different defaults. Not "a character with `kind: prop`":
`model_post_init` re-seeds a seven-bone humanoid with a face and a blink from an empty list (even
`animations={}` is re-seeded), the placeholder fallback draws a **person** where a lamp should be,
and `an character validate` scores exactly 21 blocking findings on a correct prop. Placement is
one additive `AssetRef.stage` — hash-free by construction, because the contract hashes the
*compiled* document and an `AssetRef` never reaches it. **Only `at` and `scale` shipped**
(an#118). `depth` and `after` were deferred because the stage vocabulary they belong to had
not landed yet — and #109 and #110 landed later the same day, so that reason has expired
without being revisited. Tracked as an#126; do not read the four-field shape below as
present tense. Attaching
a prop to a character is deferred: `_track_root_of` makes entity identity the first path segment,
and the rig has **no hand bone**.

## 4. What a pack may and may not do

A StylePack does **not** recolour SVG art at compile time: it would break `src` content
addressing and the asset-resolution ledger, the only substitution precedent is a regex the bench
had to abandon for XML parsing, no role tagging exists, and inferring a role from a pixel already
caused an#99's wrong-tone lid. A pack reaches SVG art **through the factory at authoring time**;
the compiler warns naming what it could not reach.

A pack must not declare a role it cannot change — `lip`, `mouth_fill`, `teeth`, `tongue`,
`eye_sclera` are runtime literals. A role that silently does nothing is worse than an absent one.

## 5. Order of work


`0` wire `migrate()` into the SceneIR read path **(landed, an#105)** → `1` `Shot.style` →
`Shot.renderer` (+ retire `kind="style"`), one migration **(landed, an#106)** → `2` promote the
scene→`Timeline` reader as `timeline_from_scene` **(landed, an#107)** → `3` props — the extraction
alone, then the path **(landed, an#108: PRs #117 and #118)** → `4` the translating camera
**(landed, an#109)** → `5` plane environments (store-declared only) →
`6` the `stage_pan` fixture, goldens, metric, tripwire **(landed, an#111)** →
`7` StylePack **(landed, an#112)**.

**Wave 7 is complete.** What it left behind for a later wave, each named with its reason:
the **dolly** (`dolly_in`/`dolly_out` and the `z`/`focal_z` sugar — depth-aware zoom, which is
what `depth` does NOT do today); `repeat`/tiling and the `gradient`/`generated` plane arts (each
needs a runtime that can draw it); a pack reaching SVG art through the factory; attaching a prop
to a character (`_track_root_of` makes entity identity the first path segment, and the rig has no
hand bone); and additive folding for the camera/plane collisions that currently raise.

Everything through `4` moves no hash. `5`–`7` move one, deliberately, for a scene that did not
exist before.

## 6. Never

- Never let an unimplemented camera move silently no-op — it raises at validate **and** compile,
  and the two vocabularies are pinned in sync by a test.
- Never derive draw order from depth (the runtime cannot honour it; five of seven surveyed tools
  keep them decoupled).
- Never add a serializing field to **any** model in `serialize.py` without an omit-when-unset
  serializer: `to_dict` prunes no `None`s and the contract hashes the whole dict, so a defaulted
  field on `TransformJSON` — the realistic Wave 7 target — moves every corpus hash for a knob
  nobody turned.
- Never ship `repeat`/tiling as schema without wiring `TilingSprite` in the runtime — both halves
  or neither.
- Never give one number two names. `depth` is the scalar; `parallax` is the per-axis override.
