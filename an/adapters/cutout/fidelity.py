"""How faithfully a compiled scene reproduces the art it was built from.

Wave 4's instrument (#9, `misc/docs/wave4_research.md` §2). One number per
sprite: the **aspect distortion**, the factor by which the compiler's box
disagrees with the art's own raster about shape.

Why this and not a rendered bounding box
----------------------------------------
The obvious instrument — render a frame, find the part's ink, measure its
extents — does not work. Measured on the repo's own art, the same ``arm_l``
render is 2, 4, 6 or 8 px wide depending only on the anti-aliasing threshold
chosen, so any tolerance asserted against it is really an assertion about a
threshold. The scale ratio below is exact, threshold-free, needs no browser and
no render at all, and reads straight off the two numbers that actually decide
the shape.

What it measures
----------------
``makeSvgSprite`` sets ``sprite.width`` and ``sprite.height`` independently
(``runtime.js:164-165``); PixiJS turns each into an independent axis scale.
So the sprite's two scale factors are

    sx = box_width  / raster_width
    sy = box_height / raster_height

and the art keeps its shape exactly when ``sx == sy``.
:attr:`PartFidelity.aspect_distortion` is ``max(sx, sy) / min(sx, sy)`` — 1.0
when the art is respected, and the factor by which it is squashed otherwise.

**The invariant this exists to enforce: aspect ratio is intrinsic to the art,
and the compiler may never override it.** A part is placed and uniformly
scaled, never stretched to fit a box.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

from an.characters.svg_utils import raster_size
from an.verify._base import Finding

#: Ratios within this of 1.0 count as uniform. Guards float noise only — it is
#: not a tolerance for "close enough", which is why it is this tight.
DFLT_ASPECT_TOLERANCE: float = 1.000_001

#: The visual kind whose art comes from a file and can therefore be distorted.
SPRITE_KIND: str = "svg_sprite"


@dataclass(frozen=True, slots=True)
class PartFidelity:
    """One sprite's box, its art's raster, and the disagreement between them."""

    node_path: str
    asset_id: str
    src: str
    box: tuple[float, float]
    raster: tuple[float, float]

    @property
    def scale_x(self) -> float:
        return self.box[0] / self.raster[0]

    @property
    def scale_y(self) -> float:
        return self.box[1] / self.raster[1]

    @property
    def aspect_distortion(self) -> float:
        """``max(sx, sy) / min(sx, sy)``; 1.0 exactly when the art is respected."""
        lo, hi = sorted((self.scale_x, self.scale_y))
        return hi / lo

    def is_uniform(self, *, tolerance: float = DFLT_ASPECT_TOLERANCE) -> bool:
        return self.aspect_distortion <= tolerance


def _walk(node: Any, prefix: str = "") -> Iterator[tuple[str, Any]]:
    """Yield ``(path, node)`` for every node in a compiled scene tree."""
    path = f"{prefix}/{node.name}" if prefix else node.name
    yield path, node
    for child in getattr(node, "children", None) or ():
        yield from _walk(child, path)


def part_fidelity(
    scene: Any,
    *,
    asset_root: Path | str,
    tolerance: float = DFLT_ASPECT_TOLERANCE,
) -> list[PartFidelity]:
    """Measure every ``svg_sprite`` in a compiled scene against its source art.

    ``asset_root`` is the directory a texture's ``src`` is relative to — the
    staged runtime directory, or the mall's characters-store root with the
    ``characters/`` prefix retained.

    Sprites whose art cannot be read are skipped rather than guessed at; a
    missing part is #76's problem, not this function's.
    """
    root = Path(asset_root)
    textures = getattr(scene.assets, "textures", {}) if scene.assets else {}
    out: list[PartFidelity] = []
    for node_path, node in _walk(scene.scene):
        visual = getattr(node, "visual", None)
        if visual is None or visual.kind != SPRITE_KIND:
            continue
        asset = textures.get(visual.asset_id)
        src = getattr(asset, "src", None) if asset is not None else None
        if not src:
            continue
        try:
            raster = raster_size(root / src)
        except (OSError, ValueError):
            continue
        out.append(
            PartFidelity(
                node_path=node_path,
                asset_id=visual.asset_id,
                src=src,
                box=(float(visual.width), float(visual.height)),
                raster=raster,
            )
        )
    return out


def aspect_findings(
    scene: Any,
    *,
    asset_root: Path | str,
    severity: str = "error",
    tolerance: float = DFLT_ASPECT_TOLERANCE,
) -> list[Finding]:
    """Report each non-uniformly scaled sprite as a typed :class:`Finding`.

    Uses the existing finding type so the orchestrator's error routing applies,
    and sets ``ir_path`` to the scene-graph node path so a fix is routed to the
    part that needs it.
    """
    return [
        Finding(
            severity=severity,  # type: ignore[arg-type]
            ir_path=part.node_path,
            description=(
                f"{part.node_path} scales its art non-uniformly by "
                f"{part.aspect_distortion:.3f}x: the compiler asks for "
                f"{part.box[0]:g}x{part.box[1]:g} while {part.src} rasterises at "
                f"{part.raster[0]:g}x{part.raster[1]:g} "
                f"(sx={part.scale_x:.5f}, sy={part.scale_y:.5f})."
            ),
            suggested_fix=(
                "Place and uniformly scale the part instead of sizing both axes. "
                "Aspect ratio is intrinsic to the art (#74)."
            ),
        )
        for part in part_fidelity(scene, asset_root=asset_root, tolerance=tolerance)
        if not part.is_uniform(tolerance=tolerance)
    ]
