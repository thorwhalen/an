"""The metric declaration table — what each number is, and which way it should move.

This is **data, not prose**, because an#41's criterion is a query over it:
">=3 metrics from >=3 distinct causal families, evaluated per mutation, with a
per-metric per-mutation sign declared in advance". A second, hand-maintained
table of families is how that criterion silently stops being true.

Three things the research requires this table to carry, all three of which
invalidate every prior ledger row if retrofitted:

1. **A per-metric per-mutation direction**, two-sided where the optimum is
   interior. ``edge_transition_width`` has an interior optimum — under 1 is a
   staircase, 3+ is soft — and the sharpness family moves in *opposite*
   directions for the two mutations, so a single per-metric sign mis-reports
   one of them.
2. **``null`` (gated) distinguished from "no change".** "No change by
   construction" is a tautology and must never count as a satisfied
   prediction, or any pre-encode statistic pads the count for free. Four
   states, not two: ``gated`` (the reference moved, or the source hash
   differs), ``unavailable`` (the check could not run — a crashed check is not
   evidence anything is fine), ``no_change`` (tautology), ``not_applicable``
   (wrong side of the encoder).
3. **The family letters**, so the redundancy rule is checkable. Family C
   counts as **one** until the correlation between ``coded_luma_edge_error``
   and ``chroma_edge_dCr`` is measured across the encoder matrix — they were
   r=0.990 in their broken forms and the corrections are *expected* to
   decorrelate them, which is not the same as measured.

The invariants are enforced in ``__post_init__``, not by convention.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

#: The levers an#41 and an#56 use. At least one per SIDE is mandatory: an
#: encoder lever cannot touch a golden-frame metric because the corpus is
#: UPSTREAM of the encoder, so requiring ">=3 metrics" from a CRF change alone
#: would fail because of where the corpus sits, not because the instrument is
#: blind — and that failure would be misdiagnosed as the harness being wrong.
#:
#: `supersample` is the third and it is the odd one out on purpose: the other
#: two make the picture worse and it makes the picture BETTER. A panel that has
#: only ever been shown degradations cannot tell an improvement from a
#: regression — run as a plain commit-to-commit diff this change reports 2
#: false regressions and 7 unearned improvements (an#56). Declaring it is how
#: that gets found before it is believed.
MUTATIONS: tuple[str, ...] = ("high_crf", "disabled_aa", "supersample")


#: What each lever is EXPECTED to change about the recorded environment.
#:
#: Load-bearing, and not obvious: `an bench-compare` refuses two rows whose
#: encode command differs, because a crf23 row and a crf40 row are not "one
#: better and one worse". But the `high_crf` lever's whole method is to change
#: that command — so without this declaration, mutation mode refuses every
#: encode-side metric and the encoder lever, which is half of an#41's
#: deliverable, can never be evaluated at all.
#:
#: So the exemption is DECLARED per lever rather than inferred, it applies only
#: in mutation mode, and it names a path together with the CHANGE the lever
#: makes to it — a blanket "ignore the
#: environment when a mutation is given" would silently let a row from another
#: machine in through the same door.
#:
#: `disabled_aa` touches `render_side.runtime_sha256`, which is PROVENANCE and
#: not a comparability key — the runtime is the code under test, and two rows
#: rendered by different runtimes are exactly what `--compare` exists to
#: compare. Listing it here is therefore not an exemption from anything; it is
#: what makes `mutation_may_not_have_applied` able to answer for that lever at
#: all. Before it existed, `assert not report["mutation_may_not_have_applied"]`
#: asserted nothing for the AA lever (an#41 review).
@dataclass(frozen=True, slots=True)
class Touch:
    """One environment key a lever is expected to change, and HOW.

    ``differs_only_in`` names the argv flags whose values the lever may move.
    Without it the exemption is by PATH, and `x264_argv` is the whole encode
    command — so a `-preset medium` -> `-preset veryslow` change, which moves
    every encode-side number, rode in on the CRF lever's exemption and was
    reported as "the lever moved it — expected" (an#41 review). ``None`` means
    any difference is the lever's, which is right for an opaque digest.
    """

    path: tuple[str, ...]
    differs_only_in: tuple[str, ...] | None = None

    @property
    def label(self) -> str:
        return ".".join(self.path)

    def is_the_levers_change(self, before: object, after: object) -> bool:
        """True when the observed difference is the one this lever makes."""
        if before == after:
            return False
        if self.differs_only_in is None:
            return True
        if not isinstance(before, list) or not isinstance(after, list):
            return False
        return _without(before, self.differs_only_in) == _without(
            after, self.differs_only_in
        )


def _without(argv: list, flags: tuple[str, ...]) -> list:
    """``argv`` with each named flag and its value removed."""
    out: list = []
    skip = False
    for item in argv:
        if skip:
            skip = False
            continue
        if item in flags:
            skip = True
            continue
        out.append(item)
    return out


MUTATION_TOUCHES: dict[str, tuple[Touch, ...]] = {
    "high_crf": (
        Touch(
            path=("environment", "encode_side", "x264_argv"),
            differs_only_in=("-crf",),
        ),
    ),
    "disabled_aa": (Touch(path=("environment", "render_side", "runtime_sha256")),),
    # The SAME path as `disabled_aa`, and that is correct rather than a
    # collision: both render levers work by staging a patched copy of the
    # runtime, so both move the digest of the runtime that rendered. The entries
    # are keyed by lever, so nothing is shared. What the shared path DOES cost
    # is the strength of the fingerprint — "the digest is not the shipped one"
    # is satisfied by either lever — which is why `_verify_supersample`
    # recomputes the digest a resolution-patched runtime produces and asserts
    # EQUALITY, instead of copying `_verify_disabled_aa`'s inequality check.
    "supersample": (Touch(path=("environment", "render_side", "runtime_sha256")),),
    # A scalar, so `differs_only_in` would have nothing to name — that field
    # exists for `x264_argv`, where the whole encode command is one value and a
    # `-preset` change could otherwise ride in under a `-crf` exemption. Here
    # the key IS the knob.
    "pix_fmt": (Touch(path=("environment", "encode_side", "pix_fmt")),),
}

Side = Literal["render", "encode"]
Family = Literal["A", "B", "C", "D", "E", "F", "G"]
Expect = Literal["increase", "decrease", "no_change", "not_applicable"]
Reference = Literal["lossless", "source_png", "none"]

#: What an encode-side metric is measured AGAINST. Two answers, and the choice
#: is per metric rather than global:
#:
#: - ``lossless`` — the decode of a `-qp 0` encode of the same frames. That IS
#:   the plane libx264 received, on any build, so the metric is pure quantiser
#:   damage with no colour-conversion term. The default, and what every
#:   counting encode-side metric uses.
#: - ``source_png`` — an explicit RGB->YUV conversion of the pre-encode PNGs.
#:   Required by exactly two things. The chroma metric's subject IS the 4:2:0
#:   subsampling that happens during that conversion -- the term the lossless
#:   leg cancels, so a lossless-referenced version is blind to it (it does NOT
#:   read ~0; measured, it reads 1.7-2.5 against this metric's 2.9-9.3, because
#:   it measures chroma quantiser damage instead -- an#72). And `encode_ringing_excess`
#:   cancels a term that only exists when BOTH its legs share this reference —
#:   against the lossless leg it degenerates to raw overshoot, which is the
#:   refuted form.
#:
#: The distinction became visible when CI measured the PNG conversion to be
#: build-dependent (exact on ffmpeg 8.1, mean 0.63 / max 5 on the Linux
#: runner's older build). It is recorded per metric so a reader can tell which
#: numbers carry that term.
REFERENCE_NOTE: dict[str, str] = {
    "lossless": "the decode of a -qp 0 encode — the plane libx264 received",
    "source_png": "an explicit RGB->YUV conversion of the pre-encode PNGs",
    "none": "a property of the encoded file, not a comparison",
}

#: Which side of the encoder each causal family lives on. Render-side metrics
#: are computed on the pre-encode PNG and are blind to the encoder BY
#: CONSTRUCTION; encode-side metrics compare a decoded frame to its own source
#: and are structurally blind to render regressions, because the reference
#: moves with the mutation. They must never be mixed.
FAMILY_SIDE: dict[str, str] = {
    "A": "render",  # edge geometry
    "B": "render",  # golden change (tripwire)
    "C": "encode",  # coded-plane edge fidelity
    "D": "encode",  # flat-field fidelity
    "E": "encode",  # temporal held-pixel fidelity
    "F": "encode",  # rate cost
    "G": "encode",  # ringing
}

FAMILY_NAME: dict[str, str] = {
    "A": "edge geometry (render-side)",
    "B": "golden change (render-side tripwire)",
    "C": "coded-plane edge fidelity (encode-side)",
    "D": "flat-field fidelity (encode-side)",
    "E": "temporal held-pixel fidelity (encode-side)",
    "F": "rate cost (encode-side)",
    "G": "ringing (encode-side)",
}


class RegistryError(ValueError):
    """A metric declaration violates one of the table's invariants."""


@dataclass(frozen=True, slots=True)
class Prediction:
    """What one metric is expected to do under one mutation, declared in advance.

    ``expect=None`` means **gated**: the number is uninterpretable rather than
    good or bad, and ``gate`` says why. It is not "no change".
    """

    expect: Expect | None
    counts: bool = False
    gate: str | None = None
    reason: str = ""
    reference: str | None = None

    def __post_init__(self) -> None:
        if self.expect is None and not self.gate:
            raise RegistryError(
                "a gated prediction must name its gate; a bare null is "
                "indistinguishable from 'nobody wrote a prediction down'"
            )
        if self.expect in ("no_change", "not_applicable") and self.counts:
            raise RegistryError(
                f"expect={self.expect!r} can never count as a satisfied "
                "prediction — 'no change by construction' is a tautology, and "
                "counting it lets any pre-encode statistic pad the witness "
                "count for free"
            )
        if self.expect is None and self.counts:
            raise RegistryError("a gated prediction cannot count toward anything")

    def to_dict(self) -> dict:
        out: dict = {"expect": self.expect, "counts": self.counts}
        if self.expect is None:
            out["state"] = "gated"
            out["gate"] = self.gate
        if self.reason:
            out["reason"] = self.reason
        if self.reference:
            out["reference"] = self.reference
        return out


@dataclass(frozen=True, slots=True)
class Optimum:
    """Which way "better" points, and whether the optimum is interior."""

    kind: Literal["one_sided", "interior", "guard"]
    expect: Literal["minimize", "maximize"] | None = None
    note: str = ""

    def __post_init__(self) -> None:
        if self.kind != "one_sided" and self.expect is not None:
            raise RegistryError(
                f"kind={self.kind!r} cannot carry expect={self.expect!r}. An "
                "interior optimum has no 'better' direction — under 1 is a "
                "staircase and 3+ is soft — and a guard has none by declaration. "
                "Giving either one makes `--compare` report a regression the "
                "table explicitly refuses to claim. Enforced here because the "
                "guard test for that mutation SURVIVED it: the combination was "
                "inexpressible only by convention, so nothing went red."
            )
        if self.kind == "one_sided" and self.expect is None:
            raise RegistryError(
                "a one-sided optimum must say which way 'better' points"
            )

    def to_dict(self) -> dict:
        return {"kind": self.kind, "expect": self.expect, "note": self.note}


@dataclass(frozen=True, slots=True)
class MetricSpec:
    """One row of the panel."""

    key: str
    family: Family
    unit: str
    optimum: Optimum
    predictions: dict[str, Prediction]
    sentence: str
    role: str | None = None
    reference: Reference = "none"
    provisional: bool = False
    unreviewed: bool = False
    tripwire: bool = False
    #: What a scene must HAVE for this row to exist at all, or ``""`` when the
    #: row applies to every scene (an#111).
    #:
    #: The render-side panel rule is "nothing may be null on a real capture,
    #: because a null render-side row is a blind panel". That rule assumes
    #: every metric could have been measured. `stage_min_plane_ratio_gap`
    #: could not: a displacement ratio needs two planes moving at different
    #: depths, and `single_character` has no planes at all — so its null is
    #: structural, not a gap in the instrument.
    #:
    #: Declared rather than hardcoded in the test, so the panel rule keeps
    #: naming its own exceptions instead of a test file carrying a list the
    #: registry does not know about.
    requires: str = ""
    notes: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if self.family not in FAMILY_SIDE:
            raise RegistryError(f"{self.key}: unknown family {self.family!r}")
        missing = [m for m in MUTATIONS if m not in self.predictions]
        if missing:
            raise RegistryError(
                f"{self.key}: no prediction declared for {missing}. Every metric "
                "declares every mutation — an absent prediction is not the same "
                "as a null one, and only one of them is honest"
            )
        if self.tripwire and any(p.counts for p in self.predictions.values()):
            raise RegistryError(
                f"{self.key}: a tripwire counts ZERO toward any criterion. It "
                "fires on improvements and regressions alike, so it is a change "
                "detector, not evidence of quality"
            )

    @property
    def side(self) -> str:
        return FAMILY_SIDE[self.family]

    def to_dict(self) -> dict:
        out: dict = {
            "side": self.side,
            "family": self.family,
            "family_name": FAMILY_NAME[self.family],
            "unit": self.unit,
            "sentence": self.sentence,
            "optimum": self.optimum.to_dict(),
            "under_mutation": {m: self.predictions[m].to_dict() for m in MUTATIONS},
        }
        if self.role:
            out["role"] = self.role
        if self.side == "encode":
            out["reference"] = self.reference
            out["reference_note"] = REFERENCE_NOTE[self.reference]
        if self.provisional:
            out["provisional"] = True
        if self.unreviewed:
            out["unreviewed"] = True
        if self.tripwire:
            out["counts"] = 0
        if self.notes:
            out["notes"] = list(self.notes)
        # Encode-side rows are MACHINE-SCOPED: same ISA + same x264 build is
        # byte-identical, a different ISA moves the decoded stream, and a
        # different x264 build moves it by two orders of magnitude. A band wide
        # enough to absorb that would swallow flat_field_deviation's entire
        # crf18->23 signal, so `--compare` (an#40) must REFUSE, not widen.
        out["comparison_scope"] = "machine" if self.side == "encode" else "any_machine"
        return out


_GATED_REFERENCE_MOVED = "reference_moved"
_GATED_SOURCE_HASH = "source_hash_differs"


def _spec(*args, **kwargs) -> MetricSpec:
    return MetricSpec(*args, **kwargs)


#: The panel. Order is display order; the ledger sorts keys anyway.
METRICS: dict[str, MetricSpec] = {
    m.key: m
    for m in [
        _spec(
            key="edge_transition_width",
            family="A",
            unit="px",
            sentence=(
                "The average thickness, in pixels, of the fuzzy band between two "
                "flat colour areas — under 1 is a jagged staircase, ~1 is clean "
                "AA, 3+ means the picture has gone soft."
            ),
            optimum=Optimum(
                kind="interior",
                note=(
                    "Two-sided: both lost AA and accidental softening (blur, "
                    "non-integer canvas scale, LINEAR texel filtering) are "
                    "failures. The absolute value is scene-dependent — a "
                    "deliberately 3px black outline is legitimately non-flat — "
                    "so compare deltas on a fixed scene, never absolutes across "
                    "scenes."
                ),
            ),
            predictions={
                "high_crf": Prediction(
                    "not_applicable",
                    reason="computed on the pre-encode PNG; the encoder cannot move it",
                ),
                "disabled_aa": Prediction(
                    "decrease",
                    counts=True,
                    reason=(
                        "family A's single witness. Holds on five of six corpus "
                        "scenes; on `promote_demo` it moves +0.0001 the OTHER way, "
                        "because the descriptor path is nearly blind to MSAA (96 "
                        "differing pixels of 12.4M — an SVG sprite is a "
                        "pre-rasterised texture, and multisampling applies to "
                        "WebGL geometry). A `contrary` verdict at that magnitude "
                        "is the lever not reaching the scene, which is why the "
                        "comparison reports the relative delta beside the "
                        "direction (an#41)."
                    ),
                    reference="2.88 -> 2.00 on `aa_probe`; 5.6368 -> 5.6369 on `promote_demo`",
                ),
                "supersample": Prediction(
                    "increase",
                    counts=True,
                    reason=(
                        "family A's witness for THIS lever, and scene-dependent "
                        "by measurement in the exact inverse of `disabled_aa`. "
                        "Holds on the five procedural scenes (`graded_field` "
                        "+8.0%, `aa_probe` +7.4%, `saturated_outline` +5.2%, "
                        "`multi_shot` +5.1%, `single_character` +2.6%) and is "
                        "`contrary` on `promote_demo` at -34.8%. That inversion "
                        "is REAL, not the +0.0001 nothing `disabled_aa` produces "
                        "on the same scene: the SVG sprite rasterises AT 2x "
                        "instead of being stretched up from a 1x texture, so the "
                        "descriptor path is the one scene this lever reaches "
                        "hardest and the AA lever cannot reach at all. The two "
                        "render levers reach complementary scenes. `increase` is "
                        "declared because the criterion is evaluated PER SCENE "
                        "and met on at least one — the same shape "
                        "`video_stream_bytes` already carries under "
                        "`disabled_aa`. An increase here is not a regression: the "
                        "optimum is interior, and this walks TOWARD a measured "
                        "ceiling (2.5846 `saturated_outline`, 2.9167 "
                        "`graded_field`, ~2.452 `multi_shot`, ~2.140 "
                        "`single_character`, ~3.503 `promote_demo`). `aa_probe` "
                        "has NO ceiling — its diagonals land the block-mean grid "
                        "differently at every k, so it oscillates +/-5-8% with no "
                        "settling — and gets no declared target, because a value "
                        "nobody measured is not a value (research §3a)."
                    ),
                    reference="2.3685 -> 2.4921 on `saturated_outline`; 5.6368 -> 3.6775 on `promote_demo`",
                ),
            },
        ),
        _spec(
            key="off_palette_pixel_fraction",
            family="A",
            unit="fraction",
            sentence=(
                "The fraction of the frame whose pixels are not exactly one of "
                "the colours the compiler declared for this shot."
            ),
            optimum=Optimum(
                kind="one_sided",
                expect="minimize",
                note=(
                    "Honest caveat: a blur, drop shadow, gradient or sub-pixel "
                    "offset moves this UP, indistinguishably from better AA. It "
                    "is a change detector on that axis. Its floor is "
                    "scene-dependent and is NOT zero on the SVG-sprite path."
                ),
            ),
            predictions={
                "high_crf": Prediction(
                    "not_applicable",
                    reason="pre-encode. NOT MEASURED — which is not the same as 'no change'",
                ),
                "disabled_aa": Prediction(
                    "decrease",
                    reason="family A already supplies edge_transition_width as its witness",
                ),
                "supersample": Prediction(
                    "increase",
                    reason=(
                        "family A already supplies `edge_transition_width` as its "
                        "witness — count at most one per family. `increase` and "
                        "NOT `not_applicable`: this is a render lever and the "
                        "metric is render-side, so `not_applicable` would make "
                        "every scene report `unexpected_movement` for a metric "
                        "doing exactly what it should. An exact block-mean "
                        "resolve replaces hard edge pixels with blends, and a "
                        "blend is off-palette by definition. MEASURED on the "
                        "corpus and scene-dependent, like everything else this "
                        "lever touches: `single_character` +28.9%, `multi_shot` "
                        "+24.2%, `aa_probe` +21.8%, `graded_field` +0.0%; "
                        "contrary on the two scenes whose k=1 frames are already "
                        "the most off-palette, `promote_demo` -22.8% and "
                        "`saturated_outline` -9.9%, where an exact resolve "
                        "REPLACES many one-off MSAA blends with fewer, more "
                        "regular ones. It counts nothing either way. And the "
                        "optimum says `minimize` while this improvement moves it "
                        "UP, which is the metric's own caveat stated in its "
                        "`note`: on this axis it is a change detector, not a "
                        "quality dial."
                    ),
                ),
            },
        ),
        _spec(
            key="frame_distinct_colours",
            family="A",
            unit="count",
            sentence="How many different colours are in the frame at all.",
            optimum=Optimum(
                kind="guard",
                note="No predicted direction on AA changes. A guard, not a dial.",
            ),
            predictions={
                "high_crf": Prediction("not_applicable", reason="pre-encode"),
                "disabled_aa": Prediction(
                    "decrease",
                    reason="do not count it alongside off_palette_pixel_fraction; same family",
                ),
                "supersample": Prediction(
                    "increase",
                    reason=(
                        "do not count it alongside `edge_transition_width`; same "
                        "family. SCENE-DEPENDENT and measured (research §4): "
                        "+197% `single_character`, +186% `aa_probe`, +184% "
                        "`multi_shot`, +10% `graded_field` — and DOWN 18% on "
                        "`saturated_outline` and 23% on `promote_demo`. On a "
                        "scene that already carries a lot of colour, 1x MSAA "
                        "emits many one-off blend values and an exact 2x resolve "
                        "replaces them with fewer, more regular ones: the picture "
                        "gets BETTER and the count goes DOWN. This is what "
                        "refutes epic #9's 'edge distinct-colour count materially "
                        "up on every corpus scene' — the statistic has a "
                        "scene-dependent sign, which is not a magnitude problem "
                        "and cannot be fixed by rendering harder. (The metric "
                        "that done-when literally names, `edge_distinct_colours`, "
                        "does not exist: refuted and deleted in Wave 2.) Read as "
                        "a PAIR with `edge_transition_width` — colours up + width "
                        "up slightly = gradation added; colours +3600% + width "
                        "+45% = lanczos ringing; colours down + width down = "
                        "sharpened. an#41's criterion counts metrics "
                        "INDEPENDENTLY and cannot express that conjunction, so "
                        "the pair is evidence for a human reader and is NOT a "
                        "gate."
                    ),
                    reference="7.6 -> 21.7 on `aa_probe`; 478.2 -> 392.3 on `saturated_outline`",
                ),
            },
        ),
        _spec(
            key="edge_masked_distinct_colours",
            family="A",
            unit="count",
            sentence=(
                "How many different colours sit ON the edges — the row above, "
                "restricted to the edge mask."
            ),
            optimum=Optimum(
                kind="guard",
                note=(
                    "A guard, not a dial, for the exact reason Wave 2 deleted "
                    "the ONE-SIDED `edge_distinct_colours`: a 3x3 blur raised "
                    "that 9.2x and +/-3-LSB noise 55x, and BOTH degradations "
                    "read as 'AA restored'. What the mask buys is narrower than "
                    "an#55 assumed, and the difference was MEASURED before this "
                    "row shipped: an INTERIOR-ONLY change cannot reach the "
                    "number, but a whole-frame blur can, because the mask is "
                    "recomputed from the frame being measured and a blur widens "
                    "the edge band. 3x3 box blur on the six committed goldens, "
                    "ratio against k=1, whole-frame vs edge-masked: aa_probe "
                    "10.25x/9.50x, graded_field 2.04x/2.35x, multi_shot "
                    "7.92x/4.63x, promote_demo 1.25x/0.83x, saturated_outline "
                    "1.49x/1.14x, single_character 8.60x/5.70x — damped on four "
                    "of six, WORSE on one, blind on none. So the metric that "
                    "separates a blur from a supersample is "
                    "`edge_transition_width` (2.1x-3.4x under the same blur "
                    "against +2.6% to +8.0% under an exact k=2 resolve), and "
                    "this row is the second half of that reading, not the first. "
                    "**The pair is evidence for a human reader and NOT a gate** "
                    "— an#41's criterion counts metrics independently and cannot "
                    "express a conjunction, so nothing here may be read as one. "
                    "No figure for THIS metric under a lever exists yet, which "
                    "is why `reference` is unset rather than borrowed from "
                    "`frame_distinct_colours` (an#55)."
                ),
            ),
            predictions={
                "high_crf": Prediction(
                    "not_applicable",
                    reason=(
                        "pre-encode, and blind to the encoder BY CONSTRUCTION "
                        "like every family A row: both the mask and the colours "
                        "come from the source PNG, which sits upstream of the "
                        "encoder"
                    ),
                ),
                "disabled_aa": Prediction(
                    "decrease",
                    reason=(
                        "the same direction as `frame_distinct_colours`, "
                        "deliberately: for flat cutout art essentially all "
                        "colour variety lives at edges, so the mask is close to "
                        "a no-op for the COUNT (wave2 research §1.3 measured the "
                        "two within 6%). The whole-frame count under the lever "
                        "on `aa_probe` goes 7.6 -> 3.0 at k=1 and 21.7 -> 6.9 at "
                        "k=2 (wave3 research §5) — those are `frame_distinct_"
                        "colours` numbers, NOT this metric's, and `reference` is "
                        "deliberately left unset until a real row measures THIS "
                        "one. NOT counted: family A spends its one witness on "
                        "`edge_transition_width` and this is the same family. "
                        "NOT gated either, and the distinction is the one most "
                        "likely to be flipped by a later reviewer: the mask does "
                        "move with the lever, but the mask and the number come "
                        "from the SAME frames, so there is no reference to move. "
                        "That is what makes `flat_field_deviation` further down "
                        "gated and this one not."
                    ),
                ),
                "supersample": Prediction(
                    "increase",
                    reason=(
                        "MEASURED under the lever before it was declared, on all "
                        "six scenes, because this metric's whole-frame sibling "
                        "has a scene-dependent sign and there was no reason to "
                        "assume the masked one would not. Up on four: `aa_probe` "
                        "+184.6%, `single_character` +179.3%, `multi_shot` "
                        "+146.3%, `graded_field` +13.6%. Down on the two "
                        "colour-rich scenes: `saturated_outline` -12.0%, "
                        "`promote_demo` -7.6% — where 1x MSAA already emits many "
                        "one-off blend values and an exact 2x resolve replaces "
                        "them with fewer, more regular ones, so the picture gets "
                        "better and the count goes down. `increase` is declared "
                        "because the criterion is evaluated PER SCENE and met on "
                        "four. NOT counted: family A spends its one witness on "
                        "`edge_transition_width` and this is the same family — "
                        "read the two together (aa_probe +184.6% colours with "
                        "+7.4% width is gradation added; the same 3x3 blur that "
                        "raises this metric 9.5x doubles the width) and the pair "
                        "separates gradation from softening. It is evidence for "
                        "a human, never a gate: an#41's criterion counts metrics "
                        "independently and cannot express a conjunction."
                    ),
                    reference=(
                        "7.58 -> 21.58 on `aa_probe`; 422.25 -> 371.75 on "
                        "`saturated_outline`"
                    ),
                ),
            },
            notes=(
                "Its mask is NOT the `masks.edge` block the encode-side rows "
                "use. That one is ffmpeg's limited-range Y; this one is "
                "full-range BT.709 luma from the source RGB, so at the shared "
                "threshold of 40 it is the wider mask. The row records it "
                "separately as `masks.render_edge`, with its own operator.",
            ),
        ),
        _spec(
            key="coded_luma_edge_error",
            reference="lossless",
            family="C",
            unit="code values (8-bit Y)",
            sentence=(
                "How much the encoder's quantiser roughens the brightness step "
                "at a line's edge, measured on the codec's own luma plane."
            ),
            optimum=Optimum(kind="one_sided", expect="minimize"),
            predictions={
                "high_crf": Prediction(
                    "increase",
                    counts=True,
                    reason="family C's witness",
                    reference="0.419 (qp0) -> 1.875 (crf23) -> 41.2 (crf51)",
                ),
                "disabled_aa": Prediction(
                    None,
                    gate=_GATED_REFERENCE_MOVED,
                    reason="the source PNG moves with the mutation, so the delta is uninterpretable",
                ),
                "supersample": Prediction(
                    None,
                    gate=_GATED_REFERENCE_MOVED,
                    reason=(
                        "same gate, same mechanism as `disabled_aa`: the source "
                        "PNG moves with the mutation. A render lever of EITHER "
                        "sign disqualifies it — and this one is the reason to say "
                        "so out loud, because a softer source shrinks the edge "
                        "mask to its easiest members and the number improves "
                        "MECHANICALLY. Ungated it is one of the 7 unearned "
                        "improvements a `mutation=None` diff reports for this "
                        "change (an#56)."
                    ),
                ),
            },
            notes=(
                "Read the coded luma plane; NEVER recompute Y from decoded RGB. "
                "The RGB round trip clips at saturated edges, and ~83% of the "
                "broken form's baseline was chroma leakage.",
            ),
        ),
        _spec(
            key="chroma_edge_dY",
            family="C",
            role="control",
            reference="source_png",
            unit="code values (8-bit Y)",
            sentence=(
                "The luma error on the SAME mask and the SAME reference as "
                "`chroma_edge_dCr` — a control, not a quality metric."
            ),
            optimum=Optimum(
                kind="guard",
                note=(
                    "Read only as the ratio's denominator. NOT a second name for "
                    "`coded_luma_edge_error`: that one references the lossless "
                    "leg and this one references the PNG conversion, so they "
                    "differ by exactly the conversion term. They were identical "
                    "(1.484149) while both referenced the PNG conversion, which "
                    "is why one of them was removed and then restored when the "
                    "references diverged."
                ),
            ),
            predictions={
                "high_crf": Prediction(
                    "increase", reason="control for the ratio below"
                ),
                "disabled_aa": Prediction(None, gate=_GATED_REFERENCE_MOVED),
                "supersample": Prediction(None, gate=_GATED_REFERENCE_MOVED),
            },
        ),
        _spec(
            key="chroma_edge_dCr",
            reference="source_png",
            family="C",
            unit="code values (8-bit Cr)",
            sentence=(
                "How much the colour shifts on the pixels straddling a hard "
                "outline once the video is encoded."
            ),
            optimum=Optimum(kind="one_sided", expect="minimize"),
            predictions={
                "high_crf": Prediction(
                    "increase",
                    reason=(
                        "family C counts as ONE family until the correlation with "
                        "coded_luma_edge_error is measured across the encoder matrix"
                    ),
                ),
                "disabled_aa": Prediction(
                    None,
                    gate=_GATED_REFERENCE_MOVED,
                    reason=(
                        "measured -0.7% on a faithful AA-off simulation; the "
                        "claimed +10.7% was refuted. Predict no render-side direction."
                    ),
                ),
                "supersample": Prediction(
                    None,
                    gate=_GATED_REFERENCE_MOVED,
                    reason=(
                        "its subject IS the 4:2:0 subsampling of a conversion "
                        "whose input this lever changes, so there is no fixed "
                        "reference. Predict no render-side direction, exactly as "
                        "for `disabled_aa`."
                    ),
                ),
            },
        ),
        _spec(
            key="chroma_edge_dCr_over_dY",
            reference="source_png",
            family="C",
            role="diagnostic",
            unit="ratio",
            sentence="dCr/dY on the edge mask: large means real chroma bleed, ~1 means generic blocking.",
            optimum=Optimum(
                kind="guard",
                note=(
                    "3.3 at qp0 yuv420p is real 4:2:0 bleed; 0.96 at crf51 is "
                    "generic damage wearing the metric's name. The denominator "
                    "IS `coded_luma_edge_error` — the research's `chroma_edge_dY` "
                    "control is mean |dY| over the edge mask, which is that "
                    "metric's definition verbatim. Measured identical (1.484149 "
                    "for both on the first real row), so it is recorded once "
                    "rather than twice: one signal under two names is exactly "
                    "how a witness count is padded dishonestly."
                ),
            ),
            predictions={
                "high_crf": Prediction(
                    "decrease",
                    reason="the chroma claim collapses as damage becomes generic",
                ),
                "disabled_aa": Prediction(None, gate=_GATED_REFERENCE_MOVED),
                "supersample": Prediction(None, gate=_GATED_REFERENCE_MOVED),
            },
        ),
        _spec(
            key="flat_field_deviation",
            reference="lossless",
            family="D",
            unit="fraction of flat px with |d|>6",
            sentence=(
                "Of the pixels the renderer painted inside a large flat colour "
                "field, what fraction came back more than 6 code values off."
            ),
            optimum=Optimum(kind="one_sided", expect="minimize"),
            predictions={
                "high_crf": Prediction(
                    "increase",
                    counts=True,
                    reason="family D's witness; monotone over a 133x span",
                    reference="0.0003 / 0.0005 / 0.0035 / 0.0127 / 0.0399 (crf18->51)",
                ),
                "disabled_aa": Prediction(
                    None,
                    gate=_GATED_SOURCE_HASH,
                    reason=(
                        "The research measured this flat across a SIMULATED AA "
                        "matrix and called the orthogonality 'the metric's whole "
                        "value'. Run against the real MSAA lever it moves on ALL "
                        "SIX corpus scenes, in both directions (an#41). The "
                        "mechanism is structural rather than surprising: the flat "
                        "mask is derived from the SOURCE frames, which this lever "
                        "changes, so the mask itself moves and the comparison has "
                        "no fixed reference. That is the definition of gated — "
                        "uninterpretable, not good or bad — and it is the same "
                        "gate `encode_flicker_on_held_pixels` already carries for "
                        "every renderer mutation, for the same reason."
                    ),
                ),
                "supersample": Prediction(
                    None,
                    gate=_GATED_SOURCE_HASH,
                    reason=(
                        "the flat mask is derived from the SOURCE frames, which "
                        "this lever changes, so the mask itself moves and the "
                        "comparison has no fixed reference — structurally "
                        "identical to `disabled_aa`, and the DIRECTION of the "
                        "render change is irrelevant to it. Note the gate is "
                        "`source_hash_differs` and NOT `reference_moved`: an#56 "
                        "describes every C/D/E/G row as `reference_moved`, which "
                        "is right for 5 of the 9 and wrong for this one, "
                        "`flat_field_p99_dev`, `encode_flicker_on_held_pixels` "
                        "and `encode_ringing_excess`. The two gates are recorded "
                        "separately on purpose: one says the reference moved, "
                        "this one says the MASK moved."
                    ),
                ),
            },
            notes=(
                "Covers the ~90% of the frame no edge metric touches. Banding "
                "regressions are invisible without it.",
            ),
        ),
        _spec(
            key="flat_field_p99_dev",
            reference="lossless",
            family="D",
            role="companion",
            unit="code values",
            sentence="The 99th percentile of the same deviation, in human units.",
            optimum=Optimum(kind="one_sided", expect="minimize"),
            predictions={
                "high_crf": Prediction(
                    "increase", reason="companion to the rate above"
                ),
                "disabled_aa": Prediction(
                    None,
                    gate=_GATED_SOURCE_HASH,
                    reason=(
                        "gated for the same structural reason as the rate above: "
                        "the flat mask moves with the source. Measured moving on "
                        "one of six scenes and holding on five, which is what a "
                        "metric with no fixed reference looks like — not evidence "
                        "of orthogonality (an#41)."
                    ),
                ),
                "supersample": Prediction(
                    None,
                    gate=_GATED_SOURCE_HASH,
                    reason=(
                        "gated for the same structural reason as the rate above: "
                        "the flat mask moves with the source, under a render "
                        "lever of either sign."
                    ),
                ),
            },
        ),
        _spec(
            key="encode_flicker_on_held_pixels",
            reference="lossless",
            family="E",
            unit="fraction of held px moving >=2",
            sentence=(
                "The fraction of pixels the animator held perfectly still that "
                "moved by at least 2 code values in the delivered video."
            ),
            optimum=Optimum(kind="one_sided", expect="minimize"),
            predictions={
                "high_crf": Prediction(
                    "increase",
                    counts=True,
                    reason=(
                        "family E's witness — and the least reliable of the four. "
                        "It is NON-MONOTONE across the CRF ladder on the real "
                        "corpus (0.000648 / 0.007018 / 0.000985 / 0.000916 / "
                        "0.001137 / 0.001685 over crf 18/23/28/33/40/51 on "
                        "`single_character`, peaking at crf23), because at high "
                        "CRF the whole frame flattens into large uniform skip "
                        "regions and held pixels stop moving — the same mechanism "
                        "the `disabled_aa` gate below documents. At the crf23 -> "
                        "crf40 step the lever uses it holds on five of six scenes "
                        "and inverts on `single_character`, so it is kept and not "
                        "leaned on: C, D and F are monotone across the whole "
                        "ladder and satisfy the criterion on all six scenes "
                        "without it (an#41)."
                    ),
                    reference="0.0113 -> 0.0243 on aa_probe; 0.0070 -> 0.0011 on single_character",
                ),
                "disabled_aa": Prediction(
                    None,
                    gate=_GATED_SOURCE_HASH,
                    reason=(
                        "excluded from EVERY renderer mutation's witness count: "
                        "without the source gate, half-res-then-nearest-upscale — "
                        "the most visible possible flat-art regression — reports a "
                        "7.1x IMPROVEMENT, because a flattened render gives x264 "
                        "large uniform skip regions."
                    ),
                ),
                "supersample": Prediction(
                    None,
                    gate=_GATED_SOURCE_HASH,
                    reason=(
                        "'excluded from EVERY renderer mutation's witness count' "
                        "is what the declaration beside `disabled_aa` says, and "
                        "this is the second renderer mutation it was written for. "
                        "Worth restating because THIS lever is the exact shape of "
                        "the failure that gate exists for: without it, "
                        "half-res-then-nearest-upscale — the most visible "
                        "possible flat-art regression — reports a 7.1x "
                        "improvement, because a flattened render gives x264 large "
                        "uniform skip regions. A block-mean resolve flattens the "
                        "source in the same direction, so ungated this metric "
                        "would reward the improvement for the identical wrong "
                        "reason and nobody could tell the two apart."
                    ),
                ),
            },
        ),
        _spec(
            key="encode_ringing_excess",
            reference="source_png",
            family="G",
            provisional=True,
            unit="code values",
            sentence=(
                "How much more the encoder overshoots around outlines than a "
                "mathematically lossless encode of the same frames does."
            ),
            optimum=Optimum(kind="one_sided", expect="minimize"),
            predictions={
                "high_crf": Prediction(
                    "increase",
                    reason="provisional; kept out of the witness count until open question 4 is settled",
                ),
                "disabled_aa": Prediction(
                    None,
                    gate=_GATED_SOURCE_HASH,
                    reason=(
                        "declared 'both legs rise together by construction'; "
                        "measured moving on ALL SIX corpus scenes under the real "
                        "MSAA lever (an#41). The cancellation is exact only when "
                        "both legs share a FIXED source, and a renderer mutation "
                        "moves the source — so what is left is uninterpretable "
                        "rather than zero."
                    ),
                ),
                "supersample": Prediction(
                    None,
                    gate=_GATED_SOURCE_HASH,
                    reason=(
                        "the cancellation is exact only when both legs share a "
                        "FIXED source; this lever moves it. Measured moving on "
                        "all six scenes under `disabled_aa`, and nothing about "
                        "the sign of the render change makes it hold still."
                    ),
                ),
            },
            notes=(
                "Provisional pending a cheap comparison against plain edge-band "
                "MAE over the identical mask. `edge_band_mae` is recorded beside "
                "it so the first ledger row answers that question.",
            ),
        ),
        _spec(
            key="ring_band_mae",
            reference="source_png",
            family="G",
            role="q4_comparator",
            unit="code values",
            sentence=(
                "Plain mean absolute luma error over the RING band — the simpler "
                "rival to `encode_ringing_excess`, on the same mask and with no "
                "second encode."
            ),
            optimum=Optimum(
                kind="one_sided",
                expect="minimize",
                note=(
                    "Research §1.8 calls this 'plain edge-band MAE over the "
                    "identical mask'. Named for the mask it actually uses, "
                    "because MAE over the EDGE mask is `coded_luma_edge_error`'s "
                    "definition verbatim — measured identical on the first real "
                    "row — and recording it twice would answer open question 4 "
                    "with a tautology."
                ),
            ),
            predictions={
                "high_crf": Prediction("increase", reason="the comparison arm"),
                "disabled_aa": Prediction(None, gate=_GATED_REFERENCE_MOVED),
                "supersample": Prediction(None, gate=_GATED_REFERENCE_MOVED),
            },
        ),
        _spec(
            key="min_ssim_win8_vs_golden",
            family="B",
            role="diagnostic",
            unit="ssim",
            sentence=(
                "The worst small window in the frame, scored against the "
                "committed golden — magnitude AND location of a golden failure."
            ),
            optimum=Optimum(
                kind="one_sided",
                expect="maximize",
                note=(
                    "SIGN AMBIGUITY, recorded deliberately: a supersampling "
                    "IMPROVEMENT moves this away from 1.0 exactly as an AA "
                    "regression does, and in changed-pixel terms reports the "
                    "improvement as the LARGER change. It is a change detector, "
                    "and must be paired with the no-reference family to say "
                    "whether a change was good."
                ),
            ),
            predictions={
                "high_crf": Prediction(
                    "not_applicable",
                    reason="the golden corpus is UPSTREAM of the encoder; no encode change can reach it",
                ),
                "disabled_aa": Prediction(
                    "decrease",
                    counts=True,
                    reason=(
                        "family B's single witness. `golden_identity` is the "
                        "tripwire beside it and counts ZERO — one family, at most "
                        "one witness, and a boolean change detector is not it."
                    ),
                    reference="0.9999 -> 0.279 at 1080p / 0.063 at native for a total eye-blink",
                ),
                "supersample": Prediction(
                    "decrease",
                    counts=True,
                    reason=(
                        "family B's single witness, and THIS lever is what the "
                        "SIGN AMBIGUITY note above was written about: a "
                        "supersampling IMPROVEMENT moves this away from 1.0 "
                        "exactly as an AA regression does, and reports the "
                        "improvement as the LARGER change. It counts as evidence "
                        "the render CHANGED, which is what family B measures; it "
                        "is NOT evidence the change was good, and the note beside "
                        "`optimum` is the standing disclaimer that must be read "
                        "with it. Fires on all six scenes: the committed baseline "
                        "is 1.0 on every one, and research §2/§4 measured a real "
                        "pixel move on every one — including `promote_demo`, "
                        "which `disabled_aa` cannot reach at all. The magnitude "
                        "is the lever's own, from the exam run recorded in the "
                        "an#56 PR body — not a figure lifted from a research "
                        "table, which is the an#41-review defect this panel "
                        "already caught once."
                    ),
                    reference=(
                        "1.0 -> 0.4380 on `single_character`, 0.4449 on "
                        "`promote_demo`, 0.6144 on `saturated_outline`, 0.7756 on "
                        "`aa_probe`, 0.7969 on `multi_shot`, 0.8201 on "
                        "`graded_field` — every scene, because every scene's "
                        "pixels move"
                    ),
                ),
            },
            notes=(
                "The metrics survey concluded SSIM should be excluded because "
                "whole-frame SSIM scores a total eye-blink at 0.9989. That was "
                "REFUTED: only the global-moment reduction is blind. Killing "
                "SSIM outright would have discarded the best numpy-only detector "
                "available.",
            ),
        ),
        _spec(
            key="stage_min_plane_ratio_gap",
            family="B",
            requires="a multiplane stage: two or more colour-filled planes",
            role="diagnostic",
            unit="ratio",
            sentence=(
                "The smallest gap between any two planes' displacement RATIOS "
                "on a panning multiplane stage (an#111). Each plane's centroid "
                "displacement is divided by the reference plane's, so a stage "
                "that parallaxes gives the declared depths back — on "
                "`stage_pan`, 0.25 / 1.0 / 2.0 — and a stage that flattened "
                "gives every plane 1.0 and this row zero. `unavailable`, never "
                "zero, for a scene with fewer than two colour-filled planes: "
                "a scene with nothing to compare has no ratio, and reporting "
                "that as flattened would fire on every other fixture."
            ),
            optimum=Optimum(
                kind="guard",
                note=(
                    "Not a quality dial: a larger gap is not a better picture, "
                    "it is a fixture whose depths are further apart. It exists "
                    "so a regression that flattens the parallax moves a ledger "
                    "number instead of waiting for someone to look at a GIF."
                ),
            ),
            predictions={
                "high_crf": Prediction(
                    "not_applicable",
                    reason="computed on the pre-encode PNGs; no encode change can reach it",
                ),
                "disabled_aa": Prediction(
                    None,
                    gate=(
                        "AA-off changes which pixels carry a plane's exact colour "
                        "along its edges, so a centroid can shift by a fraction of "
                        "a pixel and the mask COUNT can change — which the "
                        "measurement refuses outright as a clipped plane. Gated, "
                        "not predicted: the outcome is 'the instrument declines', "
                        "which is neither better nor worse."
                    ),
                ),
                "supersample": Prediction(
                    None,
                    gate=(
                        "same as disabled_aa: an edge-quality lever moves the exact-"
                        "colour mask's boundary, and this measurement is defined on "
                        "exact colours"
                    ),
                ),
            },
            notes=(
                "The trap this metric is shaped around: today's centre-anchored "
                "zoom ALREADY gives unequal per-plane displacements, so 'the "
                "planes moved at different rates' is satisfied by a scene with no "
                "parallax at all. The JSON half of the measurement probes at "
                "scene-space x = 0, where the zoom term cancels exactly; the pixel "
                "half cannot (a centroid sits at the plane's own offset), so the "
                "`stage_pan` fixture holds zoom CONSTANT instead.",
                "Ratios are taken against the LARGEST mover, always. The pixel "
                "half cannot see a `depth`, so a depth-aware reference makes the "
                "two halves report different numbers for one stage — measured "
                "while this landed: 0.75 against the `depth == 1` plane and 0.375 "
                "against the largest mover, for the same measurement.",
                "Measured at the first bless: 0.375, the far/mid gap on depths "
                "0.25 / 1.0 / 2.0 (reported as ratios 0.125 / 0.5 / 1.0). The "
                "`stage_planes_parallaxed` tripwire's floor is half of it, which "
                "is the `expression_min_pairwise_changed_px` precedent followed "
                "literally.",
            ),
        ),
        _spec(
            key="stage_planes_parallaxed",
            family="B",
            requires="a multiplane stage: two or more colour-filled planes",
            role="tripwire",
            unit="boolean",
            sentence=(
                "True when the planes moved at DIFFERENT rates — i.e. the stage "
                "parallaxed rather than panning as one rigid image. The same "
                "measurement as `stage_min_plane_ratio_gap`, read as a verdict: "
                "a boolean and the number beside it must be the same evidence, "
                "or a reader has to reconcile them."
            ),
            optimum=Optimum(
                kind="guard",
                note="A change detector. Counts zero toward any criterion.",
            ),
            predictions={
                "high_crf": Prediction(
                    "not_applicable",
                    reason="computed on the pre-encode PNGs",
                ),
                "disabled_aa": Prediction(None, gate="see stage_min_plane_ratio_gap"),
                "supersample": Prediction(None, gate="see stage_min_plane_ratio_gap"),
            },
            notes=(
                "There is deliberately NO `flat_camera` mutation lever to prove "
                "this fires. A compile-time parallax change moves the scene's "
                "contract hash, so `bench-compare` refuses the row at "
                "comparability before any family is examined — the recorded "
                "`step_hz` verdict, verbatim. The proof belongs in "
                "`an bench-mutants` as a declared guard mutant.",
            ),
        ),
        _spec(
            key="expression_min_pairwise_changed_px",
            family="B",
            role="diagnostic",
            unit="pixels",
            sentence=(
                "The smallest number of pixels by which any two of the scene's "
                "pinned frames differ in TODAY'S render — on `expressions`, the "
                "closest pair of presets; a collapse of two emotions onto one "
                "face drives it to zero (an#98). On a two-frame scene it is that "
                "pair's own change, measured rather than withheld: a render-side "
                "row is never null on a real capture."
            ),
            optimum=Optimum(
                kind="guard",
                note=(
                    "Not a quality dial: larger is not better beyond 'the presets "
                    "are apart'. It exists so a regression that makes two "
                    "expressions render alike moves a ledger number instead of "
                    "waiting for someone to look."
                ),
            ),
            predictions={
                "high_crf": Prediction(
                    "not_applicable",
                    reason="computed on the pre-encode PNGs; no encode change can reach it",
                ),
                "disabled_aa": Prediction(
                    None,
                    gate=(
                        "AA-off moves every edge on every pinned frame, so the count "
                        "of differing pixels between two frames moves with it in an "
                        "undeclared direction — measured on the lane: it MOVED under "
                        "`supersample` when first declared `not_applicable`. Gated, "
                        "not predicted: the delta is uninterpretable, not good or bad. "
                        "Family B's witness is `min_ssim_win8_vs_golden`."
                    ),
                ),
                "supersample": Prediction(
                    None,
                    gate=(
                        "same as disabled_aa: an edge-quality lever softens every "
                        "edge on every pinned frame and the pairwise count moves "
                        "with it; not a face-solver lever, no declared sign"
                    ),
                ),
            },
            notes=(
                "Measured at the first bless (an#98): 106 px between `thinking` and "
                "`skeptical`, the two asymmetric presets, and 384 px at the far end. "
                "`tests/test_expression_goldens.py` pins half the minimum on the "
                "COMMITTED goldens; this row reports the same quantity on the live "
                "render, so the ledger sees it before a re-bless does.",
            ),
        ),
        _spec(
            key="video_stream_bytes",
            family="F",
            unreviewed=True,
            unit="bytes",
            sentence="How large the encoded video stream is, with the audio track excluded.",
            optimum=Optimum(
                kind="guard",
                note="Not a quality dial in either direction — a cross-check that the metrics read the file they think they do.",
            ),
            predictions={
                "high_crf": Prediction(
                    "decrease",
                    counts=True,
                    reason="free fourth cross-check",
                    # MEASURED on this corpus at the lever's crf23 -> crf40 step:
                    # x1.18 to x1.77 smaller. The whole ladder (crf 18..51 on
                    # `single_character`: 3808/3644/2903/2463/2108/1843 bytes)
                    # tops out at x2.07, so 8x is unreachable at ANY rung. It was
                    # a research-table figure copied into a field this repo reads
                    # as a measurement of THIS pipeline (an#41 review).
                    reference="x1.18 to x1.77 at crf23 -> crf40; x2.07 across the whole ladder",
                ),
                "disabled_aa": Prediction(
                    "increase",
                    counts=True,
                    reason=(
                        "hard edges cost more to code. THE WEAKEST of this "
                        "mutation's three witnesses, the only unreviewed metric in "
                        "the set — and SCENE-DEPENDENT, measured. It holds where "
                        "the lever has non-axis-aligned edges to change (aa_probe "
                        "+6.6%, multi_shot +9.7%, saturated_outline +6.9%) and "
                        "INVERTS where it does not (single_character -6.1%, "
                        "graded_field -5.7%, promote_demo -0.1%), because AA-off "
                        "on axis-aligned art removes intermediate colours and the "
                        "picture gets CHEAPER instead of harder. So this "
                        "mutation's criterion is met per scene, on the scenes the "
                        "lever can reach — which is the measured reason `aa_probe` "
                        "is in the corpus at all (an#38, an#41)."
                    ),
                    reference="+6.6% / -6.1%, scene-dependent",
                ),
                "supersample": Prediction(
                    "decrease",
                    counts=True,
                    reason=(
                        "family F's witness, and the ONLY third family available "
                        "to a render lever: A and B can count, C/D/E/G are all "
                        "gated because their masks and references derive from the "
                        "source frames, and F is the one encode-side family whose "
                        "reference is `none` — a property of the encoded file "
                        "rather than a comparison against a moving source. So "
                        "an#41's three-family criterion for this lever is A + B + "
                        "F, forced, with no substitute. MEASURED before it was "
                        "declared, because two mechanisms fight here and guessing "
                        "was not available: a block-mean resolve ADDS distinct "
                        "values at edges (+184-197% on the three colour-poor "
                        "scenes) and it LOWERS edge frequency, which is cheaper "
                        "for the DCT. The second wins where there is geometry to "
                        "smooth. Scene-dependent, the same shape this row already "
                        "carries under `disabled_aa` — and `decrease` is declared "
                        "because that is the sign that holds on the scenes where "
                        "family A also holds, and because it is the side carrying "
                        "the magnitude: the three down-moves are -4.0% to -12.2% "
                        "and the three up-moves are +0.8% to +2.8%."
                    ),
                    reference=(
                        "single_character -12.2%, multi_shot -6.6%, "
                        "saturated_outline -4.0%; contrary and small on "
                        "graded_field +2.8%, promote_demo +1.1%, aa_probe +0.8%"
                    ),
                ),
            },
            notes=(
                "The one metric in the panel that went through NO adversarial pass.",
            ),
        ),
        _spec(
            key="file_bytes",
            family="F",
            role="companion",
            unit="bytes",
            sentence="The whole mp4 on disk, audio track included.",
            optimum=Optimum(
                kind="guard",
                note=(
                    "Contaminated by the audio cache: the renderer always emits an "
                    "AAC track, silent or not. Prefer video_stream_bytes."
                ),
            ),
            predictions={
                "high_crf": Prediction("decrease"),
                "disabled_aa": Prediction(
                    "increase",
                    reason=(
                        "companion to `video_stream_bytes`, SCENE-DEPENDENT in "
                        "the same way, and additionally contaminated by the "
                        "audio track. Measured AA-on -> off across the corpus: "
                        "+3.6% / -2.7% / +7.0% / -0.0% / +4.0% / -2.9%, so it is "
                        "`contrary` on half of it. Counts nothing; kept only as "
                        "the companion the sentence above calls it (an#41 review)."
                    ),
                ),
                "supersample": Prediction(
                    "decrease",
                    reason=(
                        "companion to `video_stream_bytes`, contaminated by the "
                        "AAC track the renderer always emits, silent or not, and "
                        "therefore scene-dependent in the same way and then some. "
                        "MEASURED alongside it rather than assumed to follow: it "
                        "does follow, on every scene and with the same three-three "
                        "split — `single_character` -5.9%, `multi_shot` -4.8%, "
                        "`saturated_outline` -2.4%; up and small on `promote_demo` "
                        "+0.6%, `aa_probe` +0.5%, `graded_field` +0.4%. Counts "
                        "nothing — prefer `video_stream_bytes`, as its own "
                        "`optimum` note says."
                    ),
                ),
            },
        ),
    ]
}

#: Tripwires. A separate block from `METRICS` because they count ZERO toward
#: any criterion — they fire on improvements and regressions alike.
TRIPWIRES: dict[str, MetricSpec] = {
    m.key: m
    for m in [
        _spec(
            key="golden_identity",
            family="B",
            tripwire=True,
            unit="boolean",
            sentence="Today's frame is pixel-for-pixel the committed golden frame.",
            optimum=Optimum(kind="guard", note="Full-frame, never edge-masked."),
            predictions={
                "high_crf": Prediction(
                    "not_applicable",
                    reason="the corpus is UPSTREAM of the encoder; no encode change can reach it",
                ),
                "disabled_aa": Prediction(
                    "decrease",
                    reason=(
                        "it FAILS — a change detector firing, not a quality "
                        "measurement, which is why it counts ZERO. Spelled "
                        "`decrease` (True -> False) rather than `no_change`: the "
                        "prediction was `no_change` while this very sentence said "
                        "it fails, so the row reported `unexpected_movement` on "
                        "every scene for a tripwire doing exactly its job. Found "
                        "by `an bench-compare` (an#41)."
                    ),
                ),
                "supersample": Prediction(
                    "decrease",
                    reason=(
                        "it FAILS — `True -> False` — on all six scenes, because "
                        "a block-mean resolve changes pixels on all six and the "
                        "committed baseline is `True` on all six. A change "
                        "detector firing, not a quality measurement, which is why "
                        "it counts ZERO — and under THIS lever that distinction "
                        "is the whole point: the tripwire fires identically for "
                        "an improvement and for a regression, so it can never be "
                        "the evidence that a supersample was worth shipping. "
                        "Spelled `decrease` rather than `no_change` for the "
                        "reason recorded beside `disabled_aa`."
                    ),
                ),
            },
            notes=(
                "Compare sha256 of DECODED pixels, never file bytes: Chromium "
                "1187 -> 1223 changes 144/144 PNG files and zero pixels.",
            ),
        ),
    ]
}


#: The two blocks must not share a key. Enforced at import rather than left to
#: the ledger builder, because a key that both counts and counts zero is a
#: criterion nobody can evaluate — and the ledger's own overlap check is
#: unreachable while this holds, which is the right place for it to be.
_overlap = sorted(set(METRICS) & set(TRIPWIRES))
if _overlap:  # pragma: no cover - an import-time invariant
    raise RegistryError(
        f"{_overlap} is declared as both a metric and a tripwire. A tripwire "
        "counts zero and a metric may count; a key that is both cannot be "
        "evaluated against any criterion."
    )
del _overlap
