"""Project init/load/save — the on-disk anatomy of an an project.

Layout (from spec §11):

    my-scene/
    ├── an.toml
    ├── scene.md
    ├── ir/scene.json
    ├── assets/{characters,environments,voices,styles}/
    ├── artifacts/{audio,visemes,shots,previews}/
    ├── output/
    └── .an/{decisions.jsonl,verifier_runs/,memory.md}
"""

from __future__ import annotations

import json
from collections.abc import MutableMapping
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from an.base import DEFAULT_FPS, DEFAULT_RESOLUTION
from an.ir.schema import Meta, Resolution, SceneIR
from an.ir.sync import ir_to_markdown, sync as sync_files
from an.stores import build_project_mall
from an.util import _read_text, _write_json, _write_text


_ANIMA_TOML_TEMPLATE = """# an project config
[project]
name = "{name}"
default_renderer = "cutout"

[render]
fps = {fps}
resolution = [{width}, {height}]

[providers]
tts = "elevenlabs"
lipsync = "rhubarb"
"""


@dataclass(slots=True)
class Project:
    """A loaded an project: directory + mall + current scene."""

    root: Path
    mall: Mapping[str, MutableMapping]
    scene: SceneIR


def init(
    project_dir: str | Path, *, name: str | None = None, force: bool = False
) -> Path:
    """Create a fresh an project at ``project_dir``.

    Idempotent unless the directory already contains a non-empty ``scene.md``;
    pass ``force=True`` to overwrite. Returns the absolute project root.
    """
    pdir = Path(project_dir).expanduser().resolve()
    proj_name = name or pdir.name

    if pdir.exists() and (pdir / "scene.md").exists() and not force:
        if (pdir / "scene.md").stat().st_size > 0:
            raise FileExistsError(
                f"{pdir / 'scene.md'} already exists with content; pass force=True to overwrite"
            )

    pdir.mkdir(parents=True, exist_ok=True)

    # Build the mall to materialize the directory tree.
    build_project_mall(pdir, ensure=True)

    # Seed scene.md with an empty SceneIR-equivalent.
    seed_scene = SceneIR(
        meta=Meta(
            title=proj_name,
            fps=DEFAULT_FPS,
            resolution=Resolution(
                width=DEFAULT_RESOLUTION[0], height=DEFAULT_RESOLUTION[1]
            ),
        )
    )
    _write_text(pdir / "scene.md", ir_to_markdown(seed_scene))
    _write_json(pdir / "ir" / "scene.json", json.loads(seed_scene.model_dump_json()))

    # Seed an.toml.
    toml_path = pdir / "an.toml"
    if not toml_path.exists() or force:
        toml_path.write_text(
            _ANIMA_TOML_TEMPLATE.format(
                name=proj_name,
                fps=DEFAULT_FPS,
                width=DEFAULT_RESOLUTION[0],
                height=DEFAULT_RESOLUTION[1],
            ),
            encoding="utf-8",
        )

    # Touch the agent-memory file so it's discoverable.
    memory_path = pdir / ".an" / "memory.md"
    if not memory_path.exists():
        memory_path.write_text(
            f"# Agent memory for {proj_name}\n\n"
            "Append running notes here across sessions.\n",
            encoding="utf-8",
        )

    return pdir


def load(project_dir: str | Path) -> Project:
    """Load an existing project. Reconciles scene.md / ir/scene.json first."""
    pdir = Path(project_dir).expanduser().resolve()
    if not pdir.exists():
        raise FileNotFoundError(f"no such project directory: {pdir}")

    # Sync md ↔ json so the loaded scene reflects the latest Markdown.
    sync_files(pdir)

    mall = build_project_mall(pdir, ensure=True)
    scene = mall["scenes"]["main"]
    return Project(root=pdir, mall=mall, scene=scene)


def save(project: Project) -> None:
    """Persist a Project's current scene back to disk (md + json)."""
    project.mall["scenes"]["main"] = project.scene
