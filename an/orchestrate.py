"""Orchestrator entry points.

The full orchestration flow — interview → IR draft → validate → audio →
render → verify → iterate — lands in Phase 5. Today (Phase 2D) we have:

- ``validate_project`` (Phase 1)
- ``render_project`` (Phase 2D — wires through an.render)
- ``iterate`` (still a stub; Phase 5)
"""

from __future__ import annotations

from pathlib import Path

from an.ir.schema import SceneIR
from an.ir.validate import (
    ValidationReport,
    validate_schema,
    validate_semantic,
)
from an.project import Project, load
from an.render import render_project as _render_project


def validate_project(project_dir: str | Path) -> ValidationReport:
    """Schema + semantic validation of the scene at ``project_dir``."""
    project: Project = load(project_dir)
    schema_report = validate_schema(project.scene)
    semantic_report = validate_semantic(
        project.scene,
        available_voices=project.mall.get("voices"),
        available_characters=project.mall.get("characters"),
    )
    return schema_report.merge(semantic_report)


def render_project(project_dir: str | Path, *, output_name: str = "main") -> Path:
    """Render the project's scene to a single mp4 under ``output/``."""
    return _render_project(project_dir, output_name=output_name)


def iterate(project_dir: str | Path, instruction: str) -> SceneIR:
    """Apply a free-text edit instruction. Phase-1 stub: not yet implemented."""
    raise NotImplementedError(
        "Iterative edit loop arrives in Phase 5 along with the orchestrator skill."
    )
