"""Migration registry: identity, chained steps, and kind isolation.

The kind dimension exists because this repo versions two document kinds
independently — the scene IR (`version`) and the character descriptor
(`schema_version`) — and both currently sit at `"0.1.0"`. Before it, a
character migration registered as `("0.1.0", "0.2.0")` was a well-formed entry
in a registry that could not tell the kinds apart, and would run against a
*scene*. See `misc/docs/wave4_research.md` §5 and an#77.
"""

from __future__ import annotations

import pytest

from an.base import SCHEMA_VERSION
from an.ir.migrate import (
    KINDS,
    MIGRATIONS,
    DocumentKind,
    kind_of,
    migrate,
    register_kind,
    register_migration,
)

SCENE = "SceneIR"


@pytest.fixture
def registry_sandbox():
    """Restore both registries, so a test's registrations cannot leak."""
    migrations = dict(MIGRATIONS)
    kinds = dict(KINDS)
    yield
    MIGRATIONS.clear()
    MIGRATIONS.update(migrations)
    KINDS.clear()
    KINDS.update(kinds)


def test_identity_migration_runs():
    doc = {"version": SCHEMA_VERSION, "kind": SCENE, "meta": {}, "timeline": []}
    out = migrate(doc, target_version=SCHEMA_VERSION)
    assert out["version"] == SCHEMA_VERSION


def test_chain_through_two_steps(registry_sandbox):
    @register_migration(SCENE, "0.0.1", "0.0.2")
    def _a(doc):
        doc["touched_a"] = True
        doc["version"] = "0.0.2"
        return doc

    @register_migration(SCENE, "0.0.2", SCHEMA_VERSION)
    def _b(doc):
        doc["touched_b"] = True
        doc["version"] = SCHEMA_VERSION
        return doc

    out = migrate({"version": "0.0.1"}, target_version=SCHEMA_VERSION)
    assert out["touched_a"]
    assert out["touched_b"]
    assert out["version"] == SCHEMA_VERSION


def test_no_path_raises():
    with pytest.raises(ValueError):
        migrate({"version": "999.999.999"}, target_version=SCHEMA_VERSION)


def test_the_error_names_the_kind_it_could_not_migrate():
    """A path error that doesn't say which registry it searched sends the
    reader to the wrong one — the whole failure this dimension prevents."""
    with pytest.raises(ValueError, match="CharacterDescriptor"):
        migrate(
            {"kind": "CharacterDescriptor", "schema_version": "999.0.0"},
            target_version="0.1.0",
        )


# --------------------------------------------------------------------------
# The regression the kind dimension exists for
# --------------------------------------------------------------------------


def test_a_migration_for_one_kind_never_runs_against_another(registry_sandbox):
    """THE regression (an#77).

    Both schemas sit at 0.1.0. Registering a character migration 0.1.0 -> 0.2.0
    must leave a *scene* at 0.1.0 untouched. Keyed on (from, to) alone it did
    not: the wrong function ran, silently, on the wrong document.
    """

    @register_migration("CharacterDescriptor", "0.1.0", "0.2.0")
    def _character_only(doc):
        doc["ran_character_migration"] = True
        doc["schema_version"] = "0.2.0"
        return doc

    scene = migrate({"kind": SCENE, "version": "0.1.0"}, target_version="0.1.0")
    assert "ran_character_migration" not in scene

    character = migrate(
        {"kind": "CharacterDescriptor", "schema_version": "0.1.0"},
        target_version="0.2.0",
    )
    assert character["ran_character_migration"] is True
    assert character["schema_version"] == "0.2.0"


def test_each_kind_reads_its_own_version_field(registry_sandbox):
    """`version` vs `schema_version` is why a migrator cannot just reach for
    `doc["version"]` — the descriptor has never had that key."""

    @register_migration("CharacterDescriptor", "0.1.0", "0.2.0")
    def _bump(doc):
        doc["schema_version"] = "0.2.0"
        return doc

    # A descriptor carrying a stray `version` key must be read by
    # `schema_version` regardless, or it migrates from the wrong source.
    out = migrate(
        {"kind": "CharacterDescriptor", "schema_version": "0.1.0", "version": "9.9.9"},
        target_version="0.2.0",
    )
    assert out["schema_version"] == "0.2.0"


def test_the_target_defaults_to_the_kinds_own_current_version():
    """A shared default would migrate a descriptor toward the *scene's*
    version, which is how the two schemas got conflated in the first place."""
    from an.characters.schema import CHARACTER_SCHEMA_VERSION

    assert kind_of({"kind": "CharacterDescriptor"}).current_version == (
        CHARACTER_SCHEMA_VERSION
    )
    assert kind_of({"kind": SCENE}).current_version == SCHEMA_VERSION


def test_both_shipped_kinds_are_registered():
    """A kind that registers only when its own module happens to be imported is
    a kind that raises "unknown document kind" in half the processes that need
    it. `an/ir/__init__.py` imports the character schema for exactly this
    reason, the way `an.adapters` imports its backends.

    Asserted in a FRESH interpreter, because by the time this test runs the
    suite has imported nearly everything — so an in-process assertion would
    pass even if the guarantee were gone.
    """
    import subprocess
    import sys

    out = subprocess.run(
        [
            sys.executable,
            "-c",
            "import an; from an.ir.migrate import KINDS; print(sorted(KINDS))",
        ],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert "CharacterDescriptor" in out, out
    assert "SceneIR" in out, out


def test_an_unregistered_kind_raises_rather_than_guessing(registry_sandbox):
    """Guessing a version field for an unknown kind fabricates an answer."""
    with pytest.raises(ValueError, match="unknown document kind"):
        migrate({"kind": "NotAThing", "version": "0.1.0"})


def test_a_kind_declares_where_its_version_lives(registry_sandbox):
    widget = register_kind(DocumentKind("Widget", "widget_version", "3.0"))
    assert widget.version_of({"widget_version": "1.0"}) == "1.0"
    assert widget.version_of({}) == "3.0", "absent version means this build's"
