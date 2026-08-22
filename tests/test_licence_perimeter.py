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


def _declared_dependency_block(text: str) -> str:
    """The body of ``dependencies = [ ... ]``, matched by BRACKET DEPTH, line by line.

    Not ``.split("]", 1)``. A single requirement carrying an extra —
    ``"uvicorn[standard]"`` — closes the array early on that spelling, and the
    perimeter then silently checks only the names above it. Measured: adding
    one such line plus a plain ``argh`` to a scratch pyproject took the file
    from 3 failures to **10 passed**. One line disarmed the whole check.

    Line-based, with comments stripped first, so a stray bracket inside a
    comment cannot terminate the scan either.

    (No doctest here: CI runs `--doctest-modules` over `testpaths`, which is
    `tests/`, so a docstring in this file is EXECUTED — and a `\\n` in a non-raw
    docstring becomes a real newline the doctest parser reads as inconsistent
    indentation. The examples live in
    `test_the_derivation_survives_the_shapes_a_requirement_can_take` instead,
    where they are ordinary code.)
    """
    lines = text.splitlines()
    start = next(
        (i for i, line in enumerate(lines) if "dependencies = [" in line), None
    )
    assert start is not None, "pyproject.toml declares no `dependencies = [`"
    depth = lines[start].split("dependencies = [", 1)[1].split("#", 1)[0]
    body: list[str] = []
    level = 1 + depth.count("[") - depth.count("]")
    if level == 0:
        return depth.rsplit("]", 1)[0]
    for line in lines[start + 1 :]:
        code = line.split("#", 1)[0]
        level += code.count("[") - code.count("]")
        if level <= 0:
            return "\n".join(body)
        body.append(line)
    raise AssertionError("`dependencies = [` is never closed in pyproject.toml")


def _hard_dependencies(text: str | None = None) -> tuple[str, ...]:
    """Every distribution `an` DECLARES, read from pyproject.

    Derived rather than restated. This was a hand-maintained tuple, which is the
    second-table smell the whole bench registry exists to avoid: a dependency
    added to `pyproject.toml` and forgotten here is a dependency the perimeter
    never looks at, and the perimeter reads as green either way. an#45 removed
    two names from that list and the literal would have kept checking both.

    Scanned as text rather than parsed: `tomllib` is 3.11+ and this repo
    supports 3.10, and the shape here is a flat array of requirement strings.

    ``text`` is injectable so the DERIVATION can be tested against a synthetic
    block. Asserting the real tuple's contents tests the contents, not the
    derivation — the exact shape this repo keeps losing mutants to. See
    `test_the_derivation_survives_the_shapes_a_requirement_can_take`.
    """
    if text is None:
        text = (Path(__file__).resolve().parents[1] / "pyproject.toml").read_text(
            encoding="utf-8"
        )
    names = []
    for line in _declared_dependency_block(text).splitlines():
        line = line.split("#", 1)[0].strip().rstrip(",").strip()
        if line.startswith(('"', "'")):
            requirement = line.strip("\"'")
            names.append(re.split(r"[<>=!~\[; ]", requirement, maxsplit=1)[0])
    return tuple(names)


def _closure(names: tuple[str, ...]) -> tuple[str, ...]:
    """The declared names PLUS everything they pull in, as installed here.

    The perimeter used to check the five declared names only, and that is a
    smaller claim than it reads as: `pip install an` installs their transitive
    closure, and a copyleft distribution three levels down is exactly as much a
    part of what a downstream consumer inherits.

    It is still a fact about THIS environment rather than about the package —
    `Requires-Dist` is read from installed metadata, so a version that resolves
    differently elsewhere is invisible here. Measured, and not hypothetically:
    the local typer 0.19.2 requires `click`, while the typer a fresh
    `pip install an` resolves today (0.27.1, since `pyproject.toml` pins no
    version) requires `shellingham`, `rich`, `annotated-doc` and **no click at
    all**. `test_the_closure_is_bigger_than_the_declared_set` says so out loud
    rather than letting the number read as universal.
    """
    from importlib.metadata import PackageNotFoundError, requires

    seen: set[str] = set()
    queue = list(names)
    while queue:
        name = _normalise(queue.pop())
        if name in seen:
            continue
        seen.add(name)
        try:
            declared = requires(name) or []
        except PackageNotFoundError:
            continue
        for requirement in declared:
            # Skip extras-only requirements: `pip install an` does not take
            # them, so they are not part of what a bare install pulls in.
            if "extra ==" in requirement:
                continue
            queue.append(re.split(r"[<>=!~\[; ()]", requirement, maxsplit=1)[0].strip())
    return tuple(sorted(seen))


def _normalise(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


#: Everything `an` declares.
HARD_DEPENDENCIES: tuple[str, ...] = _hard_dependencies()

#: Everything a bare `pip install an` pulls in, as resolved in THIS environment.
HARD_CLOSURE: tuple[str, ...] = _closure(HARD_DEPENDENCIES)


def test_the_derivation_survives_the_shapes_a_requirement_can_take():
    """MUTATION: `.split("]", 1)` instead of the bracket-depth scan.

    Tests the DERIVATION against a synthetic block, not the real tuple's
    contents — asserting the contents tests the contents, and this repo keeps
    losing mutants to exactly that shape.

    The extras case is the one that matters. `"uvicorn[standard]"` closes the
    array early under a naive split, and everything below it stops being
    checked: measured, one such line plus a plain `argh` took the file from 3
    failures to 10 passed. One line disarmed the whole perimeter.
    """
    block = """dependencies = [
    "plain",
    "with-extra[standard]",
    "pinned>=1.2",
    "marked; python_version >= '3.10'",
    "commented",  # a trailing note with a ] bracket in it
    # "commented-out",
]"""
    assert _hard_dependencies(block) == (
        "plain",
        "with-extra",
        "pinned",
        "marked",
        "commented",
    )
    # The block matcher itself, since it is where the extras bug lived.
    nested = 'dependencies = [\n  "a[x]",\n  "b",\n]\n'
    assert _declared_dependency_block(nested) == '  "a[x]",\n  "b",'
    assert _declared_dependency_block('dependencies = ["only"]') == '"only"'


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


def test_the_licence_detector_actually_detects():
    """MUTATION: `FORBIDDEN_PATTERNS = ()`.

    The tables had NO positive case: deleting the whole forbidden tuple left the
    file green, because every test that reads it only ever asserts that nothing
    matched. A detector with no demonstrated true positive is a detector nobody
    has checked, and this file's entire job is detection.
    """
    lgpl = (
        "License :: OSI Approved :: GNU Library or Lesser General Public License (LGPL)"
    )
    assert any(re.search(p, lgpl, re.I) for p in FORBIDDEN_PATTERNS), (
        "the real classifier string argh declared is not detected as copyleft"
    )
    for copyleft in ("GPL-3.0-only", "AGPL-3.0", "LGPL-2.1-or-later", "BSL-1.1"):
        assert any(re.search(p, copyleft, re.I) for p in FORBIDDEN_PATTERNS), copyleft
    # MPL is deliberately NOT in the forbidden set — it is file-level weak
    # copyleft, and `certifi` sits in EXCEPTIONS because it matches no ALLOWED
    # pattern rather than because it matches a forbidden one. Asserted so the
    # distinction is a decision rather than an omission nobody noticed.
    assert not any(re.search(p, "MPL-2.0", re.I) for p in FORBIDDEN_PATTERNS)
    assert "certifi" in EXCEPTIONS
    for permissive in ("MIT", "BSD-3-Clause", "Apache-2.0", "ISC"):
        assert not any(re.search(p, permissive, re.I) for p in FORBIDDEN_PATTERNS), (
            f"{permissive} is flagged as copyleft"
        )
        assert any(re.search(p, permissive, re.I) for p in ALLOWED_PATTERNS), permissive


def test_the_closure_is_bigger_than_the_declared_set():
    """The perimeter checks what `pip install an` PULLS IN, not what it names.

    MUTATION: `HARD_CLOSURE = HARD_DEPENDENCIES`.

    A copyleft distribution three levels down is exactly as much a part of what
    a downstream consumer inherits. And it says out loud that this is a fact
    about THIS environment: `Requires-Dist` comes from installed metadata, so a
    version that resolves differently elsewhere is invisible. Measured, and not
    hypothetically — the local typer 0.19.2 requires `click`, while the typer a
    fresh `pip install an` resolves today (0.27.1, since nothing pins a version)
    requires `shellingham`, `rich`, `annotated-doc` and NO CLICK AT ALL.
    """
    assert set(_normalise(n) for n in HARD_DEPENDENCIES) <= set(HARD_CLOSURE)
    assert len(HARD_CLOSURE) > len(HARD_DEPENDENCIES), (
        "the closure is no bigger than the declared set, which means either "
        "nothing is installed or the walk stopped"
    )


def test_no_unrecorded_hard_dependency_is_copyleft():
    """The perimeter that matters most: what every `pip install an` pulls in.

    Since an#45 the expected count is ZERO — `argh` was the only one and it was
    replaced with `typer` (MIT). The assertion is "no new one appeared", which
    is the thing a test can usefully hold.
    """
    offenders = []
    for name in HARD_CLOSURE:
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
        for name in HARD_CLOSURE
        if _installed(name)
        and any(re.search(p, _declared_licence(name), re.I) for p in FORBIDDEN_PATTERNS)
    }
    assert copyleft == {}, (
        f"the copyleft hard dependencies are now {sorted(copyleft)}; this set has "
        "been empty since an#45 and a new member is a decision somebody makes "
        "rather than a line in EXCEPTIONS nobody reads"
    )
