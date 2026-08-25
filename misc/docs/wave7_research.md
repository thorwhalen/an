# Wave 7 research — "The stage"

**Status: decided.** This is the design of record for Wave 7 of epic #9 (a camera that
translates, multiplane environments, props, a StylePack). It is the synthesis of five research
threads run 2026-08-25 (camera/parallax, environments/planes, style/rename, props, measurement),
each of which read the code and fetched its own sources. Where this document and the epic's
Wave 7 brief disagree, **this document is right and the brief is stale** — the brief was written
before any of this was measured. Where this document and the code disagree, the code is right
and both get fixed.

The five threads' raw notes — every URL, every quoted sentence, and 38 explicit `UNVERIFIED`
markers — are committed beside this file as `misc/docs/research/wave7_T1..T5.md`. Where a claim
here and a note there disagree, the note is the evidence and this file is the error.

Its companions: `wave6_research.md` (the previous wave, and the model for this document's
shape), `.claude/skills/an-dev-stage/SKILL.md` (the part an agent must not re-derive), and
`architecture_as_built.md` (current state).

---

## 1. What is built today (facts, `file:line`)

**The camera is a scale tween on a container the compiler cannot name.** `_add_camera_clips`
(`compile.py:2906`) maps `shot.camera.move` through `_CAMERA_MOVES` (`:2897-2903` — five entries,
all `(start_scale, end_scale)` pairs) onto a two-keyframe `ease_in_out` tween of `scale_x`/
`scale_y` on the target `"root"`. It early-returns when `camera is None or move is None`
(`:2917`) and when `move == "hold"` (`:2925`). An unrecognised move **raises**
(`:2927-2935`), and `an validate` reports the same refusal at severity `error`
(`validate.py:340-347`) — the two vocabularies are kept in sync by
`tests/test_loud_discards.py:677-687`.

**But `root` is already a camera, and nobody knew.** The runtime creates the container at canvas
centre and indexes it under the name `"root"` (`runtime.js:648-656`: `root.x = width/2;
root.y = height/2; nodeIndex['root'] = root`), and the vendored PixiJS 7.4.2 composes
`world = position + M·(local − pivot)` (`vendor/pixi.min.js:15`). So `root.pivot_x`/`pivot_y`
*is* a 2D camera — and `pivot_x`, `pivot_y`, `rotation`, `scale_x`, `scale_y` are all already in
`applyProperty` (`runtime.js:456-494`) and in `RUNTIME_APPLIED_PROPERTIES` (`compile.py:272-282`).
Verified empirically this session: `set root pivot_x 25` compiles today, because transform
properties skip the target-existence check (`compile.py:1888-1889`). **Wave 7 names and validates
a capability that already exists; it does not add one.**

**Three `Camera` fields are dead and are written into every scene.** `position`, `target`,
`focal_length` (`an/ir/schema.py:73-85`) are read by nothing and emitted by the md writer, so
every camera block the writer has regenerated carries **nine** junk lines (4 + 4 + 1). A
hand-authored block that says only `move: hold` — `examples/promote_demo` — carries none until it
is rewritten.

**An environment is three scalars, and a foreground plane is unreachable.**
`_build_environment_subtree` (`compile.py:741-833`) picks one of five presets (`_ENV_PRESETS`,
`:732-738`) and merges a store document through an **intersection filter** (`:804`:
`preset.update({k: v for k, v in override.items() if k in preset})`) — so any key outside
`{sky_color, ground_color, ground_y}` warns (`:789-802`) and is dropped. The test that pins the
warning uses `parallax_layers: 3` as its example of a dropped key
(`tests/test_loud_discards.py:326`). The geometry is two 4000-unit rects (`:810-831`) sized for a
zoom, not a pan. **Zero environment documents exist on disk anywhere in the repo.** And `_build_scene_root` runs
environments and characters in **two separate loops** (env `:701-707`, characters `:708-724`), so
re-ordering `entities` cannot interleave them: environment *art* in front of the characters is not
merely absent, it is structurally unreachable. A *node* after the characters is reachable — an
entity with `kind="character"` and `store="environments"` lands one — but it is built by
`_build_character_subtree`, so it draws the placeholder rig, not a plate.

**Props raise.** `AssetRef.kind` accepts `"prop"` (`an/ir/schema.py:105`); `an validate` errors
(`validate.py:113,351-360`); the compiler raises inside the *character* loop (`compile.py:717-724`),
pointing at #9. There is no props store and no `props/` staging prefix (`render.py:561-565`).
Four tests pin the refusal and all four invert in Wave 7 (`test_loud_discards.py:261,547,608`;
`test_asset_staging.py:103`).

**The styles store has no reader.** `an/stores/styles.py` is nine lines; `rg '\["styles"\]'`
finds only mall wiring. `AssetRef(kind="style")` is validated as legitimate
(`validate.py:114`) and skipped by the compiler — by the absence of a branch, not by a line that skips it, so a scene declaring one
passes validate and renders identically. `an.toml`'s `default_style` is write-only.
Colours live in three disconnected places: the compiler (`_CHARACTER_PALETTES`
`compile.py:124-131`, `_ENV_PRESETS` `:732-738`), the runtime (`runtime.js:259-260,348-351` — six
literals a pack can never reach), and the factory (`_SKIN_TONES`/`_HAIR_TONES`, plus a **second,
disagreeing** five-entry palette table at `factory.py:547-553`).

**`migrate()` never runs on a SceneIR.** Every call in the tree passes
`kind="CharacterDescriptor"` (`validate.py:173,268,321`; `characters/cli.py:138`;
`compile.py:473,1243,2066`; `characters/validate.py:205`; `factory.py:450`).
`ScenesStore.__getitem__` validates raw JSON with no migration (`stores/scenes.py:44-48`), and so
do `sync()` and `project.load`. **A registered scene-IR migration is currently decoration** — and
because `_IRModel` is `extra="allow"`, a renamed field would land as a *silent default*, not an
error. This is the single most load-bearing finding of the wave.

---

## 2. Brief vs reality — corrections to the epic's Wave 7 text

1. **"the camera stops being a scale tween on a synthetic root"** — the root is not synthetic to
   the runtime; it is the one node the runtime names, and it is already a camera (§1). The work
   is to *use* it, not to replace it.
2. **`f = 1/(1+depth)`** (the brief's formula) is a reparameterisation of the standard law and no
   surveyed tool spells it that way. Use a ratio (§3).
3. **"a flat 2D zoom would give identical ratios"** is false as written: today's centre-anchored
   zoom already gives *unequal* per-plane displacements, so a naive "displacements differ ⇒
   parallax" test passes on a scene with no parallax at all. The measurement has to be designed
   against that false positive (§8).
4. **"props … things a character holds"** — the default rig has **no hand**
   (`_default_bones`/`_default_slots`); arms pivot at the shoulder, and `bones_from_pivots`
   silently ignores a drawn `hand_l`. Attaching a prop to a hand is a Wave 4 rig-contract change,
   not a Wave 7 one (§6).
5. **Wave 7 is budgeted at two PRs.** The honest count is eight sub-PRs grouped into two rounds
   (§9). Recorded as a deviation rather than met by making the PRs bigger.
6. **The epic's Decision 3 puts the rename "early in 7b" and leaves an alternative undecided**
   (rename the *store* instead, "decided **before** 7b starts"). §9 moves it to 7a's first
   code PR, because it is the only Wave 7 change provably pixel- and contract-neutral and it
   wants the rename budget while the corpus is quiet. The alternative is hereby declined, and the
   reason is recorded rather than assumed: renaming the store leaves `Shot.style` — the field an
   author types — carrying the collision.

---

## 3. The model: one camera, one factor per plane (decided)

Every formula surveyed is the same affine expression under different framings:

```
screen_displacement_of_plane_i  =  − f_i · camera_displacement · zoom
```

Godot's `Parallax2D.scroll_scale` states the semantics we adopt — *"a value of `1` scrolls at the
same speed as the camera… greater than `1` scrolls faster, making objects appear closer. Less
than `1`… further, and a value of `0` stops the objects completely"* — though it does not print
our algebra; the older `ParallaxLayer` spells the same idea `motion_scale` and is now marked
deprecated. **Do not cite `ParallaxLayer.scroll_scale`: no such property exists**, and conflating
the two classes is the citation error this line was rewritten to remove. After Effects states the
size half in one sentence (*"a layer that is the Zoom
distance away appears at its full size, a layer that is twice the Zoom distance away appears half
as tall and wide"*), which is the same number: `f = focal_z / z`.

**`an` gets the camera-locked frame for free**, because the JS `root` *is* that frame:

```
root.pivot   = (cam_x, cam_y)                 # the camera pose — one node, two channels
plane_i.x    = x0_i + (1 − f_i) · cam_x       # the parallax compensation
plane_i.y    = y0_i + (1 − f_i) · cam_y
⇒ screen_i   = (W/2, H/2) + S · (x0_i − f_i · cam)
```

Verified numerically by porting the two vendored composition lines into Python: at 320×240 with
`cam_x = 80`, planes at `f = 0, 0.5, 1, 2` land at screen x `160, 120, 80, 0` — displacement
ratios exactly `f`, holding under zoom. **A plane at `f = 1` emits nothing**, so characters ride
the camera for free and can be tweened during a move without colliding with a compensation
channel.

**The wire primitive is `depth`, a ratio in Godot's coordinates** — one field, one meaning:

| `depth` | meaning |
|---|---|
| `0.0` | infinitely far: frozen in frame, neither pans nor scales |
| `0 < d < 1` | background (Godot's own five-layer sanity range: 0.1 sky → 0.7 forest) |
| `1.0` | the character plane — today's behaviour for everything |
| `> 1.0` | foreground: nearer than the characters, moving faster |

Negative is refused by the schema. `parallax: (fx, fy)` is the optional **per-axis override**
(default `(depth, depth)`), for a plane that scrolls horizontally but not vertically.

**Two collisions resolved, because two threads named the same number differently.** T1 proposed
`parallax: float` as the wire primitive with `z` as sugar; T2 proposed `depth: float` with
`parallax` as the per-axis tuple. One number must have one name: **`depth` is the scalar,
`parallax` is the per-axis override tuple.** And the `z`/`focal_z` physical-staging sugar
(`f = focal_z/z`, which buys apparent-size and a true dolly for free) is **deferred to 7b with
the dolly**, because 7a's done-when needs only the factor.

**Sign conventions, pinned in the schema docstring, because every surveyed tool disagrees.**
Larger `depth` = nearer = faster (Godot's ratio). Unity's z-derived factor uses the **inverse**
convention (`f_unity ≡ 1 − f_godot`) — a live sign trap for anyone reading a Unity tutorial while
writing this code. For the deferred `z` sugar, `+z` = away from the camera (AE and OpenToonz;
Moho is the outlier).

**Paint order stays authored, and is not derived from depth.** Five of the seven systems surveyed
keep depth and stacking decoupled; where the coupling exists it is opt-in (Moho's "Sort layers by
depth" checkbox). In `an` it is not even available: the runtime sets no `zIndex` and no
`sortableChildren`, so coupling would be a runtime change. A foreground plane happens through
Rive's shape — **relative ordering against a named target** — as one optional field,
`characters_after: str | None`, whose `None` default reproduces today byte-for-byte and which
dissolves the `depth == 1.0` tie instead of resolving it by convention. It also expresses what a
depth rule cannot: a fence at `depth = 0.9` that the characters stand *behind*.

---

## 4. The camera IR (decided)

Named moves stay the front door; an explicit pose is the implementation — **one code path, two
front doors**, the shape the dialogue `[emotion]` sugar already uses (`wave6_research.md` §5).

```python
class CameraKey(_IRModel):
    at: Seconds = 0.0
    x: float = 0.0          # camera position; +x moves the camera right, content left
    y: float = 0.0
    zoom: float = 1.0       # on-screen magnification (root scale_x/scale_y)
    rotation: float = 0.0   # radians; camera roll
    easing: EasingSpec | None = None   # None, NOT "ease_in_out" — see below

class Camera(_IRModel):
    move: str | None = None              # a named preset; sugar for `keys`
    keys: list[CameraKey] | None = None  # the explicit door; None = use `move`
```

**Two defaults that look harmless and are not.** `easing` defaults to `None` because today's
emission puts `"ease_in_out"` on the **first** key and `null` on the terminal one (measured:
`[(0.0, 1.0, 'ease_in_out'), (2.0, 1.25, None)]`); a per-key default of `"ease_in_out"` puts it on
both and moves the hash. And `keys` defaults to `None`, not `[]`, because the md writer dumps the
camera with `exclude_none=True`, which **keeps empty lists** — an empty default would write
`keys: []` into every camera block it regenerates.

`_CAMERA_MOVES` becomes a table of key lists. **The five existing names must desugar to exactly
the document they produce today**, which is more specific than "a scale tween": two animations
named `__camera__<shot>_scale_x` / `_scale_y`, each one channel targeting `root`, keyframes
`[(0.0, s0, "ease_in_out"), (duration, s1, null)]`, on **two** tracks whose `target_root` is the
string `"__camera__"` — and *no* pivot channels. New in 7a: `pan_left`,
`pan_right`, `tilt_up`, `tilt_down`. Deferred to 7b: `dolly_in`, `dolly_out` (a true dolly grows
the foreground ×1.40 while the moon grows ×1.02; today's `push_in` grows both ×1.25 — precisely
the uniform zoom the 1937 multiplane camera was built to replace).

Setting `move` and `keys` at once **raises**. `position`, `target` and `focal_length` are removed
by a registered migration (§9, PR 0 first). In film a lateral translation is a *truck*, not a
pan; on an orthographic 2D stage they are indistinguishable and the epic's done-when says
`pan_left`, so `pan_left` it is, with the ambiguity in the docstring rather than resolved by a
purist rename.

**Zoom composes with pan through the pivot, and that is the correct default.** Pixi scales about
the pivot, so a push-in during a pan zooms toward what the camera is looking at, not toward a
fixed frame centre.

**The collision rule.** A compensation channel targets a node the author might also target, and
the evaluators are later-wins with camera clips appended last — so the failure is *silent*. It is
not hypothetical: today `set root scale_x 3.0` together with `camera.move: push_in` evaluates to
**1.25** at the shot's end, the authored value discarded, no warning. The stage emitter must
**detect** an authored `set`/`tween` on `(node, x|y)` for any node carrying a non-unity factor and
**raise**, naming both. That is a deliberate divergence from `_add_face_clips`, which resolves the
same class of collision by warning and letting the authored channel win: a camera is not a face,
and a silently-ignored pan is worse than a refused compile. Additive folding is the eventual right
answer and is **not Wave 7**.

**Validate must refuse, never no-op** (severity `error`): an unknown `move`; `move` and `keys`
both set; `keys` unsorted or out of `[0, duration]`; `zoom <= 0`; `depth < 0`; `depth` and a
`parallax` tuple disagreeing; a plane depth on a node the shot does not build; and the collision
above. A camera translation on a stage whose planes are all `depth = 1` is a **warning** — "pan a
single-plane stage" is a legitimate, if flat, request.

---

## 5. The environment: planes (decided)

A new document kind in the `environments` store — versioned, `extra="allow"`, registered as its
own `DocumentKind` from `an/environments/schema.py` with a `0.0.0 → 0.1.0` migration that carries
today's free-form `name`/`description`/`tags` through untouched.

```python
class Plane(_EnvModel):
    name: str                                     # node name; becomes `env_id/<name>`
    art: PlaneArt                                 # fill | gradient | image | generated
    depth: float = 1.0                            # §3
    parallax: tuple[float, float] | None = None   # per-axis override
    offset: tuple[float, float] = (0.0, 0.0)
    anchor: tuple[float, float] = (0.5, 0.5)
    size: tuple[float, float] | None = None       # None = the art's own raster
    fit: Literal["stretch", "contain"] = "contain"
    repeat: Literal["none", "x", "y", "xy"] = "none"

class EnvironmentDescriptor(_EnvModel):
    schema_version: str = ENVIRONMENT_SCHEMA_VERSION
    kind: Literal["EnvironmentDescriptor"] = "EnvironmentDescriptor"
    name: str
    planes: list[Plane] = []              # LIST ORDER IS DRAW ORDER
    characters_after: str | None = None   # §3; None = today's behaviour exactly
    anchors: dict[str, tuple[float, float]] = {}
    source: AssetSource | None = None
```

Four decisions worth their reasons:

- **No `z` integer field.** List order already carries draw order and the runtime could not
  honour a `z` anyway (no `zIndex`). Unity's "Order in Layer" deliberately not adopted.
- **`anchors` replaces a `horizon` field.** A horizon is a named point like any other stage mark;
  two fields for one fact is how the intersecting override got here.
- **`source: AssetSource`** is not decoration: `collect_credits` walks **only**
  `mall["characters"]` and says so (`an/credits.py:110-116`). The PR that gives environments art
  is the PR that closes that hole, or `an credits` becomes an affirmative false statement about
  plates.
- **No `rest_camera`, no `design_resolution`.** The camera is `Shot.camera`, per shot; a field
  needs its producer and its consumer in the same change (`serialize.py:9-19`).

**`repeat` ships with its runtime or not at all.** `TilingSprite` is in the vendored bundle and
never constructed; schema with no consumer is the failure mode this rule exists for.

**Byte-identity: take the free route.** Build planes only when the store entry declares them, and
leave `_ENV_PRESETS` output byte-identical. Two of the eight ledger scenes carry an environment
(`misc/bench/corpus/multi_shot`, `examples/promote_demo`), so re-expressing the presets as planes
would move exactly those two hashes for no picture change. A richer default look ships as a new
preset (`park_multiplane`), **never** by changing `park`.

**Two blockers to clear in the same PR:** the override filter silently drops a `planes:` key
today (§1), and the `an` skill claimed (before this PR) that unknown environment keys *raise* when the compiler only warns — so the one place a reader would check is wrong in the safe-sounding
direction.

---

## 6. Props (decided)

**A `PropDescriptor` as a thin profile of `CharacterDescriptor`** — sharing `Bone`/`Slot`/
`Attachment`/`Skin` and one extracted rig builder, with different *defaults*: one bone, one slot,
no animations, no swap sets.

Refuted, with the code that refutes it:

- **"a prop is a character with `kind: prop`"** — `CharacterDescriptor.model_post_init`
  (`schema.py:361-377`) re-seeds a seven-bone humanoid with a face and a blink from an empty
  list, so `CharacterDescriptor(name="sword")` *is* a person; the placeholder fallback draws a
  humanoid where a lamp should be (the an#33 failure mode); `an character validate` scores 21
  blocking findings on a correct prop; and about ten call sites already branch on kind (`ir/validate.py:147,352,353,446`; `compile.py:461,696,702,709,717,2051,2508`) — the "five compiler edits, four of them one line" estimate below is the *edit* count, not the branch count.
- **a distinct minimal document with `states`** — `states` is `asset_sets` renamed, which needs a
  rename table, and it has a hard ceiling at one moving piece.

**The block is three strings and one default**: `_svg_asset_src`'s `"characters/"`
(`compile.py:1053-1055`), `_part_probe`'s copy (`:1093`), the missing `props/` staging prefix, and
the humanoid seeding. Five compiler edits, four of them one line.

**Placement is IR, not wire, and therefore hash-free by construction**: one additive
`AssetRef.stage: StagePlacement | None` (`at`, `scale`, `depth`, `after`) — `depth` and `after`
in §3's vocabulary, no second one. `scene_contract_sha256` hashes the *compiled* document, so an
`AssetRef` field never reaches it, and `scene.md` is unchanged via `exclude_none=True`.

**Attaching a prop to a character is deferred.** `_track_root_of` (`compile.py:2021-2023`) makes
entity identity the first path segment, so an attached prop's swap sets would be attributed to
its host and refused; and there is no hand bone (§2.4). Both are separate, named work.

---

## 7. StylePack, and the renderer rename (decided)

**A pack does NOT recolour SVG art at compile time.** Four code-backed reasons: it breaks `src`
content addressing and the asset-resolution ledger; the only substitution precedent
(`_skin_fill_of`, a regex) is exactly what `bench/palette.py` had to abandon for XML parsing; no
role tagging exists in `CharacterDescriptor`, and inferring a role from a pixel already caused
an#99's wrong-tone lid; and `tint` occurs zero times in the runtime. **A pack reaches SVG art
through the factory at authoring time** (which already owns the colour seams), and the compiler
**warns** naming the entities it could not reach.

The document lives in the `styles` store — giving that store its first reader — as a registered
`DocumentKind` with hex-string colours (deliberately not DTCG colour objects: `bench/palette.py`
mirrors `runtime.js` verbatim and a second conversion doubles the silent-divergence surface),
`roles`, a `line.width`, and per-entity overrides. **A pack must not declare a role it cannot
change**: `lip`, `mouth_fill`, `teeth`, `tongue`, `eye_sclera` are runtime literals, and a role
that silently does nothing is worse than an absent one — guarded by a test.

**Byte-identity when no pack is declared** rests on three mechanical rules: no new `VisualJSON`
field (the `fit` counter-example, `serialize.py:111`), `meta.style_pack` popped by the existing
omit-when-unset wrap serializer (`serialize.py:284-290`), and the no-pack branch returning
today's literals unchanged — a lookup with a default, not a rewrite. **Write the omit serializer
in the first commit**, not as a follow-up: this is the one Wave 7 feature that can move every hash
in the corpus.

**The rename `Shot.style` → `Shot.renderer`** (plus `Meta.default_style` and the retirement of
`AssetRef(kind="style")`) is cheaper than it looks: `_SHOT_HEADING_RE` captures `(cutout)`
*positionally*, so the `## Shot s1 (cutout)` heading never changes and **muvid needs no edit**.
The real work is `default_style:` in the meta block (which must **raise**, not drop), 28 source +
132 test sites, 19 corpus/example files, and `iterate.py`'s model-facing grammar. One migration
carries the rename and the `kind="style"` retirement together (Wave 4's "two migrations where one
would do" rule) — **and it only works at all once PR 0 lands** (§1: scene migrations never run).

---

## 8. Measurement (decided)

**The trap first.** Today's zoom camera already gives unequal per-plane displacements, so the
epic's sentence as written is satisfied by a scene with no parallax. The honest null hypothesis is
*"the camera moves the whole stage as one rigid image"*, and a centre-anchored zoom has to be
excluded, not assumed away.

**The quantity: probe each plane at scene-space `x = 0`** (the canvas centre column). The zoom
term vanishes exactly:

```
Δ_i = s₁ · p_i · D₁        ratio_ij = Δ_i / Δ_j = p_i / p_j      (for ANY zoom s₁)
```

Under a rigid pan every ratio is exactly 1. Two additions the epic does not require and should
get: assert **`Δy ≈ 0`** — *for a zoom-free pan only*; §4 endorses zoom composing through the
pivot, and a pan+zoom shot measured `Δy = −75/−45/−15/+15` — and assert the **ordering**
`p_far < p_mid < p_near`, because wrong-order parallax is a real bug a bare inequality passes.
Note the ordering check is a **bonus, not a second gate**: the zoom false positive above satisfies
it too (−10 < +10 < +30). Only the x = 0 probe excludes a zoom.

**Measurement (a), JSON, free, on every PR:** the *composed screen-space* x of each plane's probe
point at the two pinned times — composed, not per-channel, because a rigid pan on `root` gives
every plane local `Δx = 0`. **This is more than promoting a helper.** `evaluate_timeline` returns a
pose (`{(target, property): value}`); nothing in `an/` composes `world = position + M·(local −
pivot)` or walks node parents. Promoting `_python_timeline`
(`tests/test_swap_channels.py:64-107`) into `an/adapters/cutout/timeline.py` is the prerequisite;
**writing the compositor is the work item**, and it must agree with the vendored engine's own two
lines rather than re-deriving them.

**Measurement (b), pixels, labelled PR:** a `stage_pan` corpus fixture with three distinctly
coloured planes; per-plane centroid displacement between goldens via exact-colour masks. **The
x = 0 cancellation does not reach (b):** a centroid sits at the plane's own offset, not at x = 0,
so the fixture must hold zoom **constant** — measured, with planes at offset −60 under a
simultaneous zoom, the ratios read 0.2 / 0.6 / 1.0 / 1.8 instead of 0 / 0.5 / 1 / 2. And **assert
each plane's mask pixel count is unchanged between the two frames**: a plane panning partly
off-canvas biased a `depth = 2` ratio to 1.975, which would silently set the tripwire's floor
against a clipped number
(`an/bench/masks.py` is **not** reusable — it has no colour selection; the primitive is
`metrics.pack_rgb` plus equality). One diagnostic ledger row —
`stage_min_plane_ratio_gap`, family B, `role="diagnostic"`, `Optimum(kind="guard")`, counting
zero, **gated** under both edge levers — plus a `stage_planes_parallaxed` tripwire, with the floor
set at half the first bless's measured minimum. That is the
`expression_min_pairwise_changed_px` / `MIN_PAIRWISE_CHANGED_PX = 53` precedent followed
literally.

**The negative** (`pan_left` on a plane-less scene must raise) already raises at both ends today
and must be **re-pointed**, not deleted. Note extent is not the test: the legacy backdrop is a
flat 4000 px band that pans to zero pixel change.

**Keep the corpus vector.** A raster plate takes a different loader (`createImageBitmap` with
premultiplied alpha and implementation-defined colour-space conversion, on a worker pool sized by
`navigator.hardwareConcurrency`) — none of it watched by the determinism report's five fields
(`determinism.py:141-147`), and the cross-arch verdict does not cover it. Generate corpus plates
as SVG; measure raster in its own scene, excluded from family B until a cross-arch capture
exists.

**Do not register a `flat_camera` mutation lever.** A compile-time parallax change moves the
contract hash and is refused at comparability — verbatim the recorded `step_hz` verdict. The proof
that the tripwire can fire belongs in `an bench-mutants` as two declared guard mutants (flatten
the factors; swap the ordering), with a "NOT REGISTERED — refused at comparability" row in the
bench skill's lever table so nobody re-derives it.

---

## 9. The rule, and the PR structure

> **A contract hash moves only when that scene's picture-contract actually moved. A knob nobody
> turned must never move it.**

The cost is asymmetric: a golden re-bless retires family B for one scene and has a recorded-reason
protocol; a contract-hash move retires **every metric in that scene against every committed row**,
with no recovery (`compare.py:348-359`). Scored:

| feature | byte-identical for every pre-existing document? |
|---|---|
| translating camera | **yes, free** — *provided it stays a channel emitter and never becomes a node* |
| props | **yes, free** — the path is reachable only by scenes that declare one |
| plane environments | **yes if store-declared only**; re-expressing the presets moves two hashes |
| StylePack | **only if "unset" is expressed by absence in the serialized document** |

**Round 7a — nothing moves a hash.**

0. **Wire `migrate()` into the SceneIR read path** (`ScenesStore.__getitem__`, `sync()`,
   `project.load`), with the test that fails on today's tree. Every later migration depends on
   it, and without it a rename lands as a silent default.
1. **`Shot.style` → `Shot.renderer`** + the `kind="style"` retirement, one migration. The only
   Wave 7 change provably pixel- and contract-neutral, so it lands while all eight hashes can be
   asserted equal to the committed row. (The epic schedules this in 7b and leaves "rename the
   store instead" open; §2 correction 6 records the reschedule and declines the alternative.)
2. **Promote `_python_timeline`** into `an/adapters/cutout/timeline.py`, **and write the
   compositor it needs** — a pose is not a position (§8).
3. **Props** — the rig-builder extraction lands **alone**, with no exemption, because it is the
   one route that could move a hash by accident; then the prop path, `validate.py:113` and the
   compiler's refusal inverted together.
4. **The translating camera** as channels on existing nodes: the `Camera` IR, the dead fields
   removed, the refusals re-pointed, the collision rule, the `an` skill's stale
   environment-override claim fixed.
   **The ledger guard cannot see this PR.** No corpus fixture emits a camera clip at all — seven
   of the eight have `camera=None` and `promote_demo` declares `hold`, which early-returns — so
   `test_every_corpus_contract_hash_equals_the_committed_ledger_row` is *vacuously* green for the
   camera. Pin the emitted document directly (or add a camera corpus scene); do not mistake the
   green guard for evidence.

**Round 7b — one hash moves, deliberately, and it belongs to a scene that did not exist before.**

5. **Plane environments**, store-declared only; the legacy preset output asserted byte-identical.
6. **The `stage_pan` fixture + goldens + the metric + the tripwire + `tests/test_stage_parallax.py`.**
7. **StylePack**, last, omit-if-unset from commit one. If it turns out a pack *cannot* be
   no-op-when-unset, stop and ask — it is the only item that would justify a whole-corpus
   re-baseline, and that must be argued in a PR body, not slipped in.

The dolly (`dolly_in`/`dolly_out`, the `z`/`focal_z` sugar, apparent-size preservation) is 7b's
tail or Wave 7's successor — deliberately after the pan, because the pan is the done-when.

---

## 10. Risks and open questions

1. **`migrate()` on the read path may change behaviour for documents already on disk** — it is a
   fix, but it is a fix that runs code that has never run. PR 0 must land alone with the
   round-trip tests.
2. **The rig-builder extraction (props)** is the highest-risk edit in the wave for byte-identity,
   with four named break routes.
3. **`repeat`/tiling** needs `TilingSprite` wired in the runtime — a browser-lane change, verified
   only on a labelled PR. Ship both halves or neither.
4. **Raster determinism is unmeasured**, and the Wave 2 cross-arch verdict does not list it among
   its exclusions — an omission, not a clearance.
5. **Supersampling does not sharpen textures** (`getResolutionOfUrl` defaults to 1; `@2x` is the
   undocumented lever) — relevant the moment plates arrive.
6. **`an credits` walks only characters** and becomes a false compliance statement the day
   environments carry art.
7. **Additive folding of authored motion and camera compensation** is the right eventual answer to
   the collision rule and is explicitly not Wave 7.
8. **`step_hz` exemption.** The camera is exempt *by construction* — it is its own emission site
   (`compile.py:2938`), not a string-sniffed exception, and `tests/test_step_hz.py:235` asserts
   the camera clip keeps exactly two keyframes and non-`step` easing under `step_hz=10`. Wave 7
   adds a **third** emission site (plane compensation), and it must live on the camera's side of
   that fence or a stepped plane will judder under a smooth camera — the exact failure the
   exemption exists to prevent. Generalise that test to the new channels.

**Found in passing, outside the wave:** muvid's animation renderer writes
`camera: {move: static}` into the `scene.md` it hands to `an.orchestrate`, which `an` has never
implemented — reproduced against 0.1.58 and filed as
[muvid#44](https://github.com/thorwhalen/muvid/issues/44).

---

## Sources fetched 2026-08-25

PixiJS v7 `DisplayObject` / `Container` transform docs and the vendored `pixi.min.js`
composition; Godot `ParallaxLayer` / `Parallax2D` (`motion_scale`, `motion_mirroring`) and the
parallax-background tutorial; Unity 2D sorting layers and tiled sprites; Toon Boom Harmony
multiplane and Maintain Size; OpenToonz "additional Z position"; Moho layer depth and the
sort-by-depth checkbox; After Effects camera Zoom; Lottie precomps and `sid` slots; Rive draw-order
rules and data binding; Spine skeletons/skins and PhotoshopToSpine; Live2D parts and PSD import;
W3C Design Tokens Community Group format; Style Dictionary; CSS custom properties; Manim config;
Krita `.kpl`; WHATWG/MDN `createImageBitmap` premultiply and colour-space options. Each thread's raw
notes — committed under `misc/docs/research/wave7_T1..T5.md` — record the exact URL and the quoted
sentence; anything unfetchable is marked UNVERIFIED there and is not relied on here. One citation
degraded between notes and synthesis on the way (the Godot class name, §3) and was caught by the
adversarial pass; committing the notes is what makes that catchable at all.
