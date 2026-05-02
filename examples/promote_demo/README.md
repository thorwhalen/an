# promote_demo — lift a hand-drawn SVG into a reusable character

The shortest demonstration of `an.characters.promote`: one hand-drawn
SVG → a fully sliced, lip-syncable character in the project mall.

## What's here

```
promote_demo/
├── assets/characters/
│   └── raw_maya/raw_maya.svg     # hand-drawn input (Pose Animator convention)
├── scene.md                      # references "maya-promoted"
├── build.py                      # promote → render pipeline
└── README.md
```

`raw_maya.svg` follows the **Pose Animator convention** baked into
`an/characters/svg_utils.py`:

- A `<g id="skeleton">` of named `<circle>` pivots — one per joint.
- A `<g id="illustration">` containing named part groups (`head`, `torso`,
  `arm_l`, `arm_r`, `leg_l`, `leg_r`, plus optional `brow_l` / `brow_r` /
  `eye_l_open` / `eye_r_open`).

`promote()` extracts each named group into a standalone part SVG, writes a
`character.json` descriptor, and synthesizes any missing parts (e.g. eyes
and the 9-shape default mouth set, since the input doesn't ship them).

## Try it

```bash
# from the repo root
PYENV_VERSION=p12 python examples/promote_demo/build.py
```

The script:

1. **Promotes** `raw_maya` into `assets/characters/maya-promoted/`:
   ```
   maya-promoted/
   ├── character.json             # descriptor (slots, mouths, viseme map)
   ├── maya-promoted.svg          # normalized full-character SVG
   └── parts/
       ├── head.svg               # ← from <g id="head"> in raw_maya.svg
       ├── torso.svg              # ← from <g id="torso">
       ├── arm_l.svg / arm_r.svg
       ├── leg_l.svg / leg_r.svg
       ├── brow_l.svg / brow_r.svg
       ├── eye_l_open.svg / eye_l_closed.svg / eye_r_*.svg   # synthesized
       └── mouth/mouth_a.svg … mouth_x.svg                   # synthesized
   ```
2. **Renders** `scene.md` (which references `maya-promoted`) to
   `output/main.mp4`.

## The promote flow in one breath

```python
from an.characters import promote

promote(
    project_dir="examples/promote_demo",
    entity="raw_maya",          # source: assets/characters/raw_maya/raw_maya.svg
    as_="maya-promoted",        # target: assets/characters/maya-promoted/
    overwrite=True,
)
```

Re-edit `raw_maya.svg`, re-run, and the rig regenerates from the source.

## Caveats / known limitations

- The hand-drawn input ships with brows but no eyes; promote synthesizes
  open/closed eye SVGs in those cases. If you author your own eyes,
  use `<g id="eye_l_open">`, `<g id="eye_l_closed">`, etc.
- Mouth shapes are *always* the offline default 9-shape set after promote
  — overwrite `parts/mouth/mouth_*.svg` afterwards if you want hand-drawn
  visemes.
- The skeleton's `<circle>` pivots are recorded under
  `metadata.pivots_detected`. The cutout adapter doesn't drive bone-space
  motion from them yet (Phase 11b uses fixed scene-graph offsets), but
  they're the hook for future Live2D-style deformation.
