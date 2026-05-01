"""Decision log — append-only JSONL of agent decisions and user approvals.

Each call to ``append`` writes one line to ``.an/decisions.jsonl``. Reading
yields decisions in order. The store exposes a `MutableMapping` interface keyed
by integer index (as a string) for uniformity with the other stores, plus an
``append`` convenience method.
"""

from __future__ import annotations

import json
from collections.abc import MutableMapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator


class DecisionLogStore(MutableMapping):
    """Append-only JSONL log keyed by ordinal index (as string).

    Mutating in-place (``__setitem__``, ``__delitem__``) is intentionally
    forbidden; the log is append-only by design.

    >>> import tempfile, os
    >>> with tempfile.TemporaryDirectory() as d:
    ...     log = DecisionLogStore(os.path.join(d, 'decisions.jsonl'))
    ...     _ = log.append(kind='test', body={'x': 1})
    ...     _ = log.append(kind='test', body={'x': 2})
    ...     entries = list(log.values())
    ...     entries[0]['body']['x'], entries[1]['body']['x']
    (1, 2)
    """

    def __init__(self, log_path: str | Path) -> None:
        self._path = Path(log_path)
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def append(self, *, kind: str, body: Any, **extra: Any) -> int:
        """Append one decision; returns its ordinal index."""
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "kind": kind,
            "body": body,
            **extra,
        }
        with self._path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, sort_keys=True, default=str) + "\n")
        return len(self) - 1

    def _entries(self) -> list[dict[str, Any]]:
        if not self._path.exists():
            return []
        out: list[dict[str, Any]] = []
        for line in self._path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                out.append(json.loads(line))
        return out

    def __getitem__(self, key: str) -> dict[str, Any]:
        try:
            i = int(key)
        except ValueError as e:
            raise KeyError(key) from e
        entries = self._entries()
        if i < 0 or i >= len(entries):
            raise KeyError(key)
        return entries[i]

    def __setitem__(self, key: str, value: Any) -> None:
        raise TypeError("DecisionLogStore is append-only; use .append()")

    def __delitem__(self, key: str) -> None:
        raise TypeError("DecisionLogStore is append-only; cannot delete entries")

    def __iter__(self) -> Iterator[str]:
        for i in range(len(self)):
            yield str(i)

    def __len__(self) -> int:
        return len(self._entries())

    def __repr__(self) -> str:
        return f"DecisionLogStore({str(self._path)!r})"
