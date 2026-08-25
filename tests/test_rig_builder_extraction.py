"""The rig builder is one builder, and the store is an argument (an#108).

`_build_svg_character_subtree` addressed its art through three copies of the
literal `"characters/"`. That is what made "a prop is a rig too" read as a
rewrite instead of an argument: the rig maths — bones, draw order, uniform
scale, slot nesting, the probe that decides whether a texture is declared at
all — is identical for a lamp and for a person, and only the store differs.

This lands ALONE, ahead of `PropDescriptor` and the compiler's prop path,
because it is the half that can be proven to change nothing: every corpus
contract hash is asserted unchanged **with no exemption**, which is the
acceptance the wave uses for a refactor (see `.claude/skills/an-dev-stage`
§2). A hash move retires every metric in that scene against every committed
ledger row, and unlike a golden re-bless there is no recovery.
"""

from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path

import pytest

from an.adapters.cutout.compile import (
    CHARACTER_ART_PREFIX,
    _build_svg_character_subtree,
    _part_probe,
    _svg_asset_src,
)
from an.ir.schema import AssetRef
from an.stores.characters import CharactersStore
from an.stores.props import PropsStore

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "characters" / "gale"


def _first_attachment_path() -> str:
    """One real `parts/…svg` path out of the committed fixture's default skin."""
    art = json.loads((FIXTURE / "character.json").read_text(encoding="utf-8"))
    return next(
        att["path"]
        for slots in art["skins"].values()
        for atts in slots["slots"].values()
        for att in atts.values()
    )


def _tree_and_textures(store, *, art_prefix, store_name):
    """Build gale's rig out of `store`, addressed under `art_prefix`."""
    entity = AssetRef(kind="character", id="g", store=store_name, ref="gale")
    textures: dict = {}
    node = _build_svg_character_subtree(
        entity,
        store["gale"],
        textures=textures,
        probe=_part_probe(store, art_prefix=art_prefix),
        art_prefix=art_prefix,
    )
    return node, textures


def test_the_same_rig_comes_out_of_either_store():
    """The ONLY difference is the `src` prefix.

    Not "roughly the same": the compiled subtree is compared as JSON after
    rewriting the prefix, so a difference in bone positions, draw order,
    nesting, sprite extents, or which attachments got registered fails here.
    That equality is the whole claim of the extraction — if the rig differed
    by store, the prop path would need its own builder after all.
    """
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        shutil.copytree(FIXTURE, root / "characters" / "gale")
        shutil.copytree(FIXTURE, root / "props" / "gale")
        chars = CharactersStore(root / "characters")
        props = PropsStore(root / "props")
        # A prop's descriptor file is `prop.json`; the fixture is a character
        # package, so the copy is renamed rather than re-authored.
        (root / "props" / "gale" / "character.json").rename(
            root / "props" / "gale" / "prop.json"
        )

        as_char, char_tex = _tree_and_textures(
            chars, art_prefix=CHARACTER_ART_PREFIX, store_name="characters"
        )
        as_prop, prop_tex = _tree_and_textures(
            props, art_prefix="props/", store_name="props"
        )

    char_json = as_char.model_dump_json()
    prop_json = as_prop.model_dump_json()
    assert char_json == prop_json, "the rig itself must not depend on the store"

    # Textures differ in exactly one way, and it is the prefix.
    assert set(char_tex) == set(prop_tex) and char_tex, "same aliases, both non-empty"
    for alias, asset in char_tex.items():
        assert asset.src.startswith("characters/")
        assert prop_tex[alias].src == "props/" + asset.src[len("characters/") :]


def test_the_probe_refuses_a_src_under_a_different_prefix_of_the_SAME_length():
    """The `startswith` guard, pinned so it cannot be deleted.

    The obvious negative — probe a `characters/` src against a props store —
    passes for the wrong reason: strip the guard and the code blindly removes
    `len(prefix)` characters, so `"characters/gale/…"` becomes `"ters/gale/…"`,
    which does not exist, and the assertion still holds. Every prefix in the
    table happens to be a different length today (11/13/6/7), so the guard is
    unfalsifiable through the real ones.

    Two SIX-character prefixes make it falsifiable. Without the guard, a probe
    for `props/` resolves a `chars/` src against the props root and answers
    **True** — declaring a texture that stages from the wrong store and fails
    at load. The moment a fifth kind shares a length with a fourth, that is a
    real bug rather than a constructed one (an#108 review, M-3).
    """
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        shutil.copytree(FIXTURE, root / "gale")
        probe = _part_probe(PropsStore(root), art_prefix="props/")
        assert probe is not None
        rel = _first_attachment_path()
        assert probe(f"props/gale/{rel}")[0] is True
        assert probe(f"chars/gale/{rel}")[0] is False


def test_the_probe_follows_the_prefix_it_is_given():
    """The probe decides whether a texture is DECLARED at all, so a probe still
    looking under `characters/` while the src says `props/` would silently drop
    every part of every prop — the invisible-art failure an#76 exists to stop,
    reintroduced by a mismatched default."""
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        shutil.copytree(FIXTURE, root / "gale")
        store = PropsStore(root)
        probe = _part_probe(store, art_prefix="props/")
        assert probe is not None
        rel = _first_attachment_path()
        assert probe(_svg_asset_src("gale", rel, art_prefix="props/"))[0] is True
        # …and the character-prefixed src is not found under the props store.
        assert probe(_svg_asset_src("gale", rel))[0] is False


def test_the_staging_step_knows_where_a_prop_s_art_lives():
    """A `src` prefix with no entry here is staged nowhere and warned about,
    and the render then fails at load over art no node can draw."""
    from an.adapters.cutout.render import ASSET_SRC_PREFIX_TO_STORE

    assert ASSET_SRC_PREFIX_TO_STORE["props/"] == "props"


# `props` must also be a store the mall actually builds, or every prop texture
# stages into a warning. That is already pinned, for every prefix at once, by
# `tests/test_asset_staging.py::test_every_declared_prefix_maps_to_a_real_mall_store`
# — asserting it again here would be a second copy of one fact.


@pytest.mark.parametrize("prefix", ["characters/", "props/", "environments/"])
def test_the_src_is_the_prefix_plus_ref_plus_path(prefix):
    assert _svg_asset_src("x", "parts/a.svg", art_prefix=prefix) == f"{prefix}x/parts/a.svg"


def test_the_mall_puts_a_prop_where_the_staging_step_looks_for_it():
    """Key membership is not enough: the store's ROOT is the other half.

    `test_every_declared_prefix_maps_to_a_real_mall_store` proves the mall has
    a `props` key. It says nothing about where that store points — and pointing
    it at `assets/characters` survived the whole suite (an#108 review, M-1),
    which would put prop descriptors in the characters directory and make
    `_stage_scene_assets` copy prop art out of the characters root. Silent both
    ways.

    `assets/props` existing is asserted for the same reason it is asserted for
    the other four (`tests/test_project.py`): a store whose root does not exist
    still returns a live probe — `_root` is not None — whose every `is_file()`
    is False, so every part of every prop drops out of the compiled scene. That
    is the an#76 invisible-art failure arriving through a missing directory.

    Stated plainly, because a mutation run will show it: **removing `props` from
    `build_project_mall`'s `ensure=True` list changes nothing and no test goes
    red.** `JsonSidecarStore.__init__` mkdirs its root unconditionally, so the
    directory appears when the store is constructed either way. The `ensure`
    entry is belt-and-braces and consistency with the other four, not a
    load-bearing line — and this assertion covers the outcome (the directory is
    there) rather than the mechanism, which is the half that actually matters
    (an#108 review, M-2).
    """
    from an.stores import build_project_mall

    with tempfile.TemporaryDirectory() as d:
        mall = build_project_mall(d, ensure=True)
        roots = {k: getattr(v, "_root", None) for k, v in mall.items()}
        assert Path(roots["props"]) == Path(d) / "assets" / "props"
        assert Path(roots["props"]) != Path(roots["characters"])
        assert (Path(d) / "assets" / "props").is_dir()
