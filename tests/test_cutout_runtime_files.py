"""Cutout runtime files: presence + helpers."""

from __future__ import annotations

from an.adapters.cutout.runtime_files import (
    runtime_dir,
    runtime_index_html,
    runtime_js,
)


def test_runtime_dir_exists():
    p = runtime_dir()
    assert p.is_dir()


def test_index_html_present_and_loads_runtime_js():
    p = runtime_index_html()
    assert p.is_file()
    text = p.read_text(encoding="utf-8")
    assert "runtime.js" in text
    assert "<canvas" in text


def test_runtime_js_present_with_public_api():
    p = runtime_js()
    assert p.is_file()
    text = p.read_text(encoding="utf-8")
    # The four documented globals
    for fn in ("anLoadScene", "anSetTime", "anCanvasReady", "anRuntimeVersion"):
        assert fn in text, f"runtime.js missing {fn!r}"
