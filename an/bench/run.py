"""`run_bench`: render the corpus, compute the panel, write one ledger row.

The order matters and is not arbitrary. The **decode calibration** runs before
any encode-side metric, because the single largest risk in this whole
instrument is that those metrics measure a colour-space conversion instead of
the encoder — measured at a mean of 5-14 code values on the unpinned decode
spelling, an order of magnitude larger than the crf18->23 signal every one of
them is trying to see. It produces plausible, monotone numbers, so it needs an
assertion, not a comment. The calibration is a hard equality against a
mathematically lossless encode of the same frames, and it is recorded in every
row so a future ffmpeg build change surfaces as a nonzero field rather than as
quietly shifted metrics.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from an.bench import contract, imageio, masks, metrics as M, palette as P
from an.bench.capture import SceneCapture, capture_fixture, cleanup, expected_frame_count
from an.bench.corpus import BENCH_RENDER_KWARGS, DFLT_FIXTURES, Fixture
from an.bench import environment
from an.bench.environment import environment_record
from an.bench.ledger import (
    Value,
    build_ledger,
    build_scene_block,
    gated,
    measured,
    unavailable,
    write_ledger,
)
from an.bench.paths import git_state, ledger_path, repo_root
from an.bench.registry import METRICS, TRIPWIRES

#: The lossless residual that proves the two decode legs agree. A hard zero,
#: not a tolerance: any nonzero value means the source and decoded colour
#: matrix or range disagree, and every encode-side number in the row is then
#: measuring that disagreement.
CALIBRATION_TOLERANCE: int = 0


class BenchError(RuntimeError):
    """The bench could not produce a row it would be honest to file."""


def _ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


def decode_calibration(frames_dir: Path, fps: int, *, height: int, width: int) -> dict:
    """Encode the frames losslessly, decode both legs, and assert they agree.

    Returns the residual, always; raises only when it is nonzero, because a
    recorded zero is the evidence that this run's numbers mean what they say.
    """
    import numpy as np

    tmp = frames_dir.parent / "_bench_lossless.mp4"
    try:
        imageio.run_raw(imageio.lossless_encode_command(frames_dir, fps, tmp))
        src = imageio.source_yuv(frames_dir, height=height, width=width)
        dec = imageio.decoded_yuv(tmp, height=height, width=width)
        n = min(len(src), len(dec))
        residual = np.abs(
            dec[:n, 0].astype(np.int16) - src[:n, 0].astype(np.int16)
        )
        report = {
            "luma_residual_mean": float(residual.mean()),
            "luma_residual_max": int(residual.max()),
            "source_command": imageio.source_yuv_command(frames_dir),
            "decoded_command": imageio.decoded_yuv_command(tmp),
            "note": (
                "a nonzero value means the source and decoded colour matrix or "
                "range disagree, and every encode-side metric in this row is "
                "measuring that disagreement rather than the encoder"
            ),
        }
    finally:
        tmp.unlink(missing_ok=True)
    if report["luma_residual_max"] > CALIBRATION_TOLERANCE:
        raise BenchError(
            "the decode calibration failed: a mathematically lossless encode "
            f"reads back with luma residual mean {report['luma_residual_mean']:.4f}, "
            f"max {report['luma_residual_max']}, against a required 0. The "
            "source leg must carry "
            f"`-vf {imageio.SOURCE_SCALE_FILTER}`; without it the encode-side "
            "metrics measure a full-range/limited-range and matrix mismatch and "
            "report it as encoder damage."
        )
    return report


def _shot_metrics(
    capture: SceneCapture,
    shot,
    *,
    with_ringing: bool,
) -> tuple[dict[str, Value], dict[str, Any]]:
    """The whole panel for one shot, plus the provenance the panel needs recorded."""
    import numpy as np

    h, w = capture.resolution[1], capture.resolution[0]
    values: dict[str, Value] = {}

    src_rgb = imageio.source_rgb(shot.frames_dir, height=h, width=w)
    packed = M.pack_rgb(src_rgb)

    pal = P.palette_for_scene(shot.scene_json, runtime_dir=shot.runtime_dir)
    palette_uint32 = pal["palette"]

    width_mean, width_median = M.edge_transition_width(src_rgb)
    values["edge_transition_width"] = measured(
        round(width_mean, 4), median=round(width_median, 4)
    )
    values["off_palette_pixel_fraction"] = measured(
        round(M.off_palette_pixel_fraction(packed, palette_uint32), 6)
    )
    values["frame_distinct_colours"] = measured(
        round(M.frame_distinct_colours(packed), 4)
    )

    prov: dict[str, Any] = {
        **{k: v for k, v in pal.items() if k != "palette"},
        "off_palette_top_colours": M.classify_off_palette(
            M.off_palette_top_colours(packed, palette_uint32), palette_uint32
        ),
        "off_palette_blend_note": (
            "`blend_of` names the two declared colours a pixel sits between. "
            "All-blends means the metric is reporting anti-aliasing correctly; "
            "a non-blend near the top means the palette derivation missed a "
            "literal and the number is inflated with no error anywhere."
        ),
        "source_pixels_sha256": contract.frames_sha256(src_rgb),
        "frames_on_disk": shot.frame_count,
        "decoded_source_frames": int(len(src_rgb)),
        "tolerances": {
            "edge_flat_tol": M.EDGE_FLAT_TOL,
            "flat_dev_tol": M.FLAT_DEV_TOL,
            "flicker_delta_tol": M.FLICKER_DELTA_TOL,
            "ssim_radius": M.SSIM_RADIUS,
            "luma_coefficients": list(M.LUMA_709),
        },
    }

    if not _ffmpeg_available():
        reason = "ffmpeg/ffprobe not on PATH; every encode-side metric needs both"
        for key, spec in METRICS.items():
            values.setdefault(key, unavailable(reason))
        return values, prov

    src_yuv = imageio.source_yuv(shot.frames_dir, height=h, width=w)
    dec_yuv = imageio.decoded_yuv(capture.mp4, height=h, width=w)
    dec_rgb = imageio.decoded_rgb(capture.mp4, height=h, width=w)

    n = min(len(src_yuv), len(dec_yuv), len(dec_rgb), len(src_rgb))
    if n != len(src_rgb) or n != len(dec_rgb):
        # Not silently truncated: every encode-side metric pairs frame i with
        # frame i, so a length disagreement means the pairing is offset and
        # every one of them would measure inter-frame motion instead.
        prov["frame_count_disagreement"] = {
            "source_rgb": int(len(src_rgb)),
            "source_yuv": int(len(src_yuv)),
            "decoded_rgb": int(len(dec_rgb)),
            "decoded_yuv": int(len(dec_yuv)),
        }

    edge = masks.edge_mask(src_yuv[:n, 0])
    flat = masks.flat_mask(src_rgb[:n])
    ring = masks.ring_mask(edge)
    prov["masks"] = {
        "edge": {
            "operator": masks.EDGE_OPERATOR,
            "threshold": masks.EDGE_MASK_THRESHOLD,
            "edge_px": int(edge.sum()),
            "frame_px": int(edge.size),
            "fraction": round(float(edge.mean()), 6),
        },
        "flat": {
            "operator": masks.FLAT_OPERATOR,
            "dilate_k": masks.FLAT_DILATE_K,
            "flat_px": int(flat.sum()),
            "fraction": round(float(flat.mean()), 6),
        },
        "held": {"operator": masks.HELD_OPERATOR, "pairs": max(0, n - 1)},
        "ring": {"operator": masks.RING_OPERATOR, "ring_px": int(ring.sum())},
    }

    def _num(fn, *args, **kwargs) -> Value:
        v = fn(*args, **kwargs)
        if isinstance(v, float) and v != v:
            return unavailable("the mask selected no pixels in this scene")
        return measured(round(float(v), 6))

    # `coded_luma_edge_error` IS the research's `chroma_edge_dY` control — mean
    # |dY| over the edge mask is both definitions verbatim — so it is computed
    # once and used as the ratio's denominator.
    d_y = M.masked_mean_abs(dec_yuv[:n, 0], src_yuv[:n, 0], edge)
    d_cr = M.masked_mean_abs(dec_yuv[:n, 2], src_yuv[:n, 2], edge)
    values["coded_luma_edge_error"] = _num(lambda: d_y)
    values["chroma_edge_dCr"] = _num(lambda: d_cr)
    values["chroma_edge_dCr_over_dY"] = (
        measured(round(float(d_cr / d_y), 6))
        if d_y and d_y == d_y and d_cr == d_cr
        else unavailable("the luma error on the edge mask is zero or undefined, so the ratio has no value")
    )

    frac, p99 = M.flat_field_deviation(src_rgb[:n], dec_rgb[:n], flat)
    values["flat_field_deviation"] = (
        unavailable("no flat field in this scene")
        if frac != frac
        else measured(round(frac, 6))
    )
    values["flat_field_p99_dev"] = (
        unavailable("no flat field in this scene")
        if p99 != p99
        else measured(round(float(p99), 4))
    )
    values["encode_flicker_on_held_pixels"] = _num(
        M.encode_flicker_on_held_pixels, src_rgb[:n], dec_rgb[:n]
    )
    values["ring_band_mae"] = _num(
        M.masked_mean_abs, dec_yuv[:n, 0], src_yuv[:n, 0], ring
    )

    if with_ringing:
        tmp = shot.frames_dir.parent / "_bench_qp0.mp4"
        try:
            imageio.run_raw(
                imageio.lossless_encode_command(shot.frames_dir, capture.fps, tmp)
            )
            lossless = imageio.decoded_yuv(tmp, height=h, width=w)
            m = min(n, len(lossless))
            values["encode_ringing_excess"] = _num(
                M.encode_ringing_excess,
                dec_yuv[:m, 0],
                lossless[:m, 0],
                src_yuv[:m, 0],
                ring[:m],
            )
        finally:
            tmp.unlink(missing_ok=True)
    else:
        values["encode_ringing_excess"] = unavailable(
            "skipped by --no-ringing (it costs one extra lossless encode per scene)"
        )

    values["video_stream_bytes"] = measured(imageio.video_stream_bytes(capture.mp4))
    values["file_bytes"] = measured(int(capture.mp4.stat().st_size))
    return values, prov


#: Why family B is null in every row this PR can write. an#38 supplies the
#: frames; the row ships fully shaped so filling it needs no schema change —
#: and a schema change would invalidate every row written before it.
GOLDEN_ABSENT_DETAIL: str = (
    "no golden frames are committed yet; an#38 builds the corpus. The row ships "
    "fully shaped, so filling it needs no schema change."
)


def _tripwire_values(capture: SceneCapture, shot) -> dict[str, Value]:
    """The golden block. Gated until an#38 supplies the frames."""
    return {
        key: gated("golden_absent", detail=GOLDEN_ABSENT_DETAIL) for key in TRIPWIRES
    }


def run_bench(
    *,
    scenes: dict[str, Fixture] | None = None,
    out: Path | None = None,
    with_ringing: bool = True,
    keep_render: Path | None = None,
    write: bool = True,
) -> dict:
    """Render the corpus, compute the panel, and (by default) write the row."""
    root = repo_root()
    fixtures = scenes if scenes is not None else DFLT_FIXTURES
    git = git_state(root)

    scene_blocks: dict[str, dict] = {}
    calibrations: dict[str, dict] = {}
    captures: list[SceneCapture] = []
    # Read while the throwaway tree still exists: the SEI is the field that
    # decides whether two rows may be compared at all, and the tree is deleted
    # before the run-level provenance is assembled.
    sei: str | None = None

    try:
        for name, fixture in fixtures.items():
            capture = capture_fixture(
                name, fixture, repo_root=root, keep_render=keep_render
            )
            captures.append(capture)
            if sei is None:
                sei = environment.x264_sei(capture.mp4)
            shot = capture.shots[0]
            h, w = capture.resolution[1], capture.resolution[0]

            if _ffmpeg_available():
                calibrations[name] = decode_calibration(
                    shot.frames_dir, capture.fps, height=h, width=w
                )

            values, shot_prov = _shot_metrics(capture, shot, with_ringing=with_ringing)
            # Family B is golden-referenced, so it is GATED rather than absent:
            # an absent row and a null row look the same to a reader and mean
            # opposite things.
            values["min_ssim_win8_vs_golden"] = gated(
                "golden_absent", detail=GOLDEN_ABSENT_DETAIL
            )
            expected = expected_frame_count(
                float((shot.scene_json.get("meta") or {}).get("duration", 0.0)),
                capture.fps,
            )
            provenance = {
                "source": capture.source,
                "prepared": capture.prepared,
                "scene_contract_sha256": contract.scene_contract_sha256(shot.scene_json),
                "n_drawable_entities": contract.count_drawable_entities(shot.scene_json),
                "n_declared_entity_refs": capture.n_declared_entity_refs,
                "n_nodes": contract.count_nodes(shot.scene_json),
                "n_frames": expected,
                "resolution": list(capture.resolution),
                "fps": capture.fps,
                "shot_count": len(capture.shots),
                "shots": [
                    {"id": s.shot_id, "frames": s.frame_count} for s in capture.shots
                ],
                "visual_kinds": sorted(capture.visual_kinds),
                "asset_resolution": capture.asset_resolution,
                "audio_cache": capture.audio_cache,
                "wall_seconds": capture.wall_seconds,
                **shot_prov,
            }
            if provenance["frames_on_disk"] != expected:
                raise BenchError(
                    f"{name}: rendered {provenance['frames_on_disk']} frames but the "
                    f"scene declares {expected}. ffmpeg's image2 demuxer reads the "
                    "contiguous run from 0, so a mismatch means one leg of every "
                    "encode-side metric is a different length from the other."
                )
            scene_blocks[name] = build_scene_block(
                provenance=provenance,
                metrics=values,
                tripwires=_tripwire_values(capture, shot),
            )
    finally:
        if keep_render is None:
            for capture in captures:
                cleanup(capture)

    ledger = build_ledger(
        provenance={
            "git": git,
            "render_kwargs": dict(BENCH_RENDER_KWARGS),
            "environment": environment_record(x264_sei=sei),
            "encode_command_source": (
                "an.adapters.cutout.render._ffmpeg_mux + DETERMINISTIC_X264_ARGS"
            ),
            "decode_commands": {
                "source_rgb": imageio.source_rgb_command(Path("<frames>")),
                "source_yuv444": imageio.source_yuv_command(Path("<frames>")),
                "decoded_rgb": imageio.decoded_rgb_command(Path("<mp4>")),
                "decoded_yuv444": imageio.decoded_yuv_command(Path("<mp4>")),
            },
            "decode_calibration": calibrations,
        },
        scenes=scene_blocks,
    )
    if write:
        path = Path(out) if out else ledger_path(root=root, git=git)
        write_ledger(ledger, path)
        ledger["_written_to"] = str(path)
    return ledger


def format_panel(ledger: dict) -> str:
    """A human-readable digest of a row — the thing `an bench` prints."""
    lines: list[str] = []
    for name, block in sorted((ledger.get("scenes") or {}).items()):
        prov = block["provenance"]
        lines.append(
            f"{name}  {prov['resolution'][0]}x{prov['resolution'][1]} @{prov['fps']}fps  "
            f"{prov['n_frames']} frames  visuals={','.join(prov['visual_kinds'])}"
        )
        lines.append(f"  contract {prov['scene_contract_sha256'][:16]}  "
                     f"edge {prov.get('masks', {}).get('edge', {}).get('fraction', '?')}  "
                     f"flat {prov.get('masks', {}).get('flat', {}).get('fraction', '?')}")
        for key, row in block["metrics"].items():
            state = row["state"]
            shown = row["value"] if state == "measured" else f"{state}({row.get('gate') or row.get('detail','')[:40]})"
            lines.append(f"    [{row['side'][:3]}/{row['family']}] {key:32s} {shown}")
        for key, row in block["tripwires"].items():
            lines.append(f"    [tripwire] {key:32s} {row['state']}({row.get('gate')})")
    if "_written_to" in ledger:
        lines.append(f"\nwrote {ledger['_written_to']}")
    return "\n".join(lines)
