"""Run the promote → render pipeline for this example.

What this script does:

1. Calls :func:`an.characters.promote.promote` on the hand-drawn SVG at
   ``assets/characters/raw_maya/raw_maya.svg``. Promote slices the SVG's
   named groups (``<g id="head">``, ``<g id="torso">``, ...) into part
   files and writes a ``character.json`` descriptor to
   ``assets/characters/maya-promoted/``.
2. Renders the project's ``scene.md`` (which references
   ``maya-promoted``) to an mp4 under ``output/``.

Run from the repo root:

    PYENV_VERSION=p12 python examples/promote_demo/build.py

The promote step is idempotent — re-running it with ``overwrite=True``
re-slices from the source SVG, so editing ``raw_maya.svg`` and re-running
this script regenerates the rig.
"""

from __future__ import annotations

from pathlib import Path

from an.characters import promote
from an.orchestrate import render_project


PROJECT_DIR = Path(__file__).resolve().parent
SOURCE_ENTITY = "raw_maya"
PROMOTED_AS = "maya-promoted"


def main() -> None:
    """Promote the hand-drawn SVG, then render the scene."""
    desc_path = promote(
        PROJECT_DIR,
        entity=SOURCE_ENTITY,
        as_=PROMOTED_AS,
        overwrite=True,
    )
    print(f"promoted: {desc_path}")
    print(f"  source SVG → sliced parts under {desc_path.parent / 'parts'}/")

    output_path = render_project(PROJECT_DIR)
    print(f"rendered: {output_path}")


if __name__ == "__main__":
    main()
