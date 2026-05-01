"""Scenes store — wraps the project's ``scene.md`` + ``ir/scene.json`` pair.

Right now an an project contains exactly one scene ("main"), so this store
exposes only the ``"main"`` key. Future versions can support multi-scene
projects by promoting siblings inside a ``scenes/`` directory.

Reading returns a ``SceneIR``. Writing accepts a ``SceneIR`` (or a dict that
validates as one) and persists both the JSON and the regenerated Markdown.
"""

from __future__ import annotations

import json
from collections.abc import MutableMapping
from pathlib import Path
from typing import Iterator

from an.ir.schema import SceneIR
from an.ir.sync import ir_to_markdown
from an.util import _read_text, _write_json, _write_text


class ScenesStore(MutableMapping):
    """`MutableMapping` exposing the scene file pair under a project root.

    Keys: currently always ``"main"``. The store enforces this by raising
    ``KeyError`` for other keys.
    """

    SCENE_KEY = "main"

    def __init__(self, project_dir: str | Path) -> None:
        self._root = Path(project_dir)

    @property
    def md_path(self) -> Path:
        return self._root / "scene.md"

    @property
    def json_path(self) -> Path:
        return self._root / "ir" / "scene.json"

    def __getitem__(self, key: str) -> SceneIR:
        if key != self.SCENE_KEY:
            raise KeyError(key)
        if not self.json_path.exists():
            raise KeyError(key)
        return SceneIR.model_validate(json.loads(_read_text(self.json_path)))

    def __setitem__(self, key: str, value: SceneIR | dict) -> None:
        if key != self.SCENE_KEY:
            raise KeyError(f"only the {self.SCENE_KEY!r} key is supported")
        scene = value if isinstance(value, SceneIR) else SceneIR.model_validate(value)
        _write_json(self.json_path, json.loads(scene.model_dump_json()))
        _write_text(self.md_path, ir_to_markdown(scene))

    def __delitem__(self, key: str) -> None:
        if key != self.SCENE_KEY:
            raise KeyError(key)
        for p in (self.md_path, self.json_path):
            if p.exists():
                p.unlink()

    def __iter__(self) -> Iterator[str]:
        if self.json_path.exists():
            yield self.SCENE_KEY

    def __len__(self) -> int:
        return 1 if self.json_path.exists() else 0

    def __repr__(self) -> str:
        return f"ScenesStore({str(self._root)!r})"
