"""Orchestrator: validate → audio → render → verify.

Phase 5 ships ``orchestrate(project_dir, ...)`` — the high-level flow that
ties together validation, audio synthesis, rendering, and verification.
The full iterative edit loop (free-text "make Maya's laugh longer" →
re-render only the affected shot) lives in the ``an`` skill, which calls
into these primitives.

>>> from an.orchestrate import OrchestratorReport
>>> r = OrchestratorReport()
>>> r.success
True
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence

from an.ir.schema import SceneIR
from an.ir.validate import (
    ValidationReport,
    validate_schema,
    validate_semantic,
)
from an.project import Project, load
from an.render import render_project as _render_project
from an.verify._base import Verifier, VerificationReport
from an.verify.layout import LayoutLintVerifier
from an.verify.media_quality import MediaQualityVerifier


@dataclass(slots=True)
class OrchestratorReport:
    """Outcome of an end-to-end orchestrated run."""

    success: bool = True
    output_path: Path | None = None
    validation: ValidationReport | None = None
    verifications: list[VerificationReport] = field(default_factory=list)
    error: str | None = None

    def merge_verification(self, vr: VerificationReport) -> None:
        self.verifications.append(vr)
        if not vr.passed:
            self.success = False


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


def render_project(
    project_dir: str | Path,
    *,
    output_name: str = "main",
    tts: str = "offline",
    lipsync: str = "offline",
    parallel: int | str | None = None,
) -> Path:
    """Render the project's scene to a single mp4 under ``output/``."""
    return _render_project(
        project_dir,
        output_name=output_name,
        tts=tts,
        lipsync=lipsync,
        parallel=parallel,
    )


def orchestrate(
    project_dir: str | Path,
    *,
    output_name: str = "main",
    verifiers: Sequence[Verifier] | None = None,
    skip_render: bool = False,
    tts: str | object = "offline",
    lipsync: str | object = "offline",
    parallel: int | str | None = None,
) -> OrchestratorReport:
    """Run the full pipeline. Returns a structured outcome.

    Phases:
      1. Validate (schema + semantic). Hard fail if the schema is broken.
      2. Pre-render verifiers (any that accept ``render=None``).
      3. Render (audio is auto-run inside `render` when needed).
      4. Post-render verifiers.

    ``verifiers`` defaults to ``[LayoutLintVerifier()]``. Pass an empty list
    to skip verification, or include ``HumanInTheLoopVerifier()`` to prompt.
    ``skip_render=True`` runs validation + lint only.

    ``tts`` and ``lipsync`` accept either a provider name string or a
    provider instance — useful for callers (e.g. ``muvid``) that want
    to inject a :class:`an.audio.WordTimingsLipSync` driven by their
    own alignment store, instead of letting ``an`` re-transcribe.
    """
    report = OrchestratorReport()
    if verifiers is None:
        verifiers = [LayoutLintVerifier(), MediaQualityVerifier()]

    # --- 1. validation ------------------------------------------------------
    try:
        report.validation = validate_project(project_dir)
    except Exception as e:
        report.success = False
        report.error = f"validation crashed: {e!r}"
        return report
    if not report.validation.passed:
        report.success = False
        report.error = "schema/semantic validation failed"
        return report

    # --- 2. pre-render verifiers (run on IR alone) --------------------------
    project = load(project_dir)
    for v in verifiers:
        try:
            vr = v.verify(project.scene, None)
            report.merge_verification(vr)
        except Exception as e:
            partial = VerificationReport()
            partial.add("warning", f"<{v.name}>", f"verifier crashed pre-render: {e!r}")
            report.merge_verification(partial)

    if skip_render or report.success is False:
        return report

    # --- 3. render ----------------------------------------------------------
    try:
        report.output_path = _render_project(
            project_dir,
            output_name=output_name,
            tts=tts,
            lipsync=lipsync,
            parallel=parallel,
        )
    except Exception as e:
        report.success = False
        report.error = f"render failed: {e!r}"
        return report

    # --- 4. post-render verifiers (with the actual mp4) ---------------------
    project = load(project_dir)
    from an.adapters._base import RenderResult

    rr = RenderResult(mp4_path=report.output_path, duration=project.scene.meta.duration)
    for v in verifiers:
        try:
            vr = v.verify(project.scene, rr)
            report.merge_verification(vr)
        except Exception as e:
            partial = VerificationReport()
            partial.add(
                "warning", f"<{v.name}>", f"verifier crashed post-render: {e!r}"
            )
            report.merge_verification(partial)

    return report


def iterate(project_dir: str | Path, instruction: str, **kwargs):
    """Apply a free-text edit instruction. Returns an IterateResult.

    Thin re-export for consistency with the rest of the orchestrator surface;
    the real implementation lives in ``an.iterate``.
    """
    from an.iterate import iterate as _iterate
    return _iterate(project_dir, instruction, **kwargs)
