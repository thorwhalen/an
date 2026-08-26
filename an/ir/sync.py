"""Bidirectional sync between ``scene.md`` (Narrative Layer) and ``ir/scene.json`` (Scene Graph Layer).

The Markdown form is what humans edit. The JSON form is what the agent and
verifiers operate on. They must round-trip cleanly.

Markdown convention (v0.1, kept simple — extended in P5):

    # <title>

    Optional prose intro (saved to meta.notes).

    ```yaml meta
    title: Park Bench
    duration: 45
    fps: 30
    ```

    ## Shot s1 (cutout)

    Optional prose direction for this shot.

    ```yaml shot
    duration: 15
    camera:
      move: push_in
    ```

    ```dialogue
    charlie: Did you ever wonder why we always meet here?
    maya: Because the pigeons trust us.
    ```

A shot heading is ``## Shot <id> (<renderer>)`` — the parenthesised word names
the RENDERER, and is captured positionally, so the heading is unchanged by the
an#106 rename. Fenced blocks attach to the
nearest enclosing scope. Unknown blocks are preserved as ``options`` so
agent extensions don't get clobbered on round-trip.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
import warnings
from pathlib import Path
from typing import Any

import yaml

from an.base import DEFAULT_DURATION
from pydantic import ValidationError

from an.ir.migrate import (
    SCENE_IR,
    DocumentMigrationError,
    migrate,
    readable_without_migration,
    version_tuple,
)
from an.ir.schema import AssetRef, Dialogue, Meta, SceneIR, Shot
from an.util import _read_text, _write_json, _write_text


_FENCE_RE = re.compile(
    r"^```(\w+)(?:\s+(\w+))?\s*\n(.*?)\n```", re.MULTILINE | re.DOTALL
)
_SHOT_HEADING_RE = re.compile(
    r"^##\s+Shot\s+(\S+)(?:\s+\(([^)]+)\))?\s*$", re.MULTILINE
)


class SceneMarkdownError(ValueError):
    """`scene.md` says something this build cannot read — a refusal, not a crash.

    Every parse refusal in this module raises it, so the CLI can tell "the
    human's file needs one edit" apart from "something broke". That
    distinction is the whole point of naming it: an#106's first pass widened
    the CLI's catch to bare ``ValueError`` to print these cleanly, which also
    swallowed ``json.JSONDecodeError`` and ``CutoutCompileError`` — both
    ``ValueError`` subclasses — and turned a failed render into exit 0.
    """


@dataclass(slots=True)
class SyncResult:
    """Outcome of a sync operation."""

    wrote_json: bool = False
    wrote_md: bool = False
    drift_warning: str | None = None


# -----------------------------------------------------------------------------
# Markdown → IR
# -----------------------------------------------------------------------------


def markdown_to_ir(md_text: str) -> SceneIR:
    """Parse the structured Markdown form of a scene into a SceneIR.

    >>> md = '''# Demo
    ...
    ... ```yaml meta
    ... title: Demo
    ... duration: 5
    ... ```
    ...
    ... ## Shot s1 (cutout)
    ...
    ... ```yaml shot
    ... duration: 5
    ... ```
    ...
    ... ```dialogue
    ... charlie: hi
    ... ```
    ... '''
    >>> scene = markdown_to_ir(md)
    >>> scene.meta.title
    'Demo'
    >>> scene.timeline[0].id
    's1'
    >>> scene.timeline[0].dialogue[0].text
    'hi'
    """
    title = _extract_title(md_text)

    # Split into segments: a "global" segment (before any ## Shot heading) and
    # one segment per shot heading.
    parts = _split_by_shots(md_text)
    global_text = parts["__global__"]

    meta_data = _extract_yaml_block(global_text, "meta") or {}
    if "default_style" in meta_data:
        # A REFUSAL, not a silent rename. `scene.md` is the human SSOT and
        # carries no schema version, so nothing here can tell "written before
        # an#106" from "typed today" — and `Meta` is `extra="allow"`, so
        # dropping it would leave the author's declared renderer silently
        # replaced by the default. The stored JSON is migrated instead; a
        # hand-edited md is the author's to fix, once.
        raise SceneMarkdownError(
            "`default_style:` in the meta block was renamed to `default_renderer:` "
            "(an#106): it names the RENDERER that draws the shots, not art "
            "direction. Rename the key."
        )
    if title and "title" not in meta_data:
        meta_data["title"] = title
    meta = Meta(**meta_data)

    shots: list[Shot] = []
    for shot_id, renderer, body in parts["__shots__"]:
        shot_yaml = _extract_yaml_block(body, "shot") or {}
        dialogue_block = _extract_dialogue_block(body, shot_id=shot_id)
        entities_block = _extract_entities_block(body)
        actions_block = _extract_actions_block(body)
        shot_kwargs: dict[str, Any] = {
            "id": shot_id,
            "renderer": renderer or meta.default_renderer,
            "duration": shot_yaml.get("duration", DEFAULT_DURATION),
            "dialogue": dialogue_block,
            "entities": entities_block,
            "actions": actions_block,
        }
        # Camera, options, etc., come straight from the YAML if present.
        if "camera" in shot_yaml:
            shot_kwargs["camera"] = _strip_retired_camera_fields(
                shot_yaml["camera"], shot_id=shot_id
            )
        if "options" in shot_yaml:
            shot_kwargs["options"] = shot_yaml["options"]
        # Whitelisted, like `camera`: this reader enumerates shot keys, so a
        # field added to `Shot` that is not named here silently drops on read
        # (and the writer above enumerates too, so on write) — an#89.
        if "step_hz" in shot_yaml:
            shot_kwargs["step_hz"] = shot_yaml["step_hz"]
        shots.append(Shot(**shot_kwargs))

    if meta.duration == 0.0:
        meta.duration = sum(s.duration for s in shots)

    return SceneIR(meta=meta, timeline=shots)


def _extract_title(md_text: str) -> str:
    for line in md_text.splitlines():
        line = line.strip()
        if line.startswith("# ") and not line.startswith("## "):
            return line[2:].strip()
    return ""


#: What a camera block's dead fields defaulted to, so a value someone actually
#: TYPED can be told apart from one the writer emitted.
_RETIRED_CAMERA_DEFAULTS: dict[str, Any] = {
    "position": [0.0, 0.0, 0.0],
    "target": [0.0, 0.0, 0.0],
    "focal_length": 50.0,
}


def _strip_retired_camera_fields(camera: Any, *, shot_id: str) -> Any:
    """Drop an#109's removed camera fields from a `scene.md` camera block.

    The stored-JSON side is a registered migration; this is the same rule on
    the surface that carries no schema version. Dropped SILENTLY when the value
    is the default the writer emitted — every `scene.md` this package generated
    since 0.1.0 carries `position`, `target` and `focal_length`, and warning on
    all of them would be noise on exactly the documents that had nothing to do
    with it. A non-default value warns, because that one someone typed.

    Not a refusal, unlike an#106's `default_style:`. That rename would have
    silently replaced the author's declared RENDERER with the default; these
    fields selected nothing at all — they described a 3D camera this package has
    never had, and were read by nothing.
    """
    if not isinstance(camera, dict):
        return camera
    camera = dict(camera)
    authored = {}
    for field, default in _RETIRED_CAMERA_DEFAULTS.items():
        if field not in camera:
            continue
        value = camera.pop(field)
        if isinstance(default, list):
            if not (isinstance(value, (list, tuple)) and list(value) == default):
                authored[field] = value
        elif value != default:
            authored[field] = value
    if authored:
        warnings.warn(
            f"shot {shot_id!r}: camera {sorted(authored)} dropped — an#109 "
            "removed them because they described a 3D camera this package "
            "never had (the cutout camera is `root.pivot` plus `root.scale`). "
            "A non-default value was set, so this is said out loud.",
            stacklevel=3,
        )
    return camera


def _split_by_shots(md_text: str) -> dict[str, Any]:
    """Slice text into a global pre-section and per-shot sections."""
    matches = list(_SHOT_HEADING_RE.finditer(md_text))
    if not matches:
        return {"__global__": md_text, "__shots__": []}
    global_text = md_text[: matches[0].start()]
    shots: list[tuple[str, str | None, str]] = []
    for i, m in enumerate(matches):
        shot_id = m.group(1)
        renderer = m.group(2)
        body_start = m.end()
        body_end = matches[i + 1].start() if i + 1 < len(matches) else len(md_text)
        shots.append((shot_id, renderer, md_text[body_start:body_end]))
    return {"__global__": global_text, "__shots__": shots}


def _extract_yaml_block(text: str, label: str) -> dict[str, Any] | None:
    for m in _FENCE_RE.finditer(text):
        lang, lbl, body = m.group(1), m.group(2), m.group(3)
        if lang == "yaml" and lbl == label:
            data = yaml.safe_load(body) or {}
            if not isinstance(data, dict):
                raise SceneMarkdownError(f"YAML block {label!r} must be a mapping")
            return data
    return None


_DIALOGUE_LINE_RE = re.compile(
    r"^\s*(?P<speaker>[\w-]+)(?:\s*\[(?P<emotion>[\w-]+)\])?\s*:\s*(?P<text>.*?)\s*$"
)


def _extract_dialogue_block(text: str, *, shot_id: str | None = None) -> list[Dialogue]:
    """Parse a ```dialogue block.

    Each non-empty, non-comment line follows ``speaker[emotion]: text`` where
    the bracketed emotion is optional. Examples:

        charlie: Hello.
        charlie [happy]: Hello!
        maya [skeptical]: Sure.

    A line that matches none of those shapes is a **parse error**, not a skip:
    this parser used to drop it silently, and ``examples/promote_demo`` was mute
    for months because its one line read ``maya (warm): …`` (an#96).
    """
    out: list[Dialogue] = []
    for m in _FENCE_RE.finditer(text):
        lang, _lbl, body = m.group(1), m.group(2), m.group(3)
        if lang != "dialogue":
            continue
        for raw in body.splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            match = _DIALOGUE_LINE_RE.match(line)
            if not match:
                where = f"shot {shot_id!r}: " if shot_id else ""
                raise SceneMarkdownError(
                    f"{where}dialogue line {line!r} is not `speaker: text` or "
                    "`speaker [emotion]: text` — speaker ids are `[\\w-]+`, and "
                    "the emotion goes in square brackets. A line that does not "
                    "parse is refused rather than dropped, so a typo cannot "
                    "silence a character."
                )
            kwargs: dict[str, Any] = {
                "speaker": match.group("speaker").strip(),
                "text": match.group("text").strip(),
            }
            if match.group("emotion"):
                kwargs["emotion"] = match.group("emotion").strip().lower()
            out.append(Dialogue(**kwargs))
    return out


def _extract_entities_block(text: str) -> list[AssetRef]:
    """Parse a ```yaml entities block: a list of AssetRef-shaped dicts."""
    raw = _extract_yaml_list_block(text, "entities")
    if not raw:
        return []
    out: list[AssetRef] = []
    for item in raw:
        if not isinstance(item, dict):
            raise SceneMarkdownError(
                f"each entry under `yaml entities` must be a mapping; got {item!r}"
            )
        out.append(AssetRef(**item))
    return out


def _extract_actions_block(text: str) -> list:
    """Parse a ```yaml actions block: a list of leaf-action dicts.

    Supported entry shapes (one per item in the YAML list):
      - ``{kind: tween, target, property, to, duration, [from_], [easing], [start]}``
      - ``{kind: set,   target, property, value, [at]}`` — `at`, never
        `start`: a `set` is instantaneous, and `start` on one RAISES rather
        than being silently dropped as it was before an#108.
      - ``{kind: play,  target, animation, [duration], [speed], [loop], [start]}``
        — resolved at compile against the target entity's descriptor
        ``animations`` (an#7). ``loop`` omitted means the animation's own.

    A leaf action with a ``start`` key is wrapped in ``sequence(delay(start),
    action)`` so flatten yields the correct absolute time. ``set`` uses ``at``
    instead (built into the schema). Returns the list of authoring Actions.
    """
    raw = _extract_yaml_list_block(text, "actions")
    if not raw:
        return []
    # Lazy import to avoid a cycle (compose imports schema, schema imports nothing).
    from an.ir import compose as _compose

    out = []
    for i, item in enumerate(raw):
        if not isinstance(item, dict):
            raise SceneMarkdownError(
                f"each entry under `yaml actions` must be a mapping; got {item!r}"
            )
        kind = item.get("kind")
        start = (
            item.pop("start", None) if kind in ("tween", "play", "expression") else None
        )
        if kind == "tween":
            target = item["target"]
            property_ = item["property"]
            to = item["to"]
            duration = float(item["duration"])
            from_ = item.get("from_") if "from_" in item else item.get("from")
            easing = item.get("easing", "ease_in_out")
            action = _compose.tween(
                target,
                property_,
                to=to,
                duration=duration,
                from_=from_,
                easing=easing,
            )
        elif kind == "set":
            if "start" in item:
                # A REFUSAL, not an alias. `start` is the wrapper key for
                # actions that HAVE a duration — the parser turns it into
                # `sequence(delay(start), action)`. A `set` is instantaneous,
                # so its time IS `at`, and giving one number two names is how
                # a scene ends up with both.
                #
                # It was silently dropped before an#108: `start` is popped only
                # for tween/play/expression, and this branch reads `at` alone,
                # so `{kind: set, start: 1.0}` compiled to a swap at t=0. The
                # author sees a lamp that is lit from the first frame and no
                # message anywhere.
                raise SceneMarkdownError(
                    f"actions[{i}] is a `set` with `start: {item['start']!r}`, "
                    "which does nothing: a `set` is instantaneous and its time "
                    f"is `at:`. Write `at: {item['start']!r}`."
                )
            action = _compose.set_(
                item["target"],
                item["property"],
                item["value"],
                at=float(item.get("at", 0.0)),
            )
        elif kind == "play":
            # `{kind: play, target, animation, [duration], [speed], [loop],
            # [start]}` — resolved at compile against the target entity's
            # descriptor `animations` (an#7). This reader accepted the shape
            # from the start, then #24 made it refuse (nothing resolved a
            # play) while the writer below kept emitting it — three days of
            # a project's own scene.md failing to parse, ended here.
            action = _compose.play(
                item["target"],
                item["animation"],
                duration=(
                    float(item["duration"])
                    if item.get("duration") is not None
                    else None
                ),
                speed=float(item.get("speed", 1.0)),
                loop=(bool(item["loop"]) if item.get("loop") is not None else None),
            )
        elif kind == "expression":
            # `{kind: expression, target, [preset], [axes], [intensity],
            # [duration], [blend], [start]}` (an#98). Landed with its writer
            # and round trip in one commit: the writer skips unknown leaves
            # silently, so a parser-only entry would vanish from scene.md on
            # the next sync and then from the JSON on the next md edit.
            raw_axes = item.get("axes") or {}
            if not isinstance(raw_axes, dict):
                raise SceneMarkdownError(
                    f"actions[{i}].axes must be a mapping; got {raw_axes!r}"
                )
            action = _compose.expression(
                item["target"],
                item.get("preset"),
                axes={str(k): float(v) for k, v in raw_axes.items()},
                intensity=float(item.get("intensity", 1.0)),
                duration=(
                    float(item["duration"])
                    if item.get("duration") is not None
                    else None
                ),
                blend=float(item["blend"])
                if item.get("blend") is not None
                else _compose.DFLT_EXPRESSION_BLEND_S,
            )
        else:
            raise SceneMarkdownError(
                f"actions[{i}].kind must be one of tween/set/play/expression; got {kind!r}"
            )
        if start is not None and float(start) > 0:
            action = _compose.sequence(_compose.delay(float(start)), action)
        out.append(action)
    return out


def _extract_yaml_list_block(text: str, label: str) -> list[Any] | None:
    """Parse a ```yaml <label> block whose body is a YAML list."""
    for m in _FENCE_RE.finditer(text):
        lang, lbl, body = m.group(1), m.group(2), m.group(3)
        if lang == "yaml" and lbl == label:
            data = yaml.safe_load(body)
            if data is None:
                return []
            if not isinstance(data, list):
                raise SceneMarkdownError(f"YAML block {label!r} must be a list")
            return data
    return None


# -----------------------------------------------------------------------------
# IR → Markdown
# -----------------------------------------------------------------------------


def ir_to_markdown(scene: SceneIR) -> str:
    """Render a SceneIR back into the structured Markdown form.

    >>> from an.ir.schema import SceneIR, Meta, Shot
    >>> scene = SceneIR(meta=Meta(title="Demo", duration=5.0),
    ...                 timeline=[Shot(id="s1", renderer="cutout", duration=5.0)])
    >>> md = ir_to_markdown(scene)
    >>> "# Demo" in md
    True
    >>> "## Shot s1 (cutout)" in md
    True
    """
    parts: list[str] = []
    title = scene.meta.title or "Untitled"
    parts.append(f"# {title}\n")

    meta_dict = {
        "title": scene.meta.title,
        "author": scene.meta.author,
        "duration": scene.meta.duration,
        "fps": scene.meta.fps,
        "resolution": {
            "width": scene.meta.resolution.width,
            "height": scene.meta.resolution.height,
        },
        "default_renderer": scene.meta.default_renderer,
    }
    if scene.meta.step_hz is not None:
        meta_dict["step_hz"] = scene.meta.step_hz
    # Written only when set, like `step_hz`: this writer ENUMERATES the meta
    # keys, so a field added to `Meta` and not named here silently drops on
    # write — which is the an#89 trap, and the reason a round-trip test is the
    # thing that catches it (an#112).
    if scene.meta.style_pack:
        meta_dict["style_pack"] = scene.meta.style_pack
    parts.append("```yaml meta")
    parts.append(yaml.safe_dump(meta_dict, sort_keys=False).rstrip())
    parts.append("```\n")

    if scene.meta.notes:
        parts.append(scene.meta.notes.rstrip() + "\n")

    for shot in scene.timeline:
        parts.append(f"## Shot {shot.id} ({shot.renderer})\n")
        shot_yaml: dict[str, Any] = {"duration": shot.duration}
        if shot.step_hz is not None:
            shot_yaml["step_hz"] = shot.step_hz
        if shot.camera is not None:
            shot_yaml["camera"] = shot.camera.model_dump(exclude_none=True)
        if shot.options:
            shot_yaml["options"] = shot.options
        parts.append("```yaml shot")
        parts.append(yaml.safe_dump(shot_yaml, sort_keys=False).rstrip())
        parts.append("```\n")
        if shot.entities:
            parts.append("```yaml entities")
            entities_dump = [
                e.model_dump(exclude_none=True, exclude_defaults=False)
                for e in shot.entities
            ]
            parts.append(yaml.safe_dump(entities_dump, sort_keys=False).rstrip())
            parts.append("```\n")
        if shot.actions:
            actions_dump = _actions_to_yaml_list(shot.actions)
            if actions_dump:
                parts.append("```yaml actions")
                parts.append(yaml.safe_dump(actions_dump, sort_keys=False).rstrip())
                parts.append("```\n")
        if shot.dialogue:
            parts.append("```dialogue")
            for line in shot.dialogue:
                if line.emotion:
                    parts.append(f"{line.speaker} [{line.emotion}]: {line.text}")
                else:
                    parts.append(f"{line.speaker}: {line.text}")
            parts.append("```\n")

    return "\n".join(parts).rstrip() + "\n"


def _actions_to_yaml_list(actions: list) -> list[dict]:
    """Convert authoring Action objects back to the markdown-friendly dicts.

    Only handles the leaf-action shapes that the markdown parser also accepts:
    set, tween, play, plus the ``sequence(delay(start), <leaf>)`` wrapper that
    the parser produces for actions with a ``start`` time. Composition trees
    that don't fit those shapes are skipped (logged via the JSON fallback —
    no data loss, just no markdown round-trip).
    """
    from an.ir.schema import (
        DFLT_EXPRESSION_BLEND_S,
        DelayAction,
        ExpressionAction,
        PlayAction,
        SequenceAction,
        SetAction,
        TweenAction,
    )

    out: list[dict] = []
    for action in actions:
        # Unwrap sequence(delay(start), leaf) → leaf with start.
        start = None
        leaf = action
        if (
            isinstance(action, SequenceAction)
            and len(action.children) == 2
            and isinstance(action.children[0], DelayAction)
        ):
            start = action.children[0].duration
            leaf = action.children[1]
        if isinstance(leaf, TweenAction):
            entry = {
                "kind": "tween",
                "target": leaf.target,
                "property": leaf.property,
                "to": leaf.to_value,
                "duration": leaf.duration,
            }
            if leaf.from_value is not None:
                entry["from"] = leaf.from_value
            if leaf.easing not in (None, "ease_in_out"):
                entry["easing"] = leaf.easing
            if start is not None:
                entry["start"] = start
            out.append(entry)
        elif isinstance(leaf, SetAction):
            entry = {
                "kind": "set",
                "target": leaf.target,
                "property": leaf.property,
                "value": leaf.value,
            }
            if leaf.at:
                entry["at"] = leaf.at
            out.append(entry)
        elif isinstance(leaf, PlayAction):
            entry = {
                "kind": "play",
                "target": leaf.target,
                "animation": leaf.animation,
            }
            if leaf.duration is not None:
                entry["duration"] = leaf.duration
            if leaf.speed != 1.0:
                entry["speed"] = leaf.speed
            if leaf.loop is not None:
                entry["loop"] = bool(leaf.loop)
            if start is not None:
                entry["start"] = start
            out.append(entry)
        elif isinstance(leaf, ExpressionAction):
            entry = {"kind": "expression", "target": leaf.target}
            if leaf.preset is not None:
                entry["preset"] = leaf.preset
            if leaf.axes:
                entry["axes"] = dict(leaf.axes)
            if leaf.intensity != 1.0:
                entry["intensity"] = leaf.intensity
            if leaf.duration is not None:
                entry["duration"] = leaf.duration
            if leaf.blend != DFLT_EXPRESSION_BLEND_S:
                entry["blend"] = leaf.blend
            if start is not None:
                entry["start"] = start
            out.append(entry)
        # else: skip (composition trees that don't round-trip cleanly to md).
    return out


# -----------------------------------------------------------------------------
# Disk-level sync
# -----------------------------------------------------------------------------


class SceneValidationError(ValueError):
    """A stored scene document is not a valid scene — named, with its source.

    The underlying :class:`pydantic.ValidationError` is kept as ``__cause__``
    (and as ``.validation_error``) so a caller that reports **per field** —
    ``an.validate_schema`` builds one Finding per error, each with its own
    ``loc`` — does not have to choose between naming the document and naming
    the field.
    """

    def __init__(self, message: str, validation_error=None) -> None:
        super().__init__(message)
        self.validation_error = validation_error


def scene_from_json_doc(doc: dict, *, source: str | Path | None = None) -> SceneIR:
    """Validate a stored scene document, **migrating it first** (an#105).

    Every path from stored bytes to a :class:`~an.ir.schema.SceneIR` goes
    through here — the store (read **and** write), ``sync()``'s two json-wins
    branches, and ``an.validate_schema``, which is a read path too because a
    dict or a JSON string handed to it *is* a stored document. A test walks the
    package's AST and fails on any other one. Before it existed, `migrate()` was called with
    `kind="CharacterDescriptor"` at every call site in the tree and with a
    scene at none of them — so a registered scene migration never ran, and
    because `SceneIR` is ``extra="allow"``, a renamed field would have landed
    as a **silent default** on every document already on disk. Registering a
    migration and never running it is worse than not registering one, because
    the registry reads as a promise.

    Three outcomes, three different repairs, so they get three messages:

    - a version this build reads (at or above ``COMPATIBLE_VERSION``, at or
      below ``SCHEMA_VERSION``) is taken **as-is** when no migration is
      registered for it — that is exactly what ``an/base.py`` promises, and a
      loader that demanded an exact match would refuse every stored project the
      day the version is bumped;
    - a version from the future is refused as *written by a newer build*,
      because nobody will ever register a downgrade;
    - anything else — an old version with no path, or a malformed field — is
      refused naming the document.

    Migration happens **on read, with no write-back**: the document on disk
    keeps its old version until something saves the scene, so a migration must
    stay registered for as long as any project might hold that version. That is
    deliberate — a loader that rewrote every file it opened would turn `an
    validate` into a mutation — but it means the registry only ever grows.

    >>> scene_from_json_doc({"version": "0.1.0", "meta": {"title": "t"}}).meta.title
    't'
    >>> scene_from_json_doc({"version": "0.0.1"}, source="ir/scene.json")
    ... # doctest: +IGNORE_EXCEPTION_DETAIL
    Traceback (most recent call last):
    ...
    DocumentMigrationError
    """
    where = str(source) if source is not None else "scene document"
    version = doc.get(SCENE_IR.version_field, SCENE_IR.current_version)
    try:
        migrated = migrate(doc, kind=SCENE_IR.name)
    except DocumentMigrationError as e:
        if readable_without_migration(version, SCENE_IR):
            migrated = dict(doc)  # the declared compat window; read it as it is
        elif (v := version_tuple(version)) is not None and v > version_tuple(
            SCENE_IR.current_version
        ):
            raise DocumentMigrationError(
                f"{where}: written by a newer build (schema {version!r}; this build "
                f"is {SCENE_IR.current_version!r}). Upgrade `an` rather than editing "
                "the document — a downgrade migration is never registered."
            ) from e
        elif v is None:
            raise DocumentMigrationError(
                f"{where}: {SCENE_IR.version_field!r} is {version!r}, which is not a "
                "schema version. Repair the field; the document itself may be fine."
            ) from e
        else:
            raise DocumentMigrationError(f"{where}: {e}") from e
    try:
        return SceneIR.model_validate(migrated)
    except ValidationError as e:
        # Named, like the migration refusal above: the common failure is a
        # corrupt FIELD, and a nameless pydantic traceback is exactly what this
        # boundary exists to replace (an#105 review).
        raise SceneValidationError(f"{where}: {e}", e) from e


def sync(project_dir: str | Path) -> SyncResult:
    """Reconcile ``scene.md`` and ``ir/scene.json`` inside a project directory.

    Strategy in v0.1: Markdown is the human SSOT; if both exist, the JSON is
    regenerated from the Markdown unless mtimes show JSON is newer (which the
    user is told never to do — but we warn instead of silently overwriting).
    """
    pdir = Path(project_dir)
    md_path = pdir / "scene.md"
    json_path = pdir / "ir" / "scene.json"
    result = SyncResult()

    md_exists = md_path.exists()
    json_exists = json_path.exists()

    if md_exists and not json_exists:
        scene = markdown_to_ir(_read_text(md_path))
        _write_json(json_path, json.loads(scene.model_dump_json()))
        result.wrote_json = True
    elif json_exists and not md_exists:
        data = json.loads(_read_text(json_path))
        scene = scene_from_json_doc(data, source=json_path)
        _write_text(md_path, ir_to_markdown(scene))
        result.wrote_md = True
    elif md_exists and json_exists:
        # Use the newer file as source of truth. Markdown is the *human* SSOT,
        # but pipeline stages (audio, lip-sync) write rich state into the JSON
        # that the Markdown can't represent — so when JSON is newer, prefer it.
        # Tolerance: skew within 0.5s is treated as "same" (avoid flip-flopping
        # on every load just because of write-order in ScenesStore).
        md_mtime = md_path.stat().st_mtime
        json_mtime = json_path.stat().st_mtime
        skew = json_mtime - md_mtime
        if skew > 0.5:
            data = json.loads(_read_text(json_path))
            scene = scene_from_json_doc(data, source=json_path)
            _write_text(md_path, ir_to_markdown(scene))
            # Equalize mtimes so this regen doesn't immediately flip the next
            # sync into "md is newer → regenerate json (losing pipeline state)".
            import os

            os.utime(md_path, (json_mtime, json_mtime))
            result.wrote_md = True
        elif skew < -0.5:
            scene = markdown_to_ir(_read_text(md_path))
            _write_json(json_path, json.loads(scene.model_dump_json()))
            import os

            os.utime(json_path, (md_mtime, md_mtime))
            result.wrote_json = True
        # else: within tolerance, no rewrite needed.
    return result
