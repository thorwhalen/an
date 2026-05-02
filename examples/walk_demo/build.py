"""Build the walk_demo scene programmatically.

Phase 7 doesn't ship markdown `actions` syntax, so motion is authored in
Python. Run this script once to populate scene.md + ir/scene.json, then
``an render examples/walk_demo``.
"""

from __future__ import annotations

from pathlib import Path

from an import (
    AssetRef,
    Camera,
    Dialogue,
    Meta,
    Resolution,
    SceneIR,
    Shot,
    sequence,
    tween,
)
from an.project import init, load


def main() -> None:
    here = Path(__file__).resolve().parent
    init(here, force=True)
    proj = load(here)

    proj.scene = SceneIR(
        meta=Meta(
            title="Walk Demo",
            duration=4.0,
            fps=24,
            resolution=Resolution(width=640, height=360),
        ),
        timeline=[
            Shot(
                id="walk",
                style="cutout",
                duration=4.0,
                camera=Camera(move="push_in"),
                entities=[
                    AssetRef(
                        kind="character",
                        id="alpha",
                        store="characters",
                        ref="alpha-v1",
                    )
                ],
                actions=[
                    # Walk across the canvas: -250 → +250 over 3.5s,
                    # holding the last 0.5s for a beat.
                    sequence(
                        tween(
                            "alpha",
                            "x",
                            to=250.0,
                            from_=-250.0,
                            duration=3.5,
                            easing="ease_in_out",
                        ),
                    ),
                    # Subtle bob: torso tilts slightly back and forth.
                    sequence(
                        tween(
                            "alpha/torso",
                            "rotation",
                            to=0.05,
                            from_=-0.05,
                            duration=0.5,
                            easing="ease_in_out",
                        ),
                        tween(
                            "alpha/torso",
                            "rotation",
                            to=-0.05,
                            duration=0.5,
                            easing="ease_in_out",
                        ),
                        tween(
                            "alpha/torso",
                            "rotation",
                            to=0.05,
                            duration=0.5,
                            easing="ease_in_out",
                        ),
                        tween(
                            "alpha/torso",
                            "rotation",
                            to=-0.05,
                            duration=0.5,
                            easing="ease_in_out",
                        ),
                    ),
                ],
                dialogue=[
                    Dialogue(speaker="alpha", text="Off I go, on a quick stroll."),
                ],
            )
        ],
    )

    proj.mall["scenes"]["main"] = proj.scene
    print(f"wrote {here / 'scene.md'}")
    print(f"wrote {here / 'ir' / 'scene.json'}")
    print("now: an render", here)


if __name__ == "__main__":
    main()
