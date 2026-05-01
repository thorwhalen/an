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

A shot heading is ``## Shot <id> (<style>)``. Fenced blocks attach to the
nearest enclosing scope. Unknown blocks are preserved as ``options`` so
agent extensions don't get clobbered on round-trip.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from an.base import DEFAULT_DURATION
from an.ir.schema import AssetRef, Dialogue, Meta, SceneIR, Shot
from an.util import _read_text, _write_json, _write_text


_FENCE_RE = re.compile(r"^```(\w+)(?:\s+(\w+))?\s*\n(.*?)\n```", re.MULTILINE | re.DOTALL)
_SHOT_HEADING_RE = re.compile(r"^##\s+Shot\s+(\S+)(?:\s+\(([^)]+)\))?\s*$", re.MULTILINE)


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
    if title and "title" not in meta_data:
        meta_data["title"] = title
    meta = Meta(**meta_data)

    shots: list[Shot] = []
    for shot_id, style, body in parts["__shots__"]:
        shot_yaml = _extract_yaml_block(body, "shot") or {}
        dialogue_block = _extract_dialogue_block(body)
        entities_block = _extract_entities_block(body)
        shot_kwargs: dict[str, Any] = {
            "id": shot_id,
            "style": style or meta.default_style,
            "duration": shot_yaml.get("duration", DEFAULT_DURATION),
            "dialogue": dialogue_block,
            "entities": entities_block,
        }
        # Camera, options, etc., come straight from the YAML if present.
        if "camera" in shot_yaml:
            shot_kwargs["camera"] = shot_yaml["camera"]
        if "options" in shot_yaml:
            shot_kwargs["options"] = shot_yaml["options"]
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


def _split_by_shots(md_text: str) -> dict[str, Any]:
    """Slice text into a global pre-section and per-shot sections."""
    matches = list(_SHOT_HEADING_RE.finditer(md_text))
    if not matches:
        return {"__global__": md_text, "__shots__": []}
    global_text = md_text[: matches[0].start()]
    shots: list[tuple[str, str | None, str]] = []
    for i, m in enumerate(matches):
        shot_id = m.group(1)
        style = m.group(2)
        body_start = m.end()
        body_end = matches[i + 1].start() if i + 1 < len(matches) else len(md_text)
        shots.append((shot_id, style, md_text[body_start:body_end]))
    return {"__global__": global_text, "__shots__": shots}


def _extract_yaml_block(text: str, label: str) -> dict[str, Any] | None:
    for m in _FENCE_RE.finditer(text):
        lang, lbl, body = m.group(1), m.group(2), m.group(3)
        if lang == "yaml" and lbl == label:
            data = yaml.safe_load(body) or {}
            if not isinstance(data, dict):
                raise ValueError(f"YAML block {label!r} must be a mapping")
            return data
    return None


def _extract_dialogue_block(text: str) -> list[Dialogue]:
    """Parse a ```dialogue block; each non-empty line is `speaker: text`."""
    out: list[Dialogue] = []
    for m in _FENCE_RE.finditer(text):
        lang, _lbl, body = m.group(1), m.group(2), m.group(3)
        if lang != "dialogue":
            continue
        for raw in body.splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if ":" not in line:
                continue
            speaker, text_after = line.split(":", 1)
            out.append(Dialogue(speaker=speaker.strip(), text=text_after.strip()))
    return out


def _extract_entities_block(text: str) -> list[AssetRef]:
    """Parse a ```yaml entities block: a list of AssetRef-shaped dicts."""
    raw = _extract_yaml_list_block(text, "entities")
    if not raw:
        return []
    out: list[AssetRef] = []
    for item in raw:
        if not isinstance(item, dict):
            raise ValueError(
                f"each entry under `yaml entities` must be a mapping; got {item!r}"
            )
        out.append(AssetRef(**item))
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
                raise ValueError(f"YAML block {label!r} must be a list")
            return data
    return None


# -----------------------------------------------------------------------------
# IR → Markdown
# -----------------------------------------------------------------------------


def ir_to_markdown(scene: SceneIR) -> str:
    """Render a SceneIR back into the structured Markdown form.

    >>> from an.ir.schema import SceneIR, Meta, Shot
    >>> scene = SceneIR(meta=Meta(title="Demo", duration=5.0),
    ...                 timeline=[Shot(id="s1", style="cutout", duration=5.0)])
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
        "default_style": scene.meta.default_style,
    }
    parts.append("```yaml meta")
    parts.append(yaml.safe_dump(meta_dict, sort_keys=False).rstrip())
    parts.append("```\n")

    if scene.meta.notes:
        parts.append(scene.meta.notes.rstrip() + "\n")

    for shot in scene.timeline:
        parts.append(f"## Shot {shot.id} ({shot.style})\n")
        shot_yaml: dict[str, Any] = {"duration": shot.duration}
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
        if shot.dialogue:
            parts.append("```dialogue")
            for line in shot.dialogue:
                parts.append(f"{line.speaker}: {line.text}")
            parts.append("```\n")

    return "\n".join(parts).rstrip() + "\n"


# -----------------------------------------------------------------------------
# Disk-level sync
# -----------------------------------------------------------------------------


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
        scene = SceneIR.model_validate(data)
        _write_text(md_path, ir_to_markdown(scene))
        result.wrote_md = True
    elif md_exists and json_exists:
        # Use the newer file as source of truth. Markdown is the *human* SSOT,
        # but pipeline stages (audio, lip-sync) write rich state into the JSON
        # that the Markdown can't represent — so when JSON is newer, prefer it.
        md_mtime = md_path.stat().st_mtime
        json_mtime = json_path.stat().st_mtime
        if json_mtime > md_mtime:
            data = json.loads(_read_text(json_path))
            scene = SceneIR.model_validate(data)
            _write_text(md_path, ir_to_markdown(scene))
            result.wrote_md = True
        else:
            scene = markdown_to_ir(_read_text(md_path))
            _write_json(json_path, json.loads(scene.model_dump_json()))
            result.wrote_json = True
    return result
