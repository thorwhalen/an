"""MutableMapping conformance + project mall sanity."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from an import build_project_mall
from an.ir.schema import Meta, SceneIR, Shot
from an.stores.characters import CharactersStore
from an.stores.decisions import DecisionLogStore
from an.stores.scenes import ScenesStore
from an.stores.voices import VoicesStore


def test_voices_store_basic_crud():
    with tempfile.TemporaryDirectory() as d:
        store = VoicesStore(d)
        assert len(store) == 0
        store["maya"] = {"provider": "elevenlabs", "voice_id": "abc"}
        assert "maya" in store
        assert store["maya"]["provider"] == "elevenlabs"
        assert list(store) == ["maya"]
        del store["maya"]
        assert "maya" not in store
        assert len(store) == 0


def test_characters_store_writes_meta_and_sidecar():
    with tempfile.TemporaryDirectory() as d:
        store = CharactersStore(d)
        store["maya"] = {"name": "Maya", "voice_ref": "maya-warm"}
        assert store["maya"]["name"] == "Maya"
        sidecar = store.sidecar_path("maya", "torso.svg")
        sidecar.write_text("<svg/>", encoding="utf-8")
        # Re-reading the meta shouldn't be disturbed by the sidecar.
        assert store["maya"]["voice_ref"] == "maya-warm"


def test_invalid_keys_rejected():
    with tempfile.TemporaryDirectory() as d:
        store = VoicesStore(d)
        with pytest.raises(KeyError):
            store["bad/key"] = {}
        with pytest.raises(KeyError):
            store[".hidden"] = {}
        with pytest.raises(KeyError):
            store[""] = {}


def test_scenes_store_round_trips_via_mall():
    with tempfile.TemporaryDirectory() as d:
        mall = build_project_mall(d, ensure=True)
        scene = SceneIR(
            meta=Meta(title="round", duration=3.0),
            timeline=[Shot(id="s1", duration=3.0)],
        )
        mall["scenes"]["main"] = scene
        reloaded = mall["scenes"]["main"]
        assert reloaded.meta.title == "round"
        assert reloaded.timeline[0].id == "s1"
        # Both md and json should exist.
        assert (Path(d) / "scene.md").exists()
        assert (Path(d) / "ir" / "scene.json").exists()


def test_scenes_store_only_supports_main_key():
    with tempfile.TemporaryDirectory() as d:
        mall = build_project_mall(d, ensure=True)
        with pytest.raises(KeyError):
            _ = mall["scenes"]["other"]
        with pytest.raises(KeyError):
            mall["scenes"]["other"] = SceneIR()


def test_decision_log_appends_in_order():
    with tempfile.TemporaryDirectory() as d:
        log = DecisionLogStore(Path(d) / "decisions.jsonl")
        log.append(kind="a", body=1)
        log.append(kind="b", body=2)
        log.append(kind="c", body=3)
        assert len(log) == 3
        entries = [log[k] for k in log]
        assert [e["body"] for e in entries] == [1, 2, 3]
        assert [e["kind"] for e in entries] == ["a", "b", "c"]


def test_decision_log_is_append_only():
    with tempfile.TemporaryDirectory() as d:
        log = DecisionLogStore(Path(d) / "decisions.jsonl")
        log.append(kind="a", body=1)
        with pytest.raises(TypeError):
            log["0"] = {"foo": "bar"}
        with pytest.raises(TypeError):
            del log["0"]


def test_mall_keys_match_spec():
    with tempfile.TemporaryDirectory() as d:
        mall = build_project_mall(d, ensure=True)
        assert sorted(mall.keys()) == [
            "audio",
            "characters",
            "decisions",
            "environments",
            "output",
            "previews",
            "scenes",
            "shots",
            "styles",
            "visemes",
            "voices",
        ]


def test_mall_overrides_swap_stores():
    with tempfile.TemporaryDirectory() as d:
        custom: dict = {}  # plain dict acts as a MutableMapping
        mall = build_project_mall(d, ensure=True, voices=custom)
        mall["voices"]["x"] = {"hello": "world"}
        assert custom["x"] == {"hello": "world"}


def test_scenes_store_is_a_scenes_store():
    """Sanity: the mall's scenes slot is the right type."""
    with tempfile.TemporaryDirectory() as d:
        mall = build_project_mall(d, ensure=True)
        assert isinstance(mall["scenes"], ScenesStore)
