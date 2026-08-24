---
name: an-dev-rig-contract
description: How a character descriptor becomes a scene tree in the `an` repo — which fields are load-bearing, which are declared and dead, and the invariants that broke when they were ignored. Use when touching `_build_svg_character_subtree`, `an/characters/schema.py`, `extract_part`/`promote`, the descriptor→scene mapping, part sizing or placement, `viseme_map`/`asset_sets`, the character migration, or anything that decides what an illustrator's art does on screen. Triggers on "the art doesn't change anything", "part is stretched", "aspect ratio", "bones", "slots", "skins", "attachment", "view_box", "descriptor", "rig", "missing part", "white rectangle", "an character validate".
---

# The rig contract

**Authority: `misc/docs/wave4_research.md` (measured 2026-08-23).** When this file and
that one disagree, the research doc wins; when the code and both disagree, the code
wins and you fix the other two. Wave 4 is #9's fourth wave; its sub-issues are
#73–#78, plus #79 for the render-path robustness holes found alongside.

## The one-sentence state of the world

`an` has a complete, canonical, Spine-shaped rig model in `an/characters/schema.py`
that **nothing writes and nothing reads**, and a compiler that builds every character
from seven module constants and about forty inline literals instead.

## The measurement that proves it — reuse this, don't re-derive it

Gut every rig field and recompile. The emitted `CutoutSceneJSON` is **byte-identical**:

| mutation | scene |
|---|---|
| `view_box` 1024 → 64 | SAME |
| `bones` → `[]`, or the head bone moved +5000 px | SAME |
| every `draw_order` inverted | SAME |
| `slots` → `[]` | SAME |
| every attachment `path` → `parts/DOES_NOT_EXIST.svg` | SAME |
| every `anchor` → `(0.0, 1.0)` | SAME |
| every attachment `width`/`height` → 999/3 | SAME |
| `skins` → `{}` | SAME |
| **all of the above at once** | SAME |
| `viseme_map["A"]` changed *(control)* | **DIFFERENT** |
| `metadata.art_provenance = "dicebear"` *(control)* | **DIFFERENT** |

*(Historical, as measured in Wave 4. Since an#87 the compiler reads the
declared `face_overlay` field, not the provenance string; `viseme_map` became
`asset_sets` in 0.2.0. The measurement stands as the record of what the
compiler ignored before Wave 4.)*

The two controls are what make the null result mean something. **After the rewrite,
every row above must read DIFFERENT.** That inversion is the wave's real acceptance
test — it is cheap (no browser, no render) and it cannot be satisfied by accident.

**Do not use the epic's grep as the gate.** `rg -n '_SVG_.*_SIZE|head_y = |arm_y = '`
matches the 7 constants and 2 of the 4 y-offset locals, and none of the ~40 numeric
literals inside the rig builder. A rewrite that still hardcodes placement passes it.

## Which fields are load-bearing

Four descriptor keys are read, at five sites; **three have any effect**.

| key | site | effect |
|---|---|---|
| `kind` | `compile.py:554` | selects the descriptor branch |
| `name` | `compile.py:718` | **none** — a local that is never loaded |
| `viseme_map` | `compile.py:752` | mouth alias table |
| `metadata.art_provenance` | `:765`, `:1098` | face-baked overlay suppression — now the declared `face_overlay` field (an#87) |

Dead everywhere — **no producer and no consumer**: `view_box`, `bones`, `slots`
(and `draw_order`), `skins` (and every `Attachment.path`/`.anchor`/`.width`/`.height`).
`animations` (the idle rig, seeded by `model_post_init`) was on this list until
an#7: an authored `play` now resolves it through `an.characters.play` (shared by
`an validate` and `compile.py::_resolve_play`), so it is read — but only by a
`play`; nothing plays it automatically.

`runtime.js` contains zero occurrences of `rigs`, `skins` or `attachment`.
`CutoutSceneJSON.rigs: dict[str, RigJSON]` was the same defect one layer down —
a wire model nothing populated. **Deleted in an#86** along with the rest of the
sketched Spine vocabulary (`SkinJSON`, `SlotJSON`/`NodeJSON.slots`,
`current_attachment`); the wire's real swap carrier is `VisualJSON`'s per-node
asset maps, and Wave 5 (an#87) generalises those.

### The field path in the epic is wrong

`skins[].attachments[].anchor` does not exist. `Skin` has `name` and `slots`; the real
path is `skins[<skin>].slots[<slot>][<attachment>].anchor`. (`attachments` belonged to
`SkinJSON`, an unrelated scene-JSON model nothing ever constructed — deleted in an#86.)

## The invariants

### 1. Aspect ratio is intrinsic to the art. The compiler may never override it.

A part is **placed and uniformly scaled**, never stretched to fit a box.

`runtime.js:164-165` sets `sprite.width` and `sprite.height` independently; PixiJS v7
turns each into an independent axis scale. There is no fit policy in the stack, so the
box always wins. Measured on the committed promoted rig, 7 of 8 extracted parts are
non-uniformly scaled — `arm_l` by **3.929×**. The head is uniform *by coincidence*
(square raster into a square box), which is exactly the kind of accident that makes a
spot-check look fine.

**Assert on `sprite.scale.x === sprite.scale.y`. Never on an ink bounding box.**
The same `arm_l` render measures 2, 4, 6 or 8 px wide depending only on the
anti-aliasing threshold you pick. The scale ratio is exact and threshold-free. The
epic's prescribed "bounding-box measurement on an extracted frame" is superseded by
this line.

The stretch **survives a texture swap** — `_onTextureUpdate` re-applies the cached box
to every new texture — so one wrong box silently distorts every viseme in the set.

### 2. A part's position lives on the attachment, relative to the bone.

Read from DragonBones (MIT, read only — depend on none of it) and Spine's format docs.
Both agree, and both disagree with what `an` does today: the **slot carries no
transform**; the attachment carries `{x, y, rotation, scale}` relative to the slot's
bone. DragonBones' own DB→Spine converter maps these 1:1.

There is a **fourth** home, and it is the one `an` throws away: the image's own trim
record (Spine `offsetLeft/offsetBottom/originalWidth/originalHeight`; DragonBones
`frameX/frameY/frameWidth/frameHeight`), which both runtimes add back at draw time so
trimming does not move the art. That is the role `extract_part`'s crop origin plays.

Bone *hierarchy* is not being adopted — `an`'s rigs are deliberately flat (arms are
siblings of the torso). Bone *placement* is.

### 3. The crop origin already survives. Don't add a field for it.

`extract_part` writes a cropped viewBox and copies the **root's** width/height
(`svg_utils.py:273-277`), so `preserveAspectRatio="xMidYMid meet"` letterboxes. But the
parent-space origin is right there in the viewBox's first two numbers —
`arm_l.svg` is `viewBox="342.00 442.00 60.00 276.00"`, and 342 = 350 − 8 padding
exactly. The information is on disk and unread.

Two corollaries people get wrong:
- Parts do **not** render off-pivot. `xMidYMid` centres; ink centroid matches box
  centre to ±0.0 px.
- Only **8 of 21** committed parts are affected — the ones `extract_part` produces.
  Eyes and mouths are factory-synthesized and correctly dimensioned. The defect is on
  the **illustrator path only**.

Fixing width/height alone: `arm_l` 4 px → 22 px of ink in its 28 px box, and character
texture RAM **33.16 MiB → 1.87 MiB (17.7×)**.

### 4. The artist's joints are computed and then discarded.

`extract_pivots` returns `{name: (cx, cy)}` — the joint coordinates in the art's own
coordinate system, exactly what `Bone.x`/`Bone.y` wants. `promote.py:108` computes them
correctly. `promote.py:161` writes `list(pivots.keys())` into metadata, **dropping every
coordinate**. This is the single clearest statement of the wave's defect.

### 5. A missing or geometry-less part is a typed error — never a white rectangle.

**The "white rectangle" story is wrong**, and repeating it is worse than saying
nothing, because it describes a benign failure where the real one is a crash. Six
outcomes, measured end to end:

| input | what actually happens |
|---|---|
| file **absent** | **crash** — unwrapped `playwright._impl._errors.Error`, minified PixiJS `TypeError: Cannot read properties of undefined` |
| `<svg/>`, malformed XML, zero-dimension | **hangs forever** — `Assets.load` never settles, still pending after 120 s, no timeout anywhere (#79) |
| valid SVG, no drawable geometry | **silently invisible**, even under `strict_assets=True` |
| zero-byte file, `src=""`, no `src` key | the only three inputs reaching `PIXI.Texture.WHITE` |
| missing non-rest mouth | crashes **mid-capture**, at the first frame needing it |

So:
- **`strict_assets` is not the fix.** Entity-level only, one consumer
  (`compile.py:289`). It compiles a descriptor with a deleted `head.svg` with zero
  diagnostics and `fallback: false`.
- **Never write a golden test against the white rectangle.** Invisible on a white
  background, and absent for the three modes that crash, hang or vanish.
- Any load-time check must cover the **viseme alias set** too.

### 6. Reserved joint names are a namespace, not a convention.

`_find_by_id` prefers the `<g>` over a same-id `<circle>` precisely because a pivot id
can collide with a part id. That is a workaround for a missing namespace, not a fix.
The structural answer is a reserved joint-name namespace that `validate_character`
enforces.

## The migration has nowhere to go — check this before you write it

The descriptor has a version *field* (`CHARACTER_SCHEMA_VERSION = "0.1.0"`) and **no
migration machinery**. `an/ir/migrate.py` is the scene-IR registry: `migrate()` reads
only `doc["version"]` and never inspects `"kind"`, while the descriptor spells its
version `schema_version`. Both schemas sit at `"0.1.0"`.

The failure was **namespace conflation, not key collision**: a character migration
registered `("0.1.0","0.2.0")` was a valid entry in a registry that could not tell the
kinds apart, and `migrate()` would apply it to a scene.

**Fixed (an#77).** `MIGRATIONS` is keyed `(kind, from, to)`; a `DocumentKind` declares
where its version lives (`version` vs `schema_version`) and what this build writes; and
kinds self-register on import of the package owning the schema, the way renderers do —
with `an/ir/__init__.py` importing the character schema so a bare `import an` is enough.
So the prerequisite is done and `viseme_map` → `asset_sets` can be written directly.

And note every descriptor model sets `extra="allow"`, so a stale `viseme_map` key
**survives validation silently**. That is why the migration must be explicit, not a
reason it can be skipped.

**Open, settle it before writing the migration:** does `asset_sets` duplicate
`Skin.slots`? `Skin.slots: dict[str, dict[str, Attachment]]` already models
"attachments within a slot", and a multi-slot swap is what a *skin* is in both
reference systems. Evidence favours `asset_sets` **indexing into** the skin.

## Blast radius, before you start

- **Goldens split 3/3.** Procedural — `single_character`, `aa_probe`, `multi_shot` —
  must not move. Descriptor — `promote_demo`, `graded_field`, `saturated_outline` —
  will need re-blessing. **`promote_demo` is mixed** (11 svg_sprite + 2 procedural rect
  env nodes), so it is where a procedural regression could hide behind an expected
  change. Review it first.
- **Two bench fixtures are authored against the bug.** `graded_field`'s
  `scale_x 4.0 / scale_y 2.5` was chosen so the fixed `_SVG_TORSO_SIZE` box covers
  440×325; `saturated_outline`'s framing is 100% the constants. Both exist for
  measured reasons. Re-author them to preserve the property each was built to measure,
  or the instrument loses two of six scenes.
- **No test references the deleted constants.** 6 test functions assert old behaviour
  and must change; 7 assert general invariants and should survive.
- **Nothing measures a part bbox from a frame.** It must be built — as a scale ratio,
  per invariant 1.

## The PR split, and why it is inverted from the epic

The epic says "the compiler change, then test/example regeneration". **That order
cannot be honest here.** PR-1 would merge with three descriptor goldens stale and no CI
lane able to detect it (lane A never renders; the browser lane is label-gated) — and on
this repo a merge to main is a PyPI release, so the false intermediate ships.

- **PR-1 — contract + instrument, zero pixel change.** Build the scale-ratio
  measurement and land it as a test that **records today's violation as numbers**
  rather than asserting a tolerance. Extend `validate_character`. Add
  `an character contract`. Fix the false docstrings. Every golden unchanged.
- **PR-2 — the change, its migration, and its bless in one commit**, with the
  `--compare` table in the PR body showing the three procedural rows byte-identical and
  the PR-1 ratios moving to 1.000.

A two-PR split whose first half ships a lie is worse than one honest PR.

## When you finish

- Append to `misc/CHANGELOG.md`.
- Update `misc/docs/architecture_as_built.md` if the shape moved.
- `an-art-package` is **deliberately not written until the compiler reads the
  contract** — a contract the compiler ignores is worse than none, because it gets a
  human illustrator paid for work that cannot land. It ships with PR-2, not before.
