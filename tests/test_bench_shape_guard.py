"""The bench refuses a render whose pixels are not the size the scene declares.

The defect this closes was arithmetically invisible. ``an/bench/imageio.py``'s
``_reshape`` tested that the decoded byte count *divides* by
``planes * height * width`` — and a k-times supersample makes the buffer exactly
``k**2`` larger, so the check **always passed** and every family-A metric was
computed over ``k**2 * N`` wrongly-shaped frames. The corruption is *plausible*
rather than obvious: at k=2 destination row 0 is the source row's left half and
row 1 its right half, so most horizontal runs survive and
``edge_transition_width`` returns a believable number (an#54).

Default lane on purpose: no ``pytest.mark.browser``, no ``pytest.mark.ffmpeg``,
no module-level ``importorskip`` — which would delete these from *collection*
rather than skip them (an#22).
"""

from __future__ import annotations

import inspect
from pathlib import Path

import numpy as np
import pytest

from an.bench import imageio
from an.bench.capture import SceneCapture, ShotCapture, distinct_png_sizes
from an.bench.png import encode_png
from an.bench.run import BenchError, _assert_declared_resolution

#: The scene's declared size in every case below, as `(width, height)`.
DECLARED: tuple[int, int] = (320, 240)


def _write_frames(directory: Path, sizes: list[tuple[int, int]]) -> Path:
    """One PNG per entry, sized as asked. ``sizes`` is ``(width, height)``."""
    directory.mkdir(parents=True, exist_ok=True)
    for i, (w, h) in enumerate(sizes):
        (directory / f"frame_{i:06d}.png").write_bytes(
            encode_png(np.zeros((h, w, 3), np.uint8))
        )
    return directory


def _capture(frames_dir: Path, *, declared: tuple[int, int] = DECLARED) -> SceneCapture:
    """A `SceneCapture` carrying one shot, with every required field filled."""
    shot = ShotCapture(
        shot_id="s1",
        frames_dir=frames_dir,
        scene_json={},
        runtime_dir=frames_dir.parent / "runtime",
        frame_count=len(list(frames_dir.glob("frame_*.png"))),
        duration=1.0,
        frame_sizes=distinct_png_sizes(frames_dir),
    )
    return SceneCapture(
        name="fixture",
        source="misc/bench/corpus/fixture",
        prepared=False,
        project_dir=frames_dir.parent,
        mp4=frames_dir.parent / "out.mp4",
        shots=[shot],
        resolution=declared,
        fps=24,
        duration=1.0,
        n_declared_entity_refs=0,
        visual_kinds=set(),
        asset_resolution=[],
        audio_cache="cold",
        wall_seconds=0.0,
    )


def test_a_supersampled_render_is_refused_before_a_single_metric_is_computed(tmp_path):
    """MUTATION: `if sizes != {capture.resolution}:` -> `if False:`.

    `capture.resolution` comes from the staged scene's `meta.width/height` and
    never from a file; `ShotCapture.frame_sizes` is read from each PNG's IHDR
    and never from the scene. Without comparing them nothing in the pipeline
    ever looks at the size of the pixels it is measuring.
    """
    doubled = _write_frames(tmp_path / "two_x" / "frames", [(640, 480)] * 4)
    with pytest.raises(BenchError) as e:
        _assert_declared_resolution(_capture(doubled))
    assert "640, 480" in str(e.value) and "320, 240" in str(e.value), (
        "the refusal must name BOTH sizes; 'the frames are wrong' sends the "
        "reader to look for the wrong thing"
    )
    assert "frame stage" in str(e.value), (
        "and it must say where a deliberate supersample's downscale belongs, "
        "because meeting this refusal is the expected way to discover that"
    )

    matching = _write_frames(tmp_path / "one_x" / "frames", [(320, 240)] * 4)
    _assert_declared_resolution(_capture(matching))  # must not raise


def test_a_half_applied_supersample_is_refused_even_though_some_frames_are_right(
    tmp_path,
):
    """MUTATION: read one frame's size instead of every frame's.

    Sampling the first frame is the natural optimisation and it is exactly
    blind to a lever that took effect partway through a shot. Reading all of
    them costs 24 bytes per file, which is why there is no reason to sample.
    """
    mixed = _write_frames(
        tmp_path / "mixed" / "frames", [(320, 240), (320, 240), (640, 480), (640, 480)]
    )
    assert distinct_png_sizes(mixed) == ((320, 240), (640, 480))
    with pytest.raises(BenchError):
        _assert_declared_resolution(_capture(mixed))


def test_a_shot_that_wrote_no_frames_is_refused_and_says_so(tmp_path):
    """An empty `frame_sizes` is not "the sizes agree"; it is nothing to measure."""
    empty = _write_frames(tmp_path / "empty" / "frames", [])
    with pytest.raises(BenchError, match="wrote no frames"):
        _assert_declared_resolution(_capture(empty))


def test_reshape_refuses_a_buffer_that_is_exactly_k_squared_too_large():
    """MUTATION: `if frames is not None and len(buf) != per_frame * frames:` -> `if False:`.

    The second assertion is the point of the test: it pins the plausible WRONG
    ANSWER the old code returned, rather than only that the new code raises.
    """
    w, h, n, k = 320, 240, 4, 2
    buf = b"\x00" * (3 * (h * k) * (w * k) * n)

    with pytest.raises(imageio.BenchDecodeError, match="not exactly 4 frames"):
        imageio._reshape(buf, planes=3, height=h, width=w, label="source_rgb", frames=n)

    # What the divisibility check did instead: k**2 as many frames, each one a
    # scramble of a quarter of a real frame, and not a single complaint.
    assert (
        imageio._reshape(
            buf, planes=3, height=h, width=w, label="source_rgb", frames=None
        ).shape[0]
        == n * k * k
    )


def test_no_decode_leg_can_omit_the_frame_count_by_forgetting_it():
    """MUTATION: give `_reshape`'s `frames` a default of `None`.

    Every existing call keeps working and the check silently stops applying to
    any leg written later that omits it. A defaultless keyword-only parameter
    is what makes omission a `TypeError` instead of a no-op.
    """
    from an.bench.run import _png_source_rgb

    param = inspect.signature(imageio._reshape).parameters["frames"]
    assert param.kind is inspect.Parameter.KEYWORD_ONLY
    assert param.default is inspect.Parameter.empty, (
        "a default would let a future decode leg opt out of the shape check by "
        "saying nothing"
    )

    for fn in (imageio.source_rgb, imageio.source_yuv, _png_source_rgb):
        p = inspect.signature(fn).parameters["frames"]
        assert p.default is inspect.Parameter.empty, f"{fn.__name__} may not default"

    # The asymmetry is deliberate and pinned, not an oversight: the mp4 legs'
    # count is whatever the encoder emitted, and the run RECORDS that
    # disagreement rather than crashing on it.
    for fn in (imageio.decoded_rgb, imageio.decoded_yuv):
        assert "frames" not in inspect.signature(fn).parameters
