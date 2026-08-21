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

#: The two disjoint levers an#41 uses. Both are mandatory: an encoder lever
#: cannot touch a golden-frame metric because the corpus is UPSTREAM of the
#: encoder, so requiring ">=3 metrics" from a CRF change alone would fail
#: because of where the corpus sits, not because the instrument is blind — and
#: that failure would be misdiagnosed as the harness being wrong.
MUTATIONS: tuple[str, ...] = ("high_crf", "disabled_aa")

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
#: in mutation mode, and it names exact paths — a blanket "ignore the
#: environment when a mutation is given" would silently let a row from another
#: machine in through the same door.
#:
#: `disabled_aa` touches nothing the row records: it patches `runtime.js`, and
#: the runtime is the code under test rather than a comparability key. Whether
#: THAT lever applied is checked by the harness that pulls it, not by reading
#: the row back.
MUTATION_TOUCHES: dict[str, tuple[tuple[str, ...], ...]] = {
    "high_crf": (("environment", "encode_side", "x264_argv"),),
    "disabled_aa": (),
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
#:   subsampling that happens during that conversion, so a lossless-referenced
#:   version would read ~0 and measure nothing. And `encode_ringing_excess`
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
            },
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
                    reference="8x",
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
                "disabled_aa": Prediction("increase"),
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
