"""RemotionRenderer — invoke `npx remotion render` against a generated TSX project.

Phase 6 skeleton. The full implementation needs a templated Remotion node
project (package.json + Composition.tsx + Root.tsx) and per-shot TSX
generation. For now the renderer raises a clear error when invoked,
documenting what's needed.
"""

from __future__ import annotations

import shutil

from an.adapters._base import RenderContext, RenderResult
from an.ir.schema import Shot


class RemotionRenderError(RuntimeError):
    """Raised when a Remotion render fails."""


class RemotionRenderer:
    """Remotion-based renderer (skeleton)."""

    name: str = "remotion"
    supported_renderers: tuple[str, ...] = ("motion_graphics",)

    def can_render(self, shot: Shot) -> bool:
        return shot.renderer == "motion_graphics"

    def render(self, shot: Shot, ctx: RenderContext) -> RenderResult:
        if shutil.which("npx") is None:
            raise RemotionRenderError(
                "npx not found on PATH. Install Node.js (brew install node) "
                "and ensure npm/npx are available."
            )
        raise RemotionRenderError(
            "RemotionRenderer is a skeleton in Phase 6. Full implementation "
            "needs a templated Remotion node project under "
            "an/data/remotion_template/ + per-shot TSX generation. "
            "Use the cutout backend for v0.1."
        )
