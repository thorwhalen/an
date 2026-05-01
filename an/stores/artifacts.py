"""Artifact stores — derived, regeneratable products of the pipeline.

All artifact stores are content-addressed: the key is meant to be a hash of
the IR slice that produced the artifact. This lets later phases skip re-render
when the inputs haven't changed.

In Phase 1 these are simple file-backed stores; the content-hash convention is
enforced by the caller (orchestrator), not the store.
"""

from __future__ import annotations

from collections.abc import MutableMapping
from pathlib import Path
from typing import Iterator


class _BlobStore(MutableMapping):
    """Generic file-as-bytes store with a fixed extension.

    Subclasses set ``EXT`` to the file extension (without the dot).
    """

    EXT: str = "bin"

    def __init__(self, root_dir: str | Path) -> None:
        self._root = Path(root_dir)
        self._root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        if "/" in key or key.startswith(".") or not key:
            raise KeyError(f"invalid key: {key!r}")
        return self._root / f"{key}.{self.EXT}"

    def __getitem__(self, key: str) -> bytes:
        p = self._path(key)
        if not p.exists():
            raise KeyError(key)
        return p.read_bytes()

    def __setitem__(self, key: str, value: bytes) -> None:
        p = self._path(key)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(value)

    def __delitem__(self, key: str) -> None:
        p = self._path(key)
        if not p.exists():
            raise KeyError(key)
        p.unlink()

    def __iter__(self) -> Iterator[str]:
        suffix = f".{self.EXT}"
        for child in sorted(self._root.glob(f"*{suffix}")):
            yield child.name[: -len(suffix)]

    def __len__(self) -> int:
        return sum(1 for _ in self)

    def path_of(self, key: str) -> Path:
        """Return the on-disk path for a key (whether it exists or not)."""
        return self._path(key)

    def __repr__(self) -> str:
        return f"{type(self).__name__}({str(self._root)!r})"


class AudioArtifactStore(_BlobStore):
    """TTS-rendered audio clips (.wav)."""

    EXT = "wav"


class VisemeArtifactStore(_BlobStore):
    """Lip-sync viseme tracks (.json) — stored as bytes for cache uniformity."""

    EXT = "json"


class ShotArtifactStore(_BlobStore):
    """Per-shot rendered mp4s."""

    EXT = "mp4"


class PreviewArtifactStore(_BlobStore):
    """Low-res preview renders (mp4 or png sequence wrapper)."""

    EXT = "mp4"


class OutputStore(_BlobStore):
    """Final composited renders."""

    EXT = "mp4"
