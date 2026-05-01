"""Shared store helpers — tiny base classes around dol's filesystem stores.

We don't want every concrete store re-implementing the same JSON / sidecar
machinery. Two patterns dominate:

- **Pure-JSON store**: each value is a JSON document (no binary side files).
  Used for voices, styles, decisions metadata.
- **JSON + sidecar store**: each key maps to a directory containing a
  ``meta.json`` plus arbitrary binary sidecars (sprites, audio, etc.). Used
  for characters, environments, audio artifacts.

Both patterns wrap dol's ``Files`` family.
"""

from __future__ import annotations

import json
from collections.abc import MutableMapping
from pathlib import Path
from typing import Any, Iterator


class JsonDirStore(MutableMapping):
    """`MutableMapping` of name -> JSON document, stored as ``<name>.json`` files.

    Backed by the filesystem under ``root_dir``. Keys are the file stems; values
    are arbitrary JSON-serializable Python objects. ``__contains__``, ``__iter__``,
    and ``__len__`` are derived from directory listing.
    """

    def __init__(self, root_dir: str | Path) -> None:
        self._root = Path(root_dir)
        self._root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        if "/" in key or key.startswith(".") or not key:
            raise KeyError(f"invalid key: {key!r}")
        return self._root / f"{key}.json"

    def __getitem__(self, key: str) -> Any:
        p = self._path(key)
        if not p.exists():
            raise KeyError(key)
        return json.loads(p.read_text(encoding="utf-8"))

    def __setitem__(self, key: str, value: Any) -> None:
        p = self._path(key)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(
            json.dumps(value, indent=2, sort_keys=True, default=str) + "\n",
            encoding="utf-8",
        )

    def __delitem__(self, key: str) -> None:
        p = self._path(key)
        if not p.exists():
            raise KeyError(key)
        p.unlink()

    def __iter__(self) -> Iterator[str]:
        for child in sorted(self._root.glob("*.json")):
            yield child.stem

    def __len__(self) -> int:
        return sum(1 for _ in self)

    def __repr__(self) -> str:
        return f"{type(self).__name__}({str(self._root)!r})"


class JsonSidecarStore(MutableMapping):
    """`MutableMapping` of name -> dict, where each entry is a directory containing
    ``meta.json`` plus optional binary sidecars.

    Reading returns the parsed ``meta.json``. Writing replaces ``meta.json``;
    sidecars must be written separately by the caller (use ``sidecar_path(key, name)``).
    Deleting removes the whole directory.
    """

    META_NAME = "meta.json"

    def __init__(self, root_dir: str | Path) -> None:
        self._root = Path(root_dir)
        self._root.mkdir(parents=True, exist_ok=True)

    def _entry_dir(self, key: str) -> Path:
        if "/" in key or key.startswith(".") or not key:
            raise KeyError(f"invalid key: {key!r}")
        return self._root / key

    def sidecar_path(self, key: str, name: str) -> Path:
        d = self._entry_dir(key)
        d.mkdir(parents=True, exist_ok=True)
        return d / name

    def __getitem__(self, key: str) -> dict[str, Any]:
        meta = self._entry_dir(key) / self.META_NAME
        if not meta.exists():
            raise KeyError(key)
        return json.loads(meta.read_text(encoding="utf-8"))

    def __setitem__(self, key: str, value: dict[str, Any]) -> None:
        d = self._entry_dir(key)
        d.mkdir(parents=True, exist_ok=True)
        (d / self.META_NAME).write_text(
            json.dumps(value, indent=2, sort_keys=True, default=str) + "\n",
            encoding="utf-8",
        )

    def __delitem__(self, key: str) -> None:
        d = self._entry_dir(key)
        if not d.exists():
            raise KeyError(key)
        # Remove the directory and all sidecars.
        for child in d.rglob("*"):
            if child.is_file():
                child.unlink()
        for child in sorted(d.rglob("*"), reverse=True):
            if child.is_dir():
                child.rmdir()
        d.rmdir()

    def __iter__(self) -> Iterator[str]:
        for child in sorted(self._root.iterdir()):
            if child.is_dir() and (child / self.META_NAME).exists():
                yield child.name

    def __len__(self) -> int:
        return sum(1 for _ in self)

    def __repr__(self) -> str:
        return f"{type(self).__name__}({str(self._root)!r})"
