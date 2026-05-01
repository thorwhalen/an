"""Verifier protocol + Finding/VerificationReport dataclasses.

Same shape as `an.ir.validate.ValidationReport` but lives here because
verification operates on a (IR + render) pair while validation operates on
the IR alone. Keeping them separate types lets us evolve them independently.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Protocol, runtime_checkable

from an.adapters._base import RenderResult
from an.ir.schema import SceneIR


Severity = Literal["error", "warning", "info"]


@dataclass(slots=True)
class Finding:
    """A single verification issue.

    ``ir_path`` lets the orchestrator route the fix to the correct layer of
    the IR — e.g. ``"timeline/0/dialogue/1"``.
    """

    severity: Severity
    ir_path: str
    description: str
    suggested_fix: str | None = None


@dataclass(slots=True)
class VerificationReport:
    """Result of running one or more verifiers."""

    passed: bool = True
    findings: list[Finding] = field(default_factory=list)

    def add(
        self,
        severity: Severity,
        ir_path: str,
        description: str,
        suggested_fix: str | None = None,
    ) -> None:
        self.findings.append(
            Finding(
                severity=severity,
                ir_path=ir_path,
                description=description,
                suggested_fix=suggested_fix,
            )
        )
        if severity == "error":
            self.passed = False

    def merge(self, other: "VerificationReport") -> "VerificationReport":
        merged = VerificationReport(passed=self.passed and other.passed)
        merged.findings = self.findings + other.findings
        return merged


@runtime_checkable
class Verifier(Protocol):
    """Pluggable verifier. Same interface for human, lint, vision-LM, MoVer."""

    name: str

    def verify(self, ir: SceneIR, render: RenderResult | None) -> VerificationReport:
        """Verify a scene + (optional) render result. ``render`` may be None
        for pre-render lint passes."""
