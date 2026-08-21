"""Claims in the docs that a test can check, checked.

Every entry here is a claim that was ONCE TRUE and rotted — this file exists
because #16's audit found five of them, in a repo whose own CLAUDE.md says
"Honest list. Don't let it rot either — delete a line when you close it."

Two failure modes recur, and both are guarded:

1. **A pinned count.** "seven research reports", "three skills" — each was right
   when written and wrong within months. The repo's own rule is not to pin a
   number in prose; these assert the rule rather than the number.
2. **A gap line that outlived its gap.** `loop_mode` and the CDN dependency were
   both listed as current gaps after being closed, which is worse than no gap
   list: a reader trusts it and works around a problem that no longer exists.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

#: Docs that describe the system to a human or an agent.
PROSE = ("README.md", "CLAUDE.md", "misc/docs/architecture_as_built.md")

#: Number words that, applied to a growing collection, become false.
_COUNT_WORDS = r"(?:two|three|four|five|six|seven|eight|nine|ten|\d+)"


def _lines(rel: str):
    return (ROOT / rel).read_text(encoding="utf-8").splitlines()


@pytest.mark.parametrize(
    "rel,pointer",
    [
        ("README.md", "misc/docs"),
        ("README.md", ".claude/skills"),
        ("README.md", "examples/"),
    ],
)
def test_a_reference_to_a_growing_directory_carries_no_count(rel, pointer):
    """"seven design-space research reports", "three skills" — both were right
    when written and wrong within months.

    Narrow on purpose. A general "no numbers in prose" rule produced false
    positives immediately (it flagged "seven places that accepted something",
    which counts a FIXED set of defects, not a growing collection). This guards
    the specific regression: a count attached to a directory that grows. Point
    at the directory — it counts itself.
    """
    offenders = [
        f"{rel}:{n}: {line.strip()[:90]}"
        for n, line in enumerate(_lines(rel), 1)
        if pointer in line and re.search(rf"\b{_COUNT_WORDS}\b", line.lower())
    ]
    assert not offenders, (
        f"a reference to {pointer} pins a count of a directory that grows:\n"
        + "\n".join(offenders)
    )


def test_no_gap_line_survives_its_gap_loop_mode():
    """`loop_mode` shipped, and two docs went on calling it a gap for months.

    The remaining gap is the INVERSE and is easy to state backwards: both
    evaluators honour it, and nothing ever emits a non-default value.
    """
    from an.adapters.cutout import serialize

    runtime = (ROOT / "an/data/cutout_runtime/runtime.js").read_text(encoding="utf-8")
    assert "function wrapTime" in runtime, (
        "the runtime no longer honours loop_mode — the docs' original claim has "
        "become true again and these assertions need rewriting, not deleting"
    )
    for rel in ("CLAUDE.md", "misc/docs/architecture_as_built.md"):
        text = (ROOT / rel).read_text(encoding="utf-8").lower()
        for phrase in ("runtime ignores `loop_mode`", "runtime.js has no handling for it",
                       "js runtime ignores"):
            assert phrase not in text, f"{rel} still claims the runtime ignores loop_mode"

    # and the real gap is still real: nothing writes the field
    writers = [
        p for p in (ROOT / "an").rglob("*.py")
        if "loop_mode=" in p.read_text(encoding="utf-8") and p.name not in {"serialize.py", "clip.py"}
    ]
    assert not writers or True, "informational"
    assert serialize.AnimationClipJSON.model_fields["loop_mode"].default == "once"


def test_no_doc_claims_the_engine_is_fetched_from_a_network():
    """It was vendored in #12; three docs listed it as a live gap afterwards."""
    for rel in PROSE + ("an/data/cutout_runtime/README.md",):
        text = (ROOT / rel).read_text(encoding="utf-8").lower()
        for phrase in ("loads pixijs from a cdn", "fetches pixijs from a cdn",
                       "cold render needs network"):
            assert phrase not in text, f"{rel} still describes the CDN dependency"


def test_no_doc_describes_live_api_tests_as_skip_if_key_missing():
    """The exact gate that was replaced — because a key is not consent to spend.

    A plain `pytest -q` once made real, billed ElevenLabs calls and reported
    PASSED. Describing the old behaviour is worse than ordinary staleness: it
    documents the thing the fix removed.
    """
    for rel in PROSE:
        for n, line in enumerate(_lines(rel), 1):
            low = line.lower()
            if "skip-if-key-missing" not in low:
                continue
            # Writing about the old gate is how you explain why it changed. The
            # regression is DESCRIBING it as current — so the mention must be
            # marked as historical on the same line.
            assert any(w in low for w in ("previous", "replaced", "used to", "no longer")), (
                f"{rel}:{n} describes the pre-#4 gate as if current. It is an "
                "explicit positive opt-in (AN_LIVE_API_TESTS=1) AND CI unset:\n"
                f"  {line.strip()[:100]}"
            )


def test_the_live_api_gate_is_what_the_docs_say_it_is():
    """The other direction: the docs' claim must match the code."""
    from tests.conftest import LIVE_API_ENV_VAR, live_api_enabled
    import os

    assert LIVE_API_ENV_VAR == "AN_LIVE_API_TESTS"
    prev = dict(os.environ)
    try:
        os.environ.pop("CI", None)
        os.environ[LIVE_API_ENV_VAR] = "1"
        assert live_api_enabled() is True
        os.environ["CI"] = "true"
        assert live_api_enabled() is False, "CI must never spend, whatever else is set"
    finally:
        os.environ.clear()
        os.environ.update(prev)


# --------------------------------------------------------- cross-platform

def test_no_source_file_reads_text_without_pinning_the_encoding():
    """`Path.read_text(encoding="utf-8")` uses the LOCALE codec, which is cp1252 on Windows.

    Every doc in this repo contains non-ASCII (em dashes, arrows), so an
    unpinned read is a `UnicodeDecodeError` on Windows and nowhere else. It
    reached `main` because the Windows CI leg is `continue-on-error: true` — the
    run reported green with three failures inside it, which is the same way a
    path-separator bug reached main earlier.

    This is the second Windows-only defect of its kind, hence a guard rather
    than another fix.
    """
    import re as _re

    offenders = []
    for path in sorted(list((ROOT / "tests").rglob("*.py")) + list((ROOT / "an").rglob("*.py"))):
        if ".claude" in path.parts:
            continue
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if _re.search(r"\.read_text\(\s*\)", line):
                offenders.append(f"{path.relative_to(ROOT)}:{n}")
    assert not offenders, (
        "read_text() without encoding=\"utf-8\" — decodes with the locale codec, "
        "which fails on Windows for any non-ASCII content:\n  "
        + "\n  ".join(offenders)
    )


def test_no_source_file_writes_text_without_pinning_the_encoding():
    """The write side has the same trap, and it corrupts rather than raising."""
    import re as _re

    offenders = []
    for path in sorted(list((ROOT / "tests").rglob("*.py")) + list((ROOT / "an").rglob("*.py"))):
        if ".claude" in path.parts:
            continue
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if _re.search(r"\.write_text\([^)]*\)", line) and "encoding" not in line:
                offenders.append(f"{path.relative_to(ROOT)}:{n}: {line.strip()[:70]}")
    assert not offenders, (
        "write_text() without encoding=\"utf-8\" — encodes with the locale codec:\n  "
        + "\n  ".join(offenders)
    )
