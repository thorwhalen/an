# Working in the an repo

This file orients an AI agent doing engineering work *on* an itself. If you're using an from a downstream project, see `.claude/skills/an/SKILL.md` instead.

## Where things live

- **Source:** `an/` (the package).
- **Reference research:** `misc/docs/` — seven deep reports on the design space. Read the matching one before designing or extending a subsystem.
- **AI changelog:** `misc/CHANGELOG.md` — append a one-line entry under today's date when you finish a non-trivial chunk.
- **Project skills:** `.claude/skills/` — `an` (top-level orchestrator), `an-spec` (interview), `an-dev` (dev-side; this is what you want when working on the repo).
- **Tests:** `tests/`. Doctests in module docstrings cover public API; pytest covers cross-cutting checks.
- **Example:** `examples/park_bench_cartoon/` — the v0.1 demo target. Skeleton in Phase 1, fully wired in Phase 4.

## Architectural pillars (locked in)

1. **Three-layer IR.** `scene.md` (Narrative, human-edited) ↔ `ir/scene.json` (Scene Graph, agent-edited, the SSOT) → render code (per-backend, disposable). Information flows downward; verification feedback flows upward.
2. **Schema evolution from day one.** Versioning envelope, `extra="allow"` on inbound, additive-only changes, `an.ir.migrate` registry. Round-trip stability is tested.
3. **Composition combinators flatten to canonical timeline.** Authoring is fluent (`sequence`, `parallel`, `tween`); the canonical form is the flat list of `FlatAction`s with absolute times. The flat form is what verifiers and renderers operate on.
4. **Path-based property targeting.** `"charlie/torso/left_arm:rotation"` so animation generalizes across renderers.
5. **Time in seconds (float)** at the IR boundary; rational time only where audio drift matters.
6. **Everything external behind a `Protocol`.** `Renderer`, `TTSProvider`, `LipSyncProvider`, `Verifier`. One default per protocol at v0.1; trivially swappable.
7. **Persistence via dol-backed `MutableMapping`s** organized into the project mall (`an.build_project_mall`).
8. **Dispatch to interface.** Plain Python functions are the business logic; the CLI is a thin argh dispatcher over `tools._dispatch_funcs`.
9. **Verification is a swappable `Verifier`.** Same interface for human, lint, vision-LM, MoVer.
10. **Typed error routing.** Findings carry `(severity, ir_path, description)` so the orchestrator routes fixes to the lowest IR layer that can make them.

## Code conventions

- `an.__all__` is **curated**. Internals are underscore-prefixed.
- Keyword-only arguments past the 2nd or 3rd position; no magic numbers; defaults at module top.
- No globals, no service locators — pass the mall in.
- Functional over OOP; OOP only for orchestrators and stateful sessions.
- Errors are informative and wrap subprocess failures at the facade boundary.
- Doctests for public API functions; pytest for cross-cutting and integration checks.
- Local packages have **no declared dependency versions** (e.g. `"dol"` not `"dol>=0.3"`).

## Phase status

| Phase | What ships | Status |
|-------|------------|--------|
| 1     | Substrate: Scene IR, composition, stores, CLI, project init, dev skills | shipped |
| 2     | Cutout adapter (the v0.1 demo target's renderer) | not started |
| 3     | Audio pipeline (ElevenLabs TTS + Rhubarb lip-sync) | not started |
| 4     | Cutout dialogue + lip-sync integration (the v0.1 demo) | not started |
| 5     | Verifier impls + orchestrator + edit loop | not started |
| 6     | Manim + Remotion + whiteboard adapter smoke tests | not started |
| 7     | Polish: asset promotion, IR-slice cache, README, full demo | not started |

## What never to do

- Never edit `ir/scene.json` by hand for an example; edit `scene.md` and let `sync` regenerate.
- Never invent a render in Phase 1 — the `render_project` stub raises `NotImplementedError` deliberately.
- Never use `pip install <name>` for a local-ecosystem package; this is `pip install -e <path> --no-deps`.
- Never bump `SCHEMA_VERSION` without registering a migration in `an/ir/migrate.py`.
