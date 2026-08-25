"""Multiplane environments (an#110).

An environment was three scalars — `sky_color`, `ground_color`, `ground_y` —
merged through an **intersection filter** that silently dropped everything
else; the test pinning that warning used `parallax_layers: 3` as its example,
which says plainly what the shape was for. And a plane in FRONT of the
characters was *structurally unreachable*: `_build_scene_root` ran
environments and characters in two separate loops, so no ordering an author
could write would interleave them.

The acceptance is the same negative one the wave keeps using: an environment
that does not declare planes must compile byte-identically. Two of the nine
ledger scenes carry an environment, so re-expressing the presets as planes
would move exactly those two hashes for no picture change — which is why the
plane code is reached only by a document that asks for it.
"""

from __future__ import annotations

import pytest

from an.adapters.cutout.compile import (
    CutoutCompileError,
    CutoutCompileWarning,
    compile_shot,
)
from an.adapters.cutout.timeline import evaluate_timeline, timeline_from_scene
from an.environments import EnvironmentDescriptor, Plane, PlaneArt
from an.ir.schema import AssetRef, Camera, SetAction, Shot

W, H = 320, 240


def _env(**kw) -> dict:
    """A store entry: the descriptor as the JSON a store would hold."""
    import json

    return json.loads(EnvironmentDescriptor(**kw).model_dump_json())


def _shot(*, entities=(), camera=None, actions=(), duration=2.0) -> Shot:
    return Shot(
        id="s1",
        renderer="cutout",
        duration=duration,
        camera=camera,
        entities=list(entities),
        actions=list(actions),
    )


def _env_entity(ref="street") -> AssetRef:
    return AssetRef(kind="environment", id="street", store="environments", ref=ref)


def _names(node, path=()):
    """Every node name in draw order, as `parent/child` paths."""
    here = path + (node.name,)
    out = ["/".join(here[1:])] if len(here) > 1 else []
    for c in node.children:
        out += _names(c, here)
    return out


# --- byte-identity: the legacy path is not touched ---------------------------


@pytest.mark.parametrize("entry", [None, {}, {"name": "park", "tags": ["outdoor"]},
                                   {"sky_color": "#123456"}])
def test_an_environment_without_planes_compiles_the_old_backdrop(entry):
    """A free-form `meta.json`, a preset name, or nothing at all — every
    environment written before an#110 — takes the preset path unchanged."""
    store = {} if entry is None else {"street": entry}
    scene = compile_shot(
        _shot(entities=[_env_entity("park" if entry is None else "street")]),
        mall={"environments": store},
        fps=24,
        width=W,
        height=H,
    )
    (env_node,) = [c for c in scene.scene.children if c.name == "street"]
    assert [c.name for c in env_node.children] == ["sky", "ground"]
    assert all(c.visual.kind == "rect" for c in env_node.children)


def test_a_descriptor_with_no_planes_is_still_the_old_backdrop():
    """`kind: EnvironmentDescriptor` alone does not switch paths — the planes
    do. A migrated free-form entry carries the kind tag and no planes, and it
    must not change what it draws."""
    scene = compile_shot(
        _shot(entities=[_env_entity()]),
        mall={"environments": {"street": _env(name="street")}},
        fps=24,
        width=W,
        height=H,
    )
    (env_node,) = [c for c in scene.scene.children if c.name == "street"]
    assert [c.name for c in env_node.children] == ["sky", "ground"]


# --- planes ------------------------------------------------------------------


def _street(**kw) -> dict:
    return _env(
        name="street",
        planes=[
            Plane(name="sky", art=PlaneArt(color="#bcd9f2"), depth=0.05),
            Plane(name="hills", art=PlaneArt(color="#7fa07a"), depth=0.35),
            Plane(name="road", art=PlaneArt(color="#5a5a62"), depth=1.0),
            Plane(name="railing", art=PlaneArt(color="#2f2f36"), depth=1.9),
        ],
        **kw,
    )


def test_planes_are_drawn_in_list_order():
    """List order IS draw order. There is no `z` field: the runtime sets no
    `zIndex`, so a second ordering would be one it could not honour."""
    scene = compile_shot(
        _shot(entities=[_env_entity()]),
        mall={"environments": {"street": _street()}},
        fps=24,
        width=W,
        height=H,
    )
    (env_node,) = [c for c in scene.scene.children if c.name == "street"]
    assert [c.name for c in env_node.children] == ["sky", "hills", "road", "railing"]


def test_a_foreground_plane_is_drawn_after_the_characters():
    """The thing that was structurally unreachable.

    `_build_scene_root` ran environments and characters in two separate loops,
    so no entity ordering an author could write put a plane in front. Now
    `characters_after` names the plane they stand in front of.
    """
    scene = compile_shot(
        _shot(entities=[
            _env_entity(),
            AssetRef(kind="character", id="maya", store="characters", ref="maya"),
        ]),
        mall={"environments": {"street": _street(characters_after="road")}},
        fps=24,
        width=W,
        height=H,
    )
    top = [c.name for c in scene.scene.children]
    # The foreground container has its OWN name. Both halves were `street`
    # before an#110's review, and the runtime's `nodeIndex` is a flat
    # `path -> container` dict, so the second silently overwrote the first: an
    # authored `set street:x` moved the foreground half and left the
    # background where it was, with nothing warning on either side (M2).
    assert top == ["street", "maya", "street__front"], top
    behind, _, front = scene.scene.children
    assert [c.name for c in behind.children] == ["sky", "hills", "road"]
    assert [c.name for c in front.children] == ["railing"]
    # …and every node path in the scene is unique, which is the property the
    # runtime's index actually needs.
    paths = _names(scene.scene)
    assert len(paths) == len(set(paths)), sorted(paths)


def test_characters_after_naming_no_plane_warns_and_draws_everything_behind():
    """Silently drawing a foreground plane at the back is a wrong picture that
    renders happily — the an#33 failure in a new place."""
    with pytest.warns(CutoutCompileWarning, match="characters_after"):
        scene = compile_shot(
            _shot(entities=[_env_entity()]),
            mall={"environments": {"street": _street(characters_after="rooftop")}},
            fps=24,
            width=W,
            height=H,
        )
    assert [c.name for c in scene.scene.children] == ["street"]


def test_an_unknown_key_on_a_plane_raises():
    """The done-when of an#110, and the reason `Plane` is `extra="forbid"`
    while the document that holds it is `extra="allow"`: the environments
    store is a free-form `meta.json` whose natural shape includes `name` and
    `tags`, but a plane is a precise instruction to draw something."""
    import pydantic

    with pytest.raises(pydantic.ValidationError, match="parallax_layers"):
        Plane(name="sky", parallax_layers=3)


# --- parallax ----------------------------------------------------------------


def _screen_x(scene, entity, plane, t):
    """`local_x − cam` — the plane's screen-space offset from the frame centre.

    The composition the runtime performs is
    ``world = position + M·(local − pivot)``, so with the camera's pivot on
    `root` this difference IS the screen displacement, up to the zoom factor.
    """
    tl = timeline_from_scene(scene)
    pose = evaluate_timeline(tl, t)
    cam = float(pose.get(("root", "pivot_x"), 0.0))
    local = pose.get((f"{entity}/{plane}", "x"))
    return (0.0 if local is None else float(local)) - cam


def test_each_plane_moves_by_its_own_depth():
    """The wave's whole point, in one assertion.

    ``screen = x0 − f·cam``, so the displacement RATIO between two planes is
    the ratio of their depths, and it holds under any zoom because the zoom
    term cancels. A plane at `depth = 1` emits nothing at all — it rides the
    camera, which is what every node did before an#110 and is why this is
    byte-identity-free.
    """
    scene = compile_shot(
        _shot(entities=[_env_entity()], camera=Camera(move="pan_right")),
        mall={"environments": {"street": _street()}},
        fps=24,
        width=W,
        height=H,
    )
    at_end = {n: _screen_x(scene, "street", n, 2.0) for n in ("sky", "hills", "road", "railing")}
    # Ratios ARE the depths.
    assert at_end["road"] != 0
    for name, depth in (("sky", 0.05), ("hills", 0.35), ("road", 1.0), ("railing", 1.9)):
        assert at_end[name] / at_end["road"] == pytest.approx(depth), (name, at_end)
    # …and the ordering: far moves least, near moves most.
    assert abs(at_end["sky"]) < abs(at_end["hills"]) < abs(at_end["road"]) < abs(at_end["railing"])


def test_a_plane_at_depth_one_emits_no_channel_at_all():
    """Not "emits a channel that happens to be flat": nothing. That is what
    keeps a scene whose planes are all at the character plane byte-identical
    to one with no parallax code in the build."""
    scene = compile_shot(
        _shot(entities=[_env_entity()], camera=Camera(move="pan_right")),
        mall={"environments": {"street": _street()}},
        fps=24,
        width=W,
        height=H,
    )
    parallax = [a for a in scene.animations if a.startswith("__parallax__")]
    assert not any("road" in a for a in parallax), parallax
    # Two guards cover this, and the `factor == 1.0` one is the redundant
    # half: with it removed, `(1 − 1.0)·cam` is zero for every key and the
    # all-at-rest guard catches it. So that mutant is EQUIVALENT — said here
    # because a mutation run will show it surviving and it is not a gap.
    assert sorted(parallax) == [
        "__parallax__s1_street_hills_x",
        "__parallax__s1_street_railing_x",
        "__parallax__s1_street_sky_x",
    ]


def test_no_camera_move_means_no_compensation():
    """The compensation exists to cancel the camera. With no camera there is
    nothing to cancel, and a depth is not an animation."""
    scene = compile_shot(
        _shot(entities=[_env_entity()]),
        mall={"environments": {"street": _street()}},
        fps=24,
        width=W,
        height=H,
    )
    assert not [a for a in scene.animations if a.startswith("__parallax__")]


def test_a_per_axis_override_beats_the_scalar():
    """`parallax` is the per-axis override of `depth` — one number, one name,
    and the tuple wins where it is given. A plane that scrolls horizontally
    but not vertically is the case it exists for."""
    env = _env(name="street", planes=[
        Plane(name="mural", art=PlaneArt(color="#eee"), depth=0.5, parallax=(0.2, 0.0)),
    ])
    scene = compile_shot(
        _shot(entities=[_env_entity()], camera=Camera(keys=None, move="pan_right")),
        mall={"environments": {"street": env}},
        fps=24,
        width=W,
        height=H,
    )
    names = sorted(scene.animations)
    assert names == ["__camera__s1_pivot_x", "__parallax__s1_street_mural_x"]
    # …the x factor is 0.2, not the 0.5 of `depth`.
    assert _screen_x(scene, "street", "mural", 2.0) == pytest.approx(-0.2 * (W / 3))


def test_an_authored_channel_on_a_compensated_plane_raises():
    """Same rule as the camera's, for the same reason: compensation clips are
    appended last and the evaluators are later-wins, so the authored channel
    would be discarded in silence."""
    shot = _shot(
        entities=[_env_entity()],
        camera=Camera(move="pan_right"),
        actions=[SetAction(target="street/hills", property="x", value=50.0)],
    )
    with pytest.raises(CutoutCompileError, match="street/hills"):
        compile_shot(shot, mall={"environments": {"street": _street()}}, fps=24, width=W, height=H)


def test_a_plane_at_depth_one_may_be_animated_by_hand():
    """The escape hatch the error names. Depth 1.0 emits no compensation, so
    there is nothing to collide with — and the rule is per-property anyway."""
    shot = _shot(
        entities=[_env_entity()],
        camera=Camera(move="pan_right"),
        actions=[SetAction(target="street/road", property="x", value=50.0)],
    )
    compile_shot(shot, mall={"environments": {"street": _street()}}, fps=24, width=W, height=H)


# --- the image path, which had no coverage at all (an#110 review, H1) --------


@pytest.fixture()
def plate_store(tmp_path):
    """An environments store with one real SVG plate on disk."""
    from an.stores.environments import EnvironmentsStore

    d = tmp_path / "street" / "plates"
    d.mkdir(parents=True)
    (d / "wall.svg").write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 200 80" '
        'width="200" height="80"><rect width="200" height="80" fill="#963"/></svg>',
        encoding="utf-8",
    )
    store = EnvironmentsStore(tmp_path)
    store["street"] = _env(
        name="street",
        planes=[
            Plane(
                name="wall",
                art=PlaneArt(kind="image", src="plates/wall.svg"),
                depth=0.4,
                offset=(30.0, -12.0),
                anchor=(0.25, 0.75),
                fit="stretch",
            )
        ],
    )
    return store


def _plane_visual(scene, name="wall"):
    (env_node,) = [c for c in scene.scene.children if c.name == "street"]
    (node,) = [c for c in env_node.children if c.name == name]
    return node


def test_an_image_plane_registers_its_texture_under_the_environments_prefix(plate_store):
    """`environments/` — the staging step resolves a `src` prefix to a mall
    store, so the wrong one copies from the wrong directory."""
    scene = compile_shot(
        _shot(entities=[_env_entity()]),
        mall={"environments": plate_store},
        fps=24,
        width=W,
        height=H,
        strict_assets=True,
    )
    (asset,) = scene.assets.textures.values()
    assert asset.src == "environments/street/plates/wall.svg"
    assert _plane_visual(scene).visual.kind == "svg_sprite"


def test_an_image_plane_carries_its_offset_anchor_extent_and_fit(plate_store):
    """Four fields that were each droppable with the suite green (H1): the
    transform, the anchor, the probed extent, and the fit policy."""
    scene = compile_shot(
        _shot(entities=[_env_entity()]),
        mall={"environments": plate_store},
        fps=24,
        width=W,
        height=H,
        strict_assets=True,
    )
    node = _plane_visual(scene)
    assert (node.transform.x, node.transform.y) == (30.0, -12.0)
    assert (node.visual.anchor_x, node.visual.anchor_y) == (0.25, 0.75)
    assert node.visual.fit == "stretch"
    # The extent comes from the art's own header, not from a guess.
    assert (node.visual.width, node.visual.height) == (200.0, 80.0)


def test_a_plate_that_is_not_on_disk_is_DROPPED_and_recorded(plate_store, tmp_path):
    """Not a blank container: dropped, and recorded as a fallback so
    `strict_assets` can escalate it. A plate that is not there has no stand-in
    that is not a lie about the picture (an#33) — and a childless node would
    render happily and say nothing."""
    plate_store["street"] = _env(
        name="street",
        planes=[Plane(name="ghost", art=PlaneArt(kind="image", src="plates/nope.svg"))],
    )
    with pytest.warns(CutoutCompileWarning, match="ghost"):
        scene = compile_shot(
            _shot(entities=[_env_entity()]),
            mall={"environments": plate_store},
            fps=24,
            width=W,
            height=H,
        )
    (env_node,) = [c for c in scene.scene.children if c.name == "street"]
    assert env_node.children == []
    assert not scene.assets.textures
    # …and it is fatal under strict_assets, at the one place that decides.
    with pytest.raises(CutoutCompileError, match="ghost"):
        compile_shot(
            _shot(entities=[_env_entity()]),
            mall={"environments": plate_store},
            fps=24,
            width=W,
            height=H,
            strict_assets=True,
        )


def test_a_fill_plane_without_a_size_covers_the_canvas():
    """`PLANE_FILL_SPAN` is the one number a size-less fill has to choose. The
    runtime centres `root` and applies camera scale, so a plane that merely
    matched the canvas would show its edge on any pull-out."""
    from an.adapters.cutout.compile import PLANE_FILL_SPAN

    scene = compile_shot(
        _shot(entities=[_env_entity()]),
        mall={"environments": {"street": _env(name="street", planes=[Plane(name="bg")])}},
        fps=24,
        width=W,
        height=H,
    )
    node = _plane_visual(scene, "bg")
    assert (node.visual.width, node.visual.height) == (PLANE_FILL_SPAN, PLANE_FILL_SPAN)
    assert PLANE_FILL_SPAN >= 10 * max(W, H)


def test_a_fill_plane_uses_a_declared_size():
    scene = compile_shot(
        _shot(entities=[_env_entity()]),
        mall={"environments": {"street": _env(
            name="street", planes=[Plane(name="band", size=(1234.0, 56.0))])}},
        fps=24,
        width=W,
        height=H,
    )
    node = _plane_visual(scene, "band")
    assert (node.visual.width, node.visual.height) == (1234.0, 56.0)


# --- the limits, written down rather than discovered -------------------------


def test_depth_governs_translation_only_and_a_zoom_is_uniform():
    """`depth = 0` does not mean "frozen in frame".

    The compensation is on `x`/`y`; `root.scale` multiplies the whole composed
    expression, so no per-plane factor can cancel it. A `depth = 0` plate under
    a push-in still grows. Depth-aware zoom is the DOLLY, which the design of
    record defers with its reason — and the docs said "frozen in frame" in four
    places until this test was written (an#110 review, M1).
    """
    scene = compile_shot(
        _shot(entities=[_env_entity()], camera=Camera(move="push_in")),
        mall={"environments": {"street": _env(
            name="street", planes=[Plane(name="moon", depth=0.0)])}},
        fps=24,
        width=W,
        height=H,
    )
    # The camera scales…
    assert "__camera__s1_scale_x" in scene.animations
    # …and nothing compensates for it, at any depth.
    assert not [a for a in scene.animations if a.startswith("__parallax__")]


def test_a_document_that_is_not_a_descriptor_never_reaches_the_plane_path():
    """The gate that makes the legacy path byte-identical, asserted directly:
    a free-form entry validates as a plane-less descriptor all by itself, which
    is why an#110 ships NO migration for it — a registered ladder that runs on
    nothing is decoration."""
    from an.environments import EnvironmentDescriptor

    legacy = {"name": "park", "description": "a park", "tags": ["outdoor"],
              "sky_color": "#cfe9ff", "ground_y": 100.0}
    env = EnvironmentDescriptor.model_validate(legacy)
    assert env.planes == []
    assert set(env.model_extra) == {"description", "tags", "sky_color", "ground_y"}


def test_duplicate_plane_names_are_refused():
    """A plane's name is its node name AND its parallax animation id, so the
    second of a pair overwrote the first in the animations dict while `tracks`
    kept two clips pointing at the survivor: both planes drew, one depth was
    honoured, nothing said so (an#110 review, M3)."""
    import pydantic

    with pytest.raises(pydantic.ValidationError, match="duplicate names"):
        EnvironmentDescriptor(name="e", planes=[Plane(name="d"), Plane(name="d")])


def test_every_parallax_channel_targets_a_node_that_exists():
    """The channel target and the node's actual path are computed by two
    different call sites, so they can disagree — and the disagreement is a
    channel aimed at nothing, which the runtime throws on and the compiler
    never noticed. `plane_parents` is the one rule both use; this asserts the
    result rather than the sharing (an#110 review, R9).
    """
    scene = compile_shot(
        _shot(
            entities=[
                _env_entity(),
                AssetRef(kind="character", id="maya", store="characters", ref="maya"),
            ],
            camera=Camera(move="pan_right"),
        ),
        mall={"environments": {"street": _street(characters_after="road")}},
        fps=24,
        width=W,
        height=H,
    )
    paths = set(_names(scene.scene))
    targets = {
        ch.target
        for aid, anim in scene.animations.items()
        if aid.startswith("__parallax__")
        for ch in anim.channels
    }
    assert targets, "the fixture must actually parallax something"
    assert targets <= paths, sorted(targets - paths)
    # …and the foreground plane's channel names the foreground container.
    assert "street__front/railing" in targets, sorted(targets)
