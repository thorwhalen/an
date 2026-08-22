"""The golden gate (an#38): three absences, one criterion, and a bless that refuses.

Almost everything here runs in the **default** CI leg, against hand-written PNG
frames and a duck-typed capture. That is deliberate: the gate's logic — which
absence is which, what the reduction is, what a bless refuses — is where the
bugs live, and none of it needs a browser. The browser lane only has to prove
that the same logic sees a real render.

Each test names the one-line production mutation it exists to catch.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import numpy as np
import pytest

from an.bench import golden as G
from an.bench import png
from an.bench.paths import golden_dir, repo_root


# --------------------------------------------------------------------- fakes


def _fake_capture(
    tmp_path, *, name="scene", shots=(("only", 12),), fps=24, size=(8, 6)
):
    """A capture with real PNG frames on disk and no renderer anywhere near it."""
    width, height = size
    rng = np.random.default_rng(0)
    shot_objs = []
    for shot_id, count in shots:
        frames_dir = tmp_path / "work" / f"shot_{shot_id}" / "frames"
        frames_dir.mkdir(parents=True)
        for i in range(count):
            frame = rng.integers(0, 256, (height, width, 3), dtype=np.uint8)
            png.write_png(frames_dir / f"frame_{i:06d}.png", frame)
        shot_objs.append(
            SimpleNamespace(shot_id=shot_id, frames_dir=frames_dir, frame_count=count)
        )
    return SimpleNamespace(
        name=name,
        source=f"fixtures/{name}",
        shots=shot_objs,
        fps=fps,
        resolution=(width, height),
    )


def _bless(capture, times, root, *, reason="because", build="99.0.0.0"):
    return G.bless_scene(
        capture,
        times=times,
        chromium_build=build,
        reason=reason,
        git={"sha": "deadbee", "branch": "main", "dirty": False},
        scene_contract_sha256="0" * 64,
        root=root,
    )


# -------------------------------------------------------- frame identification


def test_a_pinned_time_resolves_to_the_frame_a_reader_would_expect():
    """MUTATION: in `frame_index_for`, `int(round(time * fps))` -> `int(time * fps)`.

    The trap is real and is exhibited here rather than asserted: `1.16 * 25` is
    `28.999999999999996`, so the obvious spelling silently blesses frame 28 and
    every future comparison is against a picture nobody chose. An author
    writing a decimal approximation of a frame time (`0.0416666` for 1/24) trips
    it on the very first frame.
    """
    assert int(1.16 * 25) == 28 and round(1.16 * 25) == 29, (
        "the float trap this guard exists for must still exist on this platform"
    )
    assert G.frame_index_for(1.16, fps=25, n_frames=60) == 29
    assert G.frame_index_for(0.0416666, fps=24, n_frames=12) == 1
    assert G.frame_index_for(0.25, fps=24, n_frames=12) == 6
    assert G.frame_index_for(0.0, fps=24, n_frames=12) == 0
    assert G.frame_index_for(0.1667, fps=24, n_frames=12) == 4


def test_a_pinned_time_past_the_end_of_the_scene_is_refused():
    """MUTATION: drop the range check. A clamped index blesses the wrong frame."""
    with pytest.raises(G.GoldenError, match="names no picture"):
        G.frame_index_for(9.0, fps=24, n_frames=12)


def test_frame_keys_sort_the_way_a_directory_listing_does():
    """MUTATION: `f"f{index:04d}"` -> `f"f{index}"`.

    Unpadded, `f10` sorts before `f2`, and the committed frames stop reading in
    order in the PR diff — which is where a human actually looks at them.
    """
    assert G.frame_key(7) == "f0007"
    assert sorted([G.frame_key(10), G.frame_key(2)]) == ["f0002", "f0010"]


def test_a_pinned_time_resolves_into_the_right_shot_of_a_multi_shot_scene(tmp_path):
    """MUTATION: in `resolve_frames`, `if remaining < count` -> `if remaining <= count`.

    Indices run over the CONCATENATED timeline, which is what the delivered mp4
    shows. `multi_shot`'s second golden lands on the first frame of the second
    shot precisely so a shot rendered in the wrong order cannot pass.
    """
    capture = _fake_capture(tmp_path, shots=(("intro", 6), ("beat", 6)))
    refs = G.resolve_frames(capture, [0.0, 0.25])
    assert [(r.index, r.shot_id, r.local_index) for r in refs] == [
        (0, "intro", 0),
        (6, "beat", 0),
    ]


# ----------------------------------------------------------- the three gates


def test_the_three_absences_are_three_different_gates():
    """MUTATION: set any two of the three gate constants to the same string.

    Pinned by LITERAL rather than by reading the constants back, because a test
    that reads the same constant the production code reads pins nothing — the
    an#36 sweep lost two mutants to exactly that shape.
    """
    assert G.GATE_UNDECLARED == "golden_frames_undeclared"
    assert G.GATE_ABSENT == "golden_absent_for_chromium_build"
    assert G.GATE_BUILD_UNKNOWN == "chromium_build_unknown"
    assert G.GATE_JUST_BLESSED == "blessed_this_run"
    gates = [
        G.GATE_UNDECLARED,
        G.GATE_ABSENT,
        G.GATE_BUILD_UNKNOWN,
        G.GATE_JUST_BLESSED,
    ]
    assert len(set(gates)) == len(gates), "each absence must be distinguishable"


def test_a_scene_that_declares_no_golden_frames_is_gated_as_undeclared(tmp_path):
    """MUTATION: in `compare_scene`, `if not times:` -> `if False:`."""
    capture = _fake_capture(tmp_path)
    result = G.compare_scene(
        capture, times=[], chromium_build="99.0.0.0", root=tmp_path
    )
    assert result["state"] == "gated"
    assert result["gate"] == "golden_frames_undeclared"


def test_a_missing_golden_for_this_build_is_gated_as_absent_not_as_a_pass(tmp_path):
    """MUTATION: in `compare_scene`, return `measured` when a file is missing.

    An absent golden is not a passing comparison and is not a failing one; it is
    a comparison that did not happen, and the row has to say so.
    """
    capture = _fake_capture(tmp_path)
    result = G.compare_scene(
        capture, times=[0.0, 0.25], chromium_build="99.0.0.0", root=tmp_path
    )
    assert result["state"] == "gated"
    assert result["gate"] == "golden_absent_for_chromium_build"
    assert "re-bless" in result["detail"]


def test_a_golden_that_cannot_be_decoded_is_unavailable_not_gated(tmp_path):
    """MUTATION: in `compare_scene`, fold the `unreadable` branch into `absent`.

    A check that crashed is not evidence anything is fine, and it is not the
    same fact as a check nobody has set up yet. Conflating them is how a
    corrupted golden reads as "waiting to be blessed" forever.
    """
    capture = _fake_capture(tmp_path)
    _bless(capture, [0.0, 0.25], tmp_path)
    victim = next(G.iter_committed("scene", "99.0.0.0", root=tmp_path))
    victim.write_bytes(b"this is not a png")
    result = G.compare_scene(
        capture, times=[0.0, 0.25], chromium_build="99.0.0.0", root=tmp_path
    )
    assert result["state"] == "unavailable"
    assert "could not be decoded" in result["detail"]


def test_an_unknown_chromium_build_is_its_own_fact():
    """MUTATION: in `chromium_build_of`, return a default string instead of None.

    `probe_browser` never raises — it returns `{"error": ...}` — so without this
    distinction an un-probeable browser reads exactly like a scene nobody has
    blessed yet, and the run reports the wrong problem.
    """
    assert G.chromium_build_of({"render_side": {"error": "boom"}}) is None
    assert G.chromium_build_of({}) is None
    assert G.chromium_build_of({"render_side": {"chromium_build": "1.2.3"}}) == "1.2.3"


# ------------------------------------------------------------- the criterion


def test_the_gate_compares_decoded_pixels_not_file_bytes(tmp_path):
    """MUTATION: in `compare_scene`, compare `golden.read_bytes()` to the frame's.

    Chromium 1187 -> 1223 changed 144 of 144 PNG files and ZERO pixels. Here the
    same fact is produced deliberately: the committed golden is re-encoded at a
    different zlib level, so the files differ and the pictures do not.
    """
    capture = _fake_capture(tmp_path)
    _bless(capture, [0.0, 0.25], tmp_path)
    committed = sorted(G.iter_committed("scene", "99.0.0.0", root=tmp_path))
    for path in committed:
        pixels = png.read_png(path)
        before = path.read_bytes()
        path.write_bytes(png.encode_png(pixels, level=1))
        assert path.read_bytes() != before, "the re-encode must change the file bytes"
    result = G.compare_scene(
        capture, times=[0.0, 0.25], chromium_build="99.0.0.0", root=tmp_path
    )
    assert result["state"] == "measured"
    assert result["identical"] is True


def test_a_changed_pixel_fails_the_gate_and_is_localised(tmp_path):
    """MUTATION: in `golden_comparison`, `(diff > 0).sum() == 0` -> `<= diff.size`."""
    capture = _fake_capture(tmp_path)
    _bless(capture, [0.0, 0.25], tmp_path)
    frame = capture.shots[0].frames_dir / "frame_000006.png"
    pixels = png.read_png(frame)
    pixels[0, 0] = (pixels[0, 0].astype(int) + 40) % 256
    png.write_png(frame, pixels)
    result = G.compare_scene(
        capture, times=[0.0, 0.25], chromium_build="99.0.0.0", root=tmp_path
    )
    assert result["state"] == "measured"
    assert result["identical"] is False
    assert result["changed_px"] == 1


def test_the_reduction_is_worst_frame_wins(tmp_path):
    """MUTATION: in `compare_scene`, `min(...)` -> `max(...)` for min_ssim_win8.

    A maximum (or a mean) lets one clean frame hide a broken one, and this
    metric's own name is "the worst small window in the frame".
    """
    capture = _fake_capture(tmp_path, size=(24, 24))
    _bless(capture, [0.0, 0.25], tmp_path)
    frame = capture.shots[0].frames_dir / "frame_000006.png"
    pixels = png.read_png(frame)
    pixels[4:12, 4:12] = 0
    png.write_png(frame, pixels)
    result = G.compare_scene(
        capture, times=[0.0, 0.25], chromium_build="99.0.0.0", root=tmp_path
    )
    per_frame = [f["min_ssim_win8"] for f in result["frames"]]
    assert result["min_ssim_win8"] == min(per_frame)
    assert result["min_ssim_win8"] < max(per_frame), (
        "the two frames must actually differ, or this asserts nothing"
    )


# ------------------------------------------------------------------- blessing


def test_bless_refuses_a_blank_reason(tmp_path):
    """MUTATION: in `bless_scene`, `if not reason.strip():` -> `if reason is None:`.

    A re-bless with no recorded reason is the same failure as a silently
    widened threshold — the named failure this whole wave exists to prevent —
    and whitespace must not sneak past it.
    """
    capture = _fake_capture(tmp_path)
    for blank in ("", "   ", "\n\t"):
        with pytest.raises(G.GoldenError, match="needs a reason"):
            _bless(capture, [0.0, 0.25], tmp_path, reason=blank)
    assert not list(G.iter_committed("scene", "99.0.0.0", root=tmp_path))


def test_bless_refuses_a_pixel_identical_pair(tmp_path):
    """MUTATION: delete the pairwise `np.array_equal` loop in `bless_scene`.

    Not hypothetical: on `promote_demo`, frame 0 and the `duration/2` frame
    differ by exactly ZERO pixels, so the obvious choice of second time blesses
    one picture twice and the second golden tests nothing forever after.
    """
    capture = _fake_capture(tmp_path)
    frames_dir = capture.shots[0].frames_dir
    twin = png.read_png(frames_dir / "frame_000000.png")
    png.write_png(frames_dir / "frame_000006.png", twin)
    with pytest.raises(G.GoldenError, match="pixel-identical"):
        _bless(capture, [0.0, 0.25], tmp_path)


def test_bless_refuses_a_single_frame(tmp_path):
    """MUTATION: `REQUIRED_GOLDEN_FRAMES = 1`."""
    capture = _fake_capture(tmp_path)
    with pytest.raises(G.GoldenError, match="are required"):
        _bless(capture, [0.0], tmp_path)


def test_bless_refuses_an_unknown_chromium_build(tmp_path):
    """MUTATION: in `bless_scene`, drop the `chromium_build is None` check.

    The path keys on the build, so a `None` would write the frames under a name
    no future run could ever look up.
    """
    capture = _fake_capture(tmp_path)
    with pytest.raises(G.GoldenError, match="Chromium build could not be determined"):
        _bless(capture, [0.0, 0.25], tmp_path, build=None)


def test_bless_removes_a_golden_it_no_longer_blesses(tmp_path):
    """MUTATION: delete the `removed` sweep in `bless_scene`.

    Moving a pinned time writes a NEW filename; without the sweep the old frame
    stays committed forever, indistinguishable from one that is still a gate.
    This is not hypothetical either — `graded_field`'s second golden moved from
    f0006 to f0004 during an#38 for exactly this reason.
    """
    capture = _fake_capture(tmp_path)
    _bless(capture, [0.0, 0.25], tmp_path)
    assert {
        p.name.split("-")[0]
        for p in G.iter_committed("scene", "99.0.0.0", root=tmp_path)
    } == {
        "f0000",
        "f0006",
    }
    record = _bless(capture, [0.0, 0.1667], tmp_path, reason="moved the second time")
    assert {
        p.name.split("-")[0]
        for p in G.iter_committed("scene", "99.0.0.0", root=tmp_path)
    } == {
        "f0000",
        "f0004",
    }
    assert record["removed"] == ["f0006-chromium99.0.0.0.png"]


def test_the_bless_record_says_what_was_blessed_and_why(tmp_path):
    """MUTATION: drop `reason` (or `sha256`) from the record `bless_scene` writes."""
    capture = _fake_capture(tmp_path)
    record = _bless(capture, [0.0, 0.25], tmp_path, reason="a considered reason")
    stored = json.loads(
        G.manifest_path("scene", "99.0.0.0", root=tmp_path).read_text(encoding="utf-8")
    )
    assert stored == record
    assert stored["reason"] == "a considered reason"
    assert stored["chromium_build"] == "99.0.0.0"
    assert [f["frame_key"] for f in stored["frames"]] == ["f0000", "f0006"]
    for entry in stored["frames"]:
        assert len(entry["sha256"]) == 64
        assert entry["file"].endswith("-chromium99.0.0.0.png")


def test_the_recorded_sha256_is_of_the_pixels_and_matches_a_reread(tmp_path):
    """MUTATION: in `bless_scene`, record `sha256(out.read_bytes())` instead.

    The recorded digest is what a later integrity check compares against, so if
    it were of the file bytes the check would go red on any lossless re-encode
    and green on nothing useful.
    """
    capture = _fake_capture(tmp_path)
    record = _bless(capture, [0.0, 0.25], tmp_path)
    for entry in record["frames"]:
        path = golden_dir(tmp_path) / "scene" / entry["file"]
        assert G.pixels_sha256(png.read_png(path)) == entry["sha256"]
        assert G.pixels_sha256(png.read_png(path)) != path.read_bytes().hex()[:64]


# ------------------------------------- the committed corpus, checked for free


def _committed_records():
    root = repo_root()
    for manifest in sorted(golden_dir(root).glob("*/bless-chromium*.json")):
        yield manifest, json.loads(manifest.read_text(encoding="utf-8"))


def test_every_committed_golden_decodes_and_matches_its_bless_record():
    """The whole corpus, verified without rendering anything.

    Catches a golden that was truncated, re-encoded by a well-meaning tool, or
    mangled by a text-mode checkout — none of which the browser lane would see,
    because the browser lane only runs when someone asks for it.

    MUTATION: flip one byte of any committed PNG's pixel data.
    """
    records = list(_committed_records())
    assert records, "the corpus has bless records, or this test is asserting nothing"
    for manifest, record in records:
        for entry in record["frames"]:
            path = manifest.parent / entry["file"]
            assert path.is_file(), f"{manifest.parent.name}: {entry['file']} is missing"
            assert G.pixels_sha256(png.read_png(path)) == entry["sha256"], (
                f"{path} does not decode to the pixels its bless record names"
            )


def test_no_committed_golden_is_orphaned_by_its_bless_record():
    """A PNG nothing reads is indistinguishable from one that is still a gate.

    MUTATION: delete the `removed` sweep in `bless_scene`, then move a pinned
    time and re-bless.
    """
    for manifest, record in _committed_records():
        build = record["chromium_build"]
        named = {entry["file"] for entry in record["frames"]}
        on_disk = {p.name for p in manifest.parent.glob(f"*-chromium{build}.png")}
        assert on_disk == named, (
            f"{manifest.parent.name}: {sorted(on_disk - named)} are committed but "
            f"named by no bless record; {sorted(named - on_disk)} are named but absent"
        )


def test_every_bless_record_carries_a_reason_and_a_criterion():
    """MUTATION: bless with `reason=" "` after weakening the blank-reason guard."""
    for manifest, record in _committed_records():
        assert record["reason"].strip(), f"{manifest} records no reason"
        assert "DECODED" in record["criterion"]
        assert record["git"]["sha"], f"{manifest} records no commit"


# ------------------------------------------------------- the real render lane


@pytest.mark.browser
@pytest.mark.ffmpeg
def test_the_golden_gate_is_green_on_a_real_render_of_the_committed_corpus():
    """The committed goldens still describe what `an` renders today.

    One scene rather than six, because this lane is opt-in and the point here is
    that the gate WIRES UP — the pixel-level agreement of all six is what
    `test_every_committed_golden_decodes_and_matches_its_bless_record` covers,
    for free, in the default leg.

    MUTATION: in `run_bench`, hard-code family B back to
    `gated("golden_absent")` as it was before an#38.
    """
    from an.bench.corpus import DFLT_FIXTURES
    from an.bench.run import run_bench

    ledger = run_bench(scenes={"aa_probe": DFLT_FIXTURES["aa_probe"]}, write=False)
    block = ledger["scenes"]["aa_probe"]
    tripwire = block["tripwires"]["golden_identity"]
    metric = block["metrics"]["min_ssim_win8_vs_golden"]
    assert tripwire["state"] == "measured", (
        f"the gate did not run: {tripwire.get('gate')} — {tripwire.get('detail')}"
    )
    assert tripwire["value"] is True, (
        f"today's render differs from the committed golden by "
        f"{tripwire.get('changed_px')} px (max delta {tripwire.get('max_delta')}). "
        "Look at the PNG diff before re-blessing."
    )
    assert metric["value"] == pytest.approx(1.0)


@pytest.mark.browser
@pytest.mark.ffmpeg
def test_the_golden_gate_goes_red_when_the_rasteriser_changes():
    """A gate that cannot go red is not a gate.

    Drives the same lever an#41 uses for its render mutation — PixiJS
    `antialias: false` in `runtime.js` — through the renderer's own
    `runtime_dir` seam, so no production knob exists for it and nothing about
    the shipped runtime changes.

    Measured while building an#38: this fires on all six corpus scenes, with
    `changed_px` from 52 (`saturated_outline`) to 434 (`aa_probe`) and
    `min_ssim_win8` down to -0.49 on `single_character`.

    MUTATION: in `compare_scene`, return `identical: True` unconditionally.
    """
    import shutil
    import tempfile
    from pathlib import Path

    import an.adapters.cutout.render as render_module
    from an.adapters.cutout.runtime_files import runtime_dir
    from an.bench.corpus import DFLT_FIXTURES
    from an.bench.run import run_bench

    staged = Path(tempfile.mkdtemp()) / "runtime"
    shutil.copytree(runtime_dir(), staged)
    source = (staged / "runtime.js").read_text(encoding="utf-8")
    assert source.count("antialias: true") == 1, (
        "the AA lever moved in runtime.js; an#41's render mutation points here"
    )
    (staged / "runtime.js").write_text(
        source.replace("antialias: true", "antialias: false"), encoding="utf-8"
    )

    real = render_module.runtime_dir
    render_module.runtime_dir = lambda: staged
    try:
        ledger = run_bench(scenes={"aa_probe": DFLT_FIXTURES["aa_probe"]}, write=False)
    finally:
        render_module.runtime_dir = real
        shutil.rmtree(staged.parent, ignore_errors=True)

    block = ledger["scenes"]["aa_probe"]
    tripwire = block["tripwires"]["golden_identity"]
    assert tripwire["state"] == "measured"
    assert tripwire["value"] is False, "the gate did not notice a changed rasteriser"
    assert tripwire["changed_px"] > 0
    assert block["metrics"]["min_ssim_win8_vs_golden"]["value"] < 1.0


def test_a_run_that_blessed_does_not_also_report_a_pass():
    """Comparing against a golden this run wrote is a tautology.

    MUTATION: in `run_bench`, replace the `GATE_JUST_BLESSED` branch with a
    `compare_scene` call after blessing.

    The row would otherwise carry a perfect family-B score that no code could
    ever have failed — the exact shape of a number that looks like evidence and
    is not. Asserted against `_golden_values` directly rather than by rendering:
    a render here would have to bless SOMEWHERE, and the first version of this
    test blessed into the committed corpus and overwrote a real bless record's
    reason with its own.
    """
    from an.bench.run import JUST_BLESSED_DETAIL, _golden_values

    metric, tripwire = _golden_values(
        {"state": "gated", "gate": G.GATE_JUST_BLESSED, "detail": JUST_BLESSED_DETAIL}
    )
    for value in (metric, tripwire):
        assert value.state == "gated"
        assert value.gate == "blessed_this_run"
        assert value.value is None
    assert "tautology" in JUST_BLESSED_DETAIL


def test_a_measured_golden_result_fills_both_blocks_from_one_comparison():
    """MUTATION: in `_golden_values`, return the SSIM number as the tripwire too.

    A tripwire counts zero toward any criterion and a metric may count; a
    boolean wearing a measurement's clothes is a criterion nobody can evaluate.
    """
    from an.bench.run import _golden_values

    metric, tripwire = _golden_values(
        {
            "state": "measured",
            "identical": False,
            "min_ssim_win8": 0.5,
            "changed_px": 7,
            "max_delta": 40,
            "frames": [],
        }
    )
    assert metric.value == 0.5 and isinstance(metric.value, float)
    assert tripwire.value is False and isinstance(tripwire.value, bool)
    assert metric.extra["changed_px"] == 7 and tripwire.extra["max_delta"] == 40


def test_the_pixel_digest_distinguishes_a_transposed_frame():
    """MUTATION: in `pixels_sha256`, drop the shape/dtype prefix.

    `ndarray.tobytes()` carries no shape, so a 320x240 frame and a 240x320 one
    holding the same bytes hash identically — and issue #38's literal "the
    criterion is sha256(decoded RGB array)" would then report PASS on a
    transposed frame. Both orientations are live in this corpus.
    """
    flat = np.arange(24, dtype=np.uint8)
    assert G.pixels_sha256(flat.reshape(2, 4, 3)) != G.pixels_sha256(
        flat.reshape(4, 2, 3)
    )
    assert G.pixels_sha256(flat.reshape(2, 4, 3)) == G.pixels_sha256(
        flat.reshape(2, 4, 3)
    )


def test_a_golden_blessed_at_another_resolution_does_not_crash_the_row(tmp_path):
    """MUTATION: in `_golden_values`, drop the `shape_mismatch` branch.

    `golden_comparison` returns `min_ssim_win8: None` for a shape mismatch, and
    a `measured` ledger value refuses a null — so without the branch the whole
    run dies with `TypeError: float() argument ... not 'NoneType'` the first
    time somebody changes a scene's resolution.

    The identity IS knowable (a differently-shaped picture is not the same
    picture) and the number is not, so the boolean is reported and the metric is
    `unavailable` — never `0.0`, which would be a measurement nobody took.
    """
    from an.bench.run import _golden_values

    capture = _fake_capture(tmp_path, size=(8, 6))
    _bless(capture, [0.0, 0.25], tmp_path)
    # Re-bless-free way to change the shape: rewrite the committed goldens at a
    # different size, exactly as a resolution change in the scene would.
    for path in G.iter_committed("scene", "99.0.0.0", root=tmp_path):
        png.write_png(path, np.zeros((5, 9, 3), np.uint8))

    result = G.compare_scene(
        capture, times=[0.0, 0.25], chromium_build="99.0.0.0", root=tmp_path
    )
    assert result["state"] == "measured"
    assert result["min_ssim_win8"] is None
    assert result["shape_mismatch"]

    metric, tripwire = _golden_values(result)
    assert metric.state == "unavailable" and metric.value is None
    assert tripwire.state == "measured" and tripwire.value is False


def test_a_retired_gate_name_names_its_live_replacement():
    """MUTATION: delete the `golden_absent` entry from `RETIRED_GATES`.

    `an bench --compare` reads rows written before this module existed as fact,
    and the committed an#36 row carries `gated("golden_absent")` on both
    family-B keys. A spelling that simply disappears leaves that reader nowhere
    to look up what it meant; a spelling silently REUSED for a different fact is
    worse. So a retired name stays retired and points at what replaced it.
    """
    live = {G.GATE_UNDECLARED, G.GATE_ABSENT, G.GATE_BUILD_UNKNOWN, G.GATE_JUST_BLESSED}
    assert len(live) == 4, "two gate constants collapsed onto one spelling"

    row = json.loads(
        (repo_root() / "misc/bench/ledger/2026-08-21-07e4e61.json").read_text(
            encoding="utf-8"
        )
    )
    gates = {
        value.get("gate")
        for block in row["scenes"].values()
        for section in ("metrics", "tripwires")
        for value in block[section].values()
        if value.get("gate")
    }
    assert "golden_absent" in gates, (
        "this test asserts nothing unless the committed row really does carry a "
        "retired gate name"
    )
    unknown = gates - live - set(G.RETIRED_GATES)
    assert not unknown, (
        f"the committed row carries gate name(s) {sorted(unknown)} that are "
        "neither live nor recorded as retired"
    )
    for old, new in G.RETIRED_GATES.items():
        assert old not in live, f"{old} was retired; do not resurrect the spelling"
        assert new in live, f"{old}'s replacement {new} is not a live gate"


def test_the_panel_distinguishes_a_fired_golden_from_a_passing_one():
    """MUTATION: restore `f"{row['state']}({row.get('gate')})"` for the tripwire line.

    Second mutation: drop the `_golden_failure_lines(...)` call from
    `format_panel`.

    A measured tripwire carries no gate, so that spelling printed
    `measured(None)` — the same eight characters whether the gate held or fired.
    The panel is what a human reads after `an bench`; if a fired gate is
    indistinguishable there, the gate is only as good as whoever remembers to
    grep the JSON.
    """
    from an.bench.ledger import build_scene_block, measured
    from an.bench.registry import METRICS
    from an.bench.run import format_panel

    provenance = {
        "scene_contract_sha256": "0" * 64,
        "resolution": [320, 240],
        "fps": 24,
        "n_frames": 12,
        "visual_kinds": ["rect"],
        "golden": {
            "what_moves": "the marker sweeping across the field",
            "frames": [
                {
                    "state": "compared",
                    "frame_key": "f0004",
                    "time": 0.1667,
                    "identical": False,
                    "changed_px": 431,
                    "min_ssim_win8": 0.12,
                    "golden": "misc/bench/golden/scene/f0004-chromium99.0.0.0.png",
                }
            ],
        },
    }
    metrics = {key: measured(1.0) for key in METRICS}

    def panel(value):
        block = build_scene_block(
            provenance=provenance,
            metrics=metrics,
            tripwires={
                "golden_identity": measured(value, changed_px=431, max_delta=99)
            },
        )
        return format_panel({"scenes": {"aa_probe": block}})

    fired, held = panel(False), panel(True)
    assert "FIRED" in fired and "FIRED" not in held
    assert "GOLDEN MISMATCH in aa_probe" in fired
    assert "GOLDEN MISMATCH" not in held, (
        "a passing gate must print nothing extra, or the loud block is noise"
    )
    for expected in ("431 px changed", "f0004", "the marker sweeping", "--bless"):
        assert expected in fired, f"the failure block does not mention {expected!r}"
    assert "measured(None)" not in fired and "measured(None)" not in held


# ------------------------------ an#38/#40/#41 adversarial-review hardening


def test_a_shape_mismatch_reports_no_pixel_count_rather_than_zero(tmp_path):
    """MUTATION: `int(f["changed_px"] or 0)` in `compare_scene`'s reduction.

    A shape mismatch has no per-pixel comparison to count, so `changed_px` is
    `None`. Coercing that to 0 made a FIRED gate print "GOLDEN MISMATCH: 0 px
    changed, max delta 0" — a fabricated number, in the one schema whose whole
    premise is that unknown is not zero, in the one message that tells a reader
    how bad the failure is.
    """
    from an.bench.run import _golden_failure_lines, _golden_values

    capture = _fake_capture(tmp_path)
    _bless(capture, [0.0, 0.25], tmp_path)
    for path in G.iter_committed("scene", "99.0.0.0", root=tmp_path):
        png.write_png(path, np.zeros((5, 9, 3), np.uint8))
    result = G.compare_scene(
        capture, times=[0.0, 0.25], chromium_build="99.0.0.0", root=tmp_path
    )
    assert result["changed_px"] is None
    assert result["max_delta"] is None
    _, tripwire = _golden_values(result)
    block = {
        "tripwires": {
            "golden_identity": {"state": "measured", "value": False, **tripwire.extra}
        },
        "provenance": {"golden": result},
    }
    headline = _golden_failure_lines("scene", block)[0]
    assert "0 px changed" not in headline
    assert "DIFFERENT SHAPES" in headline


def test_the_reported_golden_path_is_one_a_reader_can_open(tmp_path):
    """MUTATION: `golden_dir(root).parent.parent` instead of `.parents[2]`.

    `golden_dir` is `<root>/misc/bench/golden`, so two levels up is
    `<root>/misc` and every path in the report came out as `bench/golden/...` —
    a path that does not exist, printed in the message that tells a reader which
    file to look at. Measured against the committed row: 12 of 12 wrong.
    """
    capture = _fake_capture(tmp_path)
    _bless(capture, [0.0, 0.25], tmp_path)
    result = G.compare_scene(
        capture, times=[0.0, 0.25], chromium_build="99.0.0.0", root=tmp_path
    )
    for frame in result["frames"]:
        assert frame["golden"], frame
        assert (tmp_path / frame["golden"]).is_file(), (
            f"the report names {frame['golden']!r}, which does not exist"
        )


@pytest.mark.parametrize("corruption", ["truncated", "no_iend", "garbage_idat"])
def test_a_corrupt_golden_is_unavailable_rather_than_fatal(tmp_path, corruption):
    """MUTATION: let `struct.error` / `zlib.error` out of `an.bench.png`.

    Neither is a `PngFormatError` nor a subclass of it, so `compare_scene`'s
    handler could not catch them — and `run_bench` has no per-scene `except`, so
    one golden truncated in transit aborted the whole run and took five other
    scenes' rows with it.
    """
    capture = _fake_capture(tmp_path)
    _bless(capture, [0.0, 0.25], tmp_path)
    victim = next(G.iter_committed("scene", "99.0.0.0", root=tmp_path))
    good = victim.read_bytes()
    victim.write_bytes(
        {
            "truncated": good[:-2],
            "no_iend": good[: len(good) - 12],
            "garbage_idat": good[:33] + b"\x00" * 20 + good[53:],
        }[corruption]
    )
    result = G.compare_scene(
        capture, times=[0.0, 0.25], chromium_build="99.0.0.0", root=tmp_path
    )
    assert result["state"] == "unavailable"
    assert result["detail"]


def test_a_pinned_time_past_the_end_does_not_abort_the_run(tmp_path):
    """MUTATION: call `resolve_frames` outside the `try` in `compare_scene`.

    A time past the end of the scene is a fact about ONE scene. `run_bench` has
    no per-scene handler, so the `GoldenError` escaped and destroyed every other
    scene's row in the same run. `unavailable` is the documented outcome for
    "the check could not run"; inventing a fifth gate name would be a
    wire-format change `--compare` and `RETIRED_GATES` both have to learn.
    """
    capture = _fake_capture(tmp_path, shots=(("only", 4),))
    result = G.compare_scene(
        capture, times=[0.0, 0.25], chromium_build="99.0.0.0", root=tmp_path
    )
    assert result["state"] == "unavailable"
    assert "resolves to frame 6" in result["detail"]


def test_two_pinned_times_that_land_on_one_frame_are_refused(tmp_path):
    """MUTATION: drop the duplicate-key check in `compare_scene`.

    Two goldens of the same picture compare the same thing twice, and the second
    tests nothing — the same failure `bless_scene` already refuses at write time,
    reachable at compare time through an fps change.
    """
    capture = _fake_capture(tmp_path)
    result = G.compare_scene(
        capture, times=[0.0, 0.0], chromium_build="99.0.0.0", root=tmp_path
    )
    assert result["state"] == "unavailable"
    assert "same frame" in result["detail"]
