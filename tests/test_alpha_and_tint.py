"""``alpha`` and ``tint`` as animatable node properties (#13).

Four tiers, cheapest first, because the interesting failures live at different
levels and only the last two can actually see a pixel:

1. **Serialization** — the fields exist and survive a round trip.
2. **Rest values** — a tween with no ``from_value`` starts from its property's
   *identity*, not from 0.0. This is a regression guard for a latent bug that
   would have made every fade-out a no-op.
3. **Ordering** — the real ``poseKeysInApplicationOrder`` from ``runtime.js``,
   run under node, applies shallower targets first.
4. **Pixels** — the real renderer, in a real browser. ``alpha`` cascades and
   ``tint`` reaches a drawable; a container silently swallowing a tint is the
   exact failure this property is written to avoid, so it is asserted against
   a live PixiJS object rather than reasoned about.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

from an.adapters.cutout.compile import (
    _DFLT_PROPERTY_REST_VALUE,
    _PROPERTY_REST_VALUES,
    compile_shot,
)
from an.adapters.cutout.serialize import TransformJSON, to_dict
from an.ir.schema import AssetRef, Meta, Resolution, SceneIR, Shot, TweenAction

RUNTIME_DIR = Path(__file__).resolve().parents[1] / "an/data/cutout_runtime"
RUNTIME_JS = RUNTIME_DIR / "runtime.js"


# --------------------------------------------------------------------- tier 1


def test_transform_carries_alpha_and_tint_with_rest_defaults():
    t = TransformJSON()
    assert t.alpha == 1.0, "a node with no alpha declared must render fully opaque"
    assert t.tint is None, "no tint means no colour multiply, not black"


def test_alpha_and_tint_survive_serialization():
    t = TransformJSON(alpha=0.25, tint="#ff0000")
    d = json.loads(json.dumps(to_dict(t)))
    assert d["alpha"] == 0.25
    assert d["tint"] == "#ff0000"


# --------------------------------------------------------------------- tier 2


def _first_keyframe_value(shot: Shot, *, target: str, prop: str):
    scene = compile_shot(shot)
    for anim in scene.animations.values():
        for ch in anim.channels:
            if ch.target == target and ch.property == prop:
                return ch.keyframes[0].value
    raise AssertionError(f"no channel for {target}::{prop}")


def _shot_with_tween(prop: str, *, to_value: float) -> Shot:
    return Shot(
        id="s1",
        style="cutout",
        duration=1.0,
        entities=[
            AssetRef(kind="character", id="charlie", store="characters", ref="c-v1")
        ],
        actions=[TweenAction(target="charlie", property=prop, to_value=to_value)],
    )


@pytest.mark.parametrize(
    "prop,expected_rest",
    [
        ("x", 0.0),
        ("y", 0.0),
        ("rotation", 0.0),
        ("scale_x", 1.0),
        ("scale_y", 1.0),
        ("alpha", 1.0),
    ],
)
def test_tween_without_from_value_starts_at_the_property_rest_value(
    prop, expected_rest
):
    """The regression guard for the latent identity bug.

    Every property used to start at 0.0. For ``alpha`` that makes a fade-out
    begin already invisible — the element never appears and the "fade" is a
    silent no-op. For ``scale_*`` the subject pops in from nothing.

    Mutation test: change ``_PROPERTY_REST_VALUES`` back to a flat 0.0 and the
    ``scale_x`` / ``scale_y`` / ``alpha`` cases must go red.
    """
    got = _first_keyframe_value(
        _shot_with_tween(prop, to_value=0.5), target="charlie", prop=prop
    )
    assert got == expected_rest


def test_explicit_from_value_still_wins_over_the_rest_table():
    shot = _shot_with_tween("alpha", to_value=1.0)
    shot.actions[0].from_value = 0.0
    got = _first_keyframe_value(shot, target="charlie", prop="alpha")
    assert got == 0.0, "an explicit from_value must not be overridden by the table"


def test_every_rest_value_property_is_handled_by_the_runtime():
    """A rest value for a property the runtime ignores is a lie.

    Keeps the Python table and the JS ``applyProperty`` switch from drifting:
    each key here must appear as a ``case`` in runtime.js.
    """
    js = RUNTIME_JS.read_text()
    cases = set(re.findall(r"case '([a-z_]+)':", js))
    missing = sorted(set(_PROPERTY_REST_VALUES) - cases)
    assert not missing, f"rest values declared for properties runtime.js ignores: {missing}"


def test_discrete_properties_have_no_rest_value():
    """``viseme`` and ``tint`` are snapped, not interpolated.

    Giving them a numeric identity would imply a meaningful midpoint. They fall
    through to the documented fallback instead.
    """
    for prop in ("viseme", "tint"):
        assert prop not in _PROPERTY_REST_VALUES
    assert _DFLT_PROPERTY_REST_VALUE == 0.0


# --------------------------------------------------------------------- tier 3


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_pose_application_order_is_shallowest_first_under_node():
    """Runs the REAL function out of runtime.js, not a re-implementation.

    Ordering matters because ``tint`` cascades: if a parent's tint is applied
    after a child's, it clobbers the more specific value. Object key order is
    insertion order, i.e. a function of channel emission order, which is not a
    contract.
    """
    src = RUNTIME_JS.read_text()
    match = re.search(
        r"function poseKeysInApplicationOrder\(pose\) \{.*?\n    \}", src, re.S
    )
    assert match, "poseKeysInApplicationOrder not found in runtime.js"

    script = (
        match.group(0)
        + "\n"
        + "const pose = {"
        + "'charlie/head/mouth::tint': 1, 'charlie::tint': 2,"
        + "'charlie/head::tint': 3, 'bob::alpha': 4};\n"
        + "console.log(JSON.stringify(poseKeysInApplicationOrder(pose)));"
    )
    proc = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, timeout=30
    )
    assert proc.returncode == 0, f"node failed: {proc.stderr}"
    order = json.loads(proc.stdout.strip())
    depths = [k.split("::")[0].count("/") for k in order]
    assert depths == sorted(depths), f"not shallowest-first: {order}"
    assert order.index("charlie::tint") < order.index("charlie/head::tint")
    assert order.index("charlie/head::tint") < order.index("charlie/head/mouth::tint")


# --------------------------------------------------------------------- tier 4

playwright = pytest.importorskip(
    "playwright.sync_api", reason="playwright not installed"
)


def _chromium_available() -> bool:
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            b = p.chromium.launch(args=["--no-sandbox"])
            b.close()
        return True
    except Exception:
        return False


_HAS_CHROMIUM = _chromium_available()
_FFMPEG = shutil.which("ffmpeg")


@pytest.mark.skipif(not _HAS_CHROMIUM, reason="needs playwright chromium")
def test_tint_reaches_a_drawable_and_never_dies_on_a_container():
    """The failure this property exists to avoid, asserted against live PixiJS.

    A ``Container`` has no ``tint``; assigning one *succeeds silently* as a dead
    JS property that never reaches a pixel. ``setTint`` must therefore walk down
    to a drawable. Verified against the real engine because this is a fact about
    the engine's object model, not about our code.
    """
    from playwright.sync_api import sync_playwright

    src = RUNTIME_JS.read_text()
    match = re.search(r"function setTint\(displayObject, value\) \{.*?\n    \}", src, re.S)
    assert match, "setTint not found in runtime.js"
    parse_color = re.search(r"function parseColor\(s\) \{.*?\n    \}", src, re.S)
    assert parse_color, "parseColor not found in runtime.js"

    harness = f"""
      {parse_color.group(0)}
      {match.group(0)}
      window.__probe = function () {{
        const out = {{}};
        // A container that owns a drawable: the tint must land on the drawable.
        const parent = new PIXI.Container();
        const g = new PIXI.Graphics();
        parent.addChild(g);
        out.applied_via_child = setTint(parent, '#ff0000');
        out.child_tint = g.tint;
        out.parent_has_own_tint = ('tint' in parent);
        // A bare container with nothing to tint applies to nothing, and says so.
        out.applied_to_empty = setTint(new PIXI.Container(), '#00ff00');
        // Alpha, by contrast, genuinely lives on the container.
        const c2 = new PIXI.Container();
        c2.alpha = 0.5;
        out.container_alpha = c2.alpha;
        return out;
      }};
    """
    page_html = (
        '<!DOCTYPE html><html><head><script src="'
        + (RUNTIME_DIR / "index.html").read_text().split('src="')[1].split('"')[0]
        + '"></script></head><body><script>'
        + harness
        + "</script></body></html>"
    )

    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "probe.html"
        p.write_text(page_html)
        with sync_playwright() as pw:
            b = pw.chromium.launch(args=["--no-sandbox"])
            pg = b.new_page()
            pg.goto(p.as_uri())
            pg.wait_for_function("window.PIXI !== undefined", timeout=30_000)
            out = pg.evaluate("window.__probe()")
            b.close()

    assert out["applied_via_child"] == 1, "setTint did not reach the drawable"
    assert out["child_tint"] == 0xFF0000
    assert out["parent_has_own_tint"] is False, (
        "a Container gained a tint property — if the engine changed, the cascade "
        "in setTint is now redundant and should be simplified, not left in place"
    )
    assert out["applied_to_empty"] == 0, "tinting nothing must report that it hit nothing"
    assert out["container_alpha"] == 0.5


@pytest.mark.skipif(
    not _HAS_CHROMIUM or not _FFMPEG, reason="needs ffmpeg + playwright chromium"
)
def test_an_alpha_tween_changes_the_rendered_pixels():
    """The issue's done-when, asserted on frames rather than on the pose dict.

    A character fades from fully opaque to fully transparent over the shot, so
    the first frame carries content and the last is empty. Before ``alpha``
    existed the property was silently ignored and both frames were identical.
    """
    from an import init
    from an.orchestrate import render_project
    from an.project import load

    with tempfile.TemporaryDirectory() as d:
        root = init(Path(d) / "fade")
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
                    style="cutout",
                    duration=1.0,
                    entities=[
                        AssetRef(
                            kind="character",
                            id="charlie",
                            store="characters",
                            ref="charlie-v1",
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

        first, last = Path(d) / "first.png", Path(d) / "last.png"
        _extract_frame(output, first, at="0")
        _extract_frame(output, last, at="0.9")

        ink_first, ink_last = _ink(first), _ink(last)
        assert ink_first > 0.01, "the character is not on screen at t=0"
        # alpha ~1.0 at t=0 and ~0.1 at t=0.9, and ink is a *linear* blend
        # toward the white background, so the ratio should track alpha closely.
        # 0.35 is a generous bound that still fails outright (ratio ~1.0) if the
        # property is ignored.
        assert ink_last < ink_first * 0.35, (
            f"alpha tween did not fade: ink {ink_first:.4f} -> {ink_last:.4f} "
            f"(ratio {ink_last / ink_first:.2f}). A ratio near 1.0 means `alpha` "
            "is being silently ignored again."
        )


_NEAR_WHITE = 240


def _extract_frame(video: Path, out: Path, *, at: str) -> None:
    proc = subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-ss", at, "-i", str(video),
         "-vframes", "1", str(out)],
        capture_output=True, text=True, check=False,
    )
    assert proc.returncode == 0, f"ffmpeg failed: {proc.stderr}"
    assert out.exists()


def _ink(png: Path) -> float:
    """Mean deviation from white, normalised to 0..1 — "how much is drawn".

    Deliberately not a near-white pixel *count*. Compositing at alpha ``a`` over
    a white background is a linear blend, so a dark part at alpha 0.1 still
    lands well below any sensible "is it white" threshold and a counting metric
    barely moves. Mean deviation is proportional to alpha, which is the thing
    under test.
    """
    PIL = pytest.importorskip("PIL.Image")
    im = PIL.open(png).convert("RGB")
    px = list(im.getdata())
    total = sum((255 - r) + (255 - g) + (255 - b) for r, g, b in px)
    return total / (len(px) * 3 * 255)
