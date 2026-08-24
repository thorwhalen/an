# Real Character Art for `an` — A 2D Cutout Pipeline Upgrade Plan

**Author:** Thor Whalen
**Repo:** [thorwhalen/an](https://github.com/thorwhalen/an)
**Date:** 2 May 2026

---

## Executive Summary — "If I had two days"

1. **Pick one descriptor format and steal Spine's vocabulary.** Adopt `bones → slots → skins → animations` as the IR, but keep it as Pydantic JSON (your existing pillar). This is the only model that cleanly separates *where things attach* (slot), *which art is shown* (skin/attachment), and *what moves* (animation) — exactly the architecture pillar `an` already declared [1].
2. **Mouth set: ship 9 shapes, but make 6 mandatory.** Rhubarb's A–F are required; G/H/X are optional and Rhubarb itself defaults to including them with `--extendedShapes GHX` [2]. Name attachments `mouth_a … mouth_x` (Spine convention reused by Rhubarb's Spine integration) [3].
3. **Use SVG `<g id="part_…">` groups + a sibling `<g id="skeleton">` of named `<circle>` pivots** — this is exactly Google's open-source Pose Animator convention and is parseable in 30 lines of `lxml` [4]. Don't invent a new convention.
4. **In PixiJS v7, load each part as a texture via `Assets.load(svgUrl, { resolution: 2 })` and use one Sprite per part with `anchor.set(...)`.** Don't render SVG paths every frame — texture-from-SVG is "fast to render (rendered as a quad, not geometry)" per the official PixiJS docs [5]. Pre-rasterize visemes at build time into a spritesheet for cheap swaps.
5. **For v0.1 demo art, don't draw — call DiceBear's HTTP API.** `https://api.dicebear.com/9.x/<style>/svg?seed=<name>` returns a deterministic SVG you can post-process to add part IDs. Adventurer / lorelei / avataaars styles already have separable hair/face/clothing groups [6].
6. **Idle micro-animation budget: a 4-second sine-wave breath at ±2 px on torso Y and ±0.5° on head rotation.** That's literally MoCap Online's production reference for resting characters (1–2 cm vertical travel, 15–20 breaths/min) [7], and matches Palos Publishing's procedural sine-wave approach [8].
7. **Write `an.assets.promote(scene, entity, as_)` as: copy SVG sidecar → emit a `character.json` with the slot/skin/attachment graph → register in `mall["characters"]`.** Spine, Rive, and Lottie all do roughly the same thing under the hood [1, 9].

---

## 1. SVG Authoring Conventions for Cutout 2D

### 1.1 What production pipelines actually do

| Tool | Layer naming convention | Pivot encoding | Mouth/viseme convention |
|---|---|---|---|
| **Spine** | "Slots" hold "Attachments" by string name; runtime looks them up by name [1] | Bone has `x`, `y`, `rotation`, `scaleX/Y` in JSON [1] | Slot named `mouth`, attachments `mouth_a`, `mouth_b`, … (consistent prefix) [3] |
| **Adobe Character Animator** | Photoshop/Illustrator group names auto-tag: `Head`, `Mouth`, `Right Eye`, etc.; sub-layers like `Aa`, `Ee`, `Oh`, `M`, `F`, `L`, `Smile`, `Surprised`, `Neutral` [10] | Special handle named `Origin` (or bounding-box center if absent) [10] | 14 standard mouths: `Neutral`, `Smile`, `Surprised`, `Aa`, `D`, `Ee`, `F`, `L`, `M`, `Oh`, `R`, `S`, `Uh`, `W-Oo` [11] |
| **Lottie** | After Effects layer names live in field `nm`; SVG renderer uses `ln` (id) and `cl` (CSS class) [12] | Anchor point `ks.a`, position `ks.p`; AE-style 2D transform [12] | None — Lottie is keyframe-based, no slot/skin separation |
| **Rive** | "Artboards" → bones / shapes / state machines; binary `.riv` [9] | Bones, with state machine inputs (boolean, number, trigger) [9] | Procedural via state-machine inputs (you blend or swap shapes) [9] |
| **Synfig** | Group layers + Skeleton Layer; "Link to Bone" / "Link to Skeleton" [13] | Bones with origin (green), angle (blue), length (orange) handles [13] | Group swap |
| **Pose Animator (Google, OSS)** | `<g id="skeleton">` of `<circle>` joints with fixed names + `<g id="illustration">` for art [4] | Each joint is a `<circle>` whose center is the pivot; name = joint name [4] | n/a (machine-learning driven) |

### 1.2 Recommended on-disk shape for `an`

```
characters/maya/
├── maya.svg                # canonical layered SVG
├── maya.character.json     # Pydantic-validated descriptor
└── parts/                  # build artifact, regenerated from .svg
    ├── head.svg
    ├── torso.svg
    ├── arm_l.svg, arm_r.svg
    ├── leg_l.svg, leg_r.svg
    ├── eye_l_open.svg, eye_l_closed.svg, eye_r_open.svg, eye_r_closed.svg
    ├── brow_l.svg, brow_r.svg
    └── mouth/
        ├── mouth_a.svg, mouth_b.svg, … mouth_h.svg, mouth_x.svg
        └── mouth_smile.svg, mouth_surprised.svg   # bonus
```

In the canonical `maya.svg`, use **Inkscape's `inkscape:label`** for human names but copy the label into the SVG `id` attribute at export so a parser can rely on `id`. Inkscape itself does NOT auto-promote `inkscape:label` to `id`; this is a known long-standing pain point [14], and there's a published Inkscape extension (`Inkscape-Label-To-ID`) that automates the copy [15]. For the `an` workflow, run that extension once before saving — or do it at parse time:

```python
# python: promote inkscape:label to id, then split parts
from lxml import etree

NS = {
    "svg": "http://www.w3.org/2000/svg",
    "ink": "http://www.inkscape.org/namespaces/inkscape",
}


def normalize_svg(path):
    tree = etree.parse(path)
    for g in tree.iter("{%s}g" % NS["svg"]):
        label = g.get("{%s}label" % NS["ink"])
        if label and not g.get("id"):
            g.set("id", label.replace(" ", "_"))
    return tree


def extract_part(tree, part_id):
    """Clone the root SVG but keep only the matching group."""
    root = tree.getroot()
    keepers = root.xpath(f".//svg:g[@id='{part_id}']", namespaces=NS)
    new_root = etree.Element("{%s}svg" % NS["svg"], nsmap={None: NS["svg"]})
    new_root.set("viewBox", root.get("viewBox") or "0 0 1024 1024")
    for k in keepers:
        new_root.append(etree.fromstring(etree.tostring(k)))
    return new_root
```

The `lxml` route is the right call. `svglib` exists but its purpose is converting SVG → ReportLab Drawing for PDF/raster output, not surgical DOM manipulation [16]. `cairosvg` is a renderer not a parser. Stick with `lxml` for DOM, `cairosvg` (or `pyvips`/`resvg`) for rasterization at build time.

### 1.3 Pivot conventions

Adopt **Pose Animator's** approach because it's the only OSS convention that lives in pure SVG and has working code [4]:

```xml
<svg viewBox="0 0 1024 1024">
  <g id="skeleton">
    <circle id="neck"      cx="512" cy="380" r="3" fill="none"/>
    <circle id="shoulder_l" cx="430" cy="430" r="3" fill="none"/>
    <circle id="shoulder_r" cx="594" cy="430" r="3" fill="none"/>
    <circle id="elbow_l"    cx="380" cy="540" r="3"/>
    <!-- ... -->
  </g>
  <g id="illustration">
    <g id="torso">…</g>
    <g id="head">
      <g id="mouth"><g id="mouth_x">…</g></g>
      <g id="eye_l">…</g>
      <g id="brow_l">…</g>
    </g>
    <g id="arm_l">…</g>
    <!-- ... -->
  </g>
</svg>
```

This is exactly the Pose Animator "skeleton group containing anchor elements named with the respective joint they represent" [4]. The `cx`/`cy` of the named circle IS the pivot, in the same coordinate system as the art — no sidecar, no calibration. Filename suffixes (e.g., `head_pivot.svg`) work for a primitive pipeline but break as soon as you have two pivots on one part (head has both `neck` and a hypothetical `crown` pivot for a hat).

### 1.4 Pose normalization

The art-board mismatch problem is solved by **always exporting with a `viewBox`** and treating that viewBox as the rig's local coordinate system. Don't trust `width`/`height` (those are device-independent CSS pixels and Inkscape will happily save them as physical units like mm). PixiJS, when loading SVG via `Assets.load`, uses the SVG's intrinsic viewBox and you can override resolution at load time [5]. So:

- Define one canonical character viewBox (e.g., `0 0 1024 1024`, character standing in the middle, feet at y≈980).
- All parts inherit the same viewBox at export.
- Pivots are defined in this viewBox; sprites in PixiJS use that pivot as `anchor` after texture is loaded.

---

## 2. Mouth-Shape Art for Lip-Sync

### 2.1 How many shapes is "good-looking"?

| Source | Shape count | Notes |
|---|---|---|
| **Rhubarb basic** | 6 (A–F) | "the absolute minimum you have to draw" [2] |
| **Rhubarb full** | 9 (A–H + X) | G/H/X are optional but improve quality; default `--extendedShapes GHX` [2] |
| **Adobe Character Animator** | 14 | `Neutral`, `Smile`, `Surprised`, `Aa`, `D`, `Ee`, `F`, `L`, `M`, `Oh`, `R`, `S`, `Uh`, `W-Oo` [11] |
| **Preston Blair (originally *Animation*, 1948; expanded as *Cartoon Animation*, Walter Foster, 1994)** | 10 | `AI, O, E, U, etc, L, WQ, MBP, FV, rest` [17] |
| **Live2D / VTube Studio** | 5 vowels + open/form blendshape | `ParamA, ParamI, ParamU, ParamE, ParamO` + `ParamMouthOpen/Form` [18] |

For `an`'s pipeline, the answer is unambiguous: **9 shapes mapped 1:1 to Rhubarb's A–H + X**. Going beyond doesn't help because Rhubarb won't emit them; going below 6 drops quality.

### 2.2 What each viseme should depict (verbatim from Daniel Wolf's README) [2]

These are the canonical descriptions you can hand to an LLM image generator or a human illustrator:

- **A** — *"Closed mouth for the 'P', 'B', and 'M' sounds. This is almost identical to the Ⓧ shape, but there is ever-so-slight pressure between the lips."*
- **B** — *"Slightly open mouth with clenched teeth. This mouth shape is used for most consonants ('K', 'S', 'T', etc.). It's also used for some vowels such as the 'EE' sound in bee."*
- **C** — *"Open mouth. This mouth shape is used for vowels like 'EH' as in men and 'AE' as in bat."* — also the in-between for ⒶⒸⒹ and ⒷⒸⒹ.
- **D** — *"Wide open mouth. This mouth shape is used for vowels like 'AA' as in father."*
- **E** — *"Slightly rounded mouth. This mouth shape is used for vowels like 'AO' as in off and 'ER' as in bird."* — in-between for ⒸⒺⒻ / ⒹⒺⒻ; not wider than C.
- **F** — *"Puckered lips. This mouth shape is used for 'UW' as in you, 'OW' as in show, and 'W' as in way."*
- **G** *(optional)* — *"Upper teeth touching the lower lip for 'F' as in for and 'V' as in very."* Skip if your art style is too coarse to show teeth meaningfully.
- **H** *(optional)* — *"This shape is used for long 'L' sounds, with the tongue raised behind the upper teeth. The mouth should be at least far open as in Ⓒ, but not quite as far as in Ⓓ."*
- **X** *(optional, idle)* — *"Idle position. This mouth shape is used for pauses in speech. … almost identical to Ⓐ, but with slightly less pressure between the lips."*

So, beyond the silhouette: **G needs visible upper teeth on lower lip**; **H needs a visible tongue tip**; **B/C** benefit from clenched teeth; **F** has rounded lips. Asymmetric corners (E/F) help when the character has a head turn, but for a frontal puppet they're optional.

Rhubarb's `--datUsePrestonBlair` flag swaps the export labels: A→MBP, B→etc, C→E, D→AI, E→O, F→U, G→FV, H→L, X→rest. Stick with the alphabetic names unless you target OpenToonz [2].

### 2.3 Naming and swap conventions

The de-facto cross-tool standard is **`<slot>_<shape>`**, e.g. `mouth_a … mouth_x`, with the slot name lowercase and consistent. Rhubarb's Spine integration says: *"You can choose any naming scheme you like and Rhubarb will detect it, as long as it's consistent (including upper and lower case). For instance, A-Lips, B-Lips, C-Lips, … is fine; mouth a, mouth B, Mouth-C, … isn't."* [3]

For `an`, lock in:

```python
MOUTH_SHAPES = ["a", "b", "c", "d", "e", "f", "g", "h", "x"]
MOUTH_FILENAME = "mouth_{shape}.svg"
MOUTH_ATTACHMENT = "mouth_{shape}"  # name in the JSON descriptor
```

### 2.4 Minimum spec for an LLM authoring mouth art

The minimum spec the LLM needs to author drop-in mouth art is:

```yaml
canvas:
  viewBox: "0 0 256 128"     # mouth-local coordinates, not character-global
  anchor: [128, 64]          # where the mouth attaches to the head; centered
shape: <one_of_a..x>
constraints:
  - lip_color: "#c25450"
  - skin_color: "#f1c9a5"    # for around-mouth area
  - max_width_ratio: 0.85    # mouth never exceeds 85% of canvas width
  - max_height_ratio: 0.85
  - centroid: [128, 64]      # center of mass within ±10px
  - all_shapes_share_corner_anchor: true   # left-corner-of-lips at consistent x,y across A-X
```

The "centroid + anchor" pair is what makes shapes swap cleanly. Live2D solves this by parameter blending [18]; Spine by attachment offset; for raster swap-sets the only reliable trick is **identical canvas + identical anchor**.

---

## 3. Loading SVG into PixiJS v7

### 3.1 The two PixiJS v7 paths

PixiJS v7 has two SVG paths, summarized in the official docs [5, 19]:

| Approach | Code | Tradeoff |
|---|---|---|
| **Texture from SVG** | `const tex = await Assets.load('mouth_a.svg', { resolution: 2 }); new Sprite(tex);` | "Fast to render (rendered as a quad, not geometry)"; loses crisp scaling beyond loaded resolution; **recommended for this pipeline** [5] |
| **Graphics from SVG** (v8) / `@pixi/graphics-extras` (v7) | `const ctx = await Assets.load('p.svg', { parseAsGraphicsContext: true }); new Graphics(ctx);` | Vector-correct at any scale; slower per frame; v7 had `Graphics.svg(string)` in `@pixi/graphics-extras` but it's quirky [5] |

For `an`'s use case (60+ sprite swaps per second on mouth alone, plus body parts), **always go texture**. PixiJS v8 even kept this advice: *"If performance is a concern, consider using SVGs as textures instead of rendering them as geometry"* [19]. There's a known gotcha in v7: SVG textures are processed asynchronously, so on the very first frame `sprite._textureID` may be undefined — gate on the texture's `valid` flag or `await Assets.load` before constructing sprites [20].

### 3.2 Recommended runtime structure

```js
// bootstrap.js — run once at scene start
const charManifest = {
  bundles: [{
    name: 'maya',
    assets: [
      { alias: 'maya.head',    src: 'characters/maya/parts/head.svg' },
      { alias: 'maya.torso',   src: 'characters/maya/parts/torso.svg' },
      { alias: 'maya.arm_l',   src: 'characters/maya/parts/arm_l.svg' },
      // …
      ...['a','b','c','d','e','f','g','h','x'].map(s => ({
        alias: `maya.mouth_${s}`,
        src: `characters/maya/parts/mouth/mouth_${s}.svg`
      })),
    ],
  }],
};
await Assets.init({ manifest: charManifest, texturePreference: { resolution: 2 }});
await Assets.loadBundle('maya');

// rig assembly
const head = new Sprite(Assets.get('maya.head'));
head.anchor.set(0.5, 0.78);   // pivot = neck; computed from <circle id="neck"> at build time
head.position.set(rigOrigin.x, rigOrigin.y);

const mouth = new Sprite(Assets.get('maya.mouth_x'));
mouth.anchor.set(0.5, 0.5);
mouth.position.set(headLocal.mouth.x, headLocal.mouth.y);
head.addChild(mouth);

// per frame: swap viseme
function setViseme(letter) {
  mouth.texture = Assets.get(`maya.mouth_${letter.toLowerCase()}`);
}
```

This keeps the PixiJS v7 idiom: **one Sprite per slot, swap `texture` to change skin/viseme**. The `Assets` cache means the texture is reused, not re-rasterized [21].

### 3.3 Spritesheet vs. individual SVG textures

If you have ≥9 mouth shapes × N characters, the build-time win from packing into a TexturePacker atlas is real (one upload, one draw call). PixiJS v7 ships first-class spritesheet support: `Assets.load('atlas.json')` returns a `Spritesheet` whose `textures['mouth_a.png']` are pre-keyed [22]. Recommended workflow:

1. **Build step (Python):** `cairosvg` rasterizes each part SVG at 2× → PNG.
2. **Build step (Node):** `texturepacker` or `free-tex-packer-core` packs PNGs into `maya.png` + `maya.json`.
3. **Runtime:** `const sheet = await Assets.load('maya.json'); mouth.texture = sheet.textures['mouth_a.png'];`

Pick spritesheet for **production**; pick raw `Assets.load(svg)` for **dev hot-reload** because you can edit the SVG and rerun without a build step.

### 3.4 Hot-reload strategy

The cheapest single-character reload is:

```js
async function hotReload(characterName) {
  // 1. Build set of asset aliases for this character
  const aliases = Array.from(Assets.cache.keys()).filter(k => k.startsWith(characterName + '.'));
  // 2. Unload them
  await Assets.unload(aliases);
  // 3. Reload bundle
  await Assets.loadBundle(characterName);
  // 4. Walk the rig; for each Sprite tagged with this character, refresh texture
  rig.children.forEach(s => {
    if (s.alias?.startsWith(characterName)) s.texture = Assets.get(s.alias);
  });
}
```

`Assets.unload` is the official memory-management API — it removes from the cache and frees GPU memory [21]. This avoids re-tearing-down the whole stage. There's a known PixiJS issue (#5434) where reloading the *same URL* from disk returns the cached texture; fix by `Assets.unload(url)` *before* `Assets.load(url)` [23], or append a cache-buster query string (`?t=${Date.now()}`).

---

## 4. Open-Source Character Asset Libraries

| Library | License | Format | Separable parts? | Programmatic? |
|---|---|---|---|---|
| **DiceBear** (avataaars, lorelei, adventurer, …) | MIT (code); avatar styles vary, often CC BY 4.0 [6] | SVG (HTTP API) | Hair/eyes/mouth/clothing are SVG groups, named per style | Yes — `https://api.dicebear.com/9.x/<style>/svg?seed=<id>` [6, 24] |
| **avataaars (original Pablo Stanley)** | MIT; design free for personal & commercial [25] | SVG (React component) | Yes — `top`, `accessories`, `clothing`, `face`, `skin` props [25] | Yes — `python_avatars` (Python), `@dicebear/avataaars` (JS) [25] |
| **Boring Avatars** | MIT [26] | SVG (React) | No — abstract, no body parts | Yes |
| **Kenney "Modular Characters"** | CC0 — "Download this package (425 assets) for free, CC0 licensed!" — 2D PNG sprites + 6 vector files (no 3D parts) [27] | PNG + SVG | Yes — separate body parts | Static download |
| **Kenney "Roguelike Characters"** | CC0 — "Download this package (450 assets) for free, CC0 licensed!" [28] | PNG sprite atlas | Whole-character only | Static |
| **OpenGameArt LPC base** | OGA-BY / CC-BY-SA / GPL [29] | PNG sprite sheets (ULPC) | Yes — body, hair, head, clothes layered | Static |
| **OpenGameArt "Some characters"** | CC0 (per author) | SVG | Layered SVG, requires manual separation [30] | Static |
| **Pose Animator sample SVGs** | Apache 2.0 | SVG | Yes — `skeleton` + `illustration` groups [4] | Static demos |

### 4.1 Recommendation for `an` v0.1

Ship **two** demo characters:

1. **`maya-dicebear-adventurer`** — generated at install time by hitting DiceBear's HTTP API:
   ```python
   import urllib.request


   def fetch_dicebear(seed: str, style: str = "adventurer") -> str:
       url = f"https://api.dicebear.com/9.x/{style}/svg?seed={seed}"
       return urllib.request.urlopen(url).read().decode()
   ```
   You then post-process the returned SVG to add `id="head"` / `id="torso"` based on DiceBear's internal group structure (DiceBear groups parts by *naming convention*; their docs describe a "simple naming convention" used by their Figma plugin [31]). The post-process is the only fiddly part — it needs to be re-validated whenever DiceBear publishes a new style version.

2. **`maya-handrigged`** — a hand-authored Inkscape SVG following the Pose Animator skeleton convention, checked into the repo as the canonical example. Use it for tests and as the "what good looks like" reference.

### 4.2 LLM image generation for cutout-friendly characters

DALL·E 3 / Imagen / Nano-Banana cannot reliably generate cleanly-separable body parts in a single shot — even getting consistent multi-character compositions is shaky once you go past 2 humans [32, 33]. **The workable workflow is a 2-step generation**:

1. Generate a frontal full-body cartoon character on a transparent background. Working prompt skeleton:
   > *"Full-body frontal cartoon character of <description>, T-pose with arms slightly out from body, simple flat colors, thick black outlines, no shadows, transparent background, vector-style, 1024×1024, head size approximately 1/5 of total height."*
2. Use `rembg` or `transparent-background` to confirm alpha; then run an SVG tracer (`vtracer`, `potrace`) to get vectors; then **manually or with an LLM-vision model identify the part bounding boxes** and slice into per-part SVGs.

This is fragile. For v0.1 demo, the DiceBear path is faster and reliable; LLM generation is a v0.2 stretch goal.

### 4.3 Adobe Character Animator free puppets

Adobe ships free starter puppets for Character Animator, plus GraphicMama distributes free mouth-shape sets (14 standard frontal mouths + extras, in `.ai` and `.png`) [34]. License is "free for personal and commercial use" per pack but check each one. These are usable as a *reference* of what a fully-specified mouth set looks like — not as drop-in art for an SVG-based pipeline (the source files are Illustrator/PSD).

---

## 5. Asset-Promotion / Character-Store Conventions

### 5.1 What each format actually contains

| Format | Contains | Format type | Closest to `an`? |
|---|---|---|---|
| **Spine `.json` / `.skel`** | `skeleton` (metadata), `bones[]`, `slots[]`, `skins{}` (each slot → attachment dict), `events[]`, `animations{}` (timelines per bone/slot/attachment) [1] | Stateless data; runtime instantiates | **Best fit** — slot/skin/animation separation is exactly the architecture pillar [1] |
| **Rive `.riv`** | Binary; artboards, shapes, bones, state machines, audio events; runtime exposes typed inputs (bool, number, trigger) [9] | Binary, optimized | Bad fit — binary, opaque to Pydantic; great for designers, hostile to LLMs |
| **Lottie JSON** | `v` (version), `fr`, `w/h`, `layers[]` (each with `nm` name, `ks` transform, `shapes[]` or refId), `assets[]` [12] | Keyframes baked from After Effects | Decent fit but flat — no slot/skin abstraction |
| **Adobe Character Animator `.puppet`** | Proprietary; structured around groups with semantic tags (`Head`, `Mouth`, `Aa`, `Ee`, …) [10] | Editor file, not an interchange format | Not interchangeable |

**Verdict: model `an`'s descriptor on Spine's JSON, but keep it human-readable (verbose key names, no abbreviations).** You inherit:

- A `bones` list with parent/local-transform fields (you may not need full bone hierarchy on day 1; flat parts work).
- A `slots` list with explicit draw order (`["torso", "head", "mouth", "eye_l", "eye_r", "brow_l", "brow_r", "arm_l", "arm_r", "leg_l", "leg_r"]`).
- A `skins` map, where the default skin maps `slot → attachment_name → svg_path`.
- An `animations` map for built-in breath/blink loops, keyed as Pydantic `Animation` models.

### 5.2 Recommended `character.json` for `an` v0.2

```jsonc
{
  "name": "maya",
  "version": "1",
  "viewBox": [0, 0, 1024, 1024],
  "voice_ref": "voices/maya.wav",
  "bones": [
    { "name": "root",        "parent": null,    "x": 512, "y": 980 },
    { "name": "torso",       "parent": "root",  "x": 0, "y": -300 },
    { "name": "head",        "parent": "torso", "x": 0, "y": -260, "pivot": "neck" },
    { "name": "arm_l",       "parent": "torso", "x": -90, "y": -240, "pivot": "shoulder_l" },
    { "name": "arm_r",       "parent": "torso", "x":  90, "y": -240, "pivot": "shoulder_r" },
    { "name": "leg_l",       "parent": "root",  "x": -50, "y": -10 },
    { "name": "leg_r",       "parent": "root",  "x":  50, "y": -10 }
  ],
  "slots": [
    { "name": "torso",   "bone": "torso",  "draw_order": 0, "attachment": "torso" },
    { "name": "head",    "bone": "head",   "draw_order": 4, "attachment": "head" },
    { "name": "mouth",   "bone": "head",   "draw_order": 7, "attachment": "mouth_x" },
    { "name": "eye_l",   "bone": "head",   "draw_order": 6, "attachment": "eye_l_open" },
    { "name": "eye_r",   "bone": "head",   "draw_order": 6, "attachment": "eye_r_open" },
    { "name": "brow_l",  "bone": "head",   "draw_order": 8, "attachment": "brow_l_neutral" },
    { "name": "brow_r",  "bone": "head",   "draw_order": 8, "attachment": "brow_r_neutral" },
    { "name": "arm_l",   "bone": "arm_l",  "draw_order": 2, "attachment": "arm_l" },
    { "name": "arm_r",   "bone": "arm_r",  "draw_order": 2, "attachment": "arm_r" },
    { "name": "leg_l",   "bone": "leg_l",  "draw_order": 1, "attachment": "leg_l" },
    { "name": "leg_r",   "bone": "leg_r",  "draw_order": 1, "attachment": "leg_r" }
  ],
  "skins": {
    "default": {
      "torso":  { "torso":   { "path": "parts/torso.svg",   "anchor": [0.5, 0.0] }},
      "head":   { "head":    { "path": "parts/head.svg",    "anchor": [0.5, 0.78] }},
      "mouth":  {
        "mouth_a": { "path": "parts/mouth/mouth_a.svg", "anchor": [0.5, 0.5] },
        "mouth_b": { "path": "parts/mouth/mouth_b.svg", "anchor": [0.5, 0.5] },
        "mouth_c": { "path": "parts/mouth/mouth_c.svg", "anchor": [0.5, 0.5] },
        "mouth_d": { "path": "parts/mouth/mouth_d.svg", "anchor": [0.5, 0.5] },
        "mouth_e": { "path": "parts/mouth/mouth_e.svg", "anchor": [0.5, 0.5] },
        "mouth_f": { "path": "parts/mouth/mouth_f.svg", "anchor": [0.5, 0.5] },
        "mouth_g": { "path": "parts/mouth/mouth_g.svg", "anchor": [0.5, 0.5] },
        "mouth_h": { "path": "parts/mouth/mouth_h.svg", "anchor": [0.5, 0.5] },
        "mouth_x": { "path": "parts/mouth/mouth_x.svg", "anchor": [0.5, 0.5] }
      },
      "eye_l":   { "eye_l_open":  {"path":"parts/eye_l_open.svg"},  "eye_l_closed":{"path":"parts/eye_l_closed.svg"} },
      "eye_r":   { "eye_r_open":  {"path":"parts/eye_r_open.svg"},  "eye_r_closed":{"path":"parts/eye_r_closed.svg"} },
      "brow_l":  { "brow_l_neutral":{"path":"parts/brow_l.svg"} },
      "brow_r":  { "brow_r_neutral":{"path":"parts/brow_r.svg"} }
    }
  },
  "viseme_map": { "A":"mouth_a","B":"mouth_b","C":"mouth_c","D":"mouth_d","E":"mouth_e","F":"mouth_f","G":"mouth_g","H":"mouth_h","X":"mouth_x" },
  "animations": {
    "idle_breath": { "duration": 4.0, "tracks": [
      { "target": "bone:torso.y",      "type": "sine", "amplitude": 2.0, "phase": 0 },
      { "target": "bone:head.rotation","type": "sine", "amplitude": 0.5, "phase": 0.25 }
    ]},
    "blink": { "duration": 0.18, "tracks": [
      { "target": "slot:eye_l.attachment", "type": "step", "frames": [[0,"eye_l_open"],[0.05,"eye_l_closed"],[0.13,"eye_l_open"]]},
      { "target": "slot:eye_r.attachment", "type": "step", "frames": [[0,"eye_r_open"],[0.05,"eye_r_closed"],[0.13,"eye_r_open"]]}
    ]}
  }
}
```

The path-based property targeting (`"bone:torso.y"`, `"slot:eye_l.attachment"`) is `an`'s existing convention — borrowed structurally from Spine timelines but tuned for Pydantic IR.

### 5.3 `an.assets.promote` workflow

```python
def promote(scene: str, entity: str, as_: str) -> Path:
    """
    Lift an inline character from a scene into the reusable mall.
    """
    sd = SceneData.load(scene)  # parse scene.md → IR
    char = sd.find_entity(entity)  # in-scene character node
    src_svg = char.svg_path or sd.workdir / f"{entity}.svg"

    target = MALL["characters"] / as_
    target.mkdir(parents=True, exist_ok=False)

    # 1) Copy and normalize SVG
    canonical = target / f"{as_}.svg"
    shutil.copy(src_svg, canonical)
    tree = normalize_svg(canonical)  # promote inkscape:label→id
    tree.write(canonical)

    # 2) Slice into per-part SVGs
    parts_dir = target / "parts"
    parts_dir.mkdir()
    pivots = extract_pivots(tree)  # read <g id="skeleton">
    for part_id in PARTS_REQUIRED:
        part_svg = extract_part(tree, part_id)
        (parts_dir / f"{part_id}.svg").write_bytes(etree.tostring(part_svg))

    # 3) Build descriptor
    desc = CharacterDescriptor.from_parts(
        name=as_,
        viewBox=tree.getroot().get("viewBox"),
        pivots=pivots,
        voice_ref=char.voice_ref,
    )
    (target / f"{as_}.character.json").write_text(desc.model_dump_json(indent=2))

    # 4) Register
    MALL["characters"].register(as_, target / f"{as_}.character.json")
    return target / f"{as_}.character.json"
```

The data flow is intentionally identical to Spine's "export project → JSON + atlas" model, but it ends in a Pydantic-validated JSON, which is the `an` pillar.

---

## 6. Stylized 2D Character Design — Quick Wins

### 6.1 Proportions

Realistic adult: **head:body ≈ 1:7 to 1:8** [35]. Stylized animation (Disney, Pixar, anime): **1:5 to 1:6** is the most common; cute/childlike: **1:3** ("toddler" look) [36]. Recommended for `an`'s default puppet: **1:5** — adult enough to feel like a person, stylized enough that errors in proportion don't read as "wrong." Eye spacing rule of thumb (anime convention): **eyes are spaced one-eye-width apart**, and the eye sits on the horizontal midline of the head (for adult-like proportions; for young/cute, eyes drop below midline).

### 6.2 Asymmetry

Hand-keyed character art that's perfectly symmetric reads as "clip-art." The Disney/AnimSchool reference is to introduce **macro variations over 3–6 loops** so the audience never sees the same pose twice [37]. For static poses: tilt eyebrows asymmetrically by 1–3°, offset hair tufts by 5–10 px, and shift weight to one foot by 5° hip rotation.

### 6.3 Micro-animation: production frequencies and amplitudes

The single most quotable production source: idle breathing should be **15–20 breaths/min (relaxed) or 20–25 (alert)**, with chest vertical travel of **1–2 cm** [38]. Translated to a 1024px-tall character at "normal" framing: **2–4 px of vertical chest/torso travel, period ≈ 3–4 s.** AnimSchool emphasizes that inhale is *slower* than exhale (asymmetric ease) and that the wave should propagate up the spine through head with offset timing [39].

Concrete `an` defaults:

| Channel | Wave | Period | Amplitude | Phase offset |
|---|---|---|---|---|
| `bone:torso.y` | sine | 4.0 s | ±2 px | 0 |
| `bone:head.rotation` | sine | 4.0 s | ±0.5° | 0.25 |
| `bone:torso.x` (weight shift) | sine | 6.0 s | ±1.5 px | 0.5 |
| Spontaneous blink | step | random 3–8 s gaps | full closure 0.13 s | — |

Procedural sine-wave breath in Python (per Palos Publishing's reference) [8]:

```python
def breath_offset(t: float, breaths_per_min: int = 15, depth_px: float = 2.0) -> float:
    cycle = 60.0 / breaths_per_min
    return depth_px * math.sin(2 * math.pi * t / cycle)
```

### 6.4 Color theory and the silhouette test

The "silhouette test" is the single most cited principle in character design: **fill the character with solid black; if you can still tell who they are, the design is strong** [40, 41]. The Walt Disney Family Museum publishes this as a teachable principle: *"the character's basic design 'reads' to the audience. From the silhouette we are able to see a distinctive and recognizable design without details"* [42].

For a 2-character scene at 1080p, characters are ~300 px tall. At that size, color reads before line. Use the **South Park / Adventure Time approach**: each character gets one dominant skin tone, one hair color, one shirt color, and (at most) one accent. If two characters share the same hair color, force one to add a hat or distinct hairstyle. Run the silhouette test before shipping by:

```python
# pyvips trick: rasterize SVG, threshold alpha, save as black-on-white PNG
import pyvips

img = pyvips.Image.svgload("maya.svg")
alpha = img.extract_band(3) if img.bands == 4 else img
silhouette = (alpha > 128).ifthenelse(0, 255)
silhouette.write_to_file("maya.silhouette.png")
```

If two characters' silhouettes are confusable at 300 px width, change one of them.

---

## Recommendations (staged)

### Day 1–2 (v0.1 demo)
- Adopt the SVG convention: `<g id="skeleton">` of named `<circle>` pivots + `<g id="illustration">` with named part groups (Pose Animator convention) [4].
- Wire `lxml` parser → per-part SVG files at build time.
- Generate two demo characters via DiceBear (`adventurer` style, two different seeds) [6]; post-process to add part IDs.
- Hand-author 9 frontal mouth SVGs following Rhubarb's verbatim shape descriptions [2]. Total ~2 hours for an adequate set.
- In PixiJS v7, load each part as a texture (`Assets.load(svg, {resolution: 2})`); build the rig as parented Sprites with per-part anchor [5, 21].

### Day 3–7 (v0.2)
- Define and ship the `character.json` Pydantic schema in §5.2 above.
- Implement `an.assets.promote()`.
- Build idle-breath + blink procedural animation system using the §6.3 frequencies.
- Add a build step that pre-rasterizes parts to a TexturePacker atlas for production renders [22].

### v0.3+
- Add multi-skin support (different outfits, different hairstyles).
- Evaluate Live2D-style mesh deformation if mouth-swap looks too jumpy — but do this only after measuring; viseme swap at 30 fps is usually fine.
- Investigate Rive as an *export* target for designers who want a richer authoring tool, with `an` as the IR.

### Thresholds that change the call
- If lip-sync still looks wooden after shipping 9 mouth shapes: **add `mouth_smile` and `mouth_neutral` overlays** (Adobe Character Animator's evidence: `Smile` + `Surprised` + `Neutral` are the single biggest readability win on top of phoneme visemes [11]).
- If frame rate drops below 24 fps in headless Chromium with 2 characters: switch from per-frame SVG textures to **a single TexturePacker atlas per character** (3.3 → 3.4 spritesheet path).
- If two characters shipped from `mall["characters"]` confuse the silhouette test: enforce a `silhouette_distinct: true` validator at promote time.

---

## Caveats

- **PixiJS version drift.** PixiJS v8 went into beta on 3 October 2023 and was officially released on 5 March 2024 (per the PixiJS blog post "PixiJS v8 Launches!" by Mat Groves and Zyie); it deprecated `SVGResource` and changed the Graphics-from-SVG API (`Graphics.svg()` returns a `GraphicsContext` now) [5]. The plan above targets v7 because that's what `an` runs today; if/when you upgrade, the **texture path is unchanged** but the Graphics path is rewritten.
- **DiceBear style versioning.** DiceBear pins styles to API versions (`9.x`); the Adventurer style is a remix of Lisa Wischofsky's design, CC BY 4.0 [6]. Versions 5.x–8.x reach end-of-life April 30 2028. Pin a specific minor in URLs.
- **Inkscape → SVG `id`.** Inkscape's `inkscape:label` is *not* exported to SVG `id` automatically. This is a 2008-vintage UX bug; the workaround (extension or post-process script) is mandatory [14, 15].
- **Rhubarb mouth shapes are a *scheme*, not artwork — and the scheme is uncopyrightable, which is not the same as public-domain artwork.** Daniel Wolf's README says the six *basic* shapes (A–F) "were invented at the Hanna-Barbera studios for shows such as Scooby-Doo and The Flintstones"; G/H/X are Rhubarb's own extended additions, and the README makes **no** public-domain claim [2] (an earlier version of this line said it did — verified false against the master README, 2026-08-24). Hanna-Barbera artwork is Warner Bros. Discovery IP. What lets anyone use the A–H+X system is the idea/expression distinction (17 U.S.C. §102(b)): a phoneme→mouth-shape mapping is a *system*, and systems are not copyrightable — only particular *drawings* of the shapes are. Rhubarb itself is MIT (© Daniel Wolf); its example mouth images carry no separate licence, and specific drawings by GraphicMama or other vendors are licensed. `an` draws its own shapes (`an/characters/mouth_set.py`), so only the uncopyrightable mapping is reused. Corollary: never vendor Papagayo's CMU→Blair conversion table — that file is GPL-2.0, and the licence-perimeter test checks installed metadata only, so vendored text would evade it; re-derive any Blair mapping from the published chart.
- **LLM-generated body parts are unreliable.** DALL·E and Imagen fail at 3+ characters and at "separate body parts on transparent background." Don't put this on the v0.1 critical path [32, 33].
- **Mocap-derived idle frequencies are 3D references.** The 15–20 BPM and 1–2 cm vertical-travel numbers come from 3D-character production, not 2D cutout, but they translate cleanly when scaled by character pixel height [38].
- **Pose Animator is unmaintained.** The upstream repo's last push to master was on 2020-05-30 (per the Internet Archive mirror archived at `archive.org/details/github.com-yemount-pose-animator_-_2020-05-30_17-47-58`) [4]. The *convention* (`<g id="skeleton">` of named circles) is sound and worth copying; the runtime is not worth depending on.

---

## REFERENCES

[1] Esoteric Software. *Spine: JSON export format.* [http://en.esotericsoftware.com/spine-json-format](http://en.esotericsoftware.com/spine-json-format)

[2] Wolf D. *Rhubarb Lip Sync — Mouth shapes (README).* [https://github.com/DanielSWolf/rhubarb-lip-sync](https://github.com/DanielSWolf/rhubarb-lip-sync)

[3] Wolf D. *Rhubarb Lip Sync for Spine — README.* [https://github.com/DanielSWolf/rhubarb-lip-sync/blob/master/extras/EsotericSoftwareSpine/README.adoc](https://github.com/DanielSWolf/rhubarb-lip-sync/blob/master/extras/EsotericSoftwareSpine/README.adoc)

[4] Mount Y. *Pose Animator — README.* [https://github.com/yemount/pose-animator/](https://github.com/yemount/pose-animator/)

[5] PixiJS. *SVG's | PixiJS (v8 docs, applicable to v7 patterns).* [https://pixijs.com/8.x/guides/components/assets/svg](https://pixijs.com/8.x/guides/components/assets/svg)

[6] DiceBear. *Open Source Avatar Library & API.* [https://www.dicebear.com/](https://www.dicebear.com/)

[7] MoCap Online. *Idle Animation for Games: Designing Character Presence at Rest.* [https://mocaponline.com/blogs/mocap-news/idle-animation-game-dev-guide](https://mocaponline.com/blogs/mocap-news/idle-animation-game-dev-guide)

[8] Palos Publishing. *Simulating Breathing and Idle Motion Procedurally.* [https://palospublishing.com/simulating-breathing-and-idle-motion-procedurally/](https://palospublishing.com/simulating-breathing-and-idle-motion-procedurally/)

[9] Rive. *Format documentation.* [https://rive.app/docs/runtimes/advanced-topic/format](https://rive.app/docs/runtimes/advanced-topic/format)

[10] Adobe. *Prepare artwork in Adobe Character Animator.* [https://helpx.adobe.com/adobe-character-animator/using/prepare-artwork.html](https://helpx.adobe.com/adobe-character-animator/using/prepare-artwork.html)

[11] Adobe. *Behaviors: body (directly controlled) — Lip Sync.* [https://helpx.adobe.com/adobe-character-animator/using/behaviors/body-directly-controlled.html](https://helpx.adobe.com/adobe-character-animator/using/behaviors/body-directly-controlled.html)

[12] LottieFiles. *JSON Schema — Lottie Docs.* [https://lottiefiles.github.io/lottie-docs/schema/](https://lottiefiles.github.io/lottie-docs/schema/)

[13] Synfig Studio. *Basic Bone Tutorial.* [https://wiki.synfig.org/Doc:Basic_Bone_Tutorial](https://wiki.synfig.org/Doc:Basic_Bone_Tutorial)

[14] Inkscape Bug #243383. *SVG IDs don't match layer names.* [https://bugs.launchpad.net/bugs/243383](https://bugs.launchpad.net/bugs/243383)

[15] Cerutti M. *Inkscape-Label-To-ID extension.* [https://github.com/m21-cerutti/Inkscape-Label-To-ID](https://github.com/m21-cerutti/Inkscape-Label-To-ID)

[16] Andronikidis D. *svglib — PyPI.* [https://pypi.org/project/svglib/](https://pypi.org/project/svglib/)

[17] *Papagayo Preston Blair phoneme set (open-source code reference).* [https://github.com/aziagiles/papagayo/blob/master/phonemes_preston_blair.py](https://github.com/aziagiles/papagayo/blob/master/phonemes_preston_blair.py)

[18] DenchiSoft. *VTube Studio Lipsync — Live2D ParamA/I/U/E/O.* [https://github.com/DenchiSoft/VTubeStudio/wiki/Lipsync](https://github.com/DenchiSoft/VTubeStudio/wiki/Lipsync)

[19] PixiJS. *SVGs | pixi.js docs.* [https://pixijs.download/dev/docs/assets-5.html](https://pixijs.download/dev/docs/assets-5.html)

[20] PixiJS. *Texture API reference (v7).* [https://pixijs.download/v7.x/docs/PIXI.Texture.html](https://pixijs.download/v7.x/docs/PIXI.Texture.html)

[21] PixiJS. *Assets API reference (v7).* [https://pixijs.download/v7.x/docs/PIXI.Assets.html](https://pixijs.download/v7.x/docs/PIXI.Assets.html)

[22] PixiJS. *Spritesheet documentation.* [https://pixijs.download/dev/docs/assets.Spritesheet.html](https://pixijs.download/dev/docs/assets.Spritesheet.html)

[23] PixiJS Issue #5434. *Reloading the same texture from disk pulling the old texture.* [https://github.com/pixijs/pixijs/issues/5434](https://github.com/pixijs/pixijs/issues/5434)

[24] DiceBear. *HTTP API — Generate SVG Avatars via URL.* [https://www.dicebear.com/how-to-use/http-api/](https://www.dicebear.com/how-to-use/http-api/)

[25] Lin F-P. *avataaars-generator (Pablo Stanley designs).* [https://github.com/fangpenlin/avataaars-generator](https://github.com/fangpenlin/avataaars-generator)

[26] Boring Designers. *Boring Avatars.* [https://boringavatars.com/](https://boringavatars.com/)

[27] Kenney. *Modular Characters (CC0).* [https://kenney.nl/assets/modular-characters](https://kenney.nl/assets/modular-characters)

[28] Kenney. *Roguelike Characters (CC0).* [https://kenney.nl/assets/roguelike-characters](https://kenney.nl/assets/roguelike-characters)

[29] OpenGameArt. *LPC Character Bases.* [https://opengameart.org/content/lpc-character-bases](https://opengameart.org/content/lpc-character-bases)

[30] OpenGameArt. *Some characters (SVG, layered).* [https://opengameart.org/content/some-characters](https://opengameart.org/content/some-characters)

[31] DiceBear. *Introduction & custom-style instructions.* [https://www.dicebear.com/introduction/](https://www.dicebear.com/introduction/)

[32] OpenAI Developer Community. *Multi-Character Prompting Suggestions for DALL-E.* [https://community.openai.com/t/multi-character-prompting-suggestions-for-dall-e-image-creation/443240](https://community.openai.com/t/multi-character-prompting-suggestions-for-dall-e-image-creation/443240)

[33] OpenAI Developer Community. *Dalle 3: Prompt for full-body portraits.* [https://community.openai.com/t/dalle-3-prompt-for-full-body-portraits/438438](https://community.openai.com/t/dalle-3-prompt-for-full-body-portraits/438438)

[34] GraphicMama. *Free Mouth Shapes Sets for Adobe Character Animator.* [https://graphicmama.com/blog/free-mouth-shapes-character-animator-puppet/](https://graphicmama.com/blog/free-mouth-shapes-character-animator-puppet/)

[35] Kreafolk. *Head-to-Body Ratio: Anime Illustration Technique.* [https://kreafolk.com/blogs/articles/anime-illustration-technique](https://kreafolk.com/blogs/articles/anime-illustration-technique)

[36] Zebtoonz. *Basic Cartoon Proportions.* [http://www.zebtoonz.com/proportions.htm](http://www.zebtoonz.com/proportions.htm)

[37] AnimSchool. *Breathing Life into Idle Animations.* [https://blog.animschool.edu/2024/06/14/breathing-life-into-idle-animations/](https://blog.animschool.edu/2024/06/14/breathing-life-into-idle-animations/)

[38] MoCap Online. *Idle Animation for Games — breathing rates and amplitudes.* [https://mocaponline.com/blogs/mocap-news/idle-animation-game-dev-guide](https://mocaponline.com/blogs/mocap-news/idle-animation-game-dev-guide)

[39] Animation Mentor. *Tutorial: How to Animate Natural Breathing Loops.* [https://www.animationmentor.com/blog/tutorial-animate-natural-breathing-loops/](https://www.animationmentor.com/blog/tutorial-animate-natural-breathing-loops/)

[40] Big Red Illustration. *Importance of Silhouette in Character Design.* [https://bigredillustration.com/articles/importance-of-silhouette-in-character-design/](https://bigredillustration.com/articles/importance-of-silhouette-in-character-design/)

[41] Webcomics.com. *Character Design: The Silhouette Test.* [https://www.webcomics.com/articles/art/character-design-the-silhouette-test/](https://www.webcomics.com/articles/art/character-design-the-silhouette-test/)

[42] Walt Disney Family Museum. *Silhouette teaching resource (PDF).* [https://www.waltdisney.org/sites/default/files/2020-05/T&T_Silhouette-final2.pdf](https://www.waltdisney.org/sites/default/files/2020-05/T%26T_Silhouette-final2.pdf)