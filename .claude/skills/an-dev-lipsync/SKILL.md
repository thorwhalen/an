---
name: an-dev-lipsync
description: Lip sync in the `an` repo — where co-articulation sits (in the compiler, over the provider's raw track), the pass order (symbolic → dominance → envelope → minimum hold), the condenser that HOLDS and votes instead of dropping, the Rhubarb recognizer rule, word-timing retention and its cache rule, the alignment-model licence trap, and the two standing measurements. Load before touching `an/audio/*lipsync*`, `an/audio/pipeline.py`, `_add_viseme_clips`, `_MIN_VISEME_GAP_S`, `an/adapters/cutout/coarticulate.py`, any viseme test, or a dialogue corpus scene. Triggers on "lip sync", "viseme", "Rhubarb", "whisper", "word timings", "co-articulation", "condenser", "aligner".
---

# an-dev-lipsync — mouths that read

Design of record: `misc/docs/wave6_research.md` §11–§12, §14. Where this skill and the code
disagree, the code wins and both get fixed.

## 1. Where the passes live, and why

Providers (`offline`, `whisper`, `rhubarb`, `WordTimingsLipSync`) return a **raw**
`VisemeTrack`; the audio pipeline caches it keyed on `{audio_key, lipsync.name, transcript}`.
Every quality pass runs **in the compiler**, in front of channel emission — never in the
pipeline — so a knob change is a recompile and never a re-alignment, and no pass constant may
ever enter the cache key. `Viseme.intensity` (exists, set by no provider, round-trips through
the sidecar as `1.0`) is the dominance carrier; cached tracks keep `1.0` until re-aligned.

## 2. The pass order (each a pure function over `list[Viseme]`, doctested)

1. **Symbolic** — merge adjacent duplicates; drop a low-dominance key whose raw span is under
   one frame (JALI §4.2: tongue-only visemes have no influence on the lips; Rhubarb encodes
   the same as `Phone::N → {B, C, F, H}`, a set its optimizer resolves to the neighbours). In
   Rhubarb's letters the tongue class is B and H — but B also codes EE, so only providers that
   know the character stamp consonant-origin B low.
2. **Dominance** — Cohen–Massaro's per-segment dominance, degraded for a swap mouth to an
   **argmax over the window**: `weight = raw_span × α`. The *order* of `DOMINANCE` is sourced
   (A > F, G > E, D > C > X > B, H); the values are art direction.
3. **Envelope** — timing offsets: an anticipation lead of `2/24 s` (JALI: onset ~120 ms before
   the apex; Rhubarb's `maxExtensionDuration = 6_cs`), clamped at 0; the apex *is* the swap; a
   word-final open vowel before a gap decays to `X` ~120 ms after its last key.
4. **Minimum hold — the condenser, last.** It **holds and votes**; it never drops. Windows of
   at least ``min_hold_s`` open at each cue that clears the previous window; every cue in the
   window — the opener included — votes with the length of its span **inside the window**
   times its dominance (Rhubarb's own "highest total duration within the candidate range"),
   and the winner shows **at the window start**. A member whose span runs past the window
   and lost is not dropped: it opens the next window at the window's end, delayed — the hold
   doing its job. `[(0,X),(.30,B),(.34,A),(.38,D),(.80,X)]` → `[(0,X),(.30,D),(.80,X)]`;
   the old `continue` loop gave `[X, B, X]`. Keep `min_hold_s = 0.14` until measured
   (Rhubarb's floor is 0.08 s; one frame is the floor below which nothing registers). The
   terminal rest key stays an invariant — and since an#97 the clip **window** is frame-ceiled
   so that rest is actually sampled (a 0.71 s line's rest at 0.71 s fell between frames 17
   and 18 and the mouth stayed open after the line). Shipped as
   `an/adapters/cutout/coarticulate.py`; `compile.COARTICULATION_ENABLED = False` reproduces
   the old condenser for a side-by-side and nothing else.

**What the keyframe count honestly does.** Against the RAW provider track the passes thin
the mouth to the hold's ceiling (~7/s at 0.14 s). Against the OLD condenser the count goes
**up** on a dense track (`dialogue`: raw ~17/s → old 5.8/s → passes 7.3/s), because the old
loop had fewer keyframes only by dropping the closures and open vowels a viewer reads; the
vote keeps one shape per window and never loses one. "Fewer keyframes" is a claim about the
raw track, never about the old condenser.

Order matters: (1) before (4) so a one-frame /t/ never wins a window; (3) before (4) so the
hold is measured on shifted times.

## 3. Rhubarb: the recognizer follows the language

`pocketSphinx` is Rhubarb's default and the one for English; `phonetic` is for non-English
audio and **discards the dialog file** (`PhoneticRecognizer.cpp`: `UNUSED(dialog)`; PocketSphinx
mixes `{defaultLM, dialogLM}` at `{0.1, 0.9}`). `RhubarbLipSync(*, language="en",
recognizer=None)`: `None` resolves per language — `en` → `pocketSphinx` **with** the dialog
file, anything else → `phonetic` and no transcript is written (a file nothing reads is a lie).
The provider's `name` carries the recognizer (`rhubarb:pocketSphinx`) so the cache key changes
and no stale `phonetic` track replays. The argv is pinned by a test that **stubs
`subprocess.run`** — the binary-gated tests skip everywhere the binary is absent, which is a
guard nobody runs. Rhubarb is MIT and its Sphinx models BSD-variant; `an` shells out and
ships none of it.

## 4. Word timings: retained, additively, in the existing sidecar

`VisemeTrack.words: list[WordTiming] | None` (whisper and `WordTimingsLipSync` fill it;
offline and Rhubarb leave `None`). Persist by adding `"words"` to the viseme sidecar payload —
the key does **not** change (words are a function of the same inputs). IR:
`Dialogue.word_timings` / `Narration.word_timings`, line-relative, optional with a default, so
no `SCHEMA_VERSION` bump; JSON-only (the md writer emits `speaker [emotion]: text`).
`already_done` gains `and line.word_timings is not None` **only** for providers that can
supply words — or every Rhubarb project re-synthesizes forever. muvid feeds timings through
`WordTimingsLipSync` and gets the round trip for free; Wave 8 reads `line.start + word.start`.

## 5. The parser must not drop a line

`_extract_dialogue_block` used to drop an unmatched line silently — `promote_demo` was mute
for months because its line read `maya (warm): …`. An unmatched non-empty line inside a
```` ```dialogue ```` block is a parse **error** naming the line and the accepted shape.

## 6. The alignment-model trap (licence gate, never a fallback)

Any provider that selects weights by language carries an explicit allowlist
`{"en": (model, "MIT", url-read)}`; an unlisted language raises `LipSyncError` naming the
language and the gate — never a warning, never the vendor's default. The evidence: WhisperX's
`DEFAULT_ALIGN_MODELS_TORCH` maps fr/de/es/it to VoxPopuli bundles torchaudio documents as
CC BY-NC 4.0, `vi` to a `cc-by-nc-4.0` model, and `he/hi/hr/gl` to models with **no licence
declared**; its only refusal is for an absent entry. faster-whisper's word times come from
DTW inside the one MIT model, which is why `WhisperLipSync` is safe — its test is that
`language` is not forwarded. Same shape as `CassetteMiss`: the safe outcome must be
impossible to swallow.

## 7. What the bench can and cannot see

Before an#96 only one corpus scene spoke and its golden sampled *outside* the line, so a
condenser change moved zero committed pixels. `misc/bench/corpus/dialogue/` (an#96) has a
golden **inside** the line — frame 14, on the `h`/`a` of "shape", where today's condenser
shows `C` having dropped the `D` and `A` inside its window; the vote changes that frame, and
it is re-blessed "condenser holds" when it does. `promote_demo` renders mute in the bench by
design (no visemes stamped in its IR). The
two standing numbers: viseme keyframes per second of dialogue (from compiled `__viseme__`
clips, trailing rest excluded) must fall; the legibility score from the cassetted judge
(a dense 8-frame strip inside the line, "could you read this mouth is saying `<text>`", 1–5)
must not. Recording spends once, under `AN_LIVE_API_TESTS=1`; replay is the default and a
miss is a `CassetteMiss`. The judge stays out of the ledger.
