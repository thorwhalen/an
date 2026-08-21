"""The determinism probe, against a real browser (an#37).

`tests/test_determinism_perimeter.py` proves the *rule*; this proves the
*probe* — that `runtime.js` actually exposes it, that a live report carries
every field the Python checker requires, and that a real render passes its own
perimeter. Split because the rule must run in the default lane and this cannot:
it needs a headless Chromium, so it lives behind the browser marker
(`run-browser-tests`, or an on-demand run).

Deliberately NOT a module-level `importorskip`, which would delete these from
collection rather than skip them (an#22).
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from an.adapters._base import RenderContext
from an.adapters.cutout.render import CutoutRenderer
from an.determinism import _REQUIRED_FIELDS
from an.ir.schema import AssetRef, Shot

pytestmark = [pytest.mark.browser, pytest.mark.ffmpeg]

#: Small and short on purpose: the probe runs once per shot regardless, so the
#: frames are pure cost.
SHOT = Shot(
    id="s1",
    style="cutout",
    duration=0.25,
    entities=[AssetRef(kind="character", id="charlie", store="characters", ref="c-v1")],
)
RESOLUTION = (160, 120)
FPS = 8


@pytest.fixture(scope="module")
def live_report() -> dict:
    """Render one shot for real and hand back the probe's report."""
    with tempfile.TemporaryDirectory() as work:
        ctx = RenderContext(
            mall={}, work_dir=Path(work), fps=FPS, resolution=RESOLUTION
        )
        result = CutoutRenderer().render(SHOT, ctx)
        return dict(result.provenance["determinism"])


def test_a_real_render_passes_its_own_determinism_perimeter(live_report):
    """Reaching here at all is half the assertion — the render raises on a breach.

    If this fails on a clean checkout the perimeter has genuinely moved: some
    new code attached a filter, started a ticker, or repointed the capture at
    `preview.html`. That is the whole point.
    """
    assert live_report["violations"] == []
    assert live_report["enforced"] is True
    assert "error" not in live_report, live_report.get("error")


def test_the_live_probe_reports_every_field_the_checker_requires(live_report):
    """Two files, one contract, and neither imports the other.

    The source-level twin of this lives in the default lane; this is the one
    that cannot be fooled by a probe that parses but does not run.
    """
    missing = [f for f in _REQUIRED_FIELDS if f not in live_report]
    assert not missing, f"the live probe did not report {missing}"
    assert live_report["page"].endswith("index.html")


def test_the_blink_phase_is_stamped_per_entity(live_report):
    """The hazard nothing warns about, recorded so a rename is a visible diff."""
    assert live_report["blink_phases"], (
        "a one-character scene must report its blink phase — the value that "
        "silently re-phases every blink when a corpus entity is renamed"
    )
    assert set(live_report["blink_phases"]) == {"charlie"}
    assert all(0.0 <= v < 1.0 for v in live_report["blink_phases"].values())
