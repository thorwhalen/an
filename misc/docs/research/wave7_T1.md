# Wave 7 research — T1: a camera that translates, and multiplane parallax

Measured 2026-08-25 against `main` (an 0.1.53 line, post-an#99), before any Wave 7 code.
Every code claim carries a `file:line` checked in this session; every external claim carries
the URL fetched and the sentence relied on. Where this document and the code disagree, **the
code wins** and this document gets fixed.

Authority order for Wave 7 T1 work: this file, then epic #9's Wave 7 brief. Same convention
as `wave5_research.md` / `wave6_research.md`.

**The topic in one paragraph.** The camera today is two scale channels on a container the
compiler does not know exists, and `camera.move="pan_left"` raises by design. Two findings
reorganise the wave. First, **a translating camera needs no runtime change at all**: PixiJS
composes a node as `world = position + M·(local − pivot)`, so putting the camera position in
`root.pivot_x/pivot_y` — two properties `applyProperty` already applies, on a node `nodeIndex`
already holds — *is* a 2D camera, and it composes correctly with the existing `push_in` scale
and with a camera roll. Parallax then falls out as one compile-time channel per plane,
`c_i(t) = (1 − f_i)·cam(t)`, which is **algebraically the expression Godot and Unity evaluate
at runtime** — we bake it into keyframes instead. Second, the survey of the animation tools
says the parallax rate and the apparent size are **the same number**, `f = focal_z / z`, and
every serious tool ships a *third* scalar to decouple them. That gives Wave 7 a depth model
worth having instead of an ad-hoc factor, and it names the wave's second PR precisely: today's
`push_in` scales every plane equally, which is exactly the uniform zoom the multiplane camera
was invented in 1937 to replace.

---

## 1. What exists today, code-verified

### 1.1 The camera is a scale tween on a container the compiler cannot name

`_CAMERA_MOVES` (`an/adapters/cutout/compile.py:2897-2903`) is five names mapping to
`(start_scale, end_scale)`:

```python
"hold": (1.0, 1.0), "push_in": (1.0, 1.25), "pull_out": (1.0, 0.8),
"zoom_in": (1.0, 1.5), "zoom_out": (1.0, 0.7),
```

`_add_camera_clips` (`:2906-2963`) emits, for each of `scale_x` and `scale_y` (`:2937`), one
`AnimationClipJSON` named `__camera__{shot.id}_{axis}` with **two** keyframes — the start
value eased `ease_in_out` at `t=0`, the end value at `t=duration` (`:2949-2950`) — on
`target="root"` (`:2945`), on its own `TrackJSON(target_root="__camera__")` (`:2954-2962`). A
camera is exactly two channels, two clips, two tracks.

Order matters: `_add_camera_clips` runs **last** in `compile_shot` (`:636-639`), after
`_compile_actions` (`:611`), `_add_viseme_clips` (`:621`) and the face solver
`_add_face_clips` (`:633`). Both evaluators are later-track-wins with no sorting
(`an/adapters/cutout/timeline.py:84-100` → `an/adapters/cutout/clip.py:39-50` `merge_poses`,
*"later wins per key"*; `runtime.js:518-551`, `pose[ch.target + '::' + ch.property] = v`). So a
camera channel silently overrides anything earlier on the same `(node, property)` — harmless
today because nothing else targets `root`, and a **live hazard** for any Wave 7 mechanism that
writes to nodes authors also write to (§3.3).

### 1.2 Where an unknown move raises

`_add_camera_clips:2917-2935`, in this order:

1. `shot.camera is None or shot.camera.move is None` → return (`:2917-2918`).
2. `move = shot.camera.move.strip()`; falsy → return (`:2922-2925`). Normalising *before* the
   emptiness test is deliberate: `move=""` used to fall through while `move="  "` raised
   (`tests/test_loud_discards.py:690-696`).
3. `move == "hold"` → return, *"a real, correct no-op — not an unknown move"* (`:2926-2927`).
4. Anything else not in `_CAMERA_MOVES` → `CutoutCompileError` naming this epic:
   *"A translating camera — pan, track, whip-pan — needs a real 2D camera node with per-layer
   parallax, which is planned … today the camera is a scale tween on the scene root and
   cannot move sideways."* (`:2928-2935`).

The IR validator carries a **second, hand-synced copy**, `_RENDERABLE_CAMERA_MOVES`
(`an/ir/validate.py:101-110`), duplicated so the IR does not import an adapter, checked at
`:340-347` at severity `error`. `tests/test_loud_discards.py:677-687` asserts set-equality of
the two copies. Four camera tests exist and pass (`tests/test_loud_discards.py:66-103`,
`:690-696`; run this session: `4 passed`), plus `:106-130`, which greps the `Camera` model's
own comment block and fails if it quotes a move name the compiler lacks without a same-line
disclaimer.

### 1.3 How node transforms compose

**Python side (authoring form).** `TransformJSON` (`an/adapters/cutout/serialize.py:52-79`)
is the per-node property bag — `x, y, rotation, scale_x, scale_y, skew_x, skew_y, pivot_x,
pivot_y, alpha` — and its field defaults are the **SSOT for every property's rest value**:
`_property_rest_values()` reads them off the model rather than restating them
(`compile.py:239-269`), and `RUNTIME_APPLIED_PROPERTIES` (`:272-282`) is derived from that.
`NodeJSON` (`serialize.py:127-134`) is `name / transform / visual / children`. **There is no
depth, z, layer or parallax field anywhere in the contract.**

**Runtime side.** `buildSceneTree` (`runtime.js:126-145`) makes one `PIXI.Container` per node,
sets `container.name = path`, calls `applyTransform`, indexes it at `nodeIndex[path]`.
`applyTransform` (`:277-298`) writes all ten properties straight onto the container.
`applyPose` (`:312-336`) splits each pose key on `'::'`, looks the node up, and **throws with
the full known-path list** if it is missing (`:322-333`). `applyProperty` (`:456-497`) is a
static switch over exactly those ten names — `pivot_x`/`pivot_y` at `:464-465` — and anything
else is treated as a swap-set name and throws if the node does not declare it (`:469-495`).

**The actual matrix algebra**, read out of the shipped bytes rather than from memory
(`an/data/cutout_runtime/vendor/pixi.min.js:15`; PixiJS v7.4.2, MIT, vendored — see
`an/data/cutout_runtime/index.html:28-33` and `vendor/pixi.LICENSE.txt`):

```js
updateLocalTransform(){ … t.a=this._cx*this.scale.x, … ,
  t.tx=this.position.x-(this.pivot.x*t.a+this.pivot.y*t.c),
  t.ty=this.position.y-(this.pivot.x*t.b+this.pivot.y*t.d) … }
updateTransform(t){ … // concat the parent matrix with the objects transform
  r.tx=e.tx*s.a+e.ty*s.c+s.tx, r.ty=e.tx*s.b+e.ty*s.d+s.ty … }
```

i.e. **`local = translate(position) ∘ M ∘ translate(−pivot)`** with `M = rotate ∘ scale`, and
**`world = local × parent.world`**. Two consequences the whole of §3 rests on:

- `pivot` is subtracted **through** `M`, so a pivot offset is itself scaled and rotated;
- setting `pivot` alone **moves the rendered content** by `−M·pivot`. It is not a no-op
  re-anchoring.

### 1.4 Is any node "the camera"? — Yes, and it is invisible to the compiler

`anLoadScene` creates its own outer container after the app (`runtime.js:648-656`):

```js
const root = new PIXI.Container();
root.x = width / 2;  root.y = height / 2;   // centre the scene
app.stage.addChild(root);
root.name = 'root';  nodeIndex['root'] = root;
```

then attaches the compiled document's children directly to it, **skipping the compiled `root`
node and deliberately not applying its transform** (`:657-668`): *"The Python compiler's
top-level node is a synthetic 'root' container that just holds the entities. Skip indexing it
… Do NOT apply its transform."* This is invariant #2 in
`misc/docs/architecture_as_built.md:244`.

So there are **two** things called `root` and only one is addressable:

| | built by | in `nodeIndex` | transform honoured | in `vocab.paths` |
|---|---|---|---|---|
| the JS root (`x=W/2, y=H/2`) | `runtime.js:648-656` | **yes**, as `"root"` | yes | **no** |
| the compiled `NodeJSON(name="root")` | `compile.py:727` | no | **no — ignored** | no |

`_runtime_node_paths` (`compile.py:343-360`) mirrors the skip (`:353-357`); `_swap_vocabulary`'s
walk does the same (`:440-448`). So **`"root"` is not in `vocab.paths`** — the compiler's own
known-target set does not contain the one node the camera targets. `_add_camera_clips` gets
away with it by building its clips directly instead of going through `_compile_actions`.

**And so does an author.** `_check_swap_action` returns early for any property in
`_PROPERTY_REST_VALUES` (`compile.py:1888-1889`), documented at `:1884-1885` as *"Transform
properties pass through untouched — their targets stay runtime-checked, as before."* Verified
empirically this session:

```
Shot(actions=[set_("root", "pivot_x", 25.0, at=0.0)])  →  compiles, one channel
  {"target": "root", "property": "pivot_x", "keyframes":[{"time":0.0,"value":25.0,"easing":"step"}]}
  tracks: [('root', ['__set__0'])]
```

**A hand-authored camera already exists and is undocumented.** Wave 7 is not adding a
capability to the runtime; it is naming, validating and giving a formula to one that is
already reachable.

### 1.5 The shot's coordinate origin

- Root-local `(0, 0)` is the **canvas centre**, because the JS root sits at `(W/2, H/2)`
  (`runtime.js:650-651`) and its scale is 1 until the camera animates it. At `scale = 1`, one
  root-local unit is one output pixel.
- Characters are spread on x by `_layout_character_positions(n, spread=220.0)`
  (`compile.py:836-851`) — `[]`, `[0.0]`, `[-110, +110]`, … — set onto `sub.transform.x` at `:715`. Invariant #3, `architecture_as_built.md:246`.
- Environments are built **first** so they sit behind characters (`compile.py:699-705`); z is
  child-array order only. **Nothing in `runtime.js` sets `zIndex` or `sortableChildren`** —
  there is no depth sorting of any kind.
- The backdrop is one node per environment entity with exactly two children, `sky` and
  `ground`, each a `rect` of `huge = 4000.0` on both axes (`compile.py:810-830`), offset
  vertically by `ground_y` from a five-entry preset table (`_ENV_PRESETS`, `:732-738`). The
  comment at `:808-809` says why: *"The runtime centers root at canvas/2 and applies camera
  scale, so 4000px wide rects will always cover."* — a statement written for **zoom** that a
  **pan** invalidates (§6.2).
- `width`/`height` reach the compiler from `ctx.resolution`
  (`an/adapters/cutout/render.py:306-314`) and land in `meta` only; **no keyframe value
  anywhere depends on the output resolution today.**
- A `prop` entity raises (`compile.py:717-724`); an environment override key the renderer does
  not read **warns** (`:789-803`) — and `.claude/skills/an/SKILL.md:54` claims it *"raises"*,
  which is stale and load-bearing: a Wave 7 `planes:` key on an environment record would today
  be warned about and silently dropped.

### 1.6 How the runtime samples per frame

`render.py:698-701`: `for i in range(total_frames): t = i / float(fps);
page.evaluate("(t) => window.anSetTime(t)", t)`, with `total_frames =
max(1, round(shot.duration * ctx.fps))` (`:390`). `anSetTime` (`runtime.js:675-681`) is
`evaluateTimeline(t)` → `applyPose(pose)` → `app.render()`. `evaluateTimeline` (`:518-551`)
walks tracks in order, clips in order, evaluating every channel into
`pose[target::property]`. Application order within a frame is then re-sorted by depth
(`poseKeysInApplicationOrder`, `:304-311`) — shallowest-first, then lexicographic — because
object key order is emission order and *"the golden-frame work downstream needs a frame's pose
application to be deterministic."* `"root::pivot_x"` has zero `/` in its target, so it sorts
**first**, ahead of every entity.

There is **one applier and it is `runtime.js`** (an#86; `CLAUDE.md`, and `compile.py:277` —
*"when the Python applier was deleted (an#86)"*). Python keeps two *evaluators* —
`channel.py::evaluate` (the executable spec of `evaluateChannel`, pinned behaviourally against
the shipped JS under node by `tests/test_cutout_channel_parity.py:1-27`) and
`timeline.py::evaluate_timeline` — but **nothing on the Python side turns a pose into a screen
position.** `tests/test_loud_discards.py:500-519` asserts *set equality in both directions*
between the runtime's `applyProperty` switch cases and `RUNTIME_APPLIED_PROPERTIES`.

### 1.7 What the contract hash covers

`scene_contract_sha256` (`an/bench/contract.py:53-74`) is
`sha256(json.dumps(scene_json, sort_keys=True, separators=(",",":")))` over the **whole staged
compiled document** — *"Animation keyframe floats and asset paths are in"* — and
`scenes_contract_sha256` (`:76-101`) hashes the ordered list of per-shot digests for a
multi-shot scene, returning the single digest unchanged for a one-shot scene. `an bench
--compare` **refuses rows whose hash differs**, so any change to the compiled JSON of an
existing corpus scene retires every committed ledger row as evidence, with or without a pixel
change. This constraint killed the first draft of the Wave 6 face solver
(`wave6_research.md` §14, §15) and it binds Wave 7 identically.

`CutoutSceneMetaJSON` already carries two **omit-when-unset** fields for exactly this reason —
`step_hz` and `gaze_seeds`, popped by a `model_serializer` when unset/empty
(`serialize.py:283-290`) — the precedent any new camera metadata must follow.

---

## 2. Survey, with fetched sources

### 2.1 PixiJS v7: no camera exists, and `pivot` is the one

- `https://pixijs.download/v7.4.2/docs/PIXI.DisplayObject.html` — *"The center of rotation,
  scaling, and skewing for this display object in its local space. The `position` is the
  projection of `pivot` in the parent's local space. By default, the pivot is the origin
  (0, 0)."* → **setting `pivot` moves the rendered content** unless `position` moves with it.
  Confirmed independently against our own vendored bytes (§1.3).
- Same page — *"scale — Scaling. … the display object is scaled before rotated or skewed.
  **The center of scaling is the pivot**."* → pan and zoom compose on one node, no second
  container needed.
- `https://pixijs.io/guides/basics/scene-graph.html` — *"an object's position is relative to
  its parent, so if a parent is set to an x position of 50 pixels, and the child is set to an
  x position of 100 pixels, it will be drawn at a screen offset of 150 pixels."*
- **Negative finding, verified rather than assumed:** the v7.4.2 docs index
  (`https://pixijs.download/v7.4.2/docs/index.html`, 992 KB, 220 class pages) contains **zero**
  case-insensitive occurrences of "camera", against 431 of "DisplayObject";
  `PIXI.Camera.html` returns HTTP 403. **PixiJS v7 core ships no camera, viewport or
  view-matrix abstraction.**
- PixiJS's own v8 launch post frames the pre-v8 idiom by contrast
  (`https://pixijs.com/blog/pixi-v8-launches`): *"…a true 2D hardware-accelerated camera …
  rather than moving the world itself."* → before v8, a Pixi camera **is** moving the world.
- `pixi-viewport` (`https://raw.githubusercontent.com/davidfig/pixi-viewport/master/README.md`)
  — *"A highly configurable viewport/2D camera designed to work with pixi.js."*, MIT (LICENSE
  + `package.json` + README agree). `src/Viewport.ts:160`: `export class Viewport extends
  Container`; `moveCenter` (`:491-497`) sets `newX = ((this.worldScreenWidth / 2) - x) *
  this.scale.x`, and `get left() { return -this.x / this.scale.x }` (`:894-933`). **The camera
  is literally the container's own negated position/scale.** Version trap: `master` is 6.x with
  `peerDependencies: {"pixi.js": ">=8"}`; a v7 project must pin `pixi-viewport@5.x`.

### 2.2 Godot: the formula, and the convention stated verbatim

- `https://docs.godotengine.org/en/stable/classes/class_parallax2d.html`, `scroll_scale`:
  *"Multiplier to the final `Parallax2D`'s offset. Can be used to simulate distance from the
  camera."* … *"For example, a value of `1` scrolls at the same speed as the camera. A value
  greater than `1` scrolls faster, making objects appear closer. Less than `1` scrolls slower,
  making objects appear further, and a value of `0` stops the objects completely."*
- **The source, which is the formula** —
  `https://github.com/godotengine/godot/blob/ce139f75773d3fb36dac8a620ddc5849240b64aa/scene/2d/parallax_2d.cpp#L102-L141`:
  `scroll_ofs = screen_offset; … scroll_ofs *= scroll_scale; … scroll_ofs.x = screen_offset.x
  + scroll_offset.x - scroll_ofs.x; … set_position(scroll_ofs);` → **`position =
  screen_offset·(1 − scroll_scale) + scroll_offset`**, i.e. apparent on-screen displacement
  `= −scroll_scale · camera_delta`. The limit clamp is applied to the **camera term before the
  multiply** (`#L112-L118`).
- The deprecated pair carries the same algebra —
  `https://github.com/godotengine/godot/blob/9dc231366d4c80affbee089b1a6e908455e3d1fd/scene/2d/parallax_layer.cpp#L109-L133`:
  `new_ofs = p_offset * motion_scale + motion_offset * p_scale + orig_offset * p_scale`, with
  `p_offset ≈ −camera_position`. Both classes now say *"Deprecated: Use the `Parallax2D` node
  instead."*
  **Citation caution:** `ParallaxLayer.motion_scale`'s documented text is only *"Multiplies the
  `ParallaxLayer`'s motion. If an axis is set to `0`, it will not scroll."* — the
  slower/faster convention is **not** on that page. Do not cite it for a directional claim.
- `repeat_size` / `motion_mirroring` is the infinite-scroll term: *"Repeats the `Texture2D` of
  each of this node's children and offsets them by this value. When scrolling, the node's
  position loops … If an axis is set to `0`, the `Texture2D` will not be repeated."* The old
  `ParallaxLayer` draws **at most two instances** (*"the parallax layer only draws 2 instances
  of the layer at any given time"*); `Parallax2D` wraps by modulo instead.
- The official tutorial (`https://docs.godotengine.org/en/stable/tutorials/2d/2d_parallax.html`)
  ships a **five-layer ladder** worth stealing as a default: *"(0.7, 1) - Forest / (0.5, 1) -
  Hills / (0.3, 1) - Lower Clouds / (0.2, 1) - Higher Clouds / (0.1, 1) - Sky"* — X-only
  parallax with Y pinned at 1.
- Godot is MIT (`https://github.com/godotengine/godot/blob/4.4-stable/LICENSE.txt`); docs are
  CC BY 3.0 (page footers). Quoting prose and reading the source are both safe.

### 2.3 Unity: same algebra, and the brief's z-formula is folklore

**No official Unity page states a parallax formula.** Searches restricted to `learn.unity.com`
/ `docs.unity3d.com` / `unity.com` returned only clipping-plane API pages. All sources below
are **COMMUNITY**, quoted from fetched code.

- Explicit per-layer factor, the widely-copied `ParallaxEffect` shape
  (`https://github.com/Juliusprojects/MMP/blob/5db5c95954f89199cf02224dc9c5e97015c2927a/MMP/Assets/Parallax.cs`):
  `float distX = (cam.transform.position.x - startPosX) * (1 - parallaxEffectX);` →
  `layerPos = layerStart + cameraTravel·(1 − factor)` — **algebraically identical to Godot**,
  same convention.
- z-derived, variant 1
  (`https://github.com/yalza/UnityCommonScripts/blob/da3e5edad5c746ef11732d0f06a0a47865ea9b61/ParallaxEffect.cs`):
  `_parallaxFactor => Mathf.Abs(_zDistanceFromTarget) / _clippingPlane;` then
  `newPosition = _startingPosition + _camMoveSinceStart * _parallaxFactor;` → **the complement
  of Godot's convention**: `f_unity_z ≡ 1 − f_godot`. Any cross-engine table listing both
  without saying so is wrong in half its rows.
- z-derived, variant 2 (Brackeys lineage,
  `https://github.com/intrepion/brackeys-unity-2d-platformer/blob/d72c4a29fd5156ab53d37bd9c53ba5131cce086b/Assets/Parallaxing.cs`):
  `parallaxScales[i] = backgrounds[i].position.z * -1;` applied as a per-frame **delta** then
  smoothed with `Vector3.Lerp` → **not a pure function of camera position**; frame-rate- and
  history-dependent. Disqualified for `an` on determinism grounds alone.
- **The specific formula in the brief — `parallaxFactor = 1 - (1 / (z_layer - z_camera))`, and
  the `z/(z - cameraZ)` variant — is UNVERIFIED.** Zero hits across four GitHub code-search
  spellings and web search. The z-derived normalisations actually in circulation are
  `|Δz| / clippingPlane` and bare `−z`. **Do not attribute `1 − 1/z` to a source.** (§2.5 shows
  it is a reparameterisation of the model the animation tools do use, so nothing is lost by
  spelling it the tools' way instead.)

### 2.4 The multiplane camera in 2D animation software

**Toon Boom Harmony** (`https://docs.toonboom.com/help/harmony-22/premium/staging/about-multiplane.html`)

- *"In live action, when the camera moves around in a scene, objects near the camera will
  appear to move by a greater distance than objects far from the camera. In 2D animation,
  multiplanes can be used to achieve a similar effect without having to use 3D."*
- *"In order to move a layer further from or closer to the Camera, you must change its position
  on the Z-axis."* … *"Positioning your element closer to the camera makes them appear bigger,
  and moving them further makes them appear smaller. **It is also possible to move elements on
  the Z-axis without affecting their apparent size by using the Maintain Size tool**"*.
- *"Just like with other transformations, transformations on the Z-axis on a parent layer will
  also affect the apparent position on the Z-axis of its child layers."* → Z composes through
  parenting.
- The camera is a node, and deliberately not itself animatable
  (`.../camera/about-camera.html`): *"By default, a scene does not have a camera layer."* …
  **"The Camera layer is static which means that if you need to animate it, you must put it
  under a parent peg."**
- *(A false lead worth recording: "Auto-Apply" appears on none of the multiplane/camera pages;
  in Harmony that term belongs to the Layer Properties editor and is unrelated.)*

**OpenToonz / Tahoma2D** (`https://raw.githubusercontent.com/opentoonz/opentoonz_docs/master/source/creating_movements.rst`,
rendered at `https://opentoonz.readthedocs.io/en/latest/creating_movements.html`) — **the
cleanest data model of the four.**

- *"Each scene has a series of objects that can be transformed; they can be Xsheet columns (or
  Timeline layers), pegbars, cameras, or the table."* … *"cameras can be linked to any object
  in order to create complex shots, for example with a camera following the movement of a
  character in the scene."*
- Z in field units: *"By default all the pegbars and columns are on the table: their Z position
  is equal to the number of horizontal fields defined for the default camera… By increasing the
  field value, objects are placed farther from the camera; by decreasing it, objects are placed
  closer to the camera; at zero they are at the same Z position as the camera, and for negative
  values they are behind the camera."*
- **The decoupling scalar, stated exactly:** *"The size of the objects changes according to its
  Z position, like in a real 3D environment… To keep control of this behaviour it's possible to
  define an additional Z position value in the tool options bar, **that sets the position at
  which the object has to keep its original size**."* … *"if you want a column content to keep
  its original size when placed at the Z position 8, also enter 8 as the value in brackets."*
- Z overrides declared stacking: *"In case a column/layer's Z position is edited, columns/layers
  closer to the camera will be composited on top of others, ignoring both its Xsheet/Timeline
  order and its SO value."*
- *(Correction to a common premise: Z lives in the **Animate tool's options bar**, not an Xsheet
  column header, and the current docs use X/Y, not the legacy `N/S` / `E/W` naming.)*

**Moho** (`https://www.lostmarble.com/moho/manual/camera_tools.html`, `.../tut05/04/index.html`,
`.../layerwnd.html`, `.../tut06/07/index.html`)

- *"Although layers in Moho are primarily 2D, Moho's camera can be moved around in true 3D
  space."* … *"By giving the project's layers different depth values, you can create parallax
  (depth) effects."*
- *"Positive depth (or Z) values are closer to the camera (in the direction out of your screen),
  while negative values point away from the camera (into the screen)."* → **the opposite sign
  convention to OpenToonz and After Effects.**
- Depth sorting is an explicit opt-in, separate from depth itself: *"The 'Sort layers by depth'
  checkbox allows sub-layers to move in front of and behind each other during an animation.
  Normally, layers are drawn in the order they appear in the Layers panel."*
- The intuition, stated plainly: *"It's like driving in a car - nearby objects go by quickly,
  while distant objects seem to move slowly."*
- **PARTIALLY VERIFIED:** the words "orthographic" and "perspective projection" appear nowhere
  in the Moho docs. Perspective is established only *indirectly* — *"Technically, the Zoom
  Camera tool is changing the field of view angle (or focal length) of the virtual camera."*
  Treat "Moho's camera is perspective" as sound but inferred.

**After Effects** — **`helpx.adobe.com` was unreachable from the survey environment** (WebFetch
timeouts on four URL variants; `curl` returned HTTP 000). Quotes are from Internet Archive
snapshots of the same Adobe pages, both `HTTP 200`; **re-verify against live Adobe before
citing as primary.**

- `http://web.archive.org/web/20260426223842/https://helpx.adobe.com/after-effects/using/3d-layers.html`
  — *"When you convert a layer to 3D, a depth (z) value is added to its Position, Anchor Point,
  and Scale properties…"* … *"the origin of the coordinate system is at the upper-left corner;
  x (width) increases from left to right, y (height) increases from top to bottom, and z
  (depth) increases from near to far."* … *"When rendering for final output, 3D layers are
  rendered from the perspective of the active camera."*
- `http://web.archive.org/web/20260405153454/https://helpx.adobe.com/after-effects/using/cameras-lights-points-interest.html`
  — **the perspective-divide law, verbatim, and the single most useful sentence in this
  survey:** *"Zoom — The distance from the lens to the image plane."* … *"In other words, a
  layer that is the Zoom distance away appears at its full size, a layer that is twice the Zoom
  distance away appears half as tall and wide, and so on."*
- Orthographic in AE is a property of **working views only** — *"The working 3D views include
  the custom views and the fixed orthographic views (Front, Left, Top, Back, Right, or
  Bottom)."* Final render is always perspective. AE offers **no size compensation**; the author
  hand-corrects scale after moving in Z.

**The Disney multiplane camera** (`https://en.wikipedia.org/wiki/Multiplane_camera`, and the
Walt Disney Family Museum's *Multiplane Educator Guide*,
`https://www.waltdisney.org/sites/default/files/MultiplaneGuideCurriculumPacket_Final.pdf`)

- *"The multiplane camera is a motion-picture camera … that moves a number of pieces of artwork
  past the camera at various speeds and at various distances from one another. This creates a
  sense of parallax or depth."* … *"the further away from the camera, the slower the speed."*
- *"An advanced multiplane camera was developed by William Garity for the Disney Studios to be
  used in the production of Snow White and the Seven Dwarfs. The camera was completed in early
  1937 and tested in a Silly Symphony called The Old Mill, which won the 1937 Academy Award for
  Animated Short Film."* Up to **seven** planes of oil-on-glass artwork, a crew of up to a dozen.
- **The problem it solved, stated as the failure mode of a uniform zoom** (museum guide):
  *"To create the effect of traveling toward or through the landscape, you might use the zoom
  feature on the video camera… Rocks, trees, and bushes will appear to grow larger, but so will
  mountains, clouds, the sun, and everything else on the two-dimensional painting. The illusion
  of traveling forward in the landscape could not be accomplished realistically using this
  method."*
- **The worked example, which is our acceptance test** (museum guide): *"the trees and the
  fence would be placed on the plane closest to the camera, the house on the hillside would be
  placed on the middle plane, and the moon would be placed on the plane furthest from the
  camera… **The moon remains the same size rather than growing unrealistically larger as it
  would with a simple zoom.**"*
- Historically the camera stayed put and the *planes* moved — mathematically identical to moving
  the camera, and it explains why every tool models plane depth as a first-class per-layer value
  rather than deriving it from a camera path.

### 2.5 The ONE model that fits `an`

Every formula surveyed is **the same affine expression under different framings**:

```
screen_displacement_of_plane_i  =  − f_i · camera_displacement · zoom
```

The engines differ only in *where* they put the `(1 − f)`: Godot and Unity idiom (a) move the
layer by `cam·(1 − f)` inside a camera-locked frame; Unity's UV variant pins the quad to the
camera and scrolls UVs by `cam·f`. **`an` gets the camera-locked frame for free**: the JS
`root` *is* that frame, and `pivot` is the camera term (§1.3). So:

```
root.pivot   = (cam_x, cam_y)                   # the camera pose  — one node, two channels
plane_i.x    = x0_i + (1 − f_i) · cam_x         # the parallax compensation — Godot's formula
plane_i.y    = y0_i + (1 − f_i) · cam_y
⇒ screen_i   = (W/2, H/2) + S · (x0_i − f_i · cam)
```

with `f = 1` meaning "world-locked, rides the camera at full rate" (Godot's
`scroll_scale = 1`) and `f = 0` meaning "frozen on screen" (a sky at infinity).

**Verified numerically this session** by porting the two vendored lines from
`vendor/pixi.min.js:15` into Python and composing `world = local × parent.world` at 320×240
(camera at `cam_x = 80`, planes all at `x0 = 0`, so screen 160 is "unmoved"):

| plane | `f` | compensation `c_i` | screen_x @ S=1 | screen_x @ S=1.25 |
|---|---|---|---|---|
| sky | 0.0 | +80 | 160.00 (unmoved) | 160.00 |
| hills | 0.5 | +40 | 120.00 | 110.00 |
| charlie | 1.0 | 0 (**no channel**) | 80.00 | 60.00 |
| bush | 2.0 | −80 | 0.00 | −40.00 |

Displacement ratios `0 : 0.5 : 1 : 2` — exactly `f` — and they hold under zoom with the
parallax offset itself scaled by `S`, which is correct multiplane behaviour. This is the
epic's done-when, computed from the algebra before a pixel is rendered.

**Where `f` comes from — and the survey's decisive contribution.** After Effects gives the law
in one sentence: *"a layer that is the Zoom distance away appears at its full size, a layer that
is twice the Zoom distance away appears half as tall and wide."* So with a camera whose image
plane is at distance `focal_z`:

```
f_i = focal_z / z_i          # the parallax rate AND the perspective size factor — one number
```

`f = 1/(1+depth)` (the brief's spelling) is this same function with `depth = z/focal_z − 1`. It
is not wrong; it is just non-standard, it hides the size half of the model, and no surveyed tool
spells it that way.

**And every serious tool ships a third scalar to decouple size from motion**, because authors
want a plane's *look* preserved while its *motion* changes: OpenToonz's *"additional Z position
value … that sets the position at which the object has to keep its original size"*, Harmony's
Maintain Size tool. AE has none and makes the author fix scale by hand — do not copy AE here.
So:

```
render_scale_i = z_ref_i / z_i        # z_ref default = z_i  ⇒ scale 1.0 ⇒ NO channel emitted
                                      # z_ref = focal_z      ⇒ true perspective (AE behaviour)
```

Under a static camera `z_i` is constant, so `render_scale_i` is constant and **no scale channel
is emitted** — the byte-identity property survives. Measured this session at `focal_z = 1`:

| plane | `z` | `f = focal_z/z` | pan of 100 px → displacement | compensation `c` |
|---|---|---|---|---|
| foreground bush | 0.7 | 1.4286 | −142.86 px | −42.86 |
| charlie (action plane) | 1.0 | 1.0000 | −100.00 px | 0 (no channel) |
| house on hillside | 2.5 | 0.4000 | −40.00 px | +60.00 |
| moon | 10.0 | 0.1000 | −10.00 px | +90.00 |

**What each authoring form implies**

| form | reads as | gives you | costs |
|---|---|---|---|
| explicit `parallax: f` (Godot) | "how fast does this plane slide" | motion only; author sizes the art | no size model; a dolly has nothing to work from |
| `z` + `focal_z` (Harmony / OpenToonz / AE / Moho) | physical staging | motion **and** size from one number; a dolly is free | hides a division; `z ≤ 0` must be a validate error, not a sign flip |
| `1/(1+depth)` (the brief) | — | same as `z` under a shifted origin | non-standard spelling, unsourced, and it hides the size half |

**Three decisions that must not be collapsed into one** (the clearest cross-tool lesson):
parallax rate, apparent size, and **paint order** are independent. OpenToonz couples order to
`z` with declared order as tiebreak; Moho makes it an opt-in checkbox; Harmony leaves paint
order alone. For a cutout rig — where sibling ordering is *authored*, not spatial — the default
must be **authored order wins**. In `an` that is also the only option available: the runtime
sets no `zIndex` and no `sortableChildren` (§1.5), so depth sorting is a real runtime change.

**Pin the sign convention in the schema docstring.** All four tools disagree — Moho `+z` =
toward the viewer; AE and OpenToonz `+z` = away. Recommend **`+z` = away from the camera**
(the majority, and it makes "further ⇒ larger `z` ⇒ smaller `f` ⇒ slower" read forwards).

---

## 3. Design options for `an`

### 3.0 The constraints any option must survive

1. **One applier.** `runtime.js` alone turns a pose into pixels (an#86; `compile.py:277`;
   `tests/test_loud_discards.py:500-519`). A property the runtime applies that the compiler
   cannot emit is dead runtime code; the converse is a hard render failure; **both directions
   fail that test.**
2. **`an/determinism.py`.** `capture_violations` judges a plain dict from
   `anDeterminismReport` (`runtime.js:715-729`) — capture page, tickers, filtered nodes, node
   count. No option below attaches a filter, starts a ticker or changes the page, so the
   perimeter is untouched — but `node_count` is a *recorded provenance field*, so any option
   that inserts wrapper nodes moves it.
3. **JSON identity.** Every corpus scene's `scene_contract_sha256` must be unchanged unless
   deliberately re-blessed (`contract.py:53-74`; the Wave 6 precedent, `wave6_research.md` §14).
   **No corpus scene declares a camera** (verified: `rg camera misc/bench/corpus/*/scene.md` →
   nothing; only `examples/` use one), so the bar is *"a scene that does not translate compiles
   byte-identically"*.
4. **The evaluators' parity test.** `tests/test_cutout_channel_parity.py` pins
   `channel.py::evaluate` against the real extracted JS. Keeping the camera as ordinary numeric
   keyframes leaves it untouched; adding arithmetic to `evaluateTimeline` or `applyPose` puts a
   second, unparity-tested evaluator in the browser.
5. **The step_hz exemption (an#89).** The camera is exempt **by construction**, because
   `_add_camera_clips` is its own emission site rather than a string-sniffed exception
   (`compile.py:580-585`; `tests/test_step_hz.py:235-245`, which asserts the camera clip still
   has exactly 2 keyframes and non-`step` easing under `step_hz=10`). Whatever emits parallax
   must live at that same site, or a stepped plane will judder under a smooth camera — the exact
   failure the exemption exists to prevent.
6. **The browser lane is opt-in.** Runtime changes are verified only on a labelled PR
   (`CLAUDE.md`; `misc/docs/adr_ci_verification_perimeter.md`). Compiler changes are verified on
   **every** PR. This asymmetry in evidence is decisive below.

### 3.1 Option A — a real camera node the runtime knows

Add `camera: {x, y, zoom, rotation}` to `CutoutSceneMetaJSON` plus `parallax`/`z` on
`NodeJSON`; `runtime.js` reads them each frame and applies the offset per node. This is what
Harmony and OpenToonz do (§2.4: the camera is a node in the graph).

**In favour:** the JSON is small and reads like a camera; per-plane offsets never appear as
keyframes, so a 300-frame pan does not grow the document; a future canvas UI could scrub the
camera without recompiling; it matches the tools' own architecture.

**Refutations.**
- It moves the parallax formula into the half of the system CI does not check by default
  (constraint 6). The Wave 6 standing rule applies: never write that a rendering behaviour is
  verified in CI.
- The *arithmetic* becomes browser-only. There is no Python spec of it, unlike
  `evaluateChannel` (`channel.py`) and `wrapTime` (`clip.py::_wrap_time`), each an executable
  spec pinned against the shipped JS. It would be the first piece of render semantics with no
  Python twin.
- It makes the compiled document no longer self-describing: `scene_contract_sha256` would cover
  a camera *declaration* rather than the per-plane motion it implies, so two different parallax
  laws would hash identically. The digest exists to deny exactly that.
- An animated camera then needs its own keyframe machinery in the runtime — a second timeline —
  or it must be channels after all, at which point Option C is what you have.

**Verdict: refuted** for the arithmetic. Note what Harmony's rule actually says, though —
*"The Camera layer is static … you must put it under a parent peg"* — i.e. even there the camera
carries no animation; motion is an ordinary transform on an ancestor. Option C is that rule with
the sign flipped: `root` is the ancestor, and the camera is its inverse.

### 3.2 Option B — pure compile-time: the camera becomes per-plane channels

The compiler computes `cam(t)` and emits, for **every** drawable top-level node, a channel
`x → x0_i − f_i·cam_x(t)`. The runtime learns nothing.

**Refutations.**
- **It puts a channel on every character**, because `f = 1` still yields `−cam_x`. Those are
  exactly the nodes authors tween. The evaluators are later-wins with no additive blending
  (`timeline.py:12-17` still says *"Additive blending lands in 2B"*) and camera clips are
  appended last (`compile.py:636-639`), so a pan would **silently delete** every authored `x`
  tween in the shot. That is the loudest possible violation of "either works or raises".
- Even with the collision fixed by raising, the common case becomes "you cannot move a character
  during a camera move", which is unusable.
- Document size: at 24 fps a 5 s pan is 120 keyframes × 2 axes × N nodes, all inside the
  contract hash. Fine for 3–5 planes, not for every entity.

**Verdict: refuted as stated.** Its correction is 3.3.

### 3.3 Option C (RECOMMENDED) — camera on `root.pivot`, compensation on the planes only

Two compile-time pieces, one emission site, **zero runtime change**:

1. **The camera pose** goes onto the node the runtime already indexes as `"root"`:
   `pivot_x`/`pivot_y` for translation, the existing `scale_x`/`scale_y` for zoom, `rotation`
   for a roll. All five are already in `applyProperty` (`runtime.js:456-497`) and in
   `RUNTIME_APPLIED_PROPERTIES` (`compile.py:272-282`), so the set-equality drift gate stays
   green with no edit.
2. **Parallax** is one channel per plane whose factor differs from 1:
   `c_i(t) = (1 − f_i)·cam(t)`, added to the plane's authored `x0`. A plane at `f = 1` gets
   **nothing**, so characters — which default to `f = 1` — are never touched and can be tweened
   freely during a camera move.

Why `pivot` and not `x`/`y` on `root`: an `x` channel would have to carry `W/2 − S·cam_x`,
which (a) bakes the output resolution into keyframe values, making the compiled document
resolution-dependent for the first time, (b) couples pan to zoom *inside* the keyframe values so
the two cannot be authored independently, and (c) fights the runtime's own `root.x = width/2`
centring (`runtime.js:650`), double-specifying it. `pivot` avoids all three because the engine
subtracts it *through* `M` (§1.3) — verified numerically in §2.5.

| constraint | how Option C meets it |
|---|---|
| one applier | nothing is applied that was not applied before; only *values* are new |
| determinism | no filter, no ticker, no page change; `node_count` unchanged (no new nodes) |
| JSON identity | a shot with no translation emits no pivot channels and no compensation → **byte-identical**; all seven corpus contract hashes hold, every committed golden holds |
| parity test | numeric keyframes only; `evaluateChannel` / `evaluate` untouched |
| step_hz | emitted from `_add_camera_clips` (renamed `_add_stage_clips`), which *is* the exemption; `tests/test_step_hz.py:235-245` generalises to the new channels |
| browser lane | entirely compiler-side, therefore checked on **every** PR |

**Composition with the existing push-in — and the pivot question, answered.** Pivot and scale
live on the same node, and Pixi scales *about the pivot* (`PIXI.DisplayObject` docs, §2.1; the
algebra, §1.3). So the zoom centre **moves with the camera**: a push-in during a pan zooms
toward whatever the camera is looking at, not toward a fixed frame centre. Concretely: the
effective zoom centre is the frame centre when the camera is at the origin, and the camera
target otherwise — **never the root origin as a fixed point**. That is the correct cinematic
default and it costs nothing. A fixed-frame-centre zoom would need a second node and should not
be the default.

**The collision rule.** A compensation channel targets a node the author might also target, and
because the evaluators are later-wins with camera clips appended last, the failure would be
*silent*. So the stage emitter must **detect** an authored `set`/`tween` on `(node, x|y)` for
any node carrying a non-unity factor and **raise** a `CutoutCompileError` naming both, in the
house style of `compile.py:2928-2935`. Additive folding — resample the authored tween onto the
camera's grid and sum, the way `_add_face_clips` sums contributors — is the eventual right
answer but is a separate decision (§6.3). **Not Wave 7.**

**A micro-option considered and rejected:** putting a character's compensation on its own
`pivot_x` instead of `x`, to dodge the collision. It works only while the entity node has unit
scale and zero rotation, because the pivot is subtracted through `M` — the moment a character
scales, its parallax offset scales with it. Cute, wrong, and wrong *silently*.

### 3.4 Option D — a hybrid with wrapper nodes

Insert a `plane_<name>` container per depth, carry the compensation there, re-parent entities
into it.

**Refutation:** node paths are built from the chain of names (`runtime.js:127`;
`compile.py:344-359`), so `charlie` becomes `plane_mid/charlie` and every authored target in
every existing scene breaks. The compiler could rewrite targets (it owns both sides), but that
introduces a target-resolution indirection between what the author writes and what the runtime
indexes — a new concept whose first bug is invisible — and it moves `node_count` in the
determinism record. **Refuted for Wave 7.** Note that the *environment* already has the shape a
wrapper would provide: `park_bg/sky` and `park_bg/ground` are children of an
identity-transform `park_bg` (`compile.py:812-830`), so plane nodes can be added there without
touching any entity's path.

### 3.5 Option E — take `pixi-viewport`

**Refuted on four independent grounds:** `master` is v8-only (`peerDependencies: pixi.js >=8`)
so we would pin a 5.x line; it is a *runtime* camera, so §3.1's refutations all apply; it
supplies pan/zoom but **no parallax at all**; and Wave 1's ruling stands that every shipped byte
is vendored and licence-recorded — a second engine dependency for four lines of arithmetic is
not a trade worth making. Its value here is as *evidence* (§2.1) that "camera = negated
container transform" is the idiom, which is precisely what Option C does.

### 3.6 The dolly — the second half of "multiplane", and why 7b needs it

The epic's done-when concerns a **pan**, and Option C discharges it. But the museum guide names
the other half exactly: *"The moon remains the same size rather than growing unrealistically
larger as it would with a simple zoom."* Today's `push_in` scales `root`, so it scales every
plane equally. **Measured this session at `focal_z = 1` with a forward dolly of `cam_z = 0.2`:**

| plane | `z` → `z − cam_z` | `f` → `f` | apparent size, true dolly | apparent size, today's `push_in` |
|---|---|---|---|---|
| foreground bush | 0.7 → 0.50 | 1.4286 → 2.0000 | **×1.4000** | ×1.25 |
| charlie | 1.0 → 0.80 | 1.0000 → 1.2500 | ×1.2500 | ×1.25 |
| house on hillside | 2.5 → 2.30 | 0.4000 → 0.4348 | ×1.0870 | ×1.25 |
| moon | 10.0 → 9.80 | 0.1000 → 0.1020 | **×1.0204** | ×1.25 |

The moon growing 25% instead of 2% *is* the pre-1937 technique, restated in our own numbers.

Because §2.5's model makes `f` and the size the same quantity, the dolly is **cheap once the
`z` model exists**: `z_i(t) = z_i − cam_z(t)` makes both `f_i(t)` and `render_scale_i(t)`
per-frame channels on properties the runtime already applies (`x`, `y`, `scale_x`, `scale_y`).
No new node, no new property, no runtime change.

**Recommendation:** Wave 7 is scheduled as **two PRs** in the epic's own table. **7a** ships the
camera pose, `pan_*`, the plane model and the compensation channels — the done-when. **7b**
ships `dolly_in`/`dolly_out` with the moon test above as its acceptance, alongside the
`Shot.style` → `Shot.renderer` rename that Decision 3 already puts early in 7b. `push_in` /
`zoom_in` / `pull_out` / `zoom_out` keep their present meaning and their **byte-identical**
compiled documents throughout, so no committed camera scene is disturbed by either PR.

**Known deviation to document rather than fix:** under Option C without a dolly, an `f = 0`
plane still *scales* with a zoom even though it does not *translate*. A truly
infinitely-distant plane would do neither. 7b removes this for `dolly_*`; it remains true of
`push_in` by design, which is now a *stated* difference between two camera moves rather than an
accident.

---

## 4. The `Camera` IR

### 4.1 What is there now, and what is dead

```python
class Camera(_IRModel):                      # an/ir/schema.py:73-85
    position: tuple[float, float, float] = (0.0, 0.0, 0.0)
    target:   tuple[float, float, float] = (0.0, 0.0, 0.0)
    focal_length: float = 50.0
    move: str | None = None
```

`position`, `target` and `focal_length` are read by **nothing** — `rg focal_length an/ tests/`
finds one hit, the field declaration itself. They are not merely inert: the md writer dumps
`shot.camera.model_dump(exclude_none=True)` (`an/ir/sync.py:410-411`) and none of the three is
`None`, so **every camera block in every `scene.md` carries eight dead lines** —
`examples/park_bench_cartoon/scene.md:18-24` and `:54-60`, `examples/walk_demo/scene.md:18-24`,
`examples/promote_demo/scene.md:22`, plus both `ir/scene.json`s. A `Camera` model that
advertises a 3D camera while the renderer implements a scale tween is the same defect
`tests/test_loud_discards.py:106-130` was written to catch, one level up.

### 4.2 Proposed shape

Named moves stay the front door; an explicit pose is the implementation. **One code path, two
front doors** — the same shape the dialogue `[emotion]` sugar uses (`wave6_research.md` §5), and
the reason to prefer it is that it makes `pan_left` a *value*, not a branch.

```python
class CameraKey(_IRModel):
    """One keyed camera pose. `x`/`y` are root-local units (= output pixels at zoom 1)."""
    at: Seconds = 0.0
    x: float = 0.0            # camera position; +x moves the camera right, content left
    y: float = 0.0
    zoom: float = 1.0         # on-screen magnification; > 1 is closer  (root scale_x/scale_y)
    z: float = 0.0            # camera DOLLY along the depth axis, in the same units as plane z
    rotation: float = 0.0     # radians; camera roll
    easing: EasingSpec | None = "ease_in_out"

class Camera(_IRModel):
    move: str | None = None       # a named preset; sugar for `keys`
    keys: list[CameraKey] = []    # the explicit door; empty = use `move`
    focal_z: float = 1.0          # the depth at which a plane renders 1:1 and parallaxes at 1
```

Naming hazard worth stating in the docstring: **After Effects calls `focal_z` "Zoom"**
(*"Zoom — The distance from the lens to the image plane"*), while `an`'s existing vocabulary
already uses `zoom_in`/`zoom_out` for on-screen magnification. Keeping `zoom` for the
magnification and naming the AE quantity `focal_z` preserves the existing user-facing words at
the price of one documented collision. Do not silently reuse `focal_length` — it means a lens
property, not a distance, and it is one of the three dead fields being removed (§4.4).

`_CAMERA_MOVES` becomes a table of **key lists** instead of scale pairs. The five existing names
must desugar to exactly the document they produce today — a two-keyframe `ease_in_out` scale
tween on `root`, and **no pivot channels** (`x = y = z = 0` throughout must emit nothing, or
every committed camera scene's contract hash moves). New names for 7a: `pan_left`, `pan_right`,
`tilt_up`, `tilt_down`, each a two-key pose move of a fixed fraction of the frame; for 7b:
`dolly_in`, `dolly_out`. In film "pan" is a rotation and a lateral translation is a *truck*; on
an orthographic 2D stage they are indistinguishable, and the epic's own done-when says
`pan_left` — so `pan_left` it is, with the ambiguity stated in the docstring rather than
resolved by a purist rename.

Setting both doors at once must **raise**, not merge silently.

### 4.3 Where the plane depth lives

Not on `Camera` — depth is a property of the *stage*, not of the shot's camera, and every tool
surveyed makes it a first-class per-layer value (§2.4). Two additive carriers, neither needing a
schema version bump:

- **Environment records** grow `planes: [{name, z | parallax, z_ref, ...art fields}]`, consumed
  by `_build_environment_subtree` (`compile.py:741`), which already emits named children
  (`sky`, `ground`) under an identity-transform parent. This is also Wave 7's "art direction as
  data" theme landing in the same place.
  **Blocker to fix first:** an unknown environment-override key today **warns and is dropped**
  (`compile.py:789-803`) while `.claude/skills/an/SKILL.md:54` claims it raises — so `planes:`
  would be silently discarded until that is settled. Settle it in the same PR.
- **Entities** carry their depth through the existing `AssetRef.overrides`
  (`an/ir/schema.py:93-109`) rather than a new field, defaulting to the action plane
  (`z = focal_z`, `f = 1`).

Take **`parallax: float`** as the wire primitive (Godot's `scroll_scale` — unambiguous,
documented, and the only quantity 7a's done-when needs), with **`z` as the sugar**:
`parallax = focal_z / z`, plus optional `z_ref` giving `render_scale = z_ref / z` (default
`z_ref = z` ⇒ scale 1.0 ⇒ **no scale channel emitted** ⇒ byte-identity preserved). Giving both
`parallax` and `z` on one plane must raise. Progressive disclosure: a two-plane pan needs only
`parallax: 0.3`; a multiplane dolly needs `z`, and gets its size model for free.

**Paint order stays authored.** Do not couple z to draw order (OpenToonz does; Moho makes it an
opt-in checkbox; Harmony does not). In `an` it is not even available — the runtime sets no
`zIndex` and no `sortableChildren` (§1.5) — so coupling would be a runtime change. Record it as
a deliberate default with Moho's checkbox as the shape any future opt-in should take.

### 4.4 Migration, and the no-aliases rule

Removing `position` / `target` / `focal_length` is a **field removal from a serialized
document**, so it needs a registered migration, not a deletion: `_IRModel` is `extra="allow"`
(invariant #5, `architecture_as_built.md:247`), so simply dropping the fields leaves them alive
as extras and the md writer keeps emitting them. Register a `SceneIR` migration
(`an/ir/migrate.py:101-118`; `register_migration(kind, from, to)`, keyed **per document kind**
since an#77) that pops the three keys from every `timeline[*].camera`, and bump
`SCHEMA_VERSION` with it, per the standing rule (*"Never bump `SCHEMA_VERSION` without
registering a migration"*, `CLAUDE.md`). Adding `keys` / `focal_z` is additive and needs no
migration. **No `.v2`, no alias, no compat shim** — the federation's prime directive, and there
is no stored third-party data here to protect. Re-run the md writer over `examples/` in the same
PR so the eight dead lines actually leave the tree.

### 4.5 Validate rules — "either works or raises; never silently no-ops"

The done-when is a *validate* statement as much as a compile one. `an/ir/validate.py` must
report at severity `error` (so the free pre-flight and the pipeline agree, `:336-339`):

1. `move` not in the vocabulary → today's rule, unchanged (`:340-347`), with the
   `_RENDERABLE_CAMERA_MOVES` copy kept in sync by `tests/test_loud_discards.py:677-687`.
2. `move` **and** `keys` both set.
3. `keys` unsorted by `at`, any `at` outside `[0, shot.duration]`, or `zoom <= 0`.
4. On a plane: `z <= 0` (the `f = focal_z/z` pole — and note this is *not* "behind the camera",
   which OpenToonz permits and we should not), or `parallax` and `z` both given, or
   `parallax <= 0` where a plane is meant to be frozen (use `0` deliberately, reject negatives —
   a negative factor is a mirror, never what an author meant).
5. A plane depth declared on a node the shot does not build.
6. **The collision** of §3.3: an authored `x`/`y` action on a node that will carry a
   compensation channel.

And in the compiler, three loud refusals rather than silent drops: the collision (6); a `planes:`
list on an environment the renderer cannot lay out; and — as a **warning**, not an error — a
camera translation on a shot whose planes are all `f = 1`, because "pan a single-plane stage" is
a legitimate, if flat, request.

---

## 5. Verification plan

The done-when is *"a pan across a multi-plane environment produces golden frames in which the
planes have measurably moved at **different** rates — a flat 2D zoom would give identical
ratios."* That is two independent measurements, and the JSON one is load-bearing because it runs
on every PR while the pixel one runs only on a labelled PR (§3.0.6).

### 5.1 From the compiled JSON — free, offline, on every PR

Because Option C is entirely compile-time, the whole claim is a property of the document. For a
shot with camera keys and planes `i`, read `cam(t)` from the `root::pivot_x` / `root::pivot_y`
channels and `c_i(t)` from each plane's `x` / `y` channel (absent ⇒ constant ⇒ `f = 1`); then
assert, sampling at the frame instants the renderer actually uses, `t = k/fps`
(`render.py:698-701`):

1. **Rate recovery.** `f_i = 1 − Δc_i/Δcam` equals the declared factor to float tolerance, for
   every plane and every consecutive frame pair. This is the direct statement of "different
   rates", and it is exact, not a heuristic.
2. **Distinctness.** The set `{f_i}` has at least three distinct values on the corpus scene and
   its pairwise minimum separation exceeds a pinned floor — the shape
   `tests/test_expression_goldens.py:44-56` uses for presets.
3. **Zoom is not parallax.** Compile the *same* stage with `move="push_in"` and assert every
   recovered `f_i` is identically 1 (no compensation channel is emitted at all). This is the
   "a flat 2D zoom would give identical ratios" clause, asserted rather than asserted-about.
4. **JSON identity.** All seven pre-existing corpus `scene_contract_sha256` values equal the
   committed ledger row's, and the five existing `_CAMERA_MOVES` compile to byte-identical
   documents. Wave 6's PR-C made exactly this assertion (`tests/test_expression_compose.py`);
   copy its shape.
5. **Mutation-test the guard** (house rule; `CLAUDE.md`, `wave6_research.md` §15). Flip the sign
   in `c_i`; set every `f_i` to 1; drop the pivot channel; and swap `(1 − f)` for `f`. Each must
   turn a *named* test red. The last matters most: it is the exact convention error §2.3
   documents in the wild (`f_unity_z ≡ 1 − f_godot`).
6. **7b only — the moon test, in JSON.** With a `dolly_in`, assert
   `render_scale_i(t) = z_ref_i/(z_i − cam_z(t))` per plane and that the far plane's total size
   change is at least an order of magnitude smaller than the near plane's (measured: ×1.0204 vs
   ×1.4000 at `cam_z = 0.2`, §3.6).

What this does **not** prove: that the runtime turns those channels into those pixels. That is
§5.2's job, and the honest phrasing anywhere in the repo is "verified on a labelled PR", never
"verified in CI".

### 5.2 From pixels — a corpus scene, goldens, and centroids

**New corpus fixture** `misc/bench/corpus/parallax/` — a bench-owned scene, so its pixels are a
function of the repo alone (`corpus.py:130-141`) — 320×240 @ 24 fps like its neighbours, with:

- **three or four planes in flatly distinct, non-overlapping colours.** The reason is
  mechanical: a per-plane centroid is only recoverable if a plane's colour is unique in the
  frame. This is the same addressability trick `graded_field` and `saturated_outline` use.
  Depths from Godot's shipped ladder, e.g. `sky 0.1 / hills 0.3 / trees 0.7 / foreground 1.4`.
- each plane a **bounded shape**, not the full-width `huge = 4000` rect, so it has a centroid
  that moves;
- a camera pan of a fixed fraction of the frame width over the shot;
- **at least two golden times** where the planes have measurably separated. `bless_scene` refuses
  fewer than two frames and refuses a pixel-identical pair (`an/bench/golden.py:419-479`), and
  `Fixture.golden_note` (`corpus.py:113-119`) must say *what* moved and by how much.
- **7b:** the Disney case as a second shot or a sibling fixture — trees / house on a hillside /
  moon, under `dolly_in`. Its golden assertion is the museum's sentence, made numeric: the
  moon's bounding box changes size by less than a pinned ceiling while the foreground's changes
  by more than a pinned floor.

**The pixel assertion** (`tests/test_parallax_goldens.py`, modelled on
`tests/test_expression_goldens.py`): for each golden pair, recover each plane's centroid by
colour mask and assert

```
displacement(plane_i) / displacement(plane_ref)  ≈  f_i / f_ref
```

for every pair, within a floor derived from the **first bless's measured** minimum separation —
pinned as half of it, with the measured number written into the test docstring, exactly as
`MIN_PAIRWISE_CHANGED_PX = 53` was derived from a measured 106
(`test_expression_goldens.py:26-28`). Add a second, weaker assertion that the ratios are **not
all 1**, the literal refutation of the flat-zoom null hypothesis.

### 5.3 What the bench needs

- One `Fixture` entry in `DFLT_FIXTURES` (`corpus.py:189-…`) with `expect_visual_kinds`,
  `golden_frames` and a `golden_note`.
- **One diagnostic ledger row**, not a counted metric: `parallax_min_rate_ratio_spread`
  (family B, `role="diagnostic"`), following `expression_min_pairwise_changed_px`
  (`an/bench/registry.py:1080-1092`) — a render-side row that is never null on a real capture and
  counts nothing, because "the planes moved at different rates" is a **correctness** property
  with an offline proof (§5.1), not a quality metric to be traded off.
- **No new mutation lever.** A parallax lever would move `scene_contract_sha256` on every scene
  it touched, so `bench-compare` would refuse the row at comparability before any family was
  examined — the same measured reason `step_hz` earned no lever (an#89, `CLAUDE.md`). Say so in
  the registry rather than leaving its absence to be rediscovered.
- **Demos.** Repo rule: a new user-facing capability gets a clip. `_build_camera`
  (`misc/demos/build_demos.py:385-390`) already renders one shot per implemented move, and its
  blurb at `:792-805` currently reads *"The camera is a scale tween on the scene root, so it
  cannot translate"* — that string is part of the wave's diff. Add a `multiplane` demo whose
  blurb is the museum's moon sentence.

### 5.4 Docs and surfaces that must move in the same PR

`CLAUDE.md` (the camera row of the capability map); `misc/docs/architecture_as_built.md`
(invariant #2 at `:244` gains the pivot fact; §9's scene.md contract at `:317-320` gains the new
moves); `.claude/skills/an/SKILL.md:51` (the move list) and `:54` (the raises/warns claim,
§4.3); `README.md:72,87`; `an/iterate.py`'s grammar, which enumerates the legal moves for the
LLM and is itself pinned by a test (`tests/test_loud_discards.py:523-…`); and
`misc/CHANGELOG.md`.

---

## 6. Risks, unknowns, and open questions

1. **The `huge = 4000` plate is sized for a zoom, not a pan.** `compile.py:808-809` justifies the
   4000px rects with *"The runtime centers root at canvas/2 and applies camera scale."* A plane
   at `f = 0.3` under a 1500px pan slides 450px; at `f = 1.4` it slides 2100px and its near edge
   enters frame. Either the compiler computes the required plate width from
   `max|c_i| + W/(2·min S)` and **raises** when the declared art is narrower, or Wave 7 ships a
   wrap term (Godot's `repeat_size`, whose modulo form is strictly better than the deprecated
   two-instance `motion_mirroring`). **Recommend the raise for 7a**: a plate that runs out
   mid-pan is a silent wrong picture, the exact failure class this repo raises on.
2. **The collision rule is a refusal, and refusals age badly.** "You cannot tween a plane's x
   during a camera move" is correct and loud for Wave 7, and it is not a resting place. The
   additive fold is the real answer and needs its own decision, because it interacts with
   `step_hz`: the authored tween is stepped, the camera is not, and summing a stepped curve onto
   a smooth one produces a stepped result. **Do not fold in Wave 7.**
3. **Keyframe volume.** A 5 s pan at 24 fps with 4 planes is ~960 keyframe objects inside the
   contract hash. Two mitigations, both worth measuring before choosing: run-length-compress
   collinear keys (the face solver already does this — `wave6_research.md` §14, *"per-frame
   linear keyframes run-length compressed"*), or — better — emit the compensation as **two**
   keyframes whenever `cam(t)` is itself a two-key linear/eased ramp, since `c_i` is then an
   affine image of it and needs no resampling at all. The second is exact and free for every
   named move; only a multi-key hand-authored camera needs the first. **A dolly (7b) is not
   affine in `cam_z`** — `f_i` and `render_scale_i` are rational functions of it — so 7b's
   channels genuinely are per-frame and the compression matters there.
4. **Camera pose does not survive a cut.** Each shot compiles independently, so `root.pivot`
   resets to 0 at every shot boundary. Correct for a cut; wrong for a pan continuing across two
   shots. Out of scope, but the `keys` shape makes the eventual fix a scene-level camera track
   rather than a redesign.
5. **Depth vs paint order is deferred, not solved.** §4.3 keeps authored order. The moment a
   character is meant to walk *behind* a mid-plane, `an` has no answer, and the fix is a runtime
   change (`sortableChildren`/`zIndex`, absent today). Moho's opt-in checkbox is the shape to
   copy when it comes; do not let it arrive by accident inside Wave 7.
6. **Sign conventions differ across every tool surveyed** (§2.5). Pin `+z` = away from the
   camera in the schema docstring, or an imported scene will come out inside-out.
7. **Unverified, and not relied on:** the brief's `parallaxFactor = 1 − 1/(z − z_cam)` (§2.3 —
   zero sources located); `pixi-viewport@5.x`'s exact line numbers (the quoted source is 6.x);
   Moho's camera being *documented* as perspective (§2.4 — inferred from FOV/focal-length
   wording, never stated); the After Effects quotes, which come from **Internet Archive
   snapshots** because `helpx.adobe.com` was unreachable — re-verify before citing AE as a
   primary source; and the assumption that the `expressions`/`dialogue` corpus rigs need no
   re-blessing (they declare no camera, so they should not — but that is an assertion for the
   PR, not a measured fact here).
8. **Not measured here: a rendered frame.** Every number in §2.5 and §3.6 comes from a Python
   port of the vendored engine's own two transform lines, composed by hand. It is exact algebra
   against the shipped bytes, and it is still not a screenshot. The first Wave 7 PR should render
   the parallax corpus scene on a labelled PR and reconcile the measured centroid ratios against
   §2.5's table before anything is blessed.

---

## Sources fetched 2026-08-25

**Local, read this session** (all `file:line` above): `an/adapters/cutout/compile.py`,
`serialize.py`, `timeline.py`, `channel.py`, `clip.py`, `render.py`;
`an/data/cutout_runtime/runtime.js`, `index.html`, `vendor/pixi.min.js` (v7.4.2, MIT,
`vendor/pixi.LICENSE.txt`); `an/ir/schema.py`, `validate.py`, `sync.py`, `migrate.py`,
`compose.py`; `an/bench/contract.py`, `corpus.py`, `golden.py`, `metrics.py`, `registry.py`;
`an/determinism.py`; `an/base.py`; `an/stores/environments.py`; `tests/test_loud_discards.py`,
`test_step_hz.py`, `test_cutout_channel_parity.py`, `test_expression_goldens.py`;
`misc/docs/architecture_as_built.md`, `wave6_research.md`; `.claude/skills/an/SKILL.md`;
`misc/demos/build_demos.py`; `gh issue view 9`.

**PixiJS** — https://pixijs.download/v7.4.2/docs/PIXI.DisplayObject.html ·
https://pixijs.download/v7.4.2/docs/PIXI.Container.html ·
https://pixijs.download/v7.4.2/docs/PIXI.Transform.html ·
https://pixijs.download/v7.4.2/docs/index.html (the "camera" negative) ·
https://pixijs.io/guides/basics/scene-graph.html · https://pixijs.com/blog/pixi-v8-launches ·
https://raw.githubusercontent.com/pixijs/pixijs/v7.x/packages/math/src/Transform.ts ·
https://raw.githubusercontent.com/pixijs/pixijs/v7.x/packages/display/src/DisplayObject.ts

**pixi-viewport** — https://raw.githubusercontent.com/davidfig/pixi-viewport/master/README.md ·
.../master/LICENSE · .../master/package.json · .../master/src/Viewport.ts ·
https://viewport.pixijs.io/jsdoc/Viewport.html

**Godot** (MIT; docs CC BY 3.0) —
https://docs.godotengine.org/en/stable/classes/class_parallax2d.html ·
.../class_parallaxlayer.html · .../class_parallaxbackground.html ·
https://docs.godotengine.org/en/stable/tutorials/2d/2d_parallax.html ·
https://github.com/godotengine/godot/blob/ce139f75773d3fb36dac8a620ddc5849240b64aa/scene/2d/parallax_2d.cpp#L102-L141 ·
https://github.com/godotengine/godot/blob/9dc231366d4c80affbee089b1a6e908455e3d1fd/scene/2d/parallax_layer.cpp#L109-L133 ·
https://github.com/godotengine/godot/blob/a62870956aa65461ac157ceb5869f82ee5fd5f36/scene/2d/parallax_background.cpp#L48-L109 ·
https://github.com/godotengine/godot/blob/4.4-stable/LICENSE.txt

**Unity (COMMUNITY sources; no official formula found)** —
https://github.com/Juliusprojects/MMP/blob/5db5c95954f89199cf02224dc9c5e97015c2927a/MMP/Assets/Parallax.cs ·
https://github.com/yalza/UnityCommonScripts/blob/da3e5edad5c746ef11732d0f06a0a47865ea9b61/ParallaxEffect.cs ·
https://github.com/intrepion/brackeys-unity-2d-platformer/blob/d72c4a29fd5156ab53d37bd9c53ba5131cce086b/Assets/Parallaxing.cs ·
https://github.com/Games-Engineering-Grp-Game-Jam/PinballAirHockey/blob/c785be05bcdb902d035b9097a610955b0a231ed0/PinballAirHockey/Assets/ParallaxBackground_InTheForest/ParallaxBackground_InTheForest/Script/Parallax.cs

**Toon Boom Harmony 22** —
https://docs.toonboom.com/help/harmony-22/premium/staging/about-multiplane.html ·
.../staging/set-up-multiplane.html · .../camera/about-camera.html · .../camera/add-camera.html

**OpenToonz / Tahoma2D** —
https://raw.githubusercontent.com/opentoonz/opentoonz_docs/master/source/creating_movements.rst ·
https://opentoonz.readthedocs.io/en/latest/creating_movements.html

**Moho** — https://www.lostmarble.com/moho/manual/camera_tools.html ·
.../manual/tut05/04/index.html · .../manual/layerwnd.html · .../manual/tut06/07/index.html ·
.../manual/tut02/08/index.html

**After Effects (Internet Archive snapshots; helpx.adobe.com unreachable)** —
http://web.archive.org/web/20260426223842/https://helpx.adobe.com/after-effects/using/3d-layers.html ·
http://web.archive.org/web/20260405153454/https://helpx.adobe.com/after-effects/using/cameras-lights-points-interest.html

**The multiplane camera** — https://en.wikipedia.org/wiki/Multiplane_camera ·
https://www.waltdisney.org/sites/default/files/MultiplaneGuideCurriculumPacket_Final.pdf

**UNVERIFIED, not relied on** — `parallaxFactor = 1 − 1/(z_layer − z_camera)` and
`z/(z − cameraZ)` (no source located); Harmony's "Auto-Apply" as a Z-scale mechanism (absent
from every multiplane/camera page — a false lead); OpenToonz `N/S` / Xsheet-column-header Z
(legacy naming; current docs put Z in the Animate tool options bar and use X/Y).
