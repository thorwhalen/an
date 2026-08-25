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

import json
from dataclasses import dataclass, field
from typing import Any, Literal, Mapping

from pydantic import ValidationError

from an.base import TRANSFORM_PROPERTIES
from an.characters.play import art_exists_for, play_problems
from an.characters.schema import CharacterDescriptor
from an.expression.binding import expression_problems
from an.ir.camera import CAMERA_MOVES, CameraError, camera_keys
from an.ir.compose import flatten
from an.ir.migrate import DocumentMigrationError, migrate
from an.ir.sync import SceneValidationError, scene_from_json_doc
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
        # A dict or a JSON string is a STORED document, so it is migrated first
        # (an#105 review): without this, "validate before you spend" told an
        # agent a stale project was clean, because `extra="allow"` accepts the
        # pre-migration shape and defaults the new fields.
        raw = json.loads(doc) if isinstance(doc, str) else doc
        if not isinstance(raw, dict):
            report.add(
                "error",
                "<root>",
                f"a scene document must be an object, not {type(raw).__name__}",
            )
            return report
        scene_from_json_doc(raw)
    except DocumentMigrationError as e:
        report.add("error", "version", str(e))
    except SceneValidationError as e:
        # Per FIELD, not one finding for the document: `Finding.ir_path` is what
        # routes a fix to the layer that can make it (CLAUDE.md pillar 10).
        underlying = e.validation_error
        if underlying is None:
            report.add("error", "<root>", str(e))
        else:
            for err in underlying.errors():
                loc = "/".join(str(x) for x in err.get("loc", ()))
                report.add("error", loc or "<root>", err.get("msg", "validation error"))
    except ValidationError as e:
        for err in e.errors():
            loc = "/".join(str(x) for x in err.get("loc", ()))
            report.add("error", loc or "<root>", err.get("msg", "validation error"))
    return report


# -----------------------------------------------------------------------------
# Semantic layer
# -----------------------------------------------------------------------------


#: Camera moves the renderer implements. `hold` is a real no-op.
#:
#: **Derived, not duplicated.** This was a hand-maintained frozenset reconciled
#: with the compiler's table by a test — which works, and is not what "a move
#: that validates cannot then raise" means; that means ONE table (an#109
#: review, H-1). It moved to `an.ir.camera`, which is the IR layer, so validate
#: can import it without depending on an adapter.
_RENDERABLE_CAMERA_MOVES: frozenset[str] = frozenset(CAMERA_MOVES)

#: Entity kinds the cutout renderer draws. `voice` is legitimately
#: not drawable — they configure the render rather than appearing in it.
#: an#108: `prop` moved from "declared by the IR but not drawn" to drawn.
#: validate's verdict IS compile's, so this set and the compiler's
#: entity dispatch are pinned equal by test — a validator that passes a
#: scene the compiler refuses is worse than no validator, because it is
#: trusted.
_DRAWABLE_ENTITY_KINDS: frozenset[str] = frozenset({"character", "environment", "prop"})
_CONFIGURING_ENTITY_KINDS: frozenset[str] = frozenset({"voice"})

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


#: Entity kind → (the mall store holding its rig, the descriptor `kind` tag
#: that store's documents carry). `environment` and `voice` are absent because
#: neither has a rig to declare asset sets on.
RIG_STORES: dict[str, tuple[str, str]] = {
    "character": ("characters", "CharacterDescriptor"),
    "prop": ("props", "PropDescriptor"),
}


def _rig_document(entity, stores: Mapping[str, Any]) -> dict | None:
    """The MIGRATED descriptor behind ``entity``, or ``None``.

    Migrated because that is how the compiler reads it: every committed
    pre-0.3.0 character descriptor has no `asset_sets` on disk (0.1.0 carries
    `viseme_map`; `eyelid` is migration-seeded), so the raw dict would refuse
    swaps the compiler accepts.

    Keyed on the entity's KIND rather than on "is it a character", so a prop's
    descriptor is found in the props store (an#108). It also refuses a
    document of the wrong kind in the right store — a `CharacterDescriptor`
    under `assets/props/` is not a prop, and the compiler says so too.
    """
    try:
        store_name, want_kind = RIG_STORES[entity.kind]
    except KeyError:
        return None
    store = stores.get(store_name)
    if store is None:
        return None
    try:
        candidate = store[entity.ref]
    except (KeyError, TypeError):
        return None
    if isinstance(candidate, dict) and candidate.get("kind") == want_kind:
        return migrate(dict(candidate), kind=want_kind)
    return None


def _check_swap_references(
    shot, path: str, report: "ValidationReport", stores: Mapping[str, Any]
) -> None:
    """A set/tween on a non-transform property must name a declared asset set
    and key of its target entity's descriptor, and a `play` must resolve
    against that descriptor's animations — checked HERE, before the author
    pays for TTS or a Chromium launch, because compile raises on both
    (an#87, an#7). Same charter as `_check_renderable`; needs the store, so
    it runs from `validate_semantic`'s shot loop — and ONLY then: with
    no stores neither check runs, so a bare `validate_semantic(scene)` passes
    a play the compiler will refuse.

    ``stores`` is keyed by MALL NAME, not by entity kind, and `RIG_STORES`
    maps between them — because an#108 gave props the same rig machinery, and
    a check that only knows how to find a *character's* descriptor reports
    "no descriptor declaring asset sets" for a lamp whose descriptor is right
    there in the props store.

    Descriptor-less (procedural) entities get a carve-out for `viseme` — the
    compiler validates its codes against the drawn-mouth shapes — and an
    error for anything else, matching the compiler's verdicts.
    """
    if not stores:
        return
    # PER-KIND, not per-call. an#108's first pass changed the gate from
    # "characters store absent → skip" to "no stores at all → skip", which made
    # `validate_semantic(scene, available_characters=X)` — the signature every
    # caller outside this repo has — report EVERY prop swap as
    # "has no descriptor declaring asset sets" on a scene that compiles fine.
    # A store that was not supplied means the check did not run for that kind;
    # it never means the descriptor is missing.
    rigs = {e.id: e for e in shot.entities if e.kind in RIG_STORES}
    #: Entities whose rig store was not supplied. Their checks are SKIPPED, not
    #: failed: "no store" and "no descriptor" are different facts, and reporting
    #: the first as the second is how a caller who passes only characters gets
    #: an error on every prop swap in a scene that compiles fine.
    unchecked = {
        eid for eid, e in rigs.items() if stores.get(RIG_STORES[e.kind][0]) is None
    }
    # The expression and dialogue-emotion checks below are CHARACTER-only:
    # a prop has no face.
    refs_by_entity = {e.id: e.ref for e in shot.entities if e.kind == "character"}
    available_characters = stores.get("characters")
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
            if entity_id in unchecked:
                continue
            entity = rigs.get(entity_id)
            doc = _rig_document(entity, stores) if entity is not None else None
            # `play` resolves against a CHARACTER's animations. A prop has an
            # `animations` field so the shared rig builder can read the same
            # attribute on either document, but nothing seeds it and no author
            # tool writes one — so a `play` on a prop lands here with the same
            # "no descriptor" verdict the compiler gives it.
            desc = (
                CharacterDescriptor.model_validate(doc)
                if doc is not None and entity.kind == "character"
                else None
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
                art_exists=art_exists_for(stores.get("characters"), entity.ref),
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
            if entity_id not in refs_by_entity:
                report.add(
                    "error",
                    f"{path}/actions/{k}",
                    f"`expression` targets {entity_id!r}, which is not a character "
                    f"entity of this shot (entities: {sorted(refs_by_entity) or 'none'}) "
                    "— it would compile to nothing.",
                )
                continue
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
        if entity_id in unchecked:
            continue
        entity = rigs.get(entity_id)
        desc = _rig_document(entity, stores) if entity is not None else None
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
        # …and the ART has to be there. The compiler registers only the
        # attachments whose files resolve and then refuses a key whose art is
        # missing, so a key that is DECLARED but undrawable passed validate and
        # raised at compile — with `strict_assets` either way (an#108 review,
        # H3). Same rule the rig builder's probe uses: a store with no
        # filesystem root can answer nothing, so it must assume presence rather
        # than drop every key.
        art_exists = art_exists_for(stores.get(RIG_STORES[entity.kind][0]), entity.ref)
        if art_exists is not None:
            skin = (desc.get("skins") or {}).get("default") or {}
            slots = skin.get("slots") or {}
            drawable = {
                key
                for key, attachment_name in keys.items()
                if any(
                    art_exists(att["path"])
                    for atts in slots.values()
                    if attachment_name in atts
                    for att in (atts[attachment_name],)
                    if isinstance(att, dict) and att.get("path")
                )
            }
            keys = {k_: v_ for k_, v_ in keys.items() if k_ in drawable}
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


def _check_camera(shot, path: str, report: "ValidationReport") -> None:
    """Every way a camera can fail to render, reported for free (an#109).

    The rule is `_check_renderable`'s: **error wherever the pipeline raises**,
    so validate's verdict and the compiler's agree — and it is kept by calling
    the compiler's own resolver rather than restating its rules. The one
    finding that is NOT a raise is the last, and it is a warning: too few keys
    is a pose rather than a move, which renders as if the camera were absent.
    Nothing breaks, so nothing fails; the author still wrote a camera block
    that does nothing, so it is still worth saying.

    ``width``/``height`` are 1 because nothing here reads the resolved
    distance — only whether resolving RAISES. Passing the real canvas would
    make the check depend on a resolution the IR layer does not have.
    """
    camera = shot.camera
    if camera is None:
        return
    # Every refusal, from the resolver the COMPILER uses. Not a second copy of
    # its rules: `camera_keys` raises exactly where compiling raises, so this
    # cannot drift from what it predicts (an#109 review, H-1).
    try:
        keys = camera_keys(shot, width=1, height=1)
    except CameraError as e:
        report.add("error", path, f"{e} Rendering this shot raises.")
        return
    if camera.keys is not None and len(keys) < 2:
        report.add(
            "warning",
            f"{path}/keys",
            f"{len(keys)} camera key(s) is a pose, not a move: the compiler "
            "needs two to interpolate between, so this renders as if the "
            "camera were absent. Add a second key, or remove the camera.",
        )


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
    if shot.camera is not None:
        _check_camera(shot, f"{path}/camera", report)

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


#: Keys an#106 retired, and what to write instead. `SceneIR`'s models are
#: `extra="allow"` (deliberately — forward compatibility), so a document that
#: still carries one of these validates cleanly and renders with the DEFAULT
#: renderer. The migration rewrites stored 0.1.x documents, but nothing rewrites
#: a document that is already 0.2.0: an agent patch, a hand edit, or a caller
#: passing `style=` to `Shot(...)` all produce a permanently dead key that no
#: later migration will touch. So it is caught here, at ERROR, by name.
RETIRED_KEYS: dict[str, dict[str, str]] = {
    "meta": {"default_style": "default_renderer"},
    "shot": {"style": "renderer"},
}

#: an#109's removed camera fields. A WARNING, not an error, and the difference
#: is the harm: a surviving `style` silently picks the wrong RENDERER, while
#: these three selected nothing — they described a 3D camera this package never
#: had. What is left is dead weight in a file, so it is worth saying and not
#: worth failing over.
#:
#: Reported at all because the migration cannot reach them: a document already
#: at the current version is never migrated again, so a camera block that came
#: through a sync between the version bump and this check keeps them forever as
#: `extra="allow"` extras, and nothing else looks.
RETIRED_CAMERA_KEYS: frozenset[str] = frozenset({"position", "target", "focal_length"})


def _check_retired_keys(scene: SceneIR, report: "ValidationReport") -> None:
    """One ERROR per retired key still present as an `extra`.

    >>> from an.ir.schema import Meta, SceneIR, Shot
    >>> scene = SceneIR(meta=Meta(), timeline=[Shot(id="s1", style="manim")])
    >>> report = ValidationReport()
    >>> _check_retired_keys(scene, report)
    >>> print(report.findings[0].description)
    `style` was renamed to `renderer` (an#106) and this value is being ignored...
    """
    for key, new in RETIRED_KEYS["meta"].items():
        if key in (scene.meta.model_extra or {}):
            report.add(
                "error",
                f"meta/{key}",
                f"`{key}` was renamed to `{new}` (an#106) and this value is "
                f'being ignored — the schema still ACCEPTS it (`extra="allow"`), '
                f"so nothing else will tell you. Rename it to `{new}`.",
            )
    for i, shot in enumerate(scene.timeline):
        camera_extra = getattr(shot.camera, "model_extra", None) or {}
        stale = sorted(RETIRED_CAMERA_KEYS & set(camera_extra))
        if stale:
            report.add(
                "warning",
                f"timeline[{i}]/camera",
                f"camera carries {stale}, removed in an#109 — they described a "
                "3D camera this package never had (the cutout camera is "
                "`root.pivot` plus `root.scale`) and are read by nothing. "
                "`an sync` drops them; they are harmless until then.",
            )
        for key, new in RETIRED_KEYS["shot"].items():
            if key in (shot.model_extra or {}):
                report.add(
                    "error",
                    f"timeline[{i}]/{key}",
                    f"`{key}` was renamed to `{new}` (an#106) and this value is "
                    f"being ignored — the shot will render with "
                    f"`renderer: {shot.renderer}`. Rename it to `{new}`.",
                )


def validate_semantic(
    scene: SceneIR,
    *,
    available_voices: Mapping[str, Any] | None = None,
    available_characters: Mapping[str, Any] | None = None,
    available_props: Mapping[str, Any] | None = None,
) -> ValidationReport:
    """Cross-field semantic checks. Pass live stores in for cross-store checks.

    Both ``available_voices`` and ``available_characters`` accept any mapping;
    ``available_props`` is the same thing for `kind="prop"` entities (an#108) —
    without it a prop's swaps are reported as having no descriptor, which is
    validate refusing what compile accepts.
    Voices are consulted via ``__contains__`` only; characters additionally
    via ``__getitem__`` (the swap-reference and `play` checks read descriptor
    dicts, an#87 / an#7). Pass ``None`` to skip those checks — and know that
    skipping them is what it sounds like: a `play` or a swap the compiler
    will refuse passes silently without the store (the CLI, `an validate`,
    always passes it).
    """
    report = ValidationReport()
    #: Only the stores actually supplied — an absent one skips its checks
    #: rather than reporting everything it would have found as missing.
    rig_stores = {
        name: store
        for name, store in (
            ("characters", available_characters),
            ("props", available_props),
        )
        if store is not None
    }

    _check_retired_keys(scene, report)
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
        _check_swap_references(shot, path, report, rig_stores)

        # Entity references resolve?
        for j, entity in enumerate(shot.entities):
            if entity.kind not in RIG_STORES:
                continue
            store_name, want_kind = RIG_STORES[entity.kind]
            store = rig_stores.get(store_name)
            if store is None:
                continue  # store not supplied → this check did not run
            if entity.kind == "character":
                # A WARNING: the compiler falls back to the built-in placeholder
                # rig and the scene still renders. Deliberately not escalated —
                # an asset-less project rendering placeholders is a supported
                # way to work.
                if entity.ref not in store:
                    report.add(
                        "warning",
                        f"{path}/entities/{j}",
                        f"character ref {entity.ref!r} not in characters store",
                    )
                continue
            # A prop has NO placeholder rig — the placeholder IS a humanoid, so
            # falling back would draw a person where the prop should be — which
            # makes an unresolvable prop a hard raise at compile. The pre-flight
            # for a hard raise is an ERROR, and before an#108's review this arm
            # did not exist at all: the harsher outcome had the weaker
            # prediction, and `an validate` said "passed" about a scene that
            # cannot render.
            if _rig_document(entity, rig_stores) is None:
                if entity.ref in store:
                    why = (
                        f"is in the {store_name!r} store but is not a "
                        f"{want_kind} (rendering this shot raises)"
                    )
                else:
                    why = (
                        f"is not in the {store_name!r} store, and a {entity.kind} "
                        "has no placeholder rig (rendering this shot raises)"
                    )
                report.add(
                    "error",
                    f"{path}/entities/{j}",
                    f"{entity.kind} ref {entity.ref!r} {why}",
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
