"""`an bench` — render a fixed corpus, compute a metrics panel, write one ledger row.

The instrument Wave 2 exists to build. Its job is not to say whether the
animation is good; it is to make a **deliberate degradation move a number in a
direction declared in advance**, so that a future regression is caught by
something other than someone noticing.

Three things about the panel that are easy to assume wrongly:

- **It is not the epic's metric list.** All twelve originally-proposed metrics
  were refuted; every one here is a corrected form. ``mean adjacent-frame
  SSIM`` in particular is *out* — it moves the wrong way (0.958 at crf18 ->
  0.977 at crf51, because a crushed video is smoother), so shipping it would
  put a number in the ledger that rewards the degradation the gate exists to
  catch.
- **Render-side and encode-side metrics are blind to each other's mutations by
  construction**, and are labelled so nothing mixes them. Render-side rows
  compare across any machine; encode-side rows are machine-scoped.
- **The vision judge is deliberately not here.** Not because its input is
  nondeterministic — over frozen frames it is perfectly reproducible — but
  because a cassetted judge is a *constant*, invariant to the code under test,
  so it can never move under a deliberate degradation.

Entry points: :func:`an.bench.run.run_bench`, and ``an bench`` on the CLI.
"""

from an.bench.ledger import (  # noqa: F401
    SCHEMA_VERSION,
    LedgerSchemaError,
    Value,
    build_ledger,
    build_scene_block,
    witnesses,
)
from an.bench.golden import (  # noqa: F401
    GATE_ABSENT,
    GATE_BUILD_UNKNOWN,
    GATE_JUST_BLESSED,
    GATE_UNDECLARED,
    REQUIRED_GOLDEN_FRAMES,
    RETIRED_GATES,
    GoldenError,
    bless_scene,
    compare_scene,
    frame_key,
    pixels_sha256,
)
from an.bench.png import (  # noqa: F401
    PNG_HEADER_BYTES,
    PngFormatError,
    decode_png,
    encode_png,
    png_dimensions,
    read_png,
    read_png_dimensions,
    write_png,
)
from an.bench.registry import (  # noqa: F401
    FAMILY_NAME,
    FAMILY_SIDE,
    METRICS,
    MUTATIONS,
    TRIPWIRES,
    MetricSpec,
    Prediction,
    RegistryError,
)
from an.bench.run import BenchError, format_panel, run_bench  # noqa: F401

__all__ = [
    "METRICS",
    "GATE_ABSENT",
    "GATE_BUILD_UNKNOWN",
    "GATE_JUST_BLESSED",
    "GATE_UNDECLARED",
    "REQUIRED_GOLDEN_FRAMES",
    "RETIRED_GATES",
    "GoldenError",
    "PngFormatError",
    "bless_scene",
    "compare_scene",
    "decode_png",
    "encode_png",
    "frame_key",
    "pixels_sha256",
    "png_dimensions",
    "read_png",
    "read_png_dimensions",
    "write_png",
    "TRIPWIRES",
    "MUTATIONS",
    "FAMILY_SIDE",
    "FAMILY_NAME",
    "MetricSpec",
    "Prediction",
    "RegistryError",
    "SCHEMA_VERSION",
    "LedgerSchemaError",
    "Value",
    "build_ledger",
    "build_scene_block",
    "witnesses",
    "run_bench",
    "format_panel",
    "BenchError",
]
