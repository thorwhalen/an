# Wave 7 / T3 — StylePack, and the `Shot.style` → `Shot.renderer` rename

**Scope.** Where art direction lives, and the retirement of the word "style" as a renderer
selector. Everything below is cited `file:line` against the tree at
`/Users/thorwhalen/Dropbox/py/proj/t/an` (HEAD `9aa35f8`, branch `wave7-research`, clean).
External sources were fetched on 2026-08-25 and are quoted; anything not fetchable is marked
**UNVERIFIED**. Honours the contract-hash ruling in `w7_T5.md` §3.

---

## 1. What exists today, code-verified

### 1.1 The two meanings of "style"

| meaning | where it lives | who reads it |
|---|---|---|
| **renderer selector** — which backend renders this shot | `Shot.style: StyleName = "cutout"` (`an/ir/schema.py:343`), `Meta.default_style` (`an/ir/schema.py:384`), `StyleName` (`an/base.py:165-170`) | `RendererRegistry.find_for` → `can_render` (`an/adapters/_base.py:136-140`), four backends |
| **art direction** — palette, line weight, fonts | the `styles` store (`an/stores/styles.py`), `AssetRef(kind="style")` (`an/ir/schema.py:105`) | **nobody** |

`an/ir/schema.py:337-341` states the first meaning in prose: *"A shot's `style` selects the
renderer. Every renderer must accept the same Shot fields; renderer-specific options go under
`options`."* `an/adapters/_base.py:5-6` says the same from the other side: *"shot by inspecting
`shot.style` and asking each registered renderer's `can_render`."*

The collision is not merely nominal — it is live in one function. `an/ir/validate.py:111-114`:

```python
#: Entity kinds the cutout renderer draws. `voice` and `style` are legitimately
#: not drawable — they configure the render rather than appearing in it.
_DRAWABLE_ENTITY_KINDS: frozenset[str] = frozenset({"character", "environment"})
_CONFIGURING_ENTITY_KINDS: frozenset[str] = frozenset({"voice", "style"})
```

so `AssetRef(kind="style")` is *validated as legitimate* while `an/adapters/cutout/compile.py:724-726`
skips it with a comment (`"'voice' and 'style' entities are legitimately not drawable: they
configure the render rather than appearing in it"`). Nothing anywhere reads it. A scene
declaring one passes `an validate` clean and renders exactly as if it had not. Its only
appearance in the tree is a test fixture asserting the pre-flight accepts it
(`tests/test_loud_discards.py:666`).

### 1.2 The styles store: verified to have no reader

`an/stores/styles.py` is nine lines — a docstring (*"visual style presets (color palette, line
weight, fonts)"*) and `class StylesStore(JsonDirStore)`. It is wired into the mall at
`an/stores/__init__.py:86` and its directory created at `an/stores/__init__.py:71`.

**Nothing reads it.** `rg '\["styles"\]'` over `an/`, `tests/`, `examples/`, `misc/demos/`
returns zero content reads; the only non-wiring hits are:

- `an/adapters/cutout/render.py:562-566` — `ASSET_SRC_PREFIX_TO_STORE` maps `"styles/"` →
  `"styles"`, with the comment at `render.py:558-561`: *"Only `characters/` is emitted by the
  compiler today; the other two are here because environments, styles and props all route
  through this same staging step as they land."* An aspiration, not a reader.
- `an/credits.py:113` — *"Only the characters store carries provenance today. Environments,
  styles and …"*
- `tests/test_stores.py:112`, `tests/test_project.py:24` — assert the key/dir exist.

`an/project.py:30-41` seeds `an.toml` with `default_style = "cutout"`. Nothing parses `an.toml`
back — `rg 'an\.toml'` in `an/` hits only `project.py:6` (a docstring) and `project.py:89-90`
(the write). It is a write-only file.

### 1.3 Every hard-coded colour and line weight

**Compiler — `an/adapters/cutout/compile.py`.**

| what | line | value |
|---|---|---|
| `_CHARACTER_PALETTES` — 5 × (skin, clothing, hair) | `124-131` | `#f4c89a/#3a6ea5/#3b2a1a`, `#d8a47f/#a83249/#1a1a1a`, `#fbe1c1/#2e7d4f/#a8743f`, `#a87a5d/#5b3a8a/#2a2a2a`, `#e8c39e/#d97706/#5e3a1f` |
| `_palette_for(entity_id)` — the selector | `133-136` | `sum(ord(c) for c in entity_id) % 5` |
| `part_color` map (the per-entity helper's consumer) | `937-944` | head→skin, torso/left_arm/right_arm→clothing, **left_leg/right_leg→`#2c3e50` literal** |
| part fallback colour | `960` | `#cccccc` |
| hair band | `982` | `hair` |
| eyebrows (procedural rig) | `996` | `hair` |
| eye pupil | `1007` | `#1a1a1a` |
| mouth node colour (**inert** — see §1.4) | `1020` | `#552222` |
| `_ENV_PRESETS` — 5 backdrops | `732-738` | `default #cfe9ff/#7cba6f`, `park #a5d8ff/#7cba6f`, `indoor #f4e8c8/#a07a4a`, `night #1a2540/#2c3e50`, `sunset #f4a261/#5b4b32` |
| sky/ground rects, `huge = 4000.0` | `809-831` | reads `preset["sky_color"]` / `["ground_color"]` / `["ground_y"]` |
| `background` compile arg | `558` | `"#ffffff"` |

`background` is **unreachable from the IR**: neither call site passes it
(`an/adapters/cutout/render.py:306-314`, `an/preview.py:180-187`), and `Meta` has no background
field. It is `#ffffff` in every render this repo has ever produced. It reaches the compiled
document at `an/adapters/cutout/serialize.py:263` and the runtime at
`an/data/cutout_runtime/runtime.js:588`.

Line weight in the compiler: there is none. The only geometric "weight" constants are
`SCENE_PX_PER_VIEW_BOX = 345.0` (`compile.py:1046`) and `CONTAIN_FIT = "contain"`
(`compile.py:1050`), both scale policy, not stroke.

**Runtime — `an/data/cutout_runtime/runtime.js`.**

| what | line | value |
|---|---|---|
| `makeRect` / `makeEllipse` fallback | `230`, `243` | `'#888888'` |
| eye sclera fill | `259` | `0xffffff` |
| **eye outline — the one line weight in the runtime** | `260` | `g.lineStyle(0.6, 0x222222, 0.6)` |
| eye pupil fallback | `265` | `'#1a1a1a'` (overridden by `visual.color`) |
| `parseColor` non-string fallback | `272` | `0x888888` |
| mouth colours | `348-351` | `_LIP_COLOR 0x6b2b2b`, `_MOUTH_FILL 0x2a1010`, `_TEETH_COLOR 0xfafafa`, `_TONGUE_COLOR 0xb04848` |
| **mouth lip stroke** | `377` | `g.lineStyle(1.0, _LIP_COLOR, 1.0)` |
| canvas background | `588` | `parseColor(meta.background \|\| '#ffffff')` |

`tint` appears **zero times** in `runtime.js`, and `applyProperty` throws on any property
outside its list (`runtime.js:485-491`). There is no runtime recolouring hook of any kind.

**Factory — `an/characters/factory.py`.** This is where colours become SVG *bytes*.

| what | line |
|---|---|
| `_SKIN_TONES` — 8 hexes | `276-285` |
| `_HAIR_TONES` — 6 hexes | `288-295` |
| `_fallback_face_svg` — writes `fill="{skin}"`, `fill="{hair}"` into the head SVG | `298-314` |
| `EYE_CANVAS/EYE_CENTRE/EYE_RX,EYE_RY/PUPIL_R` | `319-323` |
| `_synthesize_eye_open` — `stroke="#222" stroke-width="2"`, `fill="#ffffff"`, pupil `fill="#1a1a1a"` | `338-350` |
| `_synthesize_eye_closed` — lid `fill={skin}`, lash `stroke="#222" stroke-width="3"` | `353-366` |
| `_synthesize_sclera` `fill="#ffffff"` / `_synthesize_pupil` `fill="#1a1a1a"` | `369-380` |
| **`_skin_fill_of`** — regex-reads a colour back *out* of the head SVG | `383-395` |
| `_palette_for_seed` — a **second, different** 5-entry palette table | `541-556` |
| `_write_torso_part` — `fill={clothing} stroke="#222" stroke-width="6"`, collar `stroke-width="6"` | `571-584` |
| `_write_arm_part` — `fill={color} stroke="#222" stroke-width="4"`, hand `fill="#f1c9a5"` | `587-600` |
| `_write_leg_part` — `fill={color}`, shoe `fill="#1a1a1a"` | `603-615` |
| `_synthesize_brow` — `stroke="#3a2a20" stroke-width="6"` | `618-630` |
| legs hard-wired at the call site: `color="#3a3a4a"` | `195-196` |

Two facts worth stating plainly:

1. **The palette tables are duplicated and they disagree.** `compile.py:124-131` and
   `factory.py:546-552` are both "5 × (skin, clothing, hair)", both selected by a stable hash,
   and entries 4–5 differ (`#a87a5d/#5b3a8a/#2a2a2a` and `#e8c39e/#d97706/#5e3a1f` vs
   `#e8c39e/#d97706/#5e3a1f` and `#f1c9a5/#7a8fb5/#3a2a20`). The selectors differ too
   (`sum(ord(c))` vs `_stable_hash`). So a character built by the factory and a character
   drawn by the procedural fallback get colours from two different tables under two different
   hashes. Nothing pins them together.
2. **Stroke weight lives only in generated SVG strings** — `6` on the torso, `4` on the arm,
   `6` on the brow, `2`/`3` on the eyes, plus `0.6` and `1.0` in the runtime. There is no
   single line-weight number anywhere.

### 1.4 How the bench derives "the declared colours"

`an/bench/palette.py` is the module a StylePack has to satisfy. Its docstring
(`palette.py:1-29`) is the specification:

> *"So the palette is read out of the artifacts the browser actually loaded — the staged
> `scene.json` and the staged SVG files beside it — never from a re-compile."*

`palette_for_scene(scene_json, *, runtime_dir)` (`palette.py:161-241`) collects from three
sources, counted separately in `palette_sources` (`palette.py:174`):

- **`scene_json`** — `meta.background` (`:178`); `visual.color` for kinds in
  `COLOURED_KINDS = {"rect","ellipse"}` (`palette.py:54`, `:189-191`); the eye node's
  `visual.color` (`:192-194`).
- **`runtime_constants`** — `RUNTIME_EYE_COLOURS = (0xFFFFFF, 0x222222)` (`palette.py:44`) and
  `RUNTIME_MOUTH_COLOURS = (0x6B2B2B, 0x2A1010, 0xFAFAFA, 0xB04848)` (`palette.py:48`), added
  whenever an `eye` or `mouth` node is present (`:195-200`).
- **`svg`** — every `fill`/`stroke`/`stop-color` literal in each **staged** SVG named by a
  referenced texture alias, parsed as XML, `display:none` subtrees excluded, unresolvable
  tokens *returned rather than guessed* (`palette.py:111-158`, `:220-233`).

Three rules it enforces that constrain the pack design:

1. `parse_color` (`palette.py:68-95`) is a **verbatim mirror** of `runtime.js:270-274` —
   `hex.padEnd(6,'0').slice(0,6)`, so `"#222"` is `0x222000`, not `0x222222`.
2. `INERT_COLOUR_KINDS = {"mouth","svg_sprite","sprite"}` (`palette.py:57`) — the mouth node's
   own colour is deliberately **not** read (`:197-200`), because `drawMouthShape` never reads
   it; every `svg_sprite` carries the `#888888` schema default (`serialize.py:118`).
3. `runtime_literal_colours` (`palette.py:250-268`) cross-checks the two runtime constant
   tuples against `runtime.js` itself, *"so adding a fifth mouth colour reddens a test instead
   of silently inflating `off_palette_pixel_fraction`"*.

`palette_hex` is a **`SCENE_KEYS` member** (`an/bench/compare.py:62-70`): a row whose palette
differs is refused for that whole scene, before any metric family is examined.

### 1.5 How `iterate.py` names styles

`an/iterate.py:132-135`, inside `_SYSTEM_PROMPT` — prose handed to a model, not code:

```
  - meta: {title, author, duration, fps, resolution, default_style, notes}
  - timeline: a list of shots, each with:
      - id (string, unique)
      - style ("cutout" | "manim" | "motion_graphics" | "whiteboard")
```

and `an/iterate.py:139`: *`"kind" MUST be one of: character, environment, voice, style.`* —
i.e. the grammar actively instructs a model to emit the inert `kind="style"` entity.
`tests/test_iterate.py` has no assertion over either list.

### 1.6 Three findings that change the plan

**(a) `SUPPORTED_STYLES` is dead.** Declared at `an/base.py:172-177`, exported at
`an/__init__.py:18,69`, and `rg SUPPORTED_STYLES` finds **no other hit in the tree**. The
`Literal` at `an/base.py:165-170` is what actually constrains the field.

**(b) `SceneIR.assets` is dead.** `an/ir/schema.py:421` declares `assets: list[AssetRef]`.
`an/ir/sync.py` contains zero `assets` hits — the markdown writer never emits it, the reader
never parses it — and no consumer reads `scene.assets`. (The `.assets` hits in
`an/adapters/cutout/fidelity.py:130` are `CutoutSceneJSON.assets`, a different model.)

**(c) `migrate()` never runs on a SceneIR.** This is the load-bearing one.
`ScenesStore.__getitem__` is `SceneIR.model_validate(json.loads(...))` with no migration
(`an/stores/scenes.py:43-48`). `sync()`'s two JSON-read branches are the same
(`an/ir/sync.py:558-559`, `:572-573`). `an.project.load` calls `sync_files` then reads the
store (`an/project.py:114-125`). Every `migrate(...)` call in the tree passes
`kind="CharacterDescriptor"` (`an/ir/validate.py:173,268,321`; `an/characters/cli.py:138`;
`an/characters/validate.py:205`; `an/characters/factory.py:450`). **A registered SceneIR
migration is currently unreachable code.** The only registered one is the identity
(`an/ir/migrate.py:121-124`).

Combined with `_IRModel`'s `extra="allow"` (`an/ir/schema.py:58`), the consequence is exact: a
renamed field does not error on an old document — it is accepted as an *extra* and the new
field silently takes its default.

---

## 2. Survey — fetched and quoted

### 2.1 W3C Design Tokens Community Group format

Fetched `https://www.designtokens.org/TR/drafts/format/` (2026-08-25; the older
`tr.designtokens.org/format/` 301s here).

> *"a design token is information associated with a human readable name, at minimum a
> name/value pair"* … tokens represent *"design decisions in a platform-agnostic way so that
> they can be shared across different disciplines, tools, and technologies."*

Required: `$value` plus the name (the parent object key). Optional: `$type`, `$description`
(*"A plain text description explaining the token's purpose"*), `$extensions` (reverse-domain
vendor metadata), `$deprecated`.

Aliases, two syntaxes: **curly-brace** `{group.token}`, which *"always resolves to the target
token's `$value` property"*; and **JSON Pointer** `"$ref": "#/path/to/target"` per RFC 6901,
for property-level access. Groups are *"objects without `$value` properties"*; they may carry
`$type` which child tokens inherit. Names cannot start with `$` and cannot contain `{`, `}` or
`.`; files are `.tokens` / `.tokens.json`, MIME `application/design-tokens+json`.

The rule that matters most here:

> *"tools must not attempt to guess the type of a token by inspecting the contents of its
> value."*

Composite types are enumerated: shadow, border (`color, width, style`), transition, typography,
gradient, **strokeStyle** (*"String value (solid, dashed, etc.) or object with dashArray and
lineCap"*).

Fetched `https://www.designtokens.org/TR/drafts/color/`: the colour `$value` is **an object,
not a hex string** —

```json
{ "$type": "color",
  "$value": { "colorSpace": "srgb", "components": [1, 0, 1], "alpha": 1, "hex": "#ff00ff" } }
```

with `colorSpace` and `components` required, `alpha` defaulting to 1, and `hex` explicitly
*"optional and serves as a fallback value only — it is not the primary representation format."*

### 2.2 Style Dictionary

Fetched `https://styledictionary.com/info/architecture/` and `/info/tokens/`. Style Dictionary
is *"a configuration-based framework that serves as a single source of truth for design
tokens"*, with a nine-step build: parse config → locate token files by glob → parse → **deep
merge into a single unified token object** → preprocess → transform (*"applying all defined
transforms to tokens containing a `value` key"*) → **resolve references** (*"Aliases (appearing
as `"{size.font.base}"`) are replaced with their actual transformed values"*) → format output
→ execute actions. Token definition: *"Any node in the object that has a `value` attribute on
it is a design token."* References are *"the dot-notation object path (the fully articulated
design token name) in curly brackets."* The docs do **not** formally name a primitive-vs-semantic
two-tier model; aliasing merely enables it.

### 2.3 CSS custom properties

Fetched MDN *Using CSS custom properties*. Declaration is `--name: value` (or `@property` with
`syntax`, `inherits`, `initial-value`); use is `var(--name, fallback)`, nestable
(`var(--a, var(--b, pink))`). Two-dash properties **always inherit**; the documented pattern is
*"Define custom properties globally at `:root` for reuse across the entire document, then
override them in specific scopes."* An invalid substitution falls back to the initial or
inherited value — a silent degradation, not an error.

### 2.4 Lottie slots and dotLottie theming

Fetched `https://lottie.github.io/lottie-spec/1.0/specs/helpers/`:

> *"Slots are a way to define a property value once and use the value in multiple properties.
> Slot definitions are in a dictionary, the slot definition key is the key that is used to
> match all properties with a `sid` field to the same key for replacement."*

A slot carries its value in `p`; a slottable object/property carries `sid` — *"Identifier to
look up the slot."* Replacement is by key match into the dictionary.

The dotLottie theming workflow (LottieFiles developer docs; the direct page redirected and was
read via search-result excerpts — **partially UNVERIFIED**): expose a property by adding a
`sid` *"at the same JSON depth as that property"*, then write a separate JSON theme file
naming each slot id and its new value; a slot is *"a named, typed placeholder that a designer
deliberately exposes"* with a type among color, scalar, vector, gradient, text, image.

**The structural lesson for us:** Lottie's theming works because the *artist* tagged the fill.
Theming is opt-in per property, declared in the art, not inferred from it.

### 2.5 Rive data binding

Fetched `https://rive.app/docs/editor/data-binding/overview`. *"View Models define the
structure of your data"*, organised *"independently from your scene hierarchy"*; Color is
named as a property type, and the docs reference *"binding the color of multiple icons to a
single color property."* The runtime page `rive.app/docs/runtimes/data-binding` returned **404**
and the per-runtime API names could not be confirmed — **UNVERIFIED** beyond the above.
Same lesson as Lottie: the binding is authored *into the artwork* in the editor.

### 2.6 Manim configuration

Fetched `https://docs.manim.community/en/stable/guides/configuration.html`. A global `config`
object (`ManimConfig`) is *"the single source of truth for all configuration options"*,
addressable as `config.background_color = WHITE` or `config["background_color"] = WHITE`, and
internally consistent (*"changing `frame_y_radius` automatically updates `frame_height`"*).
Precedence, lowest to highest: **library defaults → user-wide `~/.config/manim/manim.cfg` →
folder-wide `manim.cfg` → CLI flags → programmatic changes**. Files begin with `[CLI]`.
Manim has a *cascade*, not a *pack*: there is one config with `background_color` in it, no
named art-direction document.

### 2.7 Krita `.kpl` palettes

Fetched `https://docs.krita.org/en/untranslatable_pages/kpl_defintion.html`:

> *"KPL files are zip files containing the following files: mimetype, colorset.xml,
> profiles.xml, A number of icc files."* — mimetype being `application/x-krita-palette`.

`colorset.xml`: *"The top level element is a `Colorset` element, it's children can either be
`ColorSetEntry` elements, or `Group` elements"*; `Colorset` carries `name, comment, columns,
rows, readonly, version`; *"`Group` elements can only have `ColorSetEntry`s as children"*
(attrs `name, rows`); `ColorSetEntry` carries `name, id, bitdepth, spot` plus a `Position`
(`row`, `column`) and a colour-space element (Gray/sRGB/RGB/XYZ/CMYK/Lab/YCrCb). ICC profiles
are *embedded* in the zip *"to ensure compatibility over different computers"*.

The relevant design point: a Krita palette is a **flat named list with groups and per-swatch
`id`s** — no roles, no semantics. It is the primitive tier only.

### 2.8 The shared palette with per-character overrides (Toon Boom, Moho)

Toon Boom Harmony, `docs.toonboom.com/help/harmony-22/premium/colour/about-palette.html`:
*"Harmony uses palettes to hold all the colours needed to paint your elements, allowing complete
control and consistency in the painting process"*; *"The colour swatch has a unique ID number
that associates it with the painted zones"*; and — the whole point — modifying a swatch
*"automatically updates all the zones painted with this swatch throughout the entire scene."*

The Colour-Override node
(`docs.toonboom.com/help/harmony-22/premium/reference/node/filter/colour-override-node.html`)
lets you *"change colours from the palette without affecting the actual palette"*, with three
operation classes: whole-palette override (swap clone palettes — day/night), individual colour
override (a value or a bitmap texture), and render-selected-colours-only. Search results
(**partially UNVERIFIED**, from `clone-palette.html` excerpts) add the layering rule: *"When a
palette and its clone are present in the same scene, Harmony uses the palette that is highest
in the list"*, and *"A character usually has only one master palette"*, cloned and tweaked for
different lighting.

Moho, `lostmarble.com/moho/manual/stylewnd.html`: *"A style is the same set of information used
by a shape (color, line width, line brush, effects, etc.), but it doesn't actually appear on its
own in your animation"*; it is applied to one or more shapes; and per-shape escape: *"Turn on
the checkbox to the left of the color swatch to override the style's color."*

**The convergent shape across all four systems** — Harmony, Moho, Lottie, Rive — is the same:

1. Art is painted with a **reference (a colour ID / a style / a slot id)**, never a literal.
2. A **named document** maps references to values.
3. Overriding is a **layered lookup**, resolved at render, that never mutates the art.

And in all four the *artist* creates the reference. None of them infers a role from a pixel.

### 2.9 What a "style pack" is in practice for a cartoon

Assembling the above into the concrete shape (this synthesis is mine; the ingredients are cited
above):

- **Palette roles** — a small named set the art refers to: `skin`, `hair`, `clothing`,
  `clothing_accent`, `legs`, `line`, `eye_pupil`, `sky`, `ground`, `background`. This is
  Harmony's colour-ID layer plus DTCG's semantic-alias layer.
- **Line** — weight and colour, one number and one hex; Moho puts *line width* in the style
  alongside fill, and DTCG has `strokeStyle` as a composite type.
- **Corner radius / shading on-off** — expressible, but `an` has no rounded-rect or shading
  primitive today (`VisualJSON.kind` is `sprite|rect|ellipse|mouth|eye|svg_sprite`,
  `serialize.py:100`), so declaring them would be declaring the unreachable. Out of v1.
- **A name** — DTCG `$description`, Krita's `Colorset name`/`comment`.

---

## 3. The `StylePack` document

### 3.1 Shape

A new schema-versioned document kind, living in the `styles` store, giving that store its
first reader.

```python
# an/style/schema.py  (new module; owns the kind, registers it on import)
STYLE_PACK_SCHEMA_VERSION = "0.1.0"

STYLE_PACK_KIND: DocumentKind = register_kind(
    DocumentKind("StylePack", "schema_version", STYLE_PACK_SCHEMA_VERSION)
)

class Roles(BaseModel):                      # extra="allow"
    skin: str | None = None
    hair: str | None = None
    clothing: str | None = None
    clothing_accent: str | None = None
    legs: str | None = None
    eye_pupil: str | None = None
    line: str | None = None                  # see §3.3 for what it can reach
    sky: str | None = None
    ground: str | None = None

class Line(BaseModel):                       # extra="allow"
    width: float | None = None

class StylePack(BaseModel):                  # extra="allow"
    schema_version: str = STYLE_PACK_SCHEMA_VERSION
    kind: Literal["StylePack"] = "StylePack"
    name: str = ""
    description: str = ""
    background: str | None = None            # the canvas colour, unreachable today
    roles: Roles = Field(default_factory=Roles)
    line: Line = Field(default_factory=Line)
    characters: dict[str, Roles] = Field(default_factory=dict)   # per-entity override
```

Four deliberate choices, each with its reason:

- **`extra="allow"`** on every model, matching `_IRModel` (`an/ir/schema.py:54-58`) and the
  roadmap's forward-compatible-reads rule.
- **`DocumentKind`-registered**, per `an/ir/migrate.py:21-26` (*"each package registers its own
  kind, so that this module never has to import the packages it serves"*) and the
  `CHARACTER_DOCUMENT_KIND` precedent (`an/characters/schema.py:62-67`). Its own
  `schema_version` field, its own ladder — and it must be imported from `an/ir/__init__.py`
  the way the character schema is (`an/ir/__init__.py:92-94`), or
  `tests/test_migrate.py:140-164`'s fresh-interpreter assertion is a lie for the third kind.
- **Hex strings, not DTCG colour objects.** DTCG says the object is primary and hex is *"a
  fallback value only"* (§2.1). We deliberately do not adopt it: `an/bench/palette.py:68-95`
  mirrors `runtime.js:270-274` **verbatim** by design, and the runtime's rule is
  `parseInt(hex.padEnd(6,'0').slice(0,6), 16)` — nothing else. A `colorSpace: "srgb"` field
  would be a claim nothing honours, and a `components` array would need a second conversion
  whose result `palette.py` would then have to mirror too, doubling the surface where a
  divergence is silent. One hex string, one parse rule, one mirror.
- **`characters: dict[str, Roles]` keyed by `AssetRef.id`** — the Harmony/Moho override shape
  (§2.8), the highest tier of a two-tier lookup.

### 3.2 Declaration and resolution

```python
# an/ir/schema.py
class Meta(_IRModel):
    style_pack: str | None = None      # a key in the styles store; None = built-ins
class Shot(_IRModel):
    style_pack: str | None = None      # per-shot override; None = inherit

def resolve_style_pack(shot: Shot, scene_style_pack: str | None) -> str | None: ...
```

`resolve_style_pack` is the **one** statement of the shot-over-scene rule, exactly mirroring
`resolve_step_hz` (`an/ir/schema.py:355-369`) — which exists because the an#89 review found
three copies of that rule. Sync carries it as one whitelist line each side, the shape
`step_hz` already proved (`an/ir/sync.py:135-138` reader, `:407-409` writer).

**Not** `AssetRef(kind="style")`, and **not** `Shot.options`, for concrete reasons:

- `SceneIR.assets` is neither written nor parsed by sync (§1.6b), so the scene-level route
  would require building an entities block at scene level that has never existed; the per-shot
  route would put a non-drawable in `entities` beside drawables, which is the branch
  `compile.py:724-726` already has to special-case.
- `Shot.options` is untyped `dict[str, Any]` (`an/ir/schema.py:348`), so `an validate` could
  not pre-flight a pack key that names nothing in the store — and pre-flighting what the
  pipeline will refuse is exactly `_check_renderable`'s stated job (`an/ir/validate.py:326-338`).

Compile-time resolution — three tiers, highest wins:

```
pack.characters[entity.id].<role>   →   pack.roles.<role>   →   the built-in default
```

where "the built-in default" is *literally what the code does today*: `_palette_for(entity.id)`
(`compile.py:133-136`) for a character, `_ENV_PRESETS[ref]` (`compile.py:732-738`) for a
backdrop, `#ffffff` for the canvas. **The deterministic palette-by-id stays.** It is not a
legacy path to be retired: an asset-less project must render, which is the same principle that
keeps the placeholder rig (`compile.py:922-931`) and the environment default
(`compile.py:749-753`). What changes is that both become the *bottom* of a lookup instead of
the only thing there is.

### 3.3 The hard question: does a pack recolour SVG art?

**Ruling: no. v1 recolours only what the compiler emits — the procedural rig, the backdrop
rects, and the canvas. SVG art is reached by the pack at *authoring* time, through the factory,
never at compile time.**

Four reasons, all code-backed.

**(1) Compile-time recolouring breaks content addressing and the asset ledger.** A texture's
`src` is a path into the characters store (`_svg_asset_src`, `compile.py:1053-1055`) and
staging is a straight `shutil.copy2(source, target)` (`render.py:637-639`). Recolouring means
the staged bytes are no longer the store's bytes, so `src` names a file that does not exist at
that path — you must fabricate a src, and then `palette_for_scene`'s SVG half, which reads
`runtime_dir / src` (`palette.py:226-232`), is reading a derived file whose provenance nothing
records. `AssetResolutionJSON` (`compile.py`, read by the golden bless) exists precisely to
record *which art actually rendered*; a silent recolour is the exact fact it was built to
surface.

**(2) String substitution over SVG is not a recolour, it is a coin flip.** The only precedent
in the tree is `_skin_fill_of` (`factory.py:383-395`), a regex over
`<(?:circle|ellipse)[^>]*fill="(#[0-9a-fA-F]{6})"` whose docstring scopes it to *"every rig this
factory synthesizes"*. `an/bench/palette.py:111-158` had to parse SVG as **XML** — handling
`style="fill:#abc"`, `stop-color`, and `display:none` subtrees — and to *return* unresolvable
tokens rather than guess, because *"guessing puts a wrong colour in the palette, and the metric
then reads low with no error anywhere"* (`palette.py:119-121`). A substitution pass would miss
the same spellings, and here a miss is not a wrong number: it is a character rendered
half-recoloured, with nothing red.

**(3) There is no role tagging in the art, and it cannot be inferred.** To recolour you must
know which fill is "skin". `CharacterDescriptor` carries no such field. Inferring it is exactly
what `_skin_fill_of` does, and the an#99 review recorded what that costs
(`factory.py:483-490`): *"a rig built with `--seed` ≠ name got a lid of another tone"*. The
honest version of "the pack recolours the art" is **the art declares role-tagged fills** —
which is what Lottie's `sid` and Harmony's colour ID actually are (§2.4, §2.8), and which is a
descriptor-schema plus named-layer-import problem, i.e. Wave 9, not Wave 7.

**(4) There is no runtime escape hatch.** `tint` occurs zero times in `runtime.js`;
`applyProperty` throws on any unlisted property (`runtime.js:485-491`). Even if it existed,
tint is multiplicative and cannot take `#3a6ea5` to `#a83249`.

**Where a pack *does* reach SVG art: the factory, at authoring time.** `an character new
--style-pack <id>` reads the pack and passes its roles into the synthesizers that already take
colour arguments — `_write_torso_part(clothing=, accent=)` (`factory.py:571`),
`_write_arm_part(color=)` (`:587`), `_write_leg_part(color=)` (`:603`, today hard-wired to
`#3a3a4a` at `:195-196`), `_synthesize_brow` (`:618`, today hard-wired `#3a2a20`),
`_fallback_face_svg` (`:298`), and the eye stack's `fill=skin` (`:504`). This is a real, small
change: the factory already *has* the seam, it just resolves it from a private table
(`_palette_for_seed`, `:541-556`) instead of from a document. Doing this also closes §1.3's
finding (a) — one table instead of two disagreeing ones.

**The consequence must be audible, not silent.** A scene that declares a pack and whose
characters are SVG-rigged gets *some* of the pack (backdrop, canvas, and nothing else). That
is exactly the "I set a pack and nothing changed" failure this repo keeps filing. So the
compiler emits a warning naming each entity whose art the pack could not reach — the
`CutoutCompileWarning` shape already used at `compile.py:543-548` and `:775-802`, whose own
comment says the point is that the keys *"still do nothing, which is the part worth saying."*

**Roles deliberately NOT declared in v1**, because the pack cannot reach them and *a role a
pack names but cannot change is worse than a role it does not name*: `eye_sclera` and the eye
outline (`runtime.js:259-260`), `lip`, `mouth_fill`, `teeth`, `tongue` (`runtime.js:348-351`).
All six are runtime literals; `palette.py:44-48` records them as `runtime_constants` for exactly
this reason. `eye_pupil` **is** in, because it is a real `visual.color` on the eye node
(`compile.py:1007`) that the runtime reads (`runtime.js:265`). `line.width` is in but reaches
**only the factory-synthesized SVG stroke widths** — it does not reach `runtime.js:260`/`:377`;
say so in its docstring. The guard: a test asserting no `Roles` field name maps to a colour in
`runtime_literal_colours(runtime.js)` (`palette.py:250-268`).

### 3.4 Byte-identity when no pack is declared

The T5 §3 requirement, discharged by three mechanical rules:

1. **No new field on `VisualJSON`.** `to_dict` is `model_dump(mode="json")` with no None
   pruning (`serialize.py:321-323`), so a new field lands on every visual in every document —
   the `fit` counter-example (`serialize.py:105-111`), which serialises everywhere because it
   has no omit serializer. The pack changes the *value* of `visual.color`, never the schema.
2. **The pack's identity is stamped with omit-if-unset.**
   `CutoutSceneMetaJSON.style_pack: str | None = None`, popped by extending the existing
   `_omit_unset_step_hz` wrap serializer (`serialize.py:284-290`) — same trick, same recorded
   reason: *"the compiled document is the bench's scene contract (`scene_contract_sha256`), so
   a `null` here would move every committed row's hash for a knob nobody turned"*
   (`serialize.py:275-279`). Rename the method to `_omit_unset_policy_knobs` while there; it
   already handles two fields.
3. **The no-pack branch returns the identical literals.** The pack layer is a *lookup with a
   default*, not a rewrite: `_palette_for` and `_ENV_PRESETS` stay in place and are consulted
   unchanged when the pack is `None` or leaves a role `None`. This is what makes the acceptance
   test (§5.4) a byte-comparison rather than a tolerance.

Note what a *declared* pack does, deliberately and correctly: it moves `visual.color` values,
hence `scene_contract_sha256` **and** `palette_hex`, both `SCENE_KEYS` (`compare.py:62-70`), so
`bench-compare` refuses that scene against older rows. That is right — a scene rendered under a
different palette is not comparable to one rendered without it, and it is why the pack fixtures
are **new corpus scenes** (§5.1) rather than a mutation lever on an existing one. Same verdict
`step_hz` got, for the same mechanical reason.

---

## 4. The rename: `Shot.style` → `Shot.renderer`

### 4.1 The one-sentence test

After 7b, the bare word **`style`** never appears in the IR. It is either **`renderer`** (which
backend draws this) or **`style_pack`** (which art direction). That is the acceptance criterion
for the rename, and it is checkable as a schema-shape assertion rather than a grep (§4.5, R3).

### 4.2 Every enumeration site

Counts: **28** occurrences under `an/`, **132** under `tests/`, **19** files under
`misc/bench/corpus/` + `examples/`, plus 3 docs.

**Schema / types**
- `an/base.py:164-170` — `StyleName` → `RendererName`. Comment `#: The renderer-style of a
  shot` becomes accurate.
- `an/base.py:172-177` — `SUPPORTED_STYLES`: **delete**, do not rename (zero consumers, §1.6a).
  Drop from `an/__init__.py:18,69`.
- `an/ir/schema.py:343` — `Shot.style` → `Shot.renderer`; docstring `:337-341`.
- `an/ir/schema.py:384` — `Meta.default_style` → `Meta.default_renderer`.
- `an/ir/schema.py:105` — `AssetRef.kind` literal: drop `"style"` (§4.4).
- `an/ir/schema.py:21,27` — the module doctest.

**Sync — parser *and* writer, and the heading**
- `an/ir/sync.py:33` — docstring *"A shot heading is `## Shot <id> (<style>)`"*.
- `an/ir/sync.py:56-58` — `_SHOT_HEADING_RE`: **no change needed.** The regex captures
  group(2) as an unnamed parenthesised token, so `## Shot s1 (cutout)` parses unchanged. Only
  the local variable names (`:164`, `:167`) and the kwarg at `:123` move.
- `an/ir/sync.py:123` — `"style": style or meta.default_style` → `"renderer": renderer or
  meta.default_renderer`.
- `an/ir/sync.py:394` — writer meta key `default_style` → `default_renderer`.
- `an/ir/sync.py:406` — `f"## Shot {shot.id} ({shot.style})"` → `({shot.renderer})`. The
  emitted text is unchanged for every scene that renders cutout.

**Validate**
- `an/ir/validate.py:111-114` — `_CONFIGURING_ENTITY_KINDS` loses `"style"`; the comment loses
  the word.
- `an/ir/validate.py:346,359` — prose only.

**Dispatch**
- `an/render.py:3-4` (docstring), `:251` (`f"(style={shot.style!r})"` in the `RenderError`).
- `an/adapters/_base.py:5-6` (docstring), `:99` `supported_styles` — **delete it from the
  Protocol.** It is redundant with `Renderer.name` on all four backends
  (`CutoutRenderer.name == "cutout"` and `supported_styles == ("cutout",)`,
  `an/adapters/cutout/render.py:290-291`), and `find_for` only ever calls `can_render`
  (`_base.py:136-140`). Deleting a redundant Protocol member is the simplification the rename
  pays for. Touches `tests/test_protocols.py:32,53` and the doctest at
  `an/adapters/cutout/render.py:286-288`.
- `an/adapters/cutout/render.py:294`, `an/adapters/manim_adapter.py:34,41`,
  `an/adapters/remotion_adapter.py:25`, `an/adapters/whiteboard.py:23` — `shot.style ==` →
  `shot.renderer ==`.
- `an/preview.py:170-173`.

**Grammar handed to a model**
- `an/iterate.py:132` (`default_style` in the meta list), `:135` (the four-value enum),
  `:139` (`kind` MUST be one of … `style`).

**Project template**
- `an/project.py:33` — `default_style = "cutout"` in `_ANIMA_TOML_TEMPLATE`. Write-only
  (§1.2), so no migration; but leaving it makes the file lie. Rename.

**Corpus + examples (19 files).** `default_style:` in eight `scene.md` (six corpus + walk_demo,
promote_demo, park_bench_cartoon, single_character, character_gallery/cartoon),
`"default_style"` in five committed `ir/scene.json`, `default_style =` in two `an.toml`,
`default_style: cutout` in the demo template (`misc/demos/build_demos.py:92`). The `(cutout)`
headings need no edit.

**Docs / skills.** `README.md:68`; `misc/docs/architecture_as_built.md:172,310,313`;
`.claude/skills/an/SKILL.md:50`. Also `misc/docs/report 0` (a research doc — leave; it is a
dated artefact).

**Outside the repo — verified.** `muvid` is the only federation package that drives `an`'s
scene format (`t/muvid/muvid/renderers/animation.py:30,72,137`). It emits `## Shot {id}
(cutout)` and **no `default_style` key** (`animation.py:128-137`), so the rename requires
**zero muvid changes** — the heading token is positional. `reelee`, `illustration` and `mixing`
mention `an` only in docs or unrelated code.

### 4.3 What `an sync` does with an old `scene.md` — and the defect this exposes

- **The heading is safe.** Positional capture (§4.2), so `## Shot s1 (cutout)` keeps working.
- **`default_style:` in the ```` ```yaml meta ```` block is NOT safe.** It is fed to
  `Meta(**meta_data)` (`an/ir/sync.py:113`), and `_IRModel` is `extra="allow"`
  (`an/ir/schema.py:58`) — so the old key is accepted as an *extra field* and
  `default_renderer` silently takes `"cutout"`. Today every shipped scene says `cutout`, so the
  observable damage is zero; the *mechanism* is a silent drop, and `an/ir/sync.py:135-137`
  already names it: *"this reader enumerates shot keys, so a field added to `Shot` that is not
  named here silently drops on read."*
- **And the registered migration would not run at all** (§1.6c). Nothing in
  `ScenesStore.__getitem__`, `sync()`, or `project.load()` calls `migrate()` on a SceneIR.

So the rename PR must do three things beyond renaming:

1. **Wire the migration into the read path** — `an/stores/scenes.py:48` and `an/ir/sync.py:559`
   and `:573` become `SceneIR.model_validate(migrate(json.loads(...), kind="SceneIR"))`.
   This is a prerequisite, not a nicety: without it the migration is decoration.
2. **Make the retired markdown key raise, not drop.** `markdown_to_ir` parses a meta block into
   kwargs, not a document, so no document migration can reach it. `Meta(**meta_data)` gets a
   pre-check: `default_style` present → raise, naming `default_renderer`. Likewise a `style:`
   key inside a ```` ```yaml shot ```` block. A raise is correct here because `extra="allow"`
   makes the only alternative a silent wrong picture — the same judgement `runtime.js:353-361`
   records for unknown mouth codes.
3. **Register one migration, `SceneIR 0.1.0 → 0.2.0`**, bumping `SCHEMA_VERSION` and
   `COMPATIBLE_VERSION` (`an/base.py:20,23`) per the standing rule (*"Never bump
   SCHEMA_VERSION without registering a migration in `an/ir/migrate.py`"*), in the
   `_character_0_1_0_to_0_2_0` mould (`an/characters/schema.py:396-450`) — with a doctest in
   the migration body, and popping rather than copying the old key, for the reason that
   migration's own docstring gives: *"leaving it would let a stale map sit beside the live one
   indefinitely, and every descriptor model sets `extra="allow"`, so nothing would ever
   complain."*

### 4.4 `AssetRef(kind="style")` — retire it in the same migration

It has zero writers, zero readers, and zero effect (§1.1). Deleting a `Literal` member is
formally a breaking change (a stored document carrying it fails `AssetRef` validation —
`extra="allow"` does not rescue a bad `Literal` value), so it must ride a migration; but no
shipped project can contain one (`rg 'kind: style'` over `examples/` and `misc/bench/corpus/`
returns nothing), so the migration body is a drop with a doctest.

Wave 4's ruling applies — *"otherwise the descriptor schema breaks twice and ships two
registered migrations where one would do"* — so this goes in **7b-1's** migration, not its own.
`Meta.style_pack` / `Shot.style_pack` then arrive in 7b-2 as **optional fields with `None`
defaults, which are additive and need no migration at all** (the rule artful states explicitly
and this repo's descriptor ladder follows). One bump, one migration, one PR.

No aliases, no `style` property forwarding to `renderer`, no deprecation shim — the federation's
standing directive, and it is affordable here precisely because the heading survives untouched.

### 4.5 Risk list

| | risk | mitigation |
|---|---|---|
| **R1** | The migration is unreachable today, so a "registered migration" is decoration and the rename lands as a silent semantic change on any project whose `default_style` was not `cutout`. | Wire `migrate()` into all three read sites (§4.3.1) and pin it with a test that writes an old `ir/scene.json` to a temp project and asserts `an.project.load(...).scene.timeline[0].renderer == "manim"`. **That test fails on today's tree** — which is what makes it the important one. Mutation-test the wiring. |
| **R2** | `extra="allow"` turns every missed call site into a silent default rather than an error. | Assert the *absence*: `"style" not in Shot.model_fields`, `"default_style" not in Meta.model_fields`, and a round-trip test that an old-key document raises rather than validating. A schema-shape test in the `artful/tests/test_body_schema_stability.py` mould. |
| **R3** | 132 test occurrences + 28 source ones; `rg -w style` is a weak gate — Wave 4 measured that `rg -n '_SVG_.*_SIZE\|head_y = '` matched 2 of 4 sites and passing it did **not** prove the constants were gone. | The strong gate is the model-fields assertion (R2), not the grep. Keep the grep as the *weaker half*, the way `an`'s browser-gate guard is structured. |
| **R4** | `an.toml`'s `default_style` is write-only, so renaming it is untested by construction and not renaming it leaves a lying file. | Rename it; add the one assertion that `init()` writes `default_renderer`. No migration (nothing parses it). |
| **R5** | Deleting `supported_styles` from the `Renderer` Protocol is a public-API break. | Zero in-tree implementations outside `an/adapters/`; `muvid` calls `orchestrate`, not the registry (`animation.py:30`). Flag it in the PR body as breaking, per the directive. |
| **R6** | Rewriting `default_style:` in eight corpus/example `scene.md` files could move the ledger baseline. | It cannot: `scene_contract_sha256` hashes the **compiled** document (`an/bench/contract.py:53-71`), and `default_renderer` is not in it. **Assert it** — all eight fixtures' hashes equal their committed ledger row (`misc/bench/ledger/2026-08-24-dc406f5.json`) before and after. Same acceptance PR-C used. Also rewrite the five committed `ir/scene.json`. |
| **R7** | `iterate.py:132-139` is behaviour-driving prose with no test; a model told `style` and `kind: style` will emit both after the rename. reelee learned this the expensive way (#293: *"a call-site sweep cannot see prose that a model then dispatches"*). | Add a test asserting the prompt names `renderer`/`default_renderer`, does **not** contain `default_style`, and lists exactly `RendererName.__args__` and exactly `AssetRef.model_fields["kind"]`'s literal members — derived from the types, not typed out. |
| **R8** | Two agents in `compile.py` at once: 7a rewrites `_build_environment_subtree` (`:740-832`) and `_ENV_PRESETS` (`:732-738`), which is exactly what 7b-2 makes pack-driven. | Sequencing (§4.6); one agent per file. |

### 4.6 Sequencing recommendation for 7a / 7b

**Rename first, before both.** The epic already says *"early in 7b while the rename budget is
free"*; the sharper reason is that the rename is the **only** Wave 7 change that is provably
pixel- and contract-neutral, so it can land while the ledger baseline is still the pre-Wave-7
one and every corpus hash can be asserted equal. Every `Shot` field 7a adds (planes, a
translating camera) is another line in sync's whitelist and another enumeration site that would
otherwise be touched twice.

```
7b-1  rename + one SceneIR 0.1.0→0.2.0 migration + migration wired into the read path
      + kind="style" retired + supported_styles deleted.   Contract-neutral: all 8
      corpus hashes asserted unchanged.
7a    planes, parallax, translating camera.  Moves hashes for every scene with an
      environment; new goldens, deliberate re-bless.
7b-2  StylePack: the document, the store reader, the compiler lookup, the factory
      consumer.  Additive (optional fields, no migration).  Contract-neutral when
      no pack is declared; two NEW corpus scenes carry the packed case.
```

7a and 7b-2 both touch `compile.py`'s environment code, so they must not run concurrently. If
7a slips, 7b-2 is not blocked — it only needs `_ENV_PRESETS`' *colours*, which 7a keeps.

---

## 5. Verification

### 5.1 The StylePack golden

Goldens are keyed `misc/bench/golden/<scene>/<frame-key>-chromium<build>.png`
(`an/bench/paths.py:65`, `an/bench/golden.py:16-22`) — **one set per scene**, so "the same
scene under two packs" needs two scene names. Two new `Fixture` entries
(`an/bench/corpus.py:112-133`), same `scene.md` body, differing only in `style_pack:`:

```
misc/bench/corpus/style_pack_warm/    style_pack: warm
misc/bench/corpus/style_pack_cool/    style_pack: cool
```

Both must be **procedural-rig** scenes — `expect_visual_kinds=frozenset({"rect","ellipse"})`,
the `single_character` shape (`corpus.py:188-191`). This is not a convenience: of the eight
fixtures, `single_character` and `dialogue` are procedural and the other six are `svg_sprite`
(`corpus.py:203-296`), and per §3.3 a pack reaches only what the compiler emits. Putting the
StylePack golden on an `svg_sprite` fixture would assert a capability the ruling says we do not
have.

`bless_scene` refuses a pixel-identical *pair within a scene* (`golden.py:469-479`); nothing
compares across scenes, so the cross-pack difference needs its own assertion: decode both
scenes' goldens at the same frame key (`golden.py:10-14` — sha256 of the **decoded RGB array**,
never file bytes) and require `≥ N` differing pixels.

**N is unmeasured and must not be invented.** Measure it once — the pack changes flat fills over
the drawn character area plus the backdrop, so the count is essentially the drawn area — and
pin the measured floor with margin, recording the measurement. This is Decision 6's standing
principle (*"a value nobody measured is not a value"*, and `aa_probe` *"honestly refuses"*).

**And a cheaper gate that runs in ordinary CI**, since nothing renders a pixel on an unlabelled
PR (`misc/docs/adr_ci_verification_perimeter.md`): assert at the **compiler** level that the two
compiled documents differ in `visual.color` at ≥ k nodes and that their `palette_hex` sets are
disjoint on the role colours. The pixel test rides the `run-browser-tests` label; the contract
test is unconditional. Say which is which — never write that the pixel behaviour is "verified
in CI".

### 5.2 The bench palette derivation reads the pack

The design constraint is that **`an/bench/palette.py` needs no change**. Since a pack only moves
`visual.color` on `rect`/`ellipse`/`eye` nodes and `meta.background`, `palette_for_scene`
(`palette.py:161-241`) picks it up through the paths it already has (`:178`, `:189-194`).

Tests:

1. Compile a scene under a pack; assert every declared role colour is in `palette_hex` and the
   corresponding `_CHARACTER_PALETTES` entry (`compile.py:124-131`) is **not**.
2. Assert `palette_sources["scene_json"]` accounts for the pack's colours and
   `palette_sources["runtime_constants"]` is unchanged (`palette.py:174`) — this is what proves
   the pack reached the *document*, not that a colour coincidentally matched.
3. **The negative that enforces §3.3's role exclusions**: no `Roles` field name corresponds to a
   colour returned by `runtime_literal_colours(an/data/cutout_runtime/runtime.js)`
   (`palette.py:250-268`). A pack that names `lip` would fail this — which is the point.
4. Extend `tests/test_bench_palette.py`'s existing swap-alias pin (`palette.py:205-210` cites
   it) with a packed compiled scene, so a pack whose colours stop flowing into the derivation
   reddens a test rather than silently under-collecting.

### 5.3 Migration round-trip tests

In `tests/test_migrate.py`'s mould (its `registry_sandbox` fixture, `:29-38`, restores both
registries):

- A doctest **on the migration function itself** (the `_character_0_1_0_to_0_2_0` precedent,
  `an/characters/schema.py:404-415`): old doc in → `renderer` present, `style` absent,
  `default_renderer` present, `default_style` absent, `kind="style"` entity dropped, version
  `0.2.0`.
- `test_a_migration_for_one_kind_never_runs_against_another` (`:86-108`) extended to the third
  kind once `StylePack` registers — all three sit at a `0.x.0` and the an#77 conflation is
  exactly what a third kind re-opens.
- `test_both_shipped_kinds_are_registered` (`:140-164`) becomes three kinds, still asserted in a
  **fresh interpreter** (`:146-149`) — that is why it subprocesses.
- `test_the_target_defaults_to_the_kinds_own_current_version` (`:129-137`) gains the StylePack
  row.
- **The reachability test** (R1): an old `ir/scene.json` on disk → `an.project.load` →
  `renderer` correct. Plus the same through `ScenesStore["main"]` directly, since `sync()` and
  the store are two independent read paths.

### 5.4 The identity test

The Waves 5–6 acceptance, restated for 7b-2:

For all eight fixtures (`corpus.py:188-296`) plus the four `examples/` projects,
`to_dict(compile_shot(...))` is **byte-identical** before and after the StylePack PR when no
pack is declared, and `scene_contract_sha256` (`an/bench/contract.py:53-71`) equals the
committed ledger row. Assert the **hash**, not "the tests pass": T5 §1.2 — a moved contract hash
refuses the whole scene at `compare.py:339-359` before a single metric family is examined.

Plus the serializer guard, mutation-tested: `"style_pack" not in to_dict(scene)["meta"]` when
unset, and deleting the pop from `_omit_unset_step_hz` (`serialize.py:284-290`) must turn it
red. `step_hz` and `gaze_seeds` already have this shape; a third field joining them without a
test is how the `fit` counter-example happened.

And for 7b-1, the rename's own identity claim: the eight corpus hashes are unchanged, asserted
against the ledger row, because the rename touches no compiled field.

---

## 6. Risks and unknowns

1. **N in "differ by ≥ N px" is unmeasured** (§5.1). It must be measured, not chosen. The
   ordering constraint: the two pack fixtures must exist and render before the number exists,
   so the PR that adds them cannot also assert the floor — split, or land the fixtures with a
   `>0` placeholder and tighten in the same PR after measurement, recording the measured value.
2. **Whether `ground_y` belongs in a pack.** Deliberately excluded from v1 (§3.1): it is a
   layout number that 7a's multiplane work will replace, and a pack owning it would need
   migrating when planes land. If 7a keeps `ground_y`, revisit — but only after 7a lands.
3. **Whether `_ENV_PRESETS` should survive at all.** Five colour-only presets
   (`compile.py:732-738`) that a pack subsumes. Recommendation is to keep them as the no-pack
   fallback, for the same reason `_palette_for` stays — but that is a judgement, not a
   measurement, and it deserves one line in the PR body rather than silence.
4. **A third `DocumentKind` re-opens the an#77 surface.** Three kinds all at `0.x.0`, and
   `migrate`'s greedy chain (`an/ir/migrate.py:154-168`) picks *any* registered step from the
   source version. The kind key defends this, and `test_a_migration_for_one_kind_never_runs_against_another`
   pins it — but it currently pins **two** kinds, and a test that pins two while three exist is
   the shape of the original bug.
5. **The two palette tables (§1.3a) disagree and nothing pins them.** Folding the factory onto
   the pack (§3.3) closes it, but a character built *before* that change keeps its
   `_palette_for_seed` colours while the compiler's fallback would give it `_palette_for`
   colours — they are different tables. Nobody has measured whether any committed corpus rig is
   affected; the SVG-rigged fixtures never reach `_palette_for` at all, so the exposure is
   probably nil, but "probably" is not a measurement.
6. **`line.width` is half-reachable** (§3.3). It reaches factory-generated SVG strokes and not
   `runtime.js:260`/`:377`. Declaring it is defensible only because it *does* change something
   real; the docstring must say what it does not change, or it becomes the thing §3.3 argues
   against.
7. **Rive's runtime colour-binding API is UNVERIFIED** — `rive.app/docs/runtimes/data-binding`
   404s. The editor-side concept (§2.5) is confirmed; no runtime API name is relied on above.
   The dotLottie theme *file structure* is likewise partially UNVERIFIED (the doc page
   redirected); the Lottie **spec's** slot mechanism (§2.4) is confirmed and is what the
   argument rests on.
8. **Found in passing, outside this topic and worth filing:**
   `t/muvid/muvid/renderers/animation.py:142` emits `camera: { move: static }`, and `static` is
   not in `_CAMERA_MOVES` (`an/adapters/cutout/compile.py:2897-2903`) nor in
   `_RENDERABLE_CAMERA_MOVES` (`an/ir/validate.py:107-109`) — so that path raises
   `CutoutCompileError` at `compile.py:2927-2935`. Unrelated to the rename (which needs zero
   muvid changes), but it is a live cross-package break.

---

## 7. Sources fetched 2026-08-25

- **DTCG format** — https://www.designtokens.org/TR/drafts/format/ (canonical; `tr.designtokens.org/format/` 301s here). **DTCG color type** — https://www.designtokens.org/TR/drafts/color/
- **Style Dictionary** — https://styledictionary.com/info/architecture/ ; https://styledictionary.com/info/tokens/
- **CSS custom properties** — https://developer.mozilla.org/en-US/docs/Web/CSS/CSS_cascading_variables/Using_CSS_custom_properties
- **Lottie slots (spec)** — https://lottie.github.io/lottie-spec/1.0/specs/helpers/
- **dotLottie theming** — https://developers.lottiefiles.com/docs/tools/dotlottie-js/theming/ (redirects to `docs.lottiefiles.com`; read via search excerpts — **partially UNVERIFIED**)
- **Rive data binding (editor)** — https://rive.app/docs/editor/data-binding/overview ; runtime page https://rive.app/docs/runtimes/data-binding **404 — UNVERIFIED**
- **Manim configuration** — https://docs.manim.community/en/stable/guides/configuration.html
- **Krita KPL** — https://docs.krita.org/en/untranslatable_pages/kpl_defintion.html (the `reference_manual/resource_management/resource_palettes.html` path 404s)
- **Toon Boom Harmony** — https://docs.toonboom.com/help/harmony-22/premium/colour/about-palette.html ; https://docs.toonboom.com/help/harmony-22/premium/reference/node/filter/colour-override-node.html ; palette-list ordering from https://docs.toonboom.com/help/harmony-22/premium/colour/clone-palette.html excerpts — **partially UNVERIFIED**
- **Moho Style window** — https://www.lostmarble.com/moho/manual/stylewnd.html
- **UNVERIFIED, not relied on:** the "two-tier primitive/semantic token" framing as a *specified* concept (neither DTCG nor Style Dictionary formalises it; both only enable it via aliasing); any Rive runtime API name; the dotLottie theme-file JSON schema.
