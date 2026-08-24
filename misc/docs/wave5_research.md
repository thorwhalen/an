# Wave 5 research — swap channels

Measured 2026-08-24, before any Wave 5 code. Seven parallel research threads over the
codebase and the external literature, each followed by an adversarial pass instructed to
refute it. The adversarial passes killed or corrected **19 sub-claims** (including five
citation errors and two wrong numbers in the research itself); what is below survived
both passes or was corrected by them. Where this document and the code disagree, the code
wins and this document gets fixed.

Authority order for Wave 5 work: this file, then the epic #9 Wave 5 brief. Where they
disagree, this file is right — it was measured and the brief was written before the
codebase reached its current state. Same convention as `wave2_research.md` /
`wave3_research.md` / `wave4_research.md`.

---

## 1. The brief's two traps, measured

### Trap (a) is REFUTED — and the real hazard is its inverse

The brief: *"a swap channel with non-step easing silently holds the wrong key for half a
segment, because the existing lerp returns the second value only at t >= 1.0."*

Measured against `runtime.js:86-108` (mirroring the exact JS formulas): within a segment
`u` is strictly < 1 (scan condition at :96), and **all six named easings return
`eased < 1.0` for every `u < 1`** — so the non-numeric snap
(`eased >= 1.0 ? b.value : a.value`, :108) yields `a.value` for the entire segment and
switches exactly at the next keyframe via segment advance. **Behaviour identical to
`step`, for every named easing.** There is no "wrong key for half a segment."

Two real holes, both narrower and one inverted:

1. **Overshoot cubic beziers.** `cubicBezier` clamps endpoints but not the curve:
   measured, `(0.5, 2.0, 0.5, 2.0)` crosses 1.0 at u≈0.257 and shows the **second** key
   early for ~74% of the segment (the inverse of the brief's claim), and
   `(0.3, 3.0, 0.7, 0.0)` **flaps A→B→A within one segment** (crossings at u≈0.146 and
   u≈0.652). This is authorable from the IR *today*: `TweenAction` forwards any easing
   list verbatim (`compile.py:1157`, `:1181-1188`) and a string-valued tween skips the
   `_rest_value_for` guard when `from_value` is given.
2. **A ULP-scale hole in the "named easings are safe" theorem**: for `u` within ~2⁻²⁷ of
   1, `ease`/`ease_out`/`ease_in_out` round to exactly 1.0 in float64, so the snap fires
   early inside the final ~7.5e-9 of the segment. Invisible at frame granularity, but it
   means "any named easing behaves like step" is a float accident, not a theorem.

**Decision:** two guards, both mutation-tested. The compiler **forces `easing="step"` on
every swap-channel keyframe and warns when it overrides** an authored easing (the brief's
prescription survives as hygiene — Spine's attachment keyframes carry `{time, name}` and
no curve field at all, so "swaps are stepped by format" is the industry invariant, §10).
And the evaluator **snaps non-numeric values on TIME (`t >= b.time`), never on an eased
or derived parameter** — this design went through three drafts, each killed by
adversarial review: "clamp `eased` into [0,1]" is wrong because a clamped overshoot
still *reaches* 1.0 mid-segment and snaps early; "snap on the raw segment position"
is one float division away from wrong because `(t - a.time) / span` can round up to
1.0 while `t < b.time` (reproduced in both languages with real keyframe times). The
time comparison has no intermediate arithmetic, so hold-for-exactly-`[a.time, b.time)`
is a theorem. Easing is still validated per segment (a typo'd name stays loud on a swap
channel too — pinned, since the "validate only on the numeric branch" mutation survived
the first battery). Landed identically in `runtime.js::evaluateChannel` and
`channel.py::evaluate` (the spec — §3), pinned by the parity battery.

### Trap (b) is VERIFIED — and understated: seven silent paths, not one

`setVisemeOnMouth` fails silently on an unknown key, and six more ways
(`runtime.js:370-397`, `:336`; `compile.py:1019-1023`):

1. Sprite path, key not in `_anVisemeAssets` → previous texture silently stays (:378-391).
2. `PIXI.Assets`/`.get` absent → silent return.
3. Alias maps but texture missing → silent (mostly theoretical; a failed preload rejects
   `Assets.load`).
4. No matching Sprite AND no Graphics child → whole channel evaluates to nothing (:394-395).
5. Procedural path, unknown code → `|| VISEME_SHAPES.X` silently draws the closed mouth (:336).
6. Case asymmetry: the sprite path upper-cases (:378), the procedural path does not — a
   lowercase `'a'` swaps correctly on an SVG rig and silently draws X on a procedural one.
7. Compile-side feeder: keys whose art is missing are **silently dropped** from
   `viseme_assets` (`compile.py:1019-1023`); the `'incomplete'` resolution record it
   leaves (`compile.py:839-853`) is filtered out by `_raise_or_warn_on_asset_fallbacks`
   (fallback=False), so **no surface ever reports it**.

Contrast: an unknown **target** throws (`runtime.js:298-307`) and an unknown **property**
throws (:413-425). The swap **value** domain is the one remaining silent lane — the
code's own comments call out the asymmetry. The generalisation gives values the same
loudness (§4), with a deliberate policy split between authored and generated channels (§8).

Bonus defect in the same function's neighbourhood: the terminal rest key `'X'` silently
**vanishes** when a viseme keyframe lands at (or clamps to) exactly `line.duration`
(`compile.py:1306-1310` — the clamp runs after thinning, and the rest append is
conditional on `kfs[-1].time < line.duration`), freezing the mouth in its last viseme
forever after the line. Rest must be an invariant, not a conditionally-appended keyframe.

---

## 2. The special-case inventory (the done-when checklist)

Every site where `viseme`/`mouth`/blink/provenance is **control flow** rather than a name.
This is what the done-when's `rg` clause is really counting.

`compile.py`: dead imports `MOUTH_SHAPES`, `REQUIRED_PARTS` (:65-66, used only in a
comment) · `_MIN_VISEME_GAP_S` (:109-110, applied :1298-1303) · viseme-as-discrete-by-
omission in `_rest_value_for` (:168-197 — discreteness is nowhere declared; it is the
complement of `TransformJSON`'s numeric fields) · procedural mouth slot + visual
(:645, :681-690) · `head_has_face` via `_FACE_BAKED_PROVENANCES` (:947-949, applied
:982-983 — drops ALL head-nested face slots, not just the mouth) · `viseme_assets`
population gated on the literal `slot.name == "mouth"` (:1016-1023) ·
`_face_baked_speakers` — a **second, non-equivalent** `art_provenance` read (:1201-1230;
reads the RAW un-migrated store dict with no `kind` guard, where the tree-side read is
migrated+validated — a legacy `parts`-rig with `art_provenance="dicebear"` gets a drawn
mouth that never moves, silently) · `_add_viseme_clips` (:1233-1361): the literal target
`f"{speaker}/head/mouth"` (:1281), `property="viseme"` (:1316), per-keyframe
`easing="step"` (:1307), terminal `'X'` (:1309-1310), and the mouth-existence warning's
`endswith('/head/mouth')` path-shape assumption (:1283).

`runtime.js`: `makeVisual` kind `'mouth'` (:140-145) · `viseme_assets` →
`_anVisemeAssets` stash (:203-206) · `VISEME_SHAPES` + colour constants (:312-333) ·
`drawMouthShape` X-fallback (:335-368) · `setVisemeOnMouth` (:370-397) · `applyProperty
case 'viseme'` (:412) · the static error text enumerating `viseme` (:420-425 —
behaviour-driving text) · the blink block: `_BLINK_PERIOD_S`/`_BLINK_DUR_S` (:622-623),
`applyProceduralBlinks` with the regex and post-pose `scale.y = 1.0` reset (:625-650),
and a **second copy of the blink regex** in `anDeterminismReport` (:687-712).

Contract: `VisualJSON.kind` includes `'mouth'` (`serialize.py:82`);
`VisualJSON.viseme_assets` (:95).

Two seams are **already general and must be kept**: the non-numeric snap in
`evaluateChannel` (typed on the value, not the name — `runtime.js:104-108`) and the
preload pipeline — `compile.py:955-974` registers **every attachment of every slot of
the active skin** precisely "so a swap has its texture already loaded when the key
changes", `an/adapters/cutout/render.py:536-609` stages them, `runtime.js:488-507`
loads them before the tree is built, and `PIXI.Assets.get` + `sprite.texture = tex` +
`refitToBox` is synchronous for a loaded texture. **Multi-key sets need zero new asset
plumbing.**

One warning from the same inventory: **`viseme` is already addressable at any node,
destructively.** `applyProperty` routes the property to `setVisemeOnMouth` with no check
that the target is a mouth; on any Graphics visual (every procedural rect/ellipse/eye)
`drawMouthShape` does `g.clear()` and repaints the node into a mouth. "Addressable at
any node" therefore needs node-side scoping (which sets exist at which node), not merely
removal of the special case.

---

## 3. Four swap vocabularies; the second-evaluator resolution

The codebase carries **four** implementations/vocabularies of "evaluate and apply a
discrete swap", and the standing instruction (CLAUDE.md gaps) is to resolve the dormant
one BEFORE building swap channels:

1. **The live one**: `runtime.js` `evaluateChannel` + `applyProperty`/`setVisemeOnMouth`.
2. **The dormant cluster** `an/adapters/cutout/{scene,timeline,pose,clip,channel,transform}.py`
   (845 LOC + `easing.py` 168, closed — nothing on any executed production path imports
   it; 9 test files). Its **evaluation half is measured-identical to the runtime**: a
   177-check battery + 120 randomized wrap-time checks run through Python
   `channel.evaluate`/`_wrap_time` and the extracted JS `evaluateChannel`/`wrapTime`
   found **0 mismatches except 4 boolean cases** (Python lerps bools — `bool ⊂ int`;
   JS snaps them) and a timeline-layer null divergence (Python passes `None` values into
   the pose; JS drops them, `runtime.js:472-474`). Its **application half structurally
   cannot apply swaps**: `pose.py` derives its allow-list from `TransformParams`, which
   has no `alpha`/`viseme` field, and declares the gap (`UNRENDERED_PROPS`, pose.py:57)
   with a comment deferring the question to Wave 5 by name. `channel.py`'s docstring
   ("numeric values only … swaps arrive in 2B") has been false since Phase 4.
3. **The descriptor idle-animation vocabulary**: `AnimationTrack` targets
   `slot:<name>.attachment` "for swap animations (eyes blinking, mouth visemes)"
   (`an/characters/schema.py:195-226`), seeded into **every** descriptor by
   `model_post_init` (`blink_animation`, `idle_breath`) — and consumed by nothing.
   `blink_animation`'s defaults target the **stale pre-0.2.0 slot names**
   `eye_l`/`eye_r` (`idle.py:94-95`) and its docstring timing is stale too (claims
   0.05/0.13; code computes 0.025/0.155). `random_blink_schedule(seed=...)` (idle.py:133)
   is the deterministic seeded generator the brief asks for, already written and dead.
   `evaluate_track` (idle.py:160-199) is a fourth evaluator in miniature.
4. **The dead Spine sketch in the live wire contract**: `SkinJSON`, `RigJSON`,
   `CutoutSceneJSON.rigs`, `VisualJSON.current_attachment`, `SlotJSON.current_attachment`,
   `NodeJSON.slots` — constructed nowhere (except **three** `SlotJSON` writes in
   compile.py: :635, :645, :1042, all serialized dead weight; `runtime.js` has zero
   references to `slots`/`rigs`/`current_attachment`), yet exported in
   `an.adapters.cutout.__all__`. The same sketch exists in cluster `scene.py`
   (`Visual.current_attachment`, `Slot`).

**Resolution (decided): split the cluster on the evaluate/apply line.**

- **KEEP** `easing.py` + `channel.py` + `clip.py` + `timeline.py` (454 LOC) as the
  executable spec, and extend the existing node-extraction parity harness
  (`test_cutout_loop_modes.py:76-110` — the pattern that already pins `wrapTime`
  bit-identity against a 23-row golden table) to **`evaluateChannel`**, asserting the
  easing-name key sets across the three hand-synced copies (`an/base.py`, `easing.py`,
  `runtime.js`) in the same test. This is exactly where `runtime.js` already cites
  Python as spec (:34, :433, :457). The parity test is **non-optional**: an unverified
  spec that drifts is worse than no spec, and if it were to be dropped, full deletion
  would be more honest.
- **DELETE** `pose.py` + `scene.py` + `transform.py` and their three test files —
  application stays single-model in `runtime.js`; an applier that structurally cannot
  apply swaps would otherwise become the third swap implementation. Rehome the
  `Pose` type + `merge_poses` (imported by BOTH `clip.py:29` and `timeline.py:34`) into
  the kept half, and rewire `test_loud_discards.py`'s declared-gap parity
  (:496-526) to a compile.py-side SSOT — noting Wave 5 itself changes the gap's content
  (the special status of `viseme` disappears; the runtime's applicable set becomes
  "numeric built-ins + the node's declared swap sets").
- **DELETE** the dead Spine sketch everywhere: serialize.py's `SkinJSON`, `RigJSON`,
  `rigs`, both `current_attachment` fields, `NodeJSON.slots` + all THREE compile.py
  `SlotJSON` writes, the `__all__` entries, and the cluster copy (goes with `scene.py`).
  The compiled scene JSON is a per-render transient staged into the runtime dir
  (`an/adapters/cutout/render.py:493-495`); nothing durable stores the *document*. One
  durable **derivative** exists and changes deliberately: `scene_contract_sha256`
  (`an/bench/contract.py`) hashes the full serialized scene, so removing serialized
  defaults changes every fixture's contract hash with zero pixel change — and
  `bench-compare`'s SCENE_KEYS therefore **refuses** comparisons across the an#86
  boundary. That refusal is the instrument working as designed (same event class as a
  fixture re-authoring; Wave 4's rig rewrite crossed the same boundary), the goldens —
  which compare decoded pixels, not hashes — are what carried the zero-pixel proof
  across it, and it is recorded here rather than discovered later.
- **Value policy** (closes the two divergences): the compiler **refuses `bool` and
  `None` keyframe values** on emission — swap keys are strings, numerics are
  `int`/`float`-not-`bool` — and `channel.py`'s snap gains the same
  `not isinstance(v, bool)` guard as its numeric test so spec and runtime agree even for
  hand-built documents. Fix `channel.py`'s lying docstring in the same pass.

---

## 4. Addressing and wire format (decided)

**Scheme (i): the channel property IS the set name** — today's `viseme` scheme,
generalised. `- {kind: set, target: maya/left_hand, property: hands, value: fist}`.

- Scheme (ii) (`property="swap"`, set named elsewhere) is rejected: two sets on one node
  produce the same pose key `target::swap` — one channel is wholly and silently inert,
  and the winner is compiler emission order that nothing pins (deterministic run-to-run,
  but a contract-fragility no test guards).
- Scheme (iii) (`swap:<set>`) buys nothing the compile-time reservation check doesn't,
  and costs compound-string parsing everywhere.

**Name reservation** (compile + validate): a set name must not collide with
`_PROPERTY_REST_VALUES ∪ {rotation_rad}` (the SSOT at `compile.py:113-143`, derived from
`TransformJSON`), must not contain `::` or `/`. Otherwise the static switch silently
shadows the set.

**Wire**: `ChannelJSON` unchanged (target = absolute node path, property = set name,
step-eased string keyframes; `KeyframeJSON.value: Any` already admits strings).
`VisualJSON.viseme_assets` is **replaced** by
`VisualJSON.asset_sets: dict[str, dict[str, str]] | None` (`{set: {KEY: asset_id}}`) —
the same field name as `CharacterDescriptor.asset_sets`, one vocabulary. No alias, no
migration (per-render transient) — but the rename has **two live readers to update in
the same pass**: `an/bench/palette.py:205` (reads `visual.get('viseme_assets')` guarded
by `or {}` — after the rename it would silently collect nothing and quietly shrink the
off-palette reference palette) and the pinned assertions in `tests/test_characters.py`
(:445-449, :468-470).

**Runtime rule**: `applyProperty` keeps the static switch for the numeric built-ins; the
default case looks up the node's child sprite whose `_anAssetSets` contains `prop` and
swaps texture via `map[prop][key]` + `refitToBox` (the re-fit is load-bearing — keep
it); the procedural-mouth branch keys on **visual kind `'mouth'`**, never on the name
`viseme`. If no set matches → throw as today, listing the built-ins **and the node's set
names**. A known set with an **unknown key** → throw naming node, set, and the set's
keys (trap (b), mutation-tested). Two constraints from the existing test harness:
`test_loud_discards.py` executes `applyProperty` **standalone under node**, so the
generalised default case must stay self-contained (or that harness is reworked
deliberately), and any new `Object.keys` iteration in runtime.js must be `.sort()`ed
(`test_determinism_perimeter.py:158-177` sweep).

**Per-key geometry is out of scope, declared.** The swap primitive carries **texture
only**: node transform, anchor, and fit box are baked from the default attachment
(`compile.py:999-1014`), and a swap re-fits into the same box. This works because every
attachment in a slot today shares geometry — a convention nothing validates. A set whose
keys legitimately differ in offset/anchor/extent cannot be expressed; **validate now
warns** when a set's attachments declare differing geometry, and per-key geometry is
future work, not silently wrong output.

---

## 5. Set→slot binding: projection, not declaration (decided)

`asset_sets` is `{channel: {key: attachment_name}}` with **no slot field** — the
viseme→mouth binding is the literal `slot.name == "mouth"` (`compile.py:1018`). The
binding rule that replaces it:

**Per-node projection.** For each slot node, the compiler attaches
`asset_sets = {channel: {KEY: alias}}` built by intersecting every descriptor channel
with **that slot's** attachment dict — exactly what `compile.py:1016-1023` already does
for the mouth, minus the guard, with the silent key-drop made loud (§8). A channel may
project onto **several** slots (that is a feature: `eyelid` projects onto both eye
slots, and a generated blink emits one channel per projecting slot); authored swaps name
their node, so no inference is ever ambiguous.

- **No schema change to `asset_sets`.** Multi-slot single-channel turnarounds are what a
  *skin* is (Wave 4's ruling stands); `body_facing` in the done-when is a **single-slot
  torso set** (front/three_quarter/profile). A full multi-slot turnaround is several
  set-actions at one timestamp, or a future skin switch.
- **Texture aliases become slot-qualified** (`{entity}.{slot}.{attachment}`): the current
  `{entity}.{attachment}` namespace is silently first-wins on collision
  (`compile.py:952`, :730-732), and per-slot shared keys (next bullet) require it. Safe:
  aliases live only in the per-render transient; the two pinned test strings update.
- **Eye attachments rename to shared per-slot keys** (`open`/`closed` in both eye slots,
  paths unchanged) via the 0.3.0 migration (§7), and `default_asset_sets` gains
  `eyelid: {OPEN: "open", CLOSED: "closed"}`. One channel, both eyes, no per-side sets.

**Usage-aware escalation for missing art** (replaces the brief's blanket "missing swap
file fails like a missing part", which would brick both committed corpus rigs — they
ship only `eye_*_open`): an **inventory** gap (declared attachment, no file, key never
referenced) stays `'incomplete'`/non-fatal, preserving the shipped rationale ("a rig
without a blink still renders"); a key the shot's timeline **actually references** whose
art is missing escalates to the fallback bucket (fatal under `strict_assets`, warned
otherwise). Sequencing note: the strict verdict currently fires at `compile.py:302`,
BEFORE actions are compiled (:303) — the escalation requires moving the raise/warn after
action compilation, not a drop-in at the `_record_missing_parts` site.

---

## 6. Blinks: blast radius quantified, design decided

Current blinks are pure runtime JS (`applyProceduralBlinks`), applied AFTER the pose:
inside a window they overwrite eye `scale.y`; outside, they force it to 1.0. So the
brief's "an author cannot animate an eye at all" is **overbroad**: precisely, authored
eye **`scale_y`** is unconditionally clobbered on `<entity>/head/{left_eye,right_eye}`;
x/rotation/alpha/etc. animate fine today.

**Golden blast radius, computed** (JS `_strHash` ported exactly; phases charlie=0.762,
maya=0.284, field=0.706, plates=0.667, ada=0.414; capture is shot-local `t = i/fps`,
fps=24):

| scene | blink windows | golden times | verdict |
|---|---|---|---|
| single_character | [0.952, 1.092) | 0.0, **1.0** | **f0024 mid-blink, scale_y=0.163434** |
| promote_demo | [2.864, 3.004) | 0.0, **2.9167** | **f0070 mid-blink, scale_y=0.120961** |
| graded_field | beyond 0.5s shot | 0.0, 0.1667 | blink never fires |
| saturated_outline | beyond 0.5s shot | 0.0, 0.25 | never fires |
| aa_probe | no eyes (rig has no head) | — | never fires |
| multi_shot | beyond both 0.25s shots | 0.0, 0.0 | never fires |

Across the whole corpus the blink moves pixels on **7 frames total** (single_character
23-26, promote_demo 69-71). Two of twelve goldens are hard pixel gates on the blink
curve/phase/hash — and **both fixtures depend on the blink**: it is the only mover
between single_character's golden pair and the only mover in promote_demo's
(`promote_demo`'s `golden_note` misattributes the 224px diff to "the idle animation",
which provably never runs — nothing on the render path consumes descriptor animations).
Deleting blinks without replacement makes both pairs pixel-identical, which
`bless_scene` refuses (`golden.py:472-481`) — that option silently destroys two fixtures.

**Design (decided):**

1. Delete both regex sites (:634, :692) and the post-pose reset. The **compiler** emits
   per-eye blink channels for every eye node, scheduled by the same entity-name phase
   hash (ported `_strHash`, period 4.0, dur 0.14) so timing is preserved.
2. **Mechanism splits by what the eye can do.** Where the eye slot's projection carries
   the `eyelid` set with a resolvable `CLOSED` key (maya-promoted, every factory
   character): a true **swap channel** through the one swap implementation. Everywhere
   else (procedural rigs; both committed corpus rigs, which ship only open eyes): a
   generated **`scale_y` squash channel** (the sine sampled at frame times). A squash is
   a tween, not a swap — "one swap implementation ever" holds without contortions.
3. **Do not chase byte-identity across the Python/V8 `Math.sin` boundary.** Re-bless the
   two blink goldens deliberately (three-row protocol, reason naming this wave), fix
   `promote_demo`'s `golden_note` misattribution in the same pass, and expect the
   single_character diff to be ≈0 (same formula at the same sample times, float-level)
   while promote_demo's f0070 changes visibly (closed-eye art instead of a squash).
   This **amends the brief's done-when** ("golden frames unchanged except where a swap
   was authored") — the blink move is a deliberate, documented exception.
4. An authored eye `scale_y` tween now simply **coexists** with the generated channel
   through normal timeline layering (later tracks win) — the done-when's "authored eye
   scale_y survives to screen" test becomes an ordinary channel test.
5. **Determinism stamp rework**: `blink_phases` is NOT in `an/determinism.py`'s
   `_REQUIRED_FIELDS` (the Python verdict is untouched), but the JS stamp + regex is
   pinned by a source-text test (`test_determinism_perimeter.py:204-214`), a live
   browser test (`test_determinism_probe_browser.py:74-81`), and a passthrough
   assertion (:291-303). The phase becomes a compiler-side fact recorded in the
   compiled scene (e.g. meta), and all three tests are redesigned in the same PR.
   Note `SceneCapture.determinism` (`an/bench/capture.py:128`) is a dead field the
   render-path comment wrongly claims feeds the ledger — flagged, not inherited.

---

## 7. Provenance → a declarative descriptor fact (decided)

`art_provenance` is special-cased **twice**, non-equivalently (§2). The
absence-of-viseme-set and skin-shape candidates for the declarative fact are
**unworkable**: every on-disk dicebear descriptor carries the full default `asset_sets`,
skin, and real committed mouth art (`default_asset_sets` is the field default;
`factory.py` serializes the full model) — only the provenance string distinguishes them.

**Decision:** add `face_overlay: bool = True` to `CharacterDescriptor` (False = the face
is baked into the head art). One **0.2.0 → 0.3.0 migration** carries four coherent
changes:

- (a) derive `face_overlay=False` from `metadata.art_provenance ∈ {"dicebear",
  "external_avatar"}` (art_provenance reverts to pure provenance metadata; the
  never-written `"external_avatar"` string stops being load-bearing);
- (b) rename eye attachment **keys** to per-slot `open`/`closed` (paths unchanged) in
  skins and `Slot.attachment` defaults;
- (c) seed `asset_sets["eyelid"] = {OPEN: "open", CLOSED: "closed"}`;
- (d) rewrite stored **animation tracks**: `slot:eye_l.attachment` →
  `slot:left_eye.attachment` etc., and frame values `eye_l_open` → `open` — the 0.2.0
  migration renamed slots in `slots`/`skins` but never touched `animations`, so every
  stored descriptor carries stale targets (latent only because nothing consumes them —
  PlayAction resolution, §9, makes them live).

Both compile-side reads route through the **migrated, validated** descriptor —
`_face_baked_speakers` currently reads raw store dicts with no `kind` guard and would
never see a migration-seeded field. `factory.py` writes the field explicitly going
forward; `idle.py`'s `blink_animation` defaults are fixed in code (slot names + key
values + the stale docstring timing). The face-baked speaker's exemption from the
no-mouth typo warning is preserved (the warning's diagnosis would otherwise be wrong for
every dicebear speaker).

---

## 8. Authoring, validation, and the SetAction hold fix (decided)

**No new IR kind.** A swap is a `SetAction` with a string value on a set-named property —
it already parses, round-trips through scene.md's `\```yaml actions` block, and compiles
(`SetAction.value: Any`; `_build_anim_for` emits `easing="step"`). "Maya turns to face
left" is `- {kind: set, target: maya/torso, property: body_facing, value: left, at: 3.5}`.

**But not through the current SetAction compilation.** A `SetAction` currently compiles
to a 0.001s placement window (`compile.py:1119-1124`), so (pre-existing defect, not
swap-specific) **a set at a non-frame-aligned time silently never fires**
(`t=3.02` @30fps has window [3.02, 3.021] between samples), and when it does fire its
persistence is an accident of stateful sequential rendering (false under preview
scrubbing backwards). Swap sets — and numeric sets, same fix — compile to a **step
channel that HOLDS from `at` to the next set on the same (target, property) or shot
end**, the viseme-clip shape. Verified pixel-neutral for the corpus: `graded_field`'s
two `set` actions are at `at: 0.0` and hold to shot end either way.

**Validation, two layers:**

- `an/ir/validate.py::validate_semantic` (which already receives
  `available_characters`): a set-kind action whose property is not a transform property
  must name a declared `asset_sets` channel of the target entity's descriptor with a
  declared key — error severity, the `_RENDERABLE_CAMERA_MOVES` duplicate-and-pin
  pattern for the transform-property list. **Carve-out**: descriptor-less (procedural)
  entities get no such check (their viseme channels are generated and legitimate);
  authored swaps on a procedural entity are an error naming the reason.
- `an/characters/validate.py` grows a generic `asset_sets` pass: every channel key's
  attachment resolves in ≥1 slot (BLOCKING — today's silent freeze-on-key), resolved
  files exist (blocking when the set is the slot's only art, advisory otherwise),
  same-geometry warning per set (§4), and the `MOUTH_SHAPES` fixed-path loop becomes a
  derived case of the general check.

**Compile-time referential check** (defence in depth, next to the animation-existence
re-pass at :1087-1097): authored swap on an undeclared set/key →
`CutoutCompileError` naming the declared channels/keys. **Generated** channels
(viseme from dialogue) keep the tolerant policy but the silent drop becomes a
`CutoutCompileWarning` naming key, slot, and available keys — so the runtime's loud
unknown-key throw (§4) is unreachable from compiled scenes and a hand-written scene
gets a real error. The emotion-brow emitter gets the same node-existence check the
viseme target already has (today a missing brow node hard-crashes at frame time).

---

## 9. PlayAction (an#7): current state and resolution (decided)

The brief's "defensive re-pass fabricates an empty clip" is **stale**: the fabrication
was removed (aac6b38); `play` is now refused at three layers — markdown parse
(`sync.py:280-292`), validate (`validate.py:157-165`), compile (:1087-1097, raising with
a message that cites #7). `_compile_one` still emits the placement (speed honoured), so
only resolution is missing. Note the refusal set is **asymmetric today**:
`_actions_to_yaml_list` happily EMITS play entries that `_extract_actions_block`
refuses, so a programmatically-constructed PlayAction poisons the project's own
scene.md.

**Resolution:** `PlayAction.target` names the entity → `entity.ref` →
`mall["characters"][ref]` → **migrated** descriptor → `animations[name]`. Tracks
convert:

- `bone:<name>.<prop>` → node path via `_primary_slot_per_bone` nesting; **units and
  reference corrected**: `rotation_deg` → `rotation` with deg→rad; sine values are
  **deviations** around rest — keyframe value = node rest transform + deviation·k
  (positions scale by `k = SCENE_PX_PER_VIEW_BOX / view_box_height`; a naive conversion
  teleports the torso to y≈±2 scene px instead of breathing around its rest).
- Sine tracks resample at fps with linear easing; step/linear tracks map 1:1.
- `slot:<name>.attachment` → a swap channel on that slot's node, property = the set
  whose projection on that slot contains the frame values (post-migration, blink frames
  carry `open`/`closed`, which live in `eyelid`); no containing set →
  `CutoutCompileError`.
- **Per-instance clips**: plays mint `__play__{ordinal}` clips (the current placement
  references the named animation directly, so two plays of one animation would share a
  clip — the dedup-with-conflicting-flags case exists on day one otherwise).
  `PlayAction.loop: bool = False` becomes `bool | None = None` (None = the descriptor
  animation's own `loop`); the generated clip's `loop_mode` carries it.
  `PlacedClipJSON.loop_mode` (issue #7 step 2) is **deferred** until clip dedup exists.
- Landing deletes all three refusals in the same pass (they become false rejections the
  moment resolution works), ends the writer/parser asymmetry, and un-gates `play`
  in the scene.md grammar docs.

**Landed in PR #93** (2026-08-24; line references above are pre-#93). The adversarial
review moved resolution out of the compiler into `an.characters.play`, shared with
`an validate` (validate had passed four plays the compiler refused), made a slot track
resolve to exactly ONE set (per-frame resolution split `blink` across two channels and
the runtime's name-order application left the eye open), and widened a looping
duration-less play to the shot end (a loop bounded by its own natural duration never
looped). The "teleports to y≈±2" line above was reasoning, not a measurement — no naive
copy ever ran in this repo; the correct conversion is what `tests/test_play.py` pins.

---

## 10. step_hz (opt-in) and the external practice it encodes

**External anchors** (all fetched 2026-08-24): Spider-Verse ran character animation on
twos/threes (Danny Dimian, fxguide Dec 2018; Josh Beveridge, AWN) while simulation ran
on ones (Jeff Panko, CG Spectrum: "With regular animation you can get by on twos. But
hair and cloth need to simulate on ones"); no first-party "camera on ones" quote was
found — cite Panko + the community workflow (the Animbot/PrattBros YouTube tutorial
"Animate Spiderman on 2s and the camera on 1s"), never an invented quote. Swap-based
systems are stepped **by format**: Spine attachment keyframes carry `{time, name}` with
no curve field; DragonBones swaps an integer `displayIndex`; Toon Boom drawing
substitutions are per-cell. Live2D is the deliberate counterexample (continuous
parameters). At `DEFAULT_FPS=30`: twos ≈ 15 Hz, threes ≈ 10 Hz. Note `an` already
rate-limits its one swap channel **below** threes (`_MIN_VISEME_GAP_S` ≈ 7.1 Hz — the
existing cap stays independent in v1; unifying it under step_hz would change viseme
keyframe times, a pixel change needing its own bless).

**Design:** typed additive fields `Meta.step_hz: float | None = None` and
`Shot.step_hz: float | None = None` (shot overrides scene; None = off). **`sync.py` must
be edited in both directions in the same pass** — `ir_to_markdown` hand-enumerates meta
fields and `markdown_to_ir` whitelists shot-yaml keys, so without sync edits the fields
silently drop on write AND on read. Validate: `0 < step_hz <= fps`. Implementation:
compile-time resampling of tween curves into step-eased keyframes inside
`_build_anim_for` only — **camera exempt by construction** (`_add_camera_clips` is a
separate emission site; no string-sniffing). Stamped into `CutoutSceneMetaJSON.step_hz`
(additive, runtime-inert, bench-readable). Ships with a bench mutation lever
(the supersample pattern: reach the seam from outside, verify the lever applied) so the
ledger + a human side-by-side can judge the default flip — which remains a separate
one-line PR gated on both agreeing.

Docs caveat to carry: a stepped character under a **translating** camera slides/flickers
in screen space (Sony built tooling around exactly this); an's camera is scale-only
today — the raise site at `_add_camera_clips` already documents that limit.

**Landed in #95 (2026-08-24), with three corrections to the design above.**
(1) The grid is *shot-wide* (multiples of `1/step_hz` on the shot's clock, shared by every
tween in the shot; it restarts at a cut because shots compile independently), not
per-tween: "on twos" is a property of the frames, so a tween starting off-grid updates at
the next grid point. The mechanism is sample-and-hold of the eased curve at grid instants
— not the hold → fast-transition retiming of an animator's twos; that is the "naive
quantisation" mode epic #9 Decision 5 flags, and it is what v1 ships, disclosed.
(2) **The bench lever did not ship, and the design's "so the ledger + a human can judge
the default flip" was wrong about the ledger — twice over.** Structurally: stepping moves
`scene_contract_sha256` on every scene with a tween (the resampled keyframes are the
contract), and that hash is a scene comparability key, so `bench-compare` refuses a stepped
row before any family is examined — one step earlier than `pix_fmt` (an#72), which stayed
comparable and failed its exam. By measurement (outside the comparer, `step_hz=12` — "on
twos" at the corpus's 24 fps; a first pass at 15 Hz was a 1.6-frame grid and is
superseded): the two tween-less scenes are identical to the pixel (`source_pixels_sha256`
unchanged); on the other four every golden frame is byte-identical (goldens sit on even
frames, which are grid points, where the stepped pose IS the smooth one), family A has no
direction (+0.02/−5.2/−3.4/−0.0%), family F moves one way (`video_stream_bytes`
−5.2/−5.4/−6.2/−5.9% — one counting family, not three), and C/D/E/G move with the content
both ways (`encode_ringing_excess` +158%/−22%). The instrument is per-frame and cannot see
a temporal choice; what it can say is that per-frame quality is untouched and the encode
5–6% cheaper. Product knob shipped, no registered mutation, numbers in the `an-dev-bench`
skill; the human instrument is the `stepped-timing` demo, its frame strip committed at
`misc/docs/step_hz_side_by_side.png`; the flip is the maintainer's call on that alone.
(3) `meta.step_hz` is serialized only when set, so no bless record or ledger row was
invalidated — the "additive, bench-readable" stamp above would have moved every contract
hash as a `null`. And the range is guarded in the compiler too, not only in validate: a
render never runs validate, and a non-positive rate spun the grid walk forever.

---

## 11. Vocabulary and the licence correction

**Set sizes, cited** (schema imposes no cap; demo sizes are honest): mouths 6–14
(Rhubarb: 6 basic "absolute minimum" + 3 extended; Toon Boom Harmony standard 8; Adobe
Character Animator 14 = 11 audio + 3 silent; anime floor 3; `an`'s 9 sits on the sweet
spot) · eyelids 2–3 states · facing 3–5 keys (5-point/8-point turnaround vocabulary;
cutout puppets typically rig 3 usable facings) · hands "dozens … but only display one or
two" (Harmony docs), 100+ in pro rigs (Matt Watts, Cartoon Brew). Many-to-one is the
rule everywhere: Papagayo's Preston-Blair table collapses 70 CMU phonemes onto 9 target
shapes (10 declared, `rest` never a conversion target); Rhubarb maps whole consonant
families onto shape B. **Do not vendor Papagayo's table** — the file is GPL-2.0, and the
licence perimeter test checks installed metadata only, so vendored text would evade it;
re-derive any Blair mapping from the published chart.

**Names:** `viseme` (already shipped as `VISEME_CHANNEL`, serialized in every
descriptor — renaming any set name is a migration event, not a doc choice) · `eyelid`
(singular — the brief and `schema.py:78`'s comment both use it) · `body_facing`
(documented in "turnaround" vocabulary). The Spine analogy for docs, quoted correctly:
a Skin maps *(slot, placeholder-name)* → attachment, where the placeholder name "is not
necessarily the name of the attachment" — placeholder ≈ our swap key, which is exactly
`asset_sets` indexing into the skin.

**The "public domain" correction: one line, repo-wide.** The only occurrence is
`misc/docs/Real Character Art for an — A 2D Cutout Pipeline Upgrade Plan.md:536`. It is
wrong three ways: Rhubarb's README makes **no public-domain claim**; the Hanna-Barbera
sentence covers **six** shapes (G/H/X are Rhubarb's own additions); and Hanna-Barbera
artwork is Warner Bros. Discovery IP — what is free is the phoneme→shape **scheme**,
uncopyrightable under the idea/expression distinction (17 U.S.C. §102(b)), while
particular drawings are protectable expression. Rhubarb itself is MIT (© Daniel Wolf);
its example mouth images carry no separate licence. `an` draws its own shapes
(`an/characters/mouth_set.py`), so only the uncopyrightable mapping is reused. Also:
Rhubarb's README never uses the word "viseme" (0 occurrences vs 18 of "mouth shape") —
`viseme` is kept because `an` already ships it and it is the standard speech-science
term, not on Rhubarb's authority.

---

## 12. Corrections to the repo's own records (fix when touched)

- **CLAUDE.md pillar 11 is wrong about shot caching**: there is NO read-side shot cache
  anywhere — `an/render.py` renders every shot unconditionally and then writes
  `mall["shots"]`; `iterate.py`'s deletion-invalidation is inert; `shot.id` is an
  author-chosen id, not a content hash. The audio/viseme caches are real. step_hz
  therefore needs **zero cache work**.
- `promote_demo`'s `golden_note` attributes its f0070 diff to "the idle animation",
  which never runs (§6) — it was unknowingly blessed mid-blink.
- `compile.py` imports `MOUTH_SHAPES` and `REQUIRED_PARTS` dead (:65-66).
- `channel.py`'s docstring claims numeric-only; false since Phase 4 (§3).
- `test_cutout_loop_modes.py:12-13` claims "no JS test harness otherwise";
  `test_loud_discards.py` runs node too.
- `an/adapters/cutout/render.py:378-382`'s comment claims the determinism report feeds
  the ledger; `SceneCapture.determinism` is dead and the ledger has no such key.
  (A draft of this list also claimed the `an-dev-rig-contract` skill still carries the
  pre-#84 "compiler merely string-compares `kind`" line — checked, it does not.)

---

## 13. The re-planned wave (PR structure)

Merge-to-main publishes, so every PR must be honest standalone. Goldens split:
PR-A/B/D byte-identical (verify with the bench, label `run-browser-tests`); PR-C
re-blesses exactly two.

- **PR-A — resolve the second evaluator; harden the evaluation layer. Zero pixel
  change.** The §3 split (delete apply-half + Spine sketch; evaluateChannel parity test
  + easing-table pin; raw-position snap for non-numeric values in both languages;
  bool/None keyframe refusal;
  doc/docstring corrections incl. §11's licence line and §12's pillar-11 fix). This
  file lands here.
- **PR-B — the swap generalisation. Zero pixel change.** Wire `asset_sets` (+ the two
  reader updates), slot-qualified aliases, per-node projection, runtime dynamic swap
  with loud unknown-key/unknown-set errors, viseme path becomes a caller (all §2
  compile-side special cases absorbed), authored swaps via SetAction hold channels
  (+ the non-frame-aligned fix for numeric sets), both validation layers, the compile
  referential check, usage-aware escalation, the 0.3.0 migration + `face_overlay`,
  emotion-brow node check, the committed fixture character
  (`tests/fixtures/characters/…`, ~28 files: 12 required parts + 9 mouths + 4 hands +
  3 body_facing keys), mutation tests for both traps, the `an-dev-swap-channels` skill,
  and the demo entry. Done-when proof rides here.
- **PR-C — blinks move into the compiler.** §6 exactly: regex + reset deleted, squash
  channels + eyelid swap channels, determinism stamp rework (3 tests), deliberate
  re-bless of the two blink goldens (three-row protocol), golden_note fix.
- **PR-D — PlayAction resolution (an#7).** §9 exactly; closes #7.
- **PR-E — step_hz opt-in + lever + ledger evidence.** §10; the default flip is NOT
  here — separate one-line PR gated on ledger + human side-by-side agreeing. **As landed (#95): no lever — see the §10 addendum; the ledger half of the flip gate is withdrawn on measurement.**

The done-when amendments, recorded: the "golden frames unchanged" clause takes a
deliberate exception for the two blink goldens (PR-C, §6); the "missing swap file fails
like a missing part" framing is replaced by usage-aware escalation (§5); trap (a)'s
mutation test asserts the **forced step easing + raw-position snap**, not the refuted
"wrong key for half a segment" mechanism.
