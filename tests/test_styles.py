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
from tests._render_seam import stop_at_compile_shot

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
    # An empty pack changes no PIXEL: it mentions no role, so every
    # `colour_for` returns None and every caller keeps its literal. It IS
    # recorded in the meta, because that field is provenance — "this render
    # ran under a pack" is true even when the pack said nothing.
    assert a.scene.model_dump_json() == b.scene.model_dump_json()
    assert a.meta.style_pack is None and b.meta.style_pack == "empty"
    assert a.model_dump_json() == b.model_dump_json().replace(',"style_pack":"empty"', "")


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
    assert "pupil" in REACHABLE_ROLES and "eye_sclera" in UNREACHABLE_ROLES


def test_an_unknown_role_is_refused_too():
    """Not just the unreachable ones: a typo'd role would otherwise be an
    `extra="allow"` key in a dict, accepted and ignored."""
    with pytest.raises(pydantic.ValidationError, match="not a role"):
        StylePack(name="x", roles={"skinn": "#800000"})


# --- what a pack changes ------------------------------------------------------


@pytest.mark.parametrize("role", sorted(REACHABLE_ROLES))
def test_every_role_declared_REACHABLE_actually_reaches_the_document(role):
    """The counterpart `UNREACHABLE_ROLES` never had, and its absence is how
    `pupil` shipped declared-reachable and wired to nothing.

    `an.styles` refuses a role that silently does nothing. A role declared
    reachable and read by no code is the same defect pointing the other way,
    and the guard that was supposed to catch it asserted set membership against
    the set it was checking — a tautology in the same file (an#112 review, H1).

    This compiles. A marker colour on one role must appear in the compiled
    document, or the declaration is a lie.
    """
    import warnings

    marker = "#abcdef"
    shot = Shot(
        id="s1",
        renderer="cutout",
        duration=1.0,
        entities=[
            AssetRef(kind="environment", id="room", store="environments", ref="park"),
            AssetRef(kind="character", id="charlie", store="characters", ref="c"),
        ],
    )
    if role == "leg":
        # The placeholder rig has no legs (`_PLACEHOLDER_PARTS`), so the role
        # needs a character that declares them — which is also why the leg
        # assertion in the older test below had to be left out of its own set.
        store = {"c": {"parts": ["torso", "left_leg", "right_leg"]}}
    else:
        store = {}
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", CutoutCompileWarning)
        scene = compile_shot(
            shot,
            mall={"characters": store},
            fps=24,
            width=W,
            height=H,
            style_pack=StylePack(name="probe", roles={role: marker}),
        )
    assert marker in _colours(scene), f"{role} is declared reachable and reaches nothing"


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


# --- the wiring, which had no test at all (an#112 review, M2) ---------------


def test_the_render_path_resolves_the_pack_from_the_scenes_meta():
    """Hop one of the wiring: `scene.meta.style_pack` → `RenderContext`.

    Nothing tested that `an render` applies a pack at all — every other test
    here calls `compile_shot` with it already resolved, so this hop could have
    been deleted with a green suite (an#112 review, M2/M16).
    """
    import json as _json
    import tempfile
    from pathlib import Path as _Path

    import an.adapters.cutout.render as render_mod
    from an.project import init, load
    from an.render import render as render_project

    with tempfile.TemporaryDirectory() as tmp:
        root = init(_Path(tmp) / "p")
        (root / "assets" / "styles").mkdir(parents=True, exist_ok=True)
        (root / "assets" / "styles" / "noir.json").write_text(
            _json.dumps(_json.loads(StylePack(name="noir", roles={"sky": "#3a3a44"}).model_dump_json())),
            encoding="utf-8",
        )
        # `an init` scaffolds a scene with NO shots, so the render refuses
        # before reaching a renderer — add one, or this tests nothing.
        md = (root / "scene.md").read_text(encoding="utf-8").replace(
            "default_renderer: cutout", "default_renderer: cutout\nstyle_pack: noir"
        )
        md += "\n## Shot s1 (cutout)\n\n```yaml shot\nduration: 1.0\n```\n"
        (root / "scene.md").write_text(md, encoding="utf-8")
        (root / "ir" / "scene.json").unlink(missing_ok=True)

        seen: list[object] = []
        original = render_mod.CutoutRenderer.render

        def spy(self, shot, ctx):
            seen.append(ctx.style_pack)
            raise RuntimeError("stop before the browser")

        render_mod.CutoutRenderer.render = spy
        try:
            with pytest.raises(Exception):
                render_project(load(root), auto_audio=False)
        finally:
            render_mod.CutoutRenderer.render = original

    assert seen, "the renderer was never reached"
    assert getattr(seen[0], "name", None) == "noir", seen


def test_the_renderer_hands_the_pack_to_the_compiler(monkeypatch):
    """Hop two: `RenderContext.style_pack` → `compile_shot`. Dropping it left
    the feature inert with a green suite (an#112 review, M17).

    Asserted at the seam through `stop_at_compile_shot`, so it runs in the
    default lane. `render` checks for ffmpeg and imports `playwright.sync_api`
    *before* it compiles, so a guard that lets those run is a guard only on a
    developer machine — this test's first CI run failed for exactly that
    reason while passing locally.
    """
    import tempfile
    from pathlib import Path as _Path

    import an.adapters.cutout.render as render_mod

    seam = stop_at_compile_shot(monkeypatch)

    with tempfile.TemporaryDirectory() as tmp:
        ctx = render_mod.RenderContext(
            mall={},
            work_dir=_Path(tmp),
            style_pack=StylePack(name="noir"),
        )
        # `seam.Stop`, not `Exception`: a bare Exception also swallows the
        # missing-ffmpeg error, which would turn "the seam was never reached"
        # into a passing line.
        with pytest.raises(seam.Stop):
            render_mod.CutoutRenderer().render(_shot(), ctx)

    assert seam.reached, "the render aborted before compile_shot; the guard saw nothing"
    assert getattr(seam.kwargs.get("style_pack"), "name", None) == "noir", seam.kwargs


def test_the_preview_path_carries_it_too():
    """`an preview` compiled without the pack, so a project declaring one
    previewed in the default palette and rendered in the pack's — the author
    iterating against the wrong picture (an#112 review, H2)."""
    import inspect

    import an.preview as preview_mod

    src = inspect.getsource(preview_mod)
    assert "style_pack=style_pack_for(" in src


def test_a_per_entity_override_reaches_the_CHARACTER_palette():
    """The existing override test used the environment only, so dropping
    `entity=` from `resolve_palette` passed the suite (an#112 review, M3)."""
    import warnings

    pack = StylePack(
        name="noir",
        roles={"skin": "#d8d8d8"},
        entities={"charlie": {"skin": "#00ff00"}},
    )
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", CutoutCompileWarning)
        painted = set(_colours(_compiled(pack)))
    assert "#00ff00" in painted and "#d8d8d8" not in painted


def test_the_leg_role_reaches_a_character_that_HAS_legs():
    """The older test declares a `leg` colour and leaves it out of its own
    assertion, because the placeholder rig has no legs — so the role was
    effectively untested (an#112 review, M5)."""
    import warnings

    shot = Shot(
        id="s1", renderer="cutout", duration=1.0,
        entities=[AssetRef(kind="character", id="charlie", store="characters", ref="c")],
    )
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", CutoutCompileWarning)
        scene = compile_shot(
            shot,
            mall={"characters": {"c": {"parts": ["torso", "left_leg", "right_leg"]}}},
            fps=24,
            style_pack=StylePack(name="noir", roles={"leg": "#101014"}),
        )
    assert "#101014" in _colours(scene)


def test_the_default_leg_and_pupil_colours_are_the_literals_they_replaced():
    """Both moved from inline literals into named constants. A constant whose
    value drifts is a picture change nobody asked for, and only a labelled
    golden run would otherwise catch it."""
    from an.adapters.cutout.compile import DFLT_LEG_COLOUR, DFLT_PUPIL_COLOUR

    assert DFLT_LEG_COLOUR == "#2c3e50"
    assert DFLT_PUPIL_COLOUR == "#1a1a1a"


def test_the_compiled_document_records_which_pack_produced_it():
    """It recorded nothing: `compile_shot` never passed `style_pack` into the
    meta, so the field its docstring called provenance was unconditionally
    None (an#112 review, H4)."""
    import warnings

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", CutoutCompileWarning)
        assert _compiled(StylePack(name="noir")).meta.style_pack == "noir"
        assert _compiled(None).meta.style_pack is None


def test_the_pack_has_no_field_that_nothing_reads():
    """`LineStyle` was written, serialized and consumed nowhere — the shape
    `UNREACHABLE_ROLES` refuses, in the module that states the rule. Removed;
    this keeps it removed until something reads it (an#112 review, M1)."""
    assert not hasattr(StylePack(name="x"), "line")
    assert "LineStyle" not in dir(__import__("an.styles", fromlist=["x"]))


def test_an_empty_style_pack_name_is_not_a_pack_in_any_of_the_three_places():
    """The resolver treated `""` as no pack while both serializers wrote a
    visible `style_pack: ""` — a field that renders and does nothing."""
    assert style_pack_for(Meta(style_pack=""), {}) is None
    assert "style_pack" not in json.loads(Meta(style_pack="").model_dump_json())
    from an.ir.sync import ir_to_markdown
    from an.ir.schema import SceneIR

    assert "style_pack" not in ir_to_markdown(SceneIR(meta=Meta(style_pack="")))
