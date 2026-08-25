# Wave 7 research, thread T2 — the environment as multiplane planes, and how a plate becomes one

Measured 2026-08-25 against `main` (an 0.1.53+), before any Wave 7 code. Every code claim
carries `file:line`; every external claim carries a fetched URL and a verbatim quote.
Where this document and the code disagree, the code wins and this document gets fixed.

Scope split, stated so nothing is claimed twice: **T2 owns the environment document and the
plane** — what an environment *is*, what a plane declares, how a plate becomes one, and what
the compiler must emit. **T1 owns the camera** — what a `pan_left` does, how a move becomes
per-plane channels, and the move table. The seam between them is one scalar per plane, and
§3 defines it as an interface only.

**The wave in one paragraph.** Today an environment is two 4000-unit rectangles built from a
five-entry preset table with a three-key override, drawn before every character because
`_build_scene_root` runs environments in a separate first loop. Wave 7 replaces that with a
schema-versioned `EnvironmentDescriptor` of ordered planes, each carrying one depth scalar
that the camera multiplies its pan and scale delta by. The plane's art may be a fill, a
generated gradient, or an **image plate** — and a plate needs no runtime change, because the
vendored PixiJS already loads PNG/JPEG/WebP/AVIF and the asset-staging step already resolves
the `environments/` prefix; the block is entirely on the Python side, where no code path
produces a texture for anything but a character. The acceptance is JSON identity for the five
presets, because two of the seven corpus fixtures declare an environment and
`scene_contract_sha256` hashes the whole staged document.

---

## 1. What exists today (facts, `file:line`)

### 1.1 The environments store

`EnvironmentsStore` is nine lines and no behaviour: `class EnvironmentsStore(JsonSidecarStore)`
with a docstring and nothing else (`an/stores/environments.py:8-9`). It therefore inherits
`META_NAME = "meta.json"` (`an/stores/_common.py:80`) — unlike `CharactersStore`, which
overrides it to `"character.json"` (`an/stores/characters.py:25`).

The store is a `MutableMapping[str, dict]` over a directory per key: reading returns the
parsed `meta.json` (`_common.py:96-100`), writing replaces it with `json.dumps(..., indent=2,
sort_keys=True, default=str)` (`:102-108`), deleting removes the whole directory including
sidecars (`:110-121`), and `sidecar_path(key, name)` (`:91-94`) hands out a path for binary
side files. **Nothing in the compiler ever calls `sidecar_path` for an environment** (`rg
sidecar_path an/` finds only the definition and the characters-side writers).

Mall wiring: `"environments": EnvironmentsStore(pdir / "assets" / "environments")`
(`an/stores/__init__.py:84`), with `build_project_mall(ensure=True)` creating
`assets/environments` (`:70`). The store is one of the eleven asserted keys in the module
doctest (`:13-17`).

**There is not one environment on disk anywhere in this repo.** Every
`examples/*/assets/environments/` and `examples/character_gallery/cartoon/assets/environments/`
is an empty directory (verified by `ls`), and every scene that declares an environment names a
built-in preset. The store has a schema-shaped hole where its documents would be.

### 1.2 What a user-supplied environment can be

Exactly three float/colour scalars, and the compiler says so out loud.
`_build_environment_subtree` copies the preset dict (`an/adapters/cutout/compile.py:758`),
then merges the store document with an intersection filter:

```python
preset.update({k: v for k, v in override.items() if k in preset})   # compile.py:804
```

Anything outside `{sky_color, ground_color, ground_y}` is **read and discarded**, with a
warning that names the multiplane future explicitly (`:795-803`):

> `environment {ref} declares {unknown}, which the cutout renderer does not read (it uses
> {sorted(preset)}), so they have no effect on the render. Layered plates and parallax planes
> are planned; see https://github.com/thorwhalen/an/issues/9`

That warning is a **warning and not an error** deliberately, because the store is a free-form
`meta.json` and `name`/`description`/`tags` are its natural shape (`:790-794`) — a fact the
migration in §3 must not break. The behaviour is pinned three ways in
`tests/test_loud_discards.py`: an unread key warns (`:312`), and the test's own example of an
unread key is **`parallax_layers: 3`** (`:326`); `name` alone also warns (`:337-339`); a known
key still overrides (`:347-354`).

`AssetRef.overrides` exists in the IR (`an/ir/schema.py:109`, documented at `:98-99` as "lets a
single shot tweak presentation without forking the asset") and **is read by nothing** —
`rg '\.overrides' an/` returns only the schema definition. There is already one dead
per-shot override channel; §3 recommends not making it the second.

### 1.3 The built-in presets

`_ENV_PRESETS` (`compile.py:732-738`), five entries, three keys each:

| ref | `sky_color` | `ground_color` | `ground_y` |
|---|---|---|---|
| `default` | `#cfe9ff` | `#7cba6f` | 100.0 |
| `park` | `#a5d8ff` | `#7cba6f` | 110.0 |
| `indoor` | `#f4e8c8` | `#a07a4a` | 120.0 |
| `night` | `#1a2540` | `#2c3e50` | 110.0 |
| `sunset` | `#f4a261` | `#5b4b32` | 110.0 |

Resolution order is preset-then-store, recorded per entity (`:756-783`): `resolved="store"`
when the ref is in the store, `"preset"` when it is a built-in name, `"default"` +
`fallback=True` otherwise. `park` and `default` share a ground colour; `night`, `sunset` and
`park` share a horizon. The table encodes an aerial-perspective intuition (a bluish day sky, a
warm sunset) with no ramp behind it — the brief's §"aerial perspective as a computable ramp"
is aimed at exactly this.

### 1.4 The geometry, and the 4000-unit magic

Two `rect` visuals under one container named after the entity (`:807-833`):

```python
huge = 4000.0                                                        # compile.py:810
NodeJSON(name="sky",    transform=TransformJSON(x=0.0, y=-huge/2 + ground_y),
         visual=VisualJSON(kind="rect", width=huge, height=huge, color=sky_color))
NodeJSON(name="ground", transform=TransformJSON(x=0.0, y= huge/2 + ground_y),
         visual=VisualJSON(kind="rect", width=huge, height=huge, color=ground_color))
```

`makeRect` draws `g.drawRect(-w * ax, -h * ay, w, h)` with `ax = ay = 0.5` by default
(`an/data/cutout_runtime/runtime.js:228-239`), so each rect is centred on its node and the two
bands abut exactly at `y = ground_y`. **The horizon is `ground_y` scene units below the frame
centre**, and nothing else about the two rects is meaningful — they are unbounded by
construction, which is why the comment at `:807-809` reads "so they fill the canvas regardless
of size".

### 1.5 Z-order — and why a foreground plane is not merely missing but unreachable

`_build_scene_root` (`compile.py:672-727`) runs **two separate loops**:

```python
for entity in shot.entities:                      # compile.py:701-707
    if entity.kind == "environment":
        children.append(_build_environment_subtree(...))
for entity in shot.entities:                      # compile.py:708-716
    if entity.kind == "character":
        ...
```

with the comment "Process environments first so they sit BEHIND characters in z-order"
(`:700`). Draw order is child-array order: `buildSceneTree` does `parent.addChild(container)`
(`runtime.js:143`) and nothing sorts. `grep -c "sortableChildren\|zIndex" runtime.js` = **0**,
though both symbols are present in the vendored bundle
(`an/data/cutout_runtime/vendor/pixi.min.js`), so the capability exists and is unused.

The consequence is stronger than "there is no foreground plane": **re-ordering the `entities`
list cannot produce one**, because the two loops separate environments from characters
regardless of authored order. A foreground plane requires editing `_build_scene_root`, and
that is the one structural change 7a cannot avoid.

### 1.6 Environment size vs shot resolution

`compile_shot(width=1920, height=1080)` (`compile.py:556-557`) passes those numbers **only**
into `CutoutSceneMetaJSON` (`:651-652`); `_build_environment_subtree` takes no size argument
at all (`:741-746`). The runtime centres the scene root at `(width/2, height/2)`
(`runtime.js:650-651`), so one scene unit is one output pixel at camera scale 1.

Therefore the backdrop's *framing* is resolution-dependent and nobody declared it. At the
corpus's 320×240 (`misc/bench/corpus/multi_shot/scene.md:8-10`) the visible y-range is
[−120, 120] and `night`'s `ground_y = 110` puts the horizon 10 px above the bottom edge; at the
1920×1080 default (`an/base.py:29`) the same 110 sits just below centre. The two-rect design
hides this because both bands are effectively infinite — a *sized* plate would expose it
immediately, which is why §3 makes plane sizes explicit rather than inheriting a canvas.

### 1.7 Can raster images be textures at all, or only SVG? — three different answers

**The runtime: yes, already, with no change.** `preloadAssets` does `PIXI.Assets.add(alias,
src)` for every `assets.textures` entry and `await PIXI.Assets.load(aliases)`
(`runtime.js:555-574`); the alias list is `.sort()`ed as a stated determinism contract
(`:558-563`). `makeSvgSprite` never inspects the extension — it takes whatever
`PIXI.Assets.get(visualSpec.asset_id)` returns (`:188-192`). The vendored engine is
`pixi.js@7.4.2` (`tests/test_vendored_engine.py:28`), whose `loadTextures` parser declares,
verbatim in `vendor/pixi.min.js`:

```js
const Qf=[".jpeg",".jpg",".png",".webp",".avif"],Jf=["image/jpeg","image/png","image/webp","image/avif"];
```

**The staging step: yes, already, and it is tested.** `ASSET_SRC_PREFIX_TO_STORE`
(`an/adapters/cutout/render.py:561-565`) maps `characters/`, `environments/` and `styles/` to
mall stores; `_stage_scene_assets` (`:568-640`) copies any declared `src` under one of them
into the runtime directory, and warns loudly on every failure mode (no `src`, unknown prefix,
in-memory store, absent file). Its comment says the other two prefixes are there "because
environments, styles and props all route through this same staging step as they land"
(`:558-560`). `tests/test_asset_staging.py:69-86` stages `environments/park/sky.svg` with
`warnings.simplefilter("error")` and asserts it arrives. **This is Wave 1 item 2 already
delivered; §5's done-when "an environment authored with an image plate reaches the screen"
proves it end to end rather than building it.**

**The compiler: no. There is no producer, and three things block it.**

1. `_svg_asset_src(ref, rel_path)` hardcodes the store prefix:
   `return f"characters/{ref}/{rel_path}"` (`compile.py:1053-1055`). It is called from exactly
   two places, both inside the character path (`:1268`, `:1284`, `:1321`).
2. `_register_texture` (`:1058-1066`) is called only from
   `_build_svg_character_subtree._register` (`:1263-1268`). `_build_environment_subtree` is
   never passed the `textures` dict (`:704-706` vs `:712-714`).
3. The size probe is SVG-only. `_part_probe` (`:1069-1108`) calls `raster_size`
   (`an/characters/svg_utils.py:163-190`), which XML-parses the file and reads the root's
   `width`/`height`, falling back to the `viewBox` extent "as a browser does: a header parse,
   not a render" (`:1086-1087`). A PNG makes `_parse` raise, `probe` catches
   `(OSError, ValueError)` and returns `(True, None)` (`compile.py:1101-1106`) — **present but
   unmeasurable**, which is a documented and deliberate state (`:1074-1080`), so a PNG would
   silently fall through to the attachment's declared `width`/`height` or to the runtime's
   `contain` fit. Not a crash; a size the author never declared.

Two adjacent facts. `VisualJSON.kind` is a closed
`Literal["sprite","rect","ellipse","mouth","eye","svg_sprite"]` (`serialize.py:101`) whose
`"sprite"` member is dead — `makeVisual` falls through to `makeRect` for it
(`runtime.js:170-171`), and Wave 8's brief already lists removing it. And the aspect-fidelity
instrument keys on one name: `SPRITE_KIND: str = "svg_sprite"`
(`an/adapters/cutout/fidelity.py:49`).

### 1.8 What `strict_assets` does with missing environment art

Today it cannot fire on art, only on the **ref**, because there is no art.
`_build_environment_subtree` appends one `AssetResolutionJSON` per environment entity
(`:773-783`) with `resolved ∈ {"store","preset","default"}` and `fallback=True` only for
`"default"` (`:766-772`). `_raise_or_warn_on_asset_fallbacks` (`:510-548`) is the **single**
warn-vs-raise decision for the whole compile and runs after action and viseme compilation
(`:644`), so a swap key whose art is missing but never *used* stays a warning. `strict_assets`
threads `an render --strict-assets` → `render_project` → `RenderContext` → `compile_shot`
(`compile.py:589-594`), and the bench pins it on: `BENCH_RENDER_KWARGS = {..., "strict_assets":
True}` "because a stand-in asset renders happily as a DIFFERENT picture (an#33)"
(`an/bench/corpus.py:40-44`).

Pinned by `tests/test_loud_discards.py:879` (unknown ref warns, and raises under strict) and
`:901` (a known preset is **not** a fallback).

**The per-part precedent 7a must mirror.** `_record_missing_parts` (`compile.py:1133-1187`)
writes one `AssetResolutionJSON` per *slot* with `kind="part"` and
`id=f"{entity.id}/{slot_name}"`, and splits two cases that look alike: a slot that drew
nothing is `resolved="missing", fallback=True`; a slot that drew something but is missing one
attachment is `resolved="incomplete", fallback=False`, "because the frame is not wrong, only
the inventory is incomplete" (`:1148-1152`). A plane whose plate is absent is exactly the
first case, and `kind="plane"` / `id=f"{env_id}/{plane_name}"` is the shape.

### 1.9 How `scene.md` declares one

A ` ```yaml entities ` list item, parsed by `_extract_entities_block` (`an/ir/sync.py:230-242`)
straight into `AssetRef(**item)` — no whitelist, so the four `AssetRef` fields plus
`overrides` all round-trip. `AssetRef.kind` is a closed
`Literal["character","environment","voice","style","prop"]` (`an/ir/schema.py:105`). The writer
dumps `model_dump(exclude_none=True, exclude_defaults=False)` (`sync.py:417-424`).

```yaml
- kind: environment
  id: park_bg
  store: environments
  ref: park
```

(`examples/park_bench_cartoon/scene.md:32-35`; documented at
`misc/docs/architecture_as_built.md:322` with the preset list inline.)

`an validate` accepts `environment` as drawable (`_DRAWABLE_ENTITY_KINDS`,
`an/ir/validate.py:113`) and **says nothing about the ref** — an unknown environment ref is a
compile-time warning, never a validate finding. A `prop` entity, by contrast, is accepted by
validate's kind check and then **raises** in the compiler (`compile.py:717-724`).

### 1.10 Who is affected: the two corpus fixtures that declare an environment

| fixture | env ref | file |
|---|---|---|
| `promote_demo` | `park` | `examples/promote_demo/scene.md:26-30` |
| `multi_shot` | `night`, then `sunset` | `misc/bench/corpus/multi_shot/scene.md:21-24`, `:47-50` |

Both are corpus fixtures with committed goldens (`an/bench/corpus.py:203-215`, `:289-298`;
`misc/bench/golden/promote_demo`, `misc/bench/golden/multi_shot`). `multi_shot` declares
`expect_visual_kinds=frozenset({"rect","ellipse"})` (`:291`) — its env rects satisfy `rect`
alongside the procedural rig's. `promote_demo` declares only `{"svg_sprite"}` (`:206`), so its
environment is present but unasserted. Note `assert_render_path` tests **subset**, not equality
(`corpus.py:373-383`), so a new visual kind appearing in a scene does not fail the check — only
the contract hash notices.

---

## 2. Survey — fetched and quoted

Everything in this section was fetched 2026-08-25 by two survey passes; a claim that could not
be fetched is marked **UNVERIFIED** and is not relied on.

### 2.1 Godot — `ParallaxLayer`, `Parallax2D`, and the two encodings of "tile"

`https://docs.godotengine.org/en/stable/classes/class_parallaxlayer.html`

> "**Deprecated:** Use the `Parallax2D` node instead."

> "A `ParallaxLayer` must be the child of a `ParallaxBackground` node. Each `ParallaxLayer` can
> be set to move at different speeds relative to the camera movement or the
> `ParallaxBackground.scroll_offset` value."

> "`Vector2 motion_scale = Vector2(1, 1)` … Multiplies the `ParallaxLayer`'s motion. If an axis
> is set to 0, it will not scroll."

> "`Vector2 motion_mirroring = Vector2(0, 0)` … The interval, in pixels, at which the
> `ParallaxLayer` is drawn repeatedly. Useful for creating an infinitely scrolling background.
> If an axis is set to 0, the `ParallaxLayer` will be drawn only once along that direction."

> "**Note:** If you want the repetition to pixel-perfect match a `Texture2D` displayed by a
> child node, you should account for any scale applied to the texture when defining this
> interval. For example, if you use a child `Sprite2D` scaled to 0.5 to display a 600x600
> texture, and want this sprite to be repeated continuously horizontally, you should set the
> mirroring to `Vector2(300, 0)`."

> "**Note:** If the length of the viewport axis is bigger than twice the repeated axis size, it
> will not repeat infinitely, as the parallax layer only draws 2 instances of the layer at any
> given time. … **Note:** Despite the name, the layer will not be mirrored, it will only be
> repeated."

`https://docs.godotengine.org/en/stable/classes/class_parallaxbackground.html`

> "`Vector2 scroll_base_scale = Vector2(1, 1)` … The base motion scale for all `ParallaxLayer`
> children."

> "`bool scroll_ignore_camera_zoom = false` … If `true`, elements in `ParallaxLayer` child
> aren't affected by the zoom level of the camera."

`https://docs.godotengine.org/en/stable/classes/class_parallax2d.html` — the numeric ladder,
which the deprecated page never states:

> "`Vector2 scroll_scale = Vector2(1, 1)` … Multiplier to the final `Parallax2D`'s offset. Can
> be used to simulate distance from the camera. **For example, a value of 1 scrolls at the same
> speed as the camera. A value greater than 1 scrolls faster, making objects appear closer.
> Less than 1 scrolls slower, making objects appear further, and a value of 0 stops the objects
> completely.**"

> "`Vector2 repeat_size = Vector2(0, 0)` … Repeats the `Texture2D` of each of this node's
> children and offsets them by this value. When scrolling, the node's position loops, giving
> the illusion of an infinite scrolling background if the values are larger than the screen
> size. If an axis is set to 0, the `Texture2D` will not be repeated."

> "`int repeat_times = 1` … Overrides the amount of times the texture repeats. Each texture copy
> spreads evenly from the original by `repeat_size`. Useful for when zooming out with a camera."

`https://docs.godotengine.org/en/stable/tutorials/2d/2d_parallax.html`

> "The scene above is comprised of five layers. Some good `scroll_scale` values might be: (0.7,
> 1) - Forest / (0.5, 1) - Hills / (0.3, 1) - Lower Clouds / (0.2, 1) - Higher Clouds / (0.1, 1)
> - Sky"

> "Keep in mind that some settings like `Parallax2D.repeat_size` and `Sprite2D.region_rect` do
> not take scaling into account, so it's necessary to adjust these values based on the scale."

*Implication.* Godot is the closest fit to what Wave 7 needs and it is a **per-axis
multiplier with the fixed point at 1.0 = camera speed, 0 = frozen**, plus a *pixel interval*
for repetition rather than a boolean. Two things transfer directly: the five-layer example's
values (0.1 … 0.7 for background layers) are an empirical sanity range, and the "does not take
scaling into account" note is a trap the descriptor must resolve once — a repeat period is
authored in **post-scale** units or in **art** units, and picking silently is how a plate
seams.

### 2.2 Unity 2D — ordering as two discrete keys; tiling as "fill this size"

`https://docs.unity3d.com/Manual/class-TagManager.html`

> "Sorting Layers are used in conjunction with Sprite graphics in the 2D system. Sorting refers
> to the overlay order of different Sprites."

`https://docs.unity3d.com/Manual/2DSorting.html` (served as `2d-renderer-sorting.html`)

> "You can use layers to represent different depths because they are separate and Unity renders
> them in order. Use the `Sorting Layer` and `Order in Layer` properties of the renderer
> component of the GameObject."

> "To create sublayers, use the `Order in Layer` property. Unity renders sublayers in numerical
> order, so lower values render behind higher values."

> "Within a sorting layer, a sublayer, and a render queue, Unity determines the order of 2D
> GameObjects by calculating their distance from the camera. … **Orthographic:** Unity uses the
> distance from the camera plane to the center of the GameObject."

`https://docs.unity3d.com/Manual/class-SpriteRenderer.html` (served as
`sprite/renderer/sprite-renderer-reference.html`)

> "`Draw Mode` — Determines how Unity scales the sprite texture. The options are: **Simple:**
> Scales the entire sprite uniformly. This is the default. **Sliced:** Stretches the center and
> edges of the sprite but keeps the corners at their original size. Use this option only if you
> 9-slice the sprite. **Tiled:** Repeats the sprite texture to fill the new dimensions of the
> sprite. Use this option only if you 9-slice the sprite."

> "`Tile Mode` — Sets how Unity repeats, or tiles, the texture across the resized sprite. …
> **Continuous:** Doesn't stretch the texture. The tiles at the edges might use cropped parts of
> the texture. **Adaptive:** Stretches the center of the texture until the width or height
> reaches the Stretch Value, at which point it repeats."

`https://docs.unity3d.com/2022.3/Documentation/Manual/9SliceSprites.html`

> "**First, you need to make sure the Mesh Type is set to Full Rect.** … If the Mesh Type is set
> to Tight, 9-slicing might not work correctly."

> "In Tiled mode, the sprite stays the same size, and does not scale. Instead, the top and
> bottom of the Sprite repeat horizontally, the sides repeat vertically, and the centre of the
> Sprite repeats in a tile formation to fit the Sprite's size."

*Implication.* Unity has no parallax primitive at all — its contribution is the **opposite
tiling encoding** from Godot's: repetition is expressed as a target rectangle the texture fills
(`Size` + mode), never as a period. It is also the precedent for the brief's nine-slice props
(7a), and the Full-Rect prerequisite is the kind of hidden precondition an importer must
enforce rather than discover.

### 2.3 Toon Boom Harmony — depth as physical distance, plus an explicit "keep apparent size"

`https://docs.toonboom.com/help/harmony-22/premium/staging/about-multiplane.html`

> "One of the most exciting features in Harmony is the multiplane or Z-depth. In the multiplane,
> you can create backgrounds in several layers, spread them on the Z-axis to add depth, and then
> move the camera through this environment to create an impressive perspective illusion."

> "**In live action, when the camera moves around in a scene, objects near the camera will appear
> to move by a greater distance than objects far from the camera.** In 2D animation, multiplanes
> can be used to achieve a similar effect without having to use 3D."

> "**Positioning your element closer to the camera makes them appear bigger, and moving them
> further makes them appear smaller.** It is also possible to move elements on the Z-axis without
> affecting their apparent size by using the Maintain Size tool."

> "**NOTE** When creating a multiplane background, it is important to draw each layer fully. Even
> if some parts of a background layer is hidden behind a foreground layer in the camera's initial
> position, panning or zooming the camera may expose parts of background layers that are not
> initially visible."

`https://docs.toonboom.com/help/harmony-22/premium/getting-started/multiplane.html`

> "Using the Maintain Size tool … **This tool allows you to move a layer closer to or further away
> from the camera all the while automatically adjusting the layer's size proportionally to its
> distance from the camera, preserving its a apparent size from the camera's point of view.**"
> *(sic)*

`https://docs.toonboom.com/help/harmony-22/premium/reference/node/move/transform-loop-node.html`

> "The Transform-Loop node allows you to automatically loop a transformation applied by a peg onto
> its element … **This is especially useful if you need to repeat the pan animation of a looping
> background.**"

> "**Repeat** will simply repeat the animation as is … When the Transform-Loop node repeats the
> animation, it skips the first frame of the animation. This is useful when using looping
> backgrounds. Typically, with a looping background, the first frame of the looping animation
> looks exactly like the last frame."

Harmony has **no documented texture-tiling module** for backgrounds; repetition is a loop of the
*pan transform*. (A "Tile" module for this purpose is **UNVERIFIED** — the Harmony 22 reference
search for tiling/repeat backgrounds returns only the Loop and Transform-Loop nodes.)

*Implication.* Two things transfer. First, "draw each layer fully" is the plate-sizing rule the
brief's tiling-vs-oversized-plate question is really about, stated by the people who do this for
a living. Second, Harmony and OpenToonz **both** found a depth number insufficient on its own and
added an explicit *keep-apparent-size-at-this-depth* control — the parameter a naive descriptor
omits.

### 2.4 OpenToonz — the most directly transferable model

`https://opentoonz.readthedocs.io/en/latest/creating_movements.html`

> "Position, `X:` and `Y:` set the horizontal and vertical positions of the selected object,
> **`Z:` sets its position along the Z axis, for defining the depth of the object in 3D space**
> …, and **`SO:` sets the column/layer stacking order, that can override the default one defined
> by the Xsheet column (or Timeline layer) order**."

> "**By default all the pegbars and columns are on the table: their Z position is equal to the
> number of horizontal fields defined for the default camera** … **By increasing the field value,
> objects are placed farther from the camera; by decreasing it, objects are placed closer.**"

> "**The size of the objects changes according to its Z position, like in a real 3D environment
> … To keep control of this behaviour it's possible to define an additional Z position value in
> the tool options bar, that sets the position at which the object has to keep its original
> size.**"

> "**Columns closer to the camera are displayed on top of others, ignoring the Xsheet/Timeline
> order and the SO value. In case two or several columns have exactly the same distance, the SO
> value prevails; if two or several columns have exactly the same distance and SO value, the
> Xsheet column (or Timeline layer) order prevails**"

`https://opentoonz.readthedocs.io/en/latest/working_in_xsheet.html`

> "The column/layer stacking order sets which drawings and images are placed on top, or behind,
> other images. Its direction is from left to right in the Xsheet, and from bottom to top in the
> Timeline, so what is on the left/bottom is behind what is on the right/top."

The traditional "N/S" column field is **UNVERIFIED**: grepping the extracted text of
`creating_movements.html`, `working_in_xsheet.html` and `setting_up_a_scene.html` for `N/S`
returns zero occurrences; the current field names are `X:`, `Y:`, `Z:`, `SO:`.

*Implication.* OpenToonz gives the **three-tier ordering fallback** — computed distance, then an
explicit override, then declaration order — which is the honest answer to §3's `depth == 1.0`
ambiguity, and the "keep original size at Z = k" second number again.

### 2.5 Moho — depth as pure perspective, and two categorical escape hatches

**A premise correction the survey delivered, and it changes §3.2.** `mohodocs.com` does not
resolve (no DNS record); the reachable official manual is `https://www.lostmarble.com/moho/manual/`.
And Moho's `Scale compensation` is **not** a depth/apparent-size compensator — it is about line
weight under layer scaling. **"Auto-Zoom" is UNVERIFIED**: the survey enumerated the manual's
whole TOC and found no such setting, and two searches returned only forum threads.

`https://www.lostmarble.com/moho/manual/tut05/04/index.html`

> "Now, while holding down the `<alt>` key, drag downwards in the editing area. You'll see the
> circle get larger - this is because it is moving closer to the virtual camera."

> "Positive depth (or Z) values are closer to the camera (in the direction out of your screen),
> while negative values point away from the camera (into the screen)."

> "This tells Moho to ignore the layer ordering in the Layers window, and instead draw layers in
> order from furthest to nearest."

`https://www.lostmarble.com/moho/manual/tut06/07/index.html` — the parallax statement:

> "Try dragging the Track Camera tool around again and notice the difference. It's like driving
> in a car - nearby objects go by quickly, while distant objects seem to move slowly."

(the worked example uses Z = −1 for "Right Hill", −2 for "Left Hill", −20 for "Sky").

`https://www.lostmarble.com/moho/manual/layerwnd.html` — what the setting actually is, and the
two flags that matter here:

> "**Scale compensation** — When this box is checked (as it is by default), and you scale an
> entire layer larger or smaller, the lines in the final rendered output will automatically get
> thicker or thinner so that they retain their relative weight in the overall image."

> "**Immune to camera movements** — Sometimes you may want to make some layers ignore camera
> movements. For example, certain backgrounds or title or logo layers you may want to stay in
> one place on the screen even while you move the camera around."

> "**Depth Sort** […] The 'Sort layers by depth' checkbox allows sub-layers to move in front of
> and behind each other during an animation. […] you may want to turn on 'Sort by true
> distance'. This tells Moho to sort layers by the distance from the camera to the layers'
> origins, rather than by depth."

*Implication.* Moho has **no** keep-apparent-size scalar. Its escape hatch is **categorical** —
"Immune to camera movements" — which in the ratio encoding of §3.2 is exactly `depth = 0.0`, i.e.
the encoding already provides it. And Moho couples depth to *stacking* only behind an explicit
opt-in checkbox, which is the finding §3.1 and §3.3 act on.

### 2.6 Lottie — depth and stacking are fully decoupled; `id` ↔ `refId`

`https://lottiefiles.github.io/lottie-docs/layers/`

> "Such lists appear Precomposition, Animation, ShapeLayer, and Groop. In such lists, items
> coming first will be rendered on top" … "This means the render order goes from the last
> element to the first."

> "`ind` | integer | Index | Index that can be used for parenting and referenced in expressions"
> … "Within a list of layers, the `ind` attribute (if present) must be unique."

> "`ddd` | 0-1 integer | Threedimensional | Whether the layer is threedimensional" … "Layers can
> have 3D transforms as well: 3D layers need to have the `ddd` set to 1 (and so does the
> top-level object)."

> "`pe` | Scalar | Perspective | Distance from the Z=0 plane. Small values yield a higher
> perspective effect." *(Camera Layer, `ty = 13`)*

> "`refId` | string | Reference Id | ID of the precomp as specified in the assets"

`https://lottiefiles.github.io/lottie-docs/assets/`

> "`id` | string | ID | Unique identifier used by layers when referencing this asset"

`https://lottiefiles.github.io/lottie-docs/breakdown/precomps/`

> "You can think of the precomp asset to be similar to a video asset, and the layer plays back
> the animation defined by that asset."

> "You need to always specify `w` and `h` in the precomp layer or nothing will be displayed."

*Implication.* Lottie is the clearest statement of the decoupling: **`ind` is identity, array
position is order, and `ddd`/`p.z` is depth — three separate things, and a 3D layer's Z does not
reorder it.** The `id` ↔ `refId` split (a *named* reusable asset, a *positional* instance) is
exactly the store-ref ↔ scene-entity split `an` already has.

### 2.7 Rive — relative draw-order rules, and draw order is a hold value

`https://rive.app/docs/editor/fundamentals/artboards`

> "Artboard dimensions are measured in units rather than pixels. For example, if a 500-unit-wide
> artboard is displayed at 1000 pixels wide, each unit represents 2 pixels."

> "Artboards can't be nested directly inside other artboards. To nest an artboard, first convert
> it to a component."

`https://rive.app/docs/editor/fundamentals/nested-artboards`

> "Components streamline your workflow with reusable artboards and animations. Changes made to
> the source component are reflected across all of its instances."

> "Currently, only artboards that have been flagged as components will be exported to your `.riv`
> file."

`https://rive.app/docs/editor/interface-overview/hierarchy`

> "The Hierarchy also controls draw order. Objects higher in the Hierarchy render above objects
> lower in the Hierarchy."

`https://rive.app/docs/editor/animate-mode/animating-draw-order`

> "Rive allows you to accomplish this with Draw Order Rules." … "Draw Order Rules allow you to
> select a target (note that this must be a drawable item, not a group) and whether to draw above
> or below the target."

> "Note that these are Hold keys as Draw Order cannot be interpolated."

*Implication.* Two transferable rules. **Relative ordering against a named target** is the clean
way to say "characters go in front of the hills and behind the fence" without a numeric key —
which §3.3 adopts. And **draw order is a step function, never a tween**: if a depth float ever
drives ordering, the ordering must be a hold, not interpolated. Note the convention clash worth
pinning explicitly in `an`'s own spec: Rive's UI tree is top = front, Lottie's array is
first = front, `an`'s child array is first = **back** (`runtime.js:143`).

### 2.8 Layer-naming conventions for import (Wave 9's dependency)

**Spine — the mature answer.** `https://raw.githubusercontent.com/EsotericSoftware/spine-scripts/master/photoshop/README.md`
(note `esotericsoftware.com/spine-scripts` now **404s** — UNVERIFIED at that URL):

> "Tags in square brackets can be used in layer and group names to customize the output. The tags
> can be anywhere in the name, for example `head [slot]` or `[slot] head`. If `:name` is omitted,
> the layer or group name is used."

> "**Group and layer names:** `[bone]` or `[bone:name]` … `[slot]` or `[slot:name]` … `[skin]` or
> `[skin:name]` … `[scale:number]` … `[folder]` or `[folder:name]` … `[overlay]` … `[trim]` or
> `[trim:false]` … `[mesh]` or `[mesh:name]` … `[ignore]`"

> "**Group names:** `[merge]` … `[name:pattern]` Adds a prefix or suffix to layer names in the
> group. The pattern must contain an asterisk (*)."

> "If a layer name, folder name, or path name starts with `/` then parent layers won't affect the
> name."

`https://en.esotericsoftware.com/spine-import-psd` (the newer built-in importer):

> "Using tags in group or layer names tells Spine how to process those items. Tags are surrounded
> by square brackets, for example `[tag:value]`. … Some tags can only be used on layers, others
> only on groups, but most can be used on both."

> "The position of guides in the PSD determine the 0,0 origin in Spine."

**Live2D Cubism — names are identity across re-import.**
`https://docs.live2d.com/en/cubism-editor-manual/precautions-for-psd-data/`

> "Check to see if there are any layers with the same name — Typically, the same layer name can be
> imported into Cubism Editor, but the same layer name can cause confusion, which can lead to
> problems later on. To avoid such problems, all the layers should be named differently."

`https://docs.live2d.com/en/cubism-editor-manual/psd-re-import/`

> "As things like changing or duplicating part names (or layer group names in PSDs) or changing
> the layer order, etc., can easily cause malfunctions, try to avoid these as much as possible."

`https://docs.live2d.com/en/cubism-editor-manual/draworder/` — a *numeric* draw-order key, and an
animatable one:

> "The draw order is set in values between 0 and 1000, and the drawable object with the highest
> value is displayed in the foreground. When the draw order values are equal, the part at the top
> of the [Part(s)] palette list is displayed in the foreground."

> "The draw order can be assigned to a parameter and changed by the parameter value in the same
> way as the shape (keyform) of the drawable object."

**Harmony PSD import — three modes, and it deliberately does *not* parse layer names.**
`https://docs.toonboom.com/help/harmony-22/premium/import/about-psd-import.html`

> "**Single Layer:** Imports the rasterized, flattened version of the PSD into a single layer in
> your scene." … "**Groups as Layers:** This imports every group in the PSD file as a single layer
> in your Harmony scene." … "**Individual Layers:** This imports each group in the PSD file as a
> group in Harmony, and each layer in the PSD file as a single layer in Harmony, reproducing the
> structure of your PSD file into your Harmony scene."

`https://docs.toonboom.com/help/harmony-22/premium/import/import-multi-layer-psd-file.html`

> "**Create Layer(s) Based on Filenames:** Creates a layer based on each unique filename prefix."

> "If you import the PSD file with the Groups as Layers or the Individual Layers option, all of
> the layers and drawings imported into your scene will be linked to the same PSD file in your
> scene folder. Hence, deleting any of the drawings or layers imported from the PSD file is
> liable to delete all of the drawings imported from the PSD file."

**Rive's PSD rule**, `https://rive.app/docs/editor/assets/psd` — the same identity warning:

> "Keep PSD layer names consistent when reimporting. If you rename a layer in Photoshop and
> reimport the PSD, Rive treats it as a new layer. Any objects using the old layer may lose their
> asset reference."

**Krita — UNVERIFIED.** No official documentation of a layered-file-export naming convention was
found; `reference_manual/layers_and_masks.html` has no naming-convention content,
`reference_manual/animation.html` 404s, and the docs search page is a JS shell. The only
official naming material is the animation *frame export* filename template, which is a
file-sequence convention, not a layer-semantics one.

*Implication for the plane descriptor (Wave 9 is the customer, but the schema is decided now).*
Four rules make `EnvironmentDescriptor` importable without building the importer:
**(1)** a plane's `name` is its **identity**, must be unique, and renaming it breaks references —
say so in the docstring the way Live2D and Rive both do;
**(2)** the human label and the stable id must be separable, which is why `Plane.name` is the id
and any display label belongs in `extra`/a `label` field, never overloaded onto `name`;
**(3)** the depth hint is the *only* thing a layer name should have to carry — Spine's
`[tag:value]` syntax, position-independent, `:value` optional, is the syntax to copy for
`[depth:0.3]` when Wave 9 lands, and everything else stays explicit descriptor fields (Moho's
lesson: flags are booleans on the layer, not tokens in its name);
**(4)** `planes` list order = a background artist's layer stack order, which is precisely the
brief's "depth inferred from layer order" — but per §2.6/§2.7 that inference must produce a
`depth` *and* a position, not conflate them.

### 2.9 The cross-cutting split a descriptor must resolve

| | parallax parameter | "no parallax" | tiling | z-order | depth↔order coupled? |
|---|---|---|---|---|---|
| Godot `Parallax2D` | `scroll_scale: Vector2` multiplier | `1` = camera speed, `0` = frozen | `repeat_size` px interval + `repeat_times` | node `layer` int / tree order | no |
| Unity 2D | none (no parallax primitive) | — | Draw Mode Tiled + `Size` + Continuous/Adaptive | named `Sorting Layer` + int `Order in Layer` | orthographic z is the *tiebreak* only |
| Harmony | Z distance, perspective-derived | Maintain Size preserves apparent size | Transform-Loop on the pan | cumulative Z + hierarchy | yes |
| OpenToonz | `Z:` in camera fields, default = camera field width | at the table default; 2nd Z preserves size | not documented | distance → `SO:` → column order | yes, with two overrides |
| Moho | Z translation, perspective-derived | `Immune to camera movements` (categorical) | — | Layers-window order **unless** "Sort layers by depth" | **opt-in only** |
| Lottie | `p.z` + camera `pe` (`ddd=1`) | `ddd=0` | — | array position (first = on top) | no |
| Rive | — | — | — | hierarchy + relative Draw Order Rules (hold keys) | no |

Three findings the table makes, and each drives a decision below:

1. **Godot's `scroll_scale` and the distance-based `Z`s are the same fact in inverse
   coordinates.** Wave 7 should take Godot's encoding, for a reason specific to `an`: this
   renderer has no depth buffer and no perspective projection — the camera is a scale tween on a
   container (`compile.py:2906-2963`) — so a distance would have to be converted to a multiplier
   at compile time, and `d = z_ref / z` needs a reference distance that is a free parameter with
   no correct value. The multiplier *is* the information.
2. **Five of seven systems keep depth and stacking decoupled**, and the two that couple them
   (Harmony, OpenToonz) have a perspective camera that computes the distance anyway. Moho couples
   them only behind an explicit checkbox. So deriving draw order from `depth` is the minority
   design and §3.3 does not do it.
3. **Only the two distance-based systems needed a keep-apparent-size control** (Harmony's
   Maintain Size, OpenToonz's second Z). Moho, the third distance-based one, has none — its
   escape hatch is categorical, and in a ratio encoding that hatch *is* `depth = 0.0`. So the
   ratio encoding does not inherit the problem; §3.2 records the one place it still bites.

---

## 3. The `EnvironmentDescriptor` proposal

### 3.0 What it is, and where it lives

A schema-versioned document, `extra="allow"`, stored as the environments store's `meta.json`,
owning its own `DocumentKind`. New package `an/environments/` mirroring `an/characters/`:
`schema.py` (the models + the kind + the migrations), `generate.py` (§5), `cli.py` (§5).

```python
# an/environments/schema.py
ENVIRONMENT_SCHEMA_VERSION = "0.1.0"

ENVIRONMENT_DOCUMENT_KIND: DocumentKind = register_kind(
    DocumentKind(
        name="EnvironmentDescriptor",
        version_field="schema_version",
        current_version=ENVIRONMENT_SCHEMA_VERSION,
    )
)
```

exactly as `CHARACTER_DOCUMENT_KIND` is registered from the package that owns the schema
(`an/characters/schema.py:57-68`), for the reason `an/ir/migrate.py:21-26` gives: `migrate` must
never import the packages it serves.

**One trap the character path does not have.** `kind_of` falls back to
`DFLT_KIND = "SceneIR"` for a document with no `kind` key (`migrate.py:71`, `:92`), and today's
environment documents have no `kind` key — they are free-form `meta.json`. So every call site
must pass `kind=` explicitly, the way `_build_svg_character_subtree` does
(`compile.py:1242-1244`):

```python
EnvironmentDescriptor.model_validate(
    migrate(dict(doc), kind=ENVIRONMENT_DOCUMENT_KIND.name)
)
```

**The migration this proposal owes.** Register `("EnvironmentDescriptor", "0.0.0", "0.1.0")`,
selected by `version_of` defaulting to the *current* version when the field is absent
(`migrate.py:57-59`) — which means a legacy document would be read as 0.1.0 and fail
validation. So the loader must stamp `schema_version = "0.0.0"` on any document lacking the
field before calling `migrate`, and the 0.0.0 → 0.1.0 function converts
`{sky_color, ground_color, ground_y}` into the two-plane form and **carries `name`,
`description`, `tags` and any other key through untouched** — `extra="allow"` plus the
`compile.py:790-794` promise that those keys are the store's natural shape. A migration that
drops them turns a documented-as-harmless warning into data loss.

### 3.1 The models

```python
class PlaneArt(_EnvModel):
    kind: Literal["fill", "gradient", "image", "generated"]
    color: str | None = None                      # fill
    stops: list[tuple[float, str]] | None = None  # gradient: (offset 0..1, hex)
    angle: float = 0.0                            # gradient, radians
    path: str | None = None                       # image: relative to the env dir
    generator: str | None = None                  # generated: a name in an.environments.generate
    params: dict[str, Any] = Field(default_factory=dict)

class Plane(_EnvModel):
    name: str                       # node name; unique; becomes `env_id/<name>`
    art: PlaneArt
    depth: float = 1.0              # the ONE scalar; see 3.2
    parallax: tuple[float, float] | None = None   # per-axis override; None = (depth, depth)
    offset: tuple[float, float] = (0.0, 0.0)      # scene units, rel. to the environment node
    anchor: tuple[float, float] = (0.5, 0.5)
    size: tuple[float, float] | None = None       # None = the art's own raster
    fit: Literal["stretch", "contain"] = "contain"
    repeat: Literal["none", "x", "y", "xy"] = "none"
    repeat_size: tuple[float, float] | None = None   # None = derive from `size`

class EnvironmentDescriptor(_EnvModel):
    schema_version: str = ENVIRONMENT_SCHEMA_VERSION
    kind: Literal["EnvironmentDescriptor"] = "EnvironmentDescriptor"
    name: str
    planes: list[Plane] = Field(default_factory=list)   # LIST ORDER IS DRAW ORDER
    #: Name of the plane AFTER which character subtrees are emitted.
    #: None = after every plane, which is today's behaviour exactly.
    characters_after: str | None = None
    anchors: dict[str, tuple[float, float]] = Field(default_factory=dict)
    haze: str | None = None
    source: AssetSource | None = None
```

Field-by-field justification for the ones that are not obvious:

- **`planes` list order is draw order, and there is deliberately no `z` field.** The runtime has
  no z-index and no `sortableChildren` in use (§1.5); a `z` integer would be a second SSOT for a
  fact the emission order already carries, and one the runtime could not honour anyway. This is
  Unity's `Order in Layer` and Live2D's 0–1000 draw order deliberately *not* adopted, and
  OpenToonz's third tier ("the column order prevails") adopted as the only tier. It is also the
  majority design: five of the seven systems surveyed keep depth and stacking decoupled (§2.9).
- **`characters_after` is how a foreground plane happens, and it is *not* derived from `depth`.**
  The brief proposes "a plane at depth > 1.0 becomes a foreground by being emitted after the
  characters". The survey says that coupling is the minority design (§2.9 finding 2) and that
  where it exists it is opt-in (Moho's "Sort layers by depth" checkbox). Rive's Draw Order Rules
  — "select a target … and whether to draw above or below the target" — is the shape to copy:
  **relative ordering against a named target**. One optional field whose `None` default
  reproduces today's picture byte-for-byte, and it dissolves the `depth == 1.0` ambiguity
  entirely rather than resolving it by convention. It also expresses the case a depth rule
  cannot: a fence at `depth = 0.9` that the characters stand *behind*.
- **`anchors` replaces `horizon` as a field.** The brief wants named anchors as stage marks
  (replacing `_layout_character_positions(n, spread=220.0)`, `compile.py:836-851`) *and* a
  horizon. Those are one mechanism: `anchors["horizon"]` is a named point like
  `anchors["bench"]`. Two fields for one fact is how `_ENV_PRESETS`'s intersecting override got
  here.
- **`haze` is a colour, not a ramp.** The per-plane desaturation ramp the brief describes is 7b
  art direction (the style pack) reading `depth`; the environment declares only the colour to
  ramp *toward*, which is the one fact the style pack cannot compute.
- **`source: AssetSource`** — the same field the character descriptor carries
  (`an/characters/schema.py:321`). It is not decoration: `collect_credits` walks **only**
  `mall["characters"]` today and says so in its own docstring — "Only the characters store
  carries provenance today. Environments, styles and props will as they gain real art"
  (`an/credits.py:110-116`). The PR that gives environments art is the PR that closes that hole,
  or `an credits` becomes an affirmative false compliance statement about plates.
- **No `rest_camera`.** The camera is `Shot.camera` (`an/ir/schema.py:73-85`), per shot. An
  environment carrying its own camera creates a shot-vs-environment resolution order the brief
  did not budget for, and T1 owns the camera. Recommend against.
- **No `design_resolution`.** It would be a field-shaped placeholder, and `serialize.py:9-19`
  states the rule directly: "a new field needs its producer and its consumer in the same
  change." `size=None` meaning "the art's own raster, in scene units" is one rule with no free
  parameter, and it is the rule the character path already uses (`compile.py:1322-1333`).

### 3.2 What `depth` means numerically, and how it maps to a parallax factor

**`depth` is the multiplane distance ratio, in Godot's coordinates.** Not a distance.

- `depth = 1.0` — the character plane. The plane the camera's declared move is expressed
  against. A plane at 1.0 moves exactly as the characters do, which is today's behaviour for
  everything.
- `depth = 0.0` — infinitely far. Frozen relative to the frame: neither pans nor scales.
- `0 < depth < 1` — background. Godot's tutorial values for a five-layer stack are the sanity
  range: 0.1 sky, 0.2 higher clouds, 0.3 lower clouds, 0.5 hills, 0.7 forest.
- `depth > 1.0` — foreground, nearer than the characters, moving faster.
- Negative is refused by the schema (`Field(ge=0.0)`).

**The interface between T2 and T1 is one property, and this is its entire contract:**

```python
@property
def parallax_factor(self) -> tuple[float, float]:
    """(x, y) multipliers the camera applies to this plane. Both default to `depth`."""
    return self.parallax if self.parallax is not None else (self.depth, self.depth)
```

The compiler asks each plane for `parallax_factor` and hands the pair to the camera emitter.
T2 asserts nothing about what the camera does with it; the brief's own formulas (`scale = 1 +
(s−1)·d`, `x = dx·d`, `y = dy·d`) are T1's to confirm, refute or refine, and a character subtree
is `d = 1.0` so today's single-root behaviour is a strict special case.

**Why a per-axis pair rather than one scalar**, despite the brief's "a single float": Godot's
`motion_scale` and `scroll_scale` are both `Vector2`, and its own tutorial's five-layer example
uses `(0.7, 1)`, `(0.5, 1)`, `(0.1, 1)` — every layer pans horizontally at its own rate and
tracks the camera *vertically* at 1.0. A sky that slides sideways but not up is the single most
common cheat in the idiom, and one scalar cannot express it. The cost is one optional field
whose default makes it invisible.

**Why a ratio rather than a distance**, despite Harmony, OpenToonz and Moho all using distance:
`an`'s camera is a scale tween on a container with no projection (`compile.py:2906-2963`), so a
distance would have to be converted to a multiplier at compile time, and the conversion
`d = z_ref / z` needs a reference distance that is a free parameter with no correct value.
OpenToonz makes that parameter explicit (`Z` defaults to "the number of horizontal fields defined
for the default camera") precisely because it *has* a camera cone; `an` does not.

**Where the "maintain size" question does and does not bite.** Two of the three distance-based
systems added an explicit keep-apparent-size control — Harmony's Maintain Size, OpenToonz's
second Z — and the third, Moho, has **none**: its only escape hatch is the categorical "Immune to
camera movements" (§2.5). That split is informative. The control exists to undo the *scale* that
a distance encoding forces on you when you move a layer in depth; a ratio encoding never applies
that scale in the first place, because `x = dx·d` leaves the plane's rest position and rest size
untouched. So the ratio encoding **does not inherit the problem for placement**, and it provides
Moho's hatch for free: `depth = 0.0` *is* "immune to camera movements".

It bites in exactly one place: under `scale = 1 + (s−1)·d`, a plane at d = 0.3 in a 1.25× push-in
ends at 1.075×, and an artist who wants the plate to hold its size through the push must trade
that against the pan rate. Recommend: **do not add a second parameter in 7a.** The clean future
answer is a third component on `parallax` — `(pan_x, pan_y, scale)` with all three defaulting to
`depth` — not a second model. Recorded here so it is a known omission rather than a later bug.

### 3.3 How today's two-rectangle presets become planes — byte-identically

**The constraint, stated exactly.** `scene_contract_sha256` is
`sha256(json.dumps(scene_json, sort_keys=True, separators=(",",":")))` over the **whole staged
document** (`an/bench/contract.py:53-71`), and `an bench-compare` refuses rows whose hash
differs — so a change that moves no pixel but moves the hash retires every committed ledger row
as evidence. This is exactly the Wave 6 PR-C acceptance ("the acceptance is therefore **JSON
identity**", `misc/docs/wave6_research.md:662-669`), and it applies here to the two fixtures in
§1.10.

`to_dict` is `model_dump(mode="json")` with "no None pruning" (`serialize.py:321-323`), so
**every** `VisualJSON` and `TransformJSON` field is serialized, defaults included. The identity
budget is therefore tighter than it looks.

**It is achievable, and here is the exact recipe.** Each preset becomes a two-plane descriptor:

| plane | `art` | `depth` | `offset` | `size` | `anchor` |
|---|---|---|---|---|---|
| `sky` | `fill(color=sky_color)` | 0.0 | `(0.0, -2000.0 + ground_y)` | `(4000.0, 4000.0)` | `(0.5, 0.5)` |
| `ground` | `fill(color=ground_color)` | 1.0 | `(0.0, +2000.0 + ground_y)` | `(4000.0, 4000.0)` | `(0.5, 0.5)` |

and a `fill` plane compiles to `VisualJSON(kind="rect", width=w, height=h, color=c)` — the same
three explicit fields `_build_environment_subtree` sets today (`compile.py:821-823`,
`:828-830`), leaving `fit="stretch"`, `texture_id=None`, `asset_id=None`, `asset_sets=None`,
`anchor_x=0.5`, `anchor_y=0.5` at their defaults, which is what the current code also leaves.
The node names stay `sky` and `ground`, the container stays `NodeJSON(name=entity.id,
transform=TransformJSON(), children=[...])`, and `AssetResolutionJSON.resolved` stays the
literal `"preset"` (`compile.py:764`).

Five rules make it hold, and each is a way it breaks:

1. **No new field on `VisualJSON` or `NodeJSON` with a serializing default.** A new field with
   *any* non-excluded default appears in every node of every scene and moves every corpus hash,
   including the five fixtures with no environment at all.
2. **`depth` never reaches the wire for a static camera.** It is descriptor-side; it becomes
   camera channels, and a shot with `camera=None` or `move="hold"` emits none (`compile.py:2917`,
   `:2925-2926`). `multi_shot` declares no camera at all; `promote_demo` declares `move: hold`
   (`examples/promote_demo/scene.md:20-24`).
3. **Anything new on `CutoutSceneMetaJSON` is omitted when unset**, via the existing wrap
   serializer that already pops `step_hz` when `None` and `gaze_seeds` when empty
   (`serialize.py:284-291`) — written for precisely this reason.
4. **`0.0`, not `-0.0`, and floats not ints.** `y = -huge/2 + ground_y` is `-1890.0` for `park`;
   a descriptor that stores `ground_y` as an int and computes in ints serializes `-1890` and the
   hash moves. Keep every geometry value a `float`.
5. **The preset table ships as data but computes the same numbers.** The brief says "the presets
   ship as data"; a bundled `an/environments/data/presets.json` holding the same five
   three-key rows, expanded by the same arithmetic, is identity-preserving. **Changing what
   `park` looks like is not.**

**If a richer default look is wanted, add a preset name — do not change `park`.** A three-plane
`park` costs two golden re-blessings (`misc/bench/golden/promote_demo`,
`misc/bench/golden/multi_shot`) and retires those two fixtures' ledger rows as comparable
evidence. That is a real price for a look nobody asked for in 7a, and it is avoidable at zero
cost by naming the new thing `park_multiplane`.

**The first commit of 7a should be the proof, not the claim.** Compile `multi_shot`'s two shots
and `promote_demo`'s one shot at today's `main`, save the three staged JSON documents as
fixtures, and assert equality after the refactor — the same shape as
`tests/test_expression_compose.py`, which asserts "all seven pre-existing corpus contract hashes
equal the committed ledger row's" (`misc/docs/wave6_research.md:716-718`). Written first, it is a
gate; written last, it is a re-bless.

**The one piece of code that must change, and why it still costs no hash.**
`_build_scene_root`'s two-loop structure (§1.5) cannot interleave, and a foreground plane
requires interleaving. The change is to walk the plane list once, emitting character subtrees
after the plane named by `characters_after` (§3.1) — with `characters_after=None`, the default
and the only value any preset uses, every plane is emitted first and the child array is
identical to today's. So the structural edit lands in 7a with zero output change, and the
foreground capability arrives with the first descriptor that sets the field.

This is deliberately **not** the brief's `depth > 1.0` rule. The survey's second finding (§2.9)
is that deriving stacking from depth is the minority design and is opt-in wherever it exists,
and Rive's relative-target rule is what five of seven systems do instead. The named-target
version is also strictly more expressive — a fence at `depth = 0.9` in front of the characters is
a real shot and a depth rule cannot express it — and it removes the `depth == 1.0` tie rather
than resolving it by convention.

### 3.4 Validation the schema makes possible (and the brief asks for)

- Duplicate `plane.name` within an environment → error (it is a node path segment).
- A plane name colliding with a reserved node name (`root`) → error.
- `depth < 0` → schema error; `depth > 4` → warning (nothing in the survey's examples exceeds
  ~1.5, and the plane budget is §6's risk).
- `art.kind == "image"` with `path` absent, or `art.kind == "fill"` with `color` absent → error.
- `repeat != "none"` with `size=None` → error: there is no period to derive.
- **`len(planes) > 7` → warning.** The brief cites "the historical seven-layer rig as an
  empirical upper bound"; the survey did not confirm a seven-layer figure — Godot's tutorial uses
  five (§2.1) — so treat 7 as a **declared budget with a stated rationale, not a cited fact**,
  and say so in the message. **UNVERIFIED as a historical claim.**
- The old warning at `compile.py:795-803` becomes real validation: an unknown key on a
  *versioned* document is either migrated or reported, never dropped.

---

## 4. Textures: what a raster plate needs

### 4.1 A `VisualJSON` kind — and the argument for a new one

The functional answer is that **no new kind is needed**: `svg_sprite` already means "a
`PIXI.Sprite` from a preloaded texture identified by `asset_id`" (`serialize.py:84-85`), the
runtime resolves the loader by extension/mime, and a `.png` behind that `asset_id` renders.

The reason to add one anyway is the an#33 machinery. `Fixture.expect_visual_kinds` exists so a
capture that did not exercise its declared render path fails loudly
(`an/bench/corpus.py:1-14`, `:373-383`), and a plate corpus scene that declared `svg_sprite`
would be satisfied by any character in the frame. A plate fixture must be able to assert *a
plate reached the screen*, which needs a name of its own.

**Recommendation: add `"image"` to the `Literal` (`serialize.py:101`) and route both
`svg_sprite` and `image` to `makeSvgSprite`** (which should lose its name and become
`makeTextureSprite`), leaving every existing document byte-identical. Then
`fidelity.SPRITE_KIND` (`an/adapters/cutout/fidelity.py:49`) becomes a frozenset of both — its
`sx`/`sy` aspect-distortion instrument (`:19-32`) applies to a plate unchanged and is worth
keeping, because a stretched plate is the same defect an#74 fixed for arms.

**Recommendation against renaming `svg_sprite` → `sprite`**, tempting though it is (the dead
`"sprite"` member falls through to `makeRect`, `runtime.js:170-171`). Five of the seven corpus
fixtures declare `svg_sprite` (`corpus.py:206`, `:218`, `:230`, `:236`) and it is inside the
contract hash of every descriptor scene, so the rename costs five re-blessings for a naming
improvement. Wave 7 already carries one breaking rename (`Shot.style` → `Shot.renderer`, 7b);
this is not the wave to add a second whose entire benefit is aesthetic.

### 4.2 Runtime texture loading

Nothing to build (§1.7). Two facts worth writing into the `an-dev-stage` skill:

- **The alias sort is a determinism contract**, not tidiness (`runtime.js:558-563`), and a plate
  adds entries to the same sorted list.
- **Supersampling does not sharpen a texture.** `an render --supersample N` sets the PixiJS
  application's `resolution` (`runtime.js:635-643`), which multisamples *geometry*; a texture is
  rasterized at the resolution the loader chose. In the vendored bundle that is
  `getResolutionOfUrl(url, default=1)` — `function Kt(i,t=1){...RETINA_PREFIX.exec(i)...}` — so
  an SVG or PNG rasterizes at 1× unless the filename carries an `@2x` suffix. A plate behind a
  1.25× push-in is upscaled from a 1× raster. The `@2x` lever exists, is undocumented, and is
  untested; §5's generator should either use it or the descriptor should say the plate is
  authored at final size.

### 4.3 `asset_resolution` for planes

Mirror `_record_missing_parts` exactly (`compile.py:1133-1187`): one `AssetResolutionJSON` per
*plane* whose plate is absent, `kind="plane"`, `id=f"{env_id}/{plane_name}"`,
`resolved="missing"`, `fallback=True`, with a `detail` naming the declared path and where it was
looked for. Keep the existing one-per-entity entry for the ref resolution
(`compile.py:773-783`) — two records answering two different questions, which is what the
character path already does.

The escalation then routes through the one existing decision point
(`_raise_or_warn_on_asset_fallbacks`, `:510-548`) with no second policy, and
`strict_assets=True` makes a corpus scene with a missing plate fail rather than render a hole.
**This is what makes the brief's done-when "an environment authored with an image plate reaches
the screen" checkable** — without it, a plate that failed to stage renders as
`PIXI.Texture.WHITE` or crashes at load, and `render.py:537-551` documents which inputs produce
which.

### 4.4 The determinism perimeter — and the measurement Wave 7 owes

`an/determinism.py` watches the capture page, both tickers and any attached filter
(`:141-147`); raster art touches none of them, so nothing there goes red. That is not
reassurance — it is the wrong instrument for this question.

**The right question is whether a PNG decodes bit-identically across the three CI platforms, and
it has not been measured.** `misc/docs/wave2_crossarch_verdict.md` captured exactly two fixtures
— `examples/single_character` (procedural: `rect`, `ellipse`, `eye`, `mouth`) and
`examples/promote_demo` (`svg_sprite`, `rect`) (`:51-55`) — and its "What this does NOT settle"
list (`:149-163`) names other Chromium builds, text, and a production shot. **It does not name
raster decode**, which is precisely the shape of unexamined assumption that section exists to
prevent.

The mechanism differs from the SVG case in a way that matters. An SVG goes through pixi's
`loadSVG` → `SVGResource`, i.e. Chromium's SVG rasterizer drawing into a canvas under
SwiftShader. A PNG goes through `loadTextures` → `loadImageBitmap` → `createImageBitmap`, i.e.
Chromium's PNG decoder plus colour-space handling. PNG decoding itself is lossless and fully
specified; the risk is **colour management** — `--force-color-profile=srgb` is already pinned in
the launch argv (`wave2_crossarch_verdict.md:43`), but a PNG can carry an embedded ICC profile
or `gAMA`/`cHRM` chunks, which an SVG cannot, and those are a new input to a pinned perimeter.

**Recommendation, and it belongs at the front of the wave:** run
`misc/bench/crossarch.py capture` with a raster-plate fixture as Wave 7's first task, the way
the SVG verdict was Wave 2's first task, and record the answer in the same file. Until it
lands, §5's recommendation — **generate plates as SVG, not PNG** — keeps the wave inside the
already-measured perimeter, and any committed PNG should be stripped of ancillary chunks.

### 4.5 Content-hashed asset ids, size limits, and the sdist posture

**Asset ids.** The wire alias today is a *name*: `f"{entity.id}.{slot_name}.{attachment_name}"`
(`compile.py:1267`), deliberately slot-qualified because attachment names are a per-slot
namespace (`:1264-1266`). Recommend planes keep name-addressing — `f"{env_id}.{plane_name}"` —
because the runtime keys `PIXI.Assets` on it and a digest would make the staged JSON
unreadable while adding nothing (there is no cross-shot texture cache to deduplicate into).
The **content hash belongs in provenance**: `AssetSource.sha256` already exists and its
docstring states the reason — "A licence attached to a URL is a licence attached to whatever
that URL serves today; attached to a digest, it stays attached to the thing that was actually
used" (`an/ir/assets.py:28-30`).

**Size limits.** None exist anywhere. `_stage_scene_assets` does an unconditional
`shutil.copy2` (`render.py:640`). Recommend a warning threshold, not a refusal — the failure
mode is a slow render and a fat sdist, not a wrong picture.

**sdist / `.gitignore`.** The relevant facts: `examples/*/assets/` is gitignored
(`.gitignore:129`) with a single carve-out for `promote_demo`'s input SVG (`:135-140`); the
bench corpus is committed **whole** and lives outside `examples/` for exactly that reason
(`:153-156`); the sdist deliberately ships both `misc/` and `examples/`, with the measurement
recorded — golden corpus 52,532 B, fixtures 28,005 B, together 1.0% of the sdist against
`examples/` at 69.6% — and the standing note "Revisit at a corpus over 20 MB, or a single
re-bless over 5 MB" (`pyproject.toml:76-85`). The wheel ships only `an/` (`:58`) plus the
explicit runtime inventory enforced by `tests/test_vendored_engine.py` (`:60-73`).

A committed raster plate lands in the sdist and is ~100× the bytes of the SVGs already there.
**Recommend corpus plates be built by a `Fixture.prepare` step** (`an/bench/corpus.py:115-117`)
from a committed generator. This is a deviation from the corpus's own rule — the four
bench-owned fixtures have no `prepare` step so "their pixels are a function of the repo alone"
(`corpus.py:136-141`) — and the deviation is honest only because a committed deterministic
generator *is* the repo: the property is preserved, the mechanism changes. Say that in the
fixture's docstring rather than leaving it to be rediscovered.

---

## 5. Authoring

### 5.1 `scene.md`

**The entity block does not change.** Planes are a property of the store document, not of the
scene — the same split characters already use, and the reason a scene can reference the same
environment from twelve shots without repeating it.

```yaml
- kind: environment
  id: stage
  store: environments
  ref: valley_multiplane
```

Recommend `AssetRef.overrides` stay unread (§1.2). A per-shot plane tweak is a second
authoring surface for the same fact, and the intersecting preset override is already the
cautionary tale.

**The one scene-side change is the camera**, and it is T1's: `camera.move: pan_left` must be
added to **both** `_CAMERA_MOVES` (`compile.py:2897-2903`) and `_RENDERABLE_CAMERA_MOVES`
(`an/ir/validate.py:107-109`), which are two copies of one fact pinned equal by
`tests/test_loud_discards.py:683-685`. Change one and `an validate` and the compiler disagree
about what renders.

### 5.2 An `an env` CLI — yes, and name it `env` from the start

`_dispatch_namespaces` has exactly one entry today — `{"character": _character_dispatch_funcs}`
(`an/tools.py:512-514`) — mounted by `__main__` as `an character <verb>`. The pattern to copy is
`an/characters/cli.py`: plain functions in a module-level `_dispatch_funcs` list, wired
programmatically, never decorated (pillar 8, `CLAUDE.md`).

Recommended verbs, mirroring the character namespace:

| verb | mirrors | does |
|---|---|---|
| `an env new <name> --preset park` | `an character new` (`cli.py:42`) | write a descriptor + generated plates |
| `an env validate <name>` | `an character validate` (`:196`) | §3.4's rules, offline |
| `an env preview <name>` | `an character preview` (`:260`) | one plane stack, no scene |

**Name the namespace `env`, not `environment`**, because the epic's Wave 9 brief already names
`an env import --license … --attribution …` (epic #9, first comment). Choosing `env` now costs
nothing; choosing `environment` costs a rename in the wave that is least able to afford one.

### 5.3 A procedural plane generator — so demos stay offline and free

The precedent is `an/characters/factory.py`, which synthesizes every part of a character as an
SVG string written to disk — `_write_head_part` (`:558`), `_write_torso_part` (`:571`),
`_write_arm_part` (`:587`), `_eye_svg` (`:329`), `_synthesize_brow` (`:618`). Recommend
`an/environments/generate.py` with the same shape:

- `sky_gradient(*, top, bottom, size)` — a `<linearGradient>` fill.
- `ground(*, color, size)` — a flat band.
- `hills(*, color, count, roughness, seed)` — a deterministic silhouette from a seeded hash,
  the way `blink_phase` derives from `_js_string_hash(entity_id)` (`compile.py:191-218`) rather
  than from a PRNG. A seeded hash is reproducible across processes and Python versions; a
  `random.Random(seed)` is only reproducible across Python versions that do not change the
  generator.
- `treeline(...)`, `clouds(...)` — same rule.

**Emit SVG, not PNG.** Four reasons, in order of weight: SVG is inside the already-measured
cross-arch perimeter (§4.4) and PNG is not; SVG carries no ICC/gamma chunks; `raster_size`
already reads it (`an/characters/svg_utils.py:163-190`) so the size probe works unchanged; and
it is small enough to commit if a fixture needs to hold still.

The demo gallery then gets a `multiplane-pan` clip built entirely offline, added to `DEMOS`
(`misc/demos/build_demos.py:653`) as a `Demo(slug, title, shows, how, build)` (`:58-70`)
alongside `_build_camera` (`:385-395`), which today renders the four implemented moves as four
2-second shots. The standing rule is "a new user-facing capability gets a demo" (`CLAUDE.md`),
and the stage is four capabilities: planes, parallax, a plate, a pan.

### 5.4 The corpus scene for the pan golden

T5 owns the measurement. What T2 owes it is a scene whose planes are **recoverable from a PNG**.

Recommended fixture, `misc/bench/corpus/parallax_pan/`:

- **Three planes at distinct depths** — 0.25, 0.5, 1.0 — plus the characters at 1.0 if any. Three
  is the minimum that distinguishes "the planes moved at different rates" from "two things moved"
  and stays inside the plane budget.
- **Each a flat, maximally distinct fill with one hard vertical edge at a known x.** Not
  texture, not gradient: the measurement is a per-plane horizontal displacement, and a vertical
  edge is what makes displacement recoverable by column-scanning a frame. `saturated_outline`
  (`corpus.py:228-233`) is the precedent for choosing art that makes a metric possible rather
  than art that looks nice.
- **`expect_visual_kinds=frozenset({"image"})`** if §4.1's new kind lands — that assertion is the
  whole reason to add it (§4.1). If planes are fills only in 7a, `{"rect"}` — and say in the
  fixture's `golden_note` that the plate path is therefore *not* covered by this fixture.
- **A declared `camera.move` that pans.** Without it the scene proves nothing; with `hold` it
  proves nothing and passes.
- **At least two `golden_frames`**, the second chosen so the *ratios* differ and not merely the
  pixels — and note `--bless` refuses a pixel-identical pair, which is not hypothetical
  (`corpus.py:120-125`: `promote_demo`'s frame 0 and `duration/2` differ by exactly zero pixels).
- **A `golden_note` stating what moved and by how much per plane**, carried as data "because
  'pick a time where something moved' is a rule that decays into a habit" (`corpus.py:126-129`).

One negative worth stating: a **flat 2D zoom gives identical displacement ratios across planes**,
which is the epic's own done-when. So the fixture's assertion must compare *ratios between
planes*, not any single plane's displacement — a scene where all three planes move is passed by
today's root-scale camera.

---

## 6. Risks and unknowns

1. **The byte-identity claim is checkable before any code is written, and should be.** Compile
   `multi_shot` (two shots) and `promote_demo` (one shot) at today's `main`, commit the staged
   JSON as fixtures, assert equality after. Written first it is a gate; written last it is a
   re-bless. **HIGH** — it is the single thing that decides whether 7a retires two ledger rows.
2. **`_build_scene_root`'s two-loop structure is the foreground blocker, and the brief does not
   name it.** The brief says a plane at `depth > 1.0` "becomes a foreground by being emitted
   after the characters, with no runtime change" — true of the *runtime*, false of the
   *compiler*, which cannot interleave today (§1.5). **MEDIUM**, and it is the one structural
   edit 7a cannot avoid. §3.3 also **declines the depth-derived form** of the rule in favour of
   `characters_after`; if 7a keeps the brief's version instead, note that it hard-couples two
   things five of seven surveyed systems keep separate (§2.9).
3. **Raster determinism across ISA/OS is unmeasured** (§4.4). The Wave 2 verdict measured SVG
   and procedural geometry only and does not list raster among its exclusions — an omission, not
   a clearance. **HIGH** if plates are committed as PNG; **LOW** under §5.3's SVG-generator
   recommendation.
4. **Tiling has no runtime.** `PIXI.TilingSprite` is present in the vendored bundle and
   `runtime.js` never constructs one; `makeVisual`'s dispatch (`runtime.js:147-172`) is where a
   `repeat` plane would land, and `refitToBox`/`_anFitBox` (`:176-186`) has no tiling
   counterpart. **The `repeat`/`repeat_size` fields in §3.1 are schema without a consumer** —
   which `serialize.py:9-19` explicitly forbids. Either the runtime change lands in 7a or the
   two fields do not. Recommend: land them together, or defer both and rely on Harmony's
   "draw each layer fully" (§2.3) plus oversized plates for 7a.
5. **Draw order must never be tweened.** If any later wave lets `depth` animate, the *ordering*
   consequence has to be a hold: Rive states it outright — "these are Hold keys as Draw Order
   cannot be interpolated" (§2.7) — and Live2D's animatable 0–1000 draw order is the counterexample
   whose cost is a full re-sort per frame. `an`'s runtime cannot re-sort at all (§1.5), so today
   the constraint is free; write it into `an-dev-stage` before it stops being free.
6. **Scale parallax has no "maintain size" control** (§3.2). Two of the three distance-based
   systems surveyed found one necessary; the ratio encoding avoids it for placement and inherits
   it only for scale. Recorded as a deliberate omission so it is not later found as a bug.
   Corrects an earlier draft of this section: Moho, contrary to the survey brief's premise, has
   **no** such control and no "Auto-Zoom" — **UNVERIFIED**, and `mohodocs.com` does not resolve.
7. **Free-form environment documents exist in principle and the migration must not reject
   them.** `{name, description, tags}` is documented as the store's natural shape
   (`compile.py:790-794`); a 0.0.0 → 0.1.0 migration that drops unknown keys turns a
   documented-harmless warning into data loss. **LOW likelihood** (no such document exists in
   this repo), **HIGH cost** if it happens in a user project.
8. **`an credits` walks only `mall["characters"]`** (`an/credits.py:110-116`). The PR that gives
   environments art must extend it in the same change, or the credits report becomes an
   affirmative false statement about plates — which is exactly the failure
   `_reconstruct_legacy_source` (`:137-145`) was written to prevent for characters.
9. **The seven-plane budget is a declared budget, not a cited fact. UNVERIFIED.** The brief
   cites "the historical seven-layer rig as an empirical upper bound"; the survey found Godot's
   tutorial using five and no primary source for seven. Ship the warning with a rationale, not
   a citation.
10. **Supersample does not sharpen plates** (§4.2). A plate behind a push-in is upscaled from a
    1× raster. The `@2x` filename lever exists in the vendored engine, is undocumented here, and
    has no test.
11. **`AssetRef.overrides` is dead schema** (`an/ir/schema.py:109`) and will look like the
    natural place for per-shot plane tweaks. Recommend leaving it dead rather than reviving it
    as a second override channel.
12. **Nothing in `an` reads `mall["styles"]`.** `rg '"styles"' an/` finds only the mall
    construction (`an/stores/__init__.py:86`) and the staging prefix table
    (`render.py:564`). The 7b style pack is greenfield with a store already waiting, which is
    good — but it means the "resolution order" between style pack, environment and entity
    override has no existing precedent to copy.
13. **`Plane.name` is identity, and Wave 9 will discover that the hard way if it is not said
    now.** Live2D ("all the layers should be named differently"; renaming "can easily cause
    malfunctions") and Rive ("If you rename a layer in Photoshop and reimport the PSD, Rive
    treats it as a new layer") both document rename-breaks-the-reference as their central import
    hazard (§2.8). `an`'s equivalent is real today, not hypothetical: a plane name is a node path
    segment, so it is an animation target and a golden-frame dependency. Put the uniqueness rule
    in the schema (§3.4) and the stability rule in the docstring, in 7a.
14. **Two conventions clash and `an` sits between them.** Rive's hierarchy is top = front,
    Lottie's layer array is first = front, `an`'s child array is first = **back**
    (`runtime.js:143`). A `planes` list read as "first = front" by anyone coming from either
    reference format inverts the whole stack silently. State the direction in the field's own
    comment, not only in the skill.

---

## 7. Sources fetched 2026-08-25

- Godot — `https://docs.godotengine.org/en/stable/classes/class_parallaxlayer.html`,
  `class_parallaxbackground.html`, `class_parallax2d.html`,
  `https://docs.godotengine.org/en/stable/tutorials/2d/2d_parallax.html`
- Unity — `https://docs.unity3d.com/Manual/class-TagManager.html`,
  `https://docs.unity3d.com/Manual/2DSorting.html`,
  `https://docs.unity3d.com/Manual/class-SpriteRenderer.html`,
  `https://docs.unity3d.com/2022.3/Documentation/Manual/class-SpriteRenderer.html`,
  `https://docs.unity3d.com/Manual/9SliceSprites.html`,
  `https://docs.unity3d.com/2022.3/Documentation/Manual/9SliceSprites.html`
- Toon Boom Harmony 22 —
  `https://docs.toonboom.com/help/harmony-22/premium/staging/about-multiplane.html`,
  `.../getting-started/multiplane.html`,
  `.../reference/node/move/transform-loop-node.html`
- OpenToonz 1.7.1 — `https://opentoonz.readthedocs.io/en/latest/creating_movements.html`,
  `working_in_xsheet.html`, `setting_up_a_scene.html`
- Moho — `https://www.lostmarble.com/moho/manual/tut05/04/index.html`,
  `.../tut06/07/index.html`, `.../camera_tools.html`, `.../layer_tools.html`, `.../layerwnd.html`
  (the manual requires a browser `User-Agent`; a bare fetch gets HTTP 406)
- Lottie — `https://lottiefiles.github.io/lottie-docs/layers/`, `.../composition/`, `.../assets/`,
  `.../breakdown/precomps/`
- Rive — `https://rive.app/docs/editor/fundamentals/artboards`, `.../nested-artboards`,
  `.../interface-overview/hierarchy`, `.../animate-mode/animating-draw-order`,
  `.../editor/assets/psd`
- Spine — `https://raw.githubusercontent.com/EsotericSoftware/spine-scripts/master/photoshop/README.md`,
  `https://en.esotericsoftware.com/spine-import-psd`
- Live2D Cubism — `https://docs.live2d.com/en/cubism-editor-manual/psd-import/`,
  `.../precautions-for-psd-data/`, `.../psd-re-import/`, `.../reimport-psd/`, `.../parts/`,
  `.../draworder/`
- Toon Boom Harmony 22 PSD import — `.../import/about-psd-import.html`,
  `.../import/import-multi-layer-psd-file.html`, `.../import/import-psd-layout.html`
- **UNVERIFIED, not relied on:** a Harmony texture-tiling module for backgrounds; the
  OpenToonz/traditional "N/S" column field; the seven-layer multiplane rig as a historical upper
  bound; Tahoma2D docs (not fetched — OpenToonz's coverage sufficed); Moho "Auto-Zoom" and any
  depth-based scale compensation (the whole manual TOC was enumerated and neither exists);
  `mohodocs.com` (no DNS record); `esotericsoftware.com/spine-scripts` and
  `.../spine-photoshop` (both HTTP 404 — the tag list now lives at the GitHub README);
  a Krita layer-naming convention for layered export (no official documentation found;
  `reference_manual/animation.html` 404s and the docs search is a JS shell).
