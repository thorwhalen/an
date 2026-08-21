"""Diagnose system + Python deps that the various backends need.

Runtime-friendly: never raises; always returns structured per-tool status so
the CLI (or a calling script) can decide what to do.

>>> result = check_requirements()
>>> isinstance(result, dict) and 'ffmpeg' in result
True
"""

from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass


@dataclass(slots=True)
class ToolStatus:
    """Status of a single tool."""

    name: str
    installed: bool
    version: str | None = None
    install_hint: str | None = None
    detail: str | None = None


def _which(name: str) -> str | None:
    return shutil.which(name)


def _run_version(cmd: list[str], *, timeout: float = 5.0) -> str | None:
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout, check=False
        )
        out = (result.stdout or result.stderr or "").strip()
        # Take the first line; many tools print multi-line --version output.
        return out.splitlines()[0] if out else None
    except (OSError, subprocess.TimeoutExpired):
        return None


def _check_ffmpeg() -> ToolStatus:
    if _which("ffmpeg") is None:
        return ToolStatus(
            name="ffmpeg",
            installed=False,
            install_hint="brew install ffmpeg  # macOS, or apt install ffmpeg",
        )
    return ToolStatus(
        name="ffmpeg", installed=True, version=_run_version(["ffmpeg", "-version"])
    )


def _check_node() -> ToolStatus:
    if _which("node") is None:
        return ToolStatus(
            name="node",
            installed=False,
            install_hint="brew install node  # required for Remotion + cutout JS runtime",
        )
    return ToolStatus(
        name="node", installed=True, version=_run_version(["node", "--version"])
    )


def _check_rhubarb() -> ToolStatus:
    if _which("rhubarb") is None:
        return ToolStatus(
            name="rhubarb",
            installed=False,
            install_hint=(
                "brew install rhubarb-lipsync  # macOS; "
                "or download from https://github.com/DanielSWolf/rhubarb-lip-sync/releases"
            ),
        )
    return ToolStatus(
        name="rhubarb", installed=True, version=_run_version(["rhubarb", "--version"])
    )


def _check_python_pkg(pkg: str, install_hint: str) -> ToolStatus:
    spec = importlib.util.find_spec(pkg)
    if spec is None:
        return ToolStatus(name=pkg, installed=False, install_hint=install_hint)
    try:
        mod = importlib.import_module(pkg)
        version = getattr(mod, "__version__", None)
    except Exception as e:
        return ToolStatus(
            name=pkg,
            installed=False,
            install_hint=install_hint,
            detail=f"import failed: {e!r}",
        )
    return ToolStatus(name=pkg, installed=True, version=version)


#: Playwright's per-platform default browser cache, keyed by ``sys.platform``.
#:
#: Checking only the macOS path was a macOS-only bug that mattered the moment CI
#: first launched a browser (an#37): on Linux it reported "Chromium not
#: installed" on a machine where it was, i.e. an instruction to run a command
#: that would change nothing. ``PLAYWRIGHT_BROWSERS_PATH`` overrides all of
#: them, and Playwright reads it the same way.
PLAYWRIGHT_CACHE_BY_PLATFORM: dict[str, str] = {
    "darwin": "~/Library/Caches/ms-playwright",
    "linux": "~/.cache/ms-playwright",
    "win32": "~/AppData/Local/ms-playwright",
}


def playwright_browser_dirs(*, platform: str | None = None) -> list[str]:
    """Where Playwright's browser binaries could live on this machine.

    A list rather than one path, and all of them are checked, because
    ``sys.platform`` is not always the whole story (WSL, a Linux venv on a
    macOS-mounted home). Checking a directory that does not exist costs an
    ``os.path.isdir``. ``platform`` is injectable so the per-OS behaviour is
    testable without monkeypatching ``sys``.

    >>> playwright_browser_dirs(platform="linux")[0].endswith(".cache/ms-playwright")
    True
    """
    override = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
    if override:
        return [os.path.expanduser(override)]
    preferred = PLAYWRIGHT_CACHE_BY_PLATFORM.get(platform or sys.platform)
    ordered = ([preferred] if preferred else []) + [
        p for p in PLAYWRIGHT_CACHE_BY_PLATFORM.values() if p != preferred
    ]
    return [os.path.expanduser(p) for p in ordered]


def _check_playwright() -> ToolStatus:
    py_status = _check_python_pkg(
        "playwright",
        "pip install playwright && playwright install chromium",
    )
    if not py_status.installed:
        return py_status
    # Bonus: check whether the Chromium browser binary is installed.
    has_chromium = any(
        name.startswith("chromium")
        for directory in playwright_browser_dirs()
        for name in (os.listdir(directory) if os.path.isdir(directory) else [])
    )
    if not has_chromium:
        py_status.detail = "playwright pkg installed but Chromium not; run: playwright install chromium"
    return py_status


def _check_elevenlabs() -> ToolStatus:
    status = _check_python_pkg(
        "elevenlabs",
        "pip install elevenlabs  # also set ELEVEN_API_KEY env var",
    )
    if status.installed and not (
        os.environ.get("ELEVEN_API_KEY") or os.environ.get("ELEVENLABS_API_KEY")
    ):
        status.detail = "package installed but ELEVEN_API_KEY env var is not set"
    return status


def _check_manim() -> ToolStatus:
    return _check_python_pkg(
        "manim", "pip install manim  # plus cairo/pango system deps"
    )


def check_requirements() -> dict[str, dict]:
    """Return a per-tool status dict.

    The CLI subcommand ``an check`` pretty-prints this. Programmatic callers
    can inspect the dict directly.
    """
    checks: list[ToolStatus] = [
        _check_ffmpeg(),
        _check_node(),
        _check_rhubarb(),
        _check_playwright(),
        _check_elevenlabs(),
        _check_manim(),
    ]
    return {c.name: asdict(c) for c in checks}


def format_report(report: dict[str, dict]) -> str:
    """Pretty-print a check_requirements() result."""
    lines: list[str] = []
    for name, info in report.items():
        mark = "[OK]" if info["installed"] else "[--]"
        line = f"{mark} {name}"
        if info.get("version"):
            line += f"  {info['version']}"
        lines.append(line)
        if info.get("detail"):
            lines.append(f"     note: {info['detail']}")
        if not info["installed"] and info.get("install_hint"):
            lines.append(f"     install: {info['install_hint']}")
    return "\n".join(lines)
