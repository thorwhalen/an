---
name: an-dev-runtime-assets
description: Use when adding, moving, renaming or removing any non-Python file that the renderer loads at run time — anything under `an/data/`, a vendored JS bundle, an SVG part, a font, an HTML page. Also use when a render looks wrong rather than failing, when something works in the dev tree but not from a `pip install`, or when a "fix" to a data file appears to have no effect. Triggers on "vendor", "bundle", "asset", "force-include", "packaging", "it works locally but not installed".
---

# an-dev-runtime-assets — the files that are not code

## The silent failure this prevents

A runtime asset that is missing from the wheel **works perfectly in the editable dev
tree and vanishes for every `pip install` user**. And it does not vanish loudly: the
renderer degrades. A missing SVG texture becomes `PIXI.Texture.WHITE` — a white
rectangle in the frame. A missing engine bundle gives `ReferenceError: PIXI is not
defined` inside the browser, where nothing in the Python stack is watching.

So the bug reaches the user as *"the animation looks wrong"*, in a build nobody on the
team runs, and it is attributed to whatever art or scene change shipped alongside it.

## Rule 1 — verify by building a wheel, never by reasoning about the build backend

The packaging config is not the answer. `pyproject.toml` has a
`[tool.hatch.build.targets.wheel.force-include]` block listing three files:

```toml
"an/data/cutout_runtime/index.html" = "an/data/cutout_runtime/index.html"
"an/data/cutout_runtime/runtime.js" = "an/data/cutout_runtime/runtime.js"
"an/data/cutout_runtime/README.md"  = "an/data/cutout_runtime/README.md"
```

Reading that, you would have concluded `preview.html` — in the same directory, and
served by `an preview` — was missing from the wheel. **It was not.** `packages = ["an"]`
already ships every non-ignored file under `an/`, so the block was redundant, and
`preview.html` was present all along.

(The list is now a *complete* inventory of the runtime assets, with
`test_vendored_engine.py` keeping it complete — precisely so nobody has to make that
inference again. The lesson is about the inference, not the list.)

The point is not that the block is harmless. The point is that both the pessimistic and
the optimistic reading of that config were available and one of them was wrong. Build the
wheel:

```bash
python -m pip wheel . --no-deps -w /tmp/anwheel -q
python - <<'PY'
import glob, zipfile
w = glob.glob('/tmp/anwheel/an-*.whl')[0]
for n in sorted(zipfile.ZipFile(w).namelist()):
    if '/data/' in n or n.endswith(('.html', '.js', '.svg', '.json')):
        print(n)
PY
```

Two consequences worth internalising:

- **`.gitignore` is part of the build config.** Hatchling excludes VCS-ignored files by
  default, so a vendored bundle dropped into an ignored path silently does not ship.
- **A new file type is a new question.** "The last asset shipped, so this one will" is
  the reasoning that produced the wrong answer above.

## Rule 2 — a missing asset must fail loudly, in the language of the thing that is missing

Degradation is the enemy here, and this codebase degrades in three places:

| Site | Degrades to | Should |
|---|---|---|
| `_stage_character_assets` — declared texture whose file is absent | `continue`, silently | warn naming the alias, the declared `src`, and the store root it resolved against |
| `_stage_character_assets` — `src` not starting with `characters/` | `continue`, silently | resolve through the prefix→store table; an unknown prefix is a programming error and raises |
| `runtime.js` `makeSvgSprite` — `PIXI.Assets.get(alias)` returns nothing | `PIXI.Texture.WHITE` | say so — a white rectangle is indistinguishable from art |

The rule: **the error names the missing thing and where it was looked for.** "Texture
not found" costs a debugging session; "texture `head` declared as
`characters/charlie-v1/parts/head.svg`, not found under `<store root>`" costs nothing.

## Rule 3 — an asset the renderer fetches over the network is not an asset, it is a dependency

This *was* the state before #12: `index.html` and `preview.html` loaded the engine from a
CDN at render time. Three things followed, and each was a real defect rather than a style
preference:

1. **A cold render needs the network**, which breaks the offline-hermetic test rule the
   rest of the suite is held to.
2. **The bytes executed are not the bytes in the repo.** The shot cache is keyed on
   `shot.id`; a third party can change the renderer under you without changing any cache
   key, so a stale artifact is served forever.
3. It is a supply-chain surface for a package that has none otherwise.

Vendoring was the fix, and it is also a licensing act: a vendored MIT bundle ships with
its notice. See `an-dev-licensing`.

**The rule this leaves behind:** nothing under `an/data/` may fetch at render time.
`test_vendored_engine.py` enforces it two ways — statically, and by rendering with every
non-loopback request aborted.

**Test it the way it actually fails.** A Python-level socket guard will not see this —
the fetch happens inside the browser process. Prove hermeticity at the Playwright layer
by aborting every non-loopback request and asserting the render still succeeds. Loopback
must stay open: the renderer drives Chromium at its own local HTTP server.

## Rule 4 — when a data-file change appears to have no effect, suspect the cache before the code

Two caches will lie to you, and both have bitten this repo:

- **`__pycache__` and mtime.** Restoring a Python file with `mv`, `cp -p`, or anything
  else that rewinds its mtime leaves a `.pyc` that is *newer* than its source, so Python
  keeps using the stale bytecode. A mutation test then "passes" while the mutation is
  still live. Clear `__pycache__` and `touch` the file, or restore with a plain write.
- **The per-shot mp4 cache**, keyed on `shot.id`. It does not currently include the
  renderer's own hash, so editing `runtime.js` does not invalidate it. Delete the shot
  from the artifacts store (`del mall["shots"][shot_id]`) — invalidation here is by
  deletion, by design.

## Checklist for any asset change

- [ ] Wheel built and the file listed in its contents — the command above, not an argument.
- [ ] The path is not `.gitignore`d.
- [ ] Absence produces a named error or a named warning, not a white rectangle.
- [ ] Nothing is fetched from a network at render time.
- [ ] If it is third-party, its licence and notice ship with it (`an-dev-licensing`).
- [ ] `__pycache__` cleared before believing any before/after comparison.
