# ADR — what CI verifies, and what it deliberately does not

**Status:** accepted, 2026-08-21 · **Supersedes:** the open question in an#22 ·
**Related:** an#21, an#16, i2mint/wads#66, epic an#9 (Wave 2)

The question an#22 actually asked was not "should browser tests run in CI". It was:

> **Which failures is this repo allowed to not notice?**

This ADR is the answer, and it exists because the previous answer was never written
down — so nobody could tell a deliberate blind spot from an accidental one. That
distinction turned out to be the whole issue: two of the three blind spots were
accidental, and one of them had been silently deleting tests for months.

---

## Context — what was actually true before

| Believed | Measured |
|---|---|
| Browser tests are *skipped* in CI | They were never **collected**. Eleven modules opened with a module-level `pytest.importorskip("playwright.sync_api")`, which aborts the module import. **472 tests collected with Playwright installed, 438 without.** |
| The casualties are the browser tests | **13 of the 34 needed no browser at all** — every `an.verify.media` SSIM test, two `skip_render=True` orchestrator tests, six JSON-parser tests, and a **real billed Anthropic call** gated on nothing but "is a key set". |
| Someone would have noticed | Nobody could. **A test that is not collected appears in neither the pass count nor the skip count** — it is absent from both halves of the summary a reviewer reads. |
| `continue-on-error: true` makes GitHub report the Windows job as success | It does not. On every failing run the job conclusion, the step conclusion and the check-run row all read `failure`. It changes the **roll-up**: the *run* concludes `success`. The signal was **non-blocking, not hidden** — which is worse, because a reviewer reads the aggregate. |

The last row is the one worth internalising. Two Windows-only defects reached `main`
past a green tick (an#21's path separators; an unpinned `read_text()` locale codec),
and in both cases the red job was sitting in the checks list the whole time.

---

## Decision

### 1. The rendering lane is real, and it is opt-in per PR

`.github/workflows/browser-tests.yml` runs the `browser`-marked tests on Linux with
Chromium and ffmpeg installed. It triggers on:

- **`workflow_dispatch`** — always; and
- **`pull_request`**, but only when the PR carries the **`run-browser-tests`** label.

An unlabelled PR never starts the job, so the default cost is zero.

**Adding the label is open to agents as well as humans:**

```bash
gh api -X POST repos/thorwhalen/an/issues/<N>/labels -f 'labels[]=run-browser-tests'
```

**Not `gh pr edit --add-label`** — on this owner's repos that call goes through the
GraphQL path and dies on the projects-classic deprecation, **printing an error but
exiting 0 and applying no label**. Verified on PR #30. Always read the labels back:
`gh pr view <N> --json labels -q '.labels[].name'`.

**Add it when a PR can change a pixel** — the runtime (`an/data/cutout_runtime/`),
the cutout compiler or serializer, the render path, the vendored engine, the ffmpeg
flags, or the character rig. Nothing else in CI can see any of that.

Measured on the first Linux dispatch, cold cache:

| Step | Time |
|---|---|
| Install Chromium (`--with-deps`) | 24 s |
| Install ffmpeg | 10 s |
| Non-rendering tests (baseline) | 11 s |
| **Rendering lane** | **45 s** — 23 passed, 1 skipped |
| **Whole job** | **103 s** |

It passed on Linux on its first attempt, and 45 s is within a second of the macOS
number. **The tests are cheap; the setup is the cost**, and Chromium caches.

### 2. It is *not* on every push

~34 s of setup plus 45 s of tests against a ~50 s CI leg would roughly double PR CI
for a repo whose renderer changes in bursts. Promotion is one line when wanted:
`schedule: - cron: "0 4 * * *"` for nightly, or `pull_request: {branches: [prod]}`
for a dev→prod gate. **The gate needs no change for any of them** — it keys on
`AN_BROWSER_TESTS`.

### 3. Windows is blocking, and it gates the release

`continue-on-error: true` is removed from `windows-validation`, and `publish` has a
`needs` edge on it. Without the second half, blocking Windows reddens the tick while
a Windows-only failure on `main` still uploads to PyPI and pushes the tag — and on
this repo a merge to `main` *is* the release.

The edge is guarded by `!failure() && !cancelled()` rather than being a bare `needs`,
because **in GitHub Actions a skipped `needs` job skips its dependents** — the bare
form would silently stop every release the day someone sets `test_on_windows = false`.

Both are deliberate deviations from the generated wads template, commented in place.
Upstream knob: **i2mint/wads#66**.

### 4. The standing honesty rule

> **No doc, PR body, changelog or commit message may say a rendering behaviour is
> "verified in CI."**

It is verified on a developer machine, or on a labelled PR, or on an on-demand run —
**never on an unlabelled PR.** Say which. Wave 2's metrics ledger inherits this
caveat, and epic an#9's Option 3 named it as the price of this decision; it is now
written down rather than assumed.

### 5. The licence perimeter admits named permissive variants

Read literally, "MIT / BSD / Apache-2.0 / ISC only" disqualifies permissive licences
nobody meant to exclude. The perimeter is those four **plus explicit dated rulings**
that name what was read. First ruling: **MIT-CMU is inside** (Pillow 11.3.0, read at
`pillow-11.3.0.dist-info/licenses/LICENSE`) — MIT's grant plus BSD-3's no-endorsement
clause, no copyleft and no field-of-use limit. The ledger of rulings and the procedure
for adding one live in `.claude/skills/an-dev-licensing/SKILL.md` (Rule 6).

This admits permissive *variants* only. It is not a doorway for weak copyleft,
source-available terms, or anything whose obligations vary by how the artifact is
distributed.

---

## What holds this in place

`tests/test_browser_gate.py` — **28 guards, mutation-tested 20/20.**

Three properties, each of which failed at least once before it worked:

1. **Collection is invariant.** Which tests *exist* must not depend on what is
   installed. The load-bearing guard is
   `test_collection_does_not_depend_on_the_environment`: it shadows every optional
   import, strips the external binaries from `PATH`, and compares pytest's own node-id
   sets — a reference *outside* the guard, so it catches routes nobody enumerated. The
   AST scanner beside it is a list of known spellings and is the weaker half.
2. **A gated run says so out loud** — `browser tests: 24 collected, 0 ran, 24 did not: …`.
   That count is an **observation** (`pytest_runtest_logreport`), not `total - skipped`,
   which is a collection-time prediction and printed "24 ran" for `-m`, `-k` and
   `--collect-only` runs in which nothing ran.
3. **An explicit opt-in that cannot be honoured is an ERROR, not a skip** — scoped to a
   lane the invocation actually selected. A CI job whose `playwright install` quietly
   failed must go red, not green with 24 skips.

**This does not make the original bug impossible, and no document here may claim it
does.** An adversarial review reintroduced it four ways past an earlier, scanner-only
draft: an ffmpeg-keyed module skip, a `collect_ignore`, a class-body probe, and markers
swapped for hand-rolled skipifs. All four are now caught; the next one might not be,
which is why property 1 is written as an invariant rather than a blocklist.

---

## Consequences

- Rendering claims stay laptop- or lane-verified. That is now a stated cost, not a
  surprise.
- A PR that changes the renderer without the label gets no pixel verification.
  **Reviewers and agents should ask for the label rather than assume CI covered it.**
- Windows failures now stop releases. If that becomes a drag, the fix is to make
  Windows green, not to restore the flag.
- CI runs 460 tests where it ran 423.
