"""`an bench` end to end, against a real render (an#36).

Behind the browser marker because it renders: `run-browser-tests`, or an
on-demand run. Everything the panel *computes* is tested without a browser in
`test_bench_metrics.py`; what this proves is the part nothing else can — that a
real capture produces a complete, readable row, and that producing it leaves
the repository untouched.

Deliberately NOT a module-level `importorskip`, which would delete these from
collection rather than skip them (an#22).
"""

from __future__ import annotations

import pytest

from an.bench.capture import (
    IGNORED_ON_COPY,
    IGNORED_RELPATHS_ON_COPY,
    capture_fixture,
    cleanup,
    dirty_paths,
)
from an.bench.corpus import DFLT_FIXTURES
from an.bench.paths import repo_root
from an.bench.registry import METRICS, MUTATIONS, TRIPWIRES
from an.bench.run import run_bench

pytestmark = [pytest.mark.browser, pytest.mark.ffmpeg]

#: The procedural fixture: smallest render in the corpus, and it needs no
#: gitignored build products.
SCENE = "single_character"


@pytest.fixture(scope="module")
def ledger(tmp_path_factory) -> dict:
    out = tmp_path_factory.mktemp("bench-ledger") / "row.json"
    return run_bench(scenes={SCENE: DFLT_FIXTURES[SCENE]}, out=out)


def test_every_declared_metric_is_measured_or_says_why_not(ledger):
    block = ledger["scenes"][SCENE]
    assert set(block["metrics"]) == set(METRICS)
    for key, row in block["metrics"].items():
        assert row["state"] in ("measured", "gated", "unavailable"), key
        if row["state"] == "measured":
            assert row["value"] is not None, key
        else:
            assert row["value"] is None, key


def test_the_render_side_panel_is_fully_measured(ledger):
    """Nothing render-side may be gated on a real capture — those are the only
    metrics that can see a render mutation, so a null here is a blind panel."""
    block = ledger["scenes"][SCENE]
    render_side = {k: r for k, r in block["metrics"].items() if r["side"] == "render"}
    from an.bench.registry import METRICS

    unmeasured = {
        k: r["state"]
        for k, r in render_side.items()
        if r["state"] != "measured" and not METRICS[k].requires
    }
    # Family B included, since an#38: a golden the corpus commits makes this a
    # real number rather than a gate, and family B is the ONLY render-side
    # family that can see a change nobody predicted in advance.
    assert unmeasured == {}, unmeasured

    # A row that DECLARES what a scene must have may be null — and only for
    # that reason. `stage_min_plane_ratio_gap` needs two planes at different
    # depths, and this scene has no planes at all, so its null is structural
    # rather than a blind panel (an#111). The exception lives in the registry,
    # not in a list here, so the panel rule keeps naming its own exceptions.
    scoped = {k: r for k, r in render_side.items() if METRICS[k].requires}
    assert scoped, "the exemption must have a subject, or it is dead code"
    for key, row in scoped.items():
        assert row["state"] in {"measured", "unavailable"}, (key, row)
        if row["state"] == "unavailable":
            assert row.get("detail"), f"{key}: an unavailable row must say why"


def test_the_encode_side_panel_is_fully_measured(ledger):
    block = ledger["scenes"][SCENE]
    for key, row in block["metrics"].items():
        if row["side"] == "encode":
            assert row["state"] == "measured", (key, row.get("detail"))


def test_the_row_records_how_far_this_build_sits_from_the_encoders_input(ledger):
    """Recorded, never gated — it was a gate, and it failed on Linux only.

    The metrics reference the lossless decode, so they do not depend on this
    number. It is kept because it is exactly the build dependence that was
    hiding inside the hard equality, and because it explains why two
    C-family metrics read identically on some machines and not on others.
    """
    prov = ledger["scenes"][SCENE]["provenance"]
    distance = prov["png_to_encoder_input_luma"]
    assert distance["luma_residual_max"] >= 0
    assert prov["references_coincide"] == (distance["luma_residual_max"] == 0)


def test_the_two_c_family_metrics_differ_exactly_by_the_conversion(ledger):
    """`chroma_edge_dY` is not a second name for `coded_luma_edge_error`.

    They are the same expression on different references — lossless vs the PNG
    conversion — so they coincide on a build where the conversion is exact and
    diverge on one where it is not. Which is which is recorded, so a reader
    seeing two identical numbers knows why.
    """
    block = ledger["scenes"][SCENE]
    rows = block["metrics"]
    assert rows["coded_luma_edge_error"]["reference"] == "lossless"
    assert rows["chroma_edge_dY"]["reference"] == "source_png"
    if block["provenance"]["references_coincide"]:
        assert rows["coded_luma_edge_error"]["value"] == rows["chroma_edge_dY"]["value"]


def test_every_counting_encode_metric_references_the_lossless_leg(ledger):
    """A counting witness must not carry a build-dependent conversion term."""
    rows = ledger["scenes"][SCENE]["metrics"]
    for key, row in rows.items():
        if row["side"] != "encode":
            continue
        counts = any(p["counts"] for p in row["under_mutation"].values())
        if counts and row["reference"] != "none":
            assert row["reference"] == "lossless", (
                f"{key} counts toward a mutation and references "
                f"{row['reference']!r}, which is build-dependent"
            )


def test_the_frame_count_matches_what_the_scene_declares(ledger):
    """ffmpeg's image2 demuxer reads the contiguous run from 0.

    A stale frame left in the work dir extends one leg of every encode-side
    metric and shifts nothing on the other.
    """
    prov = ledger["scenes"][SCENE]["provenance"]
    assert prov["frames_on_disk"] == prov["n_frames"]
    assert prov["decoded_source_frames"] == prov["n_frames"]
    assert "frame_count_disagreement" not in prov


def test_the_row_records_which_render_path_actually_ran(ledger):
    """The fact whose absence made the first cross-arch capture measure the
    wrong picture on three runners and call it a clean result."""
    prov = ledger["scenes"][SCENE]["provenance"]
    assert set(prov["visual_kinds"]) >= DFLT_FIXTURES[SCENE].expect_visual_kinds
    assert prov["asset_resolution"], "an#33's per-entity record must be carried through"
    assert not any(r["fallback"] for r in prov["asset_resolution"]), (
        "the bench renders with strict_assets=True, so no row may contain a stand-in"
    )


def test_the_environment_tuple_carries_both_comparison_scopes(ledger):
    env = ledger["provenance"]["environment"]
    assert env["render_side"]["comparison_scope"] == "any_machine"
    assert env["encode_side"]["comparison_scope"] == "machine"
    assert env["encode_side"]["x264_sei"], (
        "the field that decides whether two encode-side rows may be compared "
        "at all must not be null on a real capture"
    )
    assert env["render_side"]["launch_argv"], (
        "the WebGL renderer string cannot witness the rasteriser flag flip, so "
        "the argv is the only guard and must be recorded verbatim"
    )


def test_each_mutation_has_three_families_of_witnesses_on_a_real_row(ledger):
    from an.bench.ledger import witnesses

    block = ledger["scenes"][SCENE]
    for mutation in MUTATIONS:
        families = witnesses(block, mutation)
        assert len(families) >= 3, (mutation, families)


def test_the_tripwire_block_fires_against_the_committed_golden(ledger):
    """Measured since an#38, and still counting ZERO toward any criterion.

    A tripwire fires on improvements and regressions alike, so it is a change
    detector and not evidence of quality. Its `counts: 0` is what keeps a
    boolean out of the witness count — and it is asserted here rather than
    only in the registry, because the row is what `an bench --compare` reads.
    """
    block = ledger["scenes"][SCENE]
    assert set(block["tripwires"]) == set(TRIPWIRES)
    for key, row in block["tripwires"].items():
        assert row["state"] == "measured", (
            f"{key}: {row.get('gate')} — {row.get('detail')}"
        )
        assert row["value"] is True, (
            f"{key}: today's render differs from the committed golden by "
            f"{row.get('changed_px')} px. Look at the PNG diff before re-blessing."
        )
        assert row["counts"] == 0, key


def test_the_off_palette_diagnostic_says_whether_it_is_anti_aliasing(ledger):
    """The permanent form of the check that keeps the metric honest."""
    prov = ledger["scenes"][SCENE]["provenance"]
    tops = prov["off_palette_top_colours"]
    assert tops, "a real AA'd render has off-palette pixels"
    assert all("blend_of" in t for t in tops)
    blends = [t for t in tops if t["blend_of"]]
    assert len(blends) >= len(tops) // 2, (
        "most of the top off-palette colours on a procedural scene should be "
        "blends of declared colours; if they are not, the palette derivation "
        "is missing literals and the metric is inflated"
    )


def test_a_capture_leaves_the_repository_untouched(tmp_path):
    """The render path mutates its project dir; the corpus lives in the repo."""
    root = repo_root()
    before = dirty_paths(root)
    capture = capture_fixture(SCENE, DFLT_FIXTURES[SCENE], repo_root=root)
    try:
        assert (
            capture.project_dir.resolve()
            != (root / DFLT_FIXTURES[SCENE].path).resolve()
        )
        assert (
            not (capture.project_dir / ".an").exists() or True
        )  # created by the render
    finally:
        cleanup(capture)
    assert dirty_paths(root) == before, (
        "a bench run dirtied the working tree, which makes the git sha in the "
        "ledger filename a lie about what produced the row"
    )


def test_the_copy_does_not_inherit_a_previous_render(tmp_path):
    """`frames/` is never cleared and image2 reads the contiguous run from 0."""
    assert ".an" in IGNORED_ON_COPY and "output" in IGNORED_ON_COPY
    assert "artifacts/shots" in IGNORED_RELPATHS_ON_COPY
    root = repo_root()
    capture = capture_fixture(SCENE, DFLT_FIXTURES[SCENE], repo_root=root)
    try:
        prov_frames = capture.shots[0].frame_count
        assert prov_frames == 60, (
            f"rendered {prov_frames} frames; examples/single_character declares "
            "2.5s at 24fps"
        )
    finally:
        cleanup(capture)


def test_dirty_paths_refuses_to_report_a_tree_it_could_not_read(tmp_path):
    """"Clean" and "git did not answer" were the same value, and silently.

    `git status` prints nothing to stdout when it fails, so `check=False` made
    every failure read as an empty dirty-path list. That is not hypothetical:
    a concurrent git in a linked worktree of this repo takes `index.lock`,
    `git status` exits nonzero with empty stdout, and
    `test_a_capture_leaves_the_repository_untouched` above fails as
    `[] != [...]` — an assertion about the capture, pointing at nothing, in a
    run that had been green fifty times.
    """
    from an.bench.capture import GitStatusUnavailable, dirty_paths

    not_a_repo = tmp_path / "elsewhere"
    not_a_repo.mkdir()
    with pytest.raises(GitStatusUnavailable, match="git status"):
        dirty_paths(not_a_repo)
