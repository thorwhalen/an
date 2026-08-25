"""Manim / Remotion / Whiteboard adapter skeletons.

Tests the registry wiring, can_render dispatch, and the error messages
they produce when their backends aren't available.
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

import pytest

from an import build_project_mall
from an.adapters import (
    ManimRenderer,
    RemotionRenderer,
    WhiteboardRenderer,
    list_renderers,
)
from an.adapters._base import RenderContext
from an.adapters.manim_adapter import ManimRenderError
from an.adapters.remotion_adapter import RemotionRenderError
from an.adapters.whiteboard import WhiteboardRenderError
from an.ir.schema import Shot


def test_all_four_backends_registered():
    names = list_renderers()
    for expected in ("cutout", "manim", "remotion", "whiteboard"):
        assert expected in names, f"missing renderer {expected!r}"


def test_can_render_routes_by_style():
    cases = [
        (ManimRenderer(), "manim"),
        (RemotionRenderer(), "motion_graphics"),
        (WhiteboardRenderer(), "whiteboard"),
    ]
    for renderer, style in cases:
        assert renderer.can_render(Shot(id="x", renderer=style, duration=1.0))
        assert not renderer.can_render(Shot(id="x", renderer="cutout", duration=1.0))


def test_whiteboard_render_raises_clear_stub_error():
    with tempfile.TemporaryDirectory() as d:
        mall = build_project_mall(d, ensure=True)
        ctx = RenderContext(mall=mall, work_dir=Path(d))
        shot = Shot(id="x", renderer="whiteboard", duration=1.0)
        with pytest.raises(WhiteboardRenderError, match="stub"):
            WhiteboardRenderer().render(shot, ctx)


def test_remotion_render_raises_clear_stub_error_when_npx_present():
    if shutil.which("npx") is None:
        pytest.skip("npx missing — different code path tested elsewhere")
    with tempfile.TemporaryDirectory() as d:
        mall = build_project_mall(d, ensure=True)
        ctx = RenderContext(mall=mall, work_dir=Path(d))
        shot = Shot(id="x", renderer="motion_graphics", duration=1.0)
        with pytest.raises(RemotionRenderError, match="skeleton"):
            RemotionRenderer().render(shot, ctx)


@pytest.mark.skipif(shutil.which("manim") is not None, reason="manim is installed")
def test_manim_render_errors_when_binary_missing():
    with tempfile.TemporaryDirectory() as d:
        mall = build_project_mall(d, ensure=True)
        ctx = RenderContext(mall=mall, work_dir=Path(d))
        shot = Shot(id="x", renderer="manim", duration=0.5)
        with pytest.raises(ManimRenderError, match="manim CLI"):
            ManimRenderer().render(shot, ctx)


@pytest.mark.skipif(shutil.which("manim") is None, reason="manim not installed")
def test_manim_render_produces_mp4_when_installed():
    """Smoke: when manim is installed, the placeholder script renders."""
    with tempfile.TemporaryDirectory() as d:
        mall = build_project_mall(d, ensure=True)
        ctx = RenderContext(mall=mall, work_dir=Path(d))
        shot = Shot(id="m1", renderer="manim", duration=0.25)
        result = ManimRenderer().render(shot, ctx)
        assert result.mp4_path.exists()
        assert result.mp4_path.stat().st_size > 0
