"""Schema-level tests: roundtrip stability, version envelope, forward-compat."""

from __future__ import annotations

import json

from an import COMPATIBLE_VERSION, SCHEMA_VERSION
from an.ir.schema import (
    Camera,
    Dialogue,
    Meta,
    SceneIR,
    Shot,
    SequenceAction,
    TweenAction,
)


def test_default_scene_carries_version():
    scene = SceneIR()
    assert scene.version == SCHEMA_VERSION
    assert scene.compatible_version == COMPATIBLE_VERSION
    assert scene.kind == "SceneIR"


def test_roundtrip_stability():
    scene = SceneIR(
        meta=Meta(title="x", duration=5.0),
        timeline=[
            Shot(
                id="s1",
                style="cutout",
                duration=5.0,
                camera=Camera(move="push_in"),
                dialogue=[Dialogue(speaker="charlie", text="hi")],
            )
        ],
    )
    blob = scene.model_dump_json()
    reloaded = SceneIR.model_validate_json(blob)
    assert reloaded.model_dump() == scene.model_dump()


def test_extra_fields_allowed_on_inbound():
    raw = {
        "version": SCHEMA_VERSION,
        "compatible_version": COMPATIBLE_VERSION,
        "kind": "SceneIR",
        "meta": {"title": "x", "future_field": "ignored-but-kept"},
        "timeline": [],
        "future_top_level": {"foo": "bar"},
    }
    scene = SceneIR.model_validate(raw)
    # Top-level future field is preserved via extra="allow".
    assert hasattr(scene, "future_top_level") or "future_top_level" in scene.model_extra


def test_action_discriminated_union():
    seq = SequenceAction(
        children=[
            TweenAction(target="a", property="x", to_value=10, duration=1.0),
            TweenAction(target="a", property="x", to_value=0, duration=1.0),
        ]
    )
    blob = seq.model_dump()
    assert blob["kind"] == "sequence"
    assert blob["children"][0]["kind"] == "tween"
    # Round-trip through the union type.
    from an.ir.schema import Shot
    shot = Shot(id="s", duration=2.0, actions=[seq])
    reloaded = Shot.model_validate(json.loads(shot.model_dump_json()))
    assert reloaded.actions[0].kind == "sequence"
    assert reloaded.actions[0].children[0].kind == "tween"


def test_meta_resolution_defaults_present():
    m = Meta()
    assert m.resolution.width > 0
    assert m.resolution.height > 0
    assert m.fps > 0
