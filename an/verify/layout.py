"""LayoutLintVerifier — cheap pre-render checks on the IR.

Runs the same semantic checks as ``an.ir.validate.validate_semantic`` plus a
few that need a render context (do dialogue lines fit in their shot's
duration? is the scene's total duration consistent with the timeline?).

Deliberately runs on the IR alone (RenderResult=None is fine), so the
orchestrator can call it BEFORE rendering and skip a costly render if the
IR is broken.
"""

from __future__ import annotations

from an.ir.schema import SceneIR
from an.verify._base import VerificationReport


class LayoutLintVerifier:
    """Cheap IR-only verifier. Implements ``Verifier``."""

    name: str = "layout_lint"

    def verify(self, ir: SceneIR, render=None) -> VerificationReport:
        report = VerificationReport()

        # Total duration consistency
        timeline_total = sum(s.duration for s in ir.timeline)
        if ir.meta.duration > 0 and abs(ir.meta.duration - timeline_total) > 0.01:
            report.add(
                "warning",
                "meta/duration",
                f"meta.duration ({ir.meta.duration}) doesn't match sum of "
                f"shot durations ({timeline_total})",
                suggested_fix=f"set meta.duration = {timeline_total}",
            )

        # Each shot's structural checks.
        seen_ids: set[str] = set()
        for i, shot in enumerate(ir.timeline):
            path = f"timeline/{i}"
            if shot.id in seen_ids:
                report.add(
                    "error",
                    f"{path}/id",
                    f"duplicate shot id {shot.id!r}",
                )
            seen_ids.add(shot.id)
            if shot.duration <= 0:
                report.add(
                    "error",
                    f"{path}/duration",
                    f"shot duration must be > 0; got {shot.duration}",
                )

            # Dialogue fits inside the shot?
            for j, line in enumerate(shot.dialogue):
                if line.duration is not None and line.start is not None:
                    if line.start + line.duration > shot.duration + 0.05:
                        report.add(
                            "warning",
                            f"{path}/dialogue/{j}",
                            f"dialogue line ends at {line.start + line.duration:.2f}s "
                            f"but shot duration is {shot.duration}s; line will be "
                            f"clipped or stretch the shot",
                            suggested_fix="extend shot.duration or shorten the line",
                        )
                if not line.text.strip():
                    report.add(
                        "warning",
                        f"{path}/dialogue/{j}/text",
                        "empty dialogue line",
                    )

            # Resolution sanity (per shot — actually a meta thing but reported here).
            res = ir.meta.resolution
            if res.width <= 0 or res.height <= 0:
                report.add(
                    "error",
                    "meta/resolution",
                    f"resolution must be positive; got {res.width}x{res.height}",
                )

        return report
