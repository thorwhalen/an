"""``alpha`` as an animatable node property, and the tween rest-value contract.

Four tiers, cheapest first. **Tiers 1-3 must run everywhere**, including CI,
which does not install a browser — an earlier version of this file put
``pytest.importorskip("playwright...")`` at module level and so skipped its own
pure-Python guards in CI, where the collected-test count did not move at all.
That mistake was repeated in ten other modules and is now structurally
impossible: the gate is the ``browser`` / ``ffmpeg`` markers in
``tests/conftest.py``, applied after collection, and
``tests/test_browser_gate.py`` fails if any module goes back to skipping at
import time.

Tier 4 is the only part that needs a browser, and it is the only part that can
see a pixel.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from tests._node import run_node

from an.adapters.cutout.compile import (
    _PROPERTY_REST_VALUES,
    CutoutCompileError,
    compile_shot,
)
from an.adapters.cutout.serialize import TransformJSON, to_dict
from an.ir.schema import AssetRef, Meta, Resolution, SceneIR, Shot, TweenAction

RUNTIME_JS = Path(__file__).resolve().parents[1] / "an/data/cutout_runtime/runtime.js"

#: Properties the runtime handles that are *discrete* — a code or an enum, with
#: no meaningful numeric identity, so no rest value and no interpolation.
DISCRETE_PROPERTIES = frozenset({"viseme"})


# --------------------------------------------------------------- tier 1: model


def test_transform_carries_alpha_and_rests_opaque():
    assert TransformJSON().alpha == 1.0, (
        "a node with no alpha declared must render fully opaque"
    )


def test_alpha_survives_serialization():
    d = json.loads(json.dumps(to_dict(TransformJSON(alpha=0.25))))
    assert d["alpha"] == 0.25


# ---------------------------------------------------------- tier 2: compiler


def _first_keyframe_value(shot: Shot, *, target: str, prop: str):
    scene = compile_shot(shot)
    for anim in scene.animations.values():
        for ch in anim.channels:
            if ch.target == target and ch.property == prop:
                return ch.keyframes[0].value
    raise AssertionError(f"no channel for {target}::{prop}")


def _shot_with_tween(prop: str, *, to_value, from_value=None) -> Shot:
    return Shot(
        id="s1",
        renderer="cutout",
        duration=1.0,
        entities=[
            AssetRef(kind="character", id="charlie", store="characters", ref="c-v1")
        ],
        actions=[
            TweenAction(
                target="charlie",
                property=prop,
                to_value=to_value,
                from_value=from_value,
            )
        ],
    )


@pytest.mark.parametrize(
    "prop,expected_rest",
    [("x", 0.0), ("y", 0.0), ("rotation", 0.0), ("scale_x", 1.0), ("scale_y", 1.0), ("alpha", 1.0)],
)
def test_tween_without_from_value_starts_at_the_property_rest_value(prop, expected_rest):
    """The regression guard for a latent identity bug.

    Every property used to start at 0.0. For ``alpha`` that makes a fade-out
    begin already invisible — the element never appears and the "fade" is a
    silent no-op. For ``scale_*`` the subject pops in from nothing.

    Mutation: make ``_property_rest_values`` return a flat 0.0 and the
    ``scale_x`` / ``scale_y`` / ``alpha`` cases go red.
    """
    assert (
        _first_keyframe_value(
            _shot_with_tween(prop, to_value=0.5), target="charlie", prop=prop
        )
        == expected_rest
    )


def test_rest_values_are_derived_from_the_schema_not_restated():
    """One source of truth: a node's rest pose *is* TransformJSON's defaults.

    A hand-maintained copy is a second place to forget. Adding a numeric field
    to the model must extend the table for free.
    """
    for name, field in TransformJSON.model_fields.items():
        if isinstance(field.default, (int, float)) and not isinstance(
            field.default, bool
        ):
            assert _PROPERTY_REST_VALUES[name] == float(field.default), (
                f"{name} rest value disagrees with its schema default"
            )


def test_a_tween_on_an_undeclared_property_is_refused_at_compile():
    """A property outside the transform vocabulary names a SWAP SET (an#87).

    The pre-#87 contract was two-stage: a tween on `tint` with no
    ``from_value`` was refused for its missing rest identity, while one WITH
    a ``from_value`` compiled and then died in the browser at applyProperty.
    Both now fail at compile, earlier and with the real diagnosis: `tint` is
    not a transform, `charlie` has no descriptor, and a procedural rig's only
    swap is `viseme` on its mouth. (When #62 implements tint it enters the
    transform vocabulary and both forms simply compile.)
    """
    for kwargs in ({}, {"from_value": "#000000"}):
        with pytest.raises(CutoutCompileError) as e:
            compile_shot(_shot_with_tween("tint", to_value="#ff0000", **kwargs))
        msg = str(e.value)
        assert "tint" in msg
        assert "viseme" in msg, "the error should name the one supported swap"
        assert "transform" in msg.lower()


def test_an_explicit_from_value_makes_any_transform_property_tweenable():
    """The rest-value refusal is about a missing identity, not the property.

    ``pivot_x`` has a rest value, so this exercises the from_value override on
    a genuine transform property; arbitrary NON-transform names stopped being
    tweenable at all when properties became swap-set names (see above).
    """
    got = _first_keyframe_value(
        _shot_with_tween("pivot_x", to_value=3.0, from_value=2.0),
        target="charlie",
        prop="pivot_x",
    )
    assert got == 2.0


# ------------------------------------------------- tier 3: Python <-> JS parity


def _runtime_switch_cases() -> set[str]:
    """Property names in ``applyProperty``'s switch, comments excluded.

    Scoped to the switch body rather than grepping the whole file, because a
    whole-file grep is wrong in both directions: it passes on a commented-out
    case and trips on the word ``case`` in prose.
    """
    src = RUNTIME_JS.read_text(encoding="utf-8")
    body = re.search(r"function applyProperty\([^)]*\)\s*\{(.*?)\n    \}", src, re.S)
    assert body, "applyProperty not found in runtime.js"
    text = re.sub(r"//[^\n]*", "", body.group(1))  # strip line comments
    # Both quote styles: a double-quoted `case "tint":` is legal JS, and a
    # single-quote-only extractor is blind to it (an#86 adversarial review).
    return set(re.findall(r"case\s+['\"]([a-z_]+)['\"]\s*:", text))


def test_every_runtime_property_has_a_rest_value_unless_it_is_discrete():
    """The direction that matters, and the one the shipped bug lived in.

    A property the runtime happily animates but that has no rest value falls
    through to the refusal — which is safe, but means an author gets an error on
    a property that visibly works. Anything genuinely discrete must say so
    explicitly rather than by omission.
    """
    cases = _runtime_switch_cases()
    missing = sorted(cases - set(_PROPERTY_REST_VALUES) - DISCRETE_PROPERTIES)
    assert not missing, (
        f"runtime.js animates {missing} but they have no rest value and are not "
        f"declared discrete — a tween on them will be refused"
    )


def test_no_rest_value_is_declared_for_a_property_the_runtime_ignores():
    """The other direction: a rest value for a property nothing applies is a lie."""
    extra = sorted(set(_PROPERTY_REST_VALUES) - _runtime_switch_cases())
    assert not extra, f"rest values declared for properties runtime.js ignores: {extra}"


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_applypose_applies_shallowest_target_first():
    """Asserts the WIRING, not the helper.

    Testing ``poseKeysInApplicationOrder`` in isolation passes even when
    ``applyPose`` never calls it — verified by mutation. So this runs the real
    ``applyPose`` with a stubbed node index and records the order in which
    ``applyProperty`` is reached.
    """
    src = RUNTIME_JS.read_text(encoding="utf-8")
    order_fn = re.search(
        r"function poseKeysInApplicationOrder\(pose\) \{.*?\n    \}", src, re.S
    )
    apply_fn = re.search(r"function applyPose\(pose\) \{.*?\n    \}", src, re.S)
    assert order_fn and apply_fn, "expected functions not found in runtime.js"

    script = "\n".join(
        [
            order_fn.group(0),
            apply_fn.group(0),
            "const seen = [];",
            "const nodeIndex = new Proxy({}, {get: () => ({}), has: () => true});",
            "function applyProperty(node, prop, value) { seen.push(value); }",
            "applyPose({"
            "'charlie/head/mouth::alpha': 'deep',"
            "'charlie::alpha': 'shallow',"
            "'charlie/head::alpha': 'mid'});",
            "console.log(JSON.stringify(seen));",
        ]
    )
    proc = run_node(script)
    assert proc.returncode == 0, f"node failed: {proc.stderr}"
    assert json.loads(proc.stdout.strip()) == ["shallow", "mid", "deep"]


# ------------------------------------------------------------- tier 4: pixels


#: alpha runs 1.0 -> 0.0 over the shot, and ink is a linear blend toward the
#: white background, so the ratio tracks alpha. Sampled at 90% through, the
#: expected ratio is ~0.1; this bound is generous but still fails outright
#: (ratio ~1.0) if the property is ignored.
MAX_FADED_INK_RATIO = 0.35


@pytest.mark.browser
@pytest.mark.ffmpeg
def test_an_alpha_tween_changes_the_rendered_pixels(hermetic_browser, tmp_path):
    """The done-when, asserted on frames rather than on the pose dict.

    Runs under ``hermetic_browser``, so it also proves the fade happens with the
    network switched off — the engine is vendored as of #12.
    """
    from an import init
    from an.orchestrate import render_project
    from an.project import load

    root = init(tmp_path / "fade")
    proj = load(root)
    proj.scene = SceneIR(
        meta=Meta(
            title="fade",
            duration=1.0,
            fps=12,
            resolution=Resolution(width=320, height=240),
        ),
        timeline=[
            Shot(
                id="s1",
                renderer="cutout",
                duration=1.0,
                entities=[
                    AssetRef(
                        kind="character", id="charlie", store="characters", ref="c-v1"
                    )
                ],
                actions=[
                    TweenAction(
                        target="charlie",
                        property="alpha",
                        to_value=0.0,
                        duration=1.0,
                        easing="linear",
                    )
                ],
            )
        ],
    )
    proj.mall["scenes"]["main"] = proj.scene

    output = render_project(root, output_name="fade")
    assert output.exists()
    assert hermetic_browser["blocked"] == [], (
        f"the render reached for the network: {hermetic_browser['blocked']}"
    )

    first, last = tmp_path / "first.png", tmp_path / "last.png"
    _extract_frame(output, first, at="0")
    _extract_frame(output, last, at="0.9")

    ink_first, ink_last = _ink(first), _ink(last)
    assert ink_first > 0.01, "the character is not on screen at t=0"
    assert ink_last < ink_first * MAX_FADED_INK_RATIO, (
        f"alpha tween did not fade: ink {ink_first:.4f} -> {ink_last:.4f} "
        f"(ratio {ink_last / ink_first:.2f}). A ratio near 1.0 means `alpha` is "
        "being ignored again."
    )


def _extract_frame(video: Path, out: Path, *, at: str) -> None:
    proc = subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-ss", at, "-i", str(video),
         "-vframes", "1", str(out)],
        capture_output=True, text=True, check=False,
    )
    assert proc.returncode == 0, f"ffmpeg failed: {proc.stderr}"


def _ink(png: Path) -> float:
    """Mean deviation from white, 0..1 — "how much is drawn".

    Deliberately not a near-white pixel *count*. Compositing at alpha ``a`` over
    a white background is a linear blend, so a dark part at alpha 0.1 still
    lands below any sensible "is it white" threshold and a counting metric
    barely moves — it would pass a broken implementation.
    """
    from PIL import Image as PIL
    im = PIL.open(png).convert("RGB")
    px = list(im.getdata())
    total = sum((255 - r) + (255 - g) + (255 - b) for r, g, b in px)
    return total / (len(px) * 3 * 255)
