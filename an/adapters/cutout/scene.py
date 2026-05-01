"""Scene graph for cutout characters and props.

The scene graph is a tree of `Node`s. For rigid cutout, **bones ARE nodes** —
no separate skeleton structure (per `report 5` recommendation #1). A `Node`
owns a local transform (authoring `TransformParams`), an optional `Visual`
(what to actually draw), zero or more named children, and zero or more named
**slots** (attachment points for swappable visuals).

World transforms are computed lazily with a per-node dirty flag. Mutating a
node's local transform marks the node + all its descendants dirty; reading a
world transform walks ancestors as needed and caches the result.

Path access uses slash-delimited keys (``"charlie/torso/left_arm"``). The
graph implements ``MutableMapping[str, Node]`` for ergonomic lookup.

>>> root = Node(name="charlie")
>>> torso = root.add_child(Node(name="torso"))
>>> arm = torso.add_child(Node(name="left_arm"))
>>> graph = SceneGraph(root)
>>> "charlie/torso/left_arm" in graph
True
>>> graph["charlie/torso/left_arm"] is arm
True
"""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Iterator, MutableMapping
from dataclasses import dataclass, field
from typing import Any, Literal

from an.adapters.cutout.transform import Matrix3x3, TransformParams


# -----------------------------------------------------------------------------
# Visual — what to actually draw at a node
# -----------------------------------------------------------------------------


@dataclass(slots=True)
class Visual:
    """Drawable content attached to a node.

    For Phase 2A only ``"sprite"`` and ``"rect"`` are meaningful; the JS
    runtime in 2B will turn these into PixiJS sprites / graphics.
    """

    kind: Literal["sprite", "rect"] = "rect"
    texture_id: str | None = None  # asset id; None for rect
    width: float = 50.0
    height: float = 50.0
    anchor_x: float = 0.5  # local pivot for the visual itself ([0,1] of width)
    anchor_y: float = 0.5
    color: str = "#888888"  # for rect; ignored for sprite
    current_attachment: str | None = None  # slot-driven: which sub-attachment is shown


# -----------------------------------------------------------------------------
# Node
# -----------------------------------------------------------------------------


@dataclass(slots=True)
class Slot:
    """Attachment point on a node.

    A slot has a local offset from its host node and an optional
    ``current_attachment`` key that picks which named visual to display.
    Skin lookup happens at compile/serialize time.
    """

    name: str
    x: float = 0.0
    y: float = 0.0
    rotation_rad: float = 0.0
    current_attachment: str | None = None


class Node:
    """A scene-graph node: local transform + optional visual + children + slots.

    Use ``add_child`` to attach children — it sets the parent link and marks
    the new subtree dirty.
    """

    __slots__ = (
        "name",
        "params",
        "visual",
        "_parent",
        "_children",
        "_slots",
        "_world_dirty",
        "_world_cached",
    )

    def __init__(
        self,
        name: str,
        params: TransformParams | None = None,
        visual: Visual | None = None,
    ) -> None:
        self.name = name
        self.params: TransformParams = params or TransformParams()
        self.visual: Visual | None = visual
        self._parent: Node | None = None
        self._children: OrderedDict[str, Node] = OrderedDict()
        self._slots: dict[str, Slot] = {}
        self._world_dirty: bool = True
        self._world_cached: Matrix3x3 = Matrix3x3.identity()

    # -- Tree operations ------------------------------------------------------

    @property
    def parent(self) -> "Node | None":
        return self._parent

    def add_child(self, child: "Node") -> "Node":
        """Append ``child`` to this node's children. Returns ``child``."""
        if child.name in self._children:
            raise ValueError(
                f"node {self.name!r} already has a child named {child.name!r}"
            )
        self._children[child.name] = child
        child._parent = self
        _mark_subtree_dirty(child)
        return child

    @property
    def children(self) -> "list[Node]":
        return list(self._children.values())

    @property
    def slots(self) -> dict[str, Slot]:
        return self._slots

    def add_slot(self, slot: Slot) -> Slot:
        """Register a slot on this node. Returns the slot for chaining."""
        self._slots[slot.name] = slot
        return slot

    # -- Transform mutation + lazy world transform ----------------------------

    def set_params(self, params: TransformParams) -> None:
        """Replace local params and mark this subtree dirty."""
        self.params = params
        _mark_subtree_dirty(self)

    def set_param(self, **changes: Any) -> None:
        """Patch individual params and mark dirty.

        >>> n = Node("n")
        >>> n.set_param(x=5.0, rotation_rad=0.1)
        >>> n.params.x, round(n.params.rotation_rad, 6)
        (5.0, 0.1)
        """
        cur = self.params
        new_kwargs = {
            "x": cur.x,
            "y": cur.y,
            "rotation_rad": cur.rotation_rad,
            "scale_x": cur.scale_x,
            "scale_y": cur.scale_y,
            "skew_x": cur.skew_x,
            "skew_y": cur.skew_y,
            "pivot_x": cur.pivot_x,
            "pivot_y": cur.pivot_y,
        }
        new_kwargs.update(changes)
        self.params = TransformParams(**new_kwargs)
        _mark_subtree_dirty(self)

    def world_transform(self) -> Matrix3x3:
        """Return the cached world transform, recomputing if dirty."""
        if not self._world_dirty:
            return self._world_cached
        local = Matrix3x3.from_params(self.params)
        if self._parent is None:
            self._world_cached = local
        else:
            self._world_cached = self._parent.world_transform() @ local
        self._world_dirty = False
        return self._world_cached

    def __repr__(self) -> str:
        return f"Node({self.name!r}, children={list(self._children.keys())})"


def _mark_subtree_dirty(node: Node) -> None:
    """Mark ``node`` and all descendants as needing a world-transform recompute."""
    stack: list[Node] = [node]
    while stack:
        n = stack.pop()
        n._world_dirty = True
        stack.extend(n._children.values())


# -----------------------------------------------------------------------------
# SceneGraph: MutableMapping facade keyed by slash-paths
# -----------------------------------------------------------------------------


class SceneGraph(MutableMapping[str, Node]):
    """`MutableMapping` view over a tree of `Node`s.

    Keys are slash-delimited paths from the root node. The root's own name is
    the first segment of every path.
    """

    def __init__(self, root: Node) -> None:
        self._root = root

    @property
    def root(self) -> Node:
        return self._root

    # -- MutableMapping interface --------------------------------------------

    def __getitem__(self, path: str) -> Node:
        node = self._lookup(path, missing_ok=True)
        if node is None:
            raise KeyError(path)
        return node

    def __setitem__(self, path: str, node: Node) -> None:
        """Attach ``node`` at ``path`` (parent path must already exist)."""
        if not path:
            raise KeyError("empty path")
        parts = path.split("/")
        if len(parts) == 1:
            if parts[0] != self._root.name:
                raise KeyError(f"cannot replace root with different name {parts[0]!r}")
            self._root = node
            return
        parent = self._lookup("/".join(parts[:-1]), missing_ok=False)
        leaf_name = parts[-1]
        # Replace if exists; otherwise append.
        if leaf_name in parent._children:
            parent._children[leaf_name] = node
            node._parent = parent
            _mark_subtree_dirty(node)
        else:
            node.name = leaf_name
            parent.add_child(node)

    def __delitem__(self, path: str) -> None:
        if not path or "/" not in path:
            raise KeyError(f"cannot delete root or empty path: {path!r}")
        parts = path.split("/")
        parent = self._lookup("/".join(parts[:-1]), missing_ok=False)
        leaf_name = parts[-1]
        if leaf_name not in parent._children:
            raise KeyError(path)
        del parent._children[leaf_name]

    def __iter__(self) -> Iterator[str]:
        """Depth-first iteration of all paths in the tree."""
        for path, _node in _walk(self._root, prefix=""):
            yield path

    def __len__(self) -> int:
        return sum(1 for _ in self)

    def __contains__(self, path: object) -> bool:
        if not isinstance(path, str):
            return False
        return self._lookup(path, missing_ok=True) is not None

    # -- Helpers --------------------------------------------------------------

    def _lookup(self, path: str, *, missing_ok: bool) -> Node | None:
        if not path:
            if missing_ok:
                return None
            raise KeyError("empty path")
        parts = path.split("/")
        if parts[0] != self._root.name:
            if missing_ok:
                return None
            raise KeyError(path)
        node: Node = self._root
        for part in parts[1:]:
            if part not in node._children:
                if missing_ok:
                    return None
                raise KeyError(path)
            node = node._children[part]
        return node

    def __repr__(self) -> str:
        return f"SceneGraph(root={self._root.name!r}, nodes={len(self)})"


def _walk(node: Node, *, prefix: str) -> Iterator[tuple[str, Node]]:
    full = f"{prefix}/{node.name}" if prefix else node.name
    yield full, node
    for child in node._children.values():
        yield from _walk(child, prefix=full)
