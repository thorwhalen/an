---
name: an-art-package
description: The artist-facing contract for an `an` character — folder layout, the skeleton, required parts, slot and attachment conventions, licence fields — and how to check a delivery offline before anyone is paid for it. Use when commissioning, reviewing, receiving, or authoring character art; when writing a brief for an illustrator; or when `an character validate` reports something. Triggers on "art package", "commission a character", "the artist delivered", "character contract", "brief for an illustrator", "an character validate", "an character contract", "rig an SVG", "skeleton", "pivots".
---

# Commissioning and receiving character art

**Do not hand-write the contract. Generate it:**

```bash
an character contract
```

It is derived from `an/characters/schema.py` and from the checks in
`an/characters/validate.py`, so it cannot drift from what the validator
enforces. This skill is the *why* and the review workflow; that command is the
*what*, and it is the thing to paste into a brief.

**Why this skill did not exist until Wave 4.** A contract the compiler ignores
is worse than no contract, because it gets a human illustrator paid for work
that cannot land. Until #73/#74/#75 the compiler read three descriptor fields
and built every character from seven module constants — `bones`, `slots`,
`skins` and `view_box` had no consumer *and no producer*. Art that arrived
correct rendered identically to art that did not. The epic withheld this skill
on purpose until that stopped being true.

## The one-minute version

```
<name>/
  character.json     the descriptor — bones, slots, skins, asset_sets
  <name>.svg         the canonical drawing, containing <g id="skeleton">
  parts/             one SVG per attachment
    mouth/           the viseme set
```

```bash
an character validate <name> --out-dir <parent>   # offline, free, no render
```

Every finding names the file and what to do. `error` blocks a render; `warning`
renders but is worth fixing.

## The four things that actually matter

### 1. Aspect ratio is intrinsic to the art

The compiler places a part and scales it **uniformly**. It will never stretch a
drawing to fit a box, and there is no box to fit: a part's size is its own
extent, scaled by one factor shared with every other part.

So **draw each part at its true relative size**. A part exported at the wrong
scale is now visibly wrong rather than silently corrected.

Corollary for the exporter: a part's `width`/`height` must match its `viewBox`
extent. If they disagree the art is letterboxed inside its own texture — it
renders small, centred, in a mostly-empty raster. `an character validate`
reports it; before Wave 4 an arm drew 4 px of ink in a 28 px slot this way.

### 2. The skeleton is the rig

Draw `<g id="skeleton">` containing one named `<circle>` per joint. Those
coordinates **become the bone positions** — `an character contract` lists which
joint each bone reads (`head` ← `neck`, `arm_l` ← `shoulder_l`, and so on).

A drawing with no skeleton still works; it gets a generic rig, which is almost
never what the art wants. A partial skeleton improves the rig rather than
breaking it: bones with no matching joint keep their default placement.

**A joint id must not also be a part id.** Extraction resolves the collision by
preferring the group, which is a rule the drawing does not show.

### 3. A slot's name is its node name; an attachment's name is not

Two namespaces, deliberately:

- **Slot** — where a thing is drawn (`left_eye`, `mouth`, `torso`). This is the
  scene-graph node name, and it is what `scene.md` targets.
- **Attachment** — *which* drawing (`mouth_a`, `brow_l`). Usually follows the
  files — except where one swap set must drive several slots at once: both eye
  slots name their attachments `open`/`closed` (paths still `eye_l_open.svg`
  etc.) so the single `eyelid` set projects onto each (an#87).

A slot holds several attachments and shows one at a time. That is how a mouth
carries nine visemes, and how an eye carries open and closed.

`asset_sets[channel][key]` maps a *key* to an attachment name — `viseme.A` →
`mouth_a`. Keep it: a key is not a filename, and real mouth charts are
many-to-one (roughly ten drawings carrying forty phonemes), so collapsing them
makes the first shared drawing a schema change instead of a data change.

### 4. Provenance is not optional if the art was acquired

Populate `source`. `None` means *we made this* — a claim, not a shrug. A licence
defect is the only failure that reaches backwards through finished work: a video
shipped with an unattributed CC BY asset cannot be un-shipped. See
`an-dev-licensing`.

## Reviewing a delivery

1. `an character validate <name>` — fix every `error`, read every `warning`.
2. `an character silhouette <name>` — is it readable as a shape?
3. `an character preview <name>` — visemes and idle, in a browser.
4. Render one shot and **look at it**. Three layout bugs in Wave 4's own build
   were invisible in every metric and obvious in a frame.

## What the validator cannot tell you

- **Whether the drawing is any good.** It checks that art *can* render, not that
  it *should*.
- **Whether a part is the right part.** A torso in `head.svg` passes everything.
- **Whether the skeleton matches the drawing.** Joints are read as coordinates;
  a shoulder pivot drawn at the elbow yields a rig that is valid and wrong.
- **Sub-pixel or stylistic consistency across a set.** That is `lookbook`'s
  problem, not this one's.

## Traps

- **A part that draws nothing passes every existence check.** That is the whole
  reason `validate` opens files now; `tests/fixtures/art/invisible_head.svg` is
  the regression fixture, and it is deliberately not empty.
- **A degenerate part hangs the render, it does not fail it** — `<svg/>`,
  malformed XML, or a zero-dimension root make the asset loader never settle.
  There is a deadline now (#79), but the art is still wrong.
- **A missing part is audible, not fatal, by default.** Pass
  `strict_assets=True` when you are measuring pixels. A slot that still draws
  *something* — open eyes but no closed ones — is reported as incomplete and is
  deliberately **not** a fallback, so a rig without a blink still renders.
- **`REQUIRED_PARTS` includes both eye states.** Both committed corpus rigs are
  missing the closed ones, so a validate on them fails honestly today.
