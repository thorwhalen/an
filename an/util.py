"""Internal helpers: file I/O, hashing, time arithmetic, light path utilities.

Nothing here is part of the public API — everything is underscore-prefixed
or used only by sibling an modules.
"""

from __future__ import annotations

import hashlib
import json
from fractions import Fraction
from pathlib import Path
from typing import Any, Iterable


def _read_text(path: str | Path, *, encoding: str = "utf-8") -> str:
    """Read a text file. Trivial wrapper for consistency."""
    return Path(path).read_text(encoding=encoding)


def _write_text(path: str | Path, content: str, *, encoding: str = "utf-8") -> None:
    """Write a text file, creating parent dirs as needed."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding=encoding)


def _read_json(path: str | Path) -> Any:
    """Load JSON from disk."""
    return json.loads(_read_text(path))


def _write_json(path: str | Path, obj: Any, *, indent: int = 2) -> None:
    """Dump JSON to disk with stable formatting (sorted keys, fixed indent)."""
    _write_text(path, json.dumps(obj, indent=indent, sort_keys=True, default=str) + "\n")


def _stable_hash(obj: Any) -> str:
    """Produce a content hash of an arbitrary JSON-able object.

    Used by artifact stores to key cached renders by the IR slice that
    produced them. Sort keys so equivalent dicts hash the same.

    >>> _stable_hash({"a": 1, "b": 2}) == _stable_hash({"b": 2, "a": 1})
    True
    """
    payload = json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _to_seconds(value: float | int | Fraction) -> float:
    """Normalize a time value to float seconds at the IR boundary."""
    if isinstance(value, Fraction):
        return float(value)
    return float(value)


def _flatten_paths(prefix: str, mapping: dict[str, Any]) -> Iterable[tuple[str, Any]]:
    """Yield (path, leaf) pairs for a nested dict, joining keys with '/'."""
    for key, val in mapping.items():
        new_prefix = f"{prefix}/{key}" if prefix else key
        if isinstance(val, dict):
            yield from _flatten_paths(new_prefix, val)
        else:
            yield new_prefix, val
