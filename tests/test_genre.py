"""The nw genre declaration stays honest and stays optional.

Both properties are load-bearing for hosts that catalog the federation's genres:
``import an`` must not drag in nw (so a host without nw can still use ``an``), and
the declared genre must not claim an engine it does not have.
"""

import subprocess
import sys

import pytest

nw = pytest.importorskip("nw")


def test_importing_an_does_not_import_nw():
    """``an`` gains no hard nw dependency from the genre module.

    Checked in a subprocess because the rest of this file imports nw, so an
    in-process ``sys.modules`` check would pass for the wrong reason.
    """
    code = "import an, sys; assert 'nw' not in sys.modules"
    assert subprocess.run([sys.executable, "-c", code]).returncode == 0


def test_genre_is_planned_and_claims_no_engine():
    from an.genre import CUTOUT_ANIMATION

    assert CUTOUT_ANIMATION.status == "planned"
    # an has no nw Transform pipeline and no registered nw.renderers strategy.
    # Declaring names that don't resolve would be a wiring bug, not a promise.
    assert CUTOUT_ANIMATION.transform_names == ()
    assert CUTOUT_ANIMATION.strategy_names == ()


def test_genre_registers_under_its_slug():
    from an.genre import CUTOUT_ANIMATION, CUTOUT_ANIMATION_SLUG

    assert nw.get_genre(CUTOUT_ANIMATION_SLUG) is CUTOUT_ANIMATION
