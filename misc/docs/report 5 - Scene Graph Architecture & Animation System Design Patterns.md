# Scene Graph Architecture & Animation System Design Patterns

A technical guide for designing a structured 2D cutout animation system with a Python authoring layer, JSON scene description, and JS/TS rendering.

---

## Table of Contents

1. [Scene Graph Architecture](#1-scene-graph-architecture)
2. [Character Rigging for 2D](#2-character-rigging-for-2d)
3. [Animation System Architecture](#3-animation-system-architecture)
4. [Timeline / Sequencing Architecture](#4-timeline--sequencing-architecture)
5. [Python Architecture Sketch](#5-python-architecture-sketch)
6. [References](#references)

---

## 1. Scene Graph Architecture

### 1.1 Data Structure Design

A scene graph is a directed acyclic graph (usually a tree in practice) where each node encapsulates:

- **Identity**: a unique name within its parent's children (forming a path like `charlie/torso/left_arm/hand`).
- **Local transform**: position, rotation, scale, and optionally skew, relative to the parent.
- **Computed world transform**: the product of all ancestor transforms, yielding the node's final position in world space.
- **Visual content**: optional — could be a sprite, a vector shape, a mesh, or nothing (a "group" node that only organizes children).
- **Children**: an ordered list of child nodes.
- **Attachment points (slots)**: named transforms where other elements can be "plugged in" — e.g., a `hand_grip` slot on a hand node where a sword or cup attaches.

The fundamental insight is that the tree structure *is* the coordinate system hierarchy. Each node defines a local coordinate space. Children live in their parent's space. This is the same structure used in SVG's `<g>` nesting, DOM layout, game engine scene trees, and OpenGL's model-view stack [1].

**Node structure (conceptual):**

```
Node:
    name: str
    local_transform: AffineTransform2D
    visual: Optional[Visual]          # sprite, shape, mesh, or None
    children: OrderedDict[str, Node]  # name → child node
    slots: Dict[str, AffineTransform2D]  # named attachment points
    _world_transform: Optional[AffineTransform2D]  # cached, lazily computed
    _dirty: bool                      # invalidation flag
    parent: Optional[Node]            # back-reference for upward traversal
```

The `children` being an `OrderedDict` is deliberate: child order determines draw order (painter's algorithm — later children draw on top), and keying by name gives O(1) access.

**Slots** are transforms relative to the node's local space. When you query a slot's world position, you compose: `world_transform(node) @ slot_transform`. This lets you attach props, particles, or IK targets to specific points on a character.

### 1.2 Transform Composition

Every 2D affine transform can be represented as a 3×3 homogeneous matrix [2]:

```
| a  c  tx |     | sx*cos(θ)  -sy*sin(θ)  tx |
| b  d  ty |  =  | sx*sin(θ)   sy*cos(θ)  ty |   (for rotate+scale+translate)
| 0  0   1 |     |     0           0        1 |
```

Where `(a, b, c, d)` encode rotation, scale, and skew; `(tx, ty)` encode translation. The six values `(a, b, c, d, tx, ty)` are sufficient — the bottom row is always `[0, 0, 1]`.

**Composition order.** If node C is a child of B which is a child of A:

```
world_C = world_A @ local_B @ local_C
```

Matrix multiplication is **left-to-right from root to leaf** (or equivalently, the rightmost transform is applied first to the vertex). This is the standard convention in both SVG and most 2D engines [3].

**Pivot / anchor points.** A pivot (or "anchor" or "origin") is the point around which rotation and scaling occur. Without pivots, rotation always happens around `(0, 0)` in local space. To rotate around an arbitrary pivot `(px, py)`:

```
T_pivot = Translate(-px, -py)
T_unpivot = Translate(px, py)
effective = T_unpivot @ Rotate(θ) @ Scale(sx, sy) @ T_pivot
```

In practice, you bake this into the node's local transform computation:

```python
def compute_local_matrix(t: TransformParams) -> Matrix3x3:
    """TRS composition with pivot."""
    # 1. Translate to world position
    # 2. Rotate around pivot
    # 3. Scale around pivot
    # 4. Offset by -pivot to put the origin at the pivot
    M = (
        translate(t.x, t.y)
        @ translate(t.pivot_x, t.pivot_y)
        @ rotate(t.angle)
        @ scale(t.sx, t.sy)
        @ skew(t.skew_x, t.skew_y)
        @ translate(-t.pivot_x, -t.pivot_y)
    )
    return M
```

This is exactly how CSS `transform-origin` works [4] and how Spine/Spriter represent pivots.

**Decomposition.** Sometimes you need to extract (tx, ty, rotation, scaleX, scaleY) from a matrix. For a matrix without skew:

```
tx, ty = M[0,2], M[1,2]
sx = sqrt(a² + b²)
sy = sqrt(c² + d²)
θ = atan2(b, a)
```

With skew, decomposition is more complex — use QR decomposition or the method from the CSS Transforms spec [5].

### 1.3 Dirty Flag Pattern

Recomputing every node's world transform every frame is O(n). With the dirty flag pattern, you only recompute transforms that actually changed [6].

**The mechanism:**

1. When a node's local transform changes, mark it dirty: `node._dirty = True`.
2. Propagate dirtiness **downward** to all descendants (since their world transforms depend on ancestors). This is O(subtree_size), but you can short-circuit: if a descendant is already dirty, its subtree is already marked.
3. When someone *reads* `node.world_transform`, check if dirty. If so, recompute from parent's world transform and cache the result. Mark clean.

**Two strategies:**

| Strategy | On write | On read | Best when |
|---|---|---|---|
| **Eager top-down** | Mark subtree dirty | Recompute lazily on access | Random access patterns — you might not read every node |
| **Lazy pull** | Mark only this node dirty | Walk up to find nearest clean ancestor, recompute chain down | Few nodes read per frame (e.g., only rendering visible nodes) |

For animation (where you typically update many nodes and then render all visible ones), **eager invalidation + lazy recomputation** is the standard approach. During the render pass, you do a top-down traversal anyway, so you recompute in correct parent-before-child order naturally.

**Implementation sketch:**

```python
def set_local_transform(self, transform):
    self._local_transform = transform
    self._invalidate()


def _invalidate(self):
    if not self._dirty:
        self._dirty = True
        for child in self.children.values():
            child._invalidate()


@property
def world_transform(self):
    if self._dirty:
        if self.parent is None:
            self._world_transform = self._local_transform
        else:
            self._world_transform = self.parent.world_transform @ self._local_transform
        self._dirty = False
    return self._world_transform
```

Note: the recursive `world_transform` property call on parent is safe because we traverse top-down during rendering — the parent will be resolved before the child. If you need random access outside a traversal, the lazy pull naturally walks up to the clean ancestor.

### 1.4 Spatial Queries

Two core queries on a 2D scene:

**Hit testing** ("what's at pixel (x, y)?"): Traverse the tree back-to-front (reverse draw order). For each node with visual content, transform the query point into the node's local space using the *inverse* world transform, then test against the local-space geometry (bounding box, polygon, or pixel alpha). Return the first (topmost) hit.

```python
def hit_test(node, world_point):
    # Reverse iterate children (topmost first)
    for child in reversed(node.children.values()):
        hit = hit_test(child, world_point)
        if hit is not None:
            return hit
    # Test self
    if node.visual is not None:
        local_point = node.world_transform.inverse() @ world_point
        if node.visual.contains(local_point):
            return node
    return None
```

**Culling** ("what's visible in this viewport rectangle?"): Each node maintains an axis-aligned bounding box (AABB) in world space. Before descending into a subtree, check if the node's AABB intersects the viewport. If not, skip the entire subtree.

**Bounding box hierarchy.** Each node's world AABB encloses its own visual content *plus* all descendants' AABBs. Computing this bottom-up:

```
node.world_aabb = union(node.visual.local_aabb.transformed(node.world_transform),
                        *(child.world_aabb for child in node.children.values()))
```

This gives you a **bounding volume hierarchy** (BVH) [7] for free — the scene graph *is* the BVH. Culling complexity is O(log n) in the average case (you prune entire subtrees that are off-screen).

For more advanced spatial queries (e.g., "which characters are within 100 pixels of the cursor?"), you can augment with a spatial hash or quadtree over leaf-node AABBs.

### 1.5 Scene Graph as a Mapping

The scene graph can be presented as a `Mapping[str, Node]` where keys are slash-delimited paths:

```python
scene["charlie"]  # → Node (charlie root)
scene["charlie/torso/left_arm/hand"]  # → Node (charlie's left hand)
list(scene)  # → ["charlie", "charlie/torso", ...]
```

This is a *flattened view* of the tree — it doesn't change the underlying tree structure, it just provides an alternative access pattern. This is analogous to how a filesystem can be accessed both as a tree (`os.walk`) and as a flat mapping of paths to files.

**Implementation approach — a "path store" facade:**

```python
from collections.abc import Mapping


class SceneGraph(Mapping):
    """A Mapping view over a scene tree, keyed by slash-separated paths."""

    def __init__(self, root: Node):
        self._root = root

    def __getitem__(self, path: str) -> Node:
        node = self._root
        for part in path.split("/"):
            try:
                node = node.children[part]
            except KeyError:
                raise KeyError(path)
        return node

    def __iter__(self):
        """Yield all paths in depth-first order."""
        yield from self._iter_paths(self._root, prefix="")

    def _iter_paths(self, node, prefix):
        for name, child in node.children.items():
            path = f"{prefix}{name}" if not prefix else f"{prefix}/{name}"
            yield path
            yield from self._iter_paths(child, path)

    def __len__(self):
        return sum(1 for _ in self)

    def __contains__(self, path):
        try:
            self[path]
            return True
        except KeyError:
            return False
```

This gives you the full `Mapping` protocol: `scene["path"]`, `"path" in scene`, `for path in scene`, `len(scene)`, `scene.keys()`, `scene.values()`, `scene.items()`. You can wrap it in `dol` stores for caching, transformation, or filtering.

**Enhanced access patterns** (via `__getitem__` overloading):

- **Glob patterns**: `scene["charlie/*/hand"]` → returns all nodes matching the glob (a filtered iteration).
- **Slice by depth**: `scene["charlie":2]` → all descendants of charlie up to depth 2.
- **Attribute access** as syntactic sugar: `scene.charlie.torso.left_arm` via `__getattr__` on a proxy object.

The Mapping interface also opens the door to **virtual nodes** — computed nodes that don't exist in the tree but are synthesized on access (e.g., `scene["charlie/center_of_mass"]` returning a computed position).

---

## 2. Character Rigging for 2D

### 2.1 Bone Hierarchy

A **bone** in 2D rigging is a transform node that controls one or more visual nodes. Conceptually, bones form their own tree (the "skeleton" or "armature"), and each bone has a mapping to the visual nodes it influences.

**Relationship between bones and the scene graph:**

There are two common approaches:

1. **Bones *are* scene graph nodes.** The bone hierarchy is embedded in the scene graph — bones are invisible nodes whose children are the visual parts they control. This is the simpler model and works well for rigid cutout animation (each bone controls exactly one sprite).

2. **Bones are a parallel structure.** The bone tree is separate from the visual tree. A **binding** maps each visual node to one or more bones with weights. This is necessary when bones deform meshes (skinning) rather than just rigidly positioning parts.

For a cutout animation system, **approach 1 is the default** — bones and parts are the same tree. You only need approach 2 when you add mesh deformation.

**Forward kinematics (FK)** is what the scene graph already does: set a bone's local rotation, and all descendant bones and their visual children transform accordingly. "FK" is just "the scene graph working normally." When an animator rotates the upper_arm bone by 30°, the lower_arm, hand, and all fingers rotate with it because they're descendants.

### 2.2 Inverse Kinematics (IK) in 2D

IK solves the reverse problem: given a **target position** for an end effector (e.g., the hand), compute the rotations of ancestor bones (upper_arm, lower_arm) that place the hand at the target [8].

#### CCD (Cyclic Coordinate Descent)

CCD is the simplest iterative IK algorithm [9]:

```
Given: chain of bones [root, ..., end_effector], target position
Repeat until converged or max_iterations:
    For each bone from end_effector to root:
        1. Compute vector from bone's world position to end_effector's world position
        2. Compute vector from bone's world position to target
        3. Compute angle between these two vectors
        4. Rotate the bone by that angle (clamped by joint constraints)
```

**Properties:**
- Very simple to implement (the core is ~20 lines).
- Converges reliably but can produce "curling" artifacts — the chain tends to wind around the target rather than reaching naturally.
- Works well for chains of 2–4 bones. For longer chains, FABRIK is preferred.
- O(n × iterations) where n is chain length.

#### FABRIK (Forward And Backward Reaching Inverse Kinematics)

FABRIK is a position-based solver that works by alternately "reaching" from end to root and root to end [10]:

```
Given: chain of joint positions [p0, p1, ..., pn], target T, bone lengths [d1, ..., dn]

Repeat until converged:
    # Forward pass (end → root):
    Set pn = T
    For i from n-1 to 0:
        direction = normalize(pi - pi+1)
        pi = pi+1 + direction * d(i+1)

    # Backward pass (root → end):
    Set p0 = original_root_position
    For i from 1 to n:
        direction = normalize(pi - pi-1)
        pi = pi-1 + direction * di
```

**Properties:**
- Produces more natural-looking poses than CCD.
- Very fast convergence (often 1–3 iterations).
- Naturally handles joint constraints by clamping positions after each step.
- Works with multiple end effectors and branching chains.

#### When to use each

| Factor | CCD | FABRIK |
|---|---|---|
| Chain length 2–3 | Fine | Fine |
| Chain length 4+ | Curling artifacts | Natural results |
| Joint angle constraints | Easy to add per-bone | Requires position clamping |
| Implementation effort | Minimal | Moderate |
| Typical use | Simple arm/leg IK | Full body, tentacles, tails |

**Integration with the scene graph update cycle:**

IK runs as a **post-processing step** after FK. The evaluation order is:

1. Apply animation data (set local transforms from keyframes) — this is FK.
2. Run IK solvers — overwrite bone rotations to satisfy IK targets.
3. Run constraints (see §2.4).
4. Recompute world transforms (dirty flag resolves this).
5. Render.

### 2.3 Mesh Deformation / Skinning in 2D

In rigid cutout animation, each part is a sprite that moves as a unit. But sometimes you want **smooth bending** — a character's torso that curves, an arm that has a visible elbow bend, a face that deforms during expressions.

**2D mesh deformation** (skinning) achieves this:

1. The visual content is a **mesh** — a set of vertices forming triangles, textured with the character's artwork.
2. Each vertex is **bound** to one or more bones, with a weight per bone.
3. At render time, each vertex's position is computed as a weighted sum of where each influencing bone would place it.

**Linear Blend Skinning (LBS) in 2D** [11]:

```
For each vertex v with rest position p_rest:
    p_world = Σ (weight_i * bone_i.world_transform @ bone_i.bind_inverse @ p_rest)
```

Where:
- `bone_i.world_transform` is the bone's current world matrix.
- `bone_i.bind_inverse` is the inverse of the bone's world matrix at bind time (when the mesh was authored).
- The product `world @ bind_inverse` gives the bone's *change* from rest pose.
- Weights for each vertex sum to 1.0.

In 2D, the matrices are 3×3 (homogeneous 2D), and the mesh vertices are 2D points. The triangles are rasterized with UV mapping to the original texture.

**Rigid vs. mesh — when to use which:**

| Approach | Look | Use when |
|---|---|---|
| Rigid parts | Classic cutout / paper puppet | Stylized animation, South Park-style, simple characters |
| Mesh deformation | Smooth, organic bending | Characters need visible joint bending, cloth simulation, face morphs |
| Hybrid | Mix of both | Body is rigid cutout, face has mesh deformation for expressions |

For a cutout animation system, **start with rigid parts** and add mesh deformation as an optional capability. The architecture should support both — a node's visual content is either a `Sprite` (rigid) or a `Mesh` (deformable), both implementing a common `Visual` interface.

### 2.4 Constraints

Constraints are rules applied after FK/IK that enforce relationships between nodes:

**Point constraint** (position lock): Node A's position follows Node B's position (or a weighted blend of multiple sources). Used for: props following a hand, eyes following a gaze target.

**Aim constraint** (rotation lock): Node A rotates to "look at" a target point or node. Used for: eyes tracking a point, a spotlight following a character.

**Path constraint**: A node follows a path (Bézier curve, polyline), parameterized by a 0–1 value. Used for: characters walking along a curved path, a camera following a track.

**Parent constraint**: A node dynamically re-parents — follows a specified node as if it were a child, but without actually changing the scene graph structure. Used for: picking up objects (the object "parents" to the hand without restructuring the tree).

**Constraint resolution** is a fixed-point iteration problem. Constraints can depend on each other (A aims at B which is point-constrained to C which has IK). The standard approach [12]:

1. **Order constraints by dependency.** If constraint X depends on a node that constraint Y modifies, Y must evaluate first. This is a topological sort of the constraint graph.
2. **Iterate.** Evaluate all constraints in order. If any constraint moved a node that another constraint depends on, re-evaluate (up to a max iteration count).
3. **Converge or clamp.** In practice, 1–3 iterations suffice for well-authored rigs.

The evaluation pipeline becomes:

```
animation data → FK → IK → constraints (iterated) → world transforms → render
```

---

## 3. Animation System Architecture

### 3.1 Keyframe Representation

A **keyframe** is a value at a specific time with interpolation metadata:

```
Keyframe:
    time: float              # in seconds (or normalized 0–1 within a clip)
    value: T                 # the property value (float, Vector2, Color, etc.)
    interpolation: InterpInfo  # how to interpolate FROM this keyframe TO the next
```

An **animation channel** is a sorted sequence of keyframes for a single property of a single target:

```
Channel:
    target_path: str         # e.g., "charlie/torso/left_arm"
    property: str            # e.g., "rotation", "position.x", "opacity"
    keyframes: List[Keyframe]  # sorted by time
```

**Evaluation at time t** uses binary search to find the surrounding keyframes, then interpolates:

```python
def evaluate(channel, t):
    """Evaluate channel value at time t."""
    keys = channel.keyframes
    if t <= keys[0].time:
        return keys[0].value
    if t >= keys[-1].time:
        return keys[-1].value

    # Binary search for the interval [keys[i], keys[i+1]] containing t
    i = bisect_right([k.time for k in keys], t) - 1

    k0, k1 = keys[i], keys[i + 1]
    # Normalize t to 0–1 within this segment
    local_t = (t - k0.time) / (k1.time - k0.time)
    # Apply timing curve
    eased_t = k0.interpolation.ease(local_t)
    # Interpolate value
    return lerp(k0.value, k1.value, eased_t)
```

This is O(log n) per evaluation due to the binary search. For playback (where t advances monotonically), you can cache the current segment index and only re-search when t moves past the current segment — amortized O(1).

**Value types and their lerp functions:**

| Type | lerp |
|---|---|
| `float` | `a + (b - a) * t` |
| `Vector2` | Component-wise float lerp |
| `Color` | Component-wise in linear RGB (not sRGB!) |
| `Angle` | Shortest-path angle lerp (`a + shortest_delta(a, b) * t`) |
| `bool` / `enum` | Step (snap at t=0 or t=1, no interpolation) |

### 3.2 Interpolation and Easing

There are two distinct concepts often conflated:

1. **Value interpolation**: How the value changes between two keyframes (linear, cubic Hermite, Catmull-Rom).
2. **Timing curve (easing)**: How time progresses through the interpolation (ease-in, ease-out, cubic bezier timing).

They compose: `value = value_interp(eased_time)`.

#### Easing functions

An easing function maps `[0, 1] → [0, 1]` (time-in to time-out) [13]:

- **Linear**: `f(t) = t`.
- **Step**: `f(t) = 0 if t < 1, else 1`.
- **Ease-in** (slow start): `f(t) = t²` (quadratic), `t³` (cubic), etc.
- **Ease-out** (slow end): `f(t) = 1 - (1-t)²`.
- **Ease-in-out**: `f(t) = 3t² - 2t³` (smoothstep), or compose ease-in and ease-out.
- **Cubic bezier**: The CSS/SVG standard. Defined by two control points `(x1, y1, x2, y2)`. The curve is a parametric cubic from `(0,0)` to `(1,1)`. To evaluate at time `t`, you solve for the parameter `u` such that `bezier_x(u) = t`, then return `bezier_y(u)`. This requires a numerical solve (Newton's method or bisection) [14].

The cubic bezier representation is powerful because it subsumes most common easings:

- Linear: `(0.0, 0.0, 1.0, 1.0)`
- Ease-in: `(0.42, 0, 1.0, 1.0)`
- Ease-out: `(0, 0, 0.58, 1.0)`
- Ease-in-out: `(0.42, 0, 0.58, 1.0)`

**Making easing a function parameter** is key for extensibility:

```python
# Easing is just a Callable[[float], float]
Easing = Callable[[float], float]


def linear(t: float) -> float:
    return t


def ease_in_quad(t: float) -> float:
    return t * t


def cubic_bezier(x1, y1, x2, y2) -> Easing:
    """Return an easing function for the given bezier control points."""

    def ease(t):
        # Solve for u, return y
        ...

    return ease
```

#### Value interpolation methods

Beyond simple lerp, you sometimes want the value curve itself to be smooth:

- **Catmull-Rom spline**: Given keyframes ..., K(i-1), K(i), K(i+1), K(i+2), ..., the value at time t between K(i) and K(i+1) is a cubic polynomial that passes through all four neighboring keyframes. This gives C¹ continuity (smooth velocity) automatically [15].

- **Cubic Hermite**: Each keyframe stores explicit tangent values (in-tangent, out-tangent). The curve between K(i) and K(i+1) is a cubic Hermite spline. This gives direct artistic control over the curve shape. After Effects and most animation tools use this model [16].

### 3.3 Animation Clips

A **clip** is a self-contained animation unit:

```
Clip:
    name: str
    duration: float          # seconds
    channels: List[Channel]  # all animated properties
    loop_mode: LoopMode      # once, loop, ping_pong
```

**Loop modes:**
- `once`: play from 0 to duration, then stop.
- `loop`: `effective_t = t % duration`.
- `ping_pong`: play forward, then backward. `effective_t` oscillates.

**Clip evaluation:**

```python
def evaluate_clip(clip, t):
    """Return a dict of {(target_path, property): value} at time t."""
    effective_t = apply_loop_mode(t, clip.duration, clip.loop_mode)
    return {
        (ch.target_path, ch.property): evaluate(ch, effective_t) for ch in clip.channels
    }
```

The return type — a mapping from (target, property) to value — is the **animation pose**. This is a key abstraction: a pose is a snapshot of all animated properties at a moment in time.

### 3.4 Animation Blending

When multiple animations play simultaneously, their poses must be combined.

**Override blending** (standard): A higher-priority layer completely replaces a lower-priority layer for any property it touches. If the walk animation sets `left_arm.rotation` and the wave animation also sets `left_arm.rotation`, the wave wins.

**Additive blending**: The animation's values are *added* to the base pose rather than replacing. If the base pose has `left_arm.rotation = 30°` and an additive "breathe" animation outputs `+2°`, the result is `32°`. This is essential for layering subtle effects over any base animation.

**Weighted blending**: Each layer has a weight (0–1). The final value is the weighted combination:

```
final_value = Σ (weight_i * pose_i[property]) / Σ weight_i    (normalized)
```

For rotations, use spherical linear interpolation (slerp) rather than naive averaging. In 2D, angle averaging must account for wraparound.

**Layer stack architecture:**

```
LayerStack:
    layers: List[AnimationLayer]   # ordered by priority (lowest first)

AnimationLayer:
    source: AnimationSource        # clip, blend tree, or state machine
    weight: float                  # 0.0 to 1.0
    blend_mode: BlendMode          # override or additive
    mask: Optional[Set[str]]       # which target paths this layer affects
```

The **mask** is critical for practical use: a "wave" animation should only affect the right arm, not override the legs' walk cycle. The mask is a set of target paths (or a subtree root like `"charlie/torso/right_arm"`).

**Evaluation order:**

1. Evaluate each layer's source to produce a pose.
2. Merge poses bottom-up through the stack:
   - For override layers: the layer's values replace the accumulated pose (for properties in its mask).
   - For additive layers: the layer's values are added to the accumulated pose.
   - Weight is applied before merging.

### 3.5 Animation State Machines

An animation state machine (ASM) manages which clip(s) play based on game/scene conditions [17, 18]:

```
StateMachine:
    states: Dict[str, State]
    transitions: List[Transition]
    current_state: str
    parameters: Dict[str, Any]     # named variables that drive transitions

State:
    name: str
    source: AnimationSource        # clip, blend tree, or sub-state-machine
    speed: float

Transition:
    from_state: str                # or "*" for "any state"
    to_state: str
    conditions: List[Condition]    # parameter-based conditions
    duration: float                # blend duration in seconds
    blend_curve: Easing            # how the crossfade progresses
    interrupt: bool                # can this transition be interrupted by another?
```

**Conditions** are predicates on parameters:

```
Condition:
    parameter: str
    operator: '==' | '!=' | '>' | '<' | 'trigger'
    value: Any
```

The `trigger` type is a one-shot boolean that automatically resets after being consumed (useful for "jump" or "punch" — fire once and return to idle).

**Evaluation algorithm:**

```python
def update(state_machine, dt):
    sm = state_machine
    # Check transitions from current state
    for transition in sm.transitions:
        if transition.from_state in (sm.current_state, "*"):
            if all(evaluate_condition(c, sm.parameters) for c in transition.conditions):
                sm.begin_transition(transition)
                break

    if sm.in_transition:
        # Crossfade: blend from old state to new state
        sm.transition_time += dt
        blend_t = sm.transition_time / sm.active_transition.duration
        blend_t = sm.active_transition.blend_curve(clamp01(blend_t))
        pose_old = sm.old_state.source.evaluate(...)
        pose_new = sm.new_state.source.evaluate(...)
        pose = blend_poses(pose_old, pose_new, blend_t)
        if blend_t >= 1.0:
            sm.complete_transition()
        return pose
    else:
        return sm.current_state.source.evaluate(...)
```

**Hierarchical state machines**: A state can contain a sub-state machine. Example: a "locomotion" state contains sub-states for walk, jog, run. A "combat" state contains sub-states for idle, attack, block. Transitions between top-level states handle the big mode switches; sub-states handle variations within a mode [18].

### 3.6 Blend Trees

A blend tree is a tree of animation sources blended by continuous parameters [17]:

**1D blend tree**: A single parameter (e.g., `speed`) selects between clips:

```
speed:  0.0          0.5           1.0
clips:  idle -------- walk -------- run
```

At `speed = 0.3`, the output is `blend(idle, walk, 0.6)` (lerp between the two nearest clips).

**2D blend tree**: Two parameters (e.g., `speed`, `direction`) select between clips arranged in 2D space. The blending uses barycentric interpolation or radial basis function weighting over the nearest clips.

**Data structure:**

```
BlendTree1D:
    parameter: str              # name of the driving parameter
    entries: List[BlendEntry]   # sorted by threshold

BlendEntry:
    threshold: float            # parameter value where this clip is at full weight
    source: AnimationSource     # clip or nested blend tree
```

**Evaluation:**

```python
def evaluate_blend_tree_1d(tree, parameters, t):
    param_value = parameters[tree.parameter]
    entries = tree.entries

    # Find surrounding entries
    i = bisect_right([e.threshold for e in entries], param_value) - 1
    i = clamp(i, 0, len(entries) - 2)

    e0, e1 = entries[i], entries[i + 1]
    blend_t = (param_value - e0.threshold) / (e1.threshold - e0.threshold)
    blend_t = clamp01(blend_t)

    pose0 = e0.source.evaluate(t)
    pose1 = e1.source.evaluate(t)
    return blend_poses(pose0, pose1, blend_t)
```

Blend trees compose: an entry's source can be another blend tree, a state machine, or a raw clip. This recursion gives you expressive control (Unity calls this "blend tree within blend tree" [17]).

### 3.7 Procedural Animation

Procedural animation generates motion from code/parameters rather than keyframe data. It's essential for natural-feeling characters — even keyframe-animated characters benefit from procedural secondary motion.

#### Walk cycle generation

A walk cycle can be parameterized by gait parameters [19]:

```
GaitParams:
    step_length: float      # how far each foot travels
    step_height: float      # how high the foot lifts
    speed: float            # steps per second
    hip_sway: float         # lateral hip oscillation amplitude
    hip_bob: float          # vertical hip oscillation amplitude
    arm_swing: float        # arm swing amplitude
    lean: float             # forward lean angle
    phase_offset: float     # left-right phase offset (0.5 = standard walk)
```

The walk generator produces sinusoidal oscillations for each body part, phased relative to the step cycle:

```python
def walk_pose(t: float, params: GaitParams) -> Pose:
    cycle = (t * params.speed) % 1.0  # 0–1 cycle phase

    hip_y = params.hip_bob * sin(cycle * 2π * 2)  # two bobs per cycle
    hip_x = params.hip_sway * sin(cycle * 2π)

    left_foot_phase = cycle
    right_foot_phase = (cycle + params.phase_offset) % 1.0

    left_foot_y = foot_arc(left_foot_phase, params.step_height)
    right_foot_y = foot_arc(right_foot_phase, params.step_height)
    # ... etc for all body parts

    return Pose({
        ("hips", "position.y"): hip_y,
        ("hips", "position.x"): hip_x,
        ("left_leg/foot", "position.y"): left_foot_y,
        # ...
    })
```

This procedural pose can be blended with keyframe animation — e.g., use procedural walk as a base, overlay keyframe adjustments for personality.

#### Spring-damper systems for secondary motion

Hair, clothing, tails, and accessories should respond to the character's movement with springy, organic lag [20]:

```
SpringState:
    position: float    # current value
    velocity: float    # current velocity

def spring_update(state, target, stiffness, damping, dt):
    """Damped harmonic oscillator step."""
    force = stiffness * (target - state.position)
    force -= damping * state.velocity
    state.velocity += force * dt
    state.position += state.velocity * dt
    return state
```

Attach a spring to each "secondary" node (ponytail segment, scarf segment, bouncing accessory). The spring's target tracks the parent bone's position/rotation. The spring's output becomes the node's actual position/rotation, introducing natural-feeling delay and oscillation.

**Critically damped springs** (where `damping = 2 * sqrt(stiffness)`) are often preferred — they converge to the target as fast as possible without oscillation, giving a "smooth follow" effect rather than bouncy oscillation [21].

#### Noise-based idle animation

Even when "idle," real characters have subtle motion — breathing, weight shifting, blinking, micro-sway. Procedural noise generates this:

```python
def idle_overlay(t: float, params: IdleParams) -> Pose:
    """Subtle noise-based idle motion. Added to any base animation."""
    breathing = sin(t * params.breath_rate * 2π) * params.breath_amplitude
    sway_x = perlin_noise(t * params.sway_speed) * params.sway_amplitude
    sway_rot = perlin_noise(t * params.sway_speed + 100) * params.sway_rot_amplitude
    # Blink: periodic with random jitter
    blink = blink_pattern(t, params.blink_interval, params.blink_jitter)

    return Pose({
        ("torso", "scale.y"): 1.0 + breathing,
        ("hips", "position.x"): sway_x,
        ("hips", "rotation"): sway_rot,
        ("head/left_eye", "scale.y"): blink,
        ("head/right_eye", "scale.y"): blink,
    })
```

Perlin or simplex noise gives smooth, organic-looking randomness — far better than `random()` [22]. The idle overlay is applied as an additive animation layer with low weight.

---

## 4. Timeline / Sequencing Architecture

### 4.1 Timeline Data Model

The timeline is the "score" of the animation — it orchestrates what happens when across all entities:

```
Timeline:
    duration: float
    tracks: List[Track]

Track:
    target: str                    # entity path or ID
    type: TrackType                # animation, audio, event, visibility, ...
    clips: List[PlacedClip]        # clips placed at specific times

PlacedClip:
    clip: Clip                     # the animation clip
    start_time: float              # when it starts on the timeline
    duration: float                # how long it plays (can differ from clip.duration via speed)
    speed: float                   # playback speed multiplier
    blend_in: float                # fade-in duration (seconds)
    blend_out: float               # fade-out duration (seconds)
```

**Evaluation at time t:**

```python
def evaluate_timeline(timeline, t):
    """Return the complete scene state at time t."""
    result = {}
    for track in timeline.tracks:
        track_pose = Pose()
        for placed_clip in track.clips:
            clip_active, local_t, weight = placed_clip.evaluate_timing(t)
            if clip_active:
                clip_pose = evaluate_clip(placed_clip.clip, local_t)
                track_pose = blend_into(track_pose, clip_pose, weight)
        result[track.target] = track_pose
    return result
```

**Overlapping clips** on the same track blend together using their blend_in / blend_out ramps as weights. This enables smooth crossfading between clips on a single track.

The timeline evaluation produces a mapping from entity paths to poses — exactly the same `Dict[(target, property), value]` structure as a single clip evaluation, just aggregated across all tracks.

### 4.2 Events and Triggers

Discrete events fire at specific times on the timeline:

```
EventTrack(Track):
    events: List[TimelineEvent]

TimelineEvent:
    time: float
    type: str                      # "sound", "spawn", "callback", "marker"
    data: Dict[str, Any]           # type-specific payload
```

**Event detection during playback:**

The challenge is that events are instantaneous (they happen at a *point* in time), but the playback loop advances in discrete steps. You must detect all events in the interval `(previous_t, current_t]`:

```python
def collect_events(event_track, t_prev, t_now):
    """Return all events in the half-open interval (t_prev, t_now]."""
    return [e for e in event_track.events if t_prev < e.time <= t_now]
```

**Edge cases:**
- **Scrubbing** (jumping to arbitrary time): Define policy — either fire all events in the jumped interval (risky, may trigger hundreds of sounds) or fire none (treat scrub as silent seek). Most systems fire none on backward scrub, and optionally fire on forward scrub within a threshold.
- **Looping**: When the timeline loops, events near the end and beginning must both fire in the correct order within a single frame's time step.
- **Pausing/resuming**: Events should not re-fire on resume. Track the "last processed time" rather than relying on wall clock.

### 4.3 Scene Transitions

Transitions between scenes (or shots, or acts) are placed on the timeline as special clips:

**Cut**: Instantaneous switch. At time t, scene A is visible; at t+ε, scene B is visible. Implemented as a visibility toggle event.

**Crossfade**: Both scenes render simultaneously for the fade duration. Scene A's opacity ramps from 1→0 while scene B's ramps from 0→1. Requires rendering both scenes into off-screen buffers and compositing.

**Wipe**: A mask (e.g., a moving edge, circle, or custom shape) progressively reveals scene B behind scene A. The mask is parameterized by a 0–1 progress value driven by the timeline.

**Data model:**

```
TransitionClip:
    type: 'cut' | 'crossfade' | 'wipe'
    duration: float
    from_scene: str
    to_scene: str
    easing: Easing                 # for crossfade/wipe progress
    wipe_params: Optional[WipeParams]  # shape, direction, softness
```

Transitions live on a dedicated **transition track** in the timeline, ensuring they're evaluated separately from entity animation.

### 4.4 Director Pattern

The **director** is a high-level API that translates narrative-level commands into timeline clips:

```python
director.sequence(
    charlie.walk_to(x=200),  # generates a walk clip + position channel
    charlie.say("Hello, how are you?"),  # generates lip sync clip + audio event
    bob.react("surprise"),  # generates a facial expression clip
    parallel(  # these happen simultaneously
        charlie.gesture("shrug"),
        bob.say("I'm fine!"),
    ),
)
```

**How this decomposes:**

1. `charlie.walk_to(200)` → creates a `PlacedClip` with a position channel from current_x to 200, using the walk animation, with a computed duration based on distance and walk speed.

2. `charlie.say("Hello...")` → creates a lip-sync clip (mouth shapes keyed to phonemes), an audio event for the voice file, and optionally a subtitle event. Duration determined by audio length.

3. `director.sequence(A, B, C)` → places A at time 0, B at time A.duration, C at time A.duration + B.duration.

4. `director.parallel(A, B)` → places both at the same start time. The combined duration is `max(A.duration, B.duration)`.

**Implementation pattern:**

Each character action method returns an **Action** — a deferred description that knows its duration and can generate timeline clips when placed:

```python
@dataclass
class Action:
    duration: float
    generate: Callable[[float], List[PlacedClip]]  # start_time → clips


def sequence(*actions: Action) -> Action:
    total_duration = sum(a.duration for a in actions)

    def generate(start_time):
        clips = []
        t = start_time
        for action in actions:
            clips.extend(action.generate(t))
            t += action.duration
        return clips

    return Action(duration=total_duration, generate=generate)


def parallel(*actions: Action) -> Action:
    max_duration = max(a.duration for a in actions)

    def generate(start_time):
        clips = []
        for action in actions:
            clips.extend(action.generate(start_time))
        return clips

    return Action(duration=max_duration, generate=generate)
```

The director composes `Action` objects (which are just data + a deferred generator) and then calls `generate(0)` to produce the full timeline. This is a **monad-like** pattern — each action describes a computation to be run later, and `sequence`/`parallel` are the combinators.

---

## 5. Python Architecture Sketch

### 5.1 Module Structure

```
cutout_anim/
├── __init__.py              # Public API facade
├── base.py                  # Core types: Transform, Node, Visual, Pose
├── scene.py                 # SceneGraph (Mapping facade), scene building
├── rig.py                   # Bone, IK solvers, constraints
├── anim/
│   ├── __init__.py          # Re-exports from submodules
│   ├── channel.py           # Keyframe, Channel, interpolation
│   ├── clip.py              # Clip, BlendTree, evaluate_clip
│   ├── state_machine.py     # StateMachine, State, Transition
│   ├── blend.py             # Pose blending, LayerStack
│   └── procedural.py        # Walk generator, springs, noise idle
├── timeline.py              # Timeline, Track, PlacedClip, events
├── director.py              # Action, sequence, parallel, Director
├── easing.py                # Easing functions (functions-as-params)
├── transform.py             # Matrix3x3, affine math, decompose/compose
├── serialize.py             # to_json / from_json for all types
└── util.py                  # Internal helpers
```

**Dependency graph (arrows mean "depends on"):**

```
director → timeline → anim/* → base, transform, easing
                       rig  → base, transform
                     scene  → base, transform
                 serialize  → everything (serializes all types)
```

The core `base` and `transform` modules are leaf dependencies. `easing` is also a leaf (pure functions, no dependencies). Everything builds upward from there.

### 5.2 Key Abstractions

```python
# ── transform.py ──

from dataclasses import dataclass
import numpy as np


@dataclass(frozen=True)
class Matrix3x3:
    """2D homogeneous affine transform as a 3x3 matrix.

    >>> m = Matrix3x3.identity()
    >>> m @ Matrix3x3.translate(10, 20)
    Matrix3x3(...)
    """

    data: np.ndarray  # shape (3, 3)

    @classmethod
    def identity(cls) -> "Matrix3x3": ...
    @classmethod
    def translate(cls, tx: float, ty: float) -> "Matrix3x3": ...
    @classmethod
    def rotate(cls, angle: float) -> "Matrix3x3": ...
    @classmethod
    def scale(cls, sx: float, sy: float) -> "Matrix3x3": ...

    def __matmul__(self, other: "Matrix3x3") -> "Matrix3x3":
        """Compose transforms: self @ other."""
        return Matrix3x3(self.data @ other.data)

    def inverse(self) -> "Matrix3x3": ...
    def decompose(self) -> "TransformParams": ...


@dataclass
class TransformParams:
    """Decomposed human-readable transform. This is what animators see/edit."""

    x: float = 0.0
    y: float = 0.0
    rotation: float = 0.0  # radians
    scale_x: float = 1.0
    scale_y: float = 1.0
    skew_x: float = 0.0
    skew_y: float = 0.0
    pivot_x: float = 0.0
    pivot_y: float = 0.0

    def to_matrix(self) -> Matrix3x3: ...
```

```python
# ── base.py ──

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional, Protocol, Dict
from collections import OrderedDict


class Visual(Protocol):
    """Anything that can be drawn."""

    def local_aabb(self) -> AABB: ...
    def contains(self, local_point: tuple[float, float]) -> bool: ...


@dataclass
class Sprite:
    """A rigid 2D image region. Implements Visual."""

    texture_id: str
    width: float
    height: float
    anchor_x: float = 0.5  # normalized 0–1
    anchor_y: float = 0.5


@dataclass
class Mesh:
    """A deformable 2D mesh. Implements Visual."""

    vertices: np.ndarray  # (N, 2) rest positions
    triangles: np.ndarray  # (M, 3) triangle indices
    uvs: np.ndarray  # (N, 2) texture coordinates
    bone_weights: Dict[str, np.ndarray]  # bone_name → (N,) weights
    texture_id: str


@dataclass
class Node:
    """A scene graph node.

    >>> n = Node('root')
    >>> n.add_child(Node('child'))
    >>> n.children['child'].name
    'child'
    """

    name: str
    local_transform: TransformParams = field(default_factory=TransformParams)
    visual: Optional[Visual] = None
    children: OrderedDict[str, "Node"] = field(default_factory=OrderedDict)
    slots: Dict[str, TransformParams] = field(default_factory=dict)
    parent: Optional["Node"] = field(default=None, repr=False)

    # Dirty flag state
    _world_matrix: Optional[Matrix3x3] = field(default=None, repr=False)
    _dirty: bool = field(default=True, repr=False)

    def add_child(self, child: "Node"):
        child.parent = self
        self.children[child.name] = child
        child._invalidate()

    def set_transform(self, **kwargs):
        """Update transform params. Marks dirty."""
        for k, v in kwargs.items():
            setattr(self.local_transform, k, v)
        self._invalidate()

    def _invalidate(self):
        if not self._dirty:
            self._dirty = True
            for child in self.children.values():
                child._invalidate()

    @property
    def world_transform(self) -> Matrix3x3:
        if self._dirty:
            local = self.local_transform.to_matrix()
            if self.parent is None:
                self._world_matrix = local
            else:
                self._world_matrix = self.parent.world_transform @ local
            self._dirty = False
        return self._world_matrix


# ── Pose: the universal animation output ──

Pose = Dict[tuple[str, str], float]
# Keys: (target_path, property_name) → value
# Example: {("charlie/torso", "rotation"): 0.5, ("charlie/torso", "opacity"): 1.0}
```

```python
# ── scene.py ──

from collections.abc import Mapping


class SceneGraph(Mapping):
    """Scene tree accessible as a path-keyed Mapping.

    >>> scene = SceneGraph(root)
    >>> scene["charlie/torso/left_arm"]
    Node(name='left_arm', ...)
    """

    def __init__(self, root: Node):
        self._root = root

    def __getitem__(self, path: str) -> Node:
        node = self._root
        for part in path.split("/"):
            try:
                node = node.children[part]
            except KeyError:
                raise KeyError(path)
        return node

    def __iter__(self):
        yield from self._walk(self._root, "")

    def _walk(self, node, prefix):
        for name, child in node.children.items():
            path = f"{prefix}/{name}" if prefix else name
            yield path
            yield from self._walk(child, path)

    def __len__(self):
        return sum(1 for _ in self)

    def subtree(self, path: str) -> "SceneGraph":
        """Return a SceneGraph rooted at the given path."""
        return SceneGraph(self[path])
```

```python
# ── easing.py ──

"""Easing functions: Callable[[float], float].

All functions map [0,1] → [0,1].

>>> linear(0.5)
0.5
>>> ease_in_quad(0.5)
0.25
"""

from typing import Callable

Easing = Callable[[float], float]


def linear(t: float) -> float:
    """Identity easing."""
    return t


def step(t: float) -> float:
    """Snap to 1 at the end."""
    return 0.0 if t < 1.0 else 1.0


def ease_in_quad(t: float) -> float:
    return t * t


def ease_out_quad(t: float) -> float:
    return 1.0 - (1.0 - t) ** 2


def ease_in_out_quad(t: float) -> float:
    return 2 * t * t if t < 0.5 else 1 - (-2 * t + 2) ** 2 / 2


def cubic_bezier(x1: float, y1: float, x2: float, y2: float) -> Easing:
    """Return an easing function from cubic bezier control points.

    >>> ease = cubic_bezier(0.42, 0, 0.58, 1.0)
    >>> 0.0 < ease(0.5) < 1.0
    True
    """

    def ease(t: float) -> float:
        # Newton-Raphson solve for parameter u where bezier_x(u) = t
        # then return bezier_y(u)
        ...

    return ease


def compose_easing(outer: Easing, inner: Easing) -> Easing:
    """Compose two easing functions: outer(inner(t))."""
    return lambda t: outer(inner(t))
```

```python
# ── anim/channel.py ──

from dataclasses import dataclass
from typing import List, Any
from bisect import bisect_right


@dataclass
class Keyframe:
    """A value at a point in time.

    >>> kf = Keyframe(time=0.0, value=0.0)
    """

    time: float
    value: Any
    easing: Easing = linear  # timing curve to next keyframe


@dataclass
class Channel:
    """Sorted keyframes for one property of one target.

    >>> ch = Channel("torso", "rotation", [Keyframe(0, 0.0), Keyframe(1, 3.14)])
    >>> ch.evaluate(0.5)  # interpolated value
    ...
    """

    target_path: str
    property: str
    keyframes: List[Keyframe]
    # Value interpolation strategy (default: lerp)
    value_interp: Callable[[Any, Any, float], Any] = lerp

    def evaluate(self, t: float) -> Any:
        keys = self.keyframes
        if t <= keys[0].time:
            return keys[0].value
        if t >= keys[-1].time:
            return keys[-1].value
        i = bisect_right([k.time for k in keys], t) - 1
        k0, k1 = keys[i], keys[i + 1]
        local_t = (t - k0.time) / (k1.time - k0.time)
        eased_t = k0.easing(local_t)
        return self.value_interp(k0.value, k1.value, eased_t)
```

```python
# ── anim/clip.py ──

from dataclasses import dataclass
from typing import List
from enum import Enum


class LoopMode(Enum):
    ONCE = "once"
    LOOP = "loop"
    PING_PONG = "ping_pong"


@dataclass
class Clip:
    """A collection of animation channels with a duration.

    >>> clip = Clip("walk", 1.0, channels=[...])
    >>> pose = clip.evaluate(0.5)
    """

    name: str
    duration: float
    channels: List[Channel]
    loop_mode: LoopMode = LoopMode.ONCE

    def evaluate(self, t: float) -> Pose:
        effective_t = _apply_loop(t, self.duration, self.loop_mode)
        return {
            (ch.target_path, ch.property): ch.evaluate(effective_t)
            for ch in self.channels
        }


def _apply_loop(t: float, duration: float, mode: LoopMode) -> float:
    if mode == LoopMode.ONCE:
        return max(0.0, min(t, duration))
    elif mode == LoopMode.LOOP:
        return t % duration
    elif mode == LoopMode.PING_PONG:
        cycle = t % (2 * duration)
        return cycle if cycle <= duration else 2 * duration - cycle
```

```python
# ── anim/blend.py ──

from dataclasses import dataclass
from typing import List, Optional, Set
from enum import Enum


class BlendMode(Enum):
    OVERRIDE = "override"
    ADDITIVE = "additive"


@dataclass
class AnimationLayer:
    source: "AnimationSource"  # Clip, BlendTree, StateMachine — duck-typed .evaluate()
    weight: float = 1.0
    blend_mode: BlendMode = BlendMode.OVERRIDE
    mask: Optional[Set[str]] = None  # target paths this layer affects


@dataclass
class LayerStack:
    """Ordered stack of animation layers.

    Evaluates bottom-up, merging poses.
    """

    layers: List[AnimationLayer]

    def evaluate(self, t: float, parameters: dict = None) -> Pose:
        accumulated = {}
        for layer in self.layers:
            pose = layer.source.evaluate(t, parameters)
            if layer.mask:
                pose = {k: v for k, v in pose.items() if k[0] in layer.mask}
            for key, value in pose.items():
                if layer.blend_mode == BlendMode.OVERRIDE:
                    accumulated[key] = lerp(
                        accumulated.get(key, value), value, layer.weight
                    )
                else:  # ADDITIVE
                    accumulated[key] = accumulated.get(key, 0.0) + value * layer.weight
        return accumulated
```

```python
# ── director.py ──

from dataclasses import dataclass
from typing import Callable, List


@dataclass
class Action:
    """A deferred animation action with a known duration.

    Actions compose via sequence() and parallel().
    """

    duration: float
    generate: Callable[[float], List["PlacedClip"]]  # start_time → clips


def sequence(*actions: Action) -> Action:
    """Play actions one after another."""
    total = sum(a.duration for a in actions)

    def generate(start):
        clips, t = [], start
        for a in actions:
            clips.extend(a.generate(t))
            t += a.duration
        return clips

    return Action(duration=total, generate=generate)


def parallel(*actions: Action) -> Action:
    """Play actions simultaneously."""
    max_dur = max(a.duration for a in actions)

    def generate(start):
        return [clip for a in actions for clip in a.generate(start)]

    return Action(duration=max_dur, generate=generate)


def stagger(offset: float, *actions: Action) -> Action:
    """Play actions with a fixed time offset between each start."""
    total = offset * (len(actions) - 1) + max(a.duration for a in actions)

    def generate(start):
        clips = []
        for i, a in enumerate(actions):
            clips.extend(a.generate(start + i * offset))
        return clips

    return Action(duration=total, generate=generate)
```

### 5.3 Where Architectural Patterns Apply

#### Mapping interfaces

| Where | Key type | Value type | Purpose |
|---|---|---|---|
| `SceneGraph` | `str` (path) | `Node` | Hierarchical node access |
| `AssetStore` | `str` (asset ID) | `bytes` or parsed asset | Sprite sheets, audio, fonts |
| `ClipLibrary` | `str` (clip name) | `Clip` | Named animation clips |
| `ParameterMap` | `str` (param name) | `float / bool / int` | State machine / blend tree params |
| `PoseMap` (Pose itself) | `(target, property)` | `float` | Animation output snapshot |

All of these can be backed by `dol` stores — files on disk, S3, a database, or in-memory dicts, with the same `Mapping` interface.

#### Where meshed DAGs apply

The `meshed` library (declarative DAG composition from function signatures) is a natural fit for:

1. **Transform chain as a DAG**: The scene graph's transform computation is a tree-shaped DAG. Each node's world transform depends on its parent's. `meshed` can declare this:

```python
from meshed import DAG


# For a simple chain: root → torso → arm → hand
def root_transform(root_params) -> Matrix3x3: ...
def torso_transform(root_transform, torso_params) -> Matrix3x3: ...
def arm_transform(torso_transform, arm_params) -> Matrix3x3: ...
def hand_transform(arm_transform, hand_params) -> Matrix3x3: ...


transform_dag = DAG([root_transform, torso_transform, arm_transform, hand_transform])
# Evaluates in correct order, caches intermediates
```

2. **Animation evaluation pipeline**: The full pipeline from raw animation data to final rendered transforms:

```python
def evaluate_keyframes(clip, t) -> Pose: ...
def apply_ik(pose, ik_targets) -> Pose: ...
def apply_constraints(pose, constraints) -> Pose: ...
def blend_layers(base_pose, overlay_pose, weights) -> Pose: ...
def compute_world_transforms(blended_pose, scene_graph) -> Dict[str, Matrix3x3]: ...


anim_pipeline = DAG(
    [
        evaluate_keyframes,
        apply_ik,
        apply_constraints,
        blend_layers,
        compute_world_transforms,
    ]
)
```

3. **Constraint resolution order**: Constraints form a dependency graph. `meshed` can topologically sort them and evaluate in correct order.

#### Where functions-as-parameters apply

| Abstraction | Function parameter | Type signature |
|---|---|---|
| Easing | `easing: Easing` | `Callable[[float], float]` |
| Value interpolation | `interp: Callable[[T, T, float], T]` | lerp, slerp, angle_lerp, step |
| Procedural generator | `generator: Callable[[float, Params], Pose]` | walk_pose, idle_overlay |
| IK solver | `solver: Callable[[Chain, Vec2], List[float]]` | CCD, FABRIK |
| Transition condition | `condition: Callable[[Dict], bool]` | parameter predicates |
| Event handler | `handler: Callable[[TimelineEvent], None]` | play_sound, spawn_effect |

This is dependency injection at the function level — the algorithm is parameterized by its strategy, making every piece replaceable and testable.

### 5.4 Serialization to JSON

The Python authoring layer produces a **JSON scene description** that the JS/TS renderer consumes. The JSON mirrors the Python data structures:

```json
{
  "scene": {
    "name": "root",
    "transform": {"x": 0, "y": 0, "rotation": 0, "scaleX": 1, "scaleY": 1,
                   "pivotX": 0, "pivotY": 0},
    "children": [
      {
        "name": "charlie",
        "transform": {"x": 100, "y": 300},
        "children": [
          {
            "name": "torso",
            "visual": {"type": "sprite", "textureId": "charlie_torso",
                       "width": 80, "height": 120, "anchorX": 0.5, "anchorY": 0.8},
            "slots": {"neck": {"x": 0, "y": -50, "rotation": 0}},
            "children": [...]
          }
        ]
      }
    ]
  },
  "animations": {
    "charlie_walk": {
      "duration": 1.0,
      "loopMode": "loop",
      "channels": [
        {
          "targetPath": "charlie/torso/left_leg",
          "property": "rotation",
          "keyframes": [
            {"time": 0.0, "value": -0.3, "easing": [0.42, 0, 0.58, 1.0]},
            {"time": 0.5, "value": 0.3, "easing": [0.42, 0, 0.58, 1.0]},
            {"time": 1.0, "value": -0.3, "easing": "linear"}
          ]
        }
      ]
    }
  },
  "timeline": {
    "duration": 10.0,
    "tracks": [
      {
        "target": "charlie",
        "clips": [
          {"clipId": "charlie_walk", "startTime": 0, "duration": 3.0, "speed": 1.0,
           "blendIn": 0.2, "blendOut": 0.2}
        ]
      }
    ],
    "events": [
      {"time": 2.5, "type": "sound", "data": {"assetId": "footstep_01"}}
    ]
  },
  "assets": {
    "textures": {"charlie_torso": {"src": "sprites/charlie_torso.png"}},
    "audio": {"footstep_01": {"src": "audio/footstep.mp3"}}
  }
}
```

**Serialization strategy:**

- All custom types implement `to_dict()` → `dict` and `from_dict(d: dict)` → `Self`.
- Easing functions serialize as either a string (`"linear"`, `"ease_in_quad"`) or a 4-tuple `[x1, y1, x2, y2]` for cubic bezier.
- The JSON is the **contract** between Python (authoring) and JS (runtime). Both sides validate against the same schema.
- For TypeScript, generate a Zod schema from the JSON structure → runtime validation + type inference on the JS side.

**Serialization in Python (sketch):**

```python
# ── serialize.py ──

import json

# Registry of easing functions ↔ string names
_EASING_NAMES = {linear: 'linear', step: 'step', ease_in_quad: 'ease_in_quad', ...}
_EASING_FROM_NAME = {v: k for k, v in _EASING_NAMES.items()}

def easing_to_json(fn: Easing):
    """Serialize an easing function."""
    if fn in _EASING_NAMES:
        return _EASING_NAMES[fn]
    if hasattr(fn, '_bezier_params'):
        return list(fn._bezier_params)
    raise ValueError(f"Cannot serialize easing: {fn}")

def easing_from_json(data) -> Easing:
    if isinstance(data, str):
        return _EASING_FROM_NAME[data]
    if isinstance(data, list) and len(data) == 4:
        return cubic_bezier(*data)
    raise ValueError(f"Invalid easing: {data}")

def scene_to_json(scene_graph: SceneGraph) -> dict:
    """Serialize the full scene to a JSON-compatible dict."""
    ...

def timeline_to_json(timeline: Timeline) -> dict:
    ...

def export(scene: SceneGraph, timeline: Timeline, path: str):
    """Export the complete animation project to a JSON file."""
    data = {
        'scene': scene_to_json(scene),
        'timeline': timeline_to_json(timeline),
        'animations': {name: clip_to_json(clip) for name, clip in clip_library.items()},
        'assets': asset_manifest_to_json(asset_store),
    }
    with open(path, 'w') as f:
        json.dump(data, f, indent=2)
```

---

## References

[1] Foley, J.D., et al. *Computer Graphics: Principles and Practice*, 3rd ed. Addison-Wesley, 2013. (Chapter 9: Scene Graphs and Transformations.)

[2] Vince, J. *Mathematics for Computer Graphics*, 5th ed. Springer, 2017. (Chapter 7: 2D Affine Transformations.)

[3] [SVG Coordinate Systems and Transformations — W3C SVG Spec](https://www.w3.org/TR/SVG2/coords.html)

[4] [CSS transform-origin — MDN Web Docs](https://developer.mozilla.org/en-US/docs/Web/CSS/transform-origin)

[5] [CSS Transforms Level 1 — Decomposing a 2D Matrix](https://www.w3.org/TR/css-transforms-1/#decomposing-a-2d-matrix)

[6] Nystrom, R. *Game Programming Patterns*, 2014. (Chapter: Dirty Flag.) [https://gameprogrammingpatterns.com/dirty-flag.html](https://gameprogrammingpatterns.com/dirty-flag.html)

[7] Ericson, C. *Real-Time Collision Detection*. Morgan Kaufmann, 2004. (Chapter 6: Bounding Volume Hierarchies.)

[8] Buss, S.R. "Introduction to Inverse Kinematics with Jacobian Transpose, Pseudoinverse and Damped Least Squares Methods." 2004. [https://mathweb.ucsd.edu/~sbuss/ResearchWeb/ikmethods/iksurvey.pdf](https://mathweb.ucsd.edu/~sbuss/ResearchWeb/ikmethods/iksurvey.pdf)

[9] Wang, L.C.T. and Chen, C.C. "A combined optimization method for solving the inverse kinematics problem of mechanical manipulators." *IEEE Transactions on Robotics and Automation*, 7(4), 1991.

[10] Aristidou, A. and Lasenby, J. "FABRIK: A fast, iterative solver for the Inverse Kinematics problem." *Graphical Models*, 73(5), 2011. [https://www.andreasaristidou.com/FABRIK.html](https://www.andreasaristidou.com/FABRIK.html)

[11] Lewis, J.P., Cordner, M., and Fong, N. "Pose Space Deformation: A Unified Approach to Shape Interpolation and Skeleton-Driven Deformation." *SIGGRAPH 2000*. (The classic reference for skinning techniques.)

[12] Steed, A. and Julier, S. "Design and Implementation of an Immersive Virtual Reality System." *Constraints and IK in Character Animation*, 2013. (General constraint resolution approach used in Blender, Maya, and game engines.)

[13] [Easing Functions Cheat Sheet](https://easings.net/)

[14] [A Primer on Bézier Curves — Pomax](https://pomax.github.io/bezierinfo/)

[15] Catmull, E. and Rom, R. "A Class of Local Interpolating Splines." *Computer Aided Geometric Design*, Academic Press, 1974.

[16] Bartels, R., Beatty, J., and Barsky, B. *An Introduction to Splines for Use in Computer Graphics and Geometric Modeling*. Morgan Kaufmann, 1987.

[17] [Unity Manual — Animation Blend Trees](https://docs.unity3d.com/Manual/class-BlendTree.html)

[18] [Godot Engine Documentation — AnimationTree](https://docs.godotengine.org/en/stable/tutorials/animation/animation_tree.html)

[19] van de Panne, M. "Parameterized Gait Synthesis." *IEEE Computer Graphics and Applications*, 16(2), 1996.

[20] Witkin, A. and Baraff, D. "Physically Based Modeling: Principles and Practice." *SIGGRAPH Course Notes*, 1997. [https://www.cs.cmu.edu/~baraff/sigcourse/](https://www.cs.cmu.edu/~baraff/sigcourse/)

[21] Ciccone, L. "Critically Damped Spring Smoothing." *Game Developer Magazine*, 2012.

[22] Perlin, K. "Improving Noise." *SIGGRAPH 2002 Proceedings*. [https://mrl.cs.nyu.edu/~perlin/paper445.pdf](https://mrl.cs.nyu.edu/~perlin/paper445.pdf)
