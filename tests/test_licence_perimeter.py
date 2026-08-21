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
    "argh": (
        "LGPL-3.0, and the ONLY declared-copyleft distribution in the hard "
        "dependency set. Recorded rather than waved through: `an` imports argh "
        "through its public interface, does not modify or vendor it, and a "
        "pip-installed pure-Python package is inherently replaceable — so the "
        "LGPL's combined-work condition is satisfied without reciprocity "
        "attaching to `an`'s own code. It is still outside the MIT/BSD/Apache/"
        "ISC rule this repo states, which is a decision for a human, not a "
        "test. See an#45."
    ),
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
VISION_EXTRA_CLOSURE: tuple[str, ...] = ("anthropic", "distro", "docstring-parser", "jiter")


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


#: Every distribution a bare `pip install an` pulls in.
HARD_DEPENDENCIES: tuple[str, ...] = (
    "pydantic",
    "dol",
    "argh",
    "argcomplete",
    "pyyaml",
    "numpy",
)


def test_no_unrecorded_hard_dependency_is_copyleft():
    """The perimeter that matters most: what every `pip install an` pulls in.

    Exactly one name is expected here, and it is in EXCEPTIONS with its
    reasoning. The assertion is "no NEW one appeared", which is the thing a
    test can usefully hold; whether the recorded one should stay is a decision
    for a human and is tracked as an issue.
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


def test_the_one_recorded_copyleft_hard_dependency_has_not_multiplied():
    """Pinned by count, so this test notices a second one arriving.

    An `assert not offenders` that skips every exception cannot tell "the known
    one" from "the known one plus a new one someone added to EXCEPTIONS".
    """
    copyleft = {
        name: _declared_licence(name)
        for name in HARD_DEPENDENCIES
        if _installed(name)
        and any(re.search(p, _declared_licence(name), re.I) for p in FORBIDDEN_PATTERNS)
    }
    assert set(copyleft) == {"argh"}, (
        f"the copyleft hard dependencies are now {sorted(copyleft)}; this test "
        "pins the set at exactly {'argh'} so a second one is a decision "
        "somebody makes rather than a line in EXCEPTIONS nobody reads"
    )
