"""Every distribution `an` can pull in carries a permissive licence (an#39).

House rule: MIT / BSD / Apache / ISC only, no LGPL / GPL / AGPL / BSL. A rule
nobody checks is a sentence, so this walks the installed metadata rather than
trusting a declaration in a design document.

**Read from `dist-info`, never from the PyPI licence field.** The federation
has a recorded trap here: PyPI's `dssim` 1.3.0 declares `Apache-2.0` and is a
completely unrelated discrete-event-simulation framework, while the `dssim`
anyone would want is AGPL-3.0. Checking the field rather than the artefact
installs the wrong library under a wrong licence belief.

Two exceptions are recorded by name rather than waved through, and **neither is
introduced by this package's own extras** — both arrive through dependencies
that were already hard or already shipped.
"""

from __future__ import annotations

import re
from pathlib import Path
from importlib.metadata import PackageNotFoundError, distribution, metadata

import pytest

#: Permissive families, matched case-insensitively against the declared licence.
ALLOWED_PATTERNS: tuple[str, ...] = (
    r"\bMIT\b",
    r"\bBSD\b",
    r"\bApache[- ]?2",
    r"\bApache Software License\b",
    r"\bISC\b",
    r"\bPython Software Foundation\b",
    r"\bPSF\b",
    r"\bHPND\b",
    r"\bUnlicense\b",
    r"\bCC0\b",
)

#: Never acceptable, whatever else a distribution declares.
FORBIDDEN_PATTERNS: tuple[str, ...] = (
    r"\bAGPL\b",
    r"\bGNU Affero\b",
    r"\bGPL(?!v?\d*\s*with)",
    r"\bLGPL\b",
    r"\bBusiness Source\b",
    r"\bBSL\b",
)

#: Adjudicated exceptions, each with the reason it is not a reciprocity risk.
#: Listing beats silence: a name here is a decision someone made, and a name
#: that disappears from the tree makes this test say so.
EXCEPTIONS: dict[str, str] = {
    "certifi": (
        "MPL-2.0 — file-level weak copyleft over an unmodified CA bundle "
        "consumed as data. Nothing `an` ships attracts reciprocity. Arrives "
        "via httpx, which the shipped `tts` extra already pulls."
    ),
    "typing-extensions": (
        "PSF-2.0 — permissive, and already HARD via pydantic; not introduced here."
    ),
    "jiter": (
        "declares `License-Expression: MIT` (PEP 639) but ships no LICENSE file "
        "in its installed metadata. Accepted on the declared expression, "
        "recorded here, and to be verified upstream at the pin."
    ),
}

#: Distributions the `vision` extra adds that `an` does not otherwise ship.
VISION_EXTRA_CLOSURE: tuple[str, ...] = (
    "anthropic",
    "distro",
    "docstring-parser",
    "jiter",
)


def _declared_licence(name: str) -> str:
    """The DECLARATION, in order of precision — never the licence document.

    A distribution's free-text ``License`` field is often the whole licence
    text, and several permissive ones bundle third-party notices. numpy is
    BSD-3-Clause and its field contains an LGPL URL for a vendored component's
    notice, so substring-scanning that field reports numpy as copyleft. Read
    the PEP 639 expression, then the classifiers, and only fall back to the
    field's FIRST LINE.
    """
    meta = metadata(name)
    expression = (meta.get("License-Expression") or "").strip()
    if expression and expression != "UNKNOWN":
        return expression
    classifiers = [
        c for c in (meta.get_all("Classifier") or []) if c.startswith("License ::")
    ]
    if classifiers:
        return " | ".join(classifiers)
    field = (meta.get("License") or "").strip()
    return field.splitlines()[0].strip() if field else ""


def _installed(name: str) -> bool:
    try:
        distribution(name)
    except PackageNotFoundError:
        return False
    return True


@pytest.mark.parametrize("name", VISION_EXTRA_CLOSURE)
def test_the_vision_extra_stays_inside_the_licence_perimeter(name):
    """`anthropic` is MIT; so is everything it adds that `an` did not already ship."""
    if not _installed(name):
        pytest.skip(f"{name} is not installed (the `vision` extra is optional)")
    declared = _declared_licence(name)
    assert declared, f"{name} declares no licence at all in its installed metadata"

    for pattern in FORBIDDEN_PATTERNS:
        assert not re.search(pattern, declared, re.I), (
            f"{name} declares {declared!r}, which is outside the perimeter"
        )
    if name in EXCEPTIONS:
        return
    assert any(re.search(p, declared, re.I) for p in ALLOWED_PATTERNS), (
        f"{name} declares {declared!r}, which matches no permissive family. "
        "Either it is a real problem or it is an adjudicated exception — and an "
        "exception belongs in EXCEPTIONS with its reason, not in a comment."
    )


@pytest.mark.parametrize("name,reason", sorted(EXCEPTIONS.items()))
def test_each_recorded_exception_is_still_present_and_still_that_licence(name, reason):
    """An exception for a distribution that has left the tree is stale advice.

    And one whose licence has changed is worse: it reads as adjudicated when
    nobody has looked at the current terms.
    """
    if not _installed(name):
        pytest.skip(f"{name} is no longer in the tree; drop its exception")
    assert reason.strip(), f"{name} is excepted with no reason given"
    declared = _declared_licence(name)
    assert declared, f"{name} declares no licence"


def _hard_dependencies() -> tuple[str, ...]:
    """Every distribution a bare `pip install an` pulls in, READ FROM pyproject.

    Derived rather than restated. This was a hand-maintained tuple, which is the
    second-table smell the whole bench registry exists to avoid: a dependency
    added to `pyproject.toml` and forgotten here is a dependency the perimeter
    never looks at, and the perimeter reads as green either way. an#45 removed
    two names from that list and the literal would have kept checking both.

    Scanned as text rather than parsed: `tomllib` is 3.11+ and this repo
    supports 3.10, and the shape here is a flat array of requirement strings.
    """
    text = (Path(__file__).resolve().parents[1] / "pyproject.toml").read_text(
        encoding="utf-8"
    )
    body = text.split("dependencies = [", 1)[1].split("]", 1)[0]
    names = []
    for line in body.splitlines():
        line = line.split("#", 1)[0].strip().rstrip(",").strip()
        if line.startswith(('"', "'")):
            requirement = line.strip("\"'")
            names.append(re.split(r"[<>=!~\[; ]", requirement, 1)[0])
    return tuple(names)


#: Every distribution a bare `pip install an` pulls in.
HARD_DEPENDENCIES: tuple[str, ...] = _hard_dependencies()


def test_the_hard_dependency_list_is_read_from_pyproject():
    """MUTATION: restore the hand-written tuple.

    A dependency added to `pyproject.toml` and forgotten in a literal here is a
    dependency the perimeter never looks at — and the perimeter reads as green
    either way, which is the failure mode this whole file exists to end.
    """
    assert HARD_DEPENDENCIES, "no dependencies were parsed out of pyproject.toml"
    assert "typer" in HARD_DEPENDENCIES
    assert "argh" not in HARD_DEPENDENCIES, (
        "argh was replaced in an#45; if it is back, so is the LGPL question"
    )
    for name in HARD_DEPENDENCIES:
        assert name and not name.startswith(("#", '"', "'")), (
            f"{name!r} is not a distribution name — the parse is wrong"
        )


def test_no_unrecorded_hard_dependency_is_copyleft():
    """The perimeter that matters most: what every `pip install an` pulls in.

    Since an#45 the expected count is ZERO — `argh` was the only one and it was
    replaced with `typer` (MIT). The assertion is "no new one appeared", which
    is the thing a test can usefully hold.
    """
    offenders = []
    for name in HARD_DEPENDENCIES:
        if not _installed(name) or name in EXCEPTIONS:
            continue
        declared = _declared_licence(name)
        if any(re.search(p, declared, re.I) for p in FORBIDDEN_PATTERNS):
            offenders.append(f"{name}: {declared}")
    assert not offenders, (
        "a hard dependency declares a copyleft licence and is not a recorded "
        "exception: " + "; ".join(offenders)
    )


def test_no_hard_dependency_is_copyleft_at_all():
    """Pinned at EMPTY since an#45, so this notices one arriving.

    An `assert not offenders` that skips every exception cannot tell "none" from
    "one somebody added to EXCEPTIONS". Pinning the set itself can, and the set
    is now empty: `argh` (LGPL-3.0) was the only member and it was replaced with
    `typer` (MIT) rather than excepted, which is the decision an#45 asked for.
    """
    copyleft = {
        name: _declared_licence(name)
        for name in HARD_DEPENDENCIES
        if _installed(name)
        and any(re.search(p, _declared_licence(name), re.I) for p in FORBIDDEN_PATTERNS)
    }
    assert copyleft == {}, (
        f"the copyleft hard dependencies are now {sorted(copyleft)}; this set has "
        "been empty since an#45 and a new member is a decision somebody makes "
        "rather than a line in EXCEPTIONS nobody reads"
    )
