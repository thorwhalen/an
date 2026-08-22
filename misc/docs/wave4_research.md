# Wave 4 research — the rig contract, measured

Epic #9's step 1 for Wave 4. Everything here is verified against the source tree at
`2db25ce` (v0.1.41) or measured; nothing is inferred from the brief. Where the brief
and the code disagree, the code wins and the brief is corrected below.

Method: seven parallel research threads, each followed by an adversarial pass whose
instruction was to refute it. Nine claims did not survive. Three of the corrections
were to numbers this document would otherwise have shipped, and one was to an
arithmetic slip that made a defect look *smaller* than it is.

---

## 0. The headline

The brief says art an illustrator delivers does not change what is on screen. That is
**true, and understated in three separate directions.**

1. The rig model is not merely unread — it is **unwritten too**. `promote` and
   `factory` construct a `CharacterDescriptor` from five fields, none of them a rig
   field. So `bones`, `slots`, `skins` and `view_box` have *no producer and no
   consumer anywhere in the repo*.
2. The strongest evidence is a **measurement, not a reading**: gutting all four rig
   fields — `view_box` to 64, `bones` to `[]`, every `draw_order` inverted, `slots` to
   `[]`, every attachment `path` repointed at a non-existent file, every `anchor` to
   `(0, 1)`, every attachment `width`/`height` to 999/3, `skins` to `{}`, and all of
   them at once — leaves the emitted `CutoutSceneJSON` **byte-identical**. Two
   controls (`viseme_map`, `metadata.art_provenance`) do change it, which is what
   makes the null result meaningful.
3. A missing part does **not** become a white rectangle. It does one of five other
   things, two of which are worse.

---

## 1. What the compiler reads

Four descriptor keys are read at five sites; **three are load-bearing**.

| key | site | effect |
|---|---|---|
| `kind` | `compile.py:554` | selects the descriptor branch |
| `name` | `compile.py:718` | **none** — assigned to a local that is never loaded |
| `viseme_map` | `compile.py:752` | mouth alias table |
| `metadata` | `compile.py:765`, `:1098` | `art_provenance` face-baked suppression |

Setting `desc["name"] = "totally-different"` leaves the compiled scene byte-identical.
The brief's "three fields" is right; "four reads, three with effect" is exact.

**Dead across the whole repo** (no producer, no consumer): `view_box`, `bones`,
`slots` (and every `draw_order`), `skins` (and every `Attachment.path`, `.anchor`,
`.width`, `.height`), and — a fourth the brief does not list — **`animations`**, the
idle rig, which `model_post_init` seeds with `idle_breath` and `blink`.

`runtime.js` contains zero occurrences of `rigs`, `skins` or `attachment`.

### Corrections to the brief

- **`skins[].attachments[].anchor` is not a field path.** `Skin` has `name` and
  `slots`; the real path is `skins[<skin>].slots[<slot>][<attachment>].anchor`.
  `attachments` belongs to `SkinJSON`, an unrelated scene-JSON model that nothing
  constructs.
- **The done-when's grep would pass on a rewrite that still hardcodes placement.**
  `rg -n '_SVG_.*_SIZE|head_y = |arm_y = '` matches the 7 constants and 2 of the 4
  y-offset locals, and **none** of the ~40 numeric literals inside the rig builder.
  Passing it is necessary, not sufficient. Use the byte-identity mutation battery in
  §7 as the real gate.

---

## 2. The stretch — and the number to assert on

`runtime.js:164-165` sets `sprite.width` and `sprite.height` independently from the
scene JSON. PixiJS v7 turns each into an independent axis scale; there is no fit
policy anywhere in the stack, so **the box's aspect ratio always wins and the art's
aspect ratio is never consulted.** Confirmed by construction: a 300×100 texture in a
200×200 box renders as exactly 200×200 of ink.

The brief says "stretches **every** part". On the *promoted* rig — real illustrator
art — it is 7 of 8:

| part | raster | box | sx | sy | distortion |
|---|---|---|---|---|---|
| head | 1024² | 96×96 | .09375 | .09375 | **1.000 — uniform** |
| torso | 1024² | 110×130 | .10742 | .12695 | 1.182× |
| arm_l / arm_r | 1024² | 28×110 | .02734 | .10742 | **3.929×** |
| leg_l / leg_r | 1024² | 38×120 | .03711 | .11719 | 3.158× |
| brow_l / brow_r | 1024² | 24×8 | .02344 | .00781 | 3.000× |

The head is undistorted **by coincidence** — a square raster into a square box — not
because anything preserved its aspect.

**Assert on `sprite.scale.x === sprite.scale.y`, never on an ink bounding box.**
This is a methodology finding and it contradicts the done-when's prescribed test
("asserted by bounding-box measurement on an extracted frame"): the same `arm_l`
render measures 2, 4, 6 or 8 px wide depending only on the anti-aliasing threshold
chosen. The scale ratio is exact, threshold-free, and reads straight off the sprite.
An earlier draft of this document quoted "6 px of ink in a 28 px box" — that counted
the *padded viewBox*, not the drawn rect (44 of 60 units is ink, 16 is padding).

Two further facts:

- The stretch **survives a texture swap** — `_onTextureUpdate` re-applies the cached
  box to every new texture, so every viseme inherits it.
- `AssetJSON` already declares `width` / `height` and they are **inert**. That is the
  natural, additive home for intrinsic dimensions; no schema break needed.

---

## 3. Part extraction — the brief is half right, and the wrong half changes the fix

`extract_part` writes a **cropped** viewBox while copying the **root's** width/height
(`svg_utils.py:273-277`), so `preserveAspectRatio="xMidYMid meet"` letterboxes.
Committed evidence: `maya-promoted/parts/arm_l.svg` carries
`viewBox="342.00 442.00 60.00 276.00"` with `width="1024" height="1024"`.

Exactly **8 of the 21** committed part files are affected — precisely the ones
`extract_part` produces. The other 13 (eyes 64×32, mouths 256×128) are *synthesized*
by the factory and are correctly dimensioned. **The defect is on the illustrator path
only**, which is the wave's thesis restated as a file listing.

### REFUTED: "destroying the only record of where it sat relative to its siblings"

The parent-space origin **survives verbatim** as the viewBox's first two numbers.
`arm_l`'s `342.00 442.00` is the crop origin; 342 = 350 − 8 padding, exactly. Nothing
needs to be recorded and no schema field is needed to carry it — the information is
already on disk, unread.

### REFUTED: "the part renders small **and off-pivot**"

`xMidYMid` centres. The ink centroid coincides with the sprite-box centre to ±0.0 px
in every part and every variant measured. Small: yes. Off-pivot: no.

### CONFIRMED, and it is the thing actually destroyed: the joint pivots

`extract_pivots` returns `{name: (cx, cy)}` — the artist's joint coordinates in the
art's own coordinate system, which is exactly what `Bone.x`/`Bone.y` wants.
`promote.py:108` computes them correctly. `promote.py:161` stores
`list(pivots.keys())` — **the names, discarding every coordinate** — and `Bone.x`/`.y`
keep hardcoded defaults. The illustrator's skeleton is read accurately and thrown
away one line later.

### The decision the brief asks for

**Cropped viewBox + corrected width/height.** No recorded offset is required (see
above). Measured consequences of correcting width/height alone, before any compiler
change: `arm_l` goes from ~4 px of ink in its 28 px box to 22 px, `leg_l` from ~6 to
30 — and a character's texture RAM falls from **33.16 MiB to 1.87 MiB — 17.7x** — at no
cost in file bytes or asset-load time.

### An adjacent defect found while measuring

`_subtree_bbox` does **not** cover what an illustrator's SVG actually contains, and
its docstring's claim that the result "is safe (always contains the visible art)" is
false. It scrapes numeric pairs from `d` attributes and handles a fixed element list;
transforms, `<polygon>`, `<polyline>` and stroke width are not accounted for.

---

## 4. Missing art — the brief's claim is refuted and the truth is worse

There is no single "missing texture" behaviour. There are six, reproduced end-to-end
through `CutoutRenderer.render` with a real descriptor in a real project:

| input | what actually happens |
|---|---|
| valid part | renders |
| **file absent** | **render crashes** — raw, unwrapped `playwright._impl._errors.Error` carrying a minified PixiJS `TypeError: Cannot read properties of undefined (reading 'x')` from `runtime.js:163`. No shot id, no part name. |
| **`<svg/>`, malformed XML, zero-dimension** | **render hangs forever** — `Assets.load` never settles; still `pending` after **120 s**. There is no timeout anywhere on this path. |
| **valid SVG, no drawable geometry** | **silently invisible** — no warning, no error, *even under `strict_assets=True`* |
| zero-byte file, `src=""`, or no `src` key | the only three inputs that reach `PIXI.Texture.WHITE` |
| missing non-rest mouth shape | passes load, then **crashes mid-capture** on the first frame needing it (frame 8 at fps=30 with a viseme at t=0.25) |

Consequences that change the build:

- **`CutoutAssetWarning`'s message is factually wrong.** It promises a white rectangle
  and delivers a crash. **Five sites** restate the now-known-false claim and must be
  rewritten in the same pass — including a *shipped* dev skill,
  `.claude/skills/an-dev-runtime-assets/SKILL.md`.
- **`strict_assets` is not the fix.** It is entity-level only, with one consumer
  (`compile.py:289`) reading `AssetResolutionJSON.fallback`. Measured: it compiles a
  descriptor whose `head.svg` has been deleted with **zero diagnostics** and
  `fallback: false`. The work is to extend its reach *inside* the descriptor, not to
  flip its default.
- **Do not write a golden test against "the white rectangle."** It would not see the
  fallback on a white background, and would not see it at all for the three modes
  that crash, hang or vanish.
- **`Texture.WHITE` is dead code on the file-absent path** — the falsy branch is never
  taken for a 404, because the crash happens first.
- Making a missing part a hard error breaks **no** corpus scene and **no** example.
  The instrument Wave 2 built is safe to gate Wave 4 with.

### Struck from scope

The brief asks that "the historical invisible-head example part becomes a committed
regression fixture." **No such part exists.** `examples/*/assets/` is gitignored
(`.gitignore:129`) and nothing matching is in git history. The fixture must be
*authored*, not recovered — which is fine, and cheaper, but it is not the same task.

---

## 5. The schema change, and the migration that has nowhere to go

`viseme_map` has exactly one production reader (`compile.py:752`). Renaming it is
mechanically trivial. The problem is underneath.

**REFUTED: "with the registered migration".** The character descriptor has a version
*field* (`CHARACTER_SCHEMA_VERSION = "0.1.0"`) and **no migration machinery at all**.
`an/ir/migrate.py` is the scene-IR registry: `migrate()` reads only `doc["version"]`
(`:63`) — it never inspects the `"kind"` key its own doctest passes in — while the
descriptor spells its version `schema_version`. Both schemas sit at `"0.1.0"`.

So the mechanism is **namespace conflation, not key collision**: a character migration
registered as `("0.1.0", "0.2.0")` is a well-formed entry in a registry that cannot
tell the two document kinds apart, and `migrate()` would apply it to a *scene*.

The registry needs a `kind` dimension — or the descriptor needs its own — **before**
the rename can land. That is a prerequisite the brief does not budget for.

Two more facts that shape it: every descriptor model sets `extra="allow"`, so an
un-migrated `viseme_map` key **survives validation silently** rather than failing
loudly; and the migration's committed surface is exactly **two files**, both corpus
fixtures, so the migration is real but small.

### Open: does `asset_sets` duplicate `Skin.slots`?

Wave 5 needs "a swap set names attachments within a slot". `Skin.slots:
dict[str, dict[str, Attachment]]` already models exactly that, and a multi-slot swap
(a turnaround) is what a *skin* is in both reference systems. The evidence favours
`asset_sets` **indexing into** the skin rather than replacing it — a channel key maps
to an attachment name, the attachment lives in the skin — but this should be settled
before the migration is written, because it decides whether `asset_sets` is a new
field or a view.

---

## 6. What DragonBones and Spine actually say

Read for the data model only; nothing adopted, nothing depended on. DragonBones is
MIT (verified at the LICENSE file). Spine's runtimes are **not** readable the same way
— format documentation only.

Both give the same answer to the wave's central question, and it is not the one the
repo implements:

> **A part's position lives on the ATTACHMENT, expressed relative to the slot's BONE.
> The slot itself carries no transform at all.**

DragonBones puts it in `display.transform` `{x, y, skX, skY, scX, scY}`; Spine in the
region attachment's `{x, y, rotation, scaleX, scaleY}`. DragonBones' own DB→Spine
converter maps these 1:1, which is as strong a confirmation as the question admits.

**There is a fourth home, and it is the one `an` threw away**: the image's own trim
record — Spine's atlas `offsetLeft/offsetBottom/originalWidth/originalHeight`,
DragonBones' `frameX/frameY/frameWidth/frameHeight`. Both runtimes explicitly add it
back at draw time, so that whitespace trimmed at pack time does not move the art.
That is precisely the role `extract_part`'s crop origin should play.

Also load-bearing:

- **Draw order is per-slot in both**, plus a permutation timeline for animated
  reordering. The repo's draw order is the literal order of a hand-built Python list.
- **Spine carries width/height per (skin, slot, placeholder)**, so two art packages can
  resolve the same slot to art of different aspect ratios. That is the mechanism the
  done-when's "two visibly different art packages" requires.
- DragonBones keys swaps by positional **index**, Spine by **name**. For a data model
  whose SSOT is JSON edited by agents and humans, name-keyed is the only sane choice —
  index-keyed silently reassigns art when a set is reordered.

What does **not** transfer: the repo's rigs are deliberately flat (arms are siblings
of the torso, not children). Bone *hierarchy* is not being adopted; bone *placement*
is.

---

## 7. Blast radius, and why the brief's PR split cannot be honest

### Goldens — a clean 3/3 split

| path | scenes | PNGs |
|---|---|---|
| **procedural** — must not move | `single_character`, `aa_probe`, `multi_shot` | 6 |
| **descriptor** — will need re-blessing | `promote_demo`, `graded_field`, `saturated_outline` | 6 |

`promote_demo` is **mixed** — 11 svg_sprite nodes plus 2 procedural `rect`
environment nodes — so it is the one scene where a procedural regression could hide
behind an expected descriptor change. Rank it first when reviewing the bless.

### The two bench fixtures are authored *against* the bug

`graded_field/scene.md` sets `torso: scale_x 4.0, scale_y 2.5` — chosen so the fixed
`_SVG_TORSO_SIZE` box covers 440×325 on a 320×240 frame. `saturated_outline` sets no
scale at all; its framing is 100% the compiler's constants. Both exist for measured
reasons (a 0.2795 gradient fraction; the corpus's highest edge-mask fraction, 0.0566).
**Under a descriptor-driven layout these scenes must be re-authored to preserve the
property they were built to measure**, or the instrument loses two of its six scenes.

Nothing in the repo defines a `view_box` → scene-pixel mapping. That mapping is a
design decision Wave 4 must make, not a detail it can inherit.

### Tests

**No test anywhere references the module constants the wave deletes.** The exposure is
narrower than feared: 6 test functions assert the old behaviour and must change
(alias spellings, `viseme_assets` keys, `viseme_map` casing, two module doctests);
7 assert general invariants and should survive; 3 are undetermined pending two design
decisions (whether the new compiler tolerates a slot-less descriptor, and where "a
missing part raises" lands).

### The instrument does not exist yet

**Nothing in the repo returns a part bounding box from a rendered frame.**
`bench/masks.py` returns boolean masks with no extents; `verify/media.py` has no bbox;
`bench/png.py` computes no geometry; `characters/silhouette.py` returns an IoU scalar.
It must be built — and per §2 it should measure the **scale ratio**, not ink extents.

### The PR split, inverted

The brief prescribes "the compiler change, then test/example regeneration". That order
**cannot be honest here**: PR-1 would merge with three descriptor goldens stale and no
CI lane able to detect it — lane A never renders, and the browser lane is label-gated
— and on this repo **a merge to main is a PyPI release**. The false intermediate would
ship.

Inverted:

- **PR-1 — contract + instrument, zero pixel change.** Build the scale-ratio
  measurement and land it as a test that **records today's violation as numbers**
  rather than asserting a tolerance. Extend `validate_character` to open each part
  (geometry present, viewBox sanity, prohibited constructs, joint-name collision,
  populated `AssetSource`) emitting `an.verify._base.Finding`. Add `an character
  contract`, derived from the schema. Fix the five false docstrings. Every golden
  unchanged; CI green for a reason; the recorded numbers become the before-half of the
  wave's evidence.
- **PR-2 — the change, its migration, and its bless in one commit.** Compiler walks
  `bones`/`slots`/`skins`/`view_box`; runtime fits uniformly; `extract_part` emits
  correct dimensions; missing part raises; `viseme_map` → `asset_sets` behind a
  `kind`-aware migration registry; the six tests updated; corpus `scene.md`
  re-authored; `an bench --bless` on the three descriptor scenes, with the
  `--compare` table in the PR body showing the three procedural rows byte-identical
  and the PR-1 ratios moving to 1.000.

PR-2 is large. It is also the smallest unit whose green means anything, and **a
two-PR split whose first half ships a lie is worse than one honest PR.**

---

## 8. Defects found that are not Wave 4's job

Filed separately so the wave does not silently absorb them:

1. **The `<svg/>` hang has no timeout.** Any degenerate part SVG wedges a render
   indefinitely. A deadline on the `anLoadScene` evaluate is the obvious guard.
2. **Three unwrapped `page.evaluate` calls on the render path** (`render.py:268`,
   `:279`, `:283`) surface as raw Playwright errors, violating the repo's own rule
   that subprocess failures are wrapped as typed errors at the facade boundary.
3. **`validate_character` returns a bespoke `ValidationReport`**, not `Finding`s, so
   the orchestrator's error routing does not apply to any character problem.
4. **`_subtree_bbox` under-covers real SVG** (§3).
5. **`CharacterDescriptor.animations` is dead on the render path** — the idle rig is
   seeded and never read.

---

## Provenance

Seven research threads plus seven adversarial refutations, 14 agents, ~3.4M tokens,
against the tree at `2db25ce`. Nine claims were refuted or downgraded by the
refutation pass; the corrections are folded in above rather than listed, except where
the correction is itself the finding (§2's threshold sensitivity, §3's surviving crop
origin, §4's six outcomes). No file in the repo was modified during the research.
