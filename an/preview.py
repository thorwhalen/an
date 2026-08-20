"""Live preview server: render a project's current scene in a browser, reloading on edit.

The :func:`preview_project` function spins up a local HTTP server pointed at
a freshly-compiled cutout scene JSON, then watches ``scene.md`` /
``ir/scene.json`` for changes and recompiles. The browser polls
``scene.json`` every ~500 ms (HEAD request, ``Last-Modified`` header) and
re-calls ``window.anLoadScene`` whenever the file changes.

Lossy by design: shows the runtime canvas only — no audio mux, no final
mp4. Use ``an render`` once you're happy with the look.

>>> from an.preview import _stage_preview
>>> callable(_stage_preview)
True
"""

from __future__ import annotations

import json
import shutil
import threading
import time
import webbrowser
from dataclasses import dataclass
from pathlib import Path

from an.adapters.cutout.compile import compile_shot
from an.adapters.cutout.render import _serve_dir, _stage_scene_assets
from an.adapters.cutout.runtime_files import runtime_dir
from an.adapters.cutout.serialize import to_dict
from an.base import DEFAULT_FPS, DEFAULT_RESOLUTION
from an.project import load


# Tunables (no magic numbers per the project conventions).
DEFAULT_POLL_INTERVAL_S: float = 0.5
PREVIEW_HTML_NAME: str = "preview.html"
PREVIEW_WORK_SUBDIR: tuple[str, str] = (".an", "preview")


class PreviewError(RuntimeError):
    """Raised when a preview cannot be staged or served."""


@dataclass(slots=True)
class PreviewStaging:
    """Outcome of staging a preview's runtime + initial compiled scene."""

    runtime_dir: Path
    scene_json_path: Path
    shot_id: str


def _stage_preview(
    project_dir: str | Path,
    *,
    shot_id: str | None = None,
) -> PreviewStaging:
    """Stage the runtime files + compiled scene JSON under ``<dir>/.an/preview/runtime``.

    Pure side-effect-on-disk: copies the cutout JS runtime, compiles the
    chosen shot, writes ``scene.json``, and stages any SVG character
    textures referenced by the scene. Does not start a server.
    """
    project_root = Path(project_dir).expanduser().resolve()
    if not project_root.exists():
        raise PreviewError(f"no such project directory: {project_root}")

    work_dir = project_root.joinpath(*PREVIEW_WORK_SUBDIR)
    runtime_target = work_dir / "runtime"
    work_dir.mkdir(parents=True, exist_ok=True)
    if runtime_target.exists():
        shutil.rmtree(runtime_target)
    shutil.copytree(runtime_dir(), runtime_target)

    chosen_id = _compile_scene_to(project_root, runtime_target, shot_id=shot_id)
    return PreviewStaging(
        runtime_dir=runtime_target,
        scene_json_path=runtime_target / "scene.json",
        shot_id=chosen_id,
    )


def preview_project(
    project_dir: str | Path,
    *,
    shot_id: str | None = None,
    open_browser: bool = True,
    poll_interval_s: float = DEFAULT_POLL_INTERVAL_S,
) -> str:
    """Serve a live preview of ``project_dir`` from a local HTTP server.

    Compiles the chosen shot (default: first shot in the timeline) to its
    runtime JSON, stages the cutout JS runtime + any SVG character
    textures, and serves the result on a free port. A daemon watcher
    thread polls ``scene.md`` / ``ir/scene.json`` mtimes and recompiles
    when either changes; the browser sees the new compiled scene via its
    own ~500 ms HEAD-request poll.

    Blocks the calling thread until interrupted (Ctrl-C). Returns the
    base URL after teardown.
    """
    staging = _stage_preview(project_dir, shot_id=shot_id)
    project_root = Path(project_dir).expanduser().resolve()

    stop_event = threading.Event()
    watcher = threading.Thread(
        target=_watch_loop,
        args=(
            project_root,
            staging.runtime_dir,
            shot_id,
            poll_interval_s,
            stop_event,
        ),
        daemon=True,
    )
    watcher.start()

    print(f"preview: shot={staging.shot_id} → {staging.runtime_dir}")
    with _serve_dir(staging.runtime_dir) as base_url:
        url = f"{base_url}/{PREVIEW_HTML_NAME}"
        print(f"preview: serving at {url}")
        print("  Ctrl-C to stop. Edit scene.md and the browser will reload.")
        if open_browser:
            webbrowser.open(url)
        try:
            while True:
                time.sleep(0.5)
        except KeyboardInterrupt:
            print("preview: stopping…")
        finally:
            stop_event.set()
    return base_url


# -----------------------------------------------------------------------------
# Internals
# -----------------------------------------------------------------------------


def _compile_scene_to(
    project_root: Path,
    runtime_target: Path,
    *,
    shot_id: str | None,
) -> str:
    """Compile the chosen shot to ``runtime_target/scene.json``.

    Re-syncs scene.md ↔ ir/scene.json via :func:`an.project.load` so the
    Markdown is always the source of truth. Returns the compiled shot's id.
    """
    project = load(project_root)
    scene = project.scene
    if not scene.timeline:
        raise PreviewError(
            "scene has no shots — add at least one shot to scene.md and save"
        )

    if shot_id is not None:
        matches = [s for s in scene.timeline if s.id == shot_id]
        if not matches:
            ids = ", ".join(s.id for s in scene.timeline)
            raise PreviewError(f"no shot with id={shot_id!r} (have: {ids})")
        shot = matches[0]
    else:
        shot = scene.timeline[0]

    if shot.style != "cutout":
        raise PreviewError(
            f"shot {shot.id!r} has style={shot.style!r}; live preview supports "
            f"'cutout' only. Render this shot via `an render` instead."
        )

    fps = scene.meta.fps or DEFAULT_FPS
    width = scene.meta.resolution.width or DEFAULT_RESOLUTION[0]
    height = scene.meta.resolution.height or DEFAULT_RESOLUTION[1]

    scene_json = compile_shot(
        shot, mall=project.mall, fps=fps, width=width, height=height
    )
    _stage_scene_assets(scene_json, project.mall, runtime_target)

    out = runtime_target / "scene.json"
    # Atomic-ish write so the HTTP server never serves a half-written file.
    tmp = out.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(to_dict(scene_json), sort_keys=True), encoding="utf-8")
    tmp.replace(out)
    return shot.id


def _source_mtimes(project_root: Path) -> tuple[float, float]:
    """``(scene.md mtime, ir/scene.json mtime)``; missing files report 0.0."""
    md = project_root / "scene.md"
    js = project_root / "ir" / "scene.json"
    md_t = md.stat().st_mtime if md.exists() else 0.0
    js_t = js.stat().st_mtime if js.exists() else 0.0
    return md_t, js_t


def _watch_loop(
    project_root: Path,
    runtime_target: Path,
    shot_id: str | None,
    poll_interval_s: float,
    stop_event: threading.Event,
) -> None:
    """Poll source files and recompile when either changes.

    Re-reads mtimes after each compile because :func:`an.project.load` may
    rewrite ``ir/scene.json`` during sync — without re-reading we'd see
    our own write as a change and loop forever.
    """
    last = _source_mtimes(project_root)
    while not stop_event.is_set():
        # Use stop_event.wait so Ctrl-C tears down quickly.
        if stop_event.wait(poll_interval_s):
            return
        cur = _source_mtimes(project_root)
        if cur == last:
            continue
        try:
            _compile_scene_to(project_root, runtime_target, shot_id=shot_id)
            print(f"preview: reloaded ({time.strftime('%H:%M:%S')})")
        except Exception as e:
            print(f"preview: recompile failed — {e}")
        last = _source_mtimes(project_root)
