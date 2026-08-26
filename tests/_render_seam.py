"""Stop a cutout render at the ``compile_shot`` seam, in the default CI lane.

``CutoutRenderer.render`` does three environment-dependent things *before* it
compiles anything — it checks for ffmpeg, validates the supersample factor and
imports ``playwright.sync_api``. A seam guard that lets any of those run is a
guard **only on a developer machine**: in CI the render aborts before the seam,
the spy records nothing, and the assertion fails (or, worse, passes vacuously if
it was written as "no exception reached the seam").

That is not hypothetical. ``an#112``'s style-pack guard was written without this
and went red on the first CI run for exactly this reason, while passing locally
with ffmpeg and Chromium installed. Its sibling in ``test_loud_discards.py`` had
the pattern and stayed green — the difference was hand-copied stubbing, so this
module exists to stop the next guard from being written without it.

Usage::

    def test_something_reaches_the_compiler(monkeypatch):
        seam = stop_at_compile_shot(monkeypatch)
        with pytest.raises(seam.Stop):
            render_mod.CutoutRenderer().render(shot, ctx)
        assert seam.kwargs["style_pack"].name == "noir"

The ``pytest.raises`` is deliberately typed to ``seam.Stop`` rather than
``Exception``: a bare ``Exception`` swallows the missing-ffmpeg error too, which
turns "the seam was never reached" into a passing line and defers the failure to
whatever the test asserts next.
"""

from __future__ import annotations

import sys
import types
from dataclasses import dataclass, field
from typing import Any


class CompileShotReached(Exception):
    """Raised by the spy to abort the render at the seam under test.

    Nothing past the seam is exercised, so the render must not be allowed to
    continue into Playwright — which in the default lane is not installed.
    """


@dataclass
class RenderSeam:
    """What ``compile_shot`` was called with, and the sentinel that stopped it."""

    kwargs: dict[str, Any] = field(default_factory=dict)
    args: tuple = ()
    Stop: type[Exception] = CompileShotReached

    @property
    def reached(self) -> bool:
        """True once the spy has run. False means the render aborted earlier."""
        return bool(self.kwargs) or bool(self.args)


def stub_optional_render_deps(monkeypatch) -> None:
    """Make ``CutoutRenderer.render`` reach its first compile without ffmpeg or a browser.

    Stubs are installed only where the real thing is absent, so a machine that
    *has* Playwright still exercises the real import path.
    """
    from an.adapters.cutout import render as render_mod

    if "playwright.sync_api" not in sys.modules:
        pkg = types.ModuleType("playwright")
        api = types.ModuleType("playwright.sync_api")
        api.sync_playwright = lambda: None
        pkg.sync_api = api
        monkeypatch.setitem(sys.modules, "playwright", pkg)
        monkeypatch.setitem(sys.modules, "playwright.sync_api", api)

    monkeypatch.setattr(render_mod, "_ensure_ffmpeg_available", lambda: None)


def stop_at_compile_shot(monkeypatch) -> RenderSeam:
    """Replace ``compile_shot`` with a recording spy that aborts the render.

    Returns the :class:`RenderSeam` the spy writes into. Also stubs the optional
    dependencies checked ahead of the seam, so the guard runs in the default
    lane — see the module docstring for why that is the whole point.
    """
    from an.adapters.cutout import render as render_mod

    seam = RenderSeam()
    stub_optional_render_deps(monkeypatch)

    def _spy(*args, **kwargs):
        seam.args = args
        seam.kwargs.update(kwargs)
        raise seam.Stop

    monkeypatch.setattr(render_mod, "compile_shot", _spy)
    return seam
