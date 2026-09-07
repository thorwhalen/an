"""`an.bench.environment.runtime_sha256` — suffix-filtered runtime digest (an#141)."""

from __future__ import annotations

import an.adapters.cutout.render as render_module
from an.bench.environment import (
    RUNTIME_DIGEST_SUFFIXES,
    RUNTIME_IGNORED_SUFFIXES,
    runtime_sha256,
)


def test_file_outside_digest_suffixes_does_not_move_the_hash(tmp_path, monkeypatch):
    """A stray non-asset file (e.g. `.DS_Store`) must not change the digest."""
    (tmp_path / "runtime.js").write_text("console.log('a');", encoding="utf-8")
    monkeypatch.setattr(render_module, "runtime_dir", lambda: tmp_path)
    before = runtime_sha256()

    (tmp_path / ".DS_Store").write_bytes(b"\x00\x01\x02")
    (tmp_path / "notes.md").write_text("irrelevant", encoding="utf-8")

    after = runtime_sha256()
    assert after == before


def test_file_inside_digest_suffixes_does_move_the_hash(tmp_path, monkeypatch):
    """A real runtime asset change must be visible in the digest."""
    (tmp_path / "runtime.js").write_text("console.log('a');", encoding="utf-8")
    monkeypatch.setattr(render_module, "runtime_dir", lambda: tmp_path)
    before = runtime_sha256()

    (tmp_path / "index.html").write_text("<html></html>", encoding="utf-8")

    after = runtime_sha256()
    assert after != before


def test_every_runtime_dir_suffix_is_accounted_for():
    """No file under the real runtime dir may silently fall outside the digest.

    A future runtime asset (an `.svg`, `.woff`, `.wasm`, `.mjs`) must be added
    to `RUNTIME_DIGEST_SUFFIXES` or explicitly declared non-runtime in
    `RUNTIME_IGNORED_SUFFIXES` — never left to fall through both, which is
    exactly the failure mode #141 was filed over.
    """
    from an.adapters.cutout.runtime_files import runtime_dir

    root = runtime_dir()
    known = set(RUNTIME_DIGEST_SUFFIXES) | set(RUNTIME_IGNORED_SUFFIXES)
    unaccounted = {
        p.suffix
        for p in root.rglob("*")
        if p.is_file() and p.suffix not in known
    }
    assert not unaccounted, (
        f"suffixes {sorted(unaccounted)} under {root} are neither hashed nor "
        "declared ignored"
    )
