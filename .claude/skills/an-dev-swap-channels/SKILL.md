---
name: an-dev-swap-channels
description: How swap channels work in the `an` repo — the one generic replacement-animation mechanism (an#87) that viseme, eyelid, hands, body_facing and every future set ride. Use when touching `asset_sets`, the per-slot projection in `compile.py`, `applySwap`/`applyProperty` in `runtime.js`, `VisualJSON.asset_sets`, swap validation, texture aliases, or when adding a new swap set or authoring swaps from scene.md. Triggers on "swap channel", "asset set", "attachment swap", "texture swap", "viseme special case", "the mouth doesn't change", "unknown swap key", "add a hands set", "turnaround", "body_facing", "eyelid".
---

# Swap channels: one implementation, sets as data

**Authority: `misc/docs/wave5_research.md` (measured 2026-08-24).** When this file
and that one disagree, the research doc wins; when the code disagrees with both,
the code wins and you fix the other two. The wave that built this is epic #9
Wave 5; the build is an#87 (PR-B), on the ground an#86 (PR-A) prepared.

## The one-paragraph model

A channel whose property is **not** a transform (`x`, `rotation`, `alpha`, …)
names a **swap set**. The descriptor declares sets as
`asset_sets: {set: {KEY: attachment_name}}`; the compiler **projects** each set
onto every slot whose attachments carry its values and stamps the resolved
`{set: {KEY: texture_alias}}` onto that node's `VisualJSON.asset_sets`; the
runtime's `applyProperty` default case finds the node's child visual whose
`_anAssetSets` (texture swap) or `_anDrawSets` (procedural redraw) contains the
property and applies the key — or **throws naming node, set, and known keys**.
`viseme` is a conventional set name riding this path; so is everything else.
There is no other swap implementation, and the epic forbids a second.

## Load-bearing decisions (each measured; do not relitigate casually)

1. **The property IS the set name** (scheme i). `property="swap"` was rejected
   (two sets on one node collide on the `target::swap` pose key with an
   emission-order winner nothing pins); `swap:<set>` buys nothing over the
   reservation check. Corollary: a set name must not collide with
   `compile.py::_PROPERTY_REST_VALUES ∪ {rotation_rad}` or contain `::`/`/` —
   the static switch would silently shadow it.
2. **Binding is projection, not declaration.** `asset_sets` carries no slot
   field; the skin IS the binding. A set may project onto several slots — that
   is how ONE `eyelid` set drives both eye slots, whose attachments share the
   per-slot keys `open`/`closed` (the 0.3.0 migration renamed them from
   `eye_l_open` for exactly this). **Gotcha**: sharing attachment names is
   what OPTS a slot into a set — give a hand slot an attachment named `open`
   and the eyelid set will project onto it. Per-slot attachment names are the
   membership mechanism, name them deliberately.
3. **Texture aliases are slot-qualified** (`{entity}.{slot}.{attachment}`).
   The old `{entity}.{attachment}` space was silently first-wins on cross-slot
   collision, and shared per-slot keys make collisions the NORM.
4. **Swap channels are stepped by FORMAT** (Spine's attachment keyframes carry
   `{time, name}`, no curve field). The compiler forces `easing="step"` on a
   swap tween and warns; the evaluator additionally snaps non-numeric values
   on TIME (`t >= b.time`), so easing could not move a swap even if emitted
   (an#86 — the eased and raw-parameter snaps were both measured wrong).
5. **The value domain is loud.** Authored swap on an undeclared set/key →
   `CutoutCompileError` naming the declared ones (also caught pre-render by
   `an/ir/validate.py::_check_swap_references`). A **used** key whose art is
   missing → dropped with a warning AND a fallback record (fatal under
   `strict_assets`); an unreferenced inventory gap stays a mute 'incomplete' —
   usage-aware escalation, deliberately NOT the blanket rule (which would
   brick every rig without closed-eye art). The runtime throw is for
   hand-written scenes: compiled scenes are total by construction.
6. **Sets compile to HOLD channels.** All `set` actions on one
   (target, property) merge into one step channel spanning first-`at` → shot
   end. Never reintroduce the 0.001s placement window: a set at a
   non-frame-aligned time silently never fired, and persistence was an
   accident of stateful forward rendering.
7. **The swap carries texture only.** Placement, anchor, and fit box are baked
   from the DEFAULT attachment; `refitToBox` re-fits on every swap (keep it —
   without it every key inherits the previous texture's scale). Per-key
   geometry is not expressible; `validate_character` warns when a set's
   attachments declare differing geometry. If per-key geometry becomes real
   work, it is a wire-contract change — design it, don't bolt it on.
8. **Preload needs nothing.** Every attachment of every slot of the active
   skin is registered/staged/loaded up front, precisely so a swap's texture
   is GPU-ready when the key changes. Cross-skin swaps are out of scope.

## Adding a new set (a data change, by design)

1. Declare it in the descriptor: `asset_sets["props"] = {KEY: attachment}`.
2. Put the attachments (and their files) in the slot(s) that should swap.
3. Author `{kind: set, target: <entity>/<node>, property: props, value: KEY}`.

No compiler, runtime, serialize, or schema change. The proof of that claim is
`tests/test_swap_channels.py::test_the_renderer_knows_nothing_about_the_fixture_sets`
— the committed fixture (`tests/fixtures/characters/gale/`, `hands` +
`body_facing`) animates with zero occurrences of its set names in either file.
If your new set needed code, you broke the generalisation; stop and fix that.

## The mutation-tested traps (keep them killed)

- **(a)** non-step easing on a swap tween → forced to step, with a warning
  (`test_trap_a_...`); the evaluator's time-based snap is pinned by
  `test_cutout_channel_parity.py`.
- **(b)** unknown swap key → loud at every layer: compile
  (`test_trap_b_an_undeclared_key_...`), runtime
  (`test_trap_b_the_runtime_throws_...` — executed against the extracted
  `applySwap`, not grepped). The old viseme path had SEVEN silent failure
  paths (research §1); do not reintroduce any as a "fallback".

## Where things live

| What | Where |
|---|---|
| Declared sets, eyelid keys, 0.3.0 migration | `an/characters/schema.py` |
| Projection + aliases + swap checks + hold channels | `an/adapters/cutout/compile.py` (`_swap_vocabulary`, `_check_swap_action`, `_build_svg_character_subtree`, `_compile_actions`) |
| Wire carrier | `serialize.py::VisualJSON.asset_sets` |
| The one applier | `runtime.js::applySwap` (+ `applyProperty` default case; `_anDrawSets` for the procedural mouth) |
| Pre-render validation | `an/ir/validate.py::_check_swap_references` (transform list duplicated-and-pinned); `an/characters/validate.py::_check_asset_sets` |
| Proof fixture + tests | `tests/fixtures/characters/gale/`, `tests/test_swap_channels.py` |
| Demo | `misc/demos/build_demos.py::_build_swap_channels` |
