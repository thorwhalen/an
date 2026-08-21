"""`run_bench`: render the corpus, compute the panel, write one ledger row.

**Every encode-side metric is measured against a lossless (`-qp 0`) encode of
the same frames, not against a second conversion of the PNGs.** That is a
correction, and CI is what made it: the PNG conversion agrees with the
encoder's own exactly on ffmpeg 8.1 and disagrees by mean 0.63 / max 5 on the
Linux runner's older build — 42% of `coded_luma_edge_error`'s whole crf23 value,
which would have been measured as encoder damage on that machine and as nothing
on this one. `-qp 0` is lossless, so its decoded luma **is** the plane libx264
received, on any build. Referencing to it removes the assumption rather than
widening it. See :mod:`an.bench.imageio`.

The PNG conversion is still performed and its distance from the encoder's input
is recorded as `png_to_encoder_input_luma` — that number is the build
dependence, and it belongs in provenance rather than inside a gate.
"""

from __future__ import annotations

import shutil
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from an.bench import contract, golden as G, imageio, masks, metrics as M, palette as P
from an.bench import png
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
from an.bench.registry import METRICS

class BenchError(RuntimeError):
    """The bench could not produce a row it would be honest to file."""


def _ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


def lossless_reference(frames_dir: Path, fps: int, out: Path):
    """Encode the frames losslessly and return the decode — the encoder's input.

    Returned as a path rather than an array so the caller controls its
    lifetime; every encode-side reference comes from here.
    """
    imageio.run_raw(imageio.lossless_encode_command(frames_dir, fps, out))
    return out


def conversion_distance(
    frames_dir: Path, lossless_mp4: Path, *, height: int, width: int
) -> dict:
    """How far this build's PNG->YUV conversion sits from the encoder's own.

    Recorded, never gated. It was a gate — a hard equality — and it failed on
    the Linux runner while passing here, which is precisely the shape of a
    machine-dependent fact masquerading as a universal one. Now the metrics no
    longer depend on it, and the number is kept because it is the thing that
    will explain a future cross-build surprise.
    """
    import numpy as np

    src = imageio.source_yuv(frames_dir, height=height, width=width)
    enc_in = imageio.decoded_yuv(lossless_mp4, height=height, width=width)
    n = min(len(src), len(enc_in))
    if n == 0:
        return {"error": "one of the legs decoded to zero frames"}
    residual = np.abs(enc_in[:n, 0].astype(np.int16) - src[:n, 0].astype(np.int16))
    return {
        "luma_residual_mean": round(float(residual.mean()), 6),
        "luma_residual_max": int(residual.max()),
        "png_command": imageio.source_yuv_command(frames_dir),
        "encoder_input_command": imageio.decoded_yuv_command(lossless_mp4),
        "note": (
            "Zero means this build's explicit PNG->YUV conversion reproduces "
            "what libx264 received. Nonzero means it does not, which is a "
            "property of the ffmpeg build and NOT an error: the encode-side "
            "metrics reference the lossless decode, so they do not depend on "
            "this agreeing. Measured 0.0 on ffmpeg 8.1 (arm64 macOS) and "
            "0.629 / max 5 on the Linux runner's older build."
        ),
    }


def _png_source_rgb(frames_dir: Path, *, height: int, width: int):
    """Decode a shot's PNG sequence with the package's own reader, no ffmpeg.

    Byte-for-byte the same array ``imageio.source_rgb`` returns — asserted
    against it on every rendered frame in the repo — but reachable on a machine
    with no ffmpeg, which is what makes family A honestly render-side.
    """
    import numpy as np

    from an.adapters.cutout.render import DEFAULT_FRAME_PNG_PATTERN

    frames = sorted(Path(frames_dir).glob("frame_*.png"))
    if not frames:
        raise BenchError(f"no {DEFAULT_FRAME_PNG_PATTERN} frames under {frames_dir}")
    stacked = np.stack([png.read_png(p) for p in frames])
    if stacked.shape[1:3] != (height, width):
        raise BenchError(
            f"{frames_dir} holds {stacked.shape[2]}x{stacked.shape[1]} frames but "
            f"the scene declares {width}x{height}"
        )
    return stacked


@contextmanager
def _timeline_frames_dir(capture: SceneCapture):
    """A single directory holding every frame of the scene, in timeline order.

    A one-shot scene yields its own frames directory **unchanged**, so nothing
    is copied and the existing corpus's numbers are bit-identical to what they
    were before multi-shot support existed.

    A multi-shot scene gets a temporary directory of symlinks renumbered
    ``0..N-1``, because ffmpeg's image2 demuxer reads the contiguous
    ``frame_%06d.png`` run from 0 and each shot restarts its own numbering at 0.
    Encoding the shots separately and concatenating would not do: the lossless
    reference has to be the same sequence, in the same order, that the delivered
    mp4 shows.
    """
    from an.adapters.cutout.render import DEFAULT_FRAME_PNG_PATTERN

    if len(capture.shots) == 1:
        yield capture.shots[0].frames_dir
        return
    staged = Path(tempfile.mkdtemp(prefix=f"an-bench-timeline-{capture.name}-"))
    try:
        index = 0
        for shot in capture.shots:
            for src in sorted(shot.frames_dir.glob("frame_*.png")):
                (staged / (DEFAULT_FRAME_PNG_PATTERN % index)).symlink_to(src)
                index += 1
        yield staged
    finally:
        shutil.rmtree(staged, ignore_errors=True)


def _render_side_values(src_rgb, packed, palette_uint32) -> dict[str, Value]:
    """Family A. Computed on the pre-encode pixels; blind to the encoder by construction."""
    width_mean, width_median = M.edge_transition_width(src_rgb)
    return {
        "edge_transition_width": measured(
            round(width_mean, 4), median=round(width_median, 4)
        ),
        "off_palette_pixel_fraction": measured(
            round(M.off_palette_pixel_fraction(packed, palette_uint32), 6)
        ),
        "frame_distinct_colours": measured(round(M.frame_distinct_colours(packed), 4)),
    }


def _stack(loader, capture: SceneCapture, *, height: int, width: int):
    """Decode every shot's frames in **timeline order** and concatenate them.

    The delivered mp4 is the concatenation of the per-shot renders in
    ``scene.timeline`` order (``an/render.py``'s ``_ffmpeg_concat``), so the
    reference leg of every encode-side metric has to be built the same way.
    ``capture.shots`` is already in timeline order — see
    :func:`an.bench.corpus.iter_shot_dirs`, whose ``order`` argument is
    mandatory for exactly this reason.
    """
    import numpy as np

    parts = [loader(s.frames_dir, height=height, width=width) for s in capture.shots]
    return parts[0] if len(parts) == 1 else np.concatenate(parts)


def _merged_palette(capture: SceneCapture) -> dict:
    """The union of every shot's declared palette, plus per-shot provenance.

    A union rather than the first shot's: ``off_palette_pixel_fraction`` is
    computed over the concatenated frames, so a colour declared only by the
    second shot would otherwise be counted as off-palette for the whole scene.
    """
    per_shot = [
        (s.shot_id, P.palette_for_scene(s.scene_json, runtime_dir=s.runtime_dir))
        for s in capture.shots
    ]
    palette: set = set()
    hexes: set = set()
    sources: dict[str, Any] = {}
    unresolved: list = []
    superset = False
    for shot_id, pal in per_shot:
        palette |= set(pal["palette"])
        hexes |= set(pal.get("palette_hex") or ())
        sources[shot_id] = pal.get("palette_sources")
        unresolved.extend(pal.get("unresolved_svg_colour_tokens") or ())
        superset = superset or bool(pal.get("palette_is_superset"))
    return {
        "palette": sorted(palette),
        "palette_hex": sorted(hexes),
        "palette_sources": sources,
        "palette_is_superset": superset,
        "unresolved_svg_colour_tokens": sorted(set(unresolved)),
    }


def _scene_metrics(capture: SceneCapture) -> tuple[dict[str, Value], dict[str, Any]]:
    """The whole panel for one scene, plus the provenance the panel needs recorded.

    Computed over the scene's **concatenated** frames, not over its first shot.
    A single-shot scene is the same thing with one part, so the numbers for the
    existing corpus are unchanged; a multi-shot scene is the reason this is not
    ``_shot_metrics``, because the delivered mp4 it is compared against covers
    every shot.
    """
    import numpy as np

    h, w = capture.resolution[1], capture.resolution[0]
    values: dict[str, Value] = {}

    if not _ffmpeg_available():
        # Checked BEFORE the first decode, not after it. The check used to sit
        # below `source_rgb`, which runs ffmpeg — so on a machine without it the
        # bench raised `BenchDecodeError` and this whole degradation branch was
        # unreachable. Render-side metrics still need the frames, so they are
        # decoded with the package's own PNG reader instead: family A is
        # render-side and has no business depending on the encoder's toolchain.
        src_rgb = _stack(_png_source_rgb, capture, height=h, width=w)
        packed = M.pack_rgb(src_rgb)
        pal = _merged_palette(capture)
        values.update(_render_side_values(src_rgb, packed, pal["palette"]))
        reason = "ffmpeg/ffprobe not on PATH; every encode-side metric needs both"
        for key in METRICS:
            values.setdefault(key, unavailable(reason))
        return values, {
            **{k: v for k, v in pal.items() if k != "palette"},
            "decoder": "an.bench.png (ffmpeg absent)",
            "source_pixels_sha256": contract.frames_sha256(src_rgb),
            "frames_on_disk": sum(s.frame_count for s in capture.shots),
            "decoded_source_frames": int(len(src_rgb)),
        }

    src_rgb = _stack(imageio.source_rgb, capture, height=h, width=w)
    packed = M.pack_rgb(src_rgb)

    pal = _merged_palette(capture)
    palette_uint32 = pal["palette"]

    values.update(_render_side_values(src_rgb, packed, palette_uint32))

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
        "decoder": "ffmpeg",
        "source_pixels_sha256": contract.frames_sha256(src_rgb),
        "frames_on_disk": sum(s.frame_count for s in capture.shots),
        "decoded_source_frames": int(len(src_rgb)),
        "tolerances": {
            "edge_flat_tol": M.EDGE_FLAT_TOL,
            "flat_dev_tol": M.FLAT_DEV_TOL,
            "flicker_delta_tol": M.FLICKER_DELTA_TOL,
            "ssim_radius": M.SSIM_RADIUS,
            "luma_coefficients": list(M.LUMA_709),
        },
    }

    # The reference. `-qp 0` is lossless, so this IS the plane libx264 received
    # — on any build, without assuming that our own PNG->YUV conversion matches
    # ffmpeg's internal one. It does on ffmpeg 8.1 and does not on the Linux
    # runner's older build (see the module docstring), which is exactly why the
    # metrics no longer depend on it.
    with _timeline_frames_dir(capture) as frames_dir:
        qp0_mp4 = frames_dir.parent / "_bench_qp0.mp4"
        lossless_reference(frames_dir, capture.fps, qp0_mp4)
        try:
            ref_yuv = imageio.decoded_yuv(qp0_mp4, height=h, width=w)
            ref_rgb = imageio.decoded_rgb(qp0_mp4, height=h, width=w)
            distance = conversion_distance(frames_dir, qp0_mp4, height=h, width=w)
            prov["png_to_encoder_input_luma"] = distance
            # Derived so a reader does not have to. When it is True,
            # `coded_luma_edge_error` (lossless-referenced) and `chroma_edge_dY`
            # (PNG-referenced) are numerically identical on this build — which
            # looks like a duplicate and is not: they differ by exactly this
            # residual, and on a build where it is nonzero they diverge.
            prov["references_coincide"] = distance.get("luma_residual_max") == 0
            # The direct RGB->444 conversion, kept for ONE metric: the chroma one,
            # whose subject IS the 4:2:0 subsampling that happens during the
            # conversion. A qp0 file's chroma is already subsampled, so referencing
            # it there would read ~0 and measure nothing.
            src_yuv = imageio.source_yuv(frames_dir, height=h, width=w)
            dec_yuv = imageio.decoded_yuv(capture.mp4, height=h, width=w)
            dec_rgb = imageio.decoded_rgb(capture.mp4, height=h, width=w)
        finally:
            qp0_mp4.unlink(missing_ok=True)

    n = min(len(src_yuv), len(dec_yuv), len(dec_rgb), len(src_rgb), len(ref_yuv))
    if n != len(src_rgb) or n != len(dec_rgb):
        # Not silently truncated: every encode-side metric pairs frame i with
        # frame i, so a length disagreement means the pairing is offset and
        # every one of them would measure inter-frame motion instead.
        prov["frame_count_disagreement"] = {
            "source_rgb": int(len(src_rgb)),
            "source_yuv": int(len(src_yuv)),
            "decoded_rgb": int(len(dec_rgb)),
            "decoded_yuv": int(len(dec_yuv)),
            "lossless_reference": int(len(ref_yuv)),
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

    # Referenced to the LOSSLESS leg: pure quantiser damage, with no
    # colour-conversion term and no build dependence.
    values["coded_luma_edge_error"] = _num(
        M.masked_mean_abs, dec_yuv[:n, 0], ref_yuv[:n, 0], edge
    )

    # Referenced to the PNG conversion, deliberately and for this metric only:
    # the 4:2:0 subsampling it exists to see happens DURING that conversion, so
    # a lossless-referenced version would read ~0 and measure nothing. `dY` on
    # the same reference and the same mask is therefore NOT a second name for
    # `coded_luma_edge_error` — the two differ by exactly the reference — and it
    # is the only denominator for which the ratio means what it claims.
    d_y = M.masked_mean_abs(dec_yuv[:n, 0], src_yuv[:n, 0], edge)
    d_cr = M.masked_mean_abs(dec_yuv[:n, 2], src_yuv[:n, 2], edge)
    values["chroma_edge_dY"] = _num(lambda: d_y)
    values["chroma_edge_dCr"] = _num(lambda: d_cr)
    values["chroma_edge_dCr_over_dY"] = (
        measured(round(float(d_cr / d_y), 6))
        if d_y and d_y == d_y and d_cr == d_cr
        else unavailable(
            "the luma error on the edge mask is zero or undefined, so the ratio "
            "has no value"
        )
    )

    frac, p99 = M.flat_field_deviation(ref_rgb[:n], dec_rgb[:n], flat)
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
        M.encode_flicker_on_held_pixels, ref_rgb[:n], dec_rgb[:n]
    )
    # The Q4 pair, both on the PNG reference. `encode_ringing_excess` cancels a
    # term that exists only when both its legs share that reference — against
    # the lossless leg the second term is 0 by construction and the metric
    # degenerates into raw overshoot, which is the form the research refuted.
    # `ring_band_mae` is its declared rival, so it must share the reference or
    # the comparison answers a different question.
    values["ring_band_mae"] = _num(
        M.masked_mean_abs, dec_yuv[:n, 0], src_yuv[:n, 0], ring
    )
    values["encode_ringing_excess"] = _num(
        M.encode_ringing_excess, dec_yuv[:n, 0], ref_yuv[:n, 0], src_yuv[:n, 0], ring
    )

    values["video_stream_bytes"] = measured(imageio.video_stream_bytes(capture.mp4))
    values["file_bytes"] = measured(int(capture.mp4.stat().st_size))
    return values, prov


#: Family B's two rows, one in each block. Named here because the golden result
#: fills both from one comparison, and a reader has to be able to see that the
#: boolean and the number are the same evidence read two ways.
GOLDEN_METRIC_KEY: str = "min_ssim_win8_vs_golden"
GOLDEN_TRIPWIRE_KEY: str = "golden_identity"

#: Said when a run blessed the goldens it would otherwise have compared against.
JUST_BLESSED_DETAIL: str = (
    "this run WROTE these goldens, so comparing against them is a tautology: "
    "the identity holds by construction and no code could have failed it. Run "
    "`an bench` again, without --bless, for a comparison that can fail."
)


def _golden_values(result: dict) -> tuple[Value, Value]:
    """``(metric, tripwire)`` for one scene's golden result.

    Both come from the same comparison. ``golden_identity`` is a change
    detector and counts zero toward any criterion; ``min_ssim_win8_vs_golden``
    is the magnitude beside it. Splitting them across the two blocks is what
    keeps a boolean from being counted as a measurement.
    """
    state = result["state"]
    if state == "gated":
        value = gated(result["gate"], detail=result["detail"])
        return value, value
    if state == "unavailable":
        value = unavailable(result["detail"])
        return value, value
    extra = {"changed_px": result["changed_px"], "max_delta": result["max_delta"]}
    mismatch = result.get("shape_mismatch")
    if mismatch:
        # The golden was blessed at a different resolution, so there is no
        # windowed SSIM to report — but the identity IS knowable, and it is
        # False. Reporting the boolean and withholding the number is the split
        # the two blocks exist for; substituting 0.0 would be a measurement
        # nobody took, which is the unknown-is-not-zero failure this schema
        # exists to prevent.
        metric = unavailable(
            "the committed golden has a different shape from today's frame "
            f"({mismatch}); a windowed SSIM between them has no value. The "
            "scene's resolution changed — re-bless deliberately."
        )
        return metric, measured(False, **extra, shape_mismatch=mismatch)
    metric = measured(round(float(result["min_ssim_win8"]), 6), **extra)
    tripwire = measured(bool(result["identical"]), **extra)
    return metric, tripwire


def run_bench(
    *,
    scenes: dict[str, Fixture] | None = None,
    out: Path | None = None,
    keep_render: Path | None = None,
    write: bool = True,
    bless: str = "",
    golden_root: Path | None = None,
) -> dict:
    """Render the corpus, compute the panel, and (by default) write the row.

    ``bless`` is the **reason** a re-bless is being made, and passing it is what
    turns the run into a bless. One argument rather than a ``--bless`` flag plus
    a ``--reason`` string, so "blessed with no recorded reason" — the failure
    this rule exists to prevent — is not expressible.

    ``golden_root`` redirects where goldens are read and written, and it exists
    because without it a test of the bless path has no choice but to overwrite
    the committed corpus. That is not hypothetical: the first version of an#38's
    bless test did exactly that, replacing a real bless record's reason with the
    test's own.
    """
    root = repo_root()
    goldens = Path(golden_root) if golden_root is not None else root
    fixtures = scenes if scenes is not None else DFLT_FIXTURES
    git = git_state(root)
    # Probed ONCE, before the loop: the golden path keys on the Chromium build,
    # so the gate needs it per scene, and probing again at the end would launch
    # a second browser that could in principle report a different build from the
    # one that actually rendered.
    browser = environment.probe_browser()
    chromium_build = G.chromium_build_of({"render_side": browser})

    scene_blocks: dict[str, dict] = {}
    captures: list[SceneCapture] = []
    blessed: dict[str, dict] = {}
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

            values, scene_prov = _scene_metrics(capture)
            scene_contract = contract.scenes_contract_sha256(
                [s.scene_json for s in capture.shots]
            )
            expected = sum(
                expected_frame_count(s.duration, capture.fps) for s in capture.shots
            )

            if bless:
                blessed[name] = G.bless_scene(
                    capture,
                    times=fixture.golden_frames,
                    chromium_build=chromium_build,
                    reason=bless,
                    git=git,
                    scene_contract_sha256=scene_contract,
                    golden_note=fixture.golden_note,
                    root=goldens,
                )
                golden = {"state": "gated", "gate": G.GATE_JUST_BLESSED,
                          "detail": JUST_BLESSED_DETAIL}
            elif chromium_build is None:
                golden = {
                    "state": "gated",
                    "gate": G.GATE_BUILD_UNKNOWN,
                    "detail": (
                        "the Chromium build could not be determined, and every "
                        "golden path keys on it, so no golden could be looked up. "
                        f"probe: {browser.get('error', 'no chromium_build field')}"
                    ),
                }
            else:
                golden = G.compare_scene(
                    capture,
                    times=fixture.golden_frames,
                    chromium_build=chromium_build,
                    root=goldens,
                )
            metric_value, tripwire_value = _golden_values(golden)
            values[GOLDEN_METRIC_KEY] = metric_value
            scene_prov["golden"] = {
                **golden,
                "chromium_build": chromium_build,
                "declared_times": list(fixture.golden_frames),
                "what_moves": fixture.golden_note,
                "note": (
                    "DIAGNOSTICS, not comparability keys. `an bench --compare` "
                    "keys on named provenance fields — the scene contract hash, "
                    "the resolution, the mask parameters, the encode command, "
                    "the x264 SEI and the ISA — never on this block's equality. "
                    "`today_sha256` here is a per-run MEASUREMENT and changes "
                    "exactly when the render changes, which is when two rows are "
                    "most worth comparing."
                ),
            }

            provenance = {
                "source": capture.source,
                "prepared": capture.prepared,
                "scene_contract_sha256": scene_contract,
                "shot_contract_sha256": {
                    s.shot_id: contract.scene_contract_sha256(s.scene_json)
                    for s in capture.shots
                },
                "n_drawable_entities": sum(
                    contract.count_drawable_entities(s.scene_json) for s in capture.shots
                ),
                "n_declared_entity_refs": capture.n_declared_entity_refs,
                "n_nodes": sum(contract.count_nodes(s.scene_json) for s in capture.shots),
                "n_frames": expected,
                "resolution": list(capture.resolution),
                "fps": capture.fps,
                "shot_count": len(capture.shots),
                "shot_order": [s.shot_id for s in capture.shots],
                "shots": [
                    {"id": s.shot_id, "frames": s.frame_count, "duration": s.duration}
                    for s in capture.shots
                ],
                "visual_kinds": sorted(capture.visual_kinds),
                "asset_resolution": capture.asset_resolution,
                "audio_cache": capture.audio_cache,
                "wall_seconds": capture.wall_seconds,
                **scene_prov,
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
                tripwires={GOLDEN_TRIPWIRE_KEY: tripwire_value},
            )
    finally:
        if keep_render is None:
            for capture in captures:
                cleanup(capture)

    ledger = build_ledger(
        provenance={
            "git": git,
            "render_kwargs": dict(BENCH_RENDER_KWARGS),
            "environment": environment_record(x264_sei=sei, browser=browser),
            **({"blessed": blessed} if blessed else {}),
            "encode_command_source": (
                "an.adapters.cutout.render._ffmpeg_mux + DETERMINISTIC_X264_ARGS"
            ),
            "decode_commands": {
                "source_rgb": imageio.source_rgb_command(Path("<frames>")),
                "source_yuv444": imageio.source_yuv_command(Path("<frames>")),
                "decoded_rgb": imageio.decoded_rgb_command(Path("<mp4>")),
                "decoded_yuv444": imageio.decoded_yuv_command(Path("<mp4>")),
            },
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
