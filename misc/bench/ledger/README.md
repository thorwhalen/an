# The metrics ledger

One JSON row per `an bench` run: `<date>-<sha>[-dirty].json`.

**Rows are append-only.** An existing row is evidence about a commit on a
machine. Editing one rewrites history that `an bench --compare` (an#40) reads
as fact — and unlike a code change, nothing goes red when it is wrong.

The `-dirty` suffix means the row was measured against uncommitted edits, so it
describes no commit. Those rows are for looking at, never for comparing.

## What a row carries, and why two rows may or may not be compared

Three blocks that must never be mixed:

- **`metrics`** — numbers, each labelled `render` or `encode`. The two families
  are blind to each other's mutations by construction, and their comparison
  rules differ.
- **`tripwires`** — change detectors. They fire on improvements and regressions
  alike, so they count **zero** toward any criterion.
- **`provenance`** — never gated, never counted. Everything that decides
  whether two rows may be compared at all.

**Render-side rows compare across any machine.** Measured: both render paths,
four machines (local arm64 macOS, `macos-latest`, `ubuntu-latest` x86-64,
`ubuntu-24.04-arm`), 132 frames each — zero differing pixels *and* zero
differing PNG bytes, across two different SwiftShader JIT backends.

**Encode-side rows are machine-scoped, and must be refused rather than banded.**
Same ISA + same x264 build is byte-identical; a different ISA moves the decoded
stream a little; a different x264 build moves it by two orders of magnitude
(up to 99.2% of samples). A band that wide would swallow
`flat_field_deviation`'s entire crf18→23 signal. The fields that decide it are
`provenance.environment.encode_side.x264_sei` (verbatim) and `.isa`.

A row also refuses comparison when `scenes.<name>.provenance.
scene_contract_sha256` differs: two rows measured on different scenes are not
"one better and one worse", they are mutually uninterpretable.

## Where a row's bulk lives

A row carries `metric_declarations` **once** — the full definition of every
metric and tripwire, including the prose. Per-scene rows inline only the value
and the fields `--compare` keys on (`side`, `family`, `comparison_scope`,
`under_mutation.*.{expect,counts,gate}`).

The declarations are carried *in the row* rather than referenced, because
`--compare` reads rows written by older registries: a row from six months ago
has to be interpretable without checking out the commit that wrote it. They are
carried once rather than per scene because repeating them buries the numbers in
the prose explaining what the numbers are.

Two scenes cost ~32 KB compact. Revisit the format if a row exceeds ~250 KB, or
if the directory passes ~20 MB — the same trigger the golden corpus uses.

## Four value states, and two of them are null

| state | meaning |
|---|---|
| `measured` | a number |
| `gated` | the comparison is impossible — the reference moved, or the source hash differs. **Uninterpretable, not good or bad.** |
| `unavailable` | the check could not run. A check that crashed is not evidence anything is fine. |
| — | `no_change` is a *prediction*, never a value state, and can never count: "no change by construction" is a tautology |

## Regenerating

```bash
an bench                       # the whole corpus
an bench --scenes single_character
an bench --no-ringing          # skips one extra lossless encode per scene
```

`misc/docs/wave2_research.md` §1 is the authority for what each metric is;
`misc/docs/wave2_crossarch_verdict.md` is the authority for the comparison
rules above.
