# Wave 7 / T4 — props: non-character drawables that reach the screen

Measured 2026-08-25 against `main` at `9aa35f8` (an 0.1.58, clean tree), repo
`/Users/thorwhalen/Dropbox/py/proj/t/an`. Every code claim carries `file:line`; every
external claim carries a fetched URL and a verbatim quote. Anything I could not fetch or
could not run is marked **UNVERIFIED**. Where this document and the code disagree, the code
wins and this document gets fixed.

**Scope split.** T2 owns the environment document and the plane; T1 owns the camera; T5 owns
the instrument (contract hash, goldens, levers). **T4 owns the drawable that is neither a
character nor a backdrop** — the thing a character holds, the sign on the wall, the chest
that opens. Two rulings from the sibling briefs are taken as given and honoured throughout:

- **T5 §3.2, props row:** props are free at the bench — *"`compile.py:717-724` raises on a
  `prop` entity, so no corpus scene can contain one; a new code path is reached only by
  scenes that declare props. Zero documents change. Must land together with
  `validate.py:113`."* Everything in §4 below is designed to keep that true, and §4.2 lists
  the four ways this wave could break it.
- **T2 §3.1–§3.2:** the depth vocabulary is **one scalar named `depth`** (0.0 = frozen,
  1.0 = the character plane, >1.0 = foreground), exposed to the camera as
  `parallax_factor -> (x, y)`; **list order is draw order, there is no `z` field**; and
  relative stacking against a named target is `characters_after`, Rive's rule. A prop must
  name its plane in *that* vocabulary. §3.7 does so and invents nothing.

**The wave in one paragraph.** `AssetRef.kind` has accepted `"prop"` since the IR was
written; the compiler raises on it and `an validate` calls it undrawable. Meanwhile the
character path already contains, in generic form, everything a prop needs: a bone/slot/skin
document, per-slot SVG sprites sized from the art's own raster, a per-slot projection of
declared swap sets, missing-art recording that escalates under `strict_assets`, and a
renderer-free resolver shared with `an validate`. The block is three hardcoded strings and
one seeded default: `_svg_asset_src`'s `"characters/"` prefix, `_part_probe`'s copy of it,
`ASSET_SRC_PREFIX_TO_STORE`'s missing `props/` row, and `CharacterDescriptor.model_post_init`,
which materialises a seven-bone humanoid the moment you leave `bones` empty. §3 recommends a
`PropDescriptor` that is a thin profile of `CharacterDescriptor` over a shared rig builder,
and refutes both alternatives on those facts.

---

## 1. What exists today, code-verified

### 1.1 `AssetRef(kind="prop")` at the three layers

**The IR accepts it.** `AssetRef.kind` is a closed
`Literal["character", "environment", "voice", "style", "prop"]` (`an/ir/schema.py:105`). The
rest of the model is four fields and nothing else:

```python
kind: Literal["character", "environment", "voice", "style", "prop"]   # schema.py:105
id: str                                                              # :106
store: str  # which store in the project mall                        # :107
ref: str    # key inside that store                                  # :108
overrides: dict[str, Any] | None = None                              # :109
```

**There is no transform, no size, no depth, no anchor, and no parent** on an `AssetRef`.
`overrides` is documented at `:98-99` as "lets a single shot tweak presentation without
forking the asset" and is **read by nothing**: `rg '\.overrides' an/ tests/` returns zero
hits outside the definition. That is a dead per-shot channel already in the tree, and §3.6
declines to make it the live one.

`scene.md` parses a ```` ```yaml entities ```` block straight into `AssetRef(**item)` with no
whitelist (`an/ir/sync.py:230-242`), so `kind: prop` round-trips today. The writer dumps
`model_dump(exclude_none=True, exclude_defaults=False)` (`sync.py:417-421`).

**`an validate` refuses it.** `_DRAWABLE_ENTITY_KINDS: frozenset[str] = frozenset({"character",
"environment"})` (`an/ir/validate.py:113`), `_CONFIGURING_ENTITY_KINDS = {"voice", "style"}`
(`:114`), and the per-entity check at `validate.py:351-360`:

```python
if (entity.kind not in _DRAWABLE_ENTITY_KINDS
        and entity.kind not in _CONFIGURING_ENTITY_KINDS):
    report.add("error", f"{path}/entities/{j}",
               f"entity kind {entity.kind!r} is declared by the IR but not drawn "
               "by the cutout renderer. Rendering this shot raises.")
```

Severity `error`, because validate's verdict must agree with the pipeline's.

**The compiler raises.** `_build_scene_root` (`compile.py:672`) walks `shot.entities` twice —
environments first (`:701-707`, comment at `:700`: "Process environments first so they sit
BEHIND characters in z-order"), then characters (`:708-716`) — and the `prop` branch sits
inside the *second* loop (`compile.py:717-724`):

```python
elif entity.kind == "prop":
    raise CutoutCompileError(
        f"shot {shot.id!r}: entity {entity.id!r} is a prop, which the "
        "cutout renderer does not draw yet. Props — images, nine-slice "
        "panels, things a character holds — are planned; see "
        "https://github.com/thorwhalen/an/issues/9. Until then, remove the entity rather than leaving it "
        "in the scene, where it would be silently absent from the render."
    )
```

`voice` and `style` fall through with a comment saying they are legitimately not drawable
(`:725-726`), and the function returns `NodeJSON(name="root", children=children)` (`:727`).

**Four tests pin the refusal and all four invert in Wave 7:**

| test | file:line | what it asserts |
|---|---|---|
| `test_a_prop_entity_raises_naming_the_shot_and_a_reachable_issue` | `tests/test_loud_discards.py:261-278` | the error names the shot and a reachable issue URL |
| `test_no_skill_advertises_a_capability_that_now_raises` | `tests/test_loud_discards.py:547-568` | no `.claude/skills/*/SKILL.md` line enumerating `kind` ∈ … may offer `prop` |
| `test_validate_reports_every_scene_the_pipeline_refuses[prop]` | `tests/test_loud_discards.py:604-612` (`_UNRENDERABLE_SHOTS["prop"]` at `:608-609`) | validate errors on exactly the scenes the pipeline refuses |
| `test_an_unstageable_texture_warns_instead_of_vanishing[props/banner.png]` | `tests/test_asset_staging.py:100-112` | a `props/`-prefixed texture warns `"prefix is not one of"` |

The second one carries its own precedent for what to do: *"it once said the same of `play`,
which has since been built — an#7 — so that half of the guard is gone"*
(`test_loud_discards.py:551-554`).

**`iterate.py` tells the model not to emit one.** The system prompt at
`an/iterate.py:138-140`:

```
      - entities: list of {kind, id, store, ref, ...}
        "kind" MUST be one of: character, environment, voice, style.
        "prop" is declared by the IR but NOT rendered — it raises. Do not emit one.
```

There is **no test on that enumeration**. `test_the_iterate_prompt_enumerates_the_legal_properties`
(`tests/test_loud_discards.py:523-542`) guards the *property* vocabulary only — it asserts
`scale_x`, `alpha`, `pivot_y`, `asset_sets`, `refused at compile`, `Never invent`. The
entity-kind line is unguarded in both directions, so §4.3 adds a guard rather than assuming
one exists.

**Two docs also state the refusal**: `.claude/skills/an/SKILL.md:53` ("**`kind: prop` is
declared by the IR but NOT rendered** … Do not put one in a scene.") and
`misc/docs/architecture_as_built.md:391` (`prop` listed among "the four IR-level refusals").

### 1.2 What a character descriptor already gives a prop

This is the load-bearing survey of our own code, and it is why §3 recommends what it does.
`CharacterDescriptor` (`an/characters/schema.py:259-384`) is a schema-versioned document
(`CHARACTER_SCHEMA_VERSION = "0.3.0"`, `:55`) with its own `DocumentKind` registered from the
package that owns it (`:57-68`), `extra="allow"` (`:127-130`).

| capability | where | is it character-specific? |
|---|---|---|
| **Bones** — `name/parent/x/y/rotation_deg/scale_x/scale_y/pivot` | `schema.py:133-150`; absolute positions summed along the parent chain by `_bone_positions` (`compile.py:1111-1131`) | **No.** A hinge, a lid, a swinging sign are all one-parent chains. |
| **Slots** — `name/bone/draw_order/attachment`, "a slot's name IS its scene-graph node name" | `schema.py:152-166`; `_default_slots` docstring `:589-597` | **No.** |
| **Attachments** — `path/anchor/x/y/width/height`, offsets in view_box units | `schema.py:168-194` | **No.** The offset field's own docstring cites Spine and DragonBones as the reference model (`:180-188`). |
| **Skins** — `{slot: {attachment_name: Attachment}}` | `schema.py:196-209` | **No.** This is exactly a prop's variant set (a closed vs open chest). |
| **Swap sets** — `asset_sets: {channel: {KEY: attachment_name}}`, the indirection deliberate because "a channel key is not an attachment name" | `schema.py:306-311` | **No.** The an#87 generalisation is already complete: `.claude/skills/an-dev-swap-channels/SKILL.md:80-83` documents adding `asset_sets["props"]` as a **data change with no code change**, and the proof is a committed fixture (`tests/fixtures/characters/gale/`) that animates two invented sets with zero occurrences of their names in compiler or runtime. |
| **Anchors** — `Attachment.anchor: tuple[float, float]` in 0..1, Pixi's convention | `schema.py:171-173`, applied at `compile.py:1330-1331` → `sprite.anchor.set(ax, ay)` (`runtime.js:220-222`) | **No.** |
| **Art probe** — `(exists, rasterised size)` from an SVG header parse | `compile.py:1069-1107`; `raster_size` at `an/characters/svg_utils.py:163-190` | **Almost.** The store prefix is hardcoded `"characters/"` at `compile.py:1093`. |
| **`strict_assets` escalation** — one `AssetResolutionJSON` per slot, `resolved="missing"/fallback=True` when the slot drew nothing, `"incomplete"/fallback=False` when it drew but is short an attachment | `_record_missing_parts`, `compile.py:1133-1187`; the single warn-vs-raise decision at `_raise_or_warn_on_asset_fallbacks`, `compile.py:510-548` | **No**, except `kind="part"` is a literal at `:1141`. The bench pins `strict_assets=True` (`an/bench/corpus.py:40-44`). |
| **Provenance / rights** — `source: AssetSource \| None`, field names matched to `illustration.ImageResult` | `schema.py:319-330`; `AssetSource` at `an/ir/assets.py:76-116` | **No** — and `collect_credits` says out loud that props are missing: *"Only the characters store carries provenance today. Environments, styles and props will as they gain real art"* (`an/credits.py:110-116`). |
| **Schema migrations** — chained, keyed per document kind | `an/ir/migrate.py`; two registered character migrations at `schema.py:397`, `:469` | **No.** |
| **Renderer-free resolution** — `an.characters.play` resolves a descriptor animation for both `an validate` and the compiler, "so that … the two cannot drift" | `an/characters/play.py:1-38` | **No.** |

**Character-specific, and only these:**

1. **`face_overlay` and the face-suppression branch.** `head_has_face = not desc.face_overlay`
   (`compile.py:1261`), and the branch that drops every slot nested under the head bone's
   primary slot (`compile.py:1297-1302`). It is keyed on the **bone name** `"head"` via
   `nests_under.get("head")`, so on a rig with no head bone the comparison is
   `parent == None` with `parent` a non-None string — the branch is **inert by construction**,
   not by a kind check.
2. **Blinks.** `EYE_NODE_NAMES = frozenset({"left_eye", "right_eye"})` (`compile.py:178`);
   `_eye_paths` selects nodes whose last path segment is in that set (`compile.py:2329-2334`);
   `_blink_placements` returns `None` — "nothing blinks" — when there is no eye node
   (`compile.py:2352-2354`).
3. **Palettes.** `_palette_for(entity_id)` (`compile.py:133`) and the `part_color` table
   (`compile.py:936-943`) live entirely inside the **procedural placeholder** branch of
   `_build_character_subtree`.
4. **`REQUIRED_PARTS`** — twelve names, `head/torso/arm_l/arm_r/leg_l/leg_r/eye_*_open/
   eye_*_closed/brow_l/brow_r` (`schema.py:106-119`) — checked as blocking findings by
   `validate_character` (`an/characters/validate.py:216-222`), plus all nine
   `MOUTH_SHAPES` (`:223-230`).
5. **The silhouette test.** `an character silhouette` (`an/characters/cli.py:207-255`)
   requires `<char_dir>/<name>.svg`, renders a black silhouette and reports an IoU with the
   verdict "very similar (consider redesigning)" — a *character-distinctness* tool.
6. **The whole procedural placeholder rig** — `_PLACEHOLDER_PARTS`, hair, two brows, two
   eyes, a drawn mouth carrying `PROCEDURAL_MOUTH_KEYS` (`compile.py:930-1043`). This is the
   fallback `_build_character_subtree` takes when the ref is absent or is not a descriptor
   (`compile.py:906-928`).

**The kind gates that already exist**, all four of which skip a prop entity for free:

```
compile.py:461   if entity.kind != "character" or entity.ref not in chars_store: continue   # _swap_vocabulary
compile.py:709   if entity.kind == "character":                                             # _build_scene_root
compile.py:2051  if entity.kind != "character": continue                                    # _baked_face_speakers
compile.py:2508  if entity.kind != "character": continue                                    # _add_face_clips
```

So the face solver, the baked-face check and the blink emitter cannot touch a prop **today**,
without any new code. That is not an accident to rely on silently — §3.4 makes it a stated
invariant with a test.

`compile.py:461` is the one that cuts the other way: because `_swap_vocabulary` only loads
**character** descriptors, a prop's descriptor would be absent from `vocab.descriptors`,
`vocab.declared`, `vocab.declared_maps`, `vocab.art_exists` and `vocab.entity_scale`
(`compile.py:363-402`). Consequences, exact:

- `play` on a prop **raises** with a message about procedural rigs: `if vocab is None or
  entity_id not in vocab.descriptors: raise CutoutCompileError(... "this entity has no
  descriptor (a procedural rig has none). Use tween / set.")` (`compile.py:1809-1814`).
- A **swap** on a prop still works, through the descriptor-less fallback: `_check_swap_action`
  derives `declared_sets` from what the **built nodes** carry when
  `vocab.declared.get(entity_id) is None` (`compile.py:1904-1921`) — the same path the
  procedural drawn mouth uses. The target must be the node that carries the set, not the
  entity container; targeting the container gets the good error *"the {prop!r} set resolves
  on {capable}, not on that node"* (`compile.py:1943-1951`).
- `vocab.entity_scale.get(entity_id, 1.0)` silently yields `1.0` (`compile.py:1809`, `:2632`).

### 1.3 The placement model

**Entity root origin.** Every entity subtree is a container named after the entity:
`NodeJSON(name=entity.id, transform=TransformJSON(), children=…)` — characters at
`compile.py:1371-1375` (descriptor path) and `:964-968` (procedural path), environments at
`compile.py:814-816`. The scene root is `NodeJSON(name="root", children=children)`
(`compile.py:727`), and the runtime places it at canvas centre: `root.x = width / 2;
root.y = height / 2` (`an/data/cutout_runtime/runtime.js:648-656`). **One scene unit is one
output pixel at camera scale 1.**

**The only placement any entity gets today** is a character's x, assigned *after* the subtree
is built: `sub.transform.x = x` (`compile.py:715`), where `x` comes from
`_layout_character_positions(n_chars)` (`compile.py:836-851`, called at `:697`):

```python
def _layout_character_positions(n: int, *, spread: float = 220.0) -> list[float]:
    if n <= 0: return []
    if n == 1: return [0.0]
    step = spread / (n - 1)
    return [-spread / 2 + i * step for i in range(n)]
```

**y is never set.** An environment gets `TransformJSON()` — the identity. So a prop entity
with no placement mechanism would land dead centre.

**`entity_scale` — view_box → pixels.** `SCENE_PX_PER_VIEW_BOX: float = 345.0`
(`compile.py:1046`) is "Scene-graph pixels spanned by a descriptor's full `view_box` height…
One uniform factor `k = SCENE_PX_PER_VIEW_BOX / view_box_height` scales bone positions and
part extents alike — uniform by construction, so the compiler cannot violate the invariant
that aspect ratio is intrinsic to the art (an#74)" (`compile.py:1030-1045`). It is computed
twice from the same expression: in the rig builder (`compile.py:1247-1248`) and in
`_swap_vocabulary` for the play/expression paths (`compile.py:495`).

Sizing then flows: `extent = probe(src)[1] or (attachment.width, attachment.height) or None`
(`compile.py:1322-1326`), and `VisualJSON(kind="svg_sprite", …, fit=CONTAIN_FIT,
**({"width": extent[0]*k, "height": extent[1]*k} if extent else {}))` (`compile.py:1327-1334`).
With no extent at all the `VisualJSON` defaults `width=height=50.0` apply
(`serialize.py:115-116`) and the runtime's `contain` fit draws the art at its natural shape
inside that box (`runtime.js:188-215`, `refitToBox` at `:174-184`).

**`_rig_origin`** (`compile.py:1190-1210`) recentres a rig on the **centre of its bone extent**,
not the root bone, "so framing is independent of where an author chose to put the root, which
is a rigging decision and should not be a framing one". For a one-bone prop this is a no-op
(min == max == the single bone), which is exactly the behaviour you want.

**`_track_root_of`** is three lines and is the entity-identity rule for the whole action
system (`compile.py:2021-2023`):

```python
def _track_root_of(target: str) -> str:
    """The first segment of a target path is the track root (the entity name)."""
    return target.split("/", 1)[0] if target else ""
```

**Entity identity == first path segment.** §3.5 shows this is the single fact that makes
"attach a prop under a character" more than a re-parenting change.

### 1.4 How actions target nodes

An action's `target` is a slash path (`charlie/head/left_eye`); its `property` is either a
transform name or a swap-set name.

- **Transform properties** are the eleven the runtime's `applyProperty` static switch
  implements (`runtime.js:456-494`), pinned in Python as `RUNTIME_APPLIED_PROPERTIES`, derived
  from `TransformJSON`'s field defaults (`compile.py:248-282`; `TransformJSON` at
  `serialize.py:50-79`, whose docstring at `:65-68` says it is "the single source of truth for
  a property's rest value"). `tests/test_loud_discards.py:496-521` extracts the runtime's
  actual switch cases and asserts equality in both directions.
- **Anything else names a swap set** (an#87). `_check_swap_action` (`compile.py:1869-1977`)
  returns `True` immediately for a transform property (`:1890-1891`), otherwise: the set must
  be declared (`:1922-1928`), every value must be a declared key (`:1929-1935`), the target
  must be a built node (`:1936-1940`), and the set must resolve **on that node**
  (`:1943-1951`). Unresolved art on a *used* key becomes an `AssetResolutionJSON(kind="swap",
  resolved="dropped", fallback=True)` (`compile.py:1979-2003`) — audible always, fatal under
  `strict_assets`.
- **`set` actions compile into hold channels** per `(target, property)` that hold until the
  next action on that pair, running to the shot end with nothing following
  (`compile.py:1476-1510`).
- **A tween with no `from` starts at the GLOBAL rest value, not the node's.**
  `_rest_value_for(prop, target)` (`compile.py:307-326`) is a lookup in the module-level
  `_PROPERTY_REST_VALUES` (`compile.py:267-268`) — it never consults the node's own
  `TransformJSON`. So a tween on `charlie/x` with no `from` starts at **0.0**, even when the
  character was placed at −110 by `_layout_character_positions`. This trap exists today for
  characters and would apply identically to props; §3.6 is designed around it rather than
  pretending it away.

### 1.5 Stores: there is no props store, verified

`rg 'props' an/stores/ an/ir/ an/tools.py an/__main__.py` returns **one** hit, and it is a
docstring: `an/stores/environments.py:1` — *"Environments store — backgrounds, set pieces,
and prop bundles."* The store class itself is nine lines with no behaviour
(`environments.py:8-9`), inheriting `META_NAME = "meta.json"` from `JsonSidecarStore`
(`an/stores/_common.py:80`), unlike `CharactersStore` which overrides it to `"character.json"`
(`an/stores/characters.py:25`).

`build_project_mall` (`an/stores/__init__.py:60-100`) creates eleven mall keys and four
`assets/*` directories under `ensure=True` (`:64-84`); the module doctest at `:13-17` asserts
the key set. There is no props entry in either.

And the asset-staging prefix table has no props row (`an/adapters/cutout/render.py:561-565`),
though its comment already anticipates one (`:558-560`): *"the other two are here because
environments, styles and props all route through this same staging step as they land, and the
previous hardcoded `characters/` test silently dropped everything else."* Two tests hold it:
`tests/test_asset_staging.py:96-97` asserts every value in the table names a store the mall
has, and `:100-112` asserts `props/banner.png` warns *"prefix is not one of"*.

---

## 2. Survey — fetched and quoted, 2026-08-25

Everything below was fetched today. Where the fetch returned a paraphrase rather than the
page's own sentences, that is marked. Claims I could not fetch are marked **UNVERIFIED** and
nothing rests on them.

### 2.1 Spine — a prop is a one-bone skeleton with attachments; skins are the variants

<https://en.esotericsoftware.com/spine-bones>

> "Bones are used for most animation in Spine, even for skeletons that are objects rather
> then characters."

> "A skeleton has a hierarchy of bones and there is always a single root bone. The root bone
> may have child bones under it, which themselves may have child bones, etc."

> "A bone's transform affects its child bones. For example, translating an arm bone will also
> translate the hand bone."

> "Attachments are attached to bones so when the bones are transformed, the attachments are
> also transformed. Some attachments are visual, having images, while others are conceptual,
> like bounding boxes for hit detection."

<https://en.esotericsoftware.com/spine-attachments>

> "A slot can have only a single attachment visible at any given time, or no attachments
> visible."

<https://en.esotericsoftware.com/spine-skins>

> "Skins allow a skeleton's animations to be reused with different sets of attachments."

> "Skins are made up of attachments, bones, and constraints that are only active when the
> skin is visible."

> "Skins can be used for simple outfits swaps or to assemble entire characters out of many
> different pieces."

<https://en.esotericsoftware.com/spine-json-format> (fetched; the structure below is the
page's own field descriptions, condensed by the fetch tool — the nesting and the quoted
phrases are verbatim, the surrounding prose is not)

> bones: `"name"`, `"parent"`, `"x"`, `"y"` — *"position relative to parent for setup pose"*,
> `"rotation"`, `"scaleX"`, `"scaleY"`, `"shearX"`, `"shearY"`, `"length"`.
>
> slots: `"name"`, `"bone"` (parent bone reference), `"attachment"` (setup-pose attachment
> name), `"color"`, optional `"blend"`.
>
> skins: `"skins"` → skin name → `"attachments"` → slot name → attachment name → attachment
> data. *"The default skin contains attachments not defined by a skin in Spine."*

**The two sentences that matter for T4.** First, *"even for skeletons that are objects rather
then characters"* — Spine's own framing is that a prop is not a different document type, it is
a skeleton with fewer bones. Second, the skin mechanism is **exactly** `an`'s
`Skin.slots: dict[str, dict[str, Attachment]]` (`an/characters/schema.py:196-209`) — the
nesting `skin → slot → attachment name → data` is identical, which is unsurprising because
`Attachment.x/y`'s own docstring cites Spine as the source (`schema.py:180-188`).

**A held prop, in Spine.** The mechanism is composition, not a special feature: the prop's
image is a region attachment in a slot whose bone is a child of the hand bone, so
*"translating an arm bone will also translate the hand bone"* carries it. I could not fetch a
page stating a hand-bone-attachment recipe in those words (`spine-skeleton-viewer` and
`spine-regions` were fetched and do not contain one) — **UNVERIFIED as a documented recipe,
certain as a mechanism**, since it is a direct consequence of the two quoted sentences.

### 2.2 Moho — image layers and vector layers are peers; a bone layer is a container

<https://www.lostmarble.com/moho/manual/layerwnd.html>

> "Certain types of layers act as 'groups', and can contain other layers within them."

> "If this box is checked and the image layer is placed inside a bone layer, the bones can be
> used to warp the image as if it were printed on a rubber sheet."

Moho's layer types are vector, image, group, bone, switch, particle, 3D and note; an image
layer "contains a single image file that can be used as a background, or combined with a bone
layer to build a character", and binding to bones happens three ways — automatic, manual, and
**layer binding** (fetched summary of the Moho manual via search, not a verbatim block —
<https://www.lostmarble.com/moho/manual/>; treat the exact wording as **UNVERIFIED**, the
taxonomy as fetched).

**Implication for T4.** Moho's model is the cleanest statement of "the drawable and the rig
are separate concerns": an image layer is a *peer* of a vector layer, and a bone layer is a
*container* that may or may not sit above either. `an` has no such split — `VisualJSON.kind`
is a closed literal (`serialize.py:101`) and the rig is the descriptor. The transferable rule
is that **a prop should not have to declare a rig to be drawable**, which §3.3 turns into
"the `PropDescriptor` default is one bone and one slot, not zero of each".

### 2.3 Toon Boom Harmony — drawing layers hold art; pegs hold transforms

<https://docs.toonboom.com/help/harmony-22/premium/rigging/add-peg.html>

> "Pegs are a special type of layer that do not contain any drawing. They are used strictly to
> offset and transform drawings that are under their hierarchy, without transforming the
> drawings directly."

> "When rigging or setting up a scene, it is recommended to add parent pegs for each of your
> drawing layers. This allows you to keep animation keyframes and drawings on separate layers,
> making it easier to work on the position and exposure of your drawing layers independently
> in the Timeline view."

<https://docs.toonboom.com/help/harmony-22/premium/rigging/about-peg-hierarchy-rig.html>

> "Peg layer are useful when you are doing more advanced puppet rigging. Peg layers are
> trajectory layers that do not contain drawings."

> "Then, you can perform your translation and rotation on the peg layer so all the parts
> attached to that peg layer follow the same trajectory."

> "Parenting a drawing layer to a peg layer allows you to divide your motions on two separate
> levels."

**Implication.** Harmony separates *the art* from *the thing you animate*. `an` already has
that split and did not name it: an entity's container node carries the transform and its slot
children carry the art (`compile.py:1371-1375`), which is a peg with a drawing under it. So a
prop needs **no new concept** to be animatable — it needs a container node, which the rig
builder already emits. Harmony's recommendation ("a parent peg for each drawing layer") is
also the argument against giving `AssetRef` a bare `x`: the transform belongs to a node, not
to a reference.

### 2.4 Godot — `Sprite2D` vs a `Node2D` scene

<https://docs.godotengine.org/en/stable/classes/class_sprite2d.html>

> "A node that displays a 2D texture. The texture displayed can be a region from a larger
> atlas texture, or a frame from a sprite sheet animation."

Inheritance: `Sprite2D < Node2D < CanvasItem < Node < Object`. Properties include
`centered` (bool, default `true`, *"If `true`, texture is centered."*), `offset` (Vector2,
*"The texture's drawing offset."*), `region_enabled` (bool, default `false`, *"If `true`,
texture is cut from a larger atlas texture."*), `texture` (Texture2D, *"Texture2D object to
draw."*).

<https://docs.godotengine.org/en/stable/classes/class_node2d.html>

> "A 2D game object, with a transform (position, rotation, and scale). All 2D nodes, including
> physics objects and sprites, inherit from Node2D."

> position: "Position, relative to the node's parent". rotation: "Rotation in radians, relative
> to the node's parent". scale: "The node's scale, relative to the node's parent".

<https://docs.godotengine.org/en/stable/getting_started/step_by_step/instancing.html>

> "A scene is a collection of nodes organized in a tree structure, with a single node as its
> root."

Instancing is *"replicating an object from a template like this"*; the guide recommends
"creating a scene for each element" that is an entity visible to the player. It gives **no
stated criterion** for one-node-versus-scene (fetched and checked).

**Implication.** Godot draws the line where reuse begins, not where complexity begins: a
single-texture prop is a `Sprite2D`; the moment it has moving parts or its own behaviour it is
a saved scene. Applied here, that argues for **one document type with a trivial default**
rather than two document types — a `PropDescriptor` whose default is "one bone, one slot"
degenerates to `Sprite2D` and grows into a scene without a rewrite. This is the single
strongest external support for §3's option (a) over option (b).

### 2.5 Live2D — Parts are grouping, and draw order is decoupled from them

<https://docs.live2d.com/en/cubism-editor-manual/partspalatte/>

> "allows objects to be managed by classifying parts, such as bangs, eyes, and mouth, into
> broad categories."

<https://docs.live2d.com/en/cubism-editor-manual/draworder/> (fetched via search summary; the
numeric range and tie-break are the page's own statements — **UNVERIFIED as verbatim
wording**)

> Draw order applies to drawable objects (ArtMesh, ArtPath) with values 0–1000, highest in
> front; when values are equal, the part highest in the Parts palette list wins.

**Implication.** Live2D is the system that *does* carry an explicit numeric draw order, and
even there it needs a list-order tie-break. That is the same conclusion T2 reached from a
different direction (§3.1: "list order is draw order, and there is deliberately no `z`
field"), and it is why §3.7 gives a prop **no** `z` — `an`'s runtime sorts nothing
(`buildSceneTree` calls `parent.addChild(container)`, `runtime.js:143`; `grep -c
"sortableChildren\|zIndex" runtime.js` = 0, per T2 §1.5).

### 2.6 Lottie — precomps: an asset referenced by id, transformed by the referencing layer

<https://lottiefiles.github.io/lottie-docs/assets/>

> Precomposition asset: `id` — "Unique identifier used by layers when referencing this asset";
> `layers` — "Layers"; `nm` — "Human readable name…"; `fr` — "Framerate in frames per second".
>
> Image asset: `id`, `w` — "Width of the image", `h` — "Height of the image", `u` — "Path to
> the asset file", `p` — "Name of the asset file or a data url", `e` — "If '1', 'p' is a Data
> URL".

<https://lottiefiles.github.io/lottie-docs/layers/>

> Precomposition Layer: "This layer renders a precomposition." `refId` — "ID of the precomp as
> specified in the assets"; `w`/`h` — "Width/Height of the clipping rect"; `tm` — "Timeline
> remap function (frame index -> time in seconds)".
>
> Image Layer: "This layer renders a static image." `refId` — "ID of the image as specified in
> the assets".
>
> Common: `ind` — "Index that can be used for parenting and referenced in expressions";
> `parent` — "Must be the `ind` property of another layer"; `ks` — "Layer transform".

**Implication — the sharpest borrowing in this survey.** Lottie's *image layer* and its
*precomp layer* differ in exactly one thing: what `refId` points at. Everything about
placement (`ks`), parenting (`parent`), and timing is on the **layer**, not on the asset. That
is precisely the split §3.6 proposes: the prop **document** says what the thing is and how it
is built; the **entity reference in the shot** says where it is, how big, how deep, and what
it hangs off. It also validates keeping `AssetRef.store` + `ref` as the only identity — that
is `refId`.

### 2.7 PixiJS v7 — `NineSlicePlane` (Wave 9's prop, present in the vendored bundle)

Source, `pixi.js@7.4.2`, `packages/mesh-extras/src/NineSlicePlane.ts`
(<https://raw.githubusercontent.com/pixijs/pixijs/v7.4.2/packages/mesh-extras/src/NineSlicePlane.ts>):

```
/**
 * The NineSlicePlane allows you to stretch a texture using 9-slice scaling. The corners will remain unscaled (useful
 * for buttons with rounded corners for example) and the other areas will be scaled horizontally and or vertically
 *
 * <pre>
 *      A                          B
 *    +---+----------------------+---+
 *  C | 1 |          2           | 3 |
 *    +---+----------------------+---+
 *    |   |                      |   |
 *    | 4 |          5           | 6 |
 *    |   |                      |   |
 *    +---+----------------------+---+
 *  D | 7 |          8           | 9 |
 *    +---+----------------------+---+
 *  When changing this objects width and/or height:
 *     areas 1 3 7 and 9 will remain unscaled.
 *     areas 2 and 8 will be stretched horizontally
 *     areas 4 and 6 will be stretched vertically
 *     area 5 will be stretched both horizontally and vertically
 * </pre>
 * @example
 * import { NineSlicePlane, Texture } from 'pixi.js';
 *
 * const plane9 = new NineSlicePlane(Texture.from('BoxWithRoundedCorners.png'), 15, 15, 15, 15);
 * @memberof PIXI
 */
```

```typescript
constructor(
    texture: Texture,
    leftWidth?: number,
    topHeight?: number,
    rightWidth?: number,
    bottomHeight?: number
)
```

Per the API reference (<https://api.pixijs.io/@pixi/mesh-extras/PIXI/NineSlicePlane.html>,
fetched via search summary — treat the default value as **UNVERIFIED against the source**):
the four border parameters default to `10`, are settable properties, and *"Setting the width,
leftWidth, rightWidth, topHeight, or bottomHeight will modify the vertices and UVs of the
plane."*

**It is already in the shipped engine.** `PIXI.NineSlicePlane` is exported by the vendored
bundle — `_.NineSlicePlane=im` appears once in
`an/data/cutout_runtime/vendor/pixi.min.js` (pixi.js@7.4.2, MIT; digest pinned at
`tests/test_vendored_engine.py:13-14`). So Wave 9's nine-slice prop needs **no new vendored
code**: it needs a `VisualJSON.kind` member, a `makeVisual` branch (`runtime.js:147-172`), and
four border numbers on the visual. Two warnings that follow from T5:

- `makeSvgSprite`'s `contain` fit and `refitToBox` (`runtime.js:174-184`) are the wrong policy
  for a nine-slice: the whole point is that the box is authored and the *corners* are
  invariant. A nine-slice visual needs its own sizing branch, not `_anFitBox`.
- A **new field on `VisualJSON` with a serializing default lands in every node of every
  document** and moves every corpus contract hash (`to_dict` is `model_dump(mode="json")` with
  no None pruning, `serialize.py:321-323`; T5 §3.2 row 1). `VisualJSON.fit`
  (`serialize.py:111`) is the counter-example already in the tree. Nine-slice borders must be
  `None`-defaulted **and popped by an omit-if-unset serializer**, on the
  `_omit_unset_step_hz` precedent (`serialize.py:284-290`).

### 2.8 Unity — for the record, and against a second tiling vocabulary

T2 §2.2 already fetched and quoted Unity's `Draw Mode` (Simple / Sliced / Tiled), `Tile Mode`
(Continuous / Adaptive) and the Full-Rect prerequisite for 9-slicing. Nothing here duplicates
that; the one T4 consequence is that Unity's nine-slice and Godot's `Parallax2D.repeat_size`
are **two different encodings of repetition** (fill-a-rect versus a period), so a prop that
grows a `repeat` field later must pick the same one T2's `Plane.repeat_size` picks. Do not let
props and planes diverge on this.

### 2.9 How a held prop attaches, in each — and what our rig does not have

| system | mechanism |
|---|---|
| Spine | prop image is an attachment in a slot on a bone parented to the hand bone; *"A bone's transform affects its child bones. For example, translating an arm bone will also translate the hand bone."* |
| Moho | the prop's image/vector layer is placed inside the character's **bone layer** and bound (layer binding) |
| Toon Boom | the prop's **drawing layer** is parented under the hand's **peg** in the peg hierarchy; *"all the parts attached to that peg layer follow the same trajectory"* |
| Godot | the prop scene is instanced as a **child node** of the hand node; *"Position, relative to the node's parent"* |
| Live2D | parts are grouping only; a held object is an ArtMesh under the arm's deformer |
| Lottie | `parent` — *"Must be the `ind` property of another layer"* |

**All six are the same mechanism: the prop becomes a child of the node that is the hand.**
`an`'s scene graph does this natively — `buildSceneTree` adds children and the runtime
composes transforms (`runtime.js:143`).

**But the default rig has no hand.** `_default_bones` is seven bones —
`root, torso, head, arm_l, arm_r, leg_l, leg_r` (`an/characters/schema.py:555-569`).
`_default_slots` is eleven slots — `leg_l, leg_r, torso, arm_l, arm_r, head, left_eye,
right_eye, mouth, left_brow, right_brow` (`schema.py:586-614`). `REQUIRED_PARTS` is twelve
names with no hand (`schema.py:106-119`). The arm bones sit at the **shoulder**
(`Bone(name="arm_l", parent="torso", x=-90, y=-240, pivot="shoulder_l")`, `schema.py:562`),
and `bones_from_pivots` only re-places bones that already declare a `pivot` name — it iterates
the rig, not the drawing's joints (`schema.py:657-664`: `for bone in rig: target =
pivots.get(bone.pivot)`). So an illustrator who draws a `hand_l` circle in the `skeleton`
group gets it **silently ignored**.

Three consequences, stated so Wave 7 does not quietly promise the wrong thing:

1. Attaching a prop to `arm_l` today parents it at the **shoulder**, and it would rotate
   about the shoulder. That is not "a character holding a cup"; it is a cup pinned to a
   collarbone.
2. Adding a hand bone is a **rig-contract change** — a new default bone, a new pivot name, and
   a decision about whether `hand_l.svg` joins `REQUIRED_PARTS` (which would make every
   existing rig fail `an character validate` at `validate.py:216-222`). That is Wave 4
   territory, not a prop-document change.
3. Therefore the honest Wave 7 done-when for attachment is *"a prop attaches to a **named
   node**"*, with the hand bone filed as a rig follow-up — and §3.5 shows attachment costs one
   more compiler change beyond re-parenting anyway, which is why §3.8 puts it in a second PR.

---

## 3. The design

### 3.0 The recommendation, in one line

> **(a) A `PropDescriptor` that is a thin profile of `CharacterDescriptor`** — the same field
> vocabulary (`Bone` / `Slot` / `Attachment` / `Skin` / `asset_sets`), its own `DocumentKind`
> and store, **different defaults** (one bone, one slot, no animations, no swap sets), sharing
> **one extracted rig builder** with the character path. Placement, depth and stacking live on
> the shot's entity reference, not in the document.

Options (b) and (c) are refuted below on code facts, not on taste.

### 3.1 Why not (c) — "a prop is a character with `kind: prop`"

This is the cheapest-looking option and it is the one with the most landmines, all of them in
`CharacterDescriptor.model_post_init` (`an/characters/schema.py:369-384`):

```python
def model_post_init(self, __context):
    if not self.bones:   self.bones = list(_default_bones())      # 7 humanoid bones
    if not self.slots:   self.slots = list(_default_slots())      # 11 humanoid slots
    if not self.skins:   self.skins = {"default": _default_skin()}  # parts/head.svg, parts/torso.svg, …
    if not self.animations:
        self.animations = {"idle_breath": …, "blink": …}
```

plus `asset_sets: … = Field(default_factory=default_asset_sets)` (`schema.py:311`), which
seeds a nine-key `viseme` map and a two-key `eyelid` map (`schema.py:96-103`).

**A `CharacterDescriptor(name="sword")` is a seven-bone humanoid with a face and a blink.**
An empty list does not mean "no rig" — it re-seeds the default. To express a one-image prop you
must defensively override **five** fields (`bones`, `slots`, `skins`, `animations`,
`asset_sets`), and get each one right, or the compiler declares textures for `parts/head.svg`,
records twelve missing parts through `_record_missing_parts` (`compile.py:1133-1187`), and —
under the bench's `strict_assets=True` (`an/bench/corpus.py:40-44`) — **fails the render**.

Four more facts against it:

1. **The placeholder fallback draws a human.** `_build_character_subtree` (`compile.py:854-1043`)
   dispatches to the SVG rig only when `char_meta.get("kind") == "CharacterDescriptor"`
   (`compile.py:899`); otherwise it builds `_PLACEHOLDER_PARTS` with hair, brows, eyes and a
   drawn mouth (`:930-1043`). A prop whose ref is missing or malformed would render **a
   humanoid figure** where a lamp should be — the an#33 failure mode ("a stand-in asset renders
   happily as a DIFFERENT picture") in its purest form.
2. **`an character validate` is hardcoded to the human inventory.** Twelve `REQUIRED_PARTS`
   (`validate.py:216-222`) and nine `MOUTH_SHAPES` (`:223-230`), all `BLOCKING`. A prop scores
   21 blocking findings on a correct document.
3. **`an character silhouette`** compares character *distinctness* with an IoU and advises
   "consider redesigning" (`an/characters/cli.py:207-255`). Meaningless for a prop, and its
   `<char_dir>/<name>.svg` requirement is a character-promote artefact.
4. **Kind is not just a label — five call sites branch on it** (`compile.py:461`, `:709`,
   `:717`, `:2051`, `:2508`; `validate.py:446`). "Only the entity kind differs" is already
   false at the fifth site.

The one *good* thing about (c) — that a prop with real moving parts is a character in all but
name — is preserved by (a), because (a) uses the same models.

### 3.2 Why not (b) — a distinct minimal document `{name, art, anchor, size, states}`

This is the shape a from-scratch design reaches for, and it loses four things the tree already
has:

1. **`states` is `asset_sets` renamed.** The an#87 machinery keys on
   `VisualJSON.asset_sets: {set: {KEY: asset_id}}` (`serialize.py:114`), populated by the
   per-slot projection (`compile.py:1343-1355`), consumed by the runtime's `applySwap`
   (`runtime.js:420-453`) and checked by `_check_swap_action` (`compile.py:1869-1977`). A
   second vocabulary means either a second projection code path or a translation table — and
   the repo's own record on translation tables is explicit: *"a rename table is where a field
   quietly stops being carried"* (`schema.py:592-594`, and again at `:1264-1266` in the
   compiler's alias comment).
2. **A flat `{art, anchor, size}` has a hard ceiling at one moving piece.** A chest with a
   hinged lid, a book that opens, a sign that swings — each needs a second node with its own
   pivot, i.e. a bone and a slot. At that point (b) *becomes* (a), badly, mid-flight, with
   stored documents behind it.
3. **It gives up `strict_assets`, provenance and migrations for free.**
   `_record_missing_parts`'s two-case split (`compile.py:1152-1187`) — `missing`/`fallback=True`
   versus `incomplete`/`fallback=False`, *"because the frame is not wrong, only the inventory
   is incomplete"* — is exactly right for a prop's state set and would have to be rebuilt.
   Same for `AssetSource` (`an/ir/assets.py:76-116`) and `register_kind`/`register_migration`
   (`an/ir/migrate.py`).
4. **Godot's own line is against it** (§2.4): the split is drawn at *reuse*, not at
   *complexity*, and a single-node prop is a degenerate scene rather than a different kind of
   thing.

The legitimate motive behind (b) — "simple things simple" — is met by (a)'s **defaults**, not
by a second schema. §3.3 spends exactly one paragraph on making a one-image prop a five-line
document.

### 3.3 The recommended model

New package `an/props/`, mirroring `an/characters/`: `schema.py` (models + kind + migrations),
`factory.py`, `promote.py`, `cli.py`.

```python
# an/props/schema.py
from an.characters.schema import Bone, Slot, Attachment, Skin      # SHARED, not copied
from an.ir.assets import AssetSource
from an.ir.migrate import DocumentKind, register_kind

PROP_SCHEMA_VERSION = "0.1.0"

PROP_DOCUMENT_KIND: DocumentKind = register_kind(
    DocumentKind(name="PropDescriptor",
                 version_field="schema_version",
                 current_version=PROP_SCHEMA_VERSION)
)

class PropDescriptor(BaseModel):
    model_config = ConfigDict(extra="allow", populate_by_name=True)

    schema_version: str = PROP_SCHEMA_VERSION
    kind: Literal["PropDescriptor"] = "PropDescriptor"

    name: str
    display_name: str | None = None
    view_box: tuple[int, int, int, int] = DEFAULT_VIEW_BOX      # shared constant

    bones: list[Bone] = Field(default_factory=list)
    slots: list[Slot] = Field(default_factory=list)
    skins: dict[str, Skin] = Field(default_factory=dict)
    asset_sets: dict[str, dict[str, str]] = Field(default_factory=dict)   # EMPTY, not seeded
    animations: dict[str, IdleAnimation] = Field(default_factory=dict)    # EMPTY, not seeded

    source: AssetSource | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    def model_post_init(self, __context):
        if not self.bones:
            self.bones = [Bone(name="root")]
        if not self.slots:
            self.slots = [Slot(name="body", bone="root", draw_order=0, attachment="body")]
        if not self.skins:
            self.skins = {"default": Skin(slots={"body": {"body": Attachment(path="art/body.svg")}})}
```

The **defaults are the whole design decision**: `PropDescriptor` seeds one bone, one slot, one
attachment, and — crucially — **nothing else**. A one-image prop is therefore:

```json
{"kind": "PropDescriptor", "schema_version": "0.1.0", "name": "lantern"}
```

with `art/body.svg` beside it. A two-state prop adds one map and one attachment:

```json
{"kind": "PropDescriptor", "name": "chest",
 "skins": {"default": {"slots": {"body": {
     "closed": {"path": "art/closed.svg", "anchor": [0.5, 1.0]},
     "open":   {"path": "art/open.svg",   "anchor": [0.5, 1.0]}}}}},
 "asset_sets": {"state": {"CLOSED": "closed", "OPEN": "open"}}}
```

and that is `{kind: set, target: chest/body, property: state, value: OPEN}` in `scene.md`,
with **no compiler, runtime, serialize or schema change** — the exact claim
`.claude/skills/an-dev-swap-channels/SKILL.md:80-92` already makes and proves for characters.

Three deliberate omissions:

- **No `face_overlay`.** The suppression branch is keyed on the head bone
  (`compile.py:1297-1302`) and is inert on a rig without one; adding the field would be a
  second, redundant switch. Say so in the model's docstring so nobody re-adds it.
- **No `expression_binding` / `gaze_travel`.** Both are face machinery
  (`schema.py:341-354`).
- **No `voice_ref`.** A prop does not speak. (If a talking prop ever ships, it is a character
  with a non-humanoid rig — that is what (a) buys.)

### 3.4 The compiler changes, and their exact cost

**Five edits, four of them one-line.**

1. **`_svg_asset_src` takes the prefix** (`compile.py:1053-1055`):
   ```python
   def _svg_asset_src(ref: str, rel_path: str, *, prefix: str = "characters/") -> str:
       return f"{prefix}{ref}/{rel_path}"
   ```
   Three call sites, all inside the rig builder (`compile.py:1268`, `:1284`, `:1321`).
2. **`_part_probe` takes the same prefix** — it hardcodes `prefix = "characters/"` at
   `compile.py:1093` and tests `src.startswith(prefix)` at `:1096`. Give it
   `_part_probe(store, *, prefix="characters/")`. Note its `None` return contract
   (`compile.py:1077-1081`): a store with no filesystem root must yield **no probe**, not a
   probe that answers "absent" — keep that for props.
3. **`_build_svg_character_subtree` becomes `_build_rig_subtree`** (`compile.py:1212-1375`),
   parameterised by `(descriptor, store_prefix, kind_label)`. Everything from
   `k = SCENE_PX_PER_VIEW_BOX / view_box_height` (`:1247`) through the return (`:1371-1375`)
   is already generic; the only character-specific line is `head_has_face = not
   desc.face_overlay` (`:1261`), which becomes a parameter defaulting to `False` for props.
   `_record_missing_parts`'s `kind="part"` literal (`:1141`) becomes the `kind_label`.
   **This refactor must be output-identical for characters** — see §4.2.
4. **`_build_scene_root` grows a prop branch** (`compile.py:717-724` — the `raise` becomes a
   build). It sits in the character loop today; §3.7 moves prop emission to a single ordered
   walk instead, which is the same structural edit T2 §3.3 already requires for
   `characters_after` ("the structural edit lands in 7a with zero output change").
5. **`_swap_vocabulary` widens beyond characters** (`compile.py:461`). Today:
   `if entity.kind != "character" or entity.ref not in chars_store: continue`. This is what
   populates `declared`, `declared_maps`, `descriptors`, `art_exists` and `entity_scale` — and
   without it a prop's `play` raises (`compile.py:1809-1814`) and its `entity_scale` silently
   reads 1.0 (`:1809`, `:2632`). Replace the kind test with a `{kind: (store, model, doc_kind)}`
   table. **The four face gates must stay kind-checked** (`compile.py:709`, `:2051`, `:2508`,
   `validate.py:446`) — widening `_swap_vocabulary` must not widen those, and a test should say
   so out loud, because "props are skipped by the face solver" is currently true only by
   accident of the `!= "character"` guard.

**Not needed, verified:** no `VisualJSON` change (a prop is `kind="svg_sprite"` like every
other sprite), no `NodeJSON` change, no `TransformJSON` change, no runtime change. That is what
makes props hash-free (§4.2).

### 3.5 Attaching to a character — and the one non-obvious cost

The mechanism is re-parenting: emit the prop's subtree as a child of the host node instead of
a child of `root`. All six surveyed systems do exactly this (§2.9), and `an`'s runtime composes
transforms natively.

**The cost is `_track_root_of`.** `compile.py:2021-2023` defines entity identity as *the first
segment of the target path*. An attached prop's node path becomes `charlie/arm_l/lantern/body`,
so:

- `_check_swap_action` computes `entity_id = _track_root_of(target)` = `"charlie"`
  (`compile.py:1893`), then looks up `vocab.declared["charlie"]` — which **exists** (charlie has
  a descriptor), so the descriptor-less fallback at `:1904-1921` does **not** fire, and the
  prop's `state` set is reported as *"`charlie`'s descriptor declares no asset set named
  `'state'`"* (`:1922-1928`). A correct scene refused, with a misleading message.
- Track grouping (`_compile_actions` → `TrackJSON.target_root`, consumed at
  `compile.py:2505`) files the prop's channels under the host's track.
- `_add_face_clips`'s `track_lookup = {t.target_root: t for t in tracks}` (`compile.py:2505`)
  would collide.

The fix is a **node → owning entity** map in `_SwapVocabulary` (`compile.py:363-402`) —
populated by `walk` (`compile.py:438-452`), which already visits every node — replacing
`_track_root_of`'s assumption at the swap-check site. That is a real change with its own tests,
and it is why §3.9 puts attachment in a **second** PR.

**Do not** implement attachment by copying the prop's art into the character's descriptor.
That is Spine's *skin* mechanism (§2.1), it is legitimate, and it is a different feature: a
held item that is part of the character's inventory (`asset_sets["hands"]`, already shipping
in the `gale` fixture) versus a prop that exists independently in the scene. Both should exist;
they are not substitutes.

### 3.6 Placement in `scene.md`

**`AssetRef` carries no transform today** (§1.1). Three candidate mechanisms, scored:

| | mechanism | verdict |
|---|---|---|
| (i) | reuse `AssetRef.overrides` | **No.** Untyped `dict[str, Any] \| None`, read by nothing (`rg '\.overrides'` = 0 hits), and typing it per kind is a discriminated-union job. T2 §1.2 reaches the same conclusion: *"There is already one dead per-shot override channel; do not make it the second."* |
| (ii) | actions only — `{kind: set, target: lantern, property: x, value: 120}` at t=0 | **Works today, and is a trap.** `set` compiles to a hold from t=0 to the shot end (`compile.py:1476-1510`), so it does place the prop. But `_rest_value_for` is a **global** table (`compile.py:307-326`, `:267-268`), so a later `tween` on `lantern/x` with no `from` starts at **0.0** — the prop teleports to frame centre when the tween begins. Verbose (x, y, scale = three actions) and silently wrong at the first tween. |
| (iii) | **one additive optional field on `AssetRef`** | **Recommended.** |

**The minimal additive field.** One optional sub-model, not four scalars, so that per-kind
validation has somewhere to live and so `AssetRef` (shared by five kinds) grows one field, not
four:

```python
# an/ir/schema.py
class StagePlacement(_IRModel):
    """Where an entity stands on the stage, for one shot.

    The Lottie split (§2.6): the DOCUMENT says what the thing is; the LAYER says
    where it is. `at` becomes the entity container's rest transform, exactly as
    `_layout_character_positions` does for characters today (compile.py:715).
    """
    at: tuple[float, float] | None = None   # scene units, from frame centre
    scale: float | None = None              # uniform; None = the art's own size
    depth: float = 1.0                      # T2's ONE scalar. 0=frozen, 1=character plane
    after: str | None = None                # T2's `characters_after` shape; see 3.7

class AssetRef(_IRModel):
    ...
    stage: StagePlacement | None = None     # additive; None for every scene today
```

Why this is safe and cheap:

- **It is IR, not wire.** `scene_contract_sha256` hashes the **compiled cutout document**
  (`an/bench/contract.py:53-71`), which is `serialize.to_dict(scene)`. An `AssetRef` field
  never appears there. So unlike a `VisualJSON` field, this is hash-free **by construction**,
  not by an omit-serializer. (T5 §3.2's warning about defaults applies to the wire models
  only.)
- **`scene.md` is unchanged for every existing scene.** The writer dumps
  `model_dump(exclude_none=True, exclude_defaults=False)` (`sync.py:417-421`), so a `None`
  `stage` is omitted. `_extract_entities_block` has no whitelist (`sync.py:230-242`), so the
  block round-trips.
- **No `SCHEMA_VERSION` bump and no migration.** An optional field with a default is additive
  — the same call `CharacterDescriptor.expression_binding` and `gaze_travel` made
  (`schema.py:341-354`, both documented "Additive: no schema bump"). CLAUDE.md's rule is about
  *bumping* without a migration, not about additive fields.
- **The consumer ships in the same change**, per `serialize.py:9-19`: `at`/`scale` become the
  entity container's `TransformJSON` (the `sub.transform.x = x` site, `compile.py:715`);
  `depth` feeds T1's camera emitter through T2's `parallax_factor` contract; `after` feeds the
  ordered emission walk (§3.7).

**Interaction with `set` / `tween` on the entity root, stated exactly.** `stage.at` sets the
container node's **rest** transform. An authored `set` on `lantern/x` overrides it from its
time onward (hold semantics, `compile.py:1476-1510`). An authored `tween` on `lantern/x`
**with no `from` still starts at 0.0**, because `_rest_value_for` does not consult the node
(`compile.py:307-326`). Three honest responses, in order of preference:

1. **Warn at compile** when a tween has no `from_value` on a property whose target node has a
   **non-default** rest value for it — the diagnostic writes itself from
   `vocab.node_transforms[path]`, which the expression solver already reads for exactly this
   purpose (`compile.py:2709`). One warning, one test; it fixes the pre-existing character
   case too (a two-character scene places them at ±110).
2. Document it in `iterate.py`'s prompt beside the existing rest-value sentence
   (`an/iterate.py:151-152`: *"A tween with no 'from' starts at the property's rest value: 1.0
   for scale_x / scale_y / alpha, 0.0 for the rest."*).
3. **Do not** make `_rest_value_for` node-aware in Wave 7. It would change the compiled
   keyframes of any existing scene that tweens a placed character's `x` with no `from` — a
   contract-hash move for a knob nobody turned, which is precisely what T5 §3.1 forbids.
   (`rg` the corpus before deciding; if no corpus scene does it, this becomes a Wave 8
   candidate with an exemption set.)

**Naming.** Call it `stage`, not `transform`: it carries `depth` and `after`, which are
staging facts, not transform components. And `at`, not `x`/`y`, so T2's named anchors
(`EnvironmentDescriptor.anchors`, T2 §3.1) can later widen it to `at: tuple[float, float] | str`
— a plane's `anchors["bench"]` resolving to a point. Reserve that; do not build it in 7a.

### 3.7 Naming the depth plane — in T2's vocabulary, not a second one

T2's ruling, restated: **`depth`** is one scalar (0.0 frozen … 1.0 character plane … >1.0
foreground), surfaced as `parallax_factor -> (x, y)`; **draw order is list order**, there is no
`z`; and **relative stacking is `<something>_after: <plane name>`**, Rive's target rule, chosen
because five of seven surveyed systems keep depth and stacking decoupled.

A prop names its plane with exactly those two words and no others:

- **`stage.depth: float = 1.0`** — the same scalar, the same semantics, the same
  `Field(ge=0.0)` refusal of negatives. Default 1.0 = the character plane = today's behaviour
  for everything. It belongs on the **entity reference**, not the descriptor, because a lamp is
  near the camera in one shot and far in another; that is the Lottie split again (§2.6).
- **`stage.after: str | None = None`** — names the plane (or `"characters"`) after which this
  prop's subtree is emitted. `None` = with the characters, in `shot.entities` order. This is
  `EnvironmentDescriptor.characters_after`'s shape and vocabulary verbatim (T2 §3.1), so a
  foreground prop is `after: characters` and a prop tucked behind the mid-ground is
  `after: hills`.

**What this forbids, and why.** No `z: int`, no `layer: "foreground"`, no `foreground: true`,
and **no deriving stacking from `depth > 1.0`**. T2 §3.3's reasoning applies unchanged: the
depth rule cannot express a fence at `depth = 0.9` that the characters stand *behind*, it
resolves the `depth == 1.0` tie by convention rather than dissolving it, and the runtime has no
z-index to honour an integer with anyway (`grep -c "sortableChildren\|zIndex" runtime.js` = 0).

**The structural consequence is shared with T2 and should be paid once.** Today
`_build_scene_root` runs two hard-coded loops (`compile.py:701-707`, `:708-716`), so *no
ordering of `shot.entities` can put anything behind the environment or in front of a
character.* T2 §3.3 already requires replacing this with a single ordered walk that emits
character subtrees after the plane named by `characters_after`, and records that with
`characters_after=None` the child array is byte-identical to today's. Props join that same
walk keyed on `stage.after`. **One structural edit, two features, zero output change on every
existing document.** Do not implement a second, prop-specific ordering pass.

### 3.8 Where things live — the concrete inventory

| piece | location | note |
|---|---|---|
| `PropDescriptor`, `PROP_DOCUMENT_KIND`, migrations | `an/props/schema.py` | registered from the owning package, per `an/ir/migrate.py:21-26`; reuses `Bone`/`Slot`/`Attachment`/`Skin` from `an/characters/schema.py` |
| `PropsStore(JsonSidecarStore)`, `META_NAME = "prop.json"` | `an/stores/props.py` | mirrors `CharactersStore` (`an/stores/characters.py:14-25`); `meta.json` would work but a named file is self-describing and the character store already set the precedent |
| mall wiring | `an/stores/__init__.py:84` (+ `assets/props` in the `ensure` list at `:64-84`) | **the module doctest at `:13-17` asserts the key set — it goes from eleven to twelve** |
| staging prefix | `an/adapters/cutout/render.py:561-565` — add `"props/": "props"` | `tests/test_asset_staging.py:96-97` then passes only because the mall has the store; `:100-112`'s `props/banner.png` case **must move to a genuinely unknown prefix** or it goes red |
| shared rig builder | `an/adapters/cutout/compile.py` — `_build_rig_subtree` | §3.4 |
| `an prop new` / `promote` / `validate` | `an/props/cli.py`, mounted via `an/tools.py:512-514` `_dispatch_namespaces` | `__main__.py` mounts each namespace as a typer sub-app (`an/__main__.py:1-50`) |
| credits | `an/credits.py:110-116` | its docstring names props explicitly — **this is the PR that makes that sentence false, so update it** |

**`an prop new <name>`**, modelled on `an character new` (`an/characters/cli.py:42-52`) but
**offline and free by default** — there is no DiceBear for props:

```
an prop new lantern [--art path/to.svg] [--out-dir …] [--anchor 0.5,1.0] [--overwrite]
```

With `--art`, normalise and copy the file (`an/characters/svg_utils.py` — `normalize_svg`,
`write_svg`), probe its size (`raster_size`, `svg_utils.py:163-190`), record
`AssetSource(provider=…, sha256=…)`. With no `--art`, write a deterministic placeholder SVG so
the command works with no network and no assets — the same reason `an character new --offline`
exists. **Do not** reach a paid or network API from a `new` command by default; the federation's
live-test rule (video_gen CLAUDE.md, D-vg-audio-02) is about tests, but the ergonomic principle
is the same and `an character new`'s DiceBear default is a wart, not a model.

**`an prop promote`** is a **sibling** of `an.characters.promote`, not a parameterisation of
it. `promote.py` imports `new_character`, `write_default_mouths`, `_synthesize_brow`,
`_synthesize_eye_open`, `_synthesize_eye_closed`, `bones_from_pivots` and `validate_character`
(`an/characters/promote.py:15-30`) — every one of those is the human pipeline. The reusable
half is `an/characters/svg_utils.py` (`extract_part`, `extract_pivots`, `normalize_svg`,
`write_svg`, `raster_size`). `an prop promote <entity> --as <id> [--layers a,b,c]` slices named
layers out of a source SVG into `art/<name>.svg` and writes one slot per layer with
`draw_order` from the source order — no pivots, no mouths, no `REQUIRED_PARTS`.

**`an prop validate`** is a *short* validator: the referenced attachment files exist, no
duplicate slot names, every `asset_sets` key names an attachment that exists (the reusable half
of `_check_asset_sets`, `an/characters/validate.py:255+`), and an advisory when `source is
None` — the same wording `validate_character` uses (`validate.py:242-251`), because *"a licence
defect is the only failure that reaches backwards through finished work"*.

### 3.9 PR order (inside T5 §3.3's 7a/7b budget)

T5 §3.3 already sequences Wave 7 and puts props at **7a step 3**, before the camera and before
planes, because they move no hash. Refining that step into four commits:

**7a.3a** — `PropsStore` + mall key + `props/` staging prefix + the two staging tests updated.
No compiler change; nothing renders yet. (This is the commit `tests/test_asset_staging.py:103`
turns over in.)

**7a.3b** — `PropDescriptor` + `an/props/{schema,factory,cli}.py` + `an prop new/validate` +
the CLI dispatch namespace. Still nothing renders.

**7a.3c** — the rig-builder extraction (`_build_rig_subtree`), `_svg_asset_src(prefix=)`,
`_part_probe(prefix=)`. **Character output asserted byte-identical** (§4.2). No prop path yet
— this commit is the one that must prove it changed nothing.

**7a.3d** — the prop branch in `_build_scene_root`, `_swap_vocabulary` widened, `AssetRef.stage`,
`validate.py:113` gains `"prop"`, `validate_semantic(available_props=)` threaded through
`orchestrate.validate_project` (`an/orchestrate.py:65-69`), the four inverted tests, the
`iterate.py` prompt + its new guard, `.claude/skills/an/SKILL.md:53`,
`misc/docs/architecture_as_built.md:391`, `an/credits.py:110-116`, `misc/CHANGELOG.md`, a demo.

**7b** — attachment (`stage.parent`, the node→entity map replacing `_track_root_of` at the swap
site), the `prop_swap` corpus scene + goldens + the offline golden test + the guard mutants
(§4.1). Attachment lands **after** T2's single-walk `_build_scene_root` refactor, since it
needs the same interleave.

**Deferred, deliberately, with reasons recorded:** nine-slice props (Wave 9 per the epic;
needs a `VisualJSON.kind` member and its own sizing branch — §2.7); raster plates as prop art
(T5 §4.3: the cross-arch verdict covers the **SVG** path only, so a raster prop gets its own
scene and its own cross-arch run before anyone calls it a gate); a hand bone (§2.9, a Wave 4
rig-contract change).

---

## 4. Verification

### 4.1 The prop golden

**Fixture: `misc/bench/corpus/prop_swap/`** — under `misc/bench/corpus/`, not `examples/`, for
the mechanical reason `an/bench/corpus.py:132-142` gives: `.gitignore` excludes every
`examples/*/assets/`, and a corpus scene must hold still. One shot; one prop with a two-key
state set; one `set` action swapping it at mid-shot; a plain background so nothing else moves.

```python
"prop_swap": Fixture(
    path=f"{CORPUS_DIRNAME}/prop_swap",
    expect_visual_kinds=frozenset({"svg_sprite"}),
    golden_frames=(t_before, t_after),
    golden_note="the chest's `state` swap: CLOSED at <t0>, OPEN at <t1>. The two "
                "frames straddle the set; N pixels differ, all inside the lid box.",
),
```

(`Fixture` at `an/bench/corpus.py:110-129`; the registry is `DFLT_FIXTURES` at `:187`.)

Five mechanics that decide whether this fixture is a gate or decoration:

1. **The two pinned frames must straddle the swap.** `bless_scene` refuses any pixel-identical
   pair (`an/bench/golden.py:469-479`, per T5 §1.3), and it refuses a blank reason, an unknown
   Chromium build, and fewer than two frames. `promote_demo`'s frame 0 and its `duration/2`
   frame differ by **exactly zero** pixels (`corpus.py:210-215`), which is the recorded reason
   this rule exists.
2. **`expect_visual_kinds` is a floor, not an equality** — `assert_render_path` tests subset
   (`corpus.py:373-383`), so `{"svg_sprite"}` does not forbid the backdrop's `rect`.
3. **A new corpus scene is free at the comparer.** `compare` iterates
   `sorted(names_b & names_a)` (`an/bench/compare.py:686-693`), so a scene the older row never
   had is simply not compared (T5 §1.2 fact 1).
4. **Every declared metric must be emitted for this scene** — the ledger builder errors in
   *both* directions on a declared metric a row omits and a row key the registry does not
   declare (`an/bench/ledger.py:165-186`). So the new fixture needs no new metric, but every
   existing metric must run on it or report `unavailable(detail)`.
5. **No new metric is proposed.** A state swap is a *pixel* change that
   `min_ssim_win8_vs_golden` and `golden_identity` already witness (family B,
   `registry.py:1017-1037`, `:1259-1307`). T5 §1.5's rule applies: family B has one witness by
   its own declaration, and a boolean change detector is not it. If a prop-specific number is
   ever wanted, shape it on `expression_min_pairwise_changed_px` (`registry.py:1081-1135`,
   `run.py:551-582`) — `role="diagnostic"`, `Optimum(kind="guard")`, counts zero under every
   lever.

**The offline companion**, mirroring `tests/test_expression_goldens.py:24-26`'s floor-at-half-
the-first-measurement convention: read the two committed `prop_swap` goldens back, decode, and
assert (a) the changed-pixel count clears a floor set to half the first bless's measurement,
and (b) the change is confined to the lid's bounding box (the shape of
`test_expression_goldens.py:58-68`). That test runs on a clean checkout with no browser; the
ledger row is the live-render view of the same quantity.

**Two guard mutants** in `an/bench/mutants.py` (`Mutant` at `:55-72`, `MUTANTS` at `:74`; the
module's own rule is *"Add one whenever you add a guard. If it survives, the guard is
decoration"*):

```python
Mutant(name="prop_swap_projection_dropped",
       file="an/adapters/cutout/compile.py",
       old="<the `if projected: visual.asset_sets = projected` line>",   # compile.py:1354-1355
       new="<the same line, never assigning>",
       caught_by="tests/test_props.py",
       why="the prop renders, at its default attachment, forever. The picture is "
           "valid and wrong, and only a two-frame comparison can tell."),
Mutant(name="prop_emitted_before_environment",
       file="an/adapters/cutout/compile.py",
       old="<the ordered-walk emission line keyed on stage.after>",
       new="<emit every prop first>",
       caught_by="tests/test_props.py",
       why="a foreground prop silently falls behind the backdrop — a z-order bug "
           "that no per-pixel metric names and that renders without error."),
```

**Standing honesty rule** (CLAUDE.md, `an-dev-bench` SKILL): never write that a rendering
behaviour is "verified in CI". The browser lane runs on demand or on a PR carrying
`run-browser-tests`, added with
`gh api -X POST repos/thorwhalen/an/issues/<N>/labels -f 'labels[]=run-browser-tests'` —
**not** `gh pr edit --add-label`, which exits 0 and applies nothing. Every prop PR can change a
pixel, so every prop PR gets the label.

### 4.2 Byte-identity for prop-less scenes

T5 §3.2 scores props **"YES — free"**, and the reasoning is airtight *as long as the code path
is genuinely new*: `compile.py:717-724` raises today, so no corpus scene can contain a prop,
so a prop branch is reached only by scenes that declare one.

**The guard already exists.** `tests/test_expression_compose.py:134-162`
(`test_every_corpus_contract_hash_equals_the_committed_ledger_row`) re-compiles every corpus
fixture and asserts its hash equals the newest clean ledger row's, with `assert checked >= 7`
(`:162`). Wave 6 exempted **only** its own new scene (`NEW_IN_WAVE_6 = {"expressions"}`,
`:30-31`). Wave 7 does the same and no more: `NEW_IN_WAVE_7 = {"prop_swap", …}`.

**Four ways this wave could break it, each with its mitigation:**

1. **The rig-builder extraction (7a.3c) changes character output.** The refactor touches the
   most-exercised function in the compiler. Mitigation: land 7a.3c **alone**, with the
   contract-hash test green and **no exemption set at all**. If it needs an exemption, it is
   not a refactor.
2. **A new field on `VisualJSON` / `NodeJSON` / `TransformJSON` with a serializing default.**
   `to_dict` is `model_dump(mode="json")` with no None pruning (`serialize.py:321-323`), so any
   such field lands in **every node of every document**. `VisualJSON.fit`
   (`serialize.py:105-111`) is the counter-example in the tree: additive in *semantics*,
   unconditional in *serialization*. Mitigation: §3.4 needs none — verify with
   `git diff an/adapters/cutout/serialize.py` being empty in 7a.
3. **The `_build_scene_root` ordered walk changes child-array order.** T2 §3.3 states the
   condition: with `characters_after=None` (and, here, `stage=None`) every plane is emitted
   first and the child array is identical to today's. Mitigation: the same contract-hash test,
   plus a direct assertion that a two-character + one-environment shot produces the identical
   `children` list before and after.
4. **`_swap_vocabulary` widening changes a character's `declared` / `entity_scale`.** It
   populates `entity_scale[entity.id] = SCENE_PX_PER_VIEW_BOX / view_box_height`
   (`compile.py:495`), which the play and expression paths multiply by
   (`compile.py:1809`, `:2632`). Mitigation: the widening is a lookup-table change with the
   character row unchanged; assert `vocab.entity_scale` equality on a corpus shot.

Also free by construction, worth stating so nobody adds an omit-serializer it does not need:
**`AssetRef.stage` never reaches the hash** — `scene_contract_sha256` hashes the compiled
cutout document (`an/bench/contract.py:53-71`), and `AssetRef` is IR. And `scene.md` is
byte-unchanged because the writer uses `exclude_none=True` (`sync.py:417-421`).

### 4.3 `prop` turns from "raises" into "renders"

**In `validate.py`, in the same PR that stops the compiler raising** — T5 §2.3's consequence 2,
restated because it is the one that silently breaks the agree-with-the-pipeline rule:

- `_DRAWABLE_ENTITY_KINDS` (`validate.py:113`) gains `"prop"`. If this lands *after* the
  compiler change, validate errors on something the compiler now draws; if it lands *before*,
  validate passes a scene that raises. **Same commit.**
- `validate_semantic` gains `available_props=` (signature at `validate.py:391-396`), mirroring
  `available_characters`'s ref-resolution check (`:443-453`), and
  `orchestrate.validate_project` must pass `project.mall.get("props")`
  (`an/orchestrate.py:65-69`). **Pin that the CLI passes it by test**, because the function's
  own docstring warns that a `None` store makes the check *silently skip*
  (`validate.py:399-405`) — the exact failure it warns about is "forgot to thread it from the
  CLI". (T2 §2.3 needs the identical treatment for `available_environments`; do the two
  together, one signature change.)
- `tests/test_loud_discards.py:604-612` — move the `prop` row out of `_UNRENDERABLE_SHOTS`
  (`:608-609`) and add its inverse: a prop shot that **compiles**, and on which
  `validate_semantic` reports no error.

**In `iterate.py`**, `:138-140` becomes:

```
      - entities: list of {kind, id, store, ref, [stage], ...}
        "kind" MUST be one of: character, environment, prop, voice, style.
        A prop is a non-character drawable from the props store. Place it with
        "stage": {"at": [x, y], "scale": s, "depth": d} — depth 1.0 is the
        character plane, 0.0 is frozen to the frame, >1.0 is foreground.
        A prop's state changes are `set` actions naming a swap set its
        descriptor declares, exactly as for a character.
```

and **gets a guard it does not have today** — `test_the_iterate_prompt_enumerates_the_legal_properties`
(`tests/test_loud_discards.py:523-542`) covers properties only. Add a sibling that asserts
(a) the kind enumeration names `prop`, and (b) the string `"Do not emit one"` is **gone** — the
second half matters, because a prompt that both permits and forbids a construct is worse than
either.

**In the docs, same pass** (the repo's own rule: *"an error that contradicts the docs is worse
than no error"*, `tests/test_loud_discards.py:549`):

| file:line | change |
|---|---|
| `.claude/skills/an/SKILL.md:53` | replace the "do not put one in a scene" bullet with the prop usage |
| `misc/docs/architecture_as_built.md:391` | four IR-level refusals become three |
| `an/credits.py:110-116` | props now carry provenance; rewrite the sentence rather than leaving a false completeness claim |
| `tests/test_loud_discards.py:547-568` | the `prop` clause of the skill guard **inverts** — follow the recorded `play`/an#7 precedent at `:551-554` |
| `an/stores/environments.py:1` | drop "and prop bundles" — props have their own store now |
| `misc/CHANGELOG.md` | one line under today's date (CLAUDE.md, per-PR housekeeping) |
| `misc/demos/build_demos.py` | a demo clip — CLAUDE.md: *"A new user-facing capability gets a demo."* Offline and free: the placeholder prop from `an prop new` with no `--art`. |
| `.claude/skills/an-dev-*` | a `prop` section, or a new `an-dev-props` skill, per CLAUDE.md's per-PR rule |

---

## 5. Risks and unknowns

1. **The rig-builder extraction is the real risk in this brief.**
   `_build_svg_character_subtree` (`compile.py:1212-1375`) is the most-exercised function in
   the compiler and carries at least four hard-won invariants — the slot-qualified alias space
   (`:1268-1272`, "the old `{entity}.{name}` alias space was silently first-wins on cross-slot
   collision"), the register-only-what-resolves rule (`:1274-1288`), the nested-versus-rooted
   bone offset (`:1310-1319`, "five face parts share one `head` bone, so the bone alone would
   stack them"), and the per-slot projection (`:1335-1355`). Parameterising it is the only way
   to avoid a second copy, and a second copy is worse — but the extraction must land alone,
   with the contract-hash test green and no exemption.
2. **`_swap_vocabulary`'s widening could leak the face machinery onto props.** Today props are
   skipped by the face solver only because of `entity.kind != "character"` guards at
   `compile.py:2051` and `:2508`. Those are *separate* gates from `:461`, so widening `:461` is
   safe — but nothing states the invariant, and a future tidy-up that "unifies the kind checks"
   would put blinks on a lamp. Add a test that says it: a prop with slots named `left_eye` /
   `right_eye` gets **no** blink clips.
3. **A prop named like a character's node is an unguarded collision.** Node paths are
   `<entity_id>/<slot>` and `vocab.paths` is a flat frozenset (field at `compile.py:387`, built at `:501`). Two entities
   with the same `id`, or a prop whose id collides with a character's, produce ambiguous
   targets. `_check_swap_action`'s "not a node in the built scene" error (`:1936-1940`) catches
   the miss, not the collision. Validate duplicate entity ids in a shot — one loop, and it is
   missing today for characters too.
4. **`REQUIRED_PARTS`-style completeness has no analogue for props, and that is correct but
   costs a safety net.** A character with no head is caught by `validate_character`
   (`validate.py:216-222`); a prop with a typo'd `path` is caught only by
   `_record_missing_parts` + `strict_assets` (`compile.py:1133-1187`, `:510-548`) — which is
   the right mechanism, but it means `an prop validate` must actually check the files, or the
   first sign of trouble is a render.
5. **Attachment's `_track_root_of` coupling (§3.5) is a design conclusion, not a measurement.**
   I read the call chain (`compile.py:1893` → `:1904` → `:1922`) and reasoned the failure; I did
   not build an attached prop and observe the error. **UNVERIFIED as an executed failure,
   confident as a mechanism.** Build the failing case first in 7b.
6. **The tween-rest trap (§3.6) is pre-existing and unmeasured in the corpus.** I did not `rg`
   the corpus scenes for a `from`-less tween on a placed character's `x`. If one exists, the
   §3.6 option-1 warning is a behaviour change on a committed fixture; check before writing it.
7. **`PIXI.NineSlicePlane`'s default border of `10`** is from the API reference via a search
   summary, not from the pinned 7.4.2 source. **UNVERIFIED**; read
   `packages/mesh-extras/src/NineSlicePlane.ts` at the tag before relying on it in Wave 9.
8. **Moho's and Live2D's exact wording** (§2.2, §2.5) came back as fetch summaries rather than
   quoted blocks. The taxonomies are right; do not quote those two as verbatim in a PR body.
9. **Raster prop art is outside the measured perimeter.** T5 §4 establishes that a PNG/JPEG
   takes a different Chromium parser (`loadTextures` → `createImageBitmap` with
   `premultiplyAlpha: "premultiply"` and `colorSpaceConversion` left at the
   implementation-defined `"default"`), sized by a worker pool from
   `navigator.hardwareConcurrency`, and that **none** of that is in `anDeterminismReport`
   (`runtime.js:715-728`) or the ledger's environment block. Prop art in the corpus stays SVG
   in Wave 7.
10. **The mall doctest and every `build_project_mall` caller.** `an/stores/__init__.py:13-17`
    asserts the eleven keys; adding a twelfth is a one-line doctest edit, but `rg
    build_project_mall` across `an/`, `tests/`, `examples/` and `misc/` before assuming it is
    the only place a key count is written down.
11. **`an prop new`'s placeholder art must be deterministic.** The bench renders with
    `strict_assets=True` (`corpus.py:40-44`) and goldens compare decoded pixels
    (`golden.py:10-14`); a placeholder generated with any randomness would make
    `an prop new` unusable in a fixture. Seed it from the name, as
    `an character new --offline`'s geometric fallback does.

---

## Sources

**Code, read directly (this repo, HEAD `9aa35f8`).**
`an/ir/schema.py`, `an/ir/validate.py`, `an/ir/sync.py`, `an/ir/assets.py`, `an/ir/migrate.py`;
`an/adapters/cutout/compile.py`, `serialize.py`, `render.py`;
`an/characters/{schema,play,promote,validate,cli}.py`, `an/characters/svg_utils.py`;
`an/stores/{__init__,_common,characters,environments}.py`;
`an/bench/{corpus,contract,compare,golden,ledger,registry,mutants,run}.py`;
`an/credits.py`, `an/iterate.py`, `an/orchestrate.py`, `an/tools.py`, `an/__main__.py`,
`an/base.py`;
`an/data/cutout_runtime/runtime.js`, `an/data/cutout_runtime/vendor/pixi.min.js` (pixi.js@7.4.2,
MIT — digest pinned at `tests/test_vendored_engine.py:13-14`);
`tests/test_loud_discards.py`, `tests/test_asset_staging.py`, `tests/test_expression_compose.py`,
`tests/test_expression_goldens.py`, `tests/test_iterate.py`, `tests/test_vendored_engine.py`;
`.claude/skills/an/SKILL.md`, `.claude/skills/an-dev-swap-channels/SKILL.md`;
`misc/docs/architecture_as_built.md`, `misc/docs/wave6_research.md` §14.

**Sibling briefs, honoured.** `w7_T5.md` §1 (the instrument), §2.3 (the validate/compile
agreement), §3 (contract-hash strategy — props row, the sub-PR order), §4 (raster and
determinism), §5 (levers). `w7_T2.md` §1.5 (the two-loop z-order), §1.7 (raster textures, three
answers), §1.8 (`strict_assets` and the per-part precedent), §2 (the systems survey this one
does not duplicate), §3.1–§3.3 (`EnvironmentDescriptor`, `depth`/`parallax_factor`,
`characters_after`, the byte-identity recipe).

**External, fetched 2026-08-25.**
- Spine — *Bones* <https://en.esotericsoftware.com/spine-bones>; *Attachments*
  <https://en.esotericsoftware.com/spine-attachments>; *Skins*
  <https://en.esotericsoftware.com/spine-skins>; *JSON format*
  <https://en.esotericsoftware.com/spine-json-format>.
- Moho — *Layers Window* <https://www.lostmarble.com/moho/manual/layerwnd.html>;
  manual index <https://www.lostmarble.com/moho/manual/>.
- Toon Boom Harmony 22 Premium — *Adding Pegs*
  <https://docs.toonboom.com/help/harmony-22/premium/rigging/add-peg.html>; *About Peg
  Hierarchy Rigs*
  <https://docs.toonboom.com/help/harmony-22/premium/rigging/about-peg-hierarchy-rig.html>;
  *About the Layer/Column* <https://docs.toonboom.com/help/harmony-22/premium/layers/about-layer-column.html>.
- Godot 4 — *Sprite2D* <https://docs.godotengine.org/en/stable/classes/class_sprite2d.html>;
  *Node2D* <https://docs.godotengine.org/en/stable/classes/class_node2d.html>; *Instancing*
  <https://docs.godotengine.org/en/stable/getting_started/step_by_step/instancing.html>.
- Live2D Cubism — *Parts palette* <https://docs.live2d.com/en/cubism-editor-manual/partspalatte/>;
  *About Draw Order* <https://docs.live2d.com/en/cubism-editor-manual/draworder/>.
- Lottie — *Assets* <https://lottiefiles.github.io/lottie-docs/assets/>; *Layers*
  <https://lottiefiles.github.io/lottie-docs/layers/>.
- PixiJS 7.4.2 — `NineSlicePlane.ts` source
  <https://raw.githubusercontent.com/pixijs/pixijs/v7.4.2/packages/mesh-extras/src/NineSlicePlane.ts>;
  API reference <https://api.pixijs.io/@pixi/mesh-extras/PIXI/NineSlicePlane.html>.

**Marked UNVERIFIED above.** Spine's documented recipe for attaching an item to a hand bone
(mechanism certain, page not found); Moho's and Live2D's exact wording (fetch summaries, not
quoted blocks); `NineSlicePlane`'s default border value of 10 (API-reference summary, not the
pinned source); the `_track_root_of` attachment failure as an *executed* failure rather than a
read call chain; whether any corpus scene tweens a placed character's `x` with no `from`.
