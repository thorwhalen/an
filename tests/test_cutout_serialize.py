"""JSON contract for the cutout JS runtime: round-trip stability."""

from __future__ import annotations

import json

from an.adapters.cutout.serialize import (
    AnimationClipJSON,
    AssetJSON,
    AssetsJSON,
    ChannelJSON,
    CutoutSceneJSON,
    CutoutSceneMetaJSON,
    KeyframeJSON,
    NodeJSON,
    PlacedClipJSON,
    SlotJSON,
    TimelineJSON,
    TrackJSON,
    VisualJSON,
    from_dict,
    to_dict,
)


def _full_scene() -> CutoutSceneJSON:
    return CutoutSceneJSON(
        meta=CutoutSceneMetaJSON(fps=30, width=1920, height=1080, duration=5.0),
        scene=NodeJSON(
            name="root",
            children=[
                NodeJSON(
                    name="charlie",
                    visual=VisualJSON(kind="rect", width=80, height=120, color="#aabbcc"),
                    slots={"mouth": SlotJSON(name="mouth", x=0, y=15)},
                    children=[
                        NodeJSON(name="left_arm"),
                        NodeJSON(name="right_arm"),
                    ],
                )
            ],
        ),
        animations={
            "wave": AnimationClipJSON(
                name="wave",
                duration=1.0,
                loop_mode="loop",
                channels=[
                    ChannelJSON(
                        target="charlie/right_arm",
                        property="rotation",
                        keyframes=[
                            KeyframeJSON(time=0.0, value=0.0, easing="ease_in_out"),
                            KeyframeJSON(time=0.5, value=1.5),
                            KeyframeJSON(time=1.0, value=0.0),
                        ],
                    )
                ],
            )
        },
        timeline=TimelineJSON(
            duration=5.0,
            tracks=[
                TrackJSON(
                    target_root="charlie",
                    clips=[
                        PlacedClipJSON(
                            animation_id="wave", start_time=1.0, duration=2.0
                        )
                    ],
                )
            ],
        ),
        assets=AssetsJSON(
            textures={"head": AssetJSON(src="sprites/head.png", width=60, height=70)},
        ),
    )


def test_roundtrip_via_to_from_dict():
    s = _full_scene()
    s2 = from_dict(to_dict(s))
    assert s == s2


def test_roundtrip_via_json_string():
    s = _full_scene()
    blob = json.dumps(to_dict(s), sort_keys=True)
    s2 = from_dict(json.loads(blob))
    assert s == s2


def test_minimal_scene_construction():
    s = CutoutSceneJSON(
        scene=NodeJSON(name="root"),
        timeline=TimelineJSON(duration=1.0),
    )
    assert s.version == "0.1.0"
    assert s.meta.fps == 30
    assert s.scene.name == "root"
    assert s.timeline.tracks == []


def test_easing_can_be_string_or_list():
    kf1 = KeyframeJSON(time=0.0, value=0.0, easing="ease_in")
    kf2 = KeyframeJSON(time=0.0, value=0.0, easing=[0.42, 0.0, 0.58, 1.0])
    assert kf1.easing == "ease_in"
    assert kf2.easing == [0.42, 0.0, 0.58, 1.0]


def test_extra_fields_preserved():
    raw = {
        "version": "0.1.0",
        "meta": {"fps": 30, "width": 1920, "height": 1080, "duration": 1.0},
        "scene": {"name": "root", "future": "thing"},
        "timeline": {"duration": 1.0, "tracks": []},
        "future_top_field": [1, 2, 3],
    }
    s = from_dict(raw)
    # Future-top-field should round-trip through extra="allow".
    blob = to_dict(s)
    assert "future_top_field" in blob


def test_node_children_nested_round_trip():
    s = CutoutSceneJSON(
        scene=NodeJSON(
            name="r",
            children=[
                NodeJSON(name="a", children=[NodeJSON(name="aa"), NodeJSON(name="ab")]),
            ],
        ),
        timeline=TimelineJSON(duration=1.0),
    )
    s2 = from_dict(to_dict(s))
    assert s == s2
    assert s2.scene.children[0].children[0].name == "aa"
