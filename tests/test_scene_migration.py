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
from pathlib import Path

import pytest

from an.base import SCHEMA_VERSION
from an.ir.migrate import MIGRATIONS, SCENE_IR, migrate
from an.ir.sync import SceneMigrationError, scene_from_json_doc, sync
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
    with pytest.raises(SceneMigrationError) as e:
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
    with pytest.raises(SceneMigrationError) as e:
        scene_from_json_doc({"version": "9.9.9"}, source="ir/scene.json")
    msg = str(e.value)
    assert "ir/scene.json" in msg and "9.9.9" in msg and SCHEMA_VERSION in msg


def test_every_read_path_goes_through_the_loader():
    """A guard, not a formality: the defect was three read paths each doing
    their own `SceneIR.model_validate`. A fourth would reintroduce it silently,
    so the sources are checked for a bare validate on stored JSON."""
    import re

    root = Path(__file__).resolve().parents[1]
    offenders = []
    for rel in ("an/stores/scenes.py", "an/ir/sync.py", "an/project.py"):
        src = (root / rel).read_text(encoding="utf-8")
        for i, line in enumerate(src.splitlines(), 1):
            if re.search(r"SceneIR\.model_validate\b", line) and "json.loads" in line:
                offenders.append(f"{rel}:{i}: {line.strip()}")
        # `model_validate(data)` where `data` came from a file read
        for m in re.finditer(r"data = json\.loads\(_read_text\([^)]*\)\)\s*\n\s*scene = SceneIR\.model_validate", src):
            offenders.append(f"{rel}: {m.group(0)[:60]}")
    assert not offenders, offenders
