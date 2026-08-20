# an cutout runtime

A small, dependency-light JS runtime that consumes a `CutoutSceneJSON` (the contract produced by `an.adapters.cutout.compile`) and renders it into a `<canvas>` via PixiJS v7.

This is the executor for the cutout style. Phase 2C drives it headlessly via Playwright to produce mp4s; you can also open `index.html` in a browser and call `window.anLoadScene(json)` from the devtools console to inspect a scene interactively.

## Files

- `index.html` — loads the vendored PixiJS and `runtime.js`, then surfaces a `<canvas id="stage">`.
- `runtime.js` — builds a PIXI scene tree from JSON, evaluates the timeline at a given time, and exposes:
  - `window.anLoadScene(sceneJson)` — initialize PixiJS app + build the scene tree.
  - `window.anSetTime(t)` — seek to time `t` (seconds), evaluate the timeline, apply the pose, and render one frame.
  - `window.anCanvasReady()` — `true` once the app is initialized.
  - `window.anRuntimeVersion` — semver string.

## Notes

- PixiJS 7.4.2 is **vendored** at `vendor/pixi.min.js`, with its MIT notice beside it at
  `vendor/pixi.LICENSE.txt` (the minified banner names the licence but carries neither the
  copyright line nor the permission text, so it does not discharge the obligation alone).
  Both files are pinned by sha256 in `tests/test_vendored_engine.py` — replace the file and
  the digest together, and take the bytes from the npm tarball, whose sha512 can be checked
  against the registry's published `dist.integrity` before unpacking.
- Nothing here reaches the network. A render is hermetic and
  `tests/test_vendored_engine.py` proves it by rendering with every non-loopback request
  aborted — a Python-level socket guard cannot see Chromium's fetches, so that browser-level
  check is the only thing that can.
- `kind="svg_sprite"` renders a real texture; `kind="sprite"` still falls through to a
  coloured rect, which is a live gap rather than a phase note.
- Pose keys use `target::property` (double-colon), matching the way `runtime.js` flattens the `(target, property)` tuple. Python tooling uses tuples; the colon form is just a JS convention.
