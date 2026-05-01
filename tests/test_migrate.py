"""Migration registry: identity migration + chained-step migrations."""

from __future__ import annotations

from an.base import SCHEMA_VERSION
from an.ir.migrate import MIGRATIONS, migrate, register_migration


def test_identity_migration_runs():
    doc = {"version": SCHEMA_VERSION, "kind": "SceneIR", "meta": {}, "timeline": []}
    out = migrate(doc, target_version=SCHEMA_VERSION)
    assert out["version"] == SCHEMA_VERSION


def test_chain_through_two_steps(monkeypatch):
    @register_migration("0.0.1", "0.0.2")
    def _a(doc):
        doc["touched_a"] = True
        doc["version"] = "0.0.2"
        return doc

    @register_migration("0.0.2", SCHEMA_VERSION)
    def _b(doc):
        doc["touched_b"] = True
        doc["version"] = SCHEMA_VERSION
        return doc

    try:
        out = migrate({"version": "0.0.1"}, target_version=SCHEMA_VERSION)
        assert out["touched_a"]
        assert out["touched_b"]
        assert out["version"] == SCHEMA_VERSION
    finally:
        MIGRATIONS.pop(("0.0.1", "0.0.2"), None)
        MIGRATIONS.pop(("0.0.2", SCHEMA_VERSION), None)


def test_no_path_raises():
    import pytest
    with pytest.raises(ValueError):
        migrate({"version": "999.999.999"}, target_version=SCHEMA_VERSION)
