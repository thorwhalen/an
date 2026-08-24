"""The frozen legibility strips of the `dialogue` corpus line (an#97).

Two strips of eight frames each — the line rendered by the pre-an#97
condenser (``before``) and by the co-articulation passes (``after``) — are the
**cassette key** for the legibility judge: ``judge_key`` hashes their bytes,
so they are committed once and never re-rendered. They are deliberately not
goldens (a golden has a re-bless lifecycle that would silently change the
key; see the Wave 2 record) and they come from ``an``'s own PNG writer, so
their bytes are a function of the decoded pixels and nothing else.

    python tests/_lipsync_strips.py        # render + freeze both strips

renders the fixture twice through the bench's capture path (Playwright +
ffmpeg required) and writes ``tests/fixtures/vision_frames/lipsync/{before,after}/``.
Re-running it changes the key only if the picture changed; a changed key
means the cassette has to be recorded again, which is the point.
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STRIPS_DIR = ROOT / "tests" / "fixtures" / "vision_frames" / "lipsync"
#: The corpus fixture whose one line the strips show.
FIXTURE = "dialogue"
#: Eight evenly spaced frames of the fixture's 24-frame, one-second shot —
#: inside the line, never its rest at frame 0 or the tail after it.
STRIP_FRAMES: tuple[int, ...] = (1, 4, 7, 10, 13, 16, 19, 22)
VARIANTS: tuple[tuple[str, bool], ...] = (("before", False), ("after", True))


def load_strips(root: Path = STRIPS_DIR) -> dict[str, list[bytes]]:
    """``{variant: [png bytes, ...]}`` for every variant that is complete."""
    out = {}
    for variant, _ in VARIANTS:
        files = sorted((root / variant).glob("*.png"))
        if len(files) == len(STRIP_FRAMES):
            out[variant] = [f.read_bytes() for f in files]
    return out


def freeze(*, root: Path = ROOT, out: Path = STRIPS_DIR) -> dict[str, list[Path]]:
    """Render the fixture with the passes off and on; write both strips."""
    from an.adapters.cutout import compile as compile_mod
    from an.bench import golden as G
    from an.bench.capture import capture_fixture
    from an.bench.corpus import DFLT_FIXTURES
    from an.bench.png import read_png, write_png

    written: dict[str, list[Path]] = {}
    original = compile_mod.COARTICULATION_ENABLED
    for variant, enabled in VARIANTS:
        compile_mod.COARTICULATION_ENABLED = enabled
        try:
            with tempfile.TemporaryDirectory(prefix=f"an-strips-{variant}-") as tmp:
                capture = capture_fixture(FIXTURE, DFLT_FIXTURES[FIXTURE], repo_root=root, keep_render=Path(tmp))
                n = sum(s.frame_count for s in capture.shots)
                if max(STRIP_FRAMES) >= n:
                    raise RuntimeError(f"{FIXTURE} rendered {n} frames; STRIP_FRAMES names frame {max(STRIP_FRAMES)}")
                refs = G.resolve_frames(capture, [i / capture.fps for i in STRIP_FRAMES])
                target = out / variant
                if target.exists():
                    shutil.rmtree(target)
                target.mkdir(parents=True)
                paths = []
                for slot, ref in enumerate(refs):
                    dst = target / f"f{slot}_frame{ref.index:03d}.png"
                    write_png(dst, read_png(G.frame_png_path(capture, ref)))
                    paths.append(dst)
                written[variant] = paths
        finally:
            compile_mod.COARTICULATION_ENABLED = original
    return written


if __name__ == "__main__":
    for variant, paths in freeze().items():
        print(f"{variant}: {len(paths)} frames -> {paths[0].parent}")
