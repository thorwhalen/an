"""Pose: apply_pose mutates scene graph; merge_poses overrides."""

from __future__ import annotations

import pytest

from an.adapters.cutout.pose import apply_pose, merge_poses
from an.adapters.cutout.scene import Node, SceneGraph


def _two_node_graph() -> SceneGraph:
    root = Node("r")
    root.add_child(Node("c"))
    return SceneGraph(root)


def test_apply_pose_sets_x_and_y():
    g = _two_node_graph()
    apply_pose(g, {("r/c", "x"): 5.0, ("r/c", "y"): -3.0})
    node = g["r/c"]
    assert node.params.x == 5.0
    assert node.params.y == -3.0


def test_apply_pose_marks_dirty():
    g = _two_node_graph()
    _ = g["r/c"].world_transform()
    assert g["r/c"]._world_dirty is False
    apply_pose(g, {("r/c", "x"): 1.0})
    assert g["r/c"]._world_dirty is True


def test_apply_pose_raises_on_an_unknown_target():
    """BEHAVIOUR CHANGE (#15): unknown targets used to be tolerated.

    The old contract was "channels may target absent nodes", which sounds
    permissive and in practice meant a mistyped path animated nothing with no
    diagnostic. The JS runtime made the same choice for the same stated reason
    and has now been changed too; these two evaluators must agree, and silence
    is the wrong thing for them to agree on.

    Listing the known nodes is the point — a typo is usually one character away
    from a real path.
    """
    g = _two_node_graph()
    with pytest.raises(KeyError, match="unknown node"):
        apply_pose(g, {("r/missing", "x"): 9.0})
    apply_pose(g, {("r/c", "x"): 7.0})
    assert g["r/c"].params.x == 7.0


def test_apply_pose_unknown_property_raises():
    g = _two_node_graph()
    with pytest.raises(KeyError, match="unknown pose property"):
        apply_pose(g, {("r/c", "rgb"): "red"})


def test_apply_pose_rotation_alias_resolves_to_radians():
    g = _two_node_graph()
    apply_pose(g, {("r/c", "rotation"): 1.5})
    assert g["r/c"].params.rotation_rad == 1.5


def test_merge_poses_later_wins():
    a = {("n", "x"): 1.0}
    b = {("n", "x"): 2.0, ("n", "y"): 3.0}
    merged = merge_poses(a, b)
    assert merged == {("n", "x"): 2.0, ("n", "y"): 3.0}


def test_merge_poses_preserves_keys_only_in_one():
    a = {("a", "x"): 1.0}
    b = {("b", "y"): 2.0}
    merged = merge_poses(a, b)
    assert merged == {("a", "x"): 1.0, ("b", "y"): 2.0}


def test_apply_empty_pose_is_noop():
    g = _two_node_graph()
    apply_pose(g, {})  # no error, no change
