"""The determinism perimeter: latent randomness, watched rather than assumed away.

an#37. The two things the epic named — the blink phase and the palette hash —
were already deterministic before the issue was written, and the rasteriser /
encoder pins landed unconditionally in an#31 / an#34. What was left is the part
nobody can pin: machinery that is deterministic *by accident*.

The vendored PixiJS carries four `Math.random`, two `Date.now`, six
`performance.now` and three `requestAnimationFrame` calls, and `NoiseFilter`
seeds itself from `Math.random()`. All dormant — because the runtime uses
`autoStart: false` with explicit `app.render()`, and because nothing attaches a
filter. Neither fact was written anywhere that would go red if it stopped being
true.

These tests are the place it goes red. They need no browser: `runtime.js`
observes and `an.determinism` judges, so the rule is a pure function of a dict.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from an.check_requirements import (
    PLAYWRIGHT_CACHE_BY_PLATFORM,
    playwright_browser_dirs,
)
from an.determinism import (
    AN_DETERMINISTIC_ENV_VAR,
    CAPTURE_PAGE,
    capture_violations,
    determinism_enforced,
)

RUNTIME_JS = Path(__file__).resolve().parents[1] / "an/data/cutout_runtime/runtime.js"


def _clean_report(**overrides) -> dict:
    """What the probe reports on a healthy capture."""
    report = {
        "page": "/index.html",
        "runtime_version": "0.1.0",
        "pixi_version": "7.4.2",
        "auto_start": False,
        "shared_ticker_started": False,
        "stage_filter_count": 0,
        "filtered_node_paths": [],
        "blink_phases": {"charlie": 0.123},
        "node_count": 12,
    }
    report.update(overrides)
    return report


# --------------------------------------------------------------- the verdict


def test_a_healthy_capture_reports_nothing():
    """The guard must not fire on the case it exists to protect."""
    assert capture_violations(_clean_report()) == []


def test_capturing_from_preview_html_is_a_violation():
    """`render.py` stages `preview.html` into every work dir.

    It carries seven clock calls, so a frame captured from it is a function of
    wall time. The wrong page is one line away and produces plausible video.
    """
    (v,) = capture_violations(_clean_report(page="/preview.html"))
    assert "preview.html" in v and CAPTURE_PAGE in v


@pytest.mark.parametrize(
    "field", ["shared_ticker_started", "auto_start"]
)
def test_a_running_ticker_is_a_violation(field):
    """A ticker advances the scene between the seek and the screenshot."""
    (v,) = capture_violations(_clean_report(**{field: True}))
    assert "ticker" in v and "anSetTime" in v


def test_a_filter_anywhere_is_a_violation_and_says_it_is_a_tripwire():
    """A blur is deterministic; `NoiseFilter` is not. The point is to decide.

    Wave 3 adding a grain filter would otherwise randomise every frame with
    nothing red — the exact shape this whole wave exists to prevent.
    """
    (v,) = capture_violations(_clean_report(filtered_node_paths=["charlie/head"]))
    assert "charlie/head" in v
    assert "NoiseFilter" in v, "the message must name the one that actually randomises"
    assert "tripwire" in v, "and not read as a ban on filters"


def test_a_stage_filter_is_caught_even_with_no_indexed_node():
    """The stage is not in `nodeIndex`, so the node walk alone cannot see it."""
    assert capture_violations(_clean_report(stage_filter_count=1))


def test_a_missing_field_is_a_violation_rather_than_a_default():
    """A report that cannot testify must not be read as testifying "fine".

    Defaulting `report.get("shared_ticker_started", False)` would turn an old
    staged runtime into a clean bill of health, which is precisely the
    absence-of-evidence-as-evidence-of-absence failure this module is about.
    """
    incomplete = _clean_report()
    del incomplete["shared_ticker_started"]
    (v,) = capture_violations(incomplete)
    assert "missing" in v and "shared_ticker_started" in v


def test_every_field_the_checker_reads_is_required():
    """No field may be read leniently — checked by removing each in turn."""
    from an.determinism import _REQUIRED_FIELDS

    for field in _REQUIRED_FIELDS:
        report = _clean_report()
        del report[field]
        assert capture_violations(report), (
            f"{field!r} went missing and the perimeter still reported clean"
        )


# ------------------------------------------------------------- the env switch


def test_enforcement_is_on_by_default(monkeypatch):
    """A deliberate deviation from the issue's `AN_DETERMINISTIC=1` framing.

    an#31 made the launch flags unconditional for exactly this reason: a
    property that only holds when someone remembers to export a variable is not
    a property of the system. The same argument applies to an assertion — one
    that nobody runs is worse than none, because the perimeter reads as
    guarded.
    """
    monkeypatch.delenv(AN_DETERMINISTIC_ENV_VAR, raising=False)
    assert determinism_enforced() is True


@pytest.mark.parametrize("value", ["0", "false", "no", "off", "OFF", " 0 "])
def test_the_env_var_is_an_off_switch(monkeypatch, value):
    monkeypatch.setenv(AN_DETERMINISTIC_ENV_VAR, value)
    assert determinism_enforced() is False


@pytest.mark.parametrize("value", ["1", "true", "yes", ""])
def test_anything_else_leaves_enforcement_on(monkeypatch, value):
    """Including the empty string: `AN_DETERMINISTIC=` is not "please disable"."""
    monkeypatch.setenv(AN_DETERMINISTIC_ENV_VAR, value)
    assert determinism_enforced() is True


# ------------------------------------------------ the runtime's own invariants


def test_every_object_keys_iteration_in_the_runtime_is_sorted():
    """Two of these were unsorted and safe only by accident (research §2).

    `Object.keys` order is JSON-document order, i.e. a function of the Python
    compiler's emission order — not a contract. The blink loop was safe only
    because each iteration writes an independent node, and the texture-alias
    loop only because `PIXI.Assets.load` has never been observed to care.
    Neither invariant was written down. Written as a sweep rather than two
    pinned line numbers, so a NEW unsorted iteration is caught too.
    """
    src = RUNTIME_JS.read_text(encoding="utf-8")
    unsorted = [
        m.group(0)
        for m in re.finditer(r"Object\.keys\([^)]*\)(?!\s*\.sort\b)(?!\s*\.length\b)", src)
    ]
    assert not unsorted, (
        "an unsorted Object.keys iteration reached the runtime; sort it or, if "
        "the order genuinely cannot escape, say so beside it:\n"
        + "\n".join(unsorted)
    )


def test_the_runtime_exposes_the_determinism_probe():
    """The Python checker is useless without something to check."""
    src = RUNTIME_JS.read_text(encoding="utf-8")
    assert "NS.anDeterminismReport" in src


def test_the_probe_reports_every_field_the_checker_requires():
    """Two files, one contract — and neither imports the other.

    Asserted against the runtime source rather than a live browser so the
    contract holds in the default lane; the browser test below proves the probe
    actually runs.
    """
    from an.determinism import _REQUIRED_FIELDS

    src = RUNTIME_JS.read_text(encoding="utf-8")
    body = src.split("NS.anDeterminismReport", 1)[1]
    missing = [f for f in _REQUIRED_FIELDS if f"{f}:" not in body]
    assert not missing, (
        f"the runtime probe does not report {missing}, so every render would "
        "fail the 'missing field' check"
    )


def test_the_blink_phase_dependence_on_the_entity_name_is_recorded():
    """The hazard nothing warns about, turned into a stamped fact.

    The blink phase is a pure function of the entity NAME, so renaming a corpus
    character silently re-phases every blink and moves every pixel metric.
    Recording the phases per entity does not prevent it — it makes it a visible
    diff in the ledger instead of an unexplained metric shift.
    """
    src = RUNTIME_JS.read_text(encoding="utf-8")
    body = src.split("NS.anDeterminismReport", 1)[1]
    assert "blink_phases:" in body


# ------------------------------------------------------ the Playwright probe


@pytest.mark.parametrize("platform", sorted(PLAYWRIGHT_CACHE_BY_PLATFORM))
def test_the_browser_cache_probe_prefers_this_platforms_path(monkeypatch, platform):
    """It checked the macOS path only, on every OS (an#37).

    On Linux that reported "playwright pkg installed but Chromium not" on a
    machine where it was — an instruction to run a command that changes
    nothing. It mattered the moment CI first launched a browser.
    """
    monkeypatch.delenv("PLAYWRIGHT_BROWSERS_PATH", raising=False)
    dirs = playwright_browser_dirs(platform=platform)
    expected = Path(PLAYWRIGHT_CACHE_BY_PLATFORM[platform]).expanduser()
    assert Path(dirs[0]) == expected
    assert len(dirs) == len(PLAYWRIGHT_CACHE_BY_PLATFORM), (
        "the others are still checked — sys.platform is not the whole story "
        "(WSL, a Linux venv on a macOS-mounted home)"
    )


def test_an_explicit_browsers_path_overrides_every_default(monkeypatch):
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", "/opt/pw")
    assert playwright_browser_dirs() == ["/opt/pw"]


# ---------------------------------------------------- the render path's use of it


class _FakePage:
    """Stands in for a Playwright page: answers `anDeterminismReport` and nothing else."""

    def __init__(self, report):
        self._report = report

    def evaluate(self, _js, *args):
        if isinstance(self._report, Exception):
            raise self._report
        return self._report


def test_the_render_path_refuses_a_breached_perimeter(monkeypatch):
    """A checker the render path does not consult protects nothing."""
    from an.adapters.cutout.render import CutoutRenderError, _determinism_report

    monkeypatch.delenv(AN_DETERMINISTIC_ENV_VAR, raising=False)
    with pytest.raises(CutoutRenderError) as e:
        _determinism_report(_FakePage(_clean_report(shared_ticker_started=True)))
    assert "ticker" in str(e.value)


def test_the_off_switch_records_the_breach_instead_of_raising(monkeypatch):
    """`AN_DETERMINISTIC=0` is an escape hatch, not an eraser.

    A one-off render that does not need to be reproducible is a real thing; a
    render that quietly forgets it was non-reproducible is not.
    """
    from an.adapters.cutout.render import _determinism_report

    monkeypatch.setenv(AN_DETERMINISTIC_ENV_VAR, "0")
    report = _determinism_report(_FakePage(_clean_report(stage_filter_count=1)))
    assert report["violations"], "the breach must still be recorded"
    assert report["enforced"] is False


def test_a_probe_that_cannot_run_is_a_breach_not_a_pass(monkeypatch):
    """A runtime too old to answer must not be read as answering "fine"."""
    from an.adapters.cutout.render import CutoutRenderError, _determinism_report

    monkeypatch.delenv(AN_DETERMINISTIC_ENV_VAR, raising=False)
    with pytest.raises(CutoutRenderError):
        _determinism_report(_FakePage(RuntimeError("anDeterminismReport is not a function")))


def test_a_clean_report_is_returned_for_provenance(monkeypatch):
    """Collected on every render, not only under the flag.

    The blink phases and the filter inventory belong in the metrics ledger's
    provenance row; a fact recorded only when a flag is set is a fact missing
    from every row that matters.
    """
    from an.adapters.cutout.render import _determinism_report

    monkeypatch.delenv(AN_DETERMINISTIC_ENV_VAR, raising=False)
    report = _determinism_report(_FakePage(_clean_report()))
    assert report["violations"] == []
    assert report["blink_phases"] == {"charlie": 0.123}
