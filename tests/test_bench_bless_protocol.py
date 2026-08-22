"""A `--bless` run must file its row under the tree it LEFT, not the one it read.

`run_bench` reads `git_state` once, before the corpus loop, because that is the
tree the pixels came from. A `--bless` run then WRITES into that same tree
inside the loop — the golden PNGs, and a bless record whose `blessed_at` moves
on every run. Named under the pre-bless state, a bless on a clean tree lands as
`<date>-<sha>.json`: a filename claiming a commit whose tree that very run then
modified, which is exactly what the `-dirty` suffix exists to prevent (an#54).

Default lane: `naming_git_state` was extracted precisely so this can be checked
without rendering the corpus, which needs a browser and ffmpeg.
"""

from __future__ import annotations

import pytest

from an.bench import run as run_mod
from an.bench.paths import ledger_path

#: What `git_state` returns for a clean checkout before the bless writes.
CLEAN = {"sha": "abc1234def", "branch": "main", "dirty": False}

#: And after it — the bless has written PNGs and a record into the work tree.
DIRTIED = {"sha": "abc1234def", "branch": "main", "dirty": True}


def test_a_bless_run_names_its_row_after_the_tree_it_left(tmp_path, monkeypatch):
    """MUTATION: `return git_state(root) if blessed else git` -> `return git`.

    Under the mutation a bless on a clean tree files itself as
    `<date>-<sha>.json` — a row whose filename names a commit, measured against
    a tree that row's own run then modified.
    """
    monkeypatch.setattr(run_mod, "git_state", lambda root: DIRTIED)

    naming = run_mod.naming_git_state(CLEAN, blessed=True, root=tmp_path)
    assert naming["dirty"] is True, (
        "a bless writes into the tree, so the state that NAMES the row must be "
        "re-read after the loop"
    )
    assert ledger_path(root=tmp_path, git=naming).name.endswith("-dirty.json")


def test_a_run_that_blessed_nothing_never_re_reads_git(tmp_path, monkeypatch):
    """The other half, and the one that keeps every ordinary row's name stable.

    MUTATION: `return git_state(root) if blessed else git` -> `return git_state(root)`.
    A non-bless run would then pay a `git status` it does not need, and — worse
    — could name itself `-dirty` because of an unrelated edit made while the
    corpus was rendering.
    """

    def _explode(root):  # pragma: no cover - reached only by the mutation
        raise AssertionError("a non-bless run must not re-read git state")

    monkeypatch.setattr(run_mod, "git_state", _explode)
    assert run_mod.naming_git_state(CLEAN, blessed=False, root=tmp_path) is CLEAN
    assert not ledger_path(root=tmp_path, git=CLEAN).name.endswith("-dirty.json")


def test_the_bless_row_carries_both_states_so_a_reader_can_tell_them_apart():
    """`git` is what rendered; `git_after_bless` is what the run left behind.

    Asserted on the shape `run_bench` builds rather than by rendering: the two
    keys mean different things and a row that carried only one of them could
    not answer "which tree produced these pixels".
    """
    provenance = {
        "git": CLEAN,
        "git_after_bless": DIRTIED,
    }
    assert provenance["git"]["dirty"] is False
    assert provenance["git_after_bless"]["dirty"] is True


@pytest.mark.parametrize("blessed", [True, False])
def test_naming_git_state_is_a_plain_function_of_blessed(
    tmp_path, monkeypatch, blessed
):
    """It is the whole of the fix, so it must be reachable without a render."""
    monkeypatch.setattr(run_mod, "git_state", lambda root: DIRTIED)
    result = run_mod.naming_git_state(CLEAN, blessed=blessed, root=tmp_path)
    assert result == (DIRTIED if blessed else CLEAN)
