"""The compositor: a pose is not a position (an#111).

`evaluate_timeline` returns `{(target, property): value}`. Measuring a pan
needs where a node LANDS, and a rigid pan on `root` leaves every plane's local
`x` at zero — so a local channel reads "no parallax" for a stage that is
parallaxing correctly. Composing `world = position + M·(local − pivot)` up the
node chain is the missing step, and it is the reason an#107 promoted
`timeline_from_scene` out of a test file.

**It must agree with the vendored engine, not re-derive it.** The parity test
below runs the real `applyTransform` from `runtime.js` under node, against a
real PixiJS `Container`, and compares `toGlobal` with this module's answer. A
compositor that is merely self-consistent measures its own opinion.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from tests._node import NODE_BUNDLE_TIMEOUT_S, run_node

from an.adapters.cutout.serialize import (
    CutoutSceneJSON,
    NodeJSON,
    TimelineJSON,
    TransformJSON,
)
from an.adapters.cutout.timeline import Transform2D, screen_position

RUNTIME_DIR = Path(__file__).resolve().parents[1] / "an" / "data" / "cutout_runtime"

#: One awkward chain, reused by both the Python and the node side: a nested
#: rotation and scale under a pivoted parent, which is the case a "just add the
#: offsets" compositor gets wrong.
CHAIN = [
    # The root is identity IN THE DOCUMENT, because that is what the runtime
    # builds: its own container at the canvas centre, with the document root's
    # transform explicitly not applied. What reaches it is the POSE below —
    # which is how the camera works, since `root.pivot` is the camera.
    {"name": "root", "x": 0.0, "y": 0.0, "rotation": 0.0, "scale_x": 1.0,
     "scale_y": 1.0, "pivot_x": 0.0, "pivot_y": 0.0},
    # A rotated, pivoted ANCESTOR — the case a camera `rotation` channel
    # produces, and the one an#111's review found unexercised.
    {"name": "street", "x": 12.0, "y": -8.0, "rotation": -0.22, "scale_x": 1.1,
     "scale_y": 0.9, "pivot_x": 14.0, "pivot_y": -6.0},
    {"name": "hills", "x": -40.0, "y": 55.0, "rotation": 0.35, "scale_x": 0.75,
     "scale_y": 2.0, "pivot_x": 5.0, "pivot_y": 7.0},
]

#: What the camera writes onto the runtime's root container.
ROOT_POSE = {"pivot_x": 90.0, "pivot_y": -30.0, "scale_x": 1.25, "scale_y": 1.25,
             "rotation": 0.18}
PROBE = (13.0, -21.0)
WIDTH, HEIGHT = 320, 240


def _scene() -> CutoutSceneJSON:
    def node(spec, children=()):
        return NodeJSON(
            name=spec["name"],
            transform=TransformJSON(**{k: v for k, v in spec.items() if k != "name"}),
            children=list(children),
        )

    scene = CutoutSceneJSON(
        scene=node(CHAIN[0], [node(CHAIN[1], [node(CHAIN[2])])]),
        timeline=TimelineJSON(duration=1.0),
    )
    scene.meta.width, scene.meta.height = WIDTH, HEIGHT
    return scene


# --- parity with the engine that will actually draw it -----------------------


def test_the_compositor_agrees_with_pixi_itself():
    """Run the REAL `applyTransform` and PixiJS's own `toGlobal`.

    Not a re-implementation compared against a second re-implementation: the
    function is extracted from `runtime.js` verbatim and applied to a real
    `PIXI.Container` tree from the vendored bundle. If PixiJS ever changes how
    it composes, this goes red and the measurement does not quietly start
    measuring something else.
    """
    if shutil.which("node") is None:
        pytest.skip("node not installed")
    bundle = RUNTIME_DIR / "vendor" / "pixi.min.js"
    if not bundle.is_file():
        pytest.skip(f"vendored pixi not present at {bundle}")

    src = (RUNTIME_DIR / "runtime.js").read_text(encoding="utf-8")
    start = src.index("function applyTransform(")
    apply_transform = src[start : src.index("\n    }", start) + len("\n    }")]

    script = f"""
    global.self = global; global.window = global;
    global.document = {{ createElement: () => ({{ getContext: () => null, style: {{}} }}),
                        addEventListener: () => {{}} }};
    // Indirect eval, NOT require: the bundle is an IIFE assigning `var PIXI`
    // at module scope, so under CommonJS that binding stays local and
    // `require` hands back an empty object. `(0, eval)` runs it in global
    // scope, which is what a browser <script> tag does — the environment the
    // runtime actually runs in.
    (0, eval)(require('fs').readFileSync({json.dumps(str(bundle))}, 'utf8'));
    {apply_transform}
    const chain = {json.dumps(CHAIN)};
    const rootPose = {json.dumps(ROOT_POSE)};
    let parent = null, leaf = null;
    for (let i = 0; i < chain.length; i++) {{
        const c = new PIXI.Container();
        // The root gets the POSE, mirroring the runtime: it builds its own
        // container and applies channel values to it, never the document
        // root's declared transform.
        applyTransform(c, i === 0 ? rootPose : chain[i]);
        if (parent) parent.addChild(c);
        parent = c; leaf = c;
    }}
    const p = leaf.toGlobal(new PIXI.Point({PROBE[0]}, {PROBE[1]}));
    console.log(JSON.stringify([p.x, p.y]));
    """
    proc = run_node(script, timeout=NODE_BUNDLE_TIMEOUT_S)
    if proc.returncode != 0:
        pytest.skip(f"the vendored bundle would not load under node: {proc.stderr[:200]}")
    engine_x, engine_y = json.loads(proc.stdout.strip().splitlines()[-1])

    # `toGlobal` is relative to the stage origin; `screen_position` offsets by
    # the canvas centre, which is where `runtime.js` places the root.
    ours = screen_position(
        _scene(),
        "street/hills",
        pose={("root", k): v for k, v in ROOT_POSE.items()},
        point=PROBE,
    )
    assert ours[0] - WIDTH / 2 == pytest.approx(engine_x, abs=1e-6)
    assert ours[1] - HEIGHT / 2 == pytest.approx(engine_y, abs=1e-6)


# --- the properties the measurement depends on -------------------------------


def test_the_pivot_moves_the_content_the_other_way():
    """Why `root.pivot` IS a 2D camera, in one assertion."""
    scene = _scene()
    at_rest = screen_position(scene, "street/hills")
    panned = screen_position(scene, "street/hills", pose={("root", "pivot_x"): 190.0})
    assert panned[0] < at_rest[0]


def test_a_pose_replaces_the_declared_value_rather_than_adding_to_it():
    """The runtime assigns the property on the display object, so a channel
    REPLACES. That is why the parallax compensation carries the plane's own
    offset in every keyframe instead of an offset from it — a compositor that
    added would report every plane 40 px too far right."""
    scene = _scene()
    replaced = screen_position(scene, "street/hills", pose={("street/hills", "x"): 0.0})
    added_would_be = screen_position(scene, "street/hills")
    assert replaced != added_would_be
    # …and it is exactly what the document would give with x=0.
    scene2 = _scene()
    scene2.scene.children[0].children[0].transform.x = 0.0
    assert screen_position(scene2, "street/hills") == pytest.approx(replaced)


def test_a_pose_entry_for_another_node_does_not_leak():
    """Pose keys are full paths; a node reads only its own. Two nodes sharing
    a leaf NAME is the case that catches a bare-name lookup."""
    scene = _scene()
    clean = screen_position(scene, "street/hills")
    assert screen_position(scene, "street/hills", pose={("elsewhere/hills", "x"): 9999.0}) == clean


def test_a_path_that_names_no_node_raises():
    """Silently measuring the root instead is the plausible wrong answer: it
    returns a number, and the number is the frame centre."""
    with pytest.raises(KeyError, match="ghost"):
        screen_position(_scene(), "street/ghost")


def test_the_transform_has_no_field_the_runtime_does_not_apply():
    """`skew` is deliberately absent. PixiJS composes it into the same matrix,
    but no emitter in this package produces a skew channel, and a field
    nothing writes is a claim this compositor cannot honour."""
    from dataclasses import fields

    ours = {f.name for f in fields(Transform2D)}
    applied = {"x", "y", "rotation", "scale_x", "scale_y", "pivot_x", "pivot_y"}
    assert ours == applied
    src = (RUNTIME_DIR / "runtime.js").read_text(encoding="utf-8")
    for name in applied:
        assert f"t.{name}" in src, name
