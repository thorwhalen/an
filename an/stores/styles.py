"""Styles store — visual style presets (color palette, line weight, fonts)."""

from __future__ import annotations

from an.stores._common import JsonDirStore


class StylesStore(JsonDirStore):
    """Pure-JSON style descriptors."""
