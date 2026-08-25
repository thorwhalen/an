"""StylePack: art direction as a document (an#112) — the last of Wave 7.

The `styles` store had no reader. `AssetRef(kind="style")` was validated and
then skipped, and an#106 retired it because it selected nothing. Colour lived
in three disconnected places: the compiler's palette table, six literals inside
`runtime.js`, and the character factory's two disagreeing tables.

Three rules this file exists to hold:

1. **A scene with no pack compiles byte-identically.** This is the one Wave 7
   feature that could move every corpus hash, so the omit-when-unset
   serializers were written in the same commit as the fields.
2. **A pack must not declare a role it cannot change.** `lip`, `mouth_fill`,
   `teeth`, `tongue` and the eye white are `runtime.js` literals; a role that
   silently does nothing is worse than an absent one.
3. **A pack does not recolour SVG art**, and says so rather than failing
   quietly on a scene of SVG rigs.
"""

from __future__ import annotations

import json
from pathlib import Path

import pydantic
import pytest

from an.adapters.cutout.compile import (
    CutoutCompileError,
    CutoutCompileWarning,
    compile_shot,
    style_pack_for,
)
from an.ir.schema import AssetRef, Meta, Shot
from an.styles import (
    REACHABLE_ROLES,
    STYLE_DOCUMENT_KIND,
    UNREACHABLE_ROLES,
    StylePack,
    resolve_palette,
)

RUNTIME_JS = Path(__file__).resolve().parents[1] / "an" / "data" / "cutout_runtime" / "runtime.js"
W, H = 320, 240


def _shot(**kw) -> Shot:
    return Shot(
        id="s1",
        renderer="cutout",
        duration=1.0,
        entities=[
            AssetRef(kind="environment", id="room", store="environments", ref="park"),
            AssetRef(kind="character", id="charlie", store="characters", ref="c"),
        ],
        **kw,
    )


def _compiled(pack=None):
    return compile_shot(_shot(), fps=24, width=W, height=H, style_pack=pack)


def _colours(scene) -> list[str]:
    out = []

    def walk(node):
        if node.visual is not None and node.visual.color:
            out.append(node.visual.color)
        for c in node.children:
            walk(c)

    walk(scene.scene)
    return out


# --- byte-identity, the rule the whole feature is shaped around --------------


def test_a_scene_with_no_pack_compiles_to_exactly_what_it_always_did():
    """The no-pack path is a LOOKUP WITH A DEFAULT, not a rewrite."""
    import warnings

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", CutoutCompileWarning)
        a = _compiled(None)
        b = _compiled(StylePack(name="empty"))
    # An empty pack changes nothing either: it mentions no role, so every
    # `colour_for` returns None and every caller keeps its literal.
    assert a.model_dump_json() == b.model_dump_json()


def test_an_unset_style_pack_leaves_no_trace_in_either_document():
    """Both documents. `to_dict` prunes no `None`s and the contract hashes the
    whole dict, so a defaulted field on the compiled meta moves every corpus
    hash — and a defaulted field on the IR meta rewrites every committed
    `ir/scene.json` on the next `an sync`."""
    from an.adapters.cutout.serialize import CutoutSceneMetaJSON

    assert "style_pack" not in json.loads(CutoutSceneMetaJSON().model_dump_json())
    assert "style_pack" not in json.loads(Meta().model_dump_json())
    # …and present when set, or it would be write-only.
    assert json.loads(CutoutSceneMetaJSON(style_pack="noir").model_dump_json())["style_pack"] == "noir"
    assert json.loads(Meta(style_pack="noir").model_dump_json())["style_pack"] == "noir"


def test_the_markdown_round_trip_keeps_a_declared_pack():
    """`ir_to_markdown` ENUMERATES the meta keys, so a field added to `Meta`
    and not named there silently drops on write — the an#89 trap."""
    from an.ir.sync import ir_to_markdown, markdown_to_ir

    md = (
        "# X\n\n```yaml meta\ntitle: X\nduration: 1\nfps: 24\n"
        "default_renderer: cutout\nstyle_pack: noir\n```\n\n"
        "## Shot s1 (cutout)\n\n```yaml shot\nduration: 1\n```\n"
    )
    scene = markdown_to_ir(md)
    assert scene.meta.style_pack == "noir"
    assert markdown_to_ir(ir_to_markdown(scene)).meta.style_pack == "noir"
    # …and an unset pack does not appear in a regenerated scene.md.
    plain = markdown_to_ir(md.replace("style_pack: noir\n", ""))
    assert "style_pack" not in ir_to_markdown(plain)


# --- a role a pack cannot change is refused ----------------------------------


@pytest.mark.parametrize("role", sorted(UNREACHABLE_ROLES))
def test_an_unreachable_role_is_refused_with_its_reason(role):
    """A role that silently does nothing is worse than an absent one — the
    same rule an#110 applied to `repeat`/`TilingSprite`."""
    with pytest.raises(pydantic.ValidationError, match="not reachable"):
        StylePack(name="x", roles={role: "#800000"})
    # …and in a per-entity override too, which is a second door on one map.
    with pytest.raises(pydantic.ValidationError, match="not reachable"):
        StylePack(name="x", entities={"maya": {role: "#800000"}})


def test_the_unreachable_list_matches_the_runtime_literals_it_names():
    """Read out of `runtime.js`, not typed here. The list's whole job is to be
    true about that file, so a literal that becomes document-driven — or a new
    one that does not — must move this test rather than pass it."""
    src = RUNTIME_JS.read_text(encoding="utf-8")
    for literal in ("_LIP_COLOR", "_MOUTH_FILL", "_TEETH_COLOR", "_TONGUE_COLOR"):
        assert f"const {literal}" in src, literal
    # The eye white is an inline literal in makeEye, which is why `eye_sclera`
    # is unreachable while the pupil (stamped as `visual.color`) is not.
    make_eye = src[src.index("function makeEye("):]
    make_eye = make_eye[: make_eye.index("\n    }")]
    assert "beginFill(0xffffff" in make_eye
    assert "visualSpec.color" in make_eye
    assert "pupil" in UNREACHABLE_ROLES or "pupil" in REACHABLE_ROLES
    assert "pupil" in REACHABLE_ROLES and "eye_sclera" in UNREACHABLE_ROLES


def test_an_unknown_role_is_refused_too():
    """Not just the unreachable ones: a typo'd role would otherwise be an
    `extra="allow"` key in a dict, accepted and ignored."""
    with pytest.raises(pydantic.ValidationError, match="not a role"):
        StylePack(name="x", roles={"skinn": "#800000"})


# --- what a pack changes ------------------------------------------------------


def test_a_pack_recolours_the_procedural_rig_and_the_environment_preset():
    pack = StylePack(
        name="noir",
        roles={"skin": "#d8d8d8", "clothing": "#202028", "leg": "#101014",
               "sky": "#3a3a44", "ground": "#22222a"},
    )
    import warnings

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", CutoutCompileWarning)
        painted = set(_colours(_compiled(pack)))
    assert {"#d8d8d8", "#202028", "#3a3a44", "#22222a"} <= painted
    # …and the default palette is gone from the frame.
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", CutoutCompileWarning)
        plain = set(_colours(_compiled(None)))
    assert plain & {"#3a3a44", "#22222a"} == set()


def test_a_per_entity_override_beats_the_role():
    pack = StylePack(name="noir", roles={"sky": "#3a3a44"},
                     entities={"room": {"sky": "#ff00ff"}})
    import warnings

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", CutoutCompileWarning)
        painted = set(_colours(_compiled(pack)))
    assert "#ff00ff" in painted and "#3a3a44" not in painted


def test_resolve_palette_is_a_lookup_with_a_default():
    default = ("#f4c89a", "#3a6ea5", "#3b2a1a")
    assert resolve_palette(None, "maya", default) == default
    assert resolve_palette(StylePack(name="x"), "maya", default) == default
    partial = StylePack(name="x", roles={"clothing": "#202028"})
    assert resolve_palette(partial, "maya", default) == ("#f4c89a", "#202028", "#3b2a1a")


# --- the SVG limit, said out loud ---------------------------------------------


def test_a_pack_warns_about_the_svg_art_it_cannot_reach(tmp_path):
    """A pack recolours what the COMPILER decides. An SVG rig's colours are
    inside its drawings, and this package deliberately does not rewrite SVG at
    compile time — so the author hears it from the compiler rather than from
    the frames."""
    import shutil

    from an.stores.characters import CharactersStore

    fixture = Path(__file__).resolve().parent / "fixtures" / "characters" / "gale"
    shutil.copytree(fixture, tmp_path / "gale")
    store = CharactersStore(tmp_path)
    shot = Shot(
        id="s1", renderer="cutout", duration=1.0,
        entities=[AssetRef(kind="character", id="gale", store="characters", ref="gale")],
    )
    with pytest.warns(CutoutCompileWarning, match="could not reach"):
        compile_shot(shot, mall={"characters": store}, fps=24,
                     style_pack=StylePack(name="noir", roles={"skin": "#d8d8d8"}))


def test_no_pack_means_no_warning_about_unreachable_art(tmp_path):
    """The warning is about the pack, so a scene without one must be silent —
    otherwise every SVG scene in the corpus grows a warning it cannot act on."""
    import shutil
    import warnings

    from an.stores.characters import CharactersStore

    fixture = Path(__file__).resolve().parent / "fixtures" / "characters" / "gale"
    shutil.copytree(fixture, tmp_path / "gale")
    shot = Shot(
        id="s1", renderer="cutout", duration=1.0,
        entities=[AssetRef(kind="character", id="gale", store="characters", ref="gale")],
    )
    with warnings.catch_warnings():
        warnings.simplefilter("error", CutoutCompileWarning)
        compile_shot(shot, mall={"characters": CharactersStore(tmp_path)}, fps=24)


# --- resolution ---------------------------------------------------------------


def test_a_declared_pack_that_is_missing_RAISES():
    """An art direction the author asked for and did not get is a different
    picture that renders happily — the an#33 failure."""
    with pytest.raises(CutoutCompileError, match="not in the styles store"):
        style_pack_for(Meta(style_pack="noir"), {})
    with pytest.raises(CutoutCompileError, match="not a StylePack"):
        style_pack_for(Meta(style_pack="noir"), {"noir": {"kind": "CharacterDescriptor"}})


def test_no_declared_pack_resolves_to_None_without_touching_the_store():
    class Exploding:
        def __contains__(self, _k):
            raise AssertionError("the store must not be consulted")

    assert style_pack_for(Meta(), Exploding()) is None


def test_the_pack_is_its_own_registered_document_kind():
    from an.ir.migrate import KINDS

    assert KINDS[STYLE_DOCUMENT_KIND.name] is STYLE_DOCUMENT_KIND
    assert STYLE_DOCUMENT_KIND.version_field == "schema_version"


# --- the bench sees it, without the bench being told about packs -------------


def test_the_benchs_palette_derivation_picks_up_a_pack():
    """The done-when, and the reason nothing in `an/bench/` mentions a pack:
    the compiler RESOLVES the colours into the staged document, and the
    derivation reads that document. A pack that needed the bench to know about
    it would be a second place for the two to disagree."""
    import warnings

    from an.adapters.cutout.serialize import to_dict
    from an.bench.palette import palette_for_scene

    runtime = RUNTIME_JS.parent
    pack = StylePack(name="noir", roles={"skin": "#d8d8d8", "sky": "#3a3a44"})
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", CutoutCompileWarning)
        with_pack = palette_for_scene(to_dict(_compiled(pack)), runtime_dir=runtime)
        without = palette_for_scene(to_dict(_compiled(None)), runtime_dir=runtime)
    painted = {h.lower() for h in with_pack["palette_hex"]}
    plain = {h.lower() for h in without["palette_hex"]}
    assert {"#d8d8d8", "#3a3a44"} <= painted
    assert plain & {"#d8d8d8", "#3a3a44"} == set()
