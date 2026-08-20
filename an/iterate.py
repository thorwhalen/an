"""Iterative edit loop — free-text instruction → IR patch via Claude → re-render.

Phase 10. The spec's signature user story:

    > "I say 'make Maya's laugh longer and warmer.' Claude Code edits the IR,
    > re-renders just the affected shot, and shows me the diff. I approve."

This module turns a free-text instruction into a structured set of patches
on the SceneIR JSON tree, validates them, applies them, persists, and (optionally)
re-renders. The vision LM is Claude Opus 4.7 with adaptive thinking;
``messages.parse()`` + a Pydantic schema guarantees the patches are valid
JSON of the expected shape.

Path syntax for patches: slash-delimited JSON-pointer-style. List indices
are integers. Examples:

    "meta/title"
    "timeline/0/duration"
    "timeline/1/dialogue/0/text"
    "timeline/1/dialogue/0/emotion"

Patch operations:

    {"op": "set", "path": "...", "value": ...}     # replace (or create) value
    {"op": "append", "path": "...", "value": ...}  # append to a list
    {"op": "delete", "path": "..."}                # remove an entry

The orchestrator records each iteration in ``mall["decisions"]`` so the
agent can review what changed across runs.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from an.ir.schema import SceneIR
from an.ir.validate import ValidationReport, validate_schema, validate_semantic
from an.project import Project, load


_DEFAULT_MODEL: str = "claude-opus-4-7"
_DEFAULT_MAX_TOKENS: int = 4096


class IterateError(RuntimeError):
    """Raised when an iterate call cannot apply its proposed patches."""


# -----------------------------------------------------------------------------
# Pydantic response schema — what we ask Claude to fill in.
# -----------------------------------------------------------------------------


class Patch(BaseModel):
    """A single mutation against the SceneIR JSON tree."""

    op: Literal["set", "append", "delete"] = Field(
        ..., description="Mutation kind: set/append a value, or delete an entry."
    )
    path: str = Field(
        ...,
        description=(
            "Slash-delimited path into the SceneIR. List indices are integers. "
            "Examples: 'meta/title', 'timeline/0/duration', "
            "'timeline/1/dialogue/0/text', 'timeline/1/dialogue/0/emotion'."
        ),
    )
    value: Any | None = Field(
        None,
        description=(
            "New value for set/append. Omit (or use None) for delete. "
            "Use the simplest type that fits the IR field."
        ),
    )


class IterateResponse(BaseModel):
    """Structured reply from Claude for a single iterate() call."""

    summary: str = Field(
        ...,
        description="One-sentence description of what changed and why.",
    )
    patches: list[Patch] = Field(
        default_factory=list,
        description="Ordered list of patches to apply.",
    )
    affected_shots: list[str] = Field(
        default_factory=list,
        description=(
            "Shot ids whose rendered output is invalidated by these patches. "
            "Used by the caller to re-render only what changed."
        ),
    )


# -----------------------------------------------------------------------------
# IterateResult — what iterate() returns to the caller.
# -----------------------------------------------------------------------------


@dataclass(slots=True)
class IterateResult:
    """Outcome of an iterate() call."""

    success: bool = True
    summary: str = ""
    patches: list[Patch] = field(default_factory=list)
    affected_shots: list[str] = field(default_factory=list)
    new_scene: SceneIR | None = None
    validation: ValidationReport | None = None
    error: str | None = None


# -----------------------------------------------------------------------------
# System prompt — stable, cacheable.
# -----------------------------------------------------------------------------


_SYSTEM_PROMPT = """You are an animation director's assistant. The user is iterating
on a scene; you receive the scene's current state (a JSON-shaped IR) and a free-text
instruction, and you reply with a structured patch on that JSON tree.

The IR shape (relevant fields):

  - meta: {title, author, duration, fps, resolution, default_style, notes}
  - timeline: a list of shots, each with:
      - id (string, unique)
      - style ("cutout" | "manim" | "motion_graphics" | "whiteboard")
      - duration (seconds, float)
      - camera: {move: "hold"|"push_in"|"pull_out"|"zoom_in"|"zoom_out", ...}
      - entities: list of {kind, id, store, ref, ...}
      - actions: list of action dicts (kind ∈ {tween, set, play, sequence, parallel, delay, loop})
        A tween/set action's "property" MUST be one of:
          x, y, rotation, rotation_rad, scale_x, scale_y, skew_x, skew_y,
          pivot_x, pivot_y, alpha
        Anything else (opacity, visible, color, tint, width, ...) is NOT
        implemented and now FAILS THE RENDER — it used to be silently ignored.
        "alpha" is the fade primitive and cascades to a character's parts.
        A tween with no "from" starts at the property's rest value: 1.0 for
        scale_x / scale_y / alpha, 0.0 for the rest.
        Do NOT emit "play" actions: named reusable animations are unimplemented
        and a "play" now fails the compile.
      - dialogue: list of {speaker, text, emotion, voice_ref, start, duration, ...}
      - narration: list (same shape as dialogue, no speaker pin)

Path syntax for patches: slash-delimited, list indices are integers. Examples:

  "meta/title"                              → top-level meta field
  "timeline/0/duration"                     → first shot's duration
  "timeline/1/dialogue/0/text"              → second shot, first dialogue line, text
  "timeline/1/dialogue/0/emotion"           → set the emotion ("happy" | "sad" | "angry"
                                              | "surprised" | "skeptical" | "amused"
                                              | "thinking" | "neutral")

Patch operations:

  {"op": "set", "path": "...", "value": ...}       — replace/create a value
  {"op": "append", "path": "...", "value": ...}    — append to a list
  {"op": "delete", "path": "..."}                  — remove an entry

Rules:
  1. Make the SMALLEST set of patches that fulfills the instruction. Don't reflow
     the whole scene if one field will do.
  2. Preserve shot ids unless the user explicitly asks to rename one.
  3. When changing a dialogue line's text, also update its `emotion` if the new
     wording suggests a different mood.
  4. When extending a dialogue line meaningfully, you may extend the parent shot's
     duration (set timeline/N/duration) so the line fits.
  5. Populate affected_shots with the ids of every shot whose render needs to be
     redone (i.e. any shot you patched).
  6. Do not invent new fields. Keep emotion values inside the allowed set.
  7. Keep the summary short — one sentence."""


# -----------------------------------------------------------------------------
# iterate() — the public entry point.
# -----------------------------------------------------------------------------


def iterate(
    project_dir: str | Path,
    instruction: str,
    *,
    apply: bool = True,
    model: str = _DEFAULT_MODEL,
    max_tokens: int = _DEFAULT_MAX_TOKENS,
) -> IterateResult:
    """Apply a free-text instruction to the scene at ``project_dir``.

    Steps:
      1. Load the project.
      2. Send the current scene + the instruction to Claude.
      3. Parse the structured patch response.
      4. Apply patches to a deep-copied IR; validate.
      5. If valid and ``apply=True``, persist to mall["scenes"]["main"] and
         append to mall["decisions"].

    The caller is responsible for re-rendering. ``IterateResult.affected_shots``
    enumerates which shots changed so the orchestrator can render only those.
    """
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise IterateError(
            "iterate() requires ANTHROPIC_API_KEY in the environment. "
            "Set it (and optionally `pip install anthropic` if not present), "
            "then retry."
        )
    try:
        import anthropic  # noqa: F401  (lazy import; presence check)
    except ImportError as e:
        raise IterateError(
            "iterate() requires the anthropic SDK. pip install anthropic"
        ) from e

    project: Project = load(project_dir)
    response = _call_claude(
        project.scene, instruction, model=model, max_tokens=max_tokens
    )

    new_scene_dict = _apply_patches_to_ir(project.scene, response.patches)
    try:
        new_scene = SceneIR.model_validate(new_scene_dict)
    except Exception as e:
        return IterateResult(
            success=False,
            summary=response.summary,
            patches=list(response.patches),
            affected_shots=list(response.affected_shots),
            error=f"patched IR failed schema validation: {e!r}",
        )

    schema_report = validate_schema(new_scene)
    semantic_report = validate_semantic(
        new_scene,
        available_voices=project.mall.get("voices"),
        available_characters=project.mall.get("characters"),
    )
    full_report = schema_report.merge(semantic_report)

    result = IterateResult(
        success=full_report.passed,
        summary=response.summary,
        patches=list(response.patches),
        affected_shots=list(response.affected_shots),
        new_scene=new_scene,
        validation=full_report,
    )
    if not full_report.passed:
        result.error = "validation failed on patched IR"
        return result

    if apply:
        # Invalidate any cached per-shot artifacts for affected shots so the
        # next render regenerates them.
        for shot_id in response.affected_shots:
            try:
                del project.mall["shots"][shot_id]
            except KeyError:
                pass
        project.mall["scenes"]["main"] = new_scene
        project.mall["decisions"].append(
            kind="iterate",
            body={
                "instruction": instruction,
                "summary": response.summary,
                "patches": [p.model_dump() for p in response.patches],
                "affected_shots": list(response.affected_shots),
                "model": model,
            },
        )

    return result


# -----------------------------------------------------------------------------
# Path-based patch application.
# -----------------------------------------------------------------------------


def _apply_patches_to_ir(scene: SceneIR, patches: list[Patch]) -> dict[str, Any]:
    """Apply ``patches`` to a JSON-shaped copy of ``scene`` and return the dict.

    The dict can then be re-validated via ``SceneIR.model_validate``. We
    operate on the dict (not the Pydantic model) so we can freely add/remove
    fields without fighting the schema's strictness on individual mutations.
    """
    doc = json.loads(scene.model_dump_json())
    for patch in patches:
        _apply_one(doc, patch)
    return doc


def _apply_one(doc: Any, patch: Patch) -> None:
    parts = [p for p in patch.path.split("/") if p != ""]
    if not parts:
        raise IterateError(f"empty patch path: {patch.path!r}")
    if patch.op == "set":
        parent, last = _walk_to_parent(doc, parts)
        _set_at(parent, last, patch.value)
    elif patch.op == "append":
        target = _walk(doc, parts)
        if not isinstance(target, list):
            raise IterateError(
                f"append target {patch.path!r} is not a list (got {type(target).__name__})"
            )
        target.append(patch.value)
    elif patch.op == "delete":
        parent, last = _walk_to_parent(doc, parts)
        _del_at(parent, last)
    else:
        raise IterateError(f"unknown patch op: {patch.op!r}")


def _walk(doc: Any, parts: list[str]) -> Any:
    cur = doc
    for p in parts:
        cur = _step(cur, p)
    return cur


def _walk_to_parent(doc: Any, parts: list[str]) -> tuple[Any, str]:
    parent = _walk(doc, parts[:-1])
    return parent, parts[-1]


def _step(node: Any, key: str) -> Any:
    if isinstance(node, list):
        try:
            return node[int(key)]
        except (ValueError, IndexError) as e:
            raise IterateError(f"bad list index {key!r}: {e}") from e
    if isinstance(node, dict):
        if key not in node:
            raise IterateError(f"missing key {key!r} in dict")
        return node[key]
    raise IterateError(f"can't traverse into {type(node).__name__} with key {key!r}")


def _set_at(parent: Any, key: str, value: Any) -> None:
    if isinstance(parent, list):
        try:
            parent[int(key)] = value
        except (ValueError, IndexError) as e:
            raise IterateError(f"bad list index for set {key!r}: {e}") from e
    elif isinstance(parent, dict):
        parent[key] = value
    else:
        raise IterateError(f"can't set into {type(parent).__name__}")


def _del_at(parent: Any, key: str) -> None:
    if isinstance(parent, list):
        try:
            del parent[int(key)]
        except (ValueError, IndexError) as e:
            raise IterateError(f"bad list index for delete {key!r}: {e}") from e
    elif isinstance(parent, dict):
        if key not in parent:
            raise IterateError(f"can't delete missing key {key!r}")
        del parent[key]
    else:
        raise IterateError(f"can't delete from {type(parent).__name__}")


# -----------------------------------------------------------------------------
# Anthropic call.
# -----------------------------------------------------------------------------


def _call_claude(
    scene: SceneIR,
    instruction: str,
    *,
    model: str,
    max_tokens: int,
) -> IterateResponse:
    """Send the scene + instruction to Claude. Returns a parsed IterateResponse.

    Uses plain ``messages.create`` (SDK-version-agnostic) with a strict
    "reply with JSON only" instruction, then parses leniently — same shape
    as the VisionLMVerifier's reply parser.
    """
    import anthropic

    client = anthropic.Anthropic()
    scene_repr = scene.model_dump_json(indent=2)

    schema_hint = json.dumps(IterateResponse.model_json_schema(), indent=2)
    closing = (
        "Reply ONLY with a JSON object that conforms to this schema. No prose, "
        "no markdown fences, no ```json — just the raw object:\n\n"
        f"{schema_hint}"
    )

    # The scene + schema hint are the stable, cacheable parts; the instruction
    # varies per call.
    user_content = [
        {
            "type": "text",
            "text": f"Current scene IR (JSON):\n```json\n{scene_repr}\n```",
        },
        {
            "type": "text",
            "text": closing,
            "cache_control": {"type": "ephemeral"},
        },
        {"type": "text", "text": f"Instruction: {instruction}"},
    ]

    try:
        response = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            thinking={"type": "adaptive"},
            system=_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_content}],
        )
    except anthropic.APIStatusError as e:
        raise IterateError(f"Anthropic API call failed: {e!r}") from e

    text = "".join(
        b.text for b in response.content if getattr(b, "type", None) == "text"
    )
    if not text.strip():
        raise IterateError(
            f"Claude returned no text content (stop_reason={response.stop_reason})"
        )
    return _parse_iterate_response(text)


def _parse_iterate_response(body: str) -> IterateResponse:
    """Lenient JSON parser — pull the IterateResponse object from a reply.

    Strips ``` fences, finds the outermost ``{ ... }`` envelope, validates
    against the Pydantic model.
    """
    import re

    # Strip fences.
    fence = re.search(r"```(?:json)?\s*({.*?})\s*```", body, re.DOTALL)
    raw = fence.group(1) if fence else body
    start = raw.find("{")
    end = raw.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise IterateError(f"Claude reply contained no JSON object: {body[:200]}")
    try:
        data = json.loads(raw[start : end + 1])
    except json.JSONDecodeError as e:
        raise IterateError(f"could not parse JSON from Claude reply: {e}") from e
    try:
        return IterateResponse.model_validate(data)
    except Exception as e:
        raise IterateError(f"Claude reply did not match expected shape: {e}") from e
