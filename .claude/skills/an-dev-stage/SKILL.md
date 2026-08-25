---
name: an-dev-stage
description: The stage in the `an` repo — the camera (which is `root.pivot`, and already exists), parallax as one compile-time factor per plane, the multiplane environment descriptor, props, the StylePack, and the rename `Shot.style` → `Shot.renderer`. Load before touching `_add_camera_clips`, `_build_environment_subtree`, `_build_scene_root`, `Camera`, `AssetRef.stage`, the environments/styles/props stores, or any scene that pans. Triggers on "camera", "pan", "parallax", "multiplane", "plane", "environment", "backdrop", "prop", "style pack", "art direction", "renderer selector".
---

# an-dev-stage — the camera, the planes, the props

Design of record: `misc/docs/wave7_research.md` (epic #9, Wave 7). This skill is the part an
agent must not re-derive. Where this skill and the code disagree, the code wins and both get
fixed.

## 0. Six facts that change what you would otherwise write

1. **The camera already exists.** `runtime.js` indexes the centre container as `"root"`
   (`:648-656`) and PixiJS composes `world = position + M·(local − pivot)`, so `root.pivot_x/y`
   *is* a 2D camera. `pivot_x`, `pivot_y`, `rotation`, `scale_x`, `scale_y` are all already
   applied by the runtime and already in `RUNTIME_APPLIED_PROPERTIES`. A translating camera is a
   **compiler** change with **zero runtime change** — and therefore verified on every PR, not
   only on a labelled one.
2. **`migrate()` never runs on a SceneIR.** Every call in the tree passes
   `kind="CharacterDescriptor"`; `ScenesStore.__getitem__`, `sync()` and `project.load` validate
   raw JSON. A registered scene migration is decoration until the read path is wired — and with
   `extra="allow"`, a renamed field lands as a **silent default**, not an error. Wire it first.
3. **A foreground plane is structurally unreachable today**, not merely missing:
   `_build_scene_root` runs environments and characters in two separate loops
   (`compile.py:701-716`), so entity order cannot interleave them.
4. **An environment override silently drops what it does not know**
   (`compile.py:804`, an intersection filter) — a `planes:` key would vanish with a warning. The
   `an` skill used to claim it raises; it does not.
5. **`CharacterDescriptor(name="sword")` is a seven-bone humanoid with a face and a blink**
   (`model_post_init` re-seeds from an empty list). "A prop is just a character" is the option
   with the most landmines, not the cheapest.
6. **The styles store has no reader**, `AssetRef(kind="style")` is validated and then skipped,
   and colours live in three disconnected places (compiler, runtime literals, factory — which
   has *two* disagreeing palette tables).

## 1. The model — one camera, one factor per plane

```
root.pivot = (cam_x, cam_y)                 # the camera pose
plane_i.x  = x0_i + (1 − f_i) · cam_x       # the compensation; f = 1 emits NOTHING
⇒ screen_i = (W/2, H/2) + S · (x0_i − f_i · cam)
```

`depth` is the wire scalar, a **ratio** (Godot's `scroll_scale`): `0` frozen, `1` the character
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
  the evaluators are later-wins with camera clips appended last — so **raise**, naming both.
  Additive folding is the eventual answer and is not Wave 7.

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
zoom; a rigid pan gives exactly 1. Also assert `Δy ≈ 0`, and assert the **ordering**
`p_far < p_mid < p_near` — wrong-order parallax is a real bug that a bare inequality passes.

- **JSON measurement** uses the *composed screen-space* x, not a per-node local channel (a rigid
  pan on `root` leaves every plane's local `Δx = 0`). Needs `_python_timeline` promoted out of
  `tests/test_swap_channels.py` into `an/adapters/cutout/timeline.py`.
- **Pixel measurement** is per-plane centroids over exact-colour masks; `an/bench/masks.py` is
  *not* reusable (no colour selection) — the primitive is `metrics.pack_rgb` plus equality.
- One diagnostic ledger row (family B, `role="diagnostic"`, guard optimum, counts zero, **gated**
  under both edge levers) plus a tripwire, floor at half the first bless's measured minimum —
  the `expression_min_pairwise_changed_px` precedent, followed literally.
- **No `flat_camera` mutation lever.** A compile-time parallax change moves the contract hash and
  is refused at comparability — the recorded `step_hz` verdict, verbatim. The proof belongs in
  `an bench-mutants` as declared guard mutants.
- **Corpus plates stay vector.** Raster takes a different loader (premultiplied alpha,
  implementation-defined colour-space conversion, a worker pool) that the determinism report does
  not watch, and the cross-arch verdict does not cover it.

## 4. What a pack may and may not do

A StylePack does **not** recolour SVG art at compile time: it would break `src` content
addressing and the asset-resolution ledger, the only substitution precedent is a regex the bench
had to abandon for XML parsing, no role tagging exists, and inferring a role from a pixel already
caused an#99's wrong-tone lid. A pack reaches SVG art **through the factory at authoring time**;
the compiler warns naming what it could not reach.

A pack must not declare a role it cannot change — `lip`, `mouth_fill`, `teeth`, `tongue`,
`eye_sclera` are runtime literals. A role that silently does nothing is worse than an absent one.

## 5. Order of work

`0` wire `migrate()` into the SceneIR read path → `1` `Shot.style` → `Shot.renderer` (+ retire
`kind="style"`), one migration → `2` promote `_python_timeline` → `3` props (extraction alone,
then the path) → `4` the translating camera → `5` plane environments (store-declared only) →
`6` the `stage_pan` fixture, goldens, metric, tripwire → `7` StylePack.

Everything through `4` moves no hash. `5`–`7` move one, deliberately, for a scene that did not
exist before.

## 6. Never

- Never let an unimplemented camera move silently no-op — it raises at validate **and** compile,
  and the two vocabularies are pinned in sync by a test.
- Never derive draw order from depth (the runtime cannot honour it; five of seven surveyed tools
  keep them decoupled).
- Never add a serializing field to `VisualJSON`/`NodeJSON`/`CutoutSceneMetaJSON` without an
  omit-when-unset serializer, or every corpus hash moves for a knob nobody turned.
- Never ship `repeat`/tiling as schema without wiring `TilingSprite` in the runtime — both halves
  or neither.
- Never give one number two names. `depth` is the scalar; `parallax` is the per-axis override.
