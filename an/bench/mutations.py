"""The levers: deliberate degradations, applied through seams the shipped code already has.

This is the other half of the instrument. `an bench` records numbers; these
break the pipeline on purpose so a test can check the numbers move the way the
registry declared **in advance**. A metric that never moves under any mutation
is decoration, and the only way to know which is which is to pull a lever.

**No production knob exists for any of this, and that is deliberate.** Each
lever reaches an existing seam from the outside:

- ``high_crf`` rebinds ``an.adapters.cutout.render.DETERMINISTIC_X264_ARGS``.
  ``_ffmpeg_mux`` reads that name as a module global at call time, so the
  rebinding reaches the delivered encode. It does **not** reach
  ``an.bench.imageio.lossless_encode_command``, which bound the tuple at import
  — and that is exactly right: the lossless reference must stay lossless, or
  every encode-side metric would be measured against a moving target and the
  lever would produce beautiful numbers about nothing.
- ``disabled_aa`` copies the staged runtime, flips PixiJS's ``antialias`` in the
  copy, and rebinds ``an.adapters.cutout.render.runtime_dir``. The shipped
  ``runtime.js`` is never written to.

A knob in the product would be worse than either: it would have to be
documented, defended, and kept from being switched on by accident.

**Each lever verifies that it applied.** A lever that silently failed to take
produces a run in which nothing moved — which reads exactly like an instrument
that cannot see it, and sends the reader to fix the wrong thing. So
``verify_applied`` is part of the declaration rather than a courtesy, and the
two levers verify it in different places for a structural reason: the encode
argv is recorded in the ledger row, and the runtime is not (the runtime is the
code under test, not a comparability key — see
:data:`an.bench.registry.MUTATION_TOUCHES`).
"""

from __future__ import annotations

import shutil
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterator

from an.bench.registry import MUTATIONS

#: The CRF the encoder lever raises to. 40 rather than 51: measured on the CRF
#: ladder, 40 gives C x8.4, D x8.0 and F -42% on `single_character` — large,
#: unambiguous, and still a rate a human might plausibly ship. 51 is
#: pathological, and a lever nobody would ever pull by accident is a weaker
#: proxy for the regressions this instrument exists to catch.
HIGH_CRF: str = "40"

#: The exact text the AA lever flips, and where. Pinned as a literal so a
#: rename in `runtime.js` fails here — loudly, at the lever — rather than
#: producing a "mutation" that changes nothing.
AA_ON: str = "antialias: true"
AA_OFF: str = "antialias: false"


class MutationError(RuntimeError):
    """A lever could not be applied, or applied and left no trace."""


@dataclass(frozen=True, slots=True)
class Lever:
    """One deliberate degradation, with the evidence that it took."""

    name: str
    side: str
    what: str
    why: str
    apply: Callable[[], Any]
    #: Given the ledger row the mutated run produced, raise unless the lever's
    #: fingerprint is in it. ``None`` when the row cannot carry one — see the
    #: module docstring.
    verify_row: Callable[[dict], None] | None = None


@contextmanager
def _high_crf() -> Iterator[None]:
    """Raise the delivered encode's CRF, leaving the lossless reference alone."""
    from an.adapters.cutout import render

    original = render.DETERMINISTIC_X264_ARGS
    args = list(original)
    try:
        index = args.index("-crf")
    except ValueError as e:  # pragma: no cover - the pin would have to be removed
        raise MutationError(
            "`-crf` is not in DETERMINISTIC_X264_ARGS, so the encoder lever has "
            "nothing to pull. The rate control moved; re-point this lever at it "
            "rather than letting a mutation quietly become a no-op."
        ) from e
    args[index + 1] = HIGH_CRF
    render.DETERMINISTIC_X264_ARGS = tuple(args)
    try:
        yield
    finally:
        render.DETERMINISTIC_X264_ARGS = original


def _verify_high_crf(row: dict) -> None:
    argv = (
        ((row.get("provenance") or {}).get("environment") or {}).get("encode_side")
        or {}
    ).get("x264_argv") or []
    if "-crf" not in argv or argv[argv.index("-crf") + 1] != HIGH_CRF:
        raise MutationError(
            f"the mutated row records x264_argv={argv}, which is not crf {HIGH_CRF}. "
            "The lever did not reach the encode, so every 'nothing moved' below "
            "is about the lever and not about the instrument."
        )


@contextmanager
def _disabled_aa() -> Iterator[None]:
    """Turn PixiJS multisampling off, in a copy of the runtime."""
    from an.adapters.cutout import render
    from an.adapters.cutout.runtime_files import runtime_dir

    staged = Path(tempfile.mkdtemp(prefix="an-mutation-runtime-")) / "runtime"
    shutil.copytree(runtime_dir(), staged)
    source = (staged / "runtime.js").read_text(encoding="utf-8")
    if source.count(AA_ON) != 1:
        raise MutationError(
            f"expected exactly one {AA_ON!r} in runtime.js, found "
            f"{source.count(AA_ON)}. The AA lever is pinned to that literal, and "
            "a rename must fail here rather than produce a mutation that changes "
            "nothing."
        )
    (staged / "runtime.js").write_text(source.replace(AA_ON, AA_OFF), encoding="utf-8")
    original = render.runtime_dir
    render.runtime_dir = lambda: staged
    try:
        yield
    finally:
        render.runtime_dir = original
        shutil.rmtree(staged.parent, ignore_errors=True)


#: The two levers, keyed by the mutation name the registry declares. Both are
#: mandatory and they are **disjoint on purpose**: an encoder lever cannot touch
#: a golden-frame metric, because the corpus sits UPSTREAM of the encoder. So
#: requiring three families from a CRF change alone would fail for a reason that
#: has nothing to do with the instrument being blind — and that failure would be
#: misdiagnosed as the harness being wrong.
LEVERS: dict[str, Lever] = {
    "high_crf": Lever(
        name="high_crf",
        side="encode",
        what=f"raise the delivered encode from the pinned CRF to {HIGH_CRF}",
        why=(
            "moves only post-encode metrics. The golden corpus is upstream of the "
            "encoder, so family B cannot see this by construction — which is the "
            "reason two disjoint levers are mandatory."
        ),
        apply=_high_crf,
        verify_row=_verify_high_crf,
    ),
    "disabled_aa": Lever(
        name="disabled_aa",
        side="render",
        what="build the PixiJS application with multisampling off",
        why=(
            "moves the render-side families and fires the golden tripwire. Its "
            "effect is scene-dependent by measurement, not by accident: MSAA "
            "applies to WebGL geometry, so an SVG sprite is nearly blind to it "
            "(96 differing pixels of 12.4M) and axis-aligned `drawRect` edges are "
            "bit-identical with it on or off. `aa_probe` exists so the corpus "
            "contains edges this lever can actually change."
        ),
        apply=_disabled_aa,
        verify_row=None,
    ),
}


def _check_registered() -> None:
    missing = [m for m in MUTATIONS if m not in LEVERS]
    if missing:  # pragma: no cover - an import-time invariant
        raise MutationError(
            f"the registry declares {missing} but no lever pulls them. A mutation "
            "with a full column of predictions and no way to apply it is a "
            "criterion nobody can ever evaluate."
        )
    extra = [name for name in LEVERS if name not in MUTATIONS]
    if extra:  # pragma: no cover - an import-time invariant
        raise MutationError(
            f"{extra} has a lever but no predictions. Every metric must declare "
            "what it will do under every mutation, in advance — that is the whole "
            "of the criterion."
        )


_check_registered()


def mutated_row(name: str, **run_kwargs: Any) -> dict:
    """Render the corpus with one lever pulled, and prove the lever took.

    Deliberately returns a row rather than a comparison: what to do with it is
    :mod:`an.bench.compare`'s job, and keeping the two apart is what lets the
    criterion be evaluated against a row written months ago.
    """
    from an.bench.run import run_bench

    lever = LEVERS[name]
    with lever.apply():
        row = run_bench(write=False, **run_kwargs)
    if lever.verify_row is not None:
        lever.verify_row(row)
    return row
