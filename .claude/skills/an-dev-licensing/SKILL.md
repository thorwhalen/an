---
name: an-dev-licensing
description: Use before adding, upgrading, vendoring or swapping ANY third-party dependency, model weight, font, art asset or JS bundle in the an repo — and before writing any licence claim into a doc, comment or PR body. Also use when choosing between two libraries that solve the same problem, when a search result recommends the obvious package, and when deciding whether an asset an end user's video will contain may ship. Triggers on "add a dependency", "which library", "vendor", "is X permissive", "can we use", "licence", "license", "attribution", "model weights".
---

# an-dev-licensing — the perimeter

`an` produces artifacts a user ships. That makes it a commercial-adjacent product, and the
licence question is not paperwork: **a licence defect is the only failure that reaches
backwards through completed work.** A video shipped with an unattributed CC BY asset cannot
be un-shipped. Everything else on the roadmap can be fixed forward.

**An unverifiable licence is a refusal, not a warning.**

## Rule 1 — the chip is not evidence, in either direction

Never take a licence from a GitHub repo header, an npm `license` field, a PyPI classifier,
or a summary written by anyone (including a previous agent, including this file). Open the
licence file at **the version you are pinning** and read it. Record the URL you fetched.

From one verification pass across ~60 candidates, three would have been mis-verdicted by
trusting the chip **in both directions**: one shows Apache-2.0 and hides an AGPL subtree;
one shows Apache-2.0 and hides an in-file non-commercial notice; one has no licence file at
all, with the terms living only at a URL. Two more show `NOASSERTION` and are plainly
permissive.

So: `NOASSERTION` is a signal to read the file, and an SPDX chip is *also* a signal to read
the file.

## Rule 2 — code, weights and editor are three separate licences

This is where the traps concentrate, and every one of these is real:

| Shape | Example |
|---|---|
| Permissive package, non-commercial default model | `rembg.remove(img)` with no session silently downloads a CC-BY-NC model. The package is MIT; the model is not. Pin an explicit session, and **assert the pinned name in a test** — a comment will not survive a future agent "fixing" the dependency. |
| Permissive runtime, per-seat obligation on *your users* | Spine's runtimes are free to integrate, but "each user of the Products must obtain their own Spine Editor license". For a library, that liability is transitive. Disqualifying. |
| Permissive runtime, proprietary authoring | Rive's runtime is genuinely MIT; producing `.riv` needs a per-seat SaaS editor. Not a violation — a pipeline commitment. Decide it as one. |
| Permissive wrapper over a copyleft binary | The GPL phonemization cluster: several "MIT" loaders exist whose purpose is to install GPL binaries. Every path terminates at GPL-3.0. |
| Source-available, reads as open | Remotion: employee-count cap on free use, and an explicit prohibition on distributing a derivative renderer — which is what `an`'s backend is. |
| Licence changed since you last looked | GSAP's terms changed in 2025: free for commercial use, but prohibited uses reach visual animation builders. |

## Rule 3 — vendoring is a licensing act

A vendored MIT bundle ships **with its notice**, because MIT requires the copyright line
*and* the permission text "in all copies or substantial portions". A minified banner that
names the licence and links to it discharges **neither** — verified against the vendored
PixiJS bundle, whose banner has no copyright line and no permission text.

The in-repo pattern to copy (`an/data/cutout_runtime/vendor/`):

- bytes taken from the **npm tarball**, not a CDN — identical content, but only the tarball
  carries a registry integrity hash to check *before* unpacking;
- the file kept **byte-identical** to the published release (including its trailing
  `sourceMappingURL` comment — editing it would forfeit the ability to re-check the digest
  against upstream forever);
- both the bundle and its licence **pinned by sha256 in a test**;
- both marked `-text` in `.gitattributes`, or the Windows CI leg CRLF-converts them and the
  digest goes red on that leg only;
- **not** under a path matched by `.gitignore` — `dist/` is ignored here, so a bundle
  vendored there would silently never ship.

## Rule 4 — an asset's licence must reach the artifact

A licence recorded and never displayed is not compliance. CC BY needs attribution *and*, for
a modified work, an indication that changes were made — and `an` genuinely modifies what it
ingests: it wraps an avatar into a rig.

Two live consequences:

- The DiceBear **software** licence (MIT) is a separate fact from each **style** licence.
  DiceBear itself splits them under `# Design` and `# Code` headings inside every per-style
  licence file. Of the 27 styles `an` can request, 11 are CC0, 12 are CC BY 4.0, and the
  Pablo Stanley set carries bespoke "free for personal and commercial use" terms that are
  *not* Creative Commons. See `misc/docs/wave1_verification.md` for the table.
- Default to a CC0 asset where one exists that fits. A default carrying an attribution duty
  makes every user of the default liable for discharging it, whether or not they know.

## Rule 5 — enforce in code, not in prose

Each of the traps above is the *first search result* for a problem `an` is about to have. A
sentence in a doc does not survive an agent reaching for the obvious package at 2am. What
survives:

- a test that asserts the pinned model or session name;
- a test that asserts a vendored file's digest;
- a test that asserts a licence notice is present in the installed package;
- a manifest of every non-`an`-authored byte, with a test that fails when a file appears
  without a row.

## When you cannot verify

Say so, in the artifact. `UNVERIFIABLE` is a real verdict and it behaves like
`DISQUALIFIED` until someone does the reading. Do not launder a second-hand claim into a
first-hand one by restating it without its source — that is how "X is MIT" becomes true by
repetition.

## Where the answers already are

`misc/docs/wave1_verification.md` holds the verified DiceBear table and the vendored-engine
provenance. The epic (#9) carries the federation-wide excluded/substitute table. Both cite
URLs; extend them rather than re-deriving.
