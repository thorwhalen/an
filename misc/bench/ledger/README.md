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

## What an encode-side metric is measured against

Every encode-side row carries a `reference`:

| `reference` | what it is |
|---|---|
| `lossless` | the decode of a `-qp 0` encode of the same frames — **the plane libx264 received**, on any build. Every *counting* encode-side metric uses this |
| `source_png` | an explicit RGB→YUV conversion of the pre-encode PNGs. Two metrics need it: the chroma one, whose subject *is* the 4:2:0 subsampling that happens during that conversion; and `encode_ringing_excess`, which cancels a term that exists only when both its legs share it |
| `none` | a property of the encoded file, not a comparison |

This distinction is not decoration. The PNG conversion is **build-dependent**:
it reproduces the encoder's input exactly on ffmpeg 8.1 and misses by mean 0.63
/ max 5 on the Linux CI runner's older build — 42% of `coded_luma_edge_error`'s
whole crf23 value. The first design asserted that agreement as a hard equality
and would have measured a colour conversion as encoder damage on that machine.

The distance is now recorded per scene as `png_to_encoder_input_luma`, and
`references_coincide` says whether it is zero. When it is,
`coded_luma_edge_error` and `chroma_edge_dY` read **identically** — they are the
same expression on different references — and that is why both are in the row.

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
