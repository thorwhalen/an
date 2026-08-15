"""iterate(): path-walking unit tests + live Claude API test."""

from __future__ import annotations

import importlib.util
import json
import os
import tempfile
from pathlib import Path

import pytest

from an.iterate import (
    IterateError,
    Patch,
    _apply_one,
    _apply_patches_to_ir,
)
from .conftest import requires_live_api
from an.ir.schema import (
    Dialogue,
    Meta,
    Resolution,
    SceneIR,
    Shot,
)


# -----------------------------------------------------------------------------
# Pure path-walking unit tests (no API needed)
# -----------------------------------------------------------------------------


def _doc():
    return {
        "meta": {"title": "X", "duration": 5.0},
        "timeline": [
            {"id": "s1", "duration": 3.0, "dialogue": [{"text": "hi"}]},
            {"id": "s2", "duration": 2.0, "dialogue": []},
        ],
    }


def test_set_top_level():
    doc = _doc()
    _apply_one(doc, Patch(op="set", path="meta/title", value="Y"))
    assert doc["meta"]["title"] == "Y"


def test_set_nested_list_index():
    doc = _doc()
    _apply_one(doc, Patch(op="set", path="timeline/0/duration", value=10.0))
    assert doc["timeline"][0]["duration"] == 10.0


def test_set_deep_nested():
    doc = _doc()
    _apply_one(doc, Patch(op="set", path="timeline/0/dialogue/0/text", value="hello"))
    assert doc["timeline"][0]["dialogue"][0]["text"] == "hello"


def test_append_to_list():
    doc = _doc()
    _apply_one(
        doc,
        Patch(op="append", path="timeline/0/dialogue", value={"text": "more"}),
    )
    assert len(doc["timeline"][0]["dialogue"]) == 2
    assert doc["timeline"][0]["dialogue"][1]["text"] == "more"


def test_delete_dict_key():
    doc = _doc()
    _apply_one(doc, Patch(op="delete", path="meta/duration"))
    assert "duration" not in doc["meta"]


def test_delete_list_entry():
    doc = _doc()
    _apply_one(doc, Patch(op="delete", path="timeline/1"))
    assert len(doc["timeline"]) == 1
    assert doc["timeline"][0]["id"] == "s1"


def test_set_creates_new_dict_key():
    """set on a missing dict key creates it (sensible for adding emotion etc)."""
    doc = _doc()
    _apply_one(
        doc,
        Patch(op="set", path="timeline/0/dialogue/0/emotion", value="happy"),
    )
    assert doc["timeline"][0]["dialogue"][0]["emotion"] == "happy"


def test_invalid_path_raises():
    doc = _doc()
    with pytest.raises(IterateError, match="missing key"):
        _apply_one(doc, Patch(op="set", path="meta/nope/x", value=1))


def test_append_to_non_list_raises():
    doc = _doc()
    with pytest.raises(IterateError, match="not a list"):
        _apply_one(doc, Patch(op="append", path="meta", value={"x": 1}))


def test_delete_missing_key_raises():
    doc = _doc()
    with pytest.raises(IterateError, match="can't delete missing"):
        _apply_one(doc, Patch(op="delete", path="meta/never_existed"))


def test_apply_patches_to_ir_round_trips_through_schema():
    scene = SceneIR(
        meta=Meta(title="orig", duration=3.0),
        timeline=[
            Shot(
                id="s1",
                style="cutout",
                duration=3.0,
                dialogue=[Dialogue(speaker="x", text="old")],
            )
        ],
    )
    patches = [
        Patch(op="set", path="timeline/0/dialogue/0/text", value="new"),
        Patch(op="set", path="timeline/0/dialogue/0/emotion", value="happy"),
    ]
    new_dict = _apply_patches_to_ir(scene, patches)
    new_scene = SceneIR.model_validate(new_dict)
    assert new_scene.timeline[0].dialogue[0].text == "new"
    assert new_scene.timeline[0].dialogue[0].emotion == "happy"


# -----------------------------------------------------------------------------
# Live Claude API test
# -----------------------------------------------------------------------------


@pytest.mark.live_api
@requires_live_api
@pytest.mark.skipif(
    importlib.util.find_spec("anthropic") is None
    or not os.environ.get("ANTHROPIC_API_KEY"),
    reason="needs anthropic SDK + ANTHROPIC_API_KEY",
)
def test_iterate_dialogue_text_change_live():
    """End-to-end: load a scene, ask Claude to rewrite a dialogue line."""
    from an import init
    from an.iterate import iterate
    from an.project import load

    with tempfile.TemporaryDirectory() as d:
        root = init(Path(d) / "demo")
        proj = load(root)
        proj.scene = SceneIR(
            meta=Meta(title="demo", duration=3.0),
            timeline=[
                Shot(
                    id="opener",
                    style="cutout",
                    duration=3.0,
                    dialogue=[
                        Dialogue(
                            speaker="charlie",
                            text="Hello there.",
                            emotion="neutral",
                        )
                    ],
                )
            ],
        )
        proj.mall["scenes"]["main"] = proj.scene

        result = iterate(
            root,
            "Rewrite Charlie's line to be enthusiastic and a bit longer.",
        )
        # The model should produce at least one patch on the dialogue line.
        assert result.patches, f"no patches returned: {result.summary}"
        any_dialogue_text_patch = any(
            "dialogue" in p.path and "text" in p.path for p in result.patches
        )
        assert any_dialogue_text_patch, (
            f"expected a patch on dialogue text; got {[p.path for p in result.patches]}"
        )
        # Persisted scene should reflect the changes.
        proj_reloaded = load(root)
        new_text = proj_reloaded.scene.timeline[0].dialogue[0].text
        assert new_text != "Hello there."
        # Affected shots should include the opener.
        assert "opener" in result.affected_shots


@pytest.mark.live_api
@requires_live_api
@pytest.mark.skipif(
    importlib.util.find_spec("anthropic") is None
    or not os.environ.get("ANTHROPIC_API_KEY"),
    reason="needs anthropic SDK + ANTHROPIC_API_KEY",
)
def test_iterate_dry_run_does_not_persist():
    """apply=False returns the new scene but doesn't write to mall."""
    from an import init
    from an.iterate import iterate
    from an.project import load

    with tempfile.TemporaryDirectory() as d:
        root = init(Path(d) / "demo2")
        proj = load(root)
        original = SceneIR(
            meta=Meta(title="orig", duration=2.0),
            timeline=[
                Shot(
                    id="s1",
                    style="cutout",
                    duration=2.0,
                    dialogue=[
                        Dialogue(speaker="x", text="orig", emotion="neutral")
                    ],
                )
            ],
        )
        proj.mall["scenes"]["main"] = original

        result = iterate(
            root,
            "Make x sound surprised.",
            apply=False,
        )
        # On disk, the scene should still be the original.
        proj_reloaded = load(root)
        assert proj_reloaded.scene.timeline[0].dialogue[0].text == "orig"
        # But the result should carry the new (proposed) scene.
        assert result.new_scene is not None
