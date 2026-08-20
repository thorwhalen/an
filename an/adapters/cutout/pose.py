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

import dataclasses as _dataclasses

from typing import Any, TypeAlias

from an.adapters.cutout.scene import SceneGraph
from an.adapters.cutout.transform import TransformParams


#: Mapping of (target_path, property_name) -> value.
Pose: TypeAlias = dict[tuple[str, str], Any]


#: Properties this evaluator can apply — anything else raises an informative
#: ``KeyError`` naming the known set.
#:
#: **Derived from what it can actually do**, not from what the JS runtime does.
#: An earlier pass widened this to match ``applyProperty``'s switch, on the
#: theory that two evaluators should agree. They should — but this one routes
#: every allowed property into ``TransformParams``, which has no ``alpha`` and
#: no ``viseme`` field, so "agreeing" turned a typed, actionable ``KeyError``
#: into a raw ``TypeError`` from a dataclass constructor. Advertising a
#: capability you do not have is the same defect class as discarding one you do.
#:
#: ``rotation`` is accepted as an alias for ``rotation_rad`` at the IR boundary.
#:
#: The honest relationship is a SUBSET, and ``tests/test_loud_discards.py``
#: asserts it as one, with the gap named. Closing the gap means giving
#: ``TransformParams`` the fields — which is a Wave 5 question, since this whole
#: module is off the render path (see ``UNRENDERED_PROPS``).
_ALLOWED_NODE_PROPS: frozenset[str] = frozenset(
    {f.name for f in _dataclasses.fields(TransformParams)} | {"rotation"}
)

#: Properties the JS runtime applies that this in-memory evaluator cannot.
#:
#: Declared rather than implied, so the parity test can assert the gap is
#: exactly this and not something that crept in.
UNRENDERED_PROPS: frozenset[str] = frozenset({"alpha", "viseme"})


def apply_pose(graph: SceneGraph, pose: Pose) -> None:
    """Apply ``pose`` to ``graph`` in place. Marks affected subtrees dirty.

    Unknown targets AND unknown properties both raise ``KeyError``.

    The target half used to be a silent skip, justified as "so optional channels
    don't crash a render of an early-state scene" — the same reasoning the JS
    runtime used, and the same outcome: a mistyped path animated nothing, with no
    diagnostic, and looked wired. The two evaluators now behave alike.
    """
    if not pose:
        return
    # Group by target so we patch each node once.
    by_target: dict[str, dict[str, Any]] = {}
    for (target, prop), value in pose.items():
        by_target.setdefault(target, {})[prop] = value
    for target, props in by_target.items():
        if target not in graph:
            raise KeyError(
                f"pose targets unknown node {target!r}; known: {sorted(graph)}"
            )
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
