"""VisionLMVerifier — Claude vision looks at sampled frames and reports issues.

**Opt-in, not default.** `an.orchestrate.orchestrate`'s default verifier chain
is `[LayoutLintVerifier(), MediaQualityVerifier()]` and this verifier is
deliberately not in it: it costs money and its input is a rendered mp4. The
lazy `anthropic` import and the key check therefore skip *cleanly*, so a caller
without the `vision` extra gets an informational Finding rather than an
`ImportError`. (The docstring here previously claimed the opposite — that the
skips existed "so the orchestrator can keep this verifier in its default
chain". Nothing in `an/` has ever put it in one.)

**Not configured is a skip. Configured and broken is a failure.** That
distinction is the whole of an#39. Every failure path used to add an `info`
Finding and return, and `VerificationReport.add` flips `passed` only on
`"error"` — so a dead model id, a 500, a refusal and an unparseable reply all
came back byte-identical to a clean bill of health. A verifier that reports
success when it failed to run is worse than no verifier, because it launders an
absence of evidence into evidence of absence.

So the paid call lives behind an injectable seam, :func:`judge_frames`, which
takes **frame bytes** (not paths — the real frames live in a
`TemporaryDirectory`, so a cache key over paths would miss 100% of the time)
and returns the model's **raw text** (so `_parse_issues` stays outside any
recording and parser fixes are testable against it for free). A judge that
cannot answer raises :class:`VisionJudgeError`; a cassette that has no
recording for a call raises :class:`CassetteMiss`, which derives from
**`BaseException`** because every other kind is swallowed twice on the way out
— once by this module's own handler and once by `orchestrate`'s broad
post-render `except Exception`, which guards every verifier and must stay
broad.

Cost: one Anthropic call carrying `frame_count` base64 PNGs plus a short
prompt. Roughly $0.005 with Haiku.
"""

from __future__ import annotations

import base64
import hashlib
import importlib.util
import inspect
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Sequence

from an.adapters._base import RenderResult
from an.ir.schema import SceneIR
from an.verify._base import VerificationReport
from an.verify.media import extract_frames


_DEFAULT_MODEL: str = "claude-haiku-4-5-20251001"  # cheap, vision-capable
_DEFAULT_FRAME_COUNT: int = 4
_DEFAULT_MAX_TOKENS: int = 800

#: Severity for "configured, called, no verdict" — a failed call, or a reply
#: that carried no verdict.
#:
#: `warning`, not `error`: a transient 529 must not fail a whole render. And
#: not `info`, which is the NOT-CONFIGURED severity — reusing it for
#: "configured and broken" is exactly what made this verifier invisible. Any
#: non-`info` Finding makes a dead verifier show up in the report.
#:
#: Set it to `"error"` if a refusal should fail the render; that is a policy
#: choice, and it is one constant.
FAILURE_SEVERITY: str = "warning"

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


class CassetteMiss(BaseException):
    """A recorded reply was asked for and there is none.

    Derived from ``BaseException`` rather than ``Exception``, deliberately and
    for a measured reason: an ``Exception`` raised where the API call sits is
    caught by this module's own handler AND by `an.orchestrate`'s post-render
    ``except Exception``, which guards every verifier and must stay broad. Both
    of them report and continue, so a test asserting "this run did not spend"
    would pass having verified nothing at all.

    Same reasoning, and the same shape, as `tests/conftest.py`'s
    ``OutboundNetworkAttempt`` — whose own docstring names "the verifiers' broad
    handlers" as the reason.
    """


class VisionJudgeError(RuntimeError):
    """The judge was configured, was called, and produced no verdict.

    An `an`-owned type on purpose. Narrowing the catch site to
    ``anthropic.APIError`` looks tighter and is not:
    ``issubclass(anthropic.NotFoundError, anthropic.APIError)`` is True, so a
    dead model id would still be swallowed into a pass — and it would put a
    vendor class at a catch site in this package's own control flow.
    """


#: Never part of a cassette key. A cassette filename must not be a function of
#: a credential.
_KEY_IGNORED: frozenset[str] = frozenset({"api_key"})


def judge_key(*args: Any, **kwargs: Any) -> str:
    """The cache key for one :func:`judge_frames` call.

    Derived from the seam's **signature**, not from a hand-written allowlist.
    An allowlist sets the default to *exclude*, which means a parameter added
    later collides with the base key and the recording is served forever for a
    request that changed. A false miss is red CI; a false hit is silent and
    unrecoverable, so the default has to be *include*.

    ``apply_defaults()`` matters too: without it ``judge_frames(frames)`` and
    the fully-spelled call are two different keys for one request.

    >>> k = judge_key([b"a"], model="m", max_tokens=1, prompt="p")
    >>> k == judge_key([b"a"], model="m", max_tokens=1, prompt="p", api_key="secret")
    True
    >>> k == judge_key([b"a"], model="m", max_tokens=1, prompt="  p  ")
    True
    >>> k == judge_key([b"b"], model="m", max_tokens=1, prompt="p")
    False
    """
    bound = inspect.signature(judge_frames).bind(*args, **kwargs)
    bound.apply_defaults()
    fields = {k: v for k, v in bound.arguments.items() if k not in _KEY_IGNORED}
    payload = {
        # Ordered, because frame order is semantic to the model.
        "frames": [hashlib.sha256(bytes(f)).hexdigest() for f in fields.pop("frames")],
        # Whitespace-collapsed, so re-indenting the prompt literal is not a
        # re-record. Any other edit to it is.
        "prompt": " ".join(str(fields.pop("prompt")).split()),
        # Everything else the signature carries — including anything added
        # later, which is the point.
        **fields,
    }
    blob = repr(sorted(payload.items(), key=lambda kv: kv[0]))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:32]


def judge_envelope(
    frames: Sequence[bytes],
    *,
    prompt: str = _PROMPT,
    model: str = _DEFAULT_MODEL,
    max_tokens: int = _DEFAULT_MAX_TOKENS,
    api_key: str | None = None,
) -> dict:
    """Call the vision model and return a recordable envelope.

    Memoized one level *in* from :func:`judge_frames` because a memoizer hands
    the store only ``(key, return_value)`` — so a seam returning a bare ``str``
    can record nothing beside the reply, and the provenance that makes a
    cassette auditable would be unwritable.
    """
    try:
        import anthropic  # type: ignore
    except ImportError as e:  # pragma: no cover - the caller checks first
        raise VisionJudgeError(
            "the `anthropic` SDK is not installed; `pip install an[vision]`"
        ) from e

    content_blocks: list[dict] = [
        {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": "image/png",
                "data": base64.b64encode(bytes(f)).decode("ascii"),
            },
        }
        for f in frames
    ]
    content_blocks.append({"type": "text", "text": prompt})

    client = anthropic.Anthropic(api_key=api_key)
    try:
        msg = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            messages=[{"role": "user", "content": content_blocks}],
        )
    except Exception as e:
        raise VisionJudgeError(f"the vision API call failed: {e!r}") from e

    try:
        reply = "".join(
            blk.text for blk in msg.content if getattr(blk, "type", None) == "text"
        )
    except Exception as e:
        raise VisionJudgeError(f"the vision reply carried no text blocks: {e!r}") from e

    return {
        "reply": reply,
        "frames": [hashlib.sha256(bytes(f)).hexdigest() for f in frames],
        "model": model,
        "max_tokens": max_tokens,
        "prompt": prompt,
        "recorded_at": datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z"),
        "recorded_with": {"anthropic": _anthropic_version()},
    }


def _anthropic_version() -> str | None:
    from importlib.metadata import PackageNotFoundError, version

    try:
        return version("anthropic")
    except PackageNotFoundError:
        return None


_LEGIBILITY_PROMPT = """You are judging LIP-SYNC LEGIBILITY on a dense strip of frames from a
short animated cartoon, all taken from inside ONE spoken line. The art is
deliberately simple; judge only the mouth. The character is saying:

    "{text}"

Reply in JSON only, with this shape:

{{
  "legibility": <integer 1-5: 1 = the mouth could be saying anything, 5 = you could
                 read this line from the mouth alone>,
  "heard": "<the words you would guess from the mouth shapes alone, or empty>"
}}
"""


def legibility_prompt(text: str) -> str:
    """The legibility prompt for one line. The text is part of the key, so a
    different line is a different recording."""
    return _LEGIBILITY_PROMPT.format(text=text.strip())


def _parse_legibility(body: str) -> tuple[int, str] | None:
    """``(score, heard)`` from a reply, or ``None`` when it carried no verdict.

    >>> _parse_legibility('{"legibility": 4, "heard": "hold the shape"}')
    (4, 'hold the shape')
    >>> _parse_legibility("I can't help with that.") is None
    True
    >>> _parse_legibility('{"legibility": 9}') is None
    True
    """
    import json
    import re

    if not body:
        return None
    fenced = re.search(r"```(?:json)?\s*({.*?})\s*```", body, re.DOTALL)
    raw = fenced.group(1) if fenced else body
    start, end = raw.find("{"), raw.rfind("}")
    if start == -1 or end == -1 or end < start:
        return None
    try:
        data = json.loads(raw[start : end + 1])
    except json.JSONDecodeError:
        return None
    score = data.get("legibility")
    if not isinstance(score, int) or isinstance(score, bool) or not 1 <= score <= 5:
        return None
    heard = data.get("heard")
    return score, (heard if isinstance(heard, str) else "")


def judge_legibility(
    frames: Sequence[bytes],
    text: str,
    *,
    judge: Callable[..., str] | None = None,
    model: str = _DEFAULT_MODEL,
    max_tokens: int = _DEFAULT_MAX_TOKENS,
    api_key: str | None = None,
) -> tuple[int, str] | None:
    """Score a dense in-line frame strip for lip-sync legibility (an#97).

    ``judge`` is the `judge_frames`-shaped seam — the cassette-backed one in
    tests, the paid one otherwise. Parsing stays outside the recording.
    """
    reply = (judge or judge_frames)(
        frames, prompt=legibility_prompt(text), model=model, max_tokens=max_tokens, api_key=api_key
    )
    return _parse_legibility(reply)


def judge_frames(
    frames: Sequence[bytes],
    *,
    prompt: str = _PROMPT,
    model: str = _DEFAULT_MODEL,
    max_tokens: int = _DEFAULT_MAX_TOKENS,
    api_key: str | None = None,
) -> str:
    """The paid seam: frame bytes in, the model's raw text reply out.

    ``bytes`` rather than ``Path`` because the real frames live in a
    `TemporaryDirectory`, so a key over paths hashes a fresh random string and
    misses every time. **Raw text** rather than parsed findings because that
    keeps `_parse_issues` outside any recording — a parser fix is then testable
    against the recording for free, and record-vs-replay drift is impossible.
    """
    return judge_envelope(
        frames,
        prompt=prompt,
        model=model,
        max_tokens=max_tokens,
        api_key=api_key,
    )["reply"]


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
        judge: Callable[..., str] | None = None,
    ) -> None:
        self.model = model
        self.frame_count = max(1, frame_count)
        self.max_tokens = max_tokens
        self.api_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        #: Injected, per "no globals, no service locators" — and because
        #: injection is what makes record-vs-replay drift impossible: the
        #: recorded and replayed paths are the same call through the same
        #: object, differing only in what the store returns.
        self.judge = judge or judge_frames
        #: Whether the skip conditions below apply. They are about the DEFAULT
        #: judge's ability to run — an injected one may be cassette-backed, in
        #: which case it needs neither the SDK nor a key, and gating it on them
        #: makes it unreachable exactly where it is most useful. Learned from
        #: CI: locally the SDK is installed, so every injected-judge test
        #: passed for the wrong reason.
        self._judge_is_default = judge is None

    def verify(self, ir: SceneIR, render: RenderResult | None) -> VerificationReport:
        report = VerificationReport()
        if render is None or not render.mp4_path or not render.mp4_path.exists():
            report.add("info", "<vision_lm>", "no render result; skipping vision check")
            return report
        if self._judge_is_default:
            if self.api_key is None:
                report.add("info", "<vision_lm>", "ANTHROPIC_API_KEY not set; skipping")
                return report
            if importlib.util.find_spec("anthropic") is None:
                report.add(
                    "info",
                    "<vision_lm>",
                    "anthropic SDK not installed; skipping (pip install an[vision])",
                )
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
                report.add(
                    "warning",
                    "<vision_lm>",
                    "the render produced no extractable frames, so nothing was "
                    "reviewed. This is a failure to run, not a clean review.",
                )
                return report
            frame_bytes = [f.read_bytes() for f in frames]

        # NOTE: `CassetteMiss` derives from BaseException and is deliberately
        # NOT caught here. A test asserting "this run did not spend" has to see
        # it; swallowing it here would make that test pass having verified
        # nothing.
        try:
            reply = self.judge(
                frame_bytes,
                prompt=_PROMPT,
                model=self.model,
                max_tokens=self.max_tokens,
                api_key=self.api_key,
            )
        except VisionJudgeError as e:
            report.add(
                FAILURE_SEVERITY,
                "<vision_lm>",
                f"the vision judge was configured and did not answer: {e}. "
                "Nothing was reviewed — this is not a clean pass.",
            )
            return report

        verdict = _parse_issues(reply)
        if verdict is None:
            report.add(
                FAILURE_SEVERITY,
                "<vision_lm>",
                "the vision judge answered, and the answer carried no verdict "
                f"(no parseable `issues` list). Reply began: {reply[:200]!r}. "
                "Nothing was reviewed — this is not a clean pass.",
            )
            return report
        if not verdict:
            report.add("info", "<vision_lm>", "vision LM reported no issues")
            return report
        for item in verdict:
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


def _parse_issues(body: str) -> list[dict] | None:
    """Pull the issues list out of a possibly-wrapped reply, or say there is none.

    **`None` and `[]` are different answers.** `None` means the reply carried no
    verdict — it was empty, or a refusal, or prose with no JSON in it. `[]`
    means the model looked and found nothing.

    Collapsing them is how a refusal came back byte-identical to a clean bill of
    health: an empty reply, a refusal, and a literal `{"issues": []}` all
    produced the same `"vision LM reported no issues"` Finding at `info`
    severity, and `passed` stayed True through all three.

    >>> _parse_issues("") is None
    True
    >>> _parse_issues("I can't help with that.") is None
    True
    >>> _parse_issues('{"issues": []}')
    []
    >>> _parse_issues('{"verdict": "fine"}') is None
    True
    """
    import json
    import re

    if not body:
        return None
    # Strip ```json fences if present.
    fenced = re.search(r"```(?:json)?\s*({.*?})\s*```", body, re.DOTALL)
    raw = fenced.group(1) if fenced else body
    # Find first { ... last } so prose around the JSON doesn't break parsing.
    start = raw.find("{")
    end = raw.rfind("}")
    if start == -1 or end == -1 or end < start:
        return None
    try:
        data = json.loads(raw[start : end + 1])
    except json.JSONDecodeError:
        return None
    issues = data.get("issues")
    return issues if isinstance(issues, list) else None
