"""User-facing utility functions, plus the SSOT list for CLI dispatch.

Each function here is meant to be callable from Python *and* from the shell
via `an tools <funcname>`. Keep their signatures argh-friendly: positional
args become required, defaults become optional flags.
"""

from __future__ import annotations

from pathlib import Path

from an.check_requirements import check_requirements as _check_requirements
from an.check_requirements import format_report
from an.ir.sync import sync as _sync
from an.orchestrate import render_project as _render_project
from an.orchestrate import validate_project
from an.project import init as _init
from an.iterate import iterate as _iterate
from an.preview import preview_project as _preview_project
from an.characters.cli import (
    _dispatch_funcs as _character_dispatch_funcs,
)


def init(project_dir: str, name: str | None = None, force: bool = False) -> str:
    """Create a fresh an project at ``project_dir``.

    project_dir: where to create the project (created if missing)
    name: project display name (defaults to the directory name)
    force: overwrite an existing scene.md
    """
    path = _init(project_dir, name=name, force=force)
    return f"initialized an project at {path}"


def validate(project_dir: str) -> str:
    """Validate the scene at ``project_dir``. Prints findings, exit 0 on pass."""
    report = validate_project(project_dir)
    if report.passed and not report.findings:
        return "validation: passed, no findings"
    lines = ["validation: " + ("passed" if report.passed else "FAILED")]
    for f in report.findings:
        lines.append(f"  [{f.severity}] {f.ir_path}: {f.description}")
    return "\n".join(lines)


def sync(project_dir: str) -> str:
    """Reconcile scene.md and ir/scene.json inside ``project_dir``."""
    result = _sync(project_dir)
    parts = []
    if result.wrote_json:
        parts.append("regenerated ir/scene.json from scene.md")
    if result.wrote_md:
        parts.append("regenerated scene.md from ir/scene.json")
    if result.drift_warning:
        parts.append(f"warning: {result.drift_warning}")
    return "; ".join(parts) if parts else "no changes"


def check() -> str:
    """Print a status report of all backend system + Python deps."""
    return format_report(_check_requirements())


def render(
    project_dir: str,
    output_name: str = "main",
    tts: str = "offline",
    lipsync: str = "offline",
    parallel: str = "",
) -> str:
    """Render the project at ``project_dir`` to a single mp4.

    project_dir: path to an an project (must contain scene.md / ir/scene.json)
    output_name: filename stem under output/ (default: "main")
    tts: TTS provider — "offline" (silent) or "elevenlabs" (needs ELEVEN_API_KEY)
    lipsync: lip-sync provider — "offline" (deterministic), "rhubarb"
        (needs the rhubarb binary), or "whisper" (needs faster-whisper)
    parallel: per-shot concurrency. "" or "1" = serial (default); "auto" =
        min(shots, cpu, 4); a number ≥ 2 caps the thread pool.
    """
    parallel_arg: int | str | None
    if not parallel:
        parallel_arg = None
    elif parallel == "auto":
        parallel_arg = "auto"
    else:
        try:
            parallel_arg = int(parallel)
        except ValueError:
            return f"invalid --parallel value: {parallel!r}; use a number or 'auto'"
    output_path = _render_project(
        project_dir,
        output_name=output_name,
        tts=tts,
        lipsync=lipsync,
        parallel=parallel_arg,
    )
    return f"rendered: {output_path}"


def iterate(
    project_dir: str,
    instruction: str,
    apply_changes: bool = True,
    model: str = "claude-opus-4-7",
) -> str:
    """Apply a free-text instruction to the scene. Needs ANTHROPIC_API_KEY.

    project_dir: path to an an project
    instruction: what to change in plain English (e.g. "make Maya's laugh longer and warmer")
    apply_changes: persist the new scene to disk + invalidate affected shot caches (default True)
    model: Anthropic model id (default claude-opus-4-7)
    """
    result = _iterate(
        project_dir, instruction, apply=apply_changes, model=model
    )
    lines: list[str] = []
    lines.append(f"summary: {result.summary}")
    lines.append(f"affected shots: {', '.join(result.affected_shots) or '(none)'}")
    lines.append(f"patches ({len(result.patches)}):")
    for p in result.patches:
        v = "" if p.op == "delete" else f" = {p.value!r}"
        lines.append(f"  [{p.op}] {p.path}{v}")
    if result.error:
        lines.append(f"ERROR: {result.error}")
        if result.validation:
            for f in result.validation.findings:
                lines.append(f"  [{f.severity}] {f.ir_path}: {f.description}")
    elif apply_changes:
        lines.append("status: applied + persisted; re-run `an render` to see changes")
    else:
        lines.append("status: dry-run (apply_changes=False); nothing persisted")
    return "\n".join(lines)


def preview(
    project_dir: str,
    shot: str = "",
    no_browser: bool = False,
) -> str:
    """Live-preview the project's scene in a browser; reloads on edit.

    Spins up a local HTTP server pointed at the runtime canvas. The
    browser polls ``scene.json`` for changes and re-loads when you save
    ``scene.md``. Lossy: visuals only, no audio. Blocks until Ctrl-C.

    project_dir: path to an an project (must contain scene.md / ir/scene.json)
    shot: shot id to preview (default: first shot in the timeline)
    no_browser: don't auto-open the default browser
    """
    base_url = _preview_project(
        project_dir,
        shot_id=shot or None,
        open_browser=not no_browser,
    )
    return f"preview: stopped (was at {base_url})"


# SSOT for the CLI dispatcher (per the python-dispatching skill convention).
_dispatch_funcs = [init, validate, sync, check, render, iterate, preview]


# Sub-namespaces. ``__main__`` mounts these via argh's ``namespace=`` arg so
# the CLI looks like ``an character new <name> ...``.
_dispatch_namespaces: dict[str, list] = {
    "character": _character_dispatch_funcs,
}
