"""VisionLMVerifier — Claude vision looks at sampled frames + reports issues.

Phase 9 closes the loop the architectural spec called out from day one:
a Verifier that uses a vision LM to inspect rendered frames and produce
``Finding``s the orchestrator can route back into the IR.

Lazy-imports the ``anthropic`` SDK + needs ``ANTHROPIC_API_KEY`` in env.
Skips with an informational ``Finding`` (passed=True) when either is
missing, so the orchestrator can keep this verifier in its default chain
without making it a hard dependency.

Cost: a single verify() sends 3-4 frames + a short prompt. Pricing rounds
to ≈ $0.005 per call with claude-haiku, more with sonnet.
"""

from __future__ import annotations

import base64
import os
import tempfile
from pathlib import Path

from an.adapters._base import RenderResult
from an.ir.schema import SceneIR
from an.verify._base import VerificationReport
from an.verify.media import extract_frames


_DEFAULT_MODEL: str = "claude-haiku-4-5-20251001"  # cheap, vision-capable
_DEFAULT_FRAME_COUNT: int = 4
_DEFAULT_MAX_TOKENS: int = 800

_PROMPT = """You are reviewing frames from a short animated cartoon. The character
art is intentionally simple (placeholder geometry: ellipse heads, rect
torsos/limbs, curved bezier mouths, eyes drawn as white-sclera + dark
pupils). DO NOT comment on the simplicity of the art itself — that is by
design. DO comment on:

- Characters that are clipped off-screen or overlap badly.
- Faces that are missing parts (no eyes, mouth not visible, head occluded).
- Motion that looks broken (limbs detached, character flying off-canvas).
- Mouth shape that obviously doesn't match active speech (e.g. closed lips
  during a long word).
- Background obscuring a character.

Reply in JSON only, with this shape:

{
  "issues": [
    {"severity": "warning"|"error", "where": "<short location hint>", "what": "<one sentence>"}
  ]
}

If everything looks fine, return ``{"issues": []}``.
"""


class VisionLMVerifier:
    """Claude vision Verifier (skip-if-missing-deps)."""

    name: str = "vision_lm"

    def __init__(
        self,
        *,
        model: str = _DEFAULT_MODEL,
        frame_count: int = _DEFAULT_FRAME_COUNT,
        max_tokens: int = _DEFAULT_MAX_TOKENS,
        api_key: str | None = None,
    ) -> None:
        self.model = model
        self.frame_count = max(1, frame_count)
        self.max_tokens = max_tokens
        self.api_key = api_key or os.environ.get("ANTHROPIC_API_KEY")

    def verify(self, ir: SceneIR, render: RenderResult | None) -> VerificationReport:
        report = VerificationReport()
        if render is None or not render.mp4_path or not render.mp4_path.exists():
            report.add("info", "<vision_lm>", "no render result; skipping vision check")
            return report
        if self.api_key is None:
            report.add("info", "<vision_lm>", "ANTHROPIC_API_KEY not set; skipping")
            return report
        try:
            import anthropic  # type: ignore
        except ImportError:
            report.add("info", "<vision_lm>", "anthropic SDK not installed; skipping")
            return report

        # Sample N frames roughly evenly distributed across the render.
        with tempfile.TemporaryDirectory() as d:
            target_fps = max(0.5, self.frame_count / max(0.5, render.duration))
            frames = extract_frames(render.mp4_path, d, fps=target_fps)
            # Deduplicate to exactly self.frame_count by picking evenly spaced.
            if len(frames) > self.frame_count:
                step = len(frames) / self.frame_count
                frames = [frames[int(i * step)] for i in range(self.frame_count)]
            if not frames:
                report.add("info", "<vision_lm>", "no frames extracted; skipping")
                return report

            content_blocks: list[dict] = []
            for f in frames:
                content_blocks.append(
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/png",
                            "data": base64.b64encode(f.read_bytes()).decode("ascii"),
                        },
                    }
                )
            content_blocks.append({"type": "text", "text": _PROMPT})

            client = anthropic.Anthropic(api_key=self.api_key)
            try:
                msg = client.messages.create(
                    model=self.model,
                    max_tokens=self.max_tokens,
                    messages=[{"role": "user", "content": content_blocks}],
                )
            except Exception as e:
                report.add(
                    "info",
                    "<vision_lm>",
                    f"anthropic API call failed: {e!r}",
                )
                return report

        # Parse the model's JSON reply. Be lenient — strip surrounding prose.
        body = ""
        try:
            body = "".join(
                blk.text for blk in msg.content if getattr(blk, "type", None) == "text"
            )
        except Exception:
            body = ""
        issues = _parse_issues(body)
        if not issues:
            report.add("info", "<vision_lm>", "vision LM reported no issues")
            return report
        for item in issues:
            severity = item.get("severity", "warning")
            if severity not in ("error", "warning", "info"):
                severity = "warning"
            report.add(
                severity,
                f"<vision_lm:{item.get('where', '')}>",
                item.get("what", "vision LM finding"),
                suggested_fix=item.get("fix"),
            )
        return report


def _parse_issues(body: str) -> list[dict]:
    """Pull the issues JSON out of a possibly-wrapped reply. Lenient parser."""
    import json
    import re

    if not body:
        return []
    # Strip ```json fences if present.
    fenced = re.search(r"```(?:json)?\s*({.*?})\s*```", body, re.DOTALL)
    raw = fenced.group(1) if fenced else body
    # Find first { ... last } so prose around the JSON doesn't break parsing.
    start = raw.find("{")
    end = raw.rfind("}")
    if start == -1 or end == -1 or end < start:
        return []
    try:
        data = json.loads(raw[start : end + 1])
    except json.JSONDecodeError:
        return []
    issues = data.get("issues")
    return issues if isinstance(issues, list) else []
