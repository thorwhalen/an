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

from an.base import TRANSFORM_PROPERTIES
from an.characters.play import art_exists_for, play_problems
from an.characters.schema import CharacterDescriptor
from an.expression.binding import expression_problems
from an.ir.compose import flatten
from an.ir.migrate import migrate
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


#: Camera moves the cutout renderer implements. `hold` is a real no-op.
#:
#: Duplicated from the compiler deliberately: importing it here would make the
#: IR layer depend on an adapter, which is the wrong direction. The test suite
#: pins the two together instead — the same shape `artful` uses for vocabulary
#: shared across packages that must not depend on each other.
_RENDERABLE_CAMERA_MOVES: frozenset[str] = frozenset(
    {"hold", "push_in", "pull_out", "zoom_in", "zoom_out"}
)

#: Entity kinds the cutout renderer draws. `voice` and `style` are legitimately
#: not drawable — they configure the render rather than appearing in it.
_DRAWABLE_ENTITY_KINDS: frozenset[str] = frozenset({"character", "environment"})
_CONFIGURING_ENTITY_KINDS: frozenset[str] = frozenset({"voice", "style"})

#: Any property outside the transform vocabulary on a set/tween names a swap
#: SET, which must be declared by the target entity's descriptor (an#87). The
#: vocabulary itself is the shared SSOT in ``an.base`` (importable by every
#: layer); the compiler's rest-value table is asserted equal to it by test.
_TRANSFORM_PROPERTIES: frozenset[str] = TRANSFORM_PROPERTIES

#: The swap sets a descriptor-less (procedural) rig supports — declared as
#: data on its drawn mouth by the compiler (`PROCEDURAL_MOUTH_SETS`). This
#: layer cannot import the adapter, so the value is duplicated here and
#: pinned against the compiler's constant by ``tests/test_swap_channels.py``.
_PROCEDURAL_SWAP_SETS: frozenset[str] = frozenset({"viseme"})


def _check_swap_references(
    shot, path: str, report: "ValidationReport", available_characters
) -> None:
    """A set/tween on a non-transform property must name a declared asset set
    and key of its target entity's descriptor, and a `play` must resolve
    against that descriptor's animations — checked HERE, before the author
    pays for TTS or a Chromium launch, because compile raises on both
    (an#87, an#7). Same charter as `_check_renderable`; needs the store, so
    it runs from `validate_semantic`'s shot loop — and ONLY then: with
    `available_characters=None` neither check runs, so a bare
    `validate_semantic(scene)` passes a play the compiler will refuse.

    Descriptor-less (procedural) entities get a carve-out for `viseme` — the
    compiler validates its codes against the drawn-mouth shapes — and an
    error for anything else, matching the compiler's verdicts.
    """
    if available_characters is None:
        return
    refs_by_entity = {e.id: e.ref for e in shot.entities if e.kind == "character"}
    # `play` (an#7): resolved against the target entity's MIGRATED descriptor
    # by `an.characters.play` — the SAME code the compiler resolves with, so
    # validate's verdict is compile's (an unknown bone property, a bone with
    # no slot of its own, art missing for a frame, a face slot suppressed by
    # `face_overlay=false` all used to pass here and raise there). Art is
    # checked when the store has a filesystem root; a dict store assumes
    # presence, as the compiler's part probe does.
    for k, action in enumerate(shot.actions):
        for flat in flatten(action):
            leaf = flat.action
            if getattr(leaf, "kind", None) != "play":
                continue
            entity_id = (getattr(leaf, "target", "") or "").split("/", 1)[0]
            ref = refs_by_entity.get(entity_id)
            desc = None
            if ref is not None:
                try:
                    candidate = available_characters[ref]
                except (KeyError, TypeError):
                    candidate = None
                if (
                    isinstance(candidate, dict)
                    and candidate.get("kind") == "CharacterDescriptor"
                ):
                    desc = CharacterDescriptor.model_validate(
                        migrate(dict(candidate), kind="CharacterDescriptor")
                    )
            if desc is None:
                report.add(
                    "error",
                    f"{path}/actions/{k}",
                    f"`play` names animation {leaf.animation!r} on {entity_id!r}, "
                    "which has no descriptor — named animations live in a "
                    "character's descriptor `animations`; compiling this shot raises.",
                )
                continue
            for problem in play_problems(
                desc,
                leaf.animation,
                art_exists=art_exists_for(available_characters, ref),
            ):
                report.add(
                    "error",
                    f"{path}/actions/{k}",
                    f"`play` of {leaf.animation!r} on {entity_id!r} cannot "
                    f"resolve: {problem} — compiling this shot raises.",
                )
    # `expression` (an#98) and the dialogue `[emotion]` sugar resolve through
    # `an.expression.binding.expression_problems` — the SAME function the face
    # solver raises with. An unknown preset used to be silence.
    for k, action in enumerate(shot.actions):
        for flat in flatten(action):
            leaf = flat.action
            if getattr(leaf, "kind", None) != "expression":
                continue
            entity_id = (getattr(leaf, "target", "") or "").split("/", 1)[0]
            desc = _descriptor_for(refs_by_entity.get(entity_id), available_characters)
            for problem in expression_problems(
                desc, preset=leaf.preset, axes=leaf.axes, who=entity_id
            ):
                report.add(
                    "error",
                    f"{path}/actions/{k}",
                    f"`expression` on {entity_id!r} cannot resolve: {problem} — "
                    "compiling this shot raises.",
                )
    for j, line in enumerate(shot.dialogue):
        emotion = (line.emotion or "").strip().lower()
        if not emotion:
            continue
        desc = _descriptor_for(refs_by_entity.get(line.speaker), available_characters)
        for problem in expression_problems(None, preset=emotion, who=line.speaker):
            report.add("error", f"{path}/dialogue/{j}/emotion", problem)
        if desc is not None and not desc.face_overlay:
            report.add(
                "warning",
                f"{path}/dialogue/{j}/emotion",
                f"{line.speaker!r} has its face baked into the head art "
                "(face_overlay: false), so the [emotion] on this line moves "
                "nothing; the audio still plays.",
            )
    # Flattened, like the compiler: the documented `start:` idiom wraps every
    # leaf in a `sequence`, so walking only top-level actions would miss the
    # common case (an#87 review) — an authoring-time gate that only sees the
    # top level is a gate with a hole in it.
    leaves = [
        (k, flat.action)
        for k, action in enumerate(shot.actions)
        for flat in flatten(action)
    ]
    for k, action in leaves:
        prop = getattr(action, "property", None)
        if prop is None or prop in _TRANSFORM_PROPERTIES:
            continue
        target = getattr(action, "target", "") or ""
        entity_id = target.split("/", 1)[0]
        ref = refs_by_entity.get(entity_id)
        desc = None
        if ref is not None:
            try:
                candidate = available_characters[ref]
            except (KeyError, TypeError):
                candidate = None
            if (
                isinstance(candidate, dict)
                and candidate.get("kind") == "CharacterDescriptor"
            ):
                # The MIGRATED document, as the compiler reads it: every
                # committed pre-0.3.0 descriptor has no `asset_sets` on disk
                # (0.1.0 carries `viseme_map`; `eyelid` is migration-seeded),
                # so the raw dict would refuse swaps the compiler accepts.
                desc = migrate(dict(candidate), kind="CharacterDescriptor")
        if desc is None:
            if prop not in _PROCEDURAL_SWAP_SETS:
                report.add(
                    "error",
                    f"{path}/actions/{k}",
                    f"property {prop!r} is not a transform, and "
                    f"{entity_id!r} has no descriptor declaring asset sets — "
                    "compiling this shot raises. Procedural rigs support "
                    f"exactly {sorted(_PROCEDURAL_SWAP_SETS)} on their mouth.",
                )
            continue
        declared = desc.get("asset_sets") or {}
        if prop not in declared:
            report.add(
                "error",
                f"{path}/actions/{k}",
                f"property {prop!r} names no declared asset set of "
                f"{entity_id!r} (it has: {sorted(declared)}) — compiling "
                "this shot raises.",
            )
            continue
        keys = declared.get(prop) or {}
        values = [
            v
            for v in (
                getattr(action, "value", None),
                getattr(action, "from_value", None),
                getattr(action, "to_value", None),
            )
            if v is not None
        ]
        for v in values:
            if not isinstance(v, str) or v not in keys:
                report.add(
                    "error",
                    f"{path}/actions/{k}",
                    f"{v!r} is not a declared key of {entity_id!r}'s "
                    f"{prop!r} set (it has: {sorted(keys)}) — compiling "
                    "this shot raises.",
                )


def _descriptor_for(ref, available_characters) -> CharacterDescriptor | None:
    """The MIGRATED descriptor a store holds for ``ref``, or ``None``."""
    if ref is None or available_characters is None:
        return None
    try:
        candidate = available_characters[ref]
    except (KeyError, TypeError):
        return None
    if isinstance(candidate, dict) and candidate.get("kind") == "CharacterDescriptor":
        return CharacterDescriptor.model_validate(
            migrate(dict(candidate), kind="CharacterDescriptor")
        )
    return None


def _check_renderable(shot, path: str, report: "ValidationReport") -> None:
    """Report, at validate time, what compile and render will refuse.

    This is where these checks belong. The compiler and the runtime both refuse
    these scenes now, which is right — but they refuse them *after* the author
    has paid for TTS synthesis or a Chromium launch, and `an validate` is the
    free pre-flight that `iterate()` also runs after applying a model's patches.
    A validator that says "passed" about a scene that cannot render is worse
    than no validator, because it is trusted.

    Severity is `error` wherever the pipeline raises, so validate's verdict and
    the pipeline's verdict agree. A validator that disagrees with the thing it
    predicts is its own defect.
    """
    if shot.camera is not None and shot.camera.move:
        if shot.camera.move not in _RENDERABLE_CAMERA_MOVES:
            report.add(
                "error",
                f"{path}/camera/move",
                f"camera.move={shot.camera.move!r} is not implemented by the "
                f"cutout renderer (it has: {sorted(_RENDERABLE_CAMERA_MOVES)}). "
                "Rendering this shot raises.",
            )

    for j, entity in enumerate(shot.entities):
        if (
            entity.kind not in _DRAWABLE_ENTITY_KINDS
            and entity.kind not in _CONFIGURING_ENTITY_KINDS
        ):
            report.add(
                "error",
                f"{path}/entities/{j}",
                f"entity kind {entity.kind!r} is declared by the IR but not drawn "
                "by the cutout renderer. Rendering this shot raises.",
            )

    if shot.narration:
        report.add(
            "error",
            f"{path}/narration",
            f"{len(shot.narration)} narration line(s): the audio pipeline walks "
            "shot.dialogue only, so narration produces neither audio nor video. "
            "Rendering this shot raises. Use a dialogue line with an off-screen "
            "speaker as the workaround.",
        )


def _check_step_hz(
    step_hz: float | None, *, fps: int, path: str, report: "ValidationReport"
) -> None:
    """``0 < step_hz <= fps`` (an#89): a pose grid finer than the frame rate
    cannot be shown, and zero or negative is not a rate. The schema already
    refuses ``<= 0`` (``Field(gt=0)``) and the compiler re-checks the whole
    range, because a render never runs validate."""
    if step_hz is None or fps <= 0:  # fps <= 0 is already its own error
        return
    if not (0 < step_hz <= fps):
        report.add(
            "error",
            path,
            f"step_hz must satisfy 0 < step_hz <= fps ({fps}); got {step_hz!r}. "
            f"At {fps} fps, {fps / 2:g} is 'on twos' and {fps / 3:g} 'on threes'.",
        )


def validate_semantic(
    scene: SceneIR,
    *,
    available_voices: Mapping[str, Any] | None = None,
    available_characters: Mapping[str, Any] | None = None,
) -> ValidationReport:
    """Cross-field semantic checks. Pass live stores in for cross-store checks.

    Both ``available_voices`` and ``available_characters`` accept any mapping.
    Voices are consulted via ``__contains__`` only; characters additionally
    via ``__getitem__`` (the swap-reference and `play` checks read descriptor
    dicts, an#87 / an#7). Pass ``None`` to skip those checks — and know that
    skipping them is what it sounds like: a `play` or a swap the compiler
    will refuse passes silently without the store (the CLI, `an validate`,
    always passes it).
    """
    report = ValidationReport()

    if scene.meta.duration < 0:
        report.add("error", "meta/duration", "duration must be non-negative")
    if scene.meta.fps <= 0:
        report.add("error", "meta/fps", "fps must be positive")
    _check_step_hz(
        scene.meta.step_hz, fps=scene.meta.fps, path="meta/step_hz", report=report
    )
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
        _check_step_hz(
            shot.step_hz, fps=scene.meta.fps, path=f"{path}/step_hz", report=report
        )
        if not shot.id:
            report.add("error", f"{path}/id", "shot id may not be empty")
        elif shot.id in seen_shot_ids:
            report.add("error", f"{path}/id", f"duplicate shot id: {shot.id!r}")
        seen_shot_ids.add(shot.id)

        if shot.duration <= 0:
            report.add("error", f"{path}/duration", "shot duration must be > 0")

        _check_renderable(shot, path, report)
        _check_swap_references(shot, path, report, available_characters)

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
