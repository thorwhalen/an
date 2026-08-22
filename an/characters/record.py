"""Record a character's preview HTML to an mp4.

Uses Playwright's video recording (saved as webm) and converts to mp4
via ffmpeg. The result is a real video file showing the new SVG
character art animating: cycling visemes + breath/head-tilt.

This is a stop-gap until Phase 11b wires the SVG-texture path into
``runtime.js`` and proper scene rendering uses the new character art
directly.

>>> # Smoke-tested in tests/test_characters_record.py
"""

from __future__ import annotations

import shutil
import subprocess

from an.base import MP4_FASTSTART_ARGS
from pathlib import Path
from typing import Optional


DEFAULT_RECORD_DURATION_S: float = 8.0
DEFAULT_RECORD_SIZE: tuple[int, int] = (640, 480)
DEFAULT_FFMPEG_CRF: int = 23


class PreviewRecordError(RuntimeError):
    """Raised when preview recording fails."""


def record_preview_to_mp4(
    preview_html: str | Path,
    out_mp4: str | Path,
    *,
    duration_s: float = DEFAULT_RECORD_DURATION_S,
    size: tuple[int, int] = DEFAULT_RECORD_SIZE,
    fps: int = 30,
    crf: int = DEFAULT_FFMPEG_CRF,
) -> Path:
    """Record ``preview_html`` to ``out_mp4`` for ``duration_s`` seconds.

    Returns the output mp4 path.

    Pipeline:
      1. Playwright launches headless Chromium with video recording on.
      2. Navigates to ``preview_html`` (file:// URL).
      3. Waits ``duration_s`` real-time so the browser captures frames.
      4. Closes the context to flush the webm.
      5. ffmpeg re-encodes the webm to H.264 mp4 (better compatibility,
         smaller files, plays in `quicktime` / GitHub previews).

    Both Playwright (project dep) and ffmpeg (system dep, already
    required by the renderer) must be installed.
    """
    if shutil.which("ffmpeg") is None:
        raise PreviewRecordError(
            "ffmpeg not found on PATH. Install with: brew install ffmpeg"
        )
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as e:  # pragma: no cover
        raise PreviewRecordError(
            "playwright not installed. Run: pip install playwright "
            "&& playwright install chromium"
        ) from e

    preview = Path(preview_html).resolve()
    if not preview.exists():
        raise FileNotFoundError(preview)

    out = Path(out_mp4)
    out.parent.mkdir(parents=True, exist_ok=True)
    work_dir = out.parent / f".{out.stem}.record_tmp"
    work_dir.mkdir(parents=True, exist_ok=True)

    width, height = size
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
            context = browser.new_context(
                viewport={"width": width, "height": height},
                record_video_dir=str(work_dir),
                record_video_size={"width": width, "height": height},
            )
            page = context.new_page()
            page.goto(preview.as_uri())
            # The preview JS uses requestAnimationFrame; just wait wall-clock
            # so the recording captures the breath cycles + viseme swaps.
            page.wait_for_timeout(int(duration_s * 1000))
            video = page.video
            context.close()
            browser.close()
            if video is None:
                raise PreviewRecordError("playwright did not produce a video")
            webm_path = Path(video.path())
        if not webm_path.exists():
            raise PreviewRecordError(f"expected webm at {webm_path} but not found")
        _ffmpeg_webm_to_mp4(webm_path, out, fps=fps, crf=crf)
    finally:
        if work_dir.exists():
            shutil.rmtree(work_dir, ignore_errors=True)
    return out


def record_character(
    char_dir: str | Path,
    *,
    name: Optional[str] = None,
    out_mp4: Optional[str | Path] = None,
    duration_s: float = DEFAULT_RECORD_DURATION_S,
    size: tuple[int, int] = DEFAULT_RECORD_SIZE,
) -> Path:
    """Render preview.html for the character at ``char_dir`` and record it.

    The preview HTML is generated/refreshed via the same writer used by
    ``an character preview``, so this command is self-contained.
    """
    from an.characters.cli import _write_preview_html

    cdir = Path(char_dir)
    if not cdir.is_dir():
        raise FileNotFoundError(cdir)
    cname = name or cdir.name
    preview_html = _write_preview_html(cdir, name=cname)
    target = Path(out_mp4) if out_mp4 else cdir / "preview.mp4"
    return record_preview_to_mp4(preview_html, target, duration_s=duration_s, size=size)


def _ffmpeg_webm_to_mp4(webm: Path, mp4: Path, *, fps: int, crf: int) -> None:
    """Re-encode webm → H.264 mp4 with sane defaults for short loops."""
    cmd = [
        "ffmpeg",
        "-y",
        "-loglevel",
        "error",
        "-i",
        str(webm),
        "-r",
        str(fps),
        "-c:v",
        "libx264",
        # NOT `an.adapters.cutout.render`'s flags, and not its `-pix_fmt` knob
        # either — deliberately, and this is the "third x264 site" an#59 names.
        # What this encodes is a CHARACTER PREVIEW: a documentation artifact of
        # a webm screen recording, not a rendered shot. Unifying it would put
        # `-threads 1` and BT.709 tagging on a preview for no benefit, and would
        # make flipping the DELIVERABLE's pixel format silently change every
        # character sheet. Its CRF is a parameter here and a pinned constant
        # there, which is the same distinction stated another way.
        "-pix_fmt",
        "yuv420p",
        "-crf",
        str(crf),
        # The one fact that IS shared, so it is imported rather than respelled:
        # "does an's mp4 have faststart" must not depend on which of the four
        # ffmpeg calls in this repo you happen to be reading (an#57).
        *MP4_FASTSTART_ARGS,
        str(mp4),
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as e:
        raise PreviewRecordError(
            f"ffmpeg failed: {e.stderr.strip() or e.stdout.strip()}"
        ) from e
