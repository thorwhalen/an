"""Pose: a snapshot of property values to apply to a scene graph.

A pose is the universal output of animation evaluation. Channels evaluate to
a (target, property, value) triple; clips collect them into a `Pose`; the
timeline merges multiple clips' poses; finally `apply_pose` mutates the scene
graph.

>>> from an.adapters.cutout.scene import Node, SceneGraph
>>> from an.adapters.cutout.transform import TransformParams
>>> root = Node("r")
>>> _ = root.add_child(Node("c"))
>>> graph = SceneGraph(root)
>>> apply_pose(graph, {("r/c", "x"): 42.0})
>>> graph["r/c"].params.x
42.0
"""

from __future__ import annotations

from typing import Any, TypeAlias

from an.adapters.cutout.scene import SceneGraph
from an.adapters.cutout.transform import TransformParams


#: Mapping of (target_path, property_name) -> value.
Pose: TypeAlias = dict[tuple[str, str], Any]


#: Properties this evaluator applies — anything not in this set raises.
#:
#: This must agree with ``applyProperty``'s switch in
#: ``an/data/cutout_runtime/runtime.js``, which is the evaluator that actually
#: renders; ``tests/test_loud_discards.py`` pins the two together. The list had
#: drifted — it was missing ``viseme`` and ``alpha``, both of which the runtime
#: has applied for some time — because nothing on the render path calls
#: ``apply_pose``, so no failure could surface the gap.
_ALLOWED_NODE_PROPS: frozenset[str] = frozenset(
    {
        "x",
        "y",
        "rotation",  # radians at the IR boundary; rotation_rad is its alias
        "rotation_rad",
        "scale_x",
        "scale_y",
        "skew_x",
        "skew_y",
        "pivot_x",
        "pivot_y",
        "alpha",
        "viseme",
    }
)


def apply_pose(graph: SceneGraph, pose: Pose) -> None:
    """Apply ``pose`` to ``graph`` in place. Marks affected subtrees dirty.

    Unknown targets are silently skipped (so optional channels don't crash a
    render of an early-state scene). Unknown properties on a known target
    raise ``KeyError``.
    """
    if not pose:
        return
    # Group by target so we patch each node once.
    by_target: dict[str, dict[str, Any]] = {}
    for (target, prop), value in pose.items():
        by_target.setdefault(target, {})[prop] = value
    for target, props in by_target.items():
        if target not in graph:
            continue
        node = graph[target]
        # Translate "rotation" alias to "rotation_rad" for convenience.
        normalized: dict[str, Any] = {}
        for k, v in props.items():
            if k == "rotation":
                normalized["rotation_rad"] = v
            elif k in _ALLOWED_NODE_PROPS:
                normalized[k] = v
            else:
                raise KeyError(
                    f"unknown pose property {k!r} for target {target!r}; "
                    f"known: {sorted(_ALLOWED_NODE_PROPS)}"
                )
        if normalized:
            node.set_param(**normalized)


def merge_poses(*poses: Pose) -> Pose:
    """Merge multiple poses with **override semantics** (later wins per key).

    Used by the timeline to combine concurrent clips on the same target. For
    additive blending, a later phase will introduce a separate ``add_poses``.

    >>> merge_poses({("a", "x"): 1.0}, {("a", "x"): 2.0, ("a", "y"): 3.0})
    {('a', 'x'): 2.0, ('a', 'y'): 3.0}
    """
    out: Pose = {}
    for p in poses:
        out.update(p)
    return out
