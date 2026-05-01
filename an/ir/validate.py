"""Schema and semantic validation for SceneIR documents.

Two layers, called separately so callers can pick how strict to be:

- ``validate_schema`` — Pydantic validation only. Wrong types, missing required
  fields, malformed JSON.
- ``validate_semantic`` — cross-field checks. Unknown asset references,
  zero-duration shots, voice refs missing from a voices store.

Layout-overlap checks (boxes off-screen, text behind sprites) live in
``an.verify.layout``, not here, because they need a render context.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Mapping

from pydantic import ValidationError

from an.ir.schema import SceneIR


Severity = Literal["error", "warning", "info"]


@dataclass(slots=True)
class ValidationFinding:
    """A single validation issue with a path into the IR."""

    severity: Severity
    ir_path: str
    description: str


@dataclass(slots=True)
class ValidationReport:
    """Result of running one or more validators.

    ``passed`` is True iff there are no error-severity findings.
    """

    passed: bool = True
    findings: list[ValidationFinding] = field(default_factory=list)

    def add(self, severity: Severity, ir_path: str, description: str) -> None:
        self.findings.append(
            ValidationFinding(
                severity=severity, ir_path=ir_path, description=description
            )
        )
        if severity == "error":
            self.passed = False

    def merge(self, other: "ValidationReport") -> "ValidationReport":
        merged = ValidationReport(passed=self.passed and other.passed)
        merged.findings = self.findings + other.findings
        return merged


# -----------------------------------------------------------------------------
# Schema layer
# -----------------------------------------------------------------------------


def validate_schema(doc: Any) -> ValidationReport:
    """Validate that ``doc`` (dict, JSON string, or SceneIR) conforms to the schema.

    >>> validate_schema({"meta": {"title": "x"}, "timeline": []}).passed
    True
    >>> r = validate_schema({"meta": {"title": "x"}, "timeline": [{"id": "s", "duration": "not-a-number"}]})
    >>> r.passed
    False
    """
    report = ValidationReport()
    try:
        if isinstance(doc, SceneIR):
            return report
        if isinstance(doc, str):
            SceneIR.model_validate_json(doc)
        else:
            SceneIR.model_validate(doc)
    except ValidationError as e:
        for err in e.errors():
            loc = "/".join(str(x) for x in err.get("loc", ()))
            report.add("error", loc or "<root>", err.get("msg", "validation error"))
    return report


# -----------------------------------------------------------------------------
# Semantic layer
# -----------------------------------------------------------------------------


def validate_semantic(
    scene: SceneIR,
    *,
    available_voices: Mapping[str, Any] | None = None,
    available_characters: Mapping[str, Any] | None = None,
) -> ValidationReport:
    """Cross-field semantic checks. Pass live stores in for cross-store checks.

    Both ``available_voices`` and ``available_characters`` accept any mapping;
    only their ``__contains__`` is consulted. Pass ``None`` to skip those checks.
    """
    report = ValidationReport()

    if scene.meta.duration < 0:
        report.add("error", "meta/duration", "duration must be non-negative")
    if scene.meta.fps <= 0:
        report.add("error", "meta/fps", "fps must be positive")
    if not scene.timeline:
        report.add(
            "warning",
            "timeline",
            "scene has no shots — nothing to render. Add at least one "
            "`## Shot <id> (cutout)` heading to scene.md.",
        )

    seen_shot_ids: set[str] = set()
    for i, shot in enumerate(scene.timeline):
        path = f"timeline/{i}"
        if not shot.id:
            report.add("error", f"{path}/id", "shot id may not be empty")
        elif shot.id in seen_shot_ids:
            report.add("error", f"{path}/id", f"duplicate shot id: {shot.id!r}")
        seen_shot_ids.add(shot.id)

        if shot.duration <= 0:
            report.add("error", f"{path}/duration", "shot duration must be > 0")

        # Entity references resolve?
        if available_characters is not None:
            for j, entity in enumerate(shot.entities):
                if (
                    entity.kind == "character"
                    and entity.ref not in available_characters
                ):
                    report.add(
                        "warning",
                        f"{path}/entities/{j}",
                        f"character ref {entity.ref!r} not in characters store",
                    )

        # Dialogue voice refs resolve?
        if available_voices is not None:
            for k, line in enumerate(shot.dialogue):
                if (
                    line.voice_ref is not None
                    and line.voice_ref not in available_voices
                ):
                    report.add(
                        "warning",
                        f"{path}/dialogue/{k}/voice_ref",
                        f"voice ref {line.voice_ref!r} not in voices store",
                    )

        for k, line in enumerate(shot.dialogue):
            if not line.text.strip():
                report.add(
                    "warning", f"{path}/dialogue/{k}/text", "empty dialogue line"
                )
            if not line.speaker:
                report.add(
                    "error",
                    f"{path}/dialogue/{k}/speaker",
                    "dialogue requires a speaker",
                )

    return report
