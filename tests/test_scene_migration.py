"""A stored scene document is migrated on read (an#105).

Before this, `migrate()` was called with `kind="CharacterDescriptor"` at every
call site in the tree and with a scene at **none** of them: `ScenesStore`,
`sync()` and `project.load` each validated raw JSON. So a registered scene
migration never ran — and because `SceneIR` is `extra="allow"`, a renamed field
would have landed as a *silent default* on every document already on disk.

Each test here registers a throwaway `SceneIR` migration for the duration of
the test, because the real registry holds only the identity step: the point is
that **registering is enough**, which is exactly what was not true.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from an.base import SCHEMA_VERSION
from an.ir.migrate import (
    MIGRATIONS,
    SCENE_IR,
    DocumentMigrationError,
    migrate,
)
from an.ir.sync import SceneValidationError, scene_from_json_doc, sync
from an.project import init
from an.stores.scenes import ScenesStore

OLD = "0.0.99"


@pytest.fixture
def a_registered_migration():
    """A `0.0.99 → current` step that moves `old_title` into `meta.title`."""
    key = (SCENE_IR.name, OLD, SCHEMA_VERSION)
    assert key not in MIGRATIONS, "the real registry must not already hold this"

    def step(doc):
        doc = dict(doc)
        title = doc.pop("old_title", None)
        if title is not None:
            doc.setdefault("meta", {})["title"] = title
        doc["version"] = SCHEMA_VERSION
        return doc

    MIGRATIONS[key] = step
    try:
        yield
    finally:
        del MIGRATIONS[key]


@pytest.fixture
def a_nested_migration():
    """A migration that mutates a NESTED dict — the shape `migrate()`'s old
    comment invited, and the one a rename takes."""
    key = (SCENE_IR.name, OLD, SCHEMA_VERSION)

    def step(doc):
        doc["meta"]["title"] = doc["meta"]["title"].upper()
        doc["version"] = SCHEMA_VERSION
        return doc

    MIGRATIONS[key] = step
    try:
        yield
    finally:
        del MIGRATIONS[key]


def _write_old(project: Path) -> Path:
    """A stored document at the old version, with the pre-rename field."""
    p = project / "ir" / "scene.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        json.dumps({"version": OLD, "kind": "SceneIR", "old_title": "from disk", "timeline": []}),
        encoding="utf-8",
    )
    return p


def test_the_store_migrates_on_read(tmp_path, a_registered_migration):
    """This is the test that fails on the tree before an#105."""
    project = tmp_path / "p"
    (project / "ir").mkdir(parents=True)
    _write_old(project)
    scene = ScenesStore(project)["main"]
    assert scene.version == SCHEMA_VERSION, "the version was not even checked before"
    assert scene.meta.title == "from disk", "the migration did not run"
    assert not hasattr(scene, "old_title") or "old_title" not in scene.model_dump()


def test_sync_migrates_on_read(tmp_path, a_registered_migration):
    """`sync()` reads the JSON directly on the json-only and json-newer paths."""
    project = tmp_path / "p"
    (project / "ir").mkdir(parents=True)
    _write_old(project)
    result = sync(project)
    assert result.wrote_md
    md = (project / "scene.md").read_text(encoding="utf-8")
    assert "from disk" in md, md[:200]


def test_a_silent_default_is_what_this_prevents(tmp_path):
    """Without the migration registered, the same document is REFUSED — not
    quietly validated into a default. That refusal is the whole point: an
    `extra="allow"` model will accept anything, so the version check is the
    only thing standing between an old document and a wrong scene."""
    project = tmp_path / "p"
    (project / "ir").mkdir(parents=True)
    path = _write_old(project)
    with pytest.raises(DocumentMigrationError) as e:
        ScenesStore(project)["main"]
    assert str(path) in str(e.value) and OLD in str(e.value)


def test_a_current_document_is_untouched(tmp_path):
    """The identity step runs and changes nothing — a project that never saw an
    older schema must read back exactly as it was written."""
    root = init(tmp_path / "p")
    before = json.loads((root / "ir" / "scene.json").read_text(encoding="utf-8"))
    scene = ScenesStore(root)["main"]
    after = json.loads(scene.model_dump_json())
    assert after == before


def test_registering_is_enough(a_registered_migration):
    """The contract this PR establishes: a registered migration reaches every
    read path, so a later wave can rename a field without hunting call sites."""
    doc = {"version": OLD, "kind": "SceneIR", "old_title": "x"}
    assert migrate(dict(doc), kind=SCENE_IR.name)["meta"]["title"] == "x"
    assert scene_from_json_doc(doc).meta.title == "x"


def test_the_error_names_the_file_and_both_versions(tmp_path):
    with pytest.raises(DocumentMigrationError) as e:
        scene_from_json_doc({"version": "9.9.9"}, source="ir/scene.json")
    msg = str(e.value)
    assert "ir/scene.json" in msg and "9.9.9" in msg and SCHEMA_VERSION in msg


def test_every_read_path_goes_through_the_loader():
    """A guard, not a formality — and an AST one, because the regex it replaced
    caught exactly the two spellings that were in the tree before an#105 and
    missed five more (`model_validate_json`, a differently-named local, a
    `Path.read_text`, `SceneIR(**doc)`, `from json import loads`), and looked at
    three hard-coded files, one of which contained nothing to match while the
    real bypass — `an/ir/validate.py` — was not listed.

    The rule: anywhere under `an/`, a `SceneIR` may not be built from a value
    that came out of a file. Two places are allowed to, and they are the
    boundary itself."""
    import ast

    root = Path(__file__).resolve().parents[1] / "an"
    allowed = {"an/ir/sync.py"}  # the loader, and only the loader
    offenders = []

    class Scan(ast.NodeVisitor):
        def __init__(self, rel):
            self.rel = rel
            self.from_file = set()  # names bound to something read off disk

        def visit_Assign(self, node):
            if self._reads_a_file(node.value):
                for t in node.targets:
                    if isinstance(t, ast.Name):
                        self.from_file.add(t.id)
            self.generic_visit(node)

        @staticmethod
        def _reads_a_file(node) -> bool:
            src = ast.dump(node)
            return ("read_text" in src or "_read_text" in src or "loads" in src) and "model_dump_json" not in src

        def visit_Call(self, node):
            f = node.func
            builds_scene = (
                isinstance(f, ast.Attribute)
                and f.attr.startswith("model_validate")
                and isinstance(f.value, ast.Name)
                and f.value.id == "SceneIR"
            ) or (isinstance(f, ast.Name) and f.id == "SceneIR" and any(k.arg is None for k in node.keywords))
            if builds_scene and self.rel not in allowed:
                arg = node.args[0] if node.args else None
                came_from_disk = arg is not None and (
                    self._reads_a_file(arg)
                    or (isinstance(arg, ast.Name) and arg.id in self.from_file)
                )
                if came_from_disk or any(
                    isinstance(k.value, ast.Name) and k.value.id in self.from_file for k in node.keywords
                ):
                    offenders.append(f"{self.rel}:{node.lineno}: builds a SceneIR from stored bytes")
            self.generic_visit(node)

    for path in sorted(root.rglob("*.py")):
        rel = str(path.relative_to(root.parent))
        tree = ast.parse(path.read_text(encoding="utf-8"))
        Scan(rel).visit(tree)
    assert not offenders, offenders + ["route it through an.ir.sync.scene_from_json_doc"]


def test_the_compat_window_is_honoured(monkeypatch):
    """`an/base.py` promises COMPATIBLE_VERSION is readable WITHOUT migration.
    A loader that demanded an exact match would refuse every stored project the
    day the version is bumped — which is the next PR in this wave (an#105
    review, D1)."""
    import sys

    # `an.ir.migrate` and `an.ir.sync` are the FUNCTIONS re-exported by the
    # package `__init__`; the modules come from `sys.modules`.
    mig = sys.modules["an.ir.migrate"]
    sync_mod = sys.modules["an.ir.sync"]
    # Pinned to invented versions, not the repo's current ones: this asserts
    # the RULE, and must not start passing or failing when the schema moves.
    bumped = mig.DocumentKind("SceneIR", "version", "7.3.0")
    monkeypatch.setitem(mig.KINDS, "SceneIR", bumped)
    monkeypatch.setattr(mig, "SCHEMA_VERSION", "7.3.0")
    monkeypatch.setattr(mig, "COMPATIBLE_VERSION", "7.1.0")
    monkeypatch.setattr(sync_mod, "SCENE_IR", bumped)
    doc = {"version": "7.1.0", "kind": "SceneIR", "meta": {"title": "still readable"}}
    assert mig.readable_without_migration("7.1.0", bumped) is True
    assert scene_from_json_doc(doc).meta.title == "still readable", "the compat floor was ignored"
    # …but below the floor is still a refusal.
    with pytest.raises(DocumentMigrationError):
        scene_from_json_doc({"version": "7.0.9", "kind": "SceneIR"})


def test_a_document_from_a_newer_build_says_so():
    with pytest.raises(DocumentMigrationError, match="newer build"):
        scene_from_json_doc({"version": "9.9.9"}, source="ir/scene.json")


@pytest.mark.parametrize("bad", [None, 0.1, "draft", ""])
def test_a_malformed_version_is_diagnosed_as_malformed(bad):
    """Not "no migration path" — the field is broken, the document may be fine."""
    with pytest.raises(DocumentMigrationError, match="not a schema version"):
        scene_from_json_doc({"version": bad}, source="ir/scene.json")


def test_a_corrupt_field_names_the_document(tmp_path):
    """The common failure is a bad field, and a nameless pydantic traceback is
    what this boundary exists to replace (an#105 review, D6)."""
    project = tmp_path / "p"
    (project / "ir").mkdir(parents=True)
    (project / "ir" / "scene.json").write_text(
        json.dumps({"version": SCHEMA_VERSION, "meta": {"duration": None}}), encoding="utf-8"
    )
    with pytest.raises(SceneValidationError) as e:
        ScenesStore(project)["main"]
    assert "scene.json" in str(e.value) and "duration" in str(e.value)


def test_the_store_refuses_to_write_what_it_cannot_read(tmp_path):
    """`an` could write a project it would then refuse to open (an#105 review,
    D2): the write path validated a dict without migrating it."""
    root = init(tmp_path / "p")
    store = ScenesStore(root)
    with pytest.raises(DocumentMigrationError):
        store["main"] = {"version": "0.0.42", "kind": "SceneIR", "meta": {"title": "t"}, "timeline": []}
    assert json.loads((root / "ir" / "scene.json").read_text(encoding="utf-8"))["version"] == SCHEMA_VERSION


def test_sync_migrates_on_the_json_newer_branch(tmp_path, a_registered_migration):
    """The branch that runs in every real project: both files present, the JSON
    newer because the pipeline wrote it. The first version of this test only
    ever exercised the json-ONLY branch (an#105 review, D5)."""
    import os

    project = tmp_path / "p"
    (project / "ir").mkdir(parents=True)
    (project / "scene.md").write_text("# X\n\n```yaml meta\ntitle: from md\nduration: 1\n```\n", encoding="utf-8")
    json_path = _write_old(project)
    md_mtime = (project / "scene.md").stat().st_mtime
    os.utime(json_path, (md_mtime + 10, md_mtime + 10))
    result = sync(project)
    assert result.wrote_md
    assert "from disk" in (project / "scene.md").read_text(encoding="utf-8")


def test_the_loader_uses_the_scene_registry_whatever_the_document_claims(a_registered_migration):
    """`kind_of` resolves `kind or doc.get("kind") or DFLT_KIND`, so a scene.json
    carrying a stray `kind: "CharacterDescriptor"` must not be handed to the
    descriptor's migrations — that namespace conflation is why `kind` is in the
    registry key at all."""
    doc = {"version": OLD, "kind": "CharacterDescriptor", "old_title": "x"}
    with pytest.raises(Exception) as e:  # forced to SceneIR, then refused by the Literal
        scene_from_json_doc(doc)
    assert "CharacterDescriptor" in str(e.value)


def test_a_migration_may_not_corrupt_the_callers_document(a_nested_migration):
    """`migrate()` deep-copies (an#105 review, D8): the shallow copy protected
    the top level only, so a nested rename — which is what Wave 7 does —
    rewrote the caller's dict while leaving its version key untouched, the
    shape that looks safe."""
    doc = {"version": OLD, "kind": "SceneIR", "meta": {"title": "from disk"}}
    before = json.loads(json.dumps(doc))
    scene = scene_from_json_doc(doc)
    assert scene.meta.title == "FROM DISK"
    assert doc == before, "the caller's document was mutated"


def test_validate_schema_migrates_too(a_registered_migration):
    """`an.validate_schema` is an exported read path: "validate before you
    spend" told an agent a stale document was clean, because `extra="allow"`
    accepts the pre-migration shape (an#105 review, D3)."""
    from an.ir.validate import validate_schema

    stale = {"version": "0.0.1", "kind": "SceneIR", "meta": {"title": "x"}}
    report = validate_schema(stale)
    assert not report.passed and any("0.0.1" in f.description for f in report.findings)
    assert validate_schema(json.dumps(stale)).passed is False
    assert validate_schema({"version": OLD, "kind": "SceneIR", "old_title": "x"}).passed


def _line(text: str, prefix: str) -> str:
    """The one line of `text` starting with `prefix` — or a failure naming it."""
    hits = [ln for ln in text.splitlines() if ln.startswith(prefix)]
    assert len(hits) == 1, f"{prefix!r} matched {len(hits)} lines"
    return hits[0]


def _cli(args: list[str]):
    """Run the real typer app in-process and return typer's Result.

    Not a subprocess: `result.exception` is what distinguishes "printed a
    refusal" from "swallowed a bug", and a subprocess only exposes the exit
    code and the text.
    """
    from typer.testing import CliRunner

    from an.__main__ import build_app
    from an.tools import _dispatch_funcs, _dispatch_namespaces

    return CliRunner().invoke(build_app(_dispatch_funcs, _dispatch_namespaces), args)


def test_an_validate_blames_the_stored_json_not_the_markdown(tmp_path):
    """`an validate` printed "scene.md does not parse" for a document whose md
    parses perfectly — routing an agent to edit the wrong file (an#105 review,
    D7). `an sync` and `an render` stack-dumped."""
    from an import tools
    from an.orchestrate import validate_project

    import os

    root = init(tmp_path / "p")
    json_path = root / "ir" / "scene.json"
    json_path.write_text(
        json.dumps({"version": "0.0.1", "kind": "SceneIR", "timeline": []}), encoding="utf-8"
    )
    # The JSON must be the newer file, or `sync()` never reads it — which is
    # itself the honest behaviour, and the reason this test says so out loud.
    md_mtime = (root / "scene.md").stat().st_mtime
    os.utime(json_path, (md_mtime + 10, md_mtime + 10))
    report = validate_project(root)
    (finding,) = [f for f in report.findings if f.severity == "error"]
    assert finding.ir_path == "ir/scene.json" and "scene.md does not parse" not in finding.description
    # …and the CLI prints the refusal rather than a traceback, WITHOUT
    # reporting success: `an sync` / `an render` exit 1.
    for cmd in (["sync", str(root)], ["render", str(root)]):
        result = _cli(cmd)
        assert "0.0.1" in result.output, result.output
        assert result.exit_code == 1, (cmd, result.exit_code, result.output)


# --- an#106: the rename, and what it must not do quietly ---------------------


def test_a_stored_0_1_0_document_is_renamed_on_read(tmp_path):
    """The wave's first real migration, through the read path an#105 wired."""
    project = tmp_path / "p"
    (project / "ir").mkdir(parents=True)
    (project / "ir" / "scene.json").write_text(
        json.dumps(
            {
                "version": "0.1.0",
                "kind": "SceneIR",
                "meta": {"title": "t", "default_style": "manim"},
                "timeline": [{"id": "s1", "style": "cutout", "duration": 1.0}],
            }
        ),
        encoding="utf-8",
    )
    scene = ScenesStore(project)["main"]
    assert scene.version == "0.2.0" and scene.compatible_version == "0.2.0"
    assert scene.meta.default_renderer == "manim"
    assert scene.timeline[0].renderer == "cutout"
    assert "style" not in scene.timeline[0].model_dump()


def test_a_retired_style_entity_is_dropped_not_carried(tmp_path):
    """`AssetRef(kind="style")` selected nothing — the compiler skipped it and
    no reader of the styles store existed — so the migration drops it rather
    than carrying a kind the schema no longer accepts into a validate error on
    a document that renders identically. Art direction returns as a StylePack
    (#112). This is the one place dropping is honest, and it is written down."""
    doc = {
        "version": "0.1.0",
        "kind": "SceneIR",
        "timeline": [
            {
                "id": "s1",
                "style": "cutout",
                "duration": 1.0,
                "entities": [
                    {"kind": "style", "id": "s", "store": "styles", "ref": "s-v1"},
                    {"kind": "voice", "id": "v", "store": "voices", "ref": "v-v1"},
                ],
            }
        ],
    }
    scene = scene_from_json_doc(doc)
    kinds = [e.kind for e in scene.timeline[0].entities]
    assert kinds == ["voice"], kinds
    from pydantic import ValidationError

    with pytest.raises(ValidationError):  # and a NEW one is refused outright
        from an.ir.schema import AssetRef

        AssetRef(kind="style", id="s", store="styles", ref="s")


def test_the_migration_adds_no_key_the_document_did_not_have():
    """A migration that invents `entities: []` changes a document for no
    reason, and every gratuitous key is one more diff a reader must explain."""
    from an.ir.migrate import _rename_style_to_renderer

    doc = {"version": "0.1.0", "timeline": [{"id": "s", "style": "cutout"}]}
    out = _rename_style_to_renderer(dict(doc))
    assert out["timeline"][0] == {"id": "s", "renderer": "cutout"}
    # …and no key at the TOP level either: the first version of this test only
    # looked inside `timeline`, so a mutant that added `assets: []` to every
    # document survived it (an#106 review).
    assert set(out) == {"version", "timeline"}, sorted(out)


def test_default_style_in_scene_md_is_refused_not_silently_dropped():
    """`scene.md` is the human SSOT and carries no schema version, so nothing
    can tell "written before an#106" from "typed today" — and `Meta` is
    `extra="allow"`, so dropping the key would silently replace the author's
    declared renderer with the default."""
    from an.ir.sync import markdown_to_ir

    md = "# X\n\n```yaml meta\ntitle: X\nduration: 1\ndefault_style: manim\n```\n"
    with pytest.raises(ValueError, match="default_renderer"):
        markdown_to_ir(md)


def test_the_shot_heading_is_unchanged_by_the_rename():
    """`## Shot s1 (cutout)` captures the renderer POSITIONALLY, so no scene.md
    heading changes and no downstream package that writes one needs an edit."""
    from an.ir.schema import Meta, SceneIR, Shot
    from an.ir.sync import ir_to_markdown, markdown_to_ir

    # A NON-default renderer, or the round trip proves nothing: with
    # `renderer="cutout"` the parser reading the heading and the parser
    # ignoring it produce the same answer (an#106 review).
    scene = SceneIR(
        meta=Meta(title="t", duration=1.0, default_renderer="cutout"),
        timeline=[Shot(id="s1", renderer="manim", duration=1.0)],
    )
    md = ir_to_markdown(scene)
    assert "## Shot s1 (manim)" in md
    assert markdown_to_ir(md).timeline[0].renderer == "manim", "the heading is what selects it"


def test_the_migration_writes_compatible_version_when_the_document_has_it():
    """M3: every document `an` writes carries `compatible_version`, and leaving
    it at 0.1.0 would advertise that a 0.1.0-shaped document is still readable
    as-is. The earlier test's fixture omitted the key, so the pydantic default
    satisfied its assertion whether or not the migration did anything."""
    from an.ir.migrate import _rename_style_to_renderer

    out = _rename_style_to_renderer(
        {"version": "0.1.0", "compatible_version": "0.1.0", "timeline": []}
    )
    assert out["compatible_version"] == "0.2.0" and out["version"] == "0.2.0"


def test_a_scene_level_style_asset_is_dropped_too():
    """`SceneIR.assets` is the other place a style ref could sit; only
    `Shot.entities` had a test, and the scene-level filter was killed by the
    docstring doctest alone (an#106 review)."""
    out = scene_from_json_doc(
        {
            "version": "0.1.0",
            "kind": "SceneIR",
            "assets": [
                {"kind": "style", "id": "s", "store": "styles", "ref": "s"},
                {"kind": "voice", "id": "v", "store": "voices", "ref": "v"},
            ],
        }
    )
    assert [a.kind for a in out.assets] == ["voice"]


def test_a_hand_repaired_document_is_not_reverted():
    """A user who read the changelog, added `renderer`, and forgot to delete
    `style` had their fix silently overwritten by the stale key (an#106
    review). The new key wins."""
    from an.ir.migrate import _rename_style_to_renderer

    out = _rename_style_to_renderer(
        {
            "version": "0.1.0",
            "meta": {"default_style": "manim", "default_renderer": "cutout"},
            "timeline": [{"id": "s", "style": "manim", "renderer": "cutout"}],
        }
    )
    assert out["meta"] == {"default_renderer": "cutout"}
    assert out["timeline"][0] == {"id": "s", "renderer": "cutout"}


def test_dropping_a_style_entity_is_audible():
    """A silent drop of something the author wrote is the failure
    `tests/test_loud_discards.py` exists for — even when the thing dropped did
    nothing."""
    # `match=` on the ID, not on the word "style": the word is in the
    # boilerplate, so `gone` computed from the wrong list (emitting an empty
    # `[]`) or the ids dropped from the format string both passed (an#106
    # review, L1).
    with pytest.warns(UserWarning, match=r"\['s'\]"):
        scene_from_json_doc(
            {
                "version": "0.1.0",
                "kind": "SceneIR",
                "timeline": [
                    {
                        "id": "s1",
                        "style": "cutout",
                        "duration": 1.0,
                        "entities": [{"kind": "style", "id": "s", "store": "styles", "ref": "s"}],
                    }
                ],
            }
        )


def test_the_iterate_prompt_teaches_the_schema_it_patches_against():
    """M14 — and the defect that made this test necessary: the model-facing
    grammar still said `style` and `kind: style` after the rename, so a patch
    the loop reported as applied landed as an `extra="allow"` no-op and the
    shot kept its default renderer. Derived from the TYPES, never typed out:
    a list in prose is a second SSOT that drifts silently."""
    from an.base import RendererName
    from an.ir.schema import AssetRef
    from an.iterate import _SYSTEM_PROMPT

    # The WORD, not a spelling of it: the first version of this guard checked
    # for `default_style` and `"style"`, and a mutant that wrote the field as
    # `- style ("cutout" | …)` walked straight through (an#106 review).
    import re

    assert not re.search(r"\bstyle\b", _SYSTEM_PROMPT, re.I), [
        line for line in _SYSTEM_PROMPT.splitlines() if re.search(r"\bstyle\b", line, re.I)
    ]
    assert "default_renderer" in _SYSTEM_PROMPT

    # SET EQUALITY against the parsed enumeration, not `name in prompt`.
    # Containment anywhere in a 4 KB string is nearly free: measured, deleting
    # `voice` from the kind line still passed (`voice_ref` appears in the
    # dialogue fields), so did deleting `character` (6 other occurrences), and
    # an `AssetRef.kind` value of `"set"` would pass on `asset_sets` alone —
    # the exact mutant class this test claims to defend (an#106 review, M2).
    renderers = set(RendererName.__args__)
    taught_renderers = set(
        re.findall(r'"([a-z_]+)"', _line(_SYSTEM_PROMPT, "      - renderer ("))
    )
    assert taught_renderers == renderers, taught_renderers ^ renderers

    kinds = set(AssetRef.model_fields["kind"].annotation.__args__)
    taught_kinds = {
        k.strip()
        for k in _line(_SYSTEM_PROMPT, '        "kind" MUST be one of:')
        .split(":", 1)[1]
        .rstrip(".")
        .split(",")
    }
    # `prop` is declared by the IR and deliberately NOT offered: the very next
    # prompt line tells the model not to emit one, and a renderer raises on it.
    # So the prompt teaches the kinds MINUS prop — and this asserts exactly
    # that, so retiring `prop` or adding a fifth kind both fail here.
    assert taught_kinds == kinds - {"prop"}, taught_kinds ^ (kinds - {"prop"})
    assert "\"prop\" is declared by the IR but NOT rendered" in _SYSTEM_PROMPT


def test_an_sync_and_an_render_report_the_markdown_refusal(tmp_path):
    """The refusal a user is guaranteed to hit — `default_style:` in a hand
    written scene.md — reached the CLI as a traceback (an#106 review)."""
    from an import tools

    root = init(tmp_path / "p")
    (root / "scene.md").write_text(
        "# X\n\n```yaml meta\ntitle: X\nduration: 1\ndefault_style: manim\n```\n", encoding="utf-8"
    )
    (root / "ir" / "scene.json").unlink()
    # `an validate` turns it into a Finding and exits 0 — that is what validate
    # is for. `an sync` and `an render` print the same sentence and exit 1.
    assert "default_renderer" in tools.validate(str(root))
    for cmd in (["sync", str(root)], ["render", str(root)]):
        result = _cli(cmd)
        assert "default_renderer" in result.output, result.output
        assert result.exit_code == 1, (cmd, result.exit_code, result.output)


def test_the_cli_prints_a_refusal_but_never_swallows_a_bug(tmp_path):
    """The catch is three NAMED types, not `ValueError`.

    an#106's first pass caught bare `ValueError` inside `an.tools`, reasoning
    that every refusal is one. So is `json.JSONDecodeError`. So is
    `CutoutCompileError`. So is pydantic's `ValidationError`. Measured on that
    build: a corrupt `ir/scene.json` printed one nameless line — `Expecting
    property name enclosed in double quotes: line 1 column 3 (char 2)` — and
    `an sync` exited **0**, as did `an render` on a shot that failed to
    compile. Anything checking an exit code read a broken project as fine.

    So this asserts the boundary in BOTH directions: the refusal is a sentence
    with exit 1, and a corrupt document is still an uncaught traceback with
    exit 1. Widening the catch to `ValueError` or `Exception` fails the second
    half; narrowing it to nothing fails the first."""
    import os

    root = init(tmp_path / "p")
    json_path = root / "ir" / "scene.json"
    json_path.write_text("{ this is not json", encoding="utf-8")
    md_mtime = (root / "scene.md").stat().st_mtime
    os.utime(json_path, (md_mtime + 10, md_mtime + 10))

    result = _cli(["sync", str(root)])
    assert result.exit_code == 1
    assert isinstance(result.exception, json.JSONDecodeError), result.exception
    assert not isinstance(result.exception, SystemExit)


def test_the_assets_filter_reads_a_list_or_leaves_the_value_alone():
    """`assets` is a list of AssetRefs — unless the document is malformed.

    The style-entity filter used `doc.get("assets") or []`, which iterates a
    DICT's keys and rebuilds it as a list of strings, and turns `null` into
    `[]`. Both destroy the caller's data on the way to a rename that has
    nothing to do with assets. Measured on the reverted guard:
    `{"assets": {"hero": {...}}}` migrated to `{"assets": ["hero"]}`."""
    from an.ir.migrate import _rename_style_to_renderer

    assert _rename_style_to_renderer({"version": "0.1.0", "assets": None})["assets"] is None
    weird = {"hero": {"kind": "character"}}
    assert _rename_style_to_renderer({"version": "0.1.0", "assets": weird})["assets"] == weird


def test_a_retired_key_that_survives_migration_is_an_ERROR_at_validate():
    """The prompt fix narrows the odds; THIS closes the hole.

    A `style` key can still reach a 0.2.0 document — an agent patch, a hand
    edit, `Shot(style=...)` in a caller. `Shot` is `extra="allow"`, so it
    validates cleanly and renders with the DEFAULT renderer, and the migration
    will never look at it again because the document is already 0.2.0. Without
    this check, `iterate()` reports success for a change it did not make."""
    from an.ir.schema import Meta, SceneIR, Shot
    from an.ir.validate import validate_semantic

    scene = SceneIR(meta=Meta(), timeline=[Shot(id="s1", renderer="cutout", style="manim")])
    report = validate_semantic(scene)
    assert not report.passed
    (finding,) = [f for f in report.findings if f.ir_path == "timeline[0]/style"]
    assert finding.severity == "error" and "renderer" in finding.description

    meta_scene = SceneIR(meta=Meta(default_style="manim"), timeline=[])
    meta_report = validate_semantic(meta_scene)
    assert not meta_report.passed
    assert meta_report.findings[0].ir_path == "meta/default_style"


def test_every_renderer_name_is_claimed_by_exactly_one_adapter():
    """`supported_renderers` is the adapter's ONE declaration of what it draws.

    Before an#106 it was `supported_styles`, it had zero readers, and each
    adapter's `can_render` compared `shot.renderer` to its own literal — two
    SSOTs for one fact, free to disagree. `can_render` derives from the tuple
    now, and this pins the other half: every value the schema allows has an
    adapter, and no two adapters claim the same one."""
    import an.adapters  # noqa: F401  (registers the three stub backends)
    import an.adapters.cutout  # noqa: F401  (registers the real one)
    from an.adapters._base import get_renderer, list_renderers
    from an.base import SUPPORTED_RENDERERS

    claimed: dict[str, list[str]] = {}
    for name in list_renderers():
        renderer = get_renderer(name)
        for name in renderer.supported_renderers:
            claimed.setdefault(name, []).append(renderer.name)
    assert set(claimed) == set(SUPPORTED_RENDERERS), set(claimed) ^ set(SUPPORTED_RENDERERS)
    assert all(len(v) == 1 for v in claimed.values()), claimed

    # …and the declaration is the one that decides. An adapter whose
    # `can_render` answers for a renderer its tuple does not claim (or refuses
    # one it does) is advertising one thing and doing another — the exact
    # drift that having two SSOTs allows.
    from an.ir.schema import Shot

    for name in list_renderers():
        renderer = get_renderer(name)
        answers = {
            n for n in SUPPORTED_RENDERERS if renderer.can_render(Shot(id="s", renderer=n))
        }
        assert answers == set(renderer.supported_renderers), (name, answers)
