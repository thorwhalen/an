"""Cutout-style 2D animation backend.

The render path is ``compile.py`` → ``serialize.py`` → ``render.py`` →
``runtime.js`` (the browser evaluates and applies every frame). The Python
evaluation chain (``easing``/``channel``/``clip``/``timeline``) is kept as the
executable spec of the runtime's semantics, pinned by node-backed parity tests;
application lives in ``runtime.js`` alone (an#86).

>>> from an.adapters.cutout import CutoutRenderer, compile_shot
>>> CutoutRenderer().name
'cutout'
"""

from an.adapters.cutout.compile import compile_shot
from an.adapters.cutout.serialize import (
    AnimationClipJSON,
    AssetJSON,
    AssetResolutionJSON,
    AssetsJSON,
    ChannelJSON,
    CutoutSceneJSON,
    KeyframeJSON,
    NodeJSON,
    PlacedClipJSON,
    TimelineJSON,
    TrackJSON,
    VisualJSON,
)
from an.adapters.cutout.render import CutoutRenderer, CutoutRenderError

__all__ = [
    "CutoutRenderer",
    "CutoutRenderError",
    "compile_shot",
    "CutoutSceneJSON",
    "NodeJSON",
    "VisualJSON",
    "AnimationClipJSON",
    "ChannelJSON",
    "KeyframeJSON",
    "TimelineJSON",
    "TrackJSON",
    "PlacedClipJSON",
    "AssetsJSON",
    "AssetJSON",
    "AssetResolutionJSON",
]


# Register on import so `an.adapters.list_renderers()` finds it.
from an.adapters._base import register_renderer as _register_renderer

_register_renderer(CutoutRenderer())
