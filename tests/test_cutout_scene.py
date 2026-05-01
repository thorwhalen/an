"""Cutout scene graph: tree, paths, lazy world transforms, slots."""

from __future__ import annotations

import math

import pytest

from an.adapters.cutout.scene import Node, SceneGraph, Slot, Visual
from an.adapters.cutout.transform import TransformParams


def _build_charlie() -> SceneGraph:
    root = Node("charlie")
    torso = root.add_child(Node("torso"))
    torso.add_child(Node("left_arm"))
    torso.add_child(Node("right_arm"))
    root.add_child(Node("head"))
    return SceneGraph(root)


def test_scene_graph_paths_dfs():
    graph = _build_charlie()
    paths = list(graph)
    assert paths == [
        "charlie",
        "charlie/torso",
        "charlie/torso/left_arm",
        "charlie/torso/right_arm",
        "charlie/head",
    ]


def test_scene_graph_lookup():
    graph = _build_charlie()
    arm = graph["charlie/torso/left_arm"]
    assert arm.name == "left_arm"
    assert "charlie/torso/right_arm" in graph
    assert "charlie/missing" not in graph
    with pytest.raises(KeyError):
        _ = graph["charlie/torso/no_such_arm"]


def test_scene_graph_len():
    graph = _build_charlie()
    assert len(graph) == 5


def test_world_transform_root_equals_local():
    root = Node("r", TransformParams(x=10, y=20))
    g = SceneGraph(root)
    w = g["r"].world_transform()
    assert w.tx == pytest.approx(10)
    assert w.ty == pytest.approx(20)


def test_world_transform_composes_with_parent():
    root = Node("r", TransformParams(x=10, y=0))
    child = root.add_child(Node("c", TransformParams(x=5, y=0)))
    assert root.world_transform().tx == pytest.approx(10)
    assert child.world_transform().tx == pytest.approx(15)


def test_set_param_marks_subtree_dirty():
    root = Node("r")
    a = root.add_child(Node("a"))
    b = a.add_child(Node("b"))
    # Force initial computation
    _ = b.world_transform()
    assert b._world_dirty is False
    root.set_param(x=100)
    # All descendants should now be dirty.
    assert root._world_dirty is True
    assert a._world_dirty is True
    assert b._world_dirty is True
    # New world transform reflects the change.
    assert b.world_transform().tx == pytest.approx(100)


def test_set_param_does_not_mark_unrelated_branch():
    root = Node("r")
    a = root.add_child(Node("a"))
    b = root.add_child(Node("b"))
    _ = a.world_transform()
    _ = b.world_transform()
    a.set_param(x=42)
    assert a._world_dirty is True
    assert b._world_dirty is False  # sibling untouched


def test_world_transform_with_rotation_chain():
    root = Node("r")
    child = root.add_child(Node("c", TransformParams(x=10, rotation_rad=math.pi / 2)))
    grand = child.add_child(Node("g", TransformParams(x=10)))
    # child rotates 90° then translates 10 in x → in world, child sits at
    # (10, 0) but its own +x direction is +y. So grandchild at local x=10
    # maps to world (10, 10).
    w = grand.world_transform()
    assert w.tx == pytest.approx(10, abs=1e-9)
    assert w.ty == pytest.approx(10, abs=1e-9)


def test_slots_attach_and_resolve():
    head = Node("head")
    head.add_slot(Slot(name="mouth", y=15))
    assert "mouth" in head.slots
    assert head.slots["mouth"].y == 15


def test_visual_defaults():
    v = Visual()
    assert v.kind == "rect"
    assert v.width > 0
    assert v.height > 0


def test_set_item_attaches_node_under_path():
    root = Node("r")
    g = SceneGraph(root)
    g["r/new"] = Node("placeholder")
    assert "r/new" in g
    assert g["r/new"].name == "new"  # name normalized to path leaf


def test_del_item_removes_node():
    g = _build_charlie()
    del g["charlie/head"]
    assert "charlie/head" not in g
    assert "charlie/torso" in g


def test_cannot_add_duplicate_child_name():
    root = Node("r")
    root.add_child(Node("a"))
    with pytest.raises(ValueError):
        root.add_child(Node("a"))


def test_root_path_lookup():
    g = _build_charlie()
    assert g["charlie"] is g.root


def test_world_transform_caches():
    """Second read on a clean node returns the cached matrix without recompute."""
    root = Node("r", TransformParams(x=1))
    w1 = root.world_transform()
    w2 = root.world_transform()
    assert w1 is w2  # cached object identity, since the dataclass is frozen
