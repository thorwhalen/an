"""Project init / load / save smoke tests."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from an import init, load, save
from an.ir.schema import Meta, SceneIR, Shot


def test_init_creates_full_layout():
    with tempfile.TemporaryDirectory() as d:
        root = init(Path(d) / "demo")
        for sub in (
            "scene.md",
            "ir/scene.json",
            "an.toml",
            "assets/characters",
            "assets/props",
            "assets/environments",
            "assets/voices",
            "assets/styles",
            "artifacts/audio",
            "artifacts/visemes",
            "artifacts/shots",
            "artifacts/previews",
            "output",
            ".an",
            ".an/memory.md",
        ):
            assert (root / sub).exists(), f"missing {sub}"


def test_init_idempotent_when_empty():
    with tempfile.TemporaryDirectory() as d:
        root = Path(d) / "demo"
        init(root)
        # Replacing scene.md with empty content should let init succeed again.
        (root / "scene.md").write_text("", encoding="utf-8")
        init(root)  # no error


def test_init_refuses_to_clobber():
    with tempfile.TemporaryDirectory() as d:
        root = Path(d) / "demo"
        init(root)
        # scene.md now has content; init without force should refuse.
        with pytest.raises(FileExistsError):
            init(root)


def test_init_force_overwrites():
    with tempfile.TemporaryDirectory() as d:
        root = Path(d) / "demo"
        init(root, name="orig")
        init(root, name="new", force=True)
        assert "new" in (root / "an.toml").read_text(encoding="utf-8")


def test_load_returns_project_with_mall_and_scene():
    with tempfile.TemporaryDirectory() as d:
        root = init(Path(d) / "demo")
        proj = load(root)
        assert proj.root == root
        assert "scenes" in proj.mall
        assert proj.scene.kind == "SceneIR"


def test_save_persists_scene_changes():
    with tempfile.TemporaryDirectory() as d:
        root = init(Path(d) / "demo")
        proj = load(root)
        proj.scene = SceneIR(
            meta=Meta(title="changed", duration=2.0),
            timeline=[Shot(id="s1", duration=2.0)],
        )
        save(proj)
        proj2 = load(root)
        assert proj2.scene.meta.title == "changed"
