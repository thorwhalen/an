"""HumanInTheLoopVerifier — opens the rendered mp4 and asks for approval.

Only useful in interactive sessions. Skips silently when there's no TTY (CI,
agent contexts). The orchestrator can detect the skip via the report's
informational finding.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from an.adapters._base import RenderResult
from an.ir.schema import SceneIR
from an.verify._base import VerificationReport


class HumanInTheLoopVerifier:
    """Open the mp4, prompt the user to approve. Implements ``Verifier``."""

    name: str = "human"

    def __init__(self, *, prompt: str = "Approve render? [y/N/r=reject]: ") -> None:
        self.prompt = prompt

    def verify(self, ir: SceneIR, render: RenderResult | None) -> VerificationReport:
        report = VerificationReport()
        if render is None:
            report.add(
                "info", "<render>", "no render result; nothing to inspect"
            )
            return report
        if not sys.stdin.isatty() or not sys.stdout.isatty():
            report.add(
                "info", "<terminal>",
                "no interactive terminal — skipping human review",
            )
            return report

        _open_in_default_app(render.mp4_path)
        try:
            answer = input(self.prompt).strip().lower()
        except (EOFError, KeyboardInterrupt):
            report.add(
                "info", "<input>", "no answer received; defaulting to skip"
            )
            return report

        if answer.startswith("r"):
            report.add(
                "error", "<human>",
                "human rejected the render",
                suggested_fix="ask the human what to change",
            )
        elif answer.startswith("y"):
            report.add("info", "<human>", "human approved the render")
        else:
            report.add(
                "warning", "<human>",
                "human did not explicitly approve",
            )
        return report


def _open_in_default_app(path: Path) -> None:
    """Open ``path`` in the OS's default application for its file type."""
    p = str(path)
    try:
        if sys.platform == "darwin":
            subprocess.Popen(["open", p])
        elif sys.platform.startswith("linux"):
            subprocess.Popen(["xdg-open", p])
        elif sys.platform.startswith("win"):
            import os
            os.startfile(p)  # type: ignore[attr-defined]
    except Exception:
        # Best-effort; if the launcher is missing, the user can open it manually.
        pass
