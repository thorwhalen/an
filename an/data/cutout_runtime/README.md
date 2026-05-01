# an cutout runtime

A small, dependency-light JS runtime that consumes a `CutoutSceneJSON` (the contract produced by `an.adapters.cutout.compile`) and renders it into a `<canvas>` via PixiJS v7.

This is the executor for the cutout style. Phase 2C drives it headlessly via Playwright to produce mp4s; you can also open `index.html` in a browser and call `window.anLoadScene(json)` from the devtools console to inspect a scene interactively.

## Files

- `index.html` — loads PixiJS (CDN) and `runtime.js`, then surfaces a `<canvas id="stage">`.
- `runtime.js` — builds a PIXI scene tree from JSON, evaluates the timeline at a given time, and exposes:
  - `window.anLoadScene(sceneJson)` — initialize PixiJS app + build the scene tree.
  - `window.anSetTime(t)` — seek to time `t` (seconds), evaluate the timeline, apply the pose, and render one frame.
  - `window.anCanvasReady()` — `true` once the app is initialized.
  - `window.anRuntimeVersion` — semver string.

## Notes

- PixiJS is loaded from CDN (`cdn.jsdelivr.net/npm/pixi.js@7.4.2/dist/pixi.min.js`). For fully offline rendering, vendor a copy under `vendor/pixi.min.js` and update the `<script>` `src` in `index.html`.
- Phase 2B intentionally renders all `Visual`s as colored rects, even when `kind="sprite"`. Real texture loading lands in a later sub-phase once the asset pipeline is wired.
- Pose keys use `target::property` (double-colon), matching the way `runtime.js` flattens the `(target, property)` tuple. Python tooling uses tuples; the colon form is just a JS convention.
