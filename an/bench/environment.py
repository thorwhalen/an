"""The environment tuple — the fields that decide whether two rows may be compared.

Split into two halves with **different comparison rules**, because the two
sides of the pipeline were measured and their answers are opposite:

**Render side — comparable on any machine.** Both render paths, four machines
(local arm64 macOS, ``macos-latest``, ``ubuntu-latest`` x86-64,
``ubuntu-24.04-arm``), 132 frames each: zero differing pixels *and* zero
differing PNG bytes, across two different SwiftShader JIT backends. So the
render-side metrics need **no cross-machine band column**, and the golden
corpus can be a CI gate. Record the Chromium build and the **launch argv
verbatim** — all four rasteriser configurations report a byte-identical
``UNMASKED_RENDERER_WEBGL`` string, so the renderer string is demonstrably
blind to the choice it was proposed to guard.

**Encode side — machine-scoped, not bandable.** Same ISA + same x264 build is
byte-identical; a different ISA moves the decoded stream a little (luma <=2.66%
of samples); a different x264 build moves it by two orders of magnitude (up to
99.2% of samples, mean |d| 3.94, max 36). A band that wide would swallow
``flat_field_deviation``'s entire crf18->23 signal. So ``--compare`` (an#40)
must **refuse** rows whose ``x264_sei`` or ``isa`` differ, in the same way it
refuses rows with a different ``scene_contract_sha256`` — the number is
uninterpretable, not good or bad.
"""

from __future__ import annotations

import platform
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

#: The x264 build stamp embedded in every mp4, e.g. ``core 165 r3222 <sha>``.
#: Nothing strips it (``-x264-params sei=0`` is silently ignored), which is
#: exactly why it is usable as the comparability key.
_X264_SEI_RE = re.compile(rb"core\s+(\d+)\s+r(\d+)\s+([0-9a-f]+)")


def tool_version(name: str) -> str | None:
    from importlib.metadata import PackageNotFoundError, version

    try:
        return version(name)
    except PackageNotFoundError:
        return None


def ffmpeg_identity() -> dict[str, Any]:
    """The ffmpeg build banner. Informational — the ``x264_sei`` is the key."""
    exe = shutil.which("ffmpeg")
    if exe is None:
        return {"error": "ffmpeg not on PATH"}
    try:
        out = subprocess.run(
            [exe, "-version"], capture_output=True, text=True, check=False
        )
    except OSError as e:
        return {"error": f"{type(e).__name__}: {e}"}
    return {
        "path": exe,
        "banner": out.stdout.splitlines()[0] if out.stdout else "",
    }


def x264_sei(mp4: Path) -> str | None:
    """The encoder build + thread count, read straight out of the file."""
    match = _X264_SEI_RE.search(Path(mp4).read_bytes())
    return match.group(0).decode("ascii", "replace") if match else None


def probe_browser() -> dict[str, Any]:
    """Launch Chromium with the render path's own flags and read back its identity.

    Never raises: a probe that crashes must not cost a caller a completed
    capture, and a recorded ``error`` is more honest than a missing field that
    reads as "nothing to report".
    """
    probe_js = """() => {
        const c = document.createElement('canvas');
        const gl = c.getContext('webgl2') || c.getContext('webgl');
        if (!gl) return {webgl: null};
        const dbg = gl.getExtension('WEBGL_debug_renderer_info');
        return {
            renderer: dbg ? gl.getParameter(dbg.UNMASKED_RENDERER_WEBGL)
                          : gl.getParameter(gl.RENDERER),
            vendor: dbg ? gl.getParameter(dbg.UNMASKED_VENDOR_WEBGL)
                        : gl.getParameter(gl.VENDOR),
            version: gl.getParameter(gl.VERSION),
            samples: gl.getParameter(gl.SAMPLES),
        };
    }"""
    try:
        from playwright.sync_api import sync_playwright

        from an.adapters.cutout.render import DETERMINISTIC_CHROMIUM_ARGS

        args = list(DETERMINISTIC_CHROMIUM_ARGS)
        with sync_playwright() as p:
            browser = p.chromium.launch(args=args, headless=True)
            try:
                page = browser.new_page()
                return {
                    "launch_argv": args,
                    "headless": True,
                    "chromium_build": browser.version,
                    "executable_path": str(p.chromium.executable_path),
                    "webgl": page.evaluate(probe_js),
                }
            finally:
                browser.close()
    except Exception as e:  # noqa: BLE001 — reported, never fatal
        return {"error": f"{type(e).__name__}: {e}"}


def runtime_sha256() -> str:
    """A digest of the JS runtime the renderer will stage, files and names.

    Provenance, NOT a comparability key: the runtime is the code under test, and
    two rows rendered by different runtimes are exactly what `an bench --compare`
    exists to compare. What it buys is that a render-side mutation leaves a
    fingerprint in the row — before this, the `disabled_aa` lever had no way to
    prove it applied, and `assert not report["mutation_may_not_have_applied"]`
    asserted nothing for it (an#41 review).
    """
    import hashlib

    from an.adapters.cutout.render import runtime_dir

    digest = hashlib.sha256()
    root = runtime_dir()
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        digest.update(str(path.relative_to(root)).encode("utf-8"))
        digest.update(path.read_bytes())
    return digest.hexdigest()


def environment_record(
    *,
    pix_fmt: str,
    x264_sei: str | None = None,
    browser: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Everything about this machine that could plausibly move a number.

    ``browser`` lets the caller supply an already-taken probe. The golden gate
    needs the Chromium build *before* the run-level provenance is assembled —
    the path keys on it — and probing twice would launch a second browser and
    could, in principle, report a different build from the one that rendered.

    ``pix_fmt`` is required and has no default, because it is a **comparability
    key** and the caller is the only one who can measure it. It must be the
    format the delivered files actually are — `imageio.delivered_pix_fmt` — and
    not a re-derivation, for the reason an#72 records: the delivered encode
    resolves its format from ``RenderContext.pix_fmt`` *or* the module global,
    so reading either one is a second source of truth that can disagree with
    the file, and this field is what `bench-compare` uses to decide whether two
    encode-side rows may be compared at all.
    """
    from an.adapters.cutout.render import DETERMINISTIC_X264_ARGS

    return {
        "render_side": {
            "playwright": tool_version("playwright"),
            "runtime_sha256": runtime_sha256(),
            **(probe_browser() if browser is None else browser),
            # The verdict's finding, carried as data rather than a comment so a
            # future reader does not have to know it.
            "comparison_scope": "any_machine",
            "comparison_note": (
                "measured ISA- and OS-invariant at a pinned Chromium build: zero "
                "differing pixels across arm64 macOS, x86-64 Linux and arm64 "
                "Linux, across two SwiftShader JIT backends"
            ),
        },
        "encode_side": {
            "isa": platform.machine(),
            "platform": platform.platform(),
            "ffmpeg": ffmpeg_identity(),
            "x264_sei": x264_sei,
            "x264_argv": list(DETERMINISTIC_X264_ARGS),
            # A SEPARATE key rather than folded into `x264_argv`, deliberately.
            # That list is the PINNED TUPLE and always has been — it carries
            # neither `-c:v libx264` nor `-pix_fmt` nor `+faststart` — so
            # folding a per-render knob into it would either make a constant
            # non-constant or turn it into a composed command, and either
            # changes what every already-committed row means.
            #
            # MEASURED off the delivered files by the caller, not re-derived
            # (an#72). This read `DEFAULT_PIX_FMT` under a comment claiming the
            # record and the encode "cannot disagree" — and they can: the mux
            # resolves `ctx.pix_fmt` FIRST, so an `an render --pix-fmt yuv444p`
            # delivery was recorded as 4:2:0. Nothing else in the row could
            # catch it: `x264_argv` deliberately omits `-pix_fmt`, and
            # `x264_sei` is byte-identical across the two formats. So a row
            # would claim 4:2:0, `bench-compare` would accept it against a real
            # 4:2:0 row, and diff metrics across a format change.
            "pix_fmt": pix_fmt,
            "comparison_scope": "machine",
            "comparison_note": (
                "a different x264 build moves the decoded stream by up to 99.2% "
                "of samples; a band wide enough to absorb that would swallow "
                "flat_field_deviation's entire crf18->23 signal, so refuse "
                "rather than widen"
            ),
        },
        "python": sys.version.split()[0],
        "numpy": tool_version("numpy"),
        "an": tool_version("an"),
    }
