---
name: an-dev
description: Use when working *inside* the an repo itself — writing or modifying an's own Python code, tests, schemas, adapters, or skills. Triggers when CWD is the an repo and the task is implementation work (adding a renderer, fixing a schema bug, updating a store). Not for using an from a downstream project.
---

# an-dev — working inside the an repo

This skill orients you for engineering work *on* an. If you're using an from a project, see the `an` skill instead.

## Read these before designing

- **The master spec** is the prompt that bootstrapped this project; if you don't have it in context, the user will paste it.
- **The seven research docs** in `misc/docs/`. Each subsystem has a primary doc — read it before designing:
  - `report 0 ...md` — overall architecture, IR layering, agent-tool boundary, verification (start here)
  - `dsl_design_patterns_report.md` — IR schema design, evolution rules
  - `report 2 ...md` — interchange formats, channel/keyframe representation
  - `report 5 ...md` — cutout scene-graph + director architecture
  - `report 1 ...md` — JS-side cutout ecosystem (PixiJS + GSAP recommended)
  - `report 3 ...md` — viseme standards, lip-sync pipeline
  - `Annotation systems ...md` — interval data structures, rational time, A/V sync
- **The plan file** for the current phase (under `~/.claude/plans/` or surfaced by the user).

## Architectural pillars (locked in — do not re-litigate)

1. Three-layer IR (Narrative `scene.md` / Scene Graph `ir/scene.json` / Render Code generated). Render Code is disposable.
2. Top-level versioning envelope on the IR; `extra="allow"` on inbound; additive-only field changes; migration registry chained through `an.ir.migrate`.
3. Composition primitives are Python-side combinators that flatten to a canonical timeline. The flat form is what verifiers and renderers operate on.
4. Path-based property targeting (`"charlie/torso/left_arm:rotation"`) for renderer-portability.
5. Setup pose plus deltas; slot/skin/animation separation. (Cutout adapter, Phase 2.)
6. Time in seconds (float) at the IR boundary; rational time only inside the audio pipeline where drift matters.
7. All external systems behind `Protocol`s (`Renderer`, `TTSProvider`, `LipSyncProvider`, `Verifier`).
8. All persistence via dol-backed `MutableMapping`s organized into the project mall.
9. Dispatch to interface: business logic is plain Python; CLI (argh) is dispatch only.
10. Verification is a swappable Protocol; same interface for human, lint, vision-LM, MoVer.

## Code conventions

- Public API in `an.__all__` is **curated**. Internals get an underscore prefix.
- Keyword-only arguments past the 2nd or 3rd positional. No magic numbers — defaults at the top of the module they belong to.
- No globals, no service locators. Pass the mall in.
- Functional over OOP; OOP only for orchestrators and stateful sessions.
- Errors are informative and specific. Wrap subprocess errors at the facade boundary.
- Doctests for the public API; pytest for cross-cutting checks.

## Per-PR housekeeping

- Append a one-line entry under today's date in `misc/CHANGELOG.md`.
- If a non-trivial design decision was made without explicit user blessing, log it (in the PR description for repo-level work; in `.an/decisions.jsonl` for project-level work).
- Update the matching skill in `.claude/skills/` if the user-facing surface changed.

## When unsure about prior art

The user maintains a large local Python ecosystem (`~/Dropbox/py/proj/`). Before reinventing storage / dispatch / etc., check:

- `dol` — for any `MutableMapping`-shaped persistence.
- `argh` — for CLI dispatch (see `~/.claude/skills/python-dispatching/SKILL.md`).
- The `python-storage`, `python-iterables`, `python-project-structure`, `python-dispatching` skills under `~/.claude/skills/`.
