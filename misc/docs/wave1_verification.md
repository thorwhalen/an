# Wave 1 verification record

Issue #10 of epic #9. This is **not** a survey — the survey is done and its conclusions
are in the epic. This is the narrow re-verification the epic's first wave depends on:
four questions whose answers the implementation PRs consume directly.

Every licence claim below was read from the licence file at the pinned version. Every
code claim carries a `file:line`. Where something could not be verified it says so
rather than guessing.

---

## 1. The vendored engine

The runtime fetches PixiJS from a CDN at render time, from exactly two places —
`an/data/cutout_runtime/index.html:13` and `preview.html:27`. Those are the only external
URLs anywhere in the runtime directory.

### Licence

| | |
|---|---|
| SPDX | `MIT` |
| Copyright | `Copyright (c) 2013-2023 Mathew Groves, Chad Engler` |
| Read at | `https://raw.githubusercontent.com/pixijs/pixijs/v7.4.2/LICENSE` (the tag, not a branch) |
| Tag object | `refs/tags/v7.4.2` → `b17730e98f5b5d980b9c3a6865caa07d3ee4e5f2` |
| File | 1092 bytes, sha256 `5ce7447bc57f7349ffc48338782fbcabe613696e00712b20d66bc58e780f9473` |

The `LICENSE` inside the npm tarball is **byte-identical** to the one at the git tag (same
digest), so the vendoring step never needs to reach GitHub.

**The minified bundle's own banner does not discharge the MIT notice.** MIT requires the
full copyright line *and* the full permission notice "in all copies or substantial
portions"; the banner names the licence and carries neither. So the licence file ships
alongside the bundle. This is the obligation, not a nicety.

### The artifact

| | |
|---|---|
| Source of record | `https://registry.npmjs.org/pixi.js/-/pixi.js-7.4.2.tgz` → `package/dist/pixi.min.js` |
| Size | 456,133 bytes |
| sha256 | `9ddba9cd78bc8610a1d445ec939393888be83925c78e40d66d9a17e98450228d` |
| Global | line 8 begins `var PIXI=function(_){"use strict";…` — matches the `window.PIXI` the runtime expects |

The jsDelivr bytes and the npm tarball bytes are identical (verified with `cmp`), but
**the tarball is the source of record** because only it carries a registry integrity hash
and signature to check against before unpacking.

### Four findings that change how it is done

- **`vendor/`, not `dist/`.** `.gitignore:21` contains `dist/`, so a bundle vendored there
  would be silently dropped from git and therefore from the wheel — the exact silent
  packaging failure `an-dev-runtime-assets` exists to prevent. `git check-ignore -v` on the
  `vendor/` path exits 1 (not ignored).
- **No sibling files.** `importScripts`: 0 hits. `.wasm`: 0 hits. The two `new Worker`
  call sites both build from an inline Blob. The only external reference is a trailing
  `//# sourceMappingURL=pixi.min.js.map` comment. **Do not vendor the map** (2,446,822
  bytes, 5.4× the bundle) and **do not edit the comment out** — keeping the file unmodified
  is what lets the pinned digest stay checkable against npm forever.
- **Windows CI needs `.gitattributes`.** The repo's `.gitattributes` has one line
  (`*.ipynb linguist-documentation`) and `git check-attr -a` on the vendor path returns
  nothing. Without `an/data/cutout_runtime/vendor/pixi.min.js -text`, the Windows leg
  CRLF-converts the file and any digest assertion goes red on Windows only.
- **No Python change is needed.** `render.py:208` and `preview.py:74` both
  `shutil.copytree(runtime_dir(), …)`, and `_serve_dir` serves subdirectories, so a new
  `vendor/` directory is picked up with no code change at all.

### Packaging

`pyproject.toml:47-50` force-includes three files, which reads as though everything else
is excluded. **It is not** — `packages = ["an"]` already ships every non-ignored file under
`an/`. Verified by building the wheel and listing its contents, not by reading the config:
`preview.html` is present despite not being force-included.

Both readings of that config were available and one was wrong, which is the whole argument
for verifying by build. See the `an-dev-runtime-assets` skill.

### Blast radius

`rg -n -i 'jsdelivr|cdn' tests/` returns nothing — no test asserts on the CDN URL.
`tests/test_cutout_runtime_files.py:17-22` only asserts `index.html` contains `runtime.js`
and `<canvas`, both of which survive.

Three docs state the CDN dependency as a known gap and must be corrected in the same PR:
`README.md:213-214`, `CLAUDE.md`, and `an/data/cutout_runtime/README.md:18`.

---

## 2. DiceBear style licences

### What the code does today

The default style is `adventurer`, a module constant. The API version is pinned to `9.x`.
The style is a plain string, validated only against a hardcoded 27-name tuple, and **only
at the CLI layer** — the library path does no validation at all.

**No licence and no attribution is recorded anywhere**: not in code, not in the descriptor,
not in the generated character directory, not in the README. The word appears exactly once
in `an/`, in a comment suggesting metadata *could* hold it.

One doc correction while we are here: the module docstring justifies the `9.x` pin by
saying "9.x is supported through 2028". DiceBear's own HTTP-API documentation lists 9.x and
10.x as both Active with End of Life **None**; April 30 2028 is the EOL for the
*deprecated* 5.x–8.x line. The pin is fine; the stated reason is wrong.

### The split that matters

The DiceBear **software** licence (MIT) is a separate fact from each **style** licence.
DiceBear itself makes the split explicit inside every per-style `LICENSE` file, under
literal `# Design` and `# Code` headings. Reading the repo's top-level MIT and concluding
the avatars are MIT is the trap.

### The table

Of the 27 styles `an` can request, **11 are CC0 1.0** (no attribution duty), 12 are
**CC BY 4.0** (real attribution duty), and the Pablo Stanley set carries bespoke
"free for personal and commercial use" terms that are *not* a Creative Commons licence.

| Style | Designer | Licence |
|---|---|---|
| `lorelei`, `lorelei-neutral` | Lisa Wischofsky | **CC0 1.0** |
| `notionists`, `notionists-neutral` | Zoish | **CC0 1.0** |
| `open-peeps` | Pablo Stanley | **CC0 1.0** |
| `pixel-art`, `pixel-art-neutral` | DiceBear | **CC0 1.0** |
| `identicon`, `shapes`, `thumbs` | DiceBear | **CC0 1.0** |
| `icons` | The Bootstrap Authors | MIT (no design/code split) |
| `initials` | Florian Körner | MIT (renders text, not a character) |
| **`adventurer`, `adventurer-neutral`** | Lisa Wischofsky | **CC BY 4.0** ← current default |
| `big-ears`, `big-ears-neutral` | The Visual Team | CC BY 4.0 |
| `big-smile` | Ashley Seo | CC BY 4.0 |
| `croodles`, `croodles-neutral` | vijay verma | CC BY 4.0 |
| `fun-emoji` | Davis Uche | CC BY 4.0 |
| `micah` | Micah Lanier | CC BY 4.0 |
| `miniavs` | Webpixels | CC BY 4.0 |
| `personas` | Draftbit | CC BY 4.0 |
| `avataaars`, `avataaars-neutral`, `bottts`, `bottts-neutral` | Pablo Stanley | "Free for personal and commercial use" — **not** a CC licence |

All 27 names exist as packages at the pinned major; none is stale.

### The recommendation, and why it is not just "pick any CC0 one"

**Move the default to `lorelei`.**

- It is CC0 1.0 — zero attribution duty, zero notice-retention duty.
- **It is a head-and-shoulders bust**, and that is the load-bearing constraint:
  `wrap_dicebear_for_an` pastes the whole avatar in as the single `head` part on a
  generated stick body. `notionists` and `open-peeps` are the other CC0 human styles and
  both render **half-body** characters, which would put a torso on a torso.
- It is by Lisa Wischofsky, the same artist as the outgoing `adventurer`, so the demo art
  barely shifts.

`pixel-art` is the CC0 fallback if a bust-shaped alternative is ever needed. Keep all 27
requestable — only the default moves; do not silently drop `adventurer`.

### The attribution string

Verbatim from the per-style package README at the pinned major, which is DiceBear's own
template:

> The avatar style is based on {SOURCE_TITLE} by {DESIGNER}, licensed under {LICENSE}
> ({LICENSE_URL}). / Remix of the original.

`an` genuinely produces a **modified** work (it wraps the avatar into a rig), so CC BY's
"indicate if changes were made" clause is live, not theoretical — which is what the
"Remix of the original" half discharges.

---

## 3. The offline network guard

### Adopt `illustration`'s shape; do not invent a third

Its guard lives at the repo root (`illustration/conftest.py`), patches exactly three
callables — `socket.socket.connect`, `socket.socket.connect_ex`, `socket.getaddrinfo` —
and derives its exception from **`BaseException`, not `Exception`**, specifically so
fail-soft `except Exception` paths cannot swallow it.

**The load-bearing detail is that refusal and recording are two separate mechanisms.**
`refuse()` appends to an `attempts` list *before* raising; the fixture yields that list and
asserts it is empty at teardown. This matters here more than it does in `illustration`,
because `an` degrades network failures silently in its own code —
`an/characters/factory.py:137-151` catches the `RuntimeError` from `fetch_dicebear` and
falls back to generated geometry. A refusal alone gets absorbed into a still-green test;
the *record* is what holds the line.

`install_network_guard` and `fail_on_outbound_attempts` are deliberately module-level
functions rather than fixture-internal, so both halves are individually testable — and a
test file that tests the conftest is what keeps the guard armed.

Two things change for `an`: the opt-out marker is `live_api` (already registered, so no new
plumbing), and it appends to the existing `tests/conftest.py` rather than creating a root
one, so the existing `from .conftest import` call sites keep working.

### The blind spot, measured

**A Python socket patch cannot see Chromium.** With the guard installed,
`tests/test_cutout_render.py` (3 tests) and `tests/test_render_visible_content.py` (1 test)
all **pass while Chromium downloads PixiJS from jsDelivr** — the fetch happens in another
process.

The browser-level mechanism is Playwright route interception: `page.route(url, handler)`,
with `route.abort("blockedbyclient")` for anything non-loopback. The renderer exposes no
hook to install a route, so a test must wrap `Browser.new_page`; only that call fires on
`an`'s code path.

**Proven both ways.** With all non-loopback requests aborted, the repo as it stands *fails*
— `Page.wait_for_function: Timeout 15000ms exceeded`, blocked list exactly
`['https://cdn.jsdelivr.net/npm/pixi.js@7.4.2/dist/pixi.min.js']` — and passes once the
bundle is vendored. That is the regression test for #12, and it is the only test that can
distinguish "we vendored it" from "we vendored it and the page actually uses it".

A render fetches only three things over HTTP: `index.html`, the engine, and `runtime.js`.
`scene.json` is injected via `page.evaluate`, not fetched.

### Two coverage gaps to state rather than paper over

- `testpaths = ["tests"]`, so a `tests/conftest.py` guard covers a bare `pytest` completely
  — but **not** the `pytest -q --doctest-modules an/` sweep that `CLAUDE.md` documents.
- **CI will skip the browser-level test entirely**: the workflow installs `.[dev]`, and
  `playwright` lives in the `cutout` extra. This is not specific to the new test — it means
  *no* browser test in this repo has ever run in CI, and every "verified by rendering"
  claim is verified only on a developer machine. Tracked separately; it is a CI-scope
  decision, not part of #12.

---

## 4. The silent-discard inventory

Six sites accept something and produce nothing, with no diagnostic. The repo rule
forbids breaking existing tests without asking, so this is the inventory taken *before*
making them raise. It is the input to #15, not to #12.

**Headline: all six are safe.** Verified empirically rather than by grep — each site was
patched to raise, independently and then all together, and the suite stayed at zero
failures with the doctest sweep green. No test exercises any of the five IR sites,
deliberately or incidentally.

| # | Site | Today |
|---|---|---|
| 1 | An unrecognised `camera.move` | Returns silently. Handled: `push_in`, `pull_out`, `zoom_in`, `zoom_out`. `hold` early-returns through the same condition and is a *correct* no-op. |
| 2 | `PlayAction` | Compiles to a channel-less empty clip — named reusable animations are a no-op (#7). The `__play__` prefix guard in the defensive re-pass is dead code: nothing ever produces that prefix. |
| 3 | `Shot.narration` | Fully modelled; the audio pipeline walks only `shot.dialogue`. No audio, no picture. |
| 4 | `prop` entities | Accepted by `AssetRef.kind`, dropped by the compiler's entity loop. |
| 5 | The environment-store override | `preset.update({k: v for k, v in override.items() if k in preset})` — intersects with preset keys, so anything new is read and discarded. |
| 6 | `applyProperty`'s `default:` in `runtime.js` | Ignores unknown properties silently. |

### What the inventory adds beyond "it is safe"

- **Exactly one name is advertised and unhandled: `pan_left`**, in the IR schema's own
  comment on `Camera.move`. Every other enumeration in the repo lists the five handled
  names. Delete it or implement it — otherwise the first thing the new error does is
  contradict the schema.
- **Site 6 is safe but not vacuous.** No current code path sends an unhandled property
  (verified by rendering, not only by grep: with `default:` throwing, the full suite is
  green and the real headless renders genuinely ran). But a `tween` on `visible` *does*
  hard-fail under the patched runtime, so the guard would fire on real authoring.
- **Site 6 needs a wrapper or it violates the typed-error convention.** A JS throw escapes
  `page.evaluate` as a raw `playwright._impl._errors.Error`, which is not wrapped in
  `CutoutRenderError`. Catch it in `_capture_frames` and re-raise naming the frame time and
  the property.
- **Do not port `pose.py`'s allow-list verbatim.** `_ALLOWED_NODE_PROPS` lacks `viseme` and
  `alpha`; `apply_pose` has no production caller and has drifted from what the runtime
  actually handles. The runtime's own case list is the truth, and `pose.py` should be fixed
  to match in the same pass.
- **A seventh site, not in #15's table:** `applyPose` silently skips an unknown *target*.
  It is the sibling of `applyProperty` and is why a mistyped target path — or a viseme
  channel aimed at a face-baked character's absent mouth node — produces nothing at all.
- **The typed-error convention**, for whoever implements this: nine error classes, every one
  a direct `RuntimeError` subclass, each defined in the module that raises it, each carrying
  actionable text. No shared base, no error module. `ValueError` is the existing idiom for
  "this value is not in the known set". The "zero bare `NotImplementedError`" claim in
  `CLAUDE.md` is true and was re-verified.
- **A near miss worth knowing:** a test authors `set_("a", "visible", True)`, a property the
  switch does not handle. It survives only because that test flattens in pure Python and
  never renders.

### The one real risk

`an.iterate` lets an LLM patch `actions` with arbitrary property names, and its system
prompt never enumerates the legal ones. Today a hallucinated `opacity` tween is silently
inert; after #15 it becomes a hard render failure. Enumerating the animatable properties in
that prompt belongs in the same PR.
