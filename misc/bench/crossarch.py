"""Cross-architecture determinism capture and comparison (an#31).

Wave 2's first task. Renders a fixed set of fixtures through ``an``'s real
render path, then records **``sha256`` of the decoded RGBA array** of every
frame — never of the PNG file bytes. That distinction is the whole point:
Chromium 1187 -> 1223 changes 144/144 PNG files and **zero** pixels, so a
file-byte assertion goes red on the first Playwright bump for a reason
unrelated to animation quality (``misc/docs/wave2_research.md`` §2).

Two subcommands::

    python misc/bench/crossarch.py capture --out <dir>
    python misc/bench/crossarch.py compare <dir-a> <dir-b>

``capture`` writes ``<dir>/manifest.json`` plus every frame PNG, so a
difference can be *measured* (how many pixels, how far off) rather than only
detected. ``compare`` reads two such directories and reports, per scene, the
differing-frame count, the differing-pixel count and the maximum per-channel
delta.

Fixtures cover both render paths deliberately: the descriptor (SVG-sprite)
path is 12x more sensitive to a rasteriser flip than the procedural path
(2.94% vs 0.24% of pixels under GPU-vs-software), so a procedural-only capture
would under-report.

Rendering happens in a **copy** of the fixture: ``an``'s render path mutates
the project directory (scene mtimes, the decisions log, ``.an/render_work``),
so rendering in place would make the recorded git sha a lie.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Iterator

# Tunables — module constants per the no-magic-numbers rule.
MANIFEST_NAME: str = "manifest.json"
FRAMES_DIRNAME: str = "frames"
#: The name of the compiled scene the browser actually loaded, staged per shot.
STAGED_SCENE_NAME: str = "scene.json"


def _prepare_promote_demo(project_dir: Path) -> None:
    """Regenerate the promoted character from the committed source SVG.

    `examples/promote_demo` ships only `raw_maya.svg`; the promoted character it
    references is a build product and is gitignored. Without this step the
    fixture renders on a clean checkout, produces no error, and quietly draws a
    **different character** — see `expect_visual_kinds` below.
    """
    from an.characters import promote

    promote(project_dir, entity="raw_maya", as_="maya-promoted", overwrite=True)


@dataclass(frozen=True, slots=True)
class Fixture:
    """A capture fixture: where it lives, how to build it, what it must render."""

    path: str
    #: Run against the throwaway copy before loading, to regenerate build
    #: products the repo does not track.
    prepare: Any = None
    #: Visual kinds the staged scene MUST contain. This is not belt-and-braces:
    #: a missing character descriptor makes the compiler fall back to the
    #: procedural rig with **no warning** (verified: zero warnings, and
    #: `svg_sprite` simply absent from the compiled scene). The first run of this
    #: experiment measured that fallback on three CI runners and would have
    #: reported "the descriptor path is deterministic" having never rendered it.
    expect_visual_kinds: frozenset = frozenset()


#: The fixtures, one per render path — deliberately both, because they are not
#: equally sensitive: the descriptor path is 12x more sensitive to a rasteriser
#: flip than the procedural one (2.94% vs 0.24% of pixels under GPU-vs-software),
#: so a procedural-only capture under-reports the case that matters.
DFLT_FIXTURES: dict[str, Fixture] = {
    # Procedural rig, 320x240, 2.5 s -> 60 frames. Needs no assets at all, which
    # is the only reason it is reproducible from a clean checkout today.
    "single_character": Fixture(
        path="examples/single_character",
        expect_visual_kinds=frozenset({"rect", "ellipse"}),
    ),
    # SVG-sprite descriptor (the sensitive path), 480x360, 3.0 s -> 72 frames.
    "promote_demo": Fixture(
        path="examples/promote_demo",
        prepare=_prepare_promote_demo,
        expect_visual_kinds=frozenset({"svg_sprite"}),
    ),
}
#: Rendering knobs pinned for the capture. Audio is off because it cannot move
#: a pixel and would otherwise make the frames depend on the audio cache's
#: warm/cold state; ``parallel=1`` because a timing-sensitive pool is one more
#: thing to explain if the pixels ever do differ.
CAPTURE_RENDER_KWARGS: dict[str, Any] = {"auto_audio": False, "parallel": 1}


@dataclass(frozen=True, slots=True)
class FrameRecord:
    """One captured frame: where it came from, and two independent digests."""

    key: str
    pixels_sha256: str
    png_sha256: str
    shape: tuple[int, ...]
    mode: str
    png_bytes: int


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def decode_png(path: Path) -> Any:
    """Decode ``path`` to a canonical RGBA ``numpy`` array.

    Canonicalising the mode matters: a capture that produced RGB and one that
    produced RGBA would otherwise compare as "different" for a reason that is
    not a pixel difference. The original mode is recorded separately so that
    difference is still visible.
    """
    import numpy as np
    from PIL import Image

    with Image.open(path) as im:
        mode = im.mode
        arr = np.asarray(im.convert("RGBA"))
    return arr, mode


def _frame_record(path: Path, *, key: str) -> FrameRecord:
    raw = path.read_bytes()
    arr, mode = decode_png(path)
    return FrameRecord(
        key=key,
        pixels_sha256=_sha256_bytes(arr.tobytes()),
        png_sha256=_sha256_bytes(raw),
        shape=tuple(int(x) for x in arr.shape),
        mode=mode,
        png_bytes=len(raw),
    )


def _iter_shot_frame_dirs(work_dir: Path) -> Iterator[tuple[str, Path]]:
    """Yield ``(shot_id, frames_dir)`` for every rendered shot, in sorted order."""
    for shot_dir in sorted(work_dir.glob("shot_*")):
        frames = shot_dir / FRAMES_DIRNAME
        if frames.is_dir():
            yield shot_dir.name[len("shot_") :], frames


def probe_browser_environment() -> dict[str, Any]:
    """Launch Chromium with the render path's own flags and read back its identity.

    Never raises: a probe that crashes must not cost a caller a completed
    capture, and a recorded ``error`` is more honest than a missing field that
    reads as "nothing to report". The launch argv is recorded **verbatim**
    because the WebGL renderer string is demonstrably blind to the
    software-rasteriser flag flip (research §2).
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
                webgl = page.evaluate(probe_js)
                return {
                    "launch_args": args,
                    "browser_version": browser.version,
                    "executable_path": str(p.chromium.executable_path),
                    "webgl": webgl,
                }
            finally:
                browser.close()
    except Exception as e:  # noqa: BLE001 - reported, never fatal
        return {"error": f"{type(e).__name__}: {e}"}


def _tool_version(name: str) -> str | None:
    from importlib.metadata import PackageNotFoundError, version

    try:
        return version(name)
    except PackageNotFoundError:
        return None


def _ffmpeg_identity() -> dict[str, Any]:
    """The ffmpeg build banner — informational, and the thing that will explain
    a future decoded-pixel change nobody predicted."""
    exe = shutil.which("ffmpeg")
    if exe is None:
        return {"error": "ffmpeg not on PATH"}
    try:
        out = subprocess.run(
            [exe, "-version"], capture_output=True, text=True, check=False
        )
    except OSError as e:
        return {"error": f"{type(e).__name__}: {e}"}
    return {"path": exe, "banner": out.stdout.splitlines()[0] if out.stdout else ""}


def _git_state(repo_root: Path) -> dict[str, Any]:
    def _git(*args: str) -> str | None:
        try:
            out = subprocess.run(
                ["git", *args],
                cwd=repo_root,
                capture_output=True,
                text=True,
                check=False,
            )
        except OSError:
            return None
        return out.stdout.strip() if out.returncode == 0 else None

    status = _git("status", "--porcelain")
    return {
        "sha": _git("rev-parse", "HEAD"),
        "branch": _git("rev-parse", "--abbrev-ref", "HEAD"),
        "dirty": bool(status) if status is not None else None,
    }


def environment_record(repo_root: Path) -> dict[str, Any]:
    """Everything about this machine that could plausibly move a pixel."""
    return {
        "platform": platform.platform(),
        "system": platform.system(),
        "machine": platform.machine(),
        "processor": platform.processor(),
        "python": sys.version.split()[0],
        "playwright": _tool_version("playwright"),
        "pillow": _tool_version("pillow"),
        "numpy": _tool_version("numpy"),
        "ffmpeg": _ffmpeg_identity(),
        "browser": probe_browser_environment(),
        "git": _git_state(repo_root),
    }


def _staged_visual_kinds(work_dir: Path) -> set[str]:
    """Every `kind` in the scene JSON the browser actually loaded.

    Read from the staged file rather than re-compiled, so it reports what was
    rendered rather than what a second compile would produce.
    """
    kinds: set[str] = set()

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            kind = node.get("kind")
            if isinstance(kind, str):
                kinds.add(kind)
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    for shot_dir in sorted(work_dir.glob("shot_*")):
        staged = shot_dir / "runtime" / STAGED_SCENE_NAME
        if staged.is_file():
            walk(json.loads(staged.read_text(encoding="utf-8")))
    return kinds


def capture_scene(
    name: str,
    fixture: Fixture,
    *,
    repo_root: Path,
    out_dir: Path,
) -> dict[str, Any]:
    """Render one fixture in a throwaway copy and record every frame."""
    from an.project import load
    from an.render import render

    fixture_dir = repo_root / fixture.path
    if not fixture_dir.is_dir():
        raise FileNotFoundError(f"fixture {name!r} not found at {fixture_dir}")

    scene_out = out_dir / name / FRAMES_DIRNAME
    scene_out.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix=f"an-crossarch-{name}-") as tmp:
        work_copy = Path(tmp) / fixture_dir.name
        shutil.copytree(fixture_dir, work_copy)
        if fixture.prepare is not None:
            fixture.prepare(work_copy)
        project = load(work_copy)
        scene = project.scene
        render(project, **CAPTURE_RENDER_KWARGS)

        render_work_dir = work_copy / ".an" / "render_work"
        visual_kinds = _staged_visual_kinds(render_work_dir)
        missing = fixture.expect_visual_kinds - visual_kinds
        if missing:
            raise RuntimeError(
                f"fixture {name!r} rendered WITHOUT {sorted(missing)} — it "
                f"staged {sorted(visual_kinds)} instead. A missing character "
                f"descriptor makes the compiler fall back to the procedural rig "
                f"silently, so this capture would have measured a different "
                f"picture and called it the same render path."
            )

        shots: list[dict[str, Any]] = []
        frames: list[FrameRecord] = []
        for shot_id, frames_dir in _iter_shot_frame_dirs(render_work_dir):
            pngs = sorted(frames_dir.glob("frame_*.png"))
            shots.append({"id": shot_id, "frames": len(pngs)})
            for png in pngs:
                key = f"{shot_id}/{png.stem}"
                frames.append(_frame_record(png, key=key))
                dest = scene_out / f"{shot_id}__{png.name}"
                shutil.copy2(png, dest)

    if not frames:
        raise RuntimeError(
            f"fixture {name!r} produced no frames — "
            f"looked under {render_work_dir} for shot_*/{FRAMES_DIRNAME}/frame_*.png"
        )

    return {
        "source": fixture.path,
        "prepared": fixture.prepare is not None,
        # Which render path this actually exercised — the fact whose absence
        # made the first run of this experiment measure the wrong thing.
        "visual_kinds": sorted(visual_kinds),
        "resolution": [scene.meta.resolution.width, scene.meta.resolution.height],
        "fps": scene.meta.fps,
        "shots": shots,
        "frame_count": len(frames),
        # One digest over the per-frame pixel digests: the scene-level answer to
        # "did anything move", cheap to eyeball in a diff.
        "pixels_sha256": _sha256_bytes(
            "".join(f.pixels_sha256 for f in frames).encode()
        ),
        "frames": [
            {
                "key": f.key,
                "pixels_sha256": f.pixels_sha256,
                "png_sha256": f.png_sha256,
                "shape": list(f.shape),
                "mode": f.mode,
                "png_bytes": f.png_bytes,
            }
            for f in frames
        ],
    }


def capture(
    out_dir: str | Path,
    *,
    fixtures: dict[str, Fixture] | None = None,
    repo_root: str | Path | None = None,
) -> Path:
    """Capture every fixture into ``out_dir`` and write the manifest."""
    root = Path(repo_root) if repo_root else Path(__file__).resolve().parents[2]
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    chosen = fixtures if fixtures is not None else DFLT_FIXTURES

    scenes: dict[str, Any] = {}
    for name, fixture in chosen.items():
        print(f"[crossarch] capturing {name} from {fixture.path} ...", flush=True)
        scenes[name] = capture_scene(name, fixture, repo_root=root, out_dir=out)
        print(
            f"[crossarch]   {scenes[name]['frame_count']} frames, "
            f"visuals={','.join(scenes[name]['visual_kinds'])}, "
            f"pixels_sha256={scenes[name]['pixels_sha256'][:16]}",
            flush=True,
        )

    manifest = {
        "tool": "misc/bench/crossarch.py",
        "environment": environment_record(root),
        "render_kwargs": CAPTURE_RENDER_KWARGS,
        "scenes": scenes,
    }
    manifest_path = out / MANIFEST_NAME
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(f"[crossarch] wrote {manifest_path}", flush=True)
    return manifest_path


def _compare_frame_pixels(a_png: Path, b_png: Path) -> dict[str, Any]:
    """Quantify a difference: how many pixels, and how far off."""
    import numpy as np

    a, _ = decode_png(a_png)
    b, _ = decode_png(b_png)
    if a.shape != b.shape:
        return {"shape_a": list(a.shape), "shape_b": list(b.shape)}
    delta = np.abs(a.astype(np.int16) - b.astype(np.int16))
    differing = int(np.count_nonzero(np.any(delta != 0, axis=-1)))
    return {
        "differing_pixels": differing,
        "total_pixels": int(a.shape[0] * a.shape[1]),
        "max_channel_delta": int(delta.max()),
    }


def _frame_png(root: Path, scene: str, key: str) -> Path:
    shot_id, stem = key.split("/", 1)
    return root / scene / FRAMES_DIRNAME / f"{shot_id}__{stem}.png"


def compare(a_dir: str | Path, b_dir: str | Path) -> dict[str, Any]:
    """Compare two capture directories and return a structured verdict."""
    a_root, b_root = Path(a_dir), Path(b_dir)
    a = json.loads((a_root / MANIFEST_NAME).read_text(encoding="utf-8"))
    b = json.loads((b_root / MANIFEST_NAME).read_text(encoding="utf-8"))

    report: dict[str, Any] = {
        "a": {"dir": str(a_root), "environment": a["environment"]},
        "b": {"dir": str(b_root), "environment": b["environment"]},
        "scenes": {},
    }
    for scene in sorted(set(a["scenes"]) | set(b["scenes"])):
        if scene not in a["scenes"] or scene not in b["scenes"]:
            report["scenes"][scene] = {
                "missing_from": "a" if scene not in a["scenes"] else "b"
            }
            continue
        sa, sb = a["scenes"][scene], b["scenes"][scene]
        fa = {f["key"]: f for f in sa["frames"]}
        fb = {f["key"]: f for f in sb["frames"]}
        keys = sorted(set(fa) | set(fb))
        pixel_diffs: list[dict[str, Any]] = []
        png_byte_diffs = 0
        for key in keys:
            if key not in fa or key not in fb:
                pixel_diffs.append(
                    {"key": key, "missing_from": "a" if key not in fa else "b"}
                )
                continue
            if fa[key]["png_sha256"] != fb[key]["png_sha256"]:
                png_byte_diffs += 1
            if fa[key]["pixels_sha256"] != fb[key]["pixels_sha256"]:
                detail = _compare_frame_pixels(
                    _frame_png(a_root, scene, key), _frame_png(b_root, scene, key)
                )
                pixel_diffs.append({"key": key, **detail})
        report["scenes"][scene] = {
            "frames": len(keys),
            "pixels_identical": not pixel_diffs,
            "frames_with_differing_pixels": len(pixel_diffs),
            "frames_with_differing_png_bytes": png_byte_diffs,
            "worst": max(
                (d for d in pixel_diffs if "max_channel_delta" in d),
                key=lambda d: (d["max_channel_delta"], d["differing_pixels"]),
                default=None,
            ),
            "max_differing_pixels": max(
                (d.get("differing_pixels", 0) for d in pixel_diffs), default=0
            ),
            "max_channel_delta": max(
                (d.get("max_channel_delta", 0) for d in pixel_diffs), default=0
            ),
            "differing_frames": pixel_diffs,
        }
    report["verdict"] = (
        "IDENTICAL"
        if all(s.get("pixels_identical") for s in report["scenes"].values())
        else "DIFFERS"
    )
    return report


def format_report(report: dict[str, Any]) -> str:
    """Render a comparison report as the paragraph a human actually reads."""
    lines: list[str] = []
    for side in ("a", "b"):
        env = report[side]["environment"]
        browser = env.get("browser", {})
        webgl = (browser.get("webgl") or {}) if isinstance(browser, dict) else {}
        lines.append(
            f"{side}: {report[side]['dir']}\n"
            f"   {env['system']} {env['machine']} · python {env['python']} · "
            f"playwright {env['playwright']}\n"
            f"   browser {browser.get('browser_version', '?')} · "
            f"webgl {webgl.get('renderer', '?')} · samples {webgl.get('samples', '?')}\n"
            f"   {env.get('ffmpeg', {}).get('banner', '?')}"
        )
    lines.append("")
    for scene, s in report["scenes"].items():
        if "missing_from" in s:
            lines.append(f"{scene}: MISSING from side {s['missing_from']}")
            continue
        state = "identical" if s["pixels_identical"] else "DIFFERS"
        lines.append(
            f"{scene}: {state} — {s['frames_with_differing_pixels']}/{s['frames']} frames "
            f"differ in pixels, {s['frames_with_differing_png_bytes']}/{s['frames']} in PNG bytes; "
            f"worst frame {s['max_differing_pixels']} px, max channel delta {s['max_channel_delta']}"
        )
    lines.append("")
    lines.append(f"VERDICT: {report['verdict']}")
    return "\n".join(lines)


def _main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="cmd", required=True)

    cap = sub.add_parser("capture", help="render the fixtures and record frame digests")
    cap.add_argument("--out", required=True, help="output directory for the capture")

    cmp_ = sub.add_parser("compare", help="compare two capture directories")
    cmp_.add_argument("a")
    cmp_.add_argument("b")
    cmp_.add_argument(
        "--json", dest="json_out", default=None, help="also write JSON here"
    )

    args = parser.parse_args(list(argv) if argv is not None else None)
    if args.cmd == "capture":
        capture(args.out)
        return 0
    report = compare(args.a, args.b)
    print(format_report(report))
    if args.json_out:
        Path(args.json_out).write_text(
            json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
    return 0 if report["verdict"] == "IDENTICAL" else 1


if __name__ == "__main__":
    raise SystemExit(_main())
