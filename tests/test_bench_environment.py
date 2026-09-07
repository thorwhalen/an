"""`an.bench.environment.runtime_sha256` — suffix-filtered runtime digest (an#141)."""

from __future__ import annotations

import an.adapters.cutout.render as render_module
from an.bench.environment import runtime_sha256


def test_file_outside_digest_suffixes_does_not_move_the_hash(tmp_path, monkeypatch):
    """A stray non-asset file (e.g. `.DS_Store`) must not change the digest."""
    (tmp_path / "runtime.js").write_text("console.log('a');")
    monkeypatch.setattr(render_module, "runtime_dir", lambda: tmp_path)
    before = runtime_sha256()

    (tmp_path / ".DS_Store").write_bytes(b"\x00\x01\x02")
    (tmp_path / "notes.md").write_text("irrelevant")

    after = runtime_sha256()
    assert after == before


def test_file_inside_digest_suffixes_does_move_the_hash(tmp_path, monkeypatch):
    """A real runtime asset change must be visible in the digest."""
    (tmp_path / "runtime.js").write_text("console.log('a');")
    monkeypatch.setattr(render_module, "runtime_dir", lambda: tmp_path)
    before = runtime_sha256()

    (tmp_path / "index.html").write_text("<html></html>")

    after = runtime_sha256()
    assert after != before
