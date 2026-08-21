"""The determinism perimeter: what must stay true for a render to be reproducible.

`an` renders by driving a headless Chromium and screenshotting a WebGL canvas.
Two classes of thing can make that non-reproducible, and they need different
treatment:

**Pinned inputs** — the rasteriser, the browser build, the encoder. Those are
*settings*, and they are pinned unconditionally in
:mod:`an.adapters.cutout.render` (an#31, an#34). Nothing here.

**Latent randomness** — machinery that is deterministic today by accident.
The vendored PixiJS carries `Math.random`, `Date.now`, `performance.now` and
`requestAnimationFrame` calls, and `NoiseFilter`'s default seed is
`Math.random()`. All of it is dormant because the runtime creates the app with
``autoStart: false`` and drives it with explicit ``app.render()`` calls, and
because nothing attaches a filter. Neither fact is written down anywhere that
would go red if it stopped being true. That is what this module watches.

The division of labour is deliberate: `runtime.js`'s ``anDeterminismReport``
**observes** and this module **judges**. So the rule is a pure function of a
plain dict — testable with no browser, no ffmpeg and no render — and changing
the rule is a Python diff rather than a runtime re-stage.

Enforcement is **on by default**, which is a deliberate deviation from the
issue's ``AN_DETERMINISTIC=1`` framing and follows the same reasoning an#31
used for the launch flags: a property that only holds when someone remembers to
export a variable is not a property of the system. An assertion nobody runs is
worse than no assertion, because the perimeter reads as guarded. The escape
hatch is the same variable read the other way — ``AN_DETERMINISTIC=0``.

>>> capture_violations({"page": "/index.html", "stage_filter_count": 0,
...                     "filtered_node_paths": [], "shared_ticker_started": False,
...                     "auto_start": False})
[]
"""

from __future__ import annotations

import os
from typing import Any, Mapping

#: Read as an OFF switch, not an on switch — see the module docstring.
AN_DETERMINISTIC_ENV_VAR: str = "AN_DETERMINISTIC"

#: The page the capture path must be on. `render.py` stages `preview.html`
#: into every work dir and it carries seven clock calls, so "which page did the
#: browser actually load" is a real question with a wrong answer available.
CAPTURE_PAGE: str = "index.html"

_FALSEY = frozenset({"0", "false", "no", "off"})


def determinism_enforced() -> bool:
    """Whether to refuse a render whose determinism perimeter has been breached.

    True unless :data:`AN_DETERMINISTIC_ENV_VAR` is explicitly falsey.

    >>> import os
    >>> os.environ.pop("AN_DETERMINISTIC", None) and None
    >>> determinism_enforced()
    True
    >>> os.environ["AN_DETERMINISTIC"] = "0"
    >>> determinism_enforced()
    False
    >>> del os.environ["AN_DETERMINISTIC"]
    """
    value = os.environ.get(AN_DETERMINISTIC_ENV_VAR)
    if value is None:
        return True
    return value.strip().lower() not in _FALSEY


def capture_violations(
    report: Mapping[str, Any], *, capture_page: str = CAPTURE_PAGE
) -> list[str]:
    """Return one sentence per breach of the perimeter; empty means clean.

    Each sentence says what was observed, why it makes frames non-reproducible,
    and what to do about it — because the reader of this message is the person
    who just added the thing, and "determinism violation" alone tells them
    nothing.

    A missing key is reported rather than defaulted: a report that does not
    carry a field cannot testify that the field is fine, and silently reading
    ``report.get("shared_ticker_started", False)`` would turn a stale runtime
    into a clean bill of health.

    >>> capture_violations({})[0].startswith("the determinism report is missing")
    True
    """
    missing = [k for k in _REQUIRED_FIELDS if k not in report]
    if missing:
        return [
            f"the determinism report is missing {sorted(missing)} — the staged "
            "runtime is older than this checker, so it cannot testify about "
            "those fields either way. Re-stage the runtime rather than reading "
            "the absence as a pass."
        ]

    violations: list[str] = []

    page = str(report["page"] or "")
    if not page.endswith(capture_page):
        violations.append(
            f"the browser captured from {page!r}, not {capture_page!r}. "
            "`preview.html` is staged into every work dir and carries seven "
            "clock calls (Date.now / performance.now / requestAnimationFrame), "
            "so capturing from it makes each frame a function of wall time. "
            f"Point the capture at {capture_page!r}."
        )

    if report["shared_ticker_started"] or report["auto_start"]:
        violations.append(
            "a PixiJS ticker is running (shared_ticker_started="
            f"{report['shared_ticker_started']}, auto_start={report['auto_start']}). "
            "The capture path steps time explicitly with `anSetTime` + "
            "`app.render()`; a running ticker advances the scene between the "
            "seek and the screenshot, so a frame becomes a function of how long "
            "the screenshot took. Create the app with `autoStart: false` and "
            "leave `PIXI.Ticker.shared` stopped."
        )

    filtered = list(report["filtered_node_paths"])
    if report["stage_filter_count"] or filtered:
        where = filtered or ["the stage"]
        violations.append(
            f"a PixiJS filter is attached to {where}. This is a tripwire, not a "
            "ban: the determinism perimeter just acquired a new input and "
            "nothing has verified it. `NoiseFilter` seeds itself from "
            "`Math.random()`, so it randomises every frame; a blur is fine. "
            "Decide which this is, re-bless any golden frames it moves, and "
            f"then relax the check — or set {AN_DETERMINISTIC_ENV_VAR}=0 for a "
            "one-off render that does not need to be reproducible."
        )

    return violations


#: Every field :func:`capture_violations` reads. Absence is a violation, not a
#: default — see that function's docstring.
_REQUIRED_FIELDS: tuple[str, ...] = (
    "page",
    "shared_ticker_started",
    "auto_start",
    "stage_filter_count",
    "filtered_node_paths",
)
