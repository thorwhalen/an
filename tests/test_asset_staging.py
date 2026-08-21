"""Staging declared textures into the runtime directory, audibly.

`_stage_scene_assets` used to be `_stage_character_assets` and skipped, in
silence, any texture whose `src` did not start with `characters/` and any file
that was not on disk. Both silences matter because of what the renderer does
next: `makeSvgSprite` falls back to a plain white texture when an alias is
missing, so an un-staged asset reaches the user as a white rectangle in the
frame — indistinguishable from art, and attributed to whatever else shipped that
day.

The prefix restriction is also the hard gate in front of every plate, prop and
imported illustration that later waves add: an environment texture declared
today is simply dropped.
"""

from __future__ import annotations

import warnings
from pathlib import Path

import pytest

from an.adapters.cutout.render import (
    ASSET_SRC_PREFIX_TO_STORE,
    CutoutAssetWarning,
    _stage_scene_assets,
)
from an.adapters.cutout.serialize import AssetJSON, AssetsJSON, CutoutSceneJSON, NodeJSON, TimelineJSON


class _FakeStore:
    """Minimal stand-in for a mall store: only `_root` is consulted."""

    def __init__(self, root: Path | None):
        self._root = root


def _scene(**textures: AssetJSON) -> CutoutSceneJSON:
    return CutoutSceneJSON(
        scene=NodeJSON(name="root"),
        timeline=TimelineJSON(duration=1.0, tracks=[]),
        assets=AssetsJSON(textures=dict(textures), audio={}),
    )


def test_a_character_texture_is_staged_at_its_declared_path(tmp_path):
    root = tmp_path / "characters"
    (root / "amy-v1" / "parts").mkdir(parents=True)
    (root / "amy-v1" / "parts" / "head.svg").write_text("<svg/>", encoding="utf-8")
    target = tmp_path / "runtime"
    target.mkdir()

    scene = _scene(head=AssetJSON(src="characters/amy-v1/parts/head.svg"))
    with warnings.catch_warnings():
        warnings.simplefilter("error")  # any warning here is a failure
        _stage_scene_assets(scene, {"characters": _FakeStore(root)}, target)

    assert (target / "characters/amy-v1/parts/head.svg").read_text(encoding="utf-8") == "<svg/>"


def test_a_non_character_prefix_is_staged_rather_than_dropped(tmp_path):
    """The regression this change exists for.

    Before, anything not under `characters/` was skipped by a hardcoded prefix
    test, so an environment plate could be declared and would never arrive.
    """
    root = tmp_path / "environments"
    (root / "park").mkdir(parents=True)
    (root / "park" / "sky.svg").write_text("<svg id='sky'/>", encoding="utf-8")
    target = tmp_path / "runtime"
    target.mkdir()

    scene = _scene(sky=AssetJSON(src="environments/park/sky.svg"))
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        _stage_scene_assets(scene, {"environments": _FakeStore(root)}, target)

    assert (target / "environments/park/sky.svg").exists()


def test_every_declared_prefix_maps_to_a_real_mall_store():
    """Guard against a prefix table that names a store the project never builds."""
    from an.stores import build_project_mall
    import tempfile

    with tempfile.TemporaryDirectory() as d:
        mall = build_project_mall(d)
    unknown = sorted(set(ASSET_SRC_PREFIX_TO_STORE.values()) - set(mall))
    assert not unknown, f"prefix table names stores the mall does not have: {unknown}"


@pytest.mark.parametrize(
    "src,needle",
    [
        ("props/banner.png", "prefix is not one of"),
        ("characters/amy-v1/parts/missing.svg", "was not found at"),
    ],
)
def test_an_unstageable_texture_warns_instead_of_vanishing(tmp_path, src, needle):
    """Both silences, made audible.

    Mutation test: delete either `warnings.warn` call in `_stage_scene_assets`
    and the matching case here goes red.
    """
    root = tmp_path / "characters"
    root.mkdir(parents=True)
    target = tmp_path / "runtime"
    target.mkdir()

    scene = _scene(thing=AssetJSON(src=src))
    with pytest.warns(CutoutAssetWarning, match=needle):
        _stage_scene_assets(scene, {"characters": _FakeStore(root)}, target)


def test_an_in_memory_store_warns_rather_than_staging_nothing_quietly(tmp_path):
    """A dict-backed store is a legitimate test setup — but a declared texture still will not arrive."""
    target = tmp_path / "runtime"
    target.mkdir()
    scene = _scene(head=AssetJSON(src="characters/amy-v1/parts/head.svg"))
    with pytest.warns(CutoutAssetWarning, match="no filesystem root"):
        _stage_scene_assets(scene, {"characters": _FakeStore(None)}, target)


def test_a_texture_with_no_src_warns(tmp_path):
    target = tmp_path / "runtime"
    target.mkdir()
    scene = _scene(head=AssetJSON(src=""))
    with pytest.warns(CutoutAssetWarning, match="declares no src"):
        _stage_scene_assets(scene, {"characters": _FakeStore(tmp_path)}, target)
