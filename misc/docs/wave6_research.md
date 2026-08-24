# Wave 6 research — faces

Measured 2026-08-24, before any Wave 6 code, at `main` ee268d2 (an 0.1.53). Five parallel
research threads — the repo as built, the vocabulary and its licence, expression IR and
composition, lip sync, gaze/baked faces/measurement — followed by an adversarial pass
instructed to refute the synthesis (§15 records what it changed). Where this document and
the code disagree, the code wins and this document gets fixed.

Authority order for Wave 6 work: this file, then the epic #9 Wave 6 brief (the epic's first
comment). Where they disagree, this file is right — it was measured, the brief was written
before Waves 4 and 5 landed, and §2 lists thirteen places it is stale. Same convention as
`wave5_research.md`.

**The wave in one paragraph.** Replace the eight-entry emotion→eyebrow-rotation table with a
small axis vocabulary a *silent* character can carry, authored as a new leaf action
(`expression`) that the dialogue `[emotion]` bracket desugars to; compose every generated
face contributor — emotion, gaze, blink, viseme — in one compile-time **face solver** that
emits exactly one channel per `(node, property)` (the runtime cannot add, and it should not
learn to); realise emotion on the mouth by **selecting** a `viseme@<preset>` set, never by
blending two drawings; add gaze as a pupil layer plus a seeded saccade generator; fix the two
confirmed lip-sync defects (Rhubarb's recognizer, the condenser that drops instead of holds)
and add the co-articulation passes in front of the condenser; retain word timings the
pipeline already computes; and measure all of it with one new corpus scene per concern, a
first committed vision cassette, and demo clips for every user-facing piece.

---

## 1. What is built today (facts, `file:line`)

**The emotion path.** `Dialogue.emotion: str | None` (`an/ir/schema.py:252`) — a bare string,
never validated. scene.md syntax `speaker [emotion]: text` (`an/ir/sync.py:182-184`,
lowercased at `:213-214`, written at `:396-397`). `_EMOTION_BROWS` (`compile.py:306-318`),
eight names → `(left_tilt, right_tilt)` radians. It is emitted *inside* `_add_viseme_clips`
(`:2184-2241`), per line, **after** that line's viseme clips and therefore only when the
line cleared every earlier `continue`: a viseme track (`:2083-2084`), a start and duration
(`:2085-2087`), an overlay face (`:2089-2090`), a viseme-capable mouth (`:2091-2111`). **A
`[angry]` line with no lip-sync gets no brows.** Target is the literal
`f"{speaker}/head/{brow}"` (`:2197`), property `rotation`, two step keyframes — the tilt at
0 and an *absolute* `0.0` at `line.duration` (`:2213-2217`), a return to zero rather than to
the node's rest. Unknown emotion strings emit nothing (`:2193`). No test asserts a brow
channel, a tilt value or an `__emo__` placement (`rg` over `tests/`): the compile behaviour
is unpinned.

**Track order.** `compile_shot` (`:585-611`) runs `_compile_actions` → `_add_viseme_clips`
(visemes, then emotion, appended) → `_add_blink_clips` (prepended, `:2372`) →
`_add_camera_clips`. Both evaluators are list-order later-wins with no sorting
(`timeline.py:84-100`; `runtime.js:518-545`). On one entity track the order is therefore
**blinks < set-holds < authored tweens/plays < viseme < emotion**, so a compiled emotion
overrides an authored brow tween during a line — the inverse of the an#88 rule for blinks
("an authored eye channel overrides a blink", `:2280-2283`). Nothing anywhere adds:
`timeline.py:15-17` still says "Additive blending lands in 2B"; `play` sums descriptor
*deviations* against the built rest at compile time (`an/characters/play.py:93-99`).

**The face rig.** Descriptor defaults (`an/characters/schema.py`): `default_asset_sets()` is
exactly `viseme` and `eyelid` (`:96-101`); face slots `left_eye`/`right_eye` (bone `head`,
attachment `open`), `mouth` (`mouth_x`), `left_brow`/`right_brow` (`:568-594`); brows are
**single-attachment** slots (`:681-686`); eyes carry `open`/`closed` (`:691-698`); the mouth
nine shapes (`:701-708`). The factory's open-eye SVG is one file with the sclera ellipse and
a pupil circle **baked in** (`factory.py:256-266`); the closed eye is a bare stroke with no
fill (`:269-278`); the brow a single quadratic (`:358-370`). The procedural rig's head has
`hair`, two brow rects, two `kind="eye"` visuals, a `kind="mouth"` visual (`compile.py:938-1000`);
`makeEye` draws sclera and pupil into one `PIXI.Graphics` (`runtime.js:252-269`). **There is
no pupil node on either rig**, no brow variants, no emotion mouths.

**Lip sync.** Protocols in `an/audio/lipsync.py` (`Viseme(time, code, intensity=1.0)` `:26-32`
— `intensity` is set by no provider, round-trips through the sidecar as a stored `1.0`
(`pipeline.py:210,223`), and is dropped at `pipeline._to_ir_viseme_track` `:168-172`;
`WordTiming = tuple[str, float, float]` `:45`; `word_timings_to_visemes` `:83-139` spaces a
word's visemes evenly over `[start, end]`). Word timings are produced only by
`whisper_lipsync.py:118-121` and `injectable_lipsync.py:108` (and held by `StaticWordTimings`,
`:51-57`), consumed once and discarded;
Rhubarb emits mouth cues with no words; offline has no timing. Cache keys
(`pipeline.py:68-76`) omit provider *configuration* (recognizer, model size). The compiler's
condenser (`compile.py:2116-2123`) `continue`s on any key inside `_MIN_VISEME_GAP_S = 0.14`
(`:125`); the terminal rest key is appended unconditionally (`:2155-2163`).

**Rhubarb.** `_RECOGNIZER = "phonetic"` (`rhubarb_lipsync.py:23`); `align()` always writes
the transcript and passes both `-r phonetic` and `--dialogFile` (`:59-74`).

**The judge.** `VisionLMVerifier` (`an/verify/vision.py:264-376`) calls the injected `judge`
with a **hardcoded** `_PROMPT` (`:70-92`, an issues list; nothing about emotion) over ≤4
frames spread across the whole render. `judge_frames` accepts any prompt (`:239-242`) and
`judge_key` hashes frame bytes + the collapsed prompt (`:127-155`), so a new prompt is a new
recording. **No cassette is committed** (`tests/cassettes/vision`, the code's directory, does not exist;
`test_the_committed_cassette_replays_without_spending` is skipped on that precondition,
`tests/test_vision_cassettes.py:437-461`).

**Bench hooks.** `Fixture(path, prepare, expect_visual_kinds, golden_frames, golden_note)`
(`an/bench/corpus.py:101-119`); the golden comparison is today-vs-committed per scene; there
is **no scene-vs-scene or frame-vs-frame "distinguishable" check**. Only one corpus scene
speaks, and its golden samples *outside* the spoken interval (`single_character` f0024 at
1.0 s after a 0.71 s line — OfflineTTS `0.05 + 11 × 0.06`). `promote_demo` is **mute**: its
line is written `maya (warm): …`, which `_DIALOGUE_LINE_RE` does not match, and
`_extract_dialogue_block` drops an unmatched line **silently** (`sync.py:207-208`) — the
committed `ir/scene.json` has `dialogue: []`. So the bench is blind to the mouth, and a typo
in a speaker tag deletes a line rather than erroring (§11, §14 PR-A).

**Downstream.** The sole importer of `an` in the projects tree is `muvid`
(`t/muvid/muvid/renderers/animation.py:30` `from an.orchestrate import orchestrate`; `:72`
`from an.audio import StaticWordTimings, WordTimingsLipSync`; both optional), which never
emits `[emotion]`. The brief's "only the orchestrator and the audio module" holds, and every
audio-side change below is additive.

## 2. Brief vs reality (thirteen corrections, fix when touched)

1. "A character who is not speaking cannot have a face" — stale since an#87/an#7: a silent
   character can already author brow `rotation` tweens, `eyelid`/`viseme`/any declared swap
   `set`s, and `play` descriptor animations. What is missing is a **named** expression.
2. The done-when grep `rg _EMOTION_BROWS an/` is too narrow: the vocabulary also lives in
   `an/iterate.py:169-171` (the LLM prompt), `.claude/skills/an/SKILL.md:44,62`,
   `misc/docs/architecture_as_built.md:334-339`, `README.md:82,86,169` (the README's own
   example says `maya [amused]:`), `examples/character_gallery/cartoon/scene.md:47` and
   `examples/park_bench_cartoon/scene.md:47,83` (`[amused]`), `an/ir/sync.py:194-195`
   (docstring `[skeptical]`), `misc/demos/build_demos.py:182-184` (`GRID_EMOTIONS` and a
   `_EMOTION_BROWS` comment), `tests/test_dialogue_emotion.py:24,37`. **Every preset name in
   use by live content is kept** (§8), so none of these becomes a validate error.
3. Emotion is **not** "applied for the duration of a dialogue line": only when the line also
   got a viseme channel (§1). Undocumented, untested.
4. Baked faces are *partly* typed today: `play` refuses a suppressed face slot with words
   (`play.py:168-182`); the viseme+emotion path `continue`s silently (`compile.py:2089-2090`
   — measured: a `[angry]` viseme-tracked line on a `face_overlay: false` descriptor yields
   zero warnings and no `__emo__` clip) and blinks skip silently (`:2289-2290`). The "brow art
   missing?" warning (`:2198-2206`) fires only for an overlay rig whose brow node failed to
   resolve; a baked face gets silence.
5. "The timings are already computed and discarded" is true for two of four providers
   (whisper, `WordTimingsLipSync`); Rhubarb has no words and offline no timing. "Round-trip
   from the provider into the IR and back" is definable for those two.
6. Fixing the Rhubarb recognizer replays stale `phonetic` visemes for any already-rendered
   project until `mall["visemes"]` is cleared — provider configuration is not in the cache key.
7. "Wave 2 cassetted a judge": the mechanism landed; **zero cassettes are committed**, the
   prompt is hardcoded, and the judge done-when has no recording, no baseline, no prompt.
8. `mouth[emotion_viseme]` with a fallback: sets are flat `{KEY: attachment}`, the runtime
   throws on an unknown key (`runtime.js:420-436`) — the fallback must resolve at compile
   time, and there is no two-level key.
9. "Additive offsets … compose without overriding": no evaluator adds. Additive composition
   is a compiler-side sum (§6) or two different properties; the done-when must say which.
10. "Gaze (pupil offset …)": there is no pupil to offset on either rig — a rig, art and
    (procedural) runtime-visual change, not "two floats and a PRNG" (§9).
11. "An expression block scoped to an interval on an entity": no such IR node; the only
    per-entity hook is the unused `AssetRef.overrides` (`schema.py:109`).
12. The emotion emitter violates the an#88 precedence rule the brief inherits (§1) — the
    replacement is placed like blinks, ahead of everything authored.
13. Docs drift: `an-dev-rig-contract/SKILL.md:58-59` cites `compile.py:752/:765/:1098` (today
    `_baked_face_speakers` is at `:1994`, the baked-face `continue` at `:2089-2090`, and
    `viseme_map` no longer exists in the compiler — the per-slot projection is `:1246-1257` /
    `:1310-1325`); `timeline.py:12` says "start-order" (it is list order);
    `architecture_as_built.md:339` "silently fall through to neutral" (the mechanism is "no
    channel emitted").

## 3. The vocabulary and its licence (decided)

**Source.** The MediaPipe "Blendshape V2" model card —
https://storage.googleapis.com/mediapipe-assets/Model%20Card%20Blendshape%20V2.pdf (6 pp,
2,324,099 bytes, sha256 `c8e9cf60a39998f4b341740623917590e050d1c97004e2de4568d84e026445ae`,
dated 11/11/2022; Grishchenko, Yan, Zanfir, Bazavan). Page 1, right column, verbatim,
confirmed both by `pdftotext` and by rendering the page: **"LICENSED UNDER / Apache License,
Version 2.0"**. The Face Landmarker docs page links this card and states no model licence of
its own; the repo's Apache-2.0 covers runtime code, not weights — so the card is the only
first-hand licence statement for the model and its appendix. Extraction caveat: the text
layer drops the "ft" ligature (`browDownLe`, `cheekPu`); the list must be transcribed from
the rendered page 5, never from the text layer.

**The 52 names** (appendix "List of predicted blendshapes"; the two parentheticals are the
card's): browDownLeft, browDownRight, browInnerUp, browOuterUpLeft, browOuterUpRight,
cheekPuff (predicted by the FaceMesh model), cheekSquintLeft, cheekSquintRight, eyeBlinkLeft,
eyeBlinkRight, eyeLookDownLeft, eyeLookDownRight, eyeLookInLeft, eyeLookInRight,
eyeLookOutLeft, eyeLookOutRight, eyeLookUpLeft, eyeLookUpRight, eyeSquintLeft,
eyeSquintRight, eyeWideLeft, eyeWideRight, jawForward, jawLeft, jawOpen, jawRight,
mouthClose, mouthDimpleLeft, mouthDimpleRight, mouthFrownLeft, mouthFrownRight, mouthFunnel,
mouthLeft, mouthLowerDownLeft, mouthLowerDownRight, mouthPressLeft, mouthPressRight,
mouthPucker, mouthRight, mouthRollLower, mouthRollUpper, mouthShrugLower, mouthShrugUpper,
mouthSmileLeft, mouthSmileRight, mouthStretchLeft, mouthStretchRight, mouthUpperUpLeft,
mouthUpperUpRight, noseSneerLeft, noseSneerRight, tongueOut (predicted by the FaceMesh
model). Output contract: "52 facial blendshape coefficients as float values in [0, 1] range"
— unipolar, rest 0, left/right separate. We copy that **shape** (unipolar-or-signed with a
declared rest, left/right split only where the art is split), not the count.

**Are the names licensable?** Circular 33 (https://www.copyright.gov/circs/circ33.pdf):
"Words and short phrases, such as names, titles, and slogans, are uncopyrightable because
they contain an insufficient amount of authorship" — but may be trademarks. The **list as a
whole** is closer to declaring code, and *Google v. Oracle* (2021) did not settle that: the
Court assumed copyrightability "purely for argument's sake" and ruled on fair use (Copyright
Office summary, https://www.copyright.gov/fair-use/summaries/google-llc-oracle-am-inc-2021.pdf).
**Rule adopted:** the 52 names are an interoperability vocabulary reproduced only from the
Apache-2.0 card, cited with URL and sha256 wherever the list appears; never from Apple's
documentation; the constant is `BLENDSHAPE_V2_NAMES` and no identifier carries "ARKit"
(trademark status unverified); prose may say "compatible with the ARKit-style 52-coefficient
convention". They are an **import/export mapping** to our axes, not rig channels — most of
them have no cutout meaning.

**Ideas borrowed, with their licence status** (nothing read from the disqualified commercial
2.5D SDK; Live2D's SDK stays unopened):

| vocabulary | licence read | borrowed |
|---|---|---|
| Blendshape V2 (52 names) | Apache-2.0 (card p.1) | names, as a mapping |
| VRM 1.0 `expressions` / `lookAt` | the *spec* repo carries **no licence file** (UNVERIFIED as text); UniVRM and three-vrm are MIT (`LICENSE` read) | ideas: per-expression `overrideMouth/overrideBlink/overrideLookAt` (exactly our composition problem), `isBinary`, gaze as a system separate from head with per-direction `rangeMap` clamps; the 18 short preset ids ship verbatim in three-vrm's MIT enum, so `happy/sad/angry/surprised/relaxed` and `blink*/look*` align to them |
| MPEG-4 FAPs (Ostermann 1998, paper read; standard paywalled) | — | ideas: FAPU normalisation (values as fractions of the face's own feature distances), expression "excitation", viseme transition by weight |
| FACS AUs (Wikipedia, CC BY-SA) | manual proprietary | AU **numbers** as cross-reference comments only; no emotion table transcribed |
| Rhubarb A–H, X | MIT (`LICENSE.md` read) | names (in use); C and E as in-betweens |
| Rive blend states | runtime MIT; editor proprietary | idea: neutral baseline + per-axis additive numbers |
| Adobe Character Animator | docs unreachable this session (UNVERIFIED) | nothing |
| Inochi2D | BSD-2-Clause | nothing yet |

**What a cutout face can carry** (open sources): on cartoon faces "the mouth was demonstrated to be a feature that is sufficient and
necessary for the recognition of happiness, and the eyebrows were sufficient and necessary for
the recognition of sadness"; brows alone carried nearly the full face's perceived sadness
intensity (6.07 vs 6.44) (Zhang et al. 2021, *Frontiers in Psychology*, CC BY; only
happy/sad/neutral were tested). South Park's early seasons lacked a standard half-lid and drew it per shot (the
`half` eyelid key is the first asset a production adds), and some characters' brows appear
only when worried or angry (South Park wiki, "Animation Changes"). FACS prototypes by AU
(Wikipedia): happiness 6+12, sadness 1+4+15, surprise 1+2+5+26, fear 1+2+4+5+7+20+26, anger
4+5+7+23, disgust 9+15+17 — on a cutout 1/2/4 are brow height + angle, 5/7/43 eyelid keys,
12/15 mouth form, 26 the viseme `D`; disgust's AU9 (nose) has no part and will read weakly,
a limit of the medium. OverSimplified / Crash Course production practice: UNVERIFIED.

## 4. The axes for this rig (decided)

Fifteen were proposed; **ten ship in Wave 6** (eight numeric, one selection, one scalar), the
rest are named so nobody re-derives them.
Rest = neutral; every value is an **offset over the built rest** of the node it drives.

| axis | range / rest | drives | art |
|---|---|---|---|
| `brow_height_l`, `brow_height_r` (AU1+2) | [−1, 1] / 0 | continuous `head/<brow>:y`, scaled by the rig's eye height (FAPU-style) | none |
| `brow_angle_l`, `brow_angle_r` (AU1 vs 2 vs 4) | [−1, 1] / 0 | continuous `head/<brow>:rotation`; + inner end up (worry), − inner end down (furrow); **replaces `_EMOTION_BROWS`**. The two sides rotate in opposite screen directions for the same axis sign, so the binding's per-side `gain` carries the sign (`_EMOTION_BROWS` was mirrored the same way: happy = `(−0.15, +0.15)`); the factory's baked ±4° arch (`factory.py:359`) is part of the rest the offset sits on | none |
| `lid_open_l`, `lid_open_r` (AU5/7/43) | [−1, 0.5] / 0 | **swap key, quantised** on the `eyelid` set by one rule, stated in §6: `wide` above +0.25, `open`, `half` below −0.35, `closed` below −0.85 | `half` (and optionally `wide`) per eye; today's `open`/`closed` still work — a rig without `half` snaps straight to `closed` at the lower threshold and is told so by `an character validate` (advisory) |
| `gaze_x`, `gaze_y` (AU61–64) | [−1, 1] / 0 | continuous `head/<pupil>:x`/`:y`, clamped by the eye's declared travel (§9) | pupil layer per eye (§9) |
| `mouth_form` (AU12/15) | selection, not a number | which `viseme@<preset>` set the mouth's `viseme` key indexes (§7); silent shots show that set's rest key | one chart per form that a character declares |
| `intensity` | [0, 1] / 1 | scalar on every offset (MPEG-4 "excitation"); the blend in/out ramp is a curve on this axis | none |

Deferred, deliberately: `brow_squeeze` (AU4 medial; needs a `furrowed` brow key or an `:x`
convergence — cheap, but no preset in §8 needs it to read), `squint` (AU6/7), `head_yaw` /
`head_pitch` (turnarounds are Wave 7's stage work; `head_roll` already exists as an authored
`head:rotation` tween), `mouth_open` (the viseme set carries it — `X→A→B→C→D` is a monotone
opening, and a gasp is `D` held). Resolution per channel kind, stated once: transform axes
**sum, then clamp**; swap axes resolve by **priority** (a blink's `closed` beats an emotion's
`half`; the form picks the chart, the viseme picks the key) — never a blend of two drawings.
Gaze reads no head axis, by construction: that separation is the epic's acceptance test.

## 5. The IR: `expression` is a leaf action (decided)

Three shapes were weighed. Reusing `set`/`tween` on an `expression` property breaks an#87's
ruling that a non-transform property *is* a swap-set name, and a cross-fade would be a tween
on a string. A shot-level `expressions:` list is a second timeline that `flatten` never sees.
**A leaf action, flattened like `play`, wins** — `play` is the exact precedent for a leaf whose
resolution lives outside the compiler (`an/characters/play.py`, shared with validate).

```python
class ExpressionAction(_ActionBase):            # an/ir/schema.py, joins the Action union
    kind: Literal["expression"] = "expression"
    target: PathStr                             # the ENTITY; the binding picks the nodes
    preset: str | None = None                   # None + no axes = neutral (a cheap "return to rest")
    axes: dict[str, float] = {}                 # overrides layered on the preset, axis units
    intensity: float = 1.0
    duration: Seconds | None = None             # None = to the shot end (the looping-play rule)
    blend: Seconds = DFLT_EXPRESSION_BLEND_S    # ramp in/out on the intensity axis; 0 = cut
```

Cross-fade is not a special action: two expressions on one entity overlapping by `blend`
seconds ramp their offsets in opposite directions, and because composition is additive (§6)
the sum crosses over. **The dialogue bracket is sugar**: before compilation every
`Dialogue.emotion` becomes `ExpressionAction(target=speaker, preset=emotion,
duration=line.duration)` at `line.start`, appended to the flat list — one code path, two
front doors; the `speaker [happy]: …` regex is untouched. An unknown preset is a validate
**error**, not today's silent nothing. Scene IR stays `0.1.0` (an additive union member;
`extra="allow"` on read). Six enumerations grow one entry each — `compose.py`
`duration_of`/`_flatten_into` (`:154`, `:208`), `sync.py` parser (`:284`) and writer (`:459`),
`validate.py` (`:157`), `iterate.py`'s grammar (`:153`), and **the compiler's own dispatch**
(`compile.py:1521-1542`, `:1591` — an `ExpressionAction` reaching `_compile_actions` today
raises `TypeError("unsupported FlatAction.action type")`); plus `an/ir/__init__.py` /
`an/__init__.py` `__all__` for the `expression()` combinator, following `play`. Two rules the
pass added: the desugaring is **in-memory only** — an `ExpressionAction` derived from
`[emotion]` must never reach `shot.actions` or the `ScenesStore`, or the writer emits the
emotion twice and the round trip doubles it; and because the md writer **skips unknown
leaves silently** (`sync.py:475`), the writer entry and its round-trip test land in the same
commit as the schema change, or a JSON-side expression vanishes from `scene.md` and a later
md edit strips it from the JSON too.

## 6. Composition: the face solver (decided)

Two ways to make "an emotion and a gaze compose additively, asserted at the compiler level"
true. **Offset channels** (`ChannelJSON.mode = "add"`, accumulated in both evaluators) would
touch the parity test, the determinism report and the one-applier rule, and buy nothing:
every face contributor is *generated by the compiler*, which therefore already knows the
whole sum. **Compile-time summation wins**: a face solver computes, per bound `(node,
property)`, `value(t) = rest + Σ offset_i(t)` over every generated contributor, sampled per
frame like the squash blink already is (`compile.py:2325-2345`), and emits **exactly one
channel per key**. Later-wins then has no two generated writers to arbitrate — which is
also what kills the blink-masking hazard: a sleepy expression holding `eyelid=half` appended
*after* a front-placed blink would mask every blink; as a contributor to one lid state it
cannot.

| contributor | today | under the solver |
|---|---|---|
| emotion brows | `__emo__` step clips, `rotation` only, appended | `brow_height_*` → `y` offset, `brow_angle_*` → `rotation` offset, ramped by `blend` |
| gaze | nothing | offsets on the pupil nodes' `x`/`y` — a different key from any brow or lid, so it sums with emotion by construction; saccade jitter (§9) is one more addend on the same key |
| blink | `_add_blink_clips`, front of track | a contributor to the **lid state** `lid(t) = min(lid_expr(t), lid_blink(t))`, where `lid_expr` is the clamped sum of expression offsets in [−1, 0.5] and `lid_blink` is −1 inside a blink's closed span (`_EYELID_CLOSED_SPAN`, the central half of the window) and 0 outside; the key is then read off one threshold ladder — `wide` > +0.25, `open`, `half` < −0.35, `closed` < −0.85 — so a blink always closes, `wide` is representable, and §4's numbers are the only numbers. On rigs without closed art the squash path stays a sampled sine on `scale_y` (`:2331-2350`) and a non-zero `lid_expr` scales it. **When no expression or gaze touches an entity, the solver emits today's blink clips verbatim** — same ids, same two exact-time keyframes (`:2305-2329`; they are not frame-snapped) — which is what keeps every corpus scene's compiled document identical |
| viseme | `_add_viseme_clips`, appended | keyframe *preparation* (§11) stays; **emission** moves into the solver so one mouth channel exists per mouth node |
| authored tween/set on a face node | later-wins by position (a compiled emotion wins today) | **authored is absolute and wins** — the face clip goes at the track front like blinks, and a `CutoutCompileWarning` names the overlap (`"authored tween on gale/head/left_brow:rotation overrides expression 'angry' over [2.0, 3.0]"`). Adding an author's tween as an addend would silently move the end value they wrote |

So `_add_viseme_clips`'s emission, the `_EMOTION_BROWS` emitter and `_add_blink_clips`
collapse into `_add_face_clips(shot, animations, tracks, *, vocab, provider, fps)`.
The compiler-level assertion (`tests/test_expression_compose.py`): every generated face
channel has a distinct `(target, property)`; the evaluated pose at t = 1.0 has *both* the
brow rotation at `rest + ANGRY.brow_angle_l·gain` *and* the pupil x at `rest + gaze·gain`;
and the pose is identical when the contributors are fed in reverse order (commutativity —
what override lacks). Mutation guards: swapping the solver's `+` for last-wins fails the
third; a second generated writer for a brow fails the first.

**The provider seam.** `an/expression/` holds the axes, the presets, and

```python
class ExpressionProvider(Protocol):
    def curves(self, shot: Shot, entity_id: str, *, fps: int) -> Iterable[AxisCurve]: ...
    # AxisCurve(axis, samples: list[float]) — offline, deterministic

@dataclass(frozen=True) class ChannelBinding: slot: str; property: str; gain: float
@dataclass(frozen=True) class SetBinding:     slot: str; set_family: str
CharacterDescriptor.expression_binding: dict[str, list[ChannelBinding | SetBinding]]
# default_factory from the default rig; additive field, no schema bump
```

The default provider composes authored `expression` leaves, desugared dialogue, `gaze`
actions and the seeded saccade generator; an audio- or vision-driven provider later plugs in
at `curves` and inherits the solver, the binding and every guard.

## 7. Emotion × viseme by selection (decided)

Sets are 1-D and the property *is* the set name. Key composition (`A.angry` inside `viseme`)
was rejected: the fallback becomes string parsing that `an character validate` and the
runtime know nothing about, and a partial variant sits silently beside the neutral keys.
**One set per variant, `viseme@<preset>`, wins.** `@` is legal (`swap_set_name_problem`
reserves only the transform names, `/` and `::`, `an/base.py:115-137`); each variant projects
onto the mouth slot exactly like any set; validate's per-set checks apply unchanged; the
fallback is **set-level and per line**.

```json
"asset_sets": {
  "viseme":       {"A": "mouth_a", "B": "mouth_b", "C": "mouth_c", "D": "mouth_d", "E": "mouth_e",
                   "F": "mouth_f", "G": "mouth_g", "H": "mouth_h", "X": "mouth_x"},
  "viseme@happy": {"A": "mouth_a_happy", "B": "mouth_b_happy", "C": "mouth_c_happy", "D": "mouth_d_happy",
                   "E": "mouth_e_happy", "F": "mouth_f_happy", "G": "mouth_g_happy", "H": "mouth_h_happy",
                   "X": "mouth_x_happy"},
  "eyelid":       {"OPEN": "open", "CLOSED": "closed"}
}
```

— **and** the nine `mouth_*_happy` attachments declared in `skins.default.slots.mouth`;
without them `an character validate`'s `_check_asset_sets` reports nine BLOCKING findings
today ("maps 'A' to attachment 'mouth_a_happy', which no slot of the skin carries"), which is
the check doing its job. A data change, zero compiler change, as an#87 promised.

Resolution lives in `an/expression/binding.py`, renderer-free like `an/characters/play.py`,
and is the one function `an validate`, `an character validate` and the solver call:
`resolve_mouth_set(desc, preset, *, keys_used) -> str` — `viseme@<preset>` if declared **and**
it covers `keys_used`; else `viseme` with a warning naming the missing keys; else
`ExpressionResolutionError` (a speaking overlay-face character with no neutral mouth set).
Selection is **whole-line**: two mouth properties live at once would apply in name order
(`poseKeysInApplicationOrder`, `runtime.js:304-310` — the exact hazard `play.py`'s docstring
records for `blink`), so the solver guarantees at most one mouth swap property per instant;
outside lines a silent happy character shows `viseme@happy = X`. The factory's
`mouth_set` already takes a `smile` parameter (`an/characters/mouth_set.py:48-70`), so
`viseme@happy` / `viseme@sad` variants are a generation flag away for synthesized characters.
`an character validate` adds: every `viseme@<p>` names a known preset; advisory when a variant
lacks keys the neutral set has; blocking when `face_overlay` is true and no `viseme` set
projects anywhere but a variant does.

## 8. Presets (our art direction; AU numbers are anchors, not sources)

Gaze is absent from every preset so the two sources stay independent — "thinking looks up
and away" is a `gaze` action, not a preset value.

| preset | brow_height (l, r) | brow_angle (l, r) | lid_open | mouth set | AU anchor |
|---|---|---|---|---|---|
| neutral | 0, 0 | 0, 0 | 0 | `viseme` | — |
| happy | +0.2, +0.2 | +0.1, +0.1 | −0.2 | `viseme@happy` | 6+12 |
| sad | +0.3, +0.3 | +0.6, +0.6 | −0.3 | `viseme@sad` | 1+4+15 |
| angry | −0.6, −0.6 | −0.8, −0.8 | +0.1 | `viseme@angry` | 4+5+7+23 |
| surprised | +1.0, +1.0 | 0, 0 | +0.4 | `viseme@surprised` | 1+2+5+26 |
| afraid | +0.7, +0.7 | +0.5, +0.5 | +0.5 | `viseme@afraid` | 1+2+4+5+7+20+26 |
| disgusted | −0.3, −0.3 | −0.3, −0.3 | −0.4 | `viseme@disgusted` | 9+15+17 |
| thinking | +0.5, −0.2 | +0.3, −0.1 | −0.1 | `viseme` | cartoon convention (UNVERIFIED) |
| skeptical | +0.6, −0.3 | 0, −0.2 | −0.2 (r) | `viseme` | kept: corpus and demo use it |

| amused | +0.1, +0.1 | +0.05, +0.05 | −0.1 | `viseme@happy` | `happy` at ~0.6 — kept because `examples/character_gallery`, `park_bench_cartoon` and the README author it |

Every name `_EMOTION_BROWS` accepted stays a preset (the pass found `amused` in three pieces
of live content), so the retirement is a rename of the *mechanism*, not of any scene. A
variant mouth set is *optional* per preset: a character that declares none
falls back to `viseme` with a warning, per §7. Hazard recorded: `report 3 …md:170-186`
carries a parameter table "derived from EMFACS prototypes" — that is the certification-gated
table by another name; the presets above do not copy it, and the Du/Tao/Martinez 2014 and
EmotioNet per-emotion AU lists were **not** reachable (paywalled / 403) and are not relied on.

## 9. Gaze: a pupil layer and a seeded saccade generator (decided)

**No nesting, no mask.** A slot nests only under the *primary* slot of its bone (the slot
named like the bone, `play.py:130-149`); making `left_eye` a parent would mean a new
`left_eye` bone with the eye slot rebound to it — at which point the eye leaves `head` and head
rotation stops carrying it. Not impossible, and not worth it: a pupil bound to `head` with the
eye's own `FACE_OFFSETS` (`schema.py:559-565`) lands in the same place. PixiJS masks do not exist in `runtime.js` (`rg mask` = 0) and would
be a new render-time input to a determinism perimeter that today watches page, tickers and
filters (`an/determinism.py:100-134`) — their anti-aliasing under the pinned flags is
unverified. The pupil stays inside the white by **clamping at compile time**: travel =
sclera clearance minus pupil radius, a descriptor number, not a renderer feature.

**The eye becomes three sibling slots** per side: `left_sclera` (white fill, draw order 5)
→ `left_pupil` (7) → `left_eye` (the existing slot, now the *lid*, drawn above the pupil).
Factory art: `open` becomes outline-only with a transparent interior; `closed` becomes a
**filled** skin-tone lid (a stroke-only closed eye would show the pupil through it);
`sclera_l/r.svg` and `pupil_l/r.svg` are two new synthesizers. The `eyelid` set is untouched
— the swap still lands on `left_eye`/`right_eye`. `gaze_x`/`gaze_y` on the entity compile to
yoked `x`/`y` channels on both pupil nodes (already runtime-applied properties), scaled by
the clamp. The procedural rig gets no gaze (its eye is one `Graphics`). The blink squash is applied on
the lid node (`:2351`), so once the white and pupil are siblings a rig **without** closed art
would squash only the outline while the white and pupil stayed put — `closed` art becomes
mandatory whenever `GAZE_PARTS` are present, and `an character add-gaze` synthesizes it.

**No migration, deliberately.** `_default_slots` runs only when a descriptor is constructed
without slots (`schema.py:343-351`), so new characters get the stack and existing descriptors
do not. Inserting pupil slots by migration would be *worse* than a no-op: their art would be
absent, absent art is recorded as a fallback (`compile.py:1249-1256`), and that is fatal under
`strict_assets` — every existing character would stop rendering on the bench. So gaze is a
**no-op on a pre-Wave-6 descriptor**, reported by `an character validate` as an `info`
Finding ("no pupil slot; run `an character add-gaze <name>`"), and `add-gaze` is the expand
step that synthesizes the parts and rewrites the descriptor. Pupil parts join an optional
`GAZE_PARTS`, never `REQUIRED_PARTS`. Consequence for the bench: no existing golden moves.

**The generator** (`an/adapters/cutout/gaze.py`, pure Python). At 24 fps a frame is 41.7 ms
and a small saccade (20–200 ms, up to ~700°/s — Wikipedia "Saccade", fetched; the main
sequence via PMC8960849) is sub-frame, so it **emits step keyframes** snapped to frame times — the way the blink
*squash* path samples (`compile.py:2331-2350`); the eyelid swap itself emits at exact
fractional times (`:2305-2329`) — a jump is the honest rendering.
Inputs: seed, duration, fps, per-axis amplitude clamp, fixation distribution parameters,
blink windows. Fixation lengths are right-skewed with a ~200 ms peak and a long tail
(McConkie & Dyre via PMC11404824; that study measured mean 434 ms, SD 318 ms on a random
task), so draw from a gamma clipped to `[0.12, 1.5]` s; amplitudes mostly small with a rare
large jump; a horizontal bias; and **blink–saccade coupling** — gaze-evoked blink probability
rises with amplitude (PMC3262917) — so a jump above a threshold is moved to the centre of the
nearest blink window within ±150 ms when one exists: the lid hides the pop. The "Eyes Alive"
model (Lee, Badler & Badler, SIGGRAPH 2002) is the canonical statistical source and was
unreachable (403 on both copies): its fitted numbers are **UNVERIFIED and must not be
transcribed from memory** — every constant above is a design value and is labelled so in
code. Seeding follows the blink *pattern* — a pure function of the entity name — but blinks use no
PRNG (`blink_phase` is `_js_string_hash(name) % 1000 / 1000`, `compile.py:178-184`); the
generator seeds `random.Random(_js_string_hash(entity_id) ^ GAZE_SALT)` (integer seeding is
version-stable) and stamps the seed into `meta` beside `blink_phases`, because renaming a
corpus character re-seeds every saccade (the recorded blink hazard, `compile.py:144-147`). `AN_DETERMINISTIC` needs no new entry: it is an OFF switch judging a
*runtime* report about latent randomness; a seeded Python generator is pinned by
construction, and the guard is a test compiling the same shot twice and diffing the JSON.
Authored gaze and the saccade jitter are two addends on the same pupil key in the solver —
one node, one channel, no container node.

## 10. Baked-face characters (decided)

Overlay-over-baked-art is rejected, and was rejected once already: `_fallback_face_svg`
records the "four eyes" bug (`factory.py:237-243`), the DiceBear head is pasted whole with no
landmarks (`dicebear.py:8-16`), and overlaid brows would float. **Typed refusal for anything
authored; policy no-op for anything ambient.** An explicit `expression` or `gaze` action on a
`face_overlay: false` character is a validate error and an `ExpressionResolutionError` at
compile, naming the character, the field and the two exits (`an character promote` for a
hand rig; `an character add-gaze`). A dialogue-sugar emotion is a **warning with the right
diagnosis** — the audio still plays, as lip-sync does today — replacing today's silence
(§2 item 4). Ambient saccades follow blinks, which already skip baked faces silently:
nothing was asked for. All three read the one declared fact through `_baked_face_speakers`
(`compile.py:1999-2035`).

## 11. Lip sync (decided)

**Rhubarb (defect 5a, confirmed at source).** README: `pocketSphinx` "(use for English
recordings)" is the default, `phonetic` "(use for non-English recordings)"; `--dialogFile`
"will still perform word recognition internally, but it will prefer words and phrases that
occur in the dialog file". `PhoneticRecognizer.cpp:13-14` `createDecoder(optional<std::string>
dialog) { UNUSED(dialog);`; `PocketSphinxRecognizer.cpp:94,100-101` builds a dialog language
model and mixes `{defaultLM, dialogLM}` at `{0.1f, 0.9f}`. Fix:
`RhubarbLipSync(*, language="en", recognizer=None)` — `None` resolves per language (`en` →
`pocketSphinx` **with** the dialog file; anything else → `phonetic` and the transcript is
not written at all); an explicit `recognizer` overrides; the factory and `an render --lipsync
rhubarb` grow `language`. The argv is pinned by a test that stubs `subprocess.run`, because
`tests/test_rhubarb_lipsync.py` skips without the binary. The provider's `name` gains the
recognizer (`rhubarb:pocketSphinx`) so the viseme cache key (which hashes `lipsync.name`)
changes and no stale `phonetic` track replays (§2 item 6). Rhubarb is MIT; its bundled
Sphinx models are BSD-variant (LICENSE.md); `an` shells out to a user-installed binary and
ships none of it, so no notice attaches to `an`.

**The condenser (defect 5b): hold, and let the window vote.** Rhubarb's own optimizer
does the opposite of our `continue`: "Select shape with highest total duration within the
candidate range" (`timingOptimization.cpp:33`; `minSegmentDuration = 8_cs`, "the minimum
duration a segment … must have to visually register"). Adopt a **duration-weighted vote**
weighted by dominance, and — because compilation is offline — let the vote cover the
window's *opener* too: the line is segmented into windows of at least `min_hold` starting at
each key that clears the previous window; within a window the shape with the largest
`raw_span × dominance` shows, **placed at the window start**. That is the pass's correction
to the first draft, which placed the winner at window *expiry* (a 60 ms delay the
anticipation lead would then have to undo) and never voted against the opener (a 10 ms /t/
arriving first still owned the whole hold — the mirror of the latest-wins hazard, where a
10 ms /t/ would mask a 100 ms /u/). Example: `[(0,X),(.30,B),(.34,A),(.38,D),(.80,X)]` → the
window at 0.30 holds B (0.04×0.3), A (0.04×1.0) and D (0.42×0.8); D wins and shows **at
0.30** → `[(0,X),(.30,D),(.80,X)]`, where today's loop gives `[X, B, X]`. The terminal rest invariant and the `t < line.duration` filter still
apply afterwards. The existing viseme tests key at 0/0.3/0.7 s and pass unchanged; nothing
pins the gap, which is why the defect survived. **The one speaking corpus scene samples outside
its line and `promote_demo` is mute** (§1), so the fix moves zero pixels and the bench is
blind: a corpus fixture with dialogue and a golden *inside* the line lands **before** the
change — and the parser's silent drop of an unmatched dialogue line becomes an **error**
(PR-A), with `promote_demo`'s `maya (warm):` corrected so it speaks again.

**Co-articulation passes**, pure functions over `list[Viseme]`, run **in the compiler**
where `_MIN_VISEME_GAP_S` lived (now `_LEGACY_MIN_VISEME_GAP_S`, kept for the OFF path) — not in the audio pipeline — so the cached track stays the
raw provider output and a knob change is a recompile, never a re-alignment. `Viseme.intensity`
(exists, set by nothing) becomes the dominance carrier. In order: **(a)** symbolic —
`merge_duplicates`, and `suppress_weak` drops a low-dominance key whose span is under one
frame (JALI, Edwards et al. 2016 §4.2 "Animation Phase": "Tongue-only visemes (l n t d g k N)
have no influence on the lips"; Rhubarb encodes the same at `animationRules.cpp:150`, `case Phone::N: return
single({ B, C, F, H })` — a *set* its optimizer resolves to the neighbours). In Rhubarb's
letters the tongue class is B and H, but B also codes EE, so providers that know the
character stamp consonant-origin B low and Rhubarb tracks get the letter-table default.
**(b)** dominance — Cohen–Massaro (1993) blend targets by per-segment dominance (α = .06 for
/s/,/t/ vs 1 for /u/ on protrusion); two drawings cannot be averaged, so the weighted mean
degrades to an **argmax over the window** with `weight = raw_span × α`. The order of
`DOMINANCE` is sourced — A (bilabial closure; JALI rule 1) > F, G > E, D > C > X > B, H —
its values (1.0/.9/.8/.6/.5/.3) are art direction. **(c)** envelope = timing offsets:
anticipation ("speech onset begins 120 ms before the apex", JALI §4.2 citing Bailly 1997;
the "two frames ahead" folk rule; Rhubarb's `maxExtensionDuration = 6_cs`) → `lead_s = 2/24`
(art direction), clamped at 0; the apex *is* the swap; decay — a word-final open vowel
before a gap returns to `X` `decay_s ≈ 0.12` after its last key (JALI's "120 ms to decay"),
replacing the `prev_end + 0.05` heuristic (`lipsync.py:114-115`). Note `Viseme.intensity`
already round-trips through the cache as a stored `1.0`, and the knobs never enter the key,
so old cached tracks keep `1.0` until re-aligned — the letter-table default then applies. **(d)** the minimum hold =
the condenser, **last**. Order matters: (a) before (d) so a one-frame /t/ never wins a
window; (c) before (d) so the hold is measured on shifted times. Keep `min_hold_s = 0.14`
until measured; Rhubarb's floor is 0.08 s, one frame (~0.04 s) is the floor below which
nothing registers.

**Landed in #101 (PR-A, 2026-08-24).** Word timings retained (`VisemeTrack.words`,
`Dialogue.word_timings`, the sidecar's `"words"` under the unchanged key); the Rhubarb
recognizer follows the language, with `an render --language` reaching the factory; the
parser refuses an unmatched dialogue line; `misc/bench/corpus/dialogue/` blessed. Two
deviations from the plan above, both deliberate: `promote_demo`'s line was repaired so it
*parses*, but **no visemes were stamped into its committed IR** — doing so moved its
contract hash with zero pixel change, the #98 hazard — so in the bench it still renders
mute and `dialogue` is the wave's one mid-line golden; and the `dialogue` golden at frame 14
shows **`C`, not `D`**: today's condenser drops the `D` and `A` of "shape" inside `C`'s
window, which is precisely the defect PR-B's vote changes. The "Rhubarb" and "Lip sync"
paragraphs of §1 are the pre-#101 record and stay as written.

**Landed in PR-B (an#97).** The four passes as described, with two corrections from building
them: the vote weights each member by its span **inside the window** (a long vowel no longer
out-votes the shape that owned the window from beyond it) and a losing member that runs past
the window opens the next one — Rhubarb's segmentation exactly; and the keyframe-count
claim below is true against the RAW track only — against the old condenser the count
**rises** (`dialogue`: raw ~17/s → 5.8/s → 7.3/s), because that loop was cheaper only by
dropping the shapes a viewer reads. A pre-existing defect surfaced on the way: the terminal
rest at `line.duration` was never *sampled* when the line ended between frames (frame 17 at
0.708 s, frame 18 outside a 0.71 s window), so `single_character` had its mouth stuck open
after the line; the clip window is frame-ceiled now. The legibility cassette is scaffolded
(prompt, parser, frozen strips — `python tests/_lipsync_strips.py` renders the `dialogue`
fixture with the passes off and on and writes eight frames per pane — and the replay-only
test) but **not recorded** — no API key was available to the session; the live test is one
command for a human with one (`AN_LIVE_API_TESTS=1 pytest -m live_api -k legibility`).

**Word-timing retention** (landed first; no pixel, no cache key). `VisemeTrack.words:
list[WordTiming] | None` on the audio dataclass; whisper and `WordTimingsLipSync` fill it,
offline and Rhubarb leave `None`. Persist in the **existing** viseme sidecar payload
(`pipeline.py:222-227`, add `"words"`); the key is unchanged (words are a function of the
same three inputs; the co-articulation knobs must never enter it). IR: `Dialogue.word_timings:
list[WordTimingIR] | None` beside `viseme_track` (line-relative `text, start, end`), same on
`Narration`; optional with a default, so no `SCHEMA_VERSION` bump. `already_done` gains
`and line.word_timings is not None` **only** for providers that can supply words, or every
Rhubarb project re-synthesizes forever — that is also what refreshes an old word-less payload
without a key salt. `sync.py` needs nothing: dialogue serializes as `speaker [emotion]: text`,
so the field is JSON-only. Wave 8 reads `line.start + word.start`; muvid already feeds
timings through `WordTimingsLipSync` and gets a round-trip for free.

**The alignment-model trap** (rule, no code today). `WhisperLipSync` passes no `language=`
and faster-whisper's word times come from DTW inside the one MIT model — safe. WhisperX is
the trap, verified at `alignment.py`: `DEFAULT_ALIGN_MODELS_TORCH` maps fr/de/es/it to
`VOXPOPULI_*` bundles that torchaudio documents as "under CC BY-NC 4.0"; `vi` to a
`cc-by-nc-4.0` HF model; `he/hi/hr/gl` to models with **no licence declared**; its only
refusal is for an *absent* entry. **Rule for `an`:** any provider that selects weights by
language carries an explicit allowlist `{"en": (model, "MIT", url-read)}`, and an unlisted
language raises `LipSyncError` naming the language and the licence gate — never a warning,
never a vendor fallback; the `CassetteMiss` shape. Until `WhisperLipSync` takes a language
seam, its test is that `language` is not forwarded.

## 12. Measurement (decided)

- **Corpus.** Two scenes, not ten. `misc/bench/corpus/expressions/`: a committed descriptor
  rig with the eye stack (no `prepare`), 320×240, eight 0.25 s shots, one per preset, a
  **silent** character holding `expression: <preset>` — `golden_frames` at each shot's
  mid-frame, `golden_note` recording that no blink window straddles a golden (choose the
  entity name by its `blink_phase`). `misc/bench/corpus/dialogue/`: one spoken line with a
  golden *inside* the line (t ≈ 0.3 s), blessed before the condenser changes; it also feeds
  the keyframe-count metric. `--bless`'s pixel-identical refusal is pairwise over
  every golden (`an/bench/golden.py:470-479`, a nested loop), so eight goldens are allowed
  and two presets rendering identically is refused at bless time — the weakest form of
  "distinguishable" comes free; the threshold test below is the strong one.
- **"Distinguishable"** is an offline test, not a judge: `tests/test_expression_goldens.py`
  decodes the eight goldens with `an.bench.png` and asserts, for every pair, `changed_px ≥ N`
  inside the face crop using `golden.py`'s per-frame comparison (`:396-416` is the worst-frame
  reduction over it). `N` is set from
  the first bench run's minimum pairwise delta (blessed at half of it), and recorded as a
  render-side family-B metric `expression_min_pairwise_changed_px` so a regression that
  collapses two emotions moves a ledger number.
- **The judge.** A "name the emotion" prompt is a new cassette, not a new seam. Frames are
  **frozen copies** under `tests/fixtures/vision_frames/expressions/` (the key is the sha256
  of the bytes; a re-bless re-encodes). Recorded once with `AN_LIVE_API_TESTS=1` (~$0.005,
  one call carrying eight PNGs — the first cassette this repo commits); replay is the default;
  a miss is `CassetteMiss`. Baseline for a discrete answer: chance is 1/8, assert accuracy
  ≥ k/8 with k pinned from the recording — `skeptical` vs `thinking` will likely fail, which
  is information about the vocabulary, not the judge. For legibility, a second call over a
  dense 8-frame strip inside the dialogue line asks for a 1–5 "could you read this mouth is
  saying `<text>`" score; the done-when is two numbers on one cassette: viseme keyframes per
  second of dialogue (counted from compiled `__viseme__` clips, trailing rest excluded) down
  **against the raw provider track** — against the old drop-not-hold condenser it rises on a
  dense track, measured in the PR-B addendum to §11 — legibility not down. The judge stays out of the ledger (bench rule 5).

## 13. Demos (every user-facing piece gets a clip)

Under `misc/demos/README.md`'s rules — offline synthesized characters, no burned-in labels,
panes described in `shows`, crops declared in `how`:

| slug | shows |
|---|---|
| `expressions` | a **silent** character holding an expression; 2×2 tiled like `emotion`: neutral · happy / angry · surprised — and the narrowness: brows, lid openness and mouth rest; no cheeks, no nose |
| `expressions-more` | sad · skeptical / thinking · afraid (disgusted reads weakly on a cutout — say so) |
| `gaze` | left: authored gaze sweeping left → centre → right; right: the same with ambient saccades on — the jumps are step keyframes on frames, deliberately |
| `gaze-plus-expression` | `angry` held while gaze tracks: brows stay furrowed as pupils move — additivity on screen |
| `lipsync-coarticulation` | the same line twice, side by side: today's condenser vs hold + envelope; the mouth *art* is identical, only selection timing differs |
| `emotion-visemes` | the same line as `[happy]` and `[sad]`: the viseme channel selects from an expression-specific mouth set — selection, never blending |

Every face demo renders a **synthesized descriptor character** (`_project(...,
characters=…)`), not the procedural placeholder: the procedural mouth is a drawn set with no
variant sets, so `viseme@<preset>` selection cannot be shown on it, and its rect brows
declare no gain for `brow_height → y`. The baked-face refusal is a sentence in
`expressions`' `shows` and a test, not a clip. The retired `emotion` demo's entry is
replaced, not kept beside the new one.

## 14. The re-planned wave (PR structure)

Each PR: adversarial review, then squash-merge (a release). Order chosen by dependency and by
what moves pixels.

- **PR-A — word-timing retention + the two lip-sync defects, pixel-neutral half.** `VisemeTrack.words`,
  `Dialogue.word_timings`/`Narration.word_timings`, sidecar payload, `already_done` rule;
  `RhubarbLipSync(language=, recognizer=)` with the argv test and the cache-key-changing
  `name`; the `dialogue` corpus fixture blessed on the **old** condenser; `promote_demo`'s
  dialogue line repaired and an unmatched line in a ```` ```dialogue ```` block made a
  parse **error** (today it is dropped silently, `sync.py:207-208`). Nothing in the
  corpus renders differently.
- **PR-B — co-articulation.** The four passes in the compiler (`an/adapters/cutout/coarticulate.py`),
  the condenser replaced by the duration-weighted vote, the keyframes-per-second metric,
  the legibility cassette (first committed cassette), the `dialogue` golden re-blessed
  ("condenser holds"), the `lipsync-coarticulation` demo, the `an-dev-lipsync` skill.
- **PR-C — expression.** `an/expression/` (axes, presets, binding, provider Protocol,
  `resolve_mouth_set`), `ExpressionAction` through the five enumerations, dialogue desugaring,
  the face solver (`_add_face_clips` absorbing blinks, visemes and the retired
  `_EMOTION_BROWS`), `viseme@<preset>` variants generated by the factory's `smile` flag,
  `an character validate` rules, the typed baked-face refusal, the `expressions` corpus scene
  + the distinguishable test + the emotion cassette, demos `expressions` / `expressions-more`
  / `emotion-visemes`, the `an-dev-expression` skill, the `an` skill and `iterate.py` grammar.
  **Contract, not just pixels:** `scene_contract_sha256` covers the whole staged JSON
  ("animation keyframe floats … are in", `contract.py:53-70`) and `bench-compare` refuses rows
  whose hash differs — so a solver that re-emits blinks under new ids or frame-sampled times
  would retire every committed ledger row as evidence even with zero pixel change. The
  acceptance is therefore **JSON identity**: no corpus scene authors an emotion (verified —
  `rg '\[' misc/bench/corpus/*/scene.md` finds nothing), the solver emits blink clips verbatim
  for untouched entities (§6), and all seven corpus contract hashes are asserted equal to the
  committed ledger row's before bless.
- **PR-D — gaze.** The eye stack in the factory (sclera / pupil / filled lid), `GAZE_PARTS`,
  `an character add-gaze`, `gaze_x/gaze_y` as expression axes plus the seeded saccade
  generator with its `meta` stamp, the `gaze` and `gaze-plus-expression` demos, the
  procedural-rig no-op documented. `promote_demo`'s golden is untouched (its rig predates the
  stack). **The `expressions` corpus rig gets the stack here**, so its eight goldens are
  re-blessed once ("gaze stack lands") — §9's "no existing golden moves" is about pre-Wave-6
  scenes, and PR-C's corpus rig deliberately ships without pupils.
- **PR-C also carries** a guard on `iterate.py`'s preset list (today `tests/test_iterate.py` only sets `"happy"`, so the eight-name edit has no test either way).
- **PR-E — the breaking-change bracket, if any.** Everything audio-side above is additive;
  muvid's imports (`orchestrate`, `StaticWordTimings`, `WordTimingsLipSync`) keep their
  signatures. If PR-C's `_add_face_clips` changes a compiled document for a scene with
  dialogue emotion, that is a pixel change in `examples/` only and needs no bracket. Expect
  this PR to be empty; it is listed so its absence is a decision, not an omission.

**Done-when, restated against the code:** a silent character holds a named expression across
a shot and its golden differs from neutral by ≥ N px in the face crop; all eight presets are
pairwise distinguishable by the same test; an emotion and a gaze compose in one pose with
both offsets present and the result order-independent, asserted at the compiler; the
committed cassette names the intended emotion at ≥ k/8; viseme keyframes per second fall on
the `dialogue` scene while the legibility score does not; a viseme under a preset with no
variant set falls back to `viseme` with a warning, and with no `viseme` set raises;
`rg _EMOTION_BROWS an/ .claude/ misc/docs/ README.md` returns nothing; word timings round-trip
whisper → IR → JSON → IR for the two providers that have them.

**Landed in PR-C (an#98).** As designed in §4–§8 and §10, with three deviations and these
facts from building it. Deviations: (1) §5's `an/__init__.py` export of the `expression()`
combinator is deliberately NOT made — `an.expression` is the subpackage, so the combinator is
`an.ir.expression`; (2) §6's "viseme emission moves into the solver" did not happen — emission
stayed in `_add_viseme_clips`, which now asks the provider for the line's preset and
`resolve_mouth_set` for the set, and the solver adds only the silent `__face_mouth__` holds
(still one mouth swap property live per instant, by interval subtraction rather than by one
emitter); (3) §7's validate rule is declaration-level ("declares a variant but no neutral
`viseme` set"), not projection-level. Facts:
`an/expression/` is `axes.py` (eight numeric axes, the ladder), `presets.py` (ten presets;
`disgusted` and `afraid` are the additions the table promised), `binding.py`
(`ChannelBinding`/`SetBinding`, `default_binding` from the slots a rig has, `binding_for` reading
the additive `CharacterDescriptor.expression_binding`, `resolve_mouth_set`, `expression_problems`
— the one list `an validate` reports and the compiler raises), `provider.py` (the
`ExpressionProvider` Protocol; the default sums authored leaves and the dialogue sugar into
per-frame `AxisCurve`s, ramped by `blend`) and `blendshapes.py` (the 52 names, import/export
only). The brow-angle sign was settled by geometry rather than by the old table: PixiJS
rotation is clockwise-positive with y down, so "+ = inner end up" is `-travel` on the left brow
and `+travel` on the right — the old `sad` entry had the same sign as `angry` and was wrong.
Gains are art direction (`BROW_HEIGHT_TRAVEL = 10` view-box units, `BROW_ANGLE_TRAVEL =
0.35 rad`). The solver emits one `__face__<shot>_<entity>` clip at the track front, with
per-frame linear keyframes run-length compressed; an entity nothing expresses on goes through
`_blink_placements`, the an#88 emitter's body moved verbatim, and **all seven pre-existing
corpus contract hashes equal the committed ledger row's** (asserted in
`tests/test_expression_compose.py`). Mouth variants are generated by the factory by default
(`DEFAULT_MOUTH_VARIANTS = {happy: +0.35, sad: -0.35}` corner upturn on the nine shapes), so a
fresh character honours `happy`/`amused`/`sad` without a warning. The `expressions` corpus
scene pins eight goldens (the two-frames rule became at-least-two); the first bless measured
106 px between `thinking` and `skeptical` (the asymmetric pair, as predicted) and 384 px at
the far end, so `tests/test_expression_goldens.py` pins 53, and the ledger row
`expression_min_pairwise_changed_px` (family B, diagnostic, counts nothing; gated under
`disabled_aa`/`supersample`, which move it in an undeclared direction — the lane caught the
first `not_applicable` declaration moving) reports the live number on every scene (a
two-frame scene reports its pair's own change; a render-side row is never null). Not built here: the "name the emotion" cassette (no key in the session; the judge and
freezer pattern from PR-B apply unchanged) — recorded as the remaining item on #98. Found on
the way: `an render` (the CLI) had raised `TypeError` since `--supersample` landed, because
`an.orchestrate.render_project` re-declared the leaf's parameters and fell behind while the
CLI test stubbed it; it is a pass-through now, with a test that stubs the leaf.

## 15. What the adversarial pass changed

One refuting pass over the synthesis (84 citations checked, 17 experiments run, five external
sources re-fetched). It killed or corrected:

- **Four wrong facts.** `promote_demo` has no dialogue — its line `maya (warm): …` never
  parsed and the parser drops unmatched lines silently (§1, §11, §14 PR-A gained a defect); a
  dialogue emotion on a baked face is *silent*, not mis-warned (§2.4, §10); `examples/` author
  `amused`, which the draft dropped (§8 keeps it); the JALI quotes are in §4.2, not §4.3.
- **Fifteen drifted line numbers** and the rig-contract skill's replacement lines (§1, §2).
- **An axis miscount** (ten, not nine — §4) and an **incoherent closure rule**: the draft's
  `max(blink, −lid_open)` with a 0.7 threshold contradicted §4's −0.85 and could not express
  `wide`; `_EYELID_CLOSED_SPAN` encodes the central half of the window, not 0.7 (§6).
- **A sixth enumeration** the leaf action must extend — the compiler's own dispatch raises
  `TypeError` today (§5) — and two rules: desugaring stays in memory; the md writer skips
  unknown leaves silently, so the writer entry lands with the schema (§5).
- **Two HIGH risks the draft did not name.** `scene_contract_sha256` moves for every scene
  with a character if the solver re-emits blinks differently, retiring every ledger row; the
  acceptance is JSON identity with blink clips emitted verbatim, not "byte-identical" as a
  slogan (§6, §14). And the eye stack makes `closed` art mandatory on rigs with pupils (§9).
- **The condenser's placement rule.** The draft placed the window's winner at expiry (a delay)
  and never voted against the opener; the vote now covers the whole window and the winner
  shows at the window start (§11).
- **Smaller:** the eyelid swap is not frame-snapped (only the squash path is); blinks use a
  hash, not a PRNG; nesting a pupil is possible but breaks head parenting; the §7 example
  needs its skin attachments; the brow binding needs a per-side sign; the face demos must use
  descriptor characters; `Viseme.intensity` already round-trips as `1.0`; the `expressions`
  goldens are re-blessed once when the eye stack lands; Zhang 2021's "matched" was
  overstated; `tests/cassettes/vision` is the directory's real name.

## 16. Sources fetched 2026-08-24

- Blendshape V2 model card — https://storage.googleapis.com/mediapipe-assets/Model%20Card%20Blendshape%20V2.pdf (sha256 above); Face Landmarker docs — https://developers.google.com/edge/mediapipe/solutions/vision/face_landmarker
- Circular 33 — https://www.copyright.gov/circs/circ33.pdf; *Google v. Oracle* summary — https://www.copyright.gov/fair-use/summaries/google-llc-oracle-am-inc-2021.pdf
- VRM 1.0 — https://github.com/vrm-c/vrm-specification/blob/master/specification/VRMC_vrm-1.0/expressions.md and `lookAt.md`; three-vrm — https://github.com/pixiv/three-vrm/blob/dev/LICENSE, `VRMExpressionPresetName.ts`
- Rive — https://rive.app/docs/editor/state-machine/states; https://github.com/rive-app/rive-runtime/blob/main/LICENSE
- Ostermann, *Animation of Synthetic Faces in MPEG-4* (1998) — http://ivizlab.sfu.ca/arya/Papers/IEEE/Proceedings/C%20A%20-%2098/Animation%20of%20Synthtic%20Faces%20in%20MPEG-4.pdf
- FACS — https://en.wikipedia.org/wiki/Facial_Action_Coding_System
- Zhang et al. 2021 — https://www.frontiersin.org/journals/psychology/articles/10.3389/fpsyg.2021.687974/full; Cabral, Tuts+ — https://design.tutsplus.com/articles/cartoon-fundamentals-create-emotions-from-simple-changes-in-the-face--vector-16278; South Park wiki — https://southpark.wiki.gg/wiki/Animation_Changes
- Rhubarb — https://github.com/DanielSWolf/rhubarb-lip-sync (`README.adoc`, `LICENSE.md`, `rhubarb/src/recognition/PhoneticRecognizer.cpp`, `PocketSphinxRecognizer.cpp`, `rhubarb/src/animation/timingOptimization.cpp`, `animationRules.cpp`)
- JALI — Edwards et al. 2016, https://dgp.toronto.edu/~elf/JALISIG16.pdf; Cohen & Massaro 1993 — https://bpb-us-e1.wpmucdn.com/sites.ucsc.edu/dist/0/158/files/2017/01/1993-modeling-coarticulation-in-synthetic-visual-speech.pdf
- WhisperX — https://github.com/m-bain/whisperX/blob/main/whisperx/alignment.py; torchaudio bundles — https://github.com/pytorch/audio/blob/main/src/torchaudio/pipelines/_wav2vec2/impl.py; faster-whisper — https://github.com/SYSTRAN/faster-whisper/blob/master/faster_whisper/transcribe.py
- Saccades — https://en.wikipedia.org/wiki/Saccade; https://pmc.ncbi.nlm.nih.gov/articles/PMC8960849/; https://pmc.ncbi.nlm.nih.gov/articles/PMC11404824/; https://pmc.ncbi.nlm.nih.gov/articles/PMC3262917/; "Eyes Alive" (Lee, Badler & Badler 2002, doi:10.1145/566654.566629) — UNREACHABLE, numbers not used
- UNVERIFIED (not relied on): Du, Tao & Martinez 2014 (PNAS) and EmotioNet AU lists; Williams' *Animator's Survival Kit* frame rule; Charalambous 2019 beyond its abstract; the LICENSE.md entry covering `cmudict-en-us.dict`; Adobe Character Animator docs; OverSimplified / Crash Course production practice.
