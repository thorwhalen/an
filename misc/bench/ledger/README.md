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
an bench --bless "<reason>"    # re-write the golden frames, recording why
an bench --mutation high_crf --out /tmp/mutated.json --compare <baseline>
an bench-compare               # the two newest committed rows, BY `generated_at`
an bench-compare --before A.json --after B.json
an bench-compare --mutation high_crf --strict   # for CI: nonzero when unmet
```

**The default pair is ordered by `generated_at`, not by filename** (an#54). Rows
are named `<date>-<sha7>.json`, so a filename sort orders same-day rows by *sha
hex* — and a re-baseline plus its after-run on one day is the normal shape of a
wave. A row whose stamp cannot be read sorts first, so it is dropped rather than
becoming the `after` a verdict is drawn from; it never raises, because one bad
file must not break the listing.

**A `--mutation` run is never filed here.** It renders a pipeline broken on
purpose, so a `<date>-<sha>.json` name would claim it as the commit's evidence.
`--out` keeps one anywhere *except* this directory, which the CLI refuses.

### The three-row protocol for a wave that re-blesses

A `--bless` run WRITES the goldens it would otherwise have compared against, so
its family B is gated `blessed_this_run` — and `format_comparison` skips
`unchanged` entries, so family B does not appear as "unchanged", it does not
appear at all. Three rows, in this order:

| row | how | what it is for |
|---|---|---|
| **before** | `an bench` on the base commit | the baseline |
| **after-unblessed** | `an bench` on the change, **no `--bless`** | the PR's evidence — the only row that can fail family B |
| **after-blessed** | `an bench --bless "<why>"` | the new baseline; lands as `-dirty`, because a bless writes into the tree it names |

`an bench-compare` surfaces a blessed row as a caveat rather than leaving the
`blessed` key write-only, so a reader is told which of the three they are
holding.

`misc/docs/wave2_research.md` §1 is the authority for what each metric is,
except `edge_masked_distinct_colours`, which is an#55 and whose own
docstring carries its measured limits;
`misc/docs/wave2_crossarch_verdict.md` is the authority for the comparison
rules above.

## Comparing two rows

`an bench-compare` reads two rows and **refuses when they are not comparable**.
Refusing is the feature: two rows measured on different scenes, at different
resolutions, or on different x264 builds are not "one better and one worse" —
every number in them is uninterpretable relative to the other.

Two questions, and they are different:

- **without `--mutation`** — is the second row worse? Only the one-sided metrics
  can answer that. `edge_transition_width`'s optimum is *interior* (under 1 is a
  staircase, 3+ is soft), so it reports `changed` and never `regression`; no row
  carries a target value, and manufacturing one from the baseline is how a
  comparison starts asserting more than it knows.
- **with `--mutation`** — did the declared witnesses move in the declared
  direction, and did **three distinct causal families** do so? That is an#41's
  criterion, and the per-metric per-mutation signs come from the row itself.

Five verdicts per metric under a mutation, and the last three are the useful
ones: `as_declared`, `contrary` (moved the other way), `did_not_move` (the lever
never reached it — a different diagnosis, calling for a different fix), `gated`
(the reference moves with the mutation, so the delta is uninterpretable), and
`unexpected_movement` (a metric declared orthogonal that moved — news, not a
pass).

**There is no tolerance band, and none is needed.** Two consecutive `an bench`
runs on one machine produce bit-identical numbers for every metric on all six
scenes. Zero is the normal delta; anything else is real. The report prints the
relative delta beside each movement so a 0.0% wobble on a scene the mutation does
not reach is visibly not a 30% move on one it does.

**A key absent from one row is unknown, not different.** The ledger grows
additively, so refusing on an absent field would make every future addition
retroactively destroy comparability with every row already written. Absences are
recorded as caveats and printed; `schema_version` is what guards a genuinely
unreadable row.
