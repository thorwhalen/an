"""Compile a top-level `Shot` (style="cutout") into a `CutoutSceneJSON`.

This is the bridge between the renderer-agnostic `an.ir` types and the
cutout-specific JSON contract that the JS runtime will consume in Phase 2B.

Strategy:

1. **Resolve entities** from the project mall: each `AssetRef` in
   ``shot.entities`` becomes a sub-tree of the cutout scene (a character with
   placeholder rect parts when the character store has no sidecar art yet).
2. **Flatten authoring actions** via `an.ir.compose.flatten` — every
   `tween`/`set`/`play`/composition produces leaf `FlatAction`s with
   absolute times.
3. **Compile each FlatAction to PlacedClipJSON entries** on the appropriate
   track. Tween → a 2-keyframe AnimationClipJSON + a PlacedClipJSON (or, under
   ``step_hz``, a grid of step-eased keyframes — an#89). Set →
   a step channel that HOLDS until the next action on the same
   target/property. Play → a per-instance clip (``__play__{n}``) resolved
   from the target's descriptor animation (an#7).

The compiler is deterministic and side-effect-free (it doesn't write to the
mall). It reads only.

>>> from an.ir.schema import Meta, SceneIR, Shot
>>> from an.adapters.cutout.compile import compile_shot
>>> shot = Shot(id="s1", style="cutout", duration=2.0)
>>> j = compile_shot(shot, mall={"characters": {}})
>>> j.timeline.duration
2.0
"""

from __future__ import annotations

import math
import warnings
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from an.base import TRANSFORM_PROPERTIES, swap_set_name_problem
from an.characters.play import (
    BoneTrack,
    PlayResolutionError,
    art_exists_for,
    drawn_attachment,
    primary_slot_per_bone,
    resolve_play,
    sampled_deviations,
    slot_node_path,
)
from an.ir.compose import FlatAction, flatten
from an.ir.schema import (
    Action,
    AssetRef,
    PlayAction,
    SetAction,
    Shot,
    TweenAction,
)

from an.adapters.cutout.serialize import (
    AnimationClipJSON,
    AssetJSON,
    AssetResolutionJSON,
    AssetsJSON,
    ChannelJSON,
    CutoutSceneJSON,
    CutoutSceneMetaJSON,
    KeyframeJSON,
    NodeJSON,
    PlacedClipJSON,
    TimelineJSON,
    TrackJSON,
    TransformJSON,
    VisualJSON,
)
from an.characters.schema import (
    CHARACTER_DOCUMENT_KIND,
    EYELID_CHANNEL,
    MOUTH_SHAPES,
    VISEME_CHANNEL,
    Attachment,
    Bone,
    CharacterDescriptor,
    Skin,
    Slot,
)
from an.characters.svg_utils import raster_size
from an.ir.migrate import migrate


# Default placeholder character: a recognizable stick-figure layout in pixel
# space so the demo is *visible* without art assets. Each entry pins a part to
# a (x, y) offset; colors come from a per-character palette so multiple
# characters look distinct. Used only when the characters store has no rig.
_PLACEHOLDER_PARTS: tuple[str, ...] = ("head", "torso", "left_arm", "right_arm")
_PLACEHOLDER_PART_GEOMETRY: dict[str, dict[str, float]] = {
    "head": {"x": 0.0, "y": -55.0, "width": 50.0, "height": 50.0},
    "torso": {"x": 0.0, "y": 0.0, "width": 60.0, "height": 80.0},
    "left_arm": {"x": -50.0, "y": -10.0, "width": 30.0, "height": 70.0},
    "right_arm": {"x": 50.0, "y": -10.0, "width": 30.0, "height": 70.0},
    "left_leg": {"x": -18.0, "y": 65.0, "width": 30.0, "height": 70.0},
    "right_leg": {"x": 18.0, "y": 65.0, "width": 30.0, "height": 70.0},
}

# Per-character color palettes. Each entry is (skin, clothing, hair). Picked
# deterministically from the entity.id so re-renders are stable.
_CHARACTER_PALETTES: tuple[tuple[str, str, str], ...] = (
    ("#f4c89a", "#3a6ea5", "#3b2a1a"),  # peach skin, blue clothes, dark hair
    ("#d8a47f", "#a83249", "#1a1a1a"),  # tan skin, red clothes, black hair
    ("#fbe1c1", "#2e7d4f", "#a8743f"),  # pale skin, green clothes, ginger
    ("#a87a5d", "#5b3a8a", "#2a2a2a"),  # darker skin, purple clothes, black
    ("#e8c39e", "#d97706", "#5e3a1f"),  # warm skin, orange clothes, brown
)


def _palette_for(entity_id: str) -> tuple[str, str, str]:
    """Deterministic (skin, clothing, hair) palette for a given entity id."""
    idx = sum(ord(c) for c in entity_id) % len(_CHARACTER_PALETTES)
    return _CHARACTER_PALETTES[idx]


# Cap viseme keyframe density to ~7Hz; reduces "twitchy" mouth at full speed.
_MIN_VISEME_GAP_S: float = 0.14

#: The procedural (drawn) mouth's swap vocabulary, DECLARED as data on its
#: visual exactly as the runtime declares it (`g._anDrawSets = {viseme: ...}`)
#: and as an SVG mouth carries its projection. A drawn mouth has no textures,
#: so each key maps to itself — the code the runtime's shape table draws. The
#: compiler never branches on the set's NAME: the drawn mouth is just a node
#: whose visual carries a `viseme` set (an#87).
PROCEDURAL_MOUTH_KEYS: dict[str, str] = {s.upper(): s.upper() for s in MOUTH_SHAPES}
PROCEDURAL_MOUTH_SETS: frozenset[str] = frozenset({VISEME_CHANNEL})

# -- Blinks (an#88): generated by the COMPILER, as ordinary channels ---------
#
# These were a runtime-only pass (`applyProceduralBlinks`) that matched eye
# nodes by regex and forced `scale.y` AFTER the pose every frame — which is why
# an authored eye `scale_y` could never reach the screen. They are channels
# now, on the entity's track ahead of everything authored, so later-wins
# evaluation lets an author override a blink like any other motion.
#
# The schedule keeps the runtime's exact rule — period, duration, depth, and
# the phase as a pure function of the entity NAME (stamped into the compiled
# scene's meta, because renaming a corpus character re-phases every blink and
# moves every pixel metric; that hazard is recorded, not fixed).
_BLINK_PERIOD_S: float = 4.0
_BLINK_DURATION_S: float = 0.14
_BLINK_DEPTH: float = 0.95
#: The nodes that blink, by name: the default rig's eye slots ARE its node
#: names, on both the procedural and the descriptor path.
EYE_NODE_NAMES: frozenset[str] = frozenset({"left_eye", "right_eye"})
#: Within a blink window, the eyelid swap shows CLOSED for the central half —
#: the span where the squash curve sits above 0.7 of full closure.
_EYELID_CLOSED_SPAN: tuple[float, float] = (0.25, 0.75)


def _js_string_hash(s: str) -> int:
    """Port of the runtime's ``_strHash`` — JS int32 ``(h << 5) - h + code``.

    >>> _js_string_hash("charlie") % 1000
    762
    """
    # JS iterates UTF-16 code units (`charCodeAt`), not code points: a
    # non-BMP character is two units. Encode the same way so the port stays
    # bit-identical for every entity id, not only ASCII/BMP ones.
    units = s.encode("utf-16-le")
    h = 0
    for i in range(0, len(units), 2):
        h = ((h << 5) - h) + int.from_bytes(units[i : i + 2], "little")
        h &= 0xFFFFFFFF
        if h >= 0x80000000:
            h -= 0x100000000
    return abs(h)


def blink_phase(entity_id: str) -> float:
    """The entity's blink phase in [0, 1): the runtime's rule, ported exactly.

    >>> blink_phase("charlie")
    0.762
    """
    return (_js_string_hash(entity_id) % 1000) / 1000.0


def _blink_windows(entity_id: str, duration: float) -> list[tuple[float, float]]:
    """``[(start, end), ...]`` of every blink overlapping ``[0, duration]``.

    The runtime blinked when ``(t + phase * P) % P < D``, i.e. at
    ``t = k*P - phase*P + [0, D)``.
    """
    offset = blink_phase(entity_id) * _BLINK_PERIOD_S
    out = []
    k = 0
    while True:
        start = k * _BLINK_PERIOD_S - offset
        end = start + _BLINK_DURATION_S
        if start > duration:
            break
        if end > 0.0:
            out.append((start, end))
        k += 1
    return out


def _property_rest_values() -> dict[str, float]:
    """Rest ("identity") value per animatable property, derived from the schema.

    A tween that declares no ``from_value`` starts from its property's rest
    value. This is not cosmetic: offsets and rotations rest at 0.0, but the
    *multiplicative* properties rest at 1.0, and defaulting all of them to 0.0
    silently breaks the two most obvious uses of a tween — a fade-out (``alpha``
    starting at 0 is already invisible, so the fade never happens and the
    element simply is not there) and a scale move (the subject pops in from
    nothing). Nothing had noticed because the camera builds its own explicit
    keyframes and no example authors a scale or alpha tween.

    **Derived, not restated.** A node's rest pose *is* ``TransformJSON``'s field
    defaults, so reading them off the model keeps one source of truth; a
    hand-maintained copy would be a second place to forget.
    """
    rest = {
        name: float(field.default)
        for name, field in TransformJSON.model_fields.items()
        if isinstance(field.default, (int, float))
        and not isinstance(field.default, bool)
    }
    # `rotation_rad` is an accepted alias for `rotation` in the runtime's
    # property switch; it is not a schema field, so it has to be added here
    # rather than derived.
    rest["rotation_rad"] = rest["rotation"]
    return rest


#: See :func:`_property_rest_values`.
_PROPERTY_REST_VALUES: dict[str, float] = _property_rest_values()


#: Every property name the JS runtime's ``applyProperty`` STATIC switch
#: implements — exactly the numeric transform vocabulary (the rest-value SSOT
#: above). This is the Python side of the two-evaluator drift gate:
#: ``tests/test_loud_discards.py`` extracts the runtime's actual switch cases
#: and asserts exact equality with this set, in both directions. It replaced
#: ``pose.py``'s allow-list when the Python applier was deleted (an#86).
#: Any OTHER property is a swap-set name, applied dynamically through the
#: node's ``asset_sets`` projection (an#87) — ``viseme`` left the static
#: switch when that landed, which is precisely what makes it a conventional
#: set name rather than control flow.
RUNTIME_APPLIED_PROPERTIES: frozenset[str] = frozenset(_PROPERTY_REST_VALUES)


class CutoutCompileError(ValueError):
    """A shot cannot be compiled to a cutout scene. Carries actionable detail.

    A ``ValueError`` rather than a ``RuntimeError`` — unlike the rest of this
    package's error tree — because every one of these is "this value is not in
    the known set", which is the existing idiom for argument-level rejection.
    The render-time errors stay ``RuntimeError``: they are failures of the
    machinery, not of the input.
    """


class CutoutCompileWarning(UserWarning):
    """A shot compiles, but something in it will not reach the screen.

    The line between this and :class:`CutoutCompileError` is whether the author
    could plausibly have meant it. An unknown ``camera.move`` is always a
    mistake, so it raises. A speaker with no mouth is usually an off-screen
    narrator and occasionally a typo — refusing it would break the documented
    idiom, and passing in silence is what this whole change is against.
    """


def _rest_value_for(prop: str, target: str) -> float:
    """The implicit start of a tween on ``prop``, or refuse to invent one.

    A property with no numeric identity — a viseme code, a colour — has no
    meaningful "start from rest". Substituting 0.0 does not mean "unchanged", it
    means *zero*, and the renderer will happily apply it: a colour-valued tween
    with an implicit 0.0 start renders its subject solid black for the whole
    shot, silently. So this raises rather than guessing.
    """
    if prop in _PROPERTY_REST_VALUES:
        return _PROPERTY_REST_VALUES[prop]
    raise CutoutCompileError(
        f"tween on {target!r}:{prop!r} has no from_value, and {prop!r} has no "
        f"rest value to start from (known: {sorted(_PROPERTY_REST_VALUES)}). "
        "Give the action an explicit from_value, or use a `set` action if you "
        "meant to change it discretely — a tween cannot interpolate from a "
        "value that does not exist, and defaulting it to 0 would render as "
        "'fully transparent' / 'black' / 'scaled to nothing' rather than as "
        "'unchanged'."
    )


#: Fallback for a property with no declared rest value.
#:
#: Reachable only by a property that is neither a transform field nor an alias —
#: i.e. a discrete one such as ``viseme``, for which no numeric identity is
#: meaningful. Multiplying such a property by an implicit 0.0 is how a
#: colour-valued tween would render its subject solid black, so the compiler
#: refuses instead: see :func:`_rest_value_for`.
_DFLT_PROPERTY_REST_VALUE: float | None = None

# Emotion → (left_brow_tilt, right_brow_tilt) in radians. Mirrored so the
# brows look symmetric for happy/sad and asymmetric for surprise/skepticism.
_EMOTION_BROWS: dict[str, tuple[float, float]] = {
    "neutral": (0.0, 0.0),
    "happy": (-0.15, 0.15),  # outer ends raised
    "sad": (0.20, -0.20),  # inner ends raised, outer drops
    "angry": (0.30, -0.30),  # furrowed
    "surprised": (-0.25, 0.25),  # both up high
    "skeptical": (-0.20, 0.10),  # one brow raised
    "amused": (-0.10, 0.10),
    "thinking": (0.10, 0.0),
}


def _runtime_node_paths(node: NodeJSON, prefix: str = "") -> set[str]:
    """Every path the JS runtime will index for ``node``'s subtree.

    Mirrors ``buildSceneTree``, including the detail that the synthetic top-level
    ``root`` container is NOT indexed — the compiler emits target paths starting
    at the entity name. Getting that wrong here would make every check below
    off by one segment.
    """
    paths = set()
    path = f"{prefix}/{node.name}" if prefix else node.name
    if prefix or node.name != "root":
        paths.add(path)
        child_prefix = path
    else:
        child_prefix = ""  # skip the synthetic root, as the runtime does
    for child in node.children:
        paths |= _runtime_node_paths(child, child_prefix)
    return paths


@dataclass(frozen=True)
class _SwapVocabulary:
    """What the built scene can swap, and what the descriptors declare.

    The compiler's one source of swap truth for a shot (an#87), built AFTER
    the scene tree so ``node_sets`` reflects what actually resolved:

    - ``node_sets`` — node path → set name → {KEY: texture alias}: the per-slot
      projections stamped on each visual (``VisualJSON.asset_sets``). The
      procedural drawn mouth is in here too — it declares its set on its
      visual like everything else, so nothing below names a set specially.
    - ``node_asset_ids`` — node path → the visual's default texture alias,
      which is what makes a set's REST key derivable (the key whose alias is
      the default attachment).
    - ``declared`` — entity id → set name → declared keys, from the MIGRATED
      descriptor. Declared-but-unresolved (art missing) is the escalation
      case; undeclared is an authoring error. Entities without a descriptor
      are absent, and their built nodes' sets ARE their declaration.
    - ``paths`` — every node path the runtime will index (targets check).
    """

    node_sets: dict[str, dict[str, dict[str, str]]]
    node_asset_ids: dict[str, str | None]
    declared: dict[str, dict[str, frozenset[str]]]
    paths: frozenset[str]
    #: entity id → set → {KEY: attachment name}, as DECLARED (an#7 needs the
    #: attachment names: descriptor animation tracks name attachments).
    declared_maps: dict[str, dict[str, dict[str, str]]] = field(default_factory=dict)
    #: entity id → its MIGRATED descriptor (an#7: `play` resolves against
    #: it through `an.characters.play`, the same code `an validate` runs).
    descriptors: dict[str, CharacterDescriptor] = field(default_factory=dict)
    #: entity id → `rel_path -> art on disk`, or None when the store cannot
    #: say (then every declared attachment is assumed present — the part
    #: probe's own rule, so resolution and the rig builder agree).
    art_exists: dict[str, Callable[[str], bool] | None] = field(default_factory=dict)
    #: node path → its rest transform, so a descriptor animation's DEVIATIONS
    #: can be turned into the absolute values channels carry.
    node_transforms: dict[str, TransformJSON] = field(default_factory=dict)
    #: entity id → k, the view_box → scene-pixel factor its rig was built with.
    entity_scale: dict[str, float] = field(default_factory=dict)

    def swap_capable_paths(self, entity_id: str, set_name: str) -> list[str]:
        """Node paths under ``entity_id`` that can apply ``set_name``."""
        return sorted(
            p
            for p, sets in self.node_sets.items()
            if p.split("/", 1)[0] == entity_id and set_name in sets
        )

    def rest_key(self, path: str, set_name: str) -> str | None:
        """The key a node shows at rest for ``set_name``, or None.

        The key whose alias is the visual's default texture — for an SVG
        mouth the slot's default attachment, for the drawn mouth (whose keys
        map to themselves) the runtime's initial ``X``. Derived, so a viseme
        vocabulary other than Rhubarb's (MPEG-4 numbers, Azure names) still
        knows how to close the mouth.
        """
        key_map = self.node_sets.get(path, {}).get(set_name) or {}
        asset_id = self.node_asset_ids.get(path)
        for key, alias in key_map.items():
            if alias == asset_id:
                return key
        if asset_id is None and "X" in key_map:
            return "X"
        return None


def _swap_vocabulary(
    root: NodeJSON, shot: Shot, mall: Mapping[str, Mapping]
) -> _SwapVocabulary:
    node_sets: dict[str, dict[str, dict[str, str]]] = {}
    node_asset_ids: dict[str, str | None] = {}
    node_transforms: dict[str, TransformJSON] = {}

    def walk(node: NodeJSON, prefix: str) -> None:
        path = f"{prefix}/{node.name}" if prefix else node.name
        if prefix or node.name != "root":
            node_transforms[path] = node.transform
            v = node.visual
            if v is not None and v.asset_sets:
                node_sets[path] = v.asset_sets
                node_asset_ids[path] = v.asset_id
            child_prefix = path
        else:
            child_prefix = ""  # skip the synthetic root, as the runtime does
        for child in node.children:
            walk(child, child_prefix)

    walk(root, "")

    declared: dict[str, dict[str, frozenset[str]]] = {}
    declared_maps: dict[str, dict[str, dict[str, str]]] = {}
    descriptors: dict[str, CharacterDescriptor] = {}
    art_exists: dict[str, Callable[[str], bool] | None] = {}
    entity_scale: dict[str, float] = {}
    chars_store = mall.get("characters") or {}
    for entity in shot.entities:
        if entity.kind != "character" or entity.ref not in chars_store:
            continue
        try:
            desc_data = chars_store[entity.ref]
        except KeyError:
            continue
        if (
            not isinstance(desc_data, dict)
            or desc_data.get("kind") != "CharacterDescriptor"
        ):
            continue
        desc = CharacterDescriptor.model_validate(
            migrate(dict(desc_data), kind=CHARACTER_DOCUMENT_KIND.name)
        )
        for channel in desc.asset_sets:
            problem = swap_set_name_problem(channel)
            if problem is not None:
                # The reservation check (an#87): a set named `alpha` would be
                # applied by the runtime's static switch, never as a swap —
                # the descriptor would declare a capability the pipeline
                # silently routes elsewhere.
                raise CutoutCompileError(
                    f"character {entity.ref!r} declares an asset set that "
                    f"cannot be a swap-set name: {problem}. Transform "
                    f"properties are: {sorted(TRANSFORM_PROPERTIES)}."
                )
        declared[entity.id] = {
            channel: frozenset(keys) for channel, keys in desc.asset_sets.items()
        }
        declared_maps[entity.id] = {
            channel: dict(keys) for channel, keys in desc.asset_sets.items()
        }
        descriptors[entity.id] = desc
        art_exists[entity.id] = art_exists_for(chars_store, entity.ref)
        entity_scale[entity.id] = SCENE_PX_PER_VIEW_BOX / float(desc.view_box[3] or 1)

    return _SwapVocabulary(
        node_sets=node_sets,
        node_asset_ids=node_asset_ids,
        declared=declared,
        paths=frozenset(_runtime_node_paths(root)),
        declared_maps=declared_maps,
        descriptors=descriptors,
        art_exists=art_exists,
        node_transforms=node_transforms,
        entity_scale=entity_scale,
    )


def _raise_or_warn_on_asset_fallbacks(
    shot_id: str,
    resolutions: list[AssetResolutionJSON],
    *,
    strict: bool,
) -> None:
    """Make a stand-in asset audible — and, under ``strict``, fatal (an#33).

    The fallback itself is legitimate: a project with no art must still render,
    and that is the only reason ``an`` works out of the box. What is not
    legitimate is that it was *indistinguishable* from the real thing. A
    corpus blessed on a machine where the assets exist and gated on one where
    they do not blesses one picture and gates another, and every tripwire
    reports a clean pass because both sides are internally consistent.

    So: a warning by default (the render is still what the author can get
    today), and an error for any caller that is measuring pixels.
    """
    fallbacks = [r for r in resolutions if r.fallback]
    if not fallbacks:
        return
    lines = [f"  - {r.kind} {r.id!r}: {r.detail}" for r in fallbacks]
    body = (
        f"shot {shot_id!r} rendered {len(fallbacks)} stand-in asset(s):\n"
        + "\n".join(lines)
    )
    if strict:
        raise CutoutCompileError(
            body + "\n\nstrict_assets=True refuses this because the render would be a "
            "DIFFERENT picture that looks like a successful one. Either commit / "
            "regenerate the missing asset, or drop strict_assets if a stand-in "
            "is what you meant."
        )
    warnings.warn(
        body + "\n\nThe render will succeed and look plausible, which is exactly why "
        "this is said out loud. Pass strict_assets=True to make it fatal.",
        CutoutCompileWarning,
        stacklevel=3,
    )


def compile_shot(
    shot: Shot,
    mall: Mapping[str, Mapping] | None = None,
    *,
    fps: int = 30,
    width: int = 1920,
    height: int = 1080,
    background: str = "#ffffff",
    strict_assets: bool = False,
    step_hz: float | None = None,
) -> CutoutSceneJSON:
    """Compile a single cutout-style `Shot` to its JS-runtime JSON form.

    ``step_hz`` (an#89) resamples every authored **tween** onto a SHOT-wide
    pose grid of that many updates per second (multiples of ``1/step_hz`` on
    this shot's clock, shared by every tween in the shot; the grid restarts at
    a cut), each keyframe step-eased, so the character holds each pose for the
    frames between grid points — "on twos" at half the frame rate, "on threes"
    at a third. It is sample-and-hold of the eased curve at the grid instants,
    not a retiming into holds and fast transitions. ``None`` (default) leaves
    tweens smooth and the compiled document byte-identical to before the knob
    existed; anything else must satisfy ``0 < step_hz <= fps`` — checked HERE
    as well as by ``an validate``, because a render never runs validate and a
    non-positive rate used to spin ``step_times`` forever (an#89 review).
    Exempt by construction, because they are separate
    emission sites rather than string-sniffed: the camera (`_add_camera_clips`
    — a stepped character under a translating camera slides in screen space,
    which is why the practice keeps cameras on ones), compiled blinks, `play`
    clips, and swap channels (already stepped by format). The value is
    stamped into ``meta.step_hz`` — only when set — so a serialized scene
    declares its timing policy without moving the contract hash of one that
    has none.

    ``strict_assets`` turns a stand-in asset — the placeholder rig drawn for a
    character whose descriptor is missing, or the default backdrop drawn for an
    unknown environment ref — from a warning into a :class:`CutoutCompileError`.
    Off by default so an asset-less project still renders; on for anything that
    measures pixels, where a stand-in is a wrong answer wearing a right one's
    clothes (an#33).
    """
    if shot.style != "cutout":
        raise ValueError(f"compile_shot expects style='cutout'; got {shot.style!r}")
    if step_hz is not None and not (math.isfinite(step_hz) and 0 < step_hz <= fps):
        raise CutoutCompileError(
            f"step_hz must satisfy 0 < step_hz <= fps ({fps}); got {step_hz!r}. "
            f"At {fps} fps, {fps / 2:g} is 'on twos' and {fps / 3:g} 'on threes'."
        )
    mall = mall or {}

    textures: dict[str, AssetJSON] = {}
    resolutions: list[AssetResolutionJSON] = []
    scene_root = _build_scene_root(
        shot, mall, textures=textures, resolutions=resolutions
    )
    vocab = _swap_vocabulary(scene_root, shot, mall)
    animations, tracks = _compile_actions(
        shot.actions,
        shot.duration,
        vocab=vocab,
        resolutions=resolutions,
        fps=fps,
        step_hz=step_hz,
    )
    # Phase 4: emit a viseme channel per dialogue line that has a viseme_track.
    _add_viseme_clips(
        shot,
        animations,
        tracks,
        mall=mall,
        vocab=vocab,
    )
    # Blinks (an#88): compiled per eye, ahead of everything authored.
    blink_phases = _add_blink_clips(
        shot, animations, tracks, vocab=vocab, fps=fps, mall=mall
    )
    # Phase 7: wire camera.move ("push_in", "pull_out", "hold") into a scale
    # animation on the synthetic scene root so directors get visible camera
    # behavior without writing channels by hand.
    _add_camera_clips(shot, animations, tracks)
    # AFTER action + viseme compilation, deliberately: a swap key the timeline
    # actually USES whose art is missing is recorded as a fallback during
    # those passes (usage-aware escalation, an#87), and this is the one place
    # that decides warn-vs-raise for every fallback.
    _raise_or_warn_on_asset_fallbacks(shot.id, resolutions, strict=strict_assets)

    timeline = TimelineJSON(duration=shot.duration, tracks=tracks)

    return CutoutSceneJSON(
        meta=CutoutSceneMetaJSON(
            fps=fps,
            width=width,
            height=height,
            duration=shot.duration,
            background=background,
            blink_phases=blink_phases,
            step_hz=step_hz,
        ),
        scene=scene_root,
        animations=animations,
        timeline=timeline,
        assets=AssetsJSON(textures=textures),
        asset_resolution=resolutions,
    )


# -----------------------------------------------------------------------------
# Scene tree construction
# -----------------------------------------------------------------------------


def _build_scene_root(
    shot: Shot,
    mall: Mapping[str, Mapping],
    *,
    textures: dict[str, AssetJSON] | None = None,
    resolutions: list[AssetResolutionJSON] | None = None,
) -> NodeJSON:
    """Construct the cutout scene tree under a single root from shot.entities.

    Multiple characters get spread along the x-axis so they don't overlap.
    For N characters, positions are evenly distributed across a fixed band;
    a single character lives at the center.

    ``resolutions`` is an out-parameter, filled the same way ``textures`` is:
    one :class:`AssetResolutionJSON` per drawable entity, in scene order,
    recording what each declared ref actually became.
    """
    if textures is None:
        textures = {}
    if resolutions is None:
        resolutions = []
    children: list[NodeJSON] = []
    characters_store = mall.get("characters") or {}
    environments_store = mall.get("environments") or {}
    char_entities = [e for e in shot.entities if e.kind == "character"]
    n_chars = len(char_entities)
    char_positions = _layout_character_positions(n_chars)
    char_idx = 0
    # Process environments first so they sit BEHIND characters in z-order.
    for entity in shot.entities:
        if entity.kind == "environment":
            children.append(
                _build_environment_subtree(
                    entity, environments_store, resolutions=resolutions
                )
            )
    for entity in shot.entities:
        if entity.kind == "character":
            x = char_positions[char_idx]
            char_idx += 1
            sub = _build_character_subtree(
                entity, characters_store, textures=textures, resolutions=resolutions
            )
            sub.transform.x = x
            children.append(sub)
        elif entity.kind == "prop":
            raise CutoutCompileError(
                f"shot {shot.id!r}: entity {entity.id!r} is a prop, which the "
                "cutout renderer does not draw yet. Props — images, nine-slice "
                "panels, things a character holds — are planned; see "
                "https://github.com/thorwhalen/an/issues/9. Until then, remove the entity rather than leaving it "
                "in the scene, where it would be silently absent from the render."
            )
        # `voice` and `style` entities are legitimately not drawable: they
        # configure the render rather than appearing in it.
    return NodeJSON(name="root", children=children)


# Environment presets — built-in named backdrops. A user-supplied environment
# in the store can override fields by name (sky_color, ground_color, ground_y).
_ENV_PRESETS: dict[str, dict[str, Any]] = {
    "default": {"sky_color": "#cfe9ff", "ground_color": "#7cba6f", "ground_y": 100.0},
    "park": {"sky_color": "#a5d8ff", "ground_color": "#7cba6f", "ground_y": 110.0},
    "indoor": {"sky_color": "#f4e8c8", "ground_color": "#a07a4a", "ground_y": 120.0},
    "night": {"sky_color": "#1a2540", "ground_color": "#2c3e50", "ground_y": 110.0},
    "sunset": {"sky_color": "#f4a261", "ground_color": "#5b4b32", "ground_y": 110.0},
}


def _build_environment_subtree(
    entity: AssetRef,
    env_store: Mapping,
    *,
    resolutions: list[AssetResolutionJSON] | None = None,
) -> NodeJSON:
    """Backdrop: a sky band + a ground band, full canvas width.

    Picks a preset by ``entity.ref`` (e.g. "park", "night"); the store
    can override any of (sky_color, ground_color, ground_y) by ref.

    A ref that names neither a store entry nor a preset draws the *default*
    backdrop, which is a different picture from the one the author asked for
    — so it is recorded as a fallback (an#33).
    """
    preset_key = (entity.ref or "default").lower()
    known_preset = preset_key in _ENV_PRESETS
    preset = dict(_ENV_PRESETS.get(preset_key, _ENV_PRESETS["default"]))
    in_store = entity.ref in env_store
    if resolutions is not None:
        if in_store:
            resolved, fallback, detail = "store", False, ""
        elif known_preset:
            resolved, fallback, detail = "preset", False, ""
        else:
            resolved, fallback, detail = (
                "default",
                True,
                f"environment ref {entity.ref!r} names neither an entry in the "
                f"{entity.store!r} store nor a built-in preset "
                f"({sorted(_ENV_PRESETS)}), so the DEFAULT backdrop was drawn",
            )
        resolutions.append(
            AssetResolutionJSON(
                id=entity.id,
                kind="environment",
                store=entity.store,
                ref=entity.ref,
                resolved=resolved,
                fallback=fallback,
                detail=detail,
            )
        )
    if in_store:
        try:
            override = env_store[entity.ref]
            if isinstance(override, dict):
                unknown = sorted(set(override) - set(preset))
                if unknown:
                    # A warning, not an error. EnvironmentsStore is a
                    # JsonSidecarStore over a free-form meta.json, so `name` /
                    # `description` / `tags` are its natural shape — raising here
                    # would hard-fail ordinary data. The keys still do nothing,
                    # which is the part worth saying.
                    warnings.warn(
                        f"environment {entity.ref!r} declares {unknown}, which the "
                        f"cutout renderer does not read (it uses {sorted(preset)}), "
                        "so they have no effect on the render. Layered plates "
                        "and parallax planes are planned; see "
                        "https://github.com/thorwhalen/an/issues/9",
                        CutoutCompileWarning,
                        stacklevel=2,
                    )
                preset.update({k: v for k, v in override.items() if k in preset})
        except KeyError:
            pass
    # Sky and ground are HUGE rects so they fill the canvas regardless of size.
    # The runtime centers root at canvas/2 and applies camera scale, so 4000px
    # wide rects will always cover.
    huge = 4000.0
    ground_y = float(preset["ground_y"])
    sky_color = str(preset["sky_color"])
    ground_color = str(preset["ground_color"])
    return NodeJSON(
        name=entity.id,
        transform=TransformJSON(),
        children=[
            NodeJSON(
                name="sky",
                transform=TransformJSON(x=0.0, y=-huge / 2 + ground_y),
                visual=VisualJSON(
                    kind="rect", width=huge, height=huge, color=sky_color
                ),
            ),
            NodeJSON(
                name="ground",
                transform=TransformJSON(x=0.0, y=huge / 2 + ground_y),
                visual=VisualJSON(
                    kind="rect", width=huge, height=huge, color=ground_color
                ),
            ),
        ],
    )


def _layout_character_positions(n: int, *, spread: float = 220.0) -> list[float]:
    """Return ``n`` x-positions evenly distributed about 0.

    >>> _layout_character_positions(0)
    []
    >>> _layout_character_positions(1)
    [0.0]
    >>> _layout_character_positions(2, spread=200.0)
    [-100.0, 100.0]
    """
    if n <= 0:
        return []
    if n == 1:
        return [0.0]
    step = spread / (n - 1)
    return [-spread / 2 + i * step for i in range(n)]


def _build_character_subtree(
    entity: AssetRef,
    characters_store: Mapping,
    *,
    textures: dict[str, AssetJSON] | None = None,
    resolutions: list[AssetResolutionJSON] | None = None,
) -> NodeJSON:
    """Build a NodeJSON subtree for one character.

    Phase 11b: if the characters store has a Phase-11a CharacterDescriptor
    for this entity (``kind == "CharacterDescriptor"``), build the SVG
    rig and populate the ``textures`` accumulator. Otherwise fall back to
    the procedural rig so legacy / asset-less characters keep rendering.

    That fallback is deliberate and stays — an asset-less project must render
    — but it is no longer *silent*: what happened is appended to
    ``resolutions`` so the compiled scene carries which rig was actually built
    (an#33).
    """
    char_meta: dict[str, Any] = {}
    in_store = entity.ref in characters_store
    if in_store:
        try:
            value = characters_store[entity.ref]
            if isinstance(value, dict):
                char_meta = value
        except KeyError:
            char_meta = {}
            in_store = False

    def _record(resolved: str, *, fallback: bool = False, detail: str = "") -> None:
        if resolutions is None:
            return
        resolutions.append(
            AssetResolutionJSON(
                id=entity.id,
                kind="character",
                store=entity.store,
                ref=entity.ref,
                resolved=resolved,
                fallback=fallback,
                detail=detail,
            )
        )

    if char_meta.get("kind") == "CharacterDescriptor":
        _record("descriptor")
        return _build_svg_character_subtree(
            entity,
            char_meta,
            textures=textures if textures is not None else {},
            probe=_part_probe(characters_store),
            resolutions=resolutions,
        )

    declared_parts = char_meta.get("parts")
    if declared_parts:
        _record("parts")
    elif in_store:
        _record(
            "placeholder",
            fallback=True,
            detail=(
                f"character ref {entity.ref!r} IS in the {entity.store!r} store, "
                "but the entry is neither a CharacterDescriptor nor a rig with "
                "'parts', so the built-in placeholder rig was drawn instead"
            ),
        )
    else:
        _record(
            "placeholder",
            fallback=True,
            detail=(
                f"character ref {entity.ref!r} is not in the {entity.store!r} "
                "store, so the built-in placeholder rig was drawn instead of "
                "the character the scene names"
            ),
        )

    parts = declared_parts or _PLACEHOLDER_PARTS
    skin, clothing, hair = _palette_for(entity.id)
    # Per-part color: head/limbs are skin colour, torso/arm-clothing is the
    # clothing colour. Override via a future store.parts.colors mapping.
    part_color: dict[str, str] = {
        "head": skin,
        "torso": clothing,
        "left_arm": clothing,
        "right_arm": clothing,
        "left_leg": "#2c3e50",
        "right_leg": "#2c3e50",
    }
    children: list[NodeJSON] = []
    for part in parts:
        geom = _PLACEHOLDER_PART_GEOMETRY.get(
            part, {"x": 0.0, "y": 0.0, "width": 50.0, "height": 50.0}
        )
        # Head renders as a fleshier ellipse; everything else stays a rect.
        kind = "ellipse" if part == "head" else "rect"
        children.append(
            NodeJSON(
                name=part,
                transform=TransformJSON(x=float(geom["x"]), y=float(geom["y"])),
                visual=VisualJSON(
                    kind=kind,
                    width=float(geom["width"]),
                    height=float(geom["height"]),
                    color=part_color.get(part, "#cccccc"),
                ),
            )
        )
    char_node = NodeJSON(
        name=entity.id,
        transform=TransformJSON(),
        children=children,
    )
    if "head" in parts:
        # Head children: hair on top, eyebrows above eyes, two eyes (white +
        # pupil drawn together by the runtime when kind="eye"), mouth (viseme
        # target — runtime draws curved lips per viseme code).
        for child in char_node.children:
            if child.name != "head":
                continue
            # Hair: rounded band atop the head.
            child.children.append(
                NodeJSON(
                    name="hair",
                    transform=TransformJSON(x=0.0, y=-20.0),
                    visual=VisualJSON(
                        kind="ellipse", width=46.0, height=18.0, color=hair
                    ),
                )
            )
            # Eyebrows: small dark rects above each eye; rotation = expression.
            for brow_name, bx in (("left_brow", -10.0), ("right_brow", 10.0)):
                child.children.append(
                    NodeJSON(
                        name=brow_name,
                        transform=TransformJSON(x=bx, y=-10.0),
                        visual=VisualJSON(
                            kind="rect",
                            width=10.0,
                            height=2.5,
                            color=hair,
                        ),
                    )
                )
            # Eyes: white sclera + dark pupil drawn together by makeEye.
            for eye_name, ex in (("left_eye", -10.0), ("right_eye", 10.0)):
                child.children.append(
                    NodeJSON(
                        name=eye_name,
                        transform=TransformJSON(x=ex, y=-3.0),
                        visual=VisualJSON(
                            kind="eye", width=10.0, height=8.0, color="#1a1a1a"
                        ),
                    )
                )
            # Mouth (viseme target).
            child.children.append(
                NodeJSON(
                    name="mouth",
                    transform=TransformJSON(x=0.0, y=14.0),
                    visual=VisualJSON(
                        kind="mouth",
                        width=22.0,
                        height=4.0,
                        color="#552222",
                        asset_sets={VISEME_CHANNEL: dict(PROCEDURAL_MOUTH_KEYS)},
                    ),
                )
            )
    return char_node


# -----------------------------------------------------------------------------
# Phase 11b: SVG-textured character rig from a CharacterDescriptor
# -----------------------------------------------------------------------------


#: Scene-graph pixels spanned by a descriptor's full ``view_box`` height.
#:
#: The single number that maps descriptor space to scene space. One uniform
#: factor ``k = SCENE_PX_PER_VIEW_BOX / view_box_height`` scales bone positions
#: and part extents alike — uniform by construction, so the compiler cannot
#: violate the invariant that aspect ratio is intrinsic to the art (an#74).
#:
#: 345 is a calibration, not a preference. It is what reproduces the framing the
#: seven deleted ``_SVG_*_SIZE`` constants hand-tuned: at k = 345/1024 = 0.3369,
#: ``saturated-rig``'s own art gives torso 107.8x129.4 against the old 110x130,
#: legs 37.7x118.6 against 38x120. The constants were an approximation of
#: exactly this product, which is the evidence that the rig should have been
#: driving it all along.
SCENE_PX_PER_VIEW_BOX: float = 345.0

#: The fit policy every compiled sprite carries. Named rather than inlined so
#: the one place that decides "the art keeps its shape" is greppable.
CONTAIN_FIT: str = "contain"


def _svg_asset_src(ref: str, rel_path: str) -> str:
    """Path used inside the runtime dir, relative to ``index.html``."""
    return f"characters/{ref}/{rel_path}"


def _register_texture(
    textures: dict[str, AssetJSON],
    alias: str,
    src: str,
) -> str:
    """Add a texture entry if not already present; return ``alias``."""
    if alias not in textures:
        textures[alias] = AssetJSON(src=src)
    return alias


def _part_probe(
    characters_store: Mapping,
) -> Callable[[str], tuple[bool, tuple[float, float] | None]] | None:
    """A probe answering ``(art exists, the size it rasterises at)`` for a part.

    **Two questions, deliberately not one.** Whether the art is *there* decides
    whether the compiler declares a texture for it; whether it can be *measured*
    decides only whether the sprite's box comes from the art or from the
    runtime's fit. Collapsing them is a real bug and it was here: a degenerate
    ``<svg/>`` is unmeasurable but present, and treating that as absent made the
    part vanish from the scene silently — trading an#79's hang for exactly the
    invisible-art failure #76 exists to stop.

    Returns ``None`` when the store has no filesystem root — **not** a probe
    that answers "absent" — because a store that can answer nothing must drop
    no parts rather than all of them.

    Size is read from the SVG root's ``width``/``height``, falling back to the
    viewBox extent as a browser does: a header parse, not a render.
    """
    root = getattr(characters_store, "_root", None)
    if root is None:
        return None
    base = Path(root)
    prefix = "characters/"

    def probe(src: str) -> tuple[bool, tuple[float, float] | None]:
        if not src.startswith(prefix):
            return False, None
        path = base / src[len(prefix) :]
        if not path.is_file():
            return False, None
        try:
            return True, raster_size(path)
        except (OSError, ValueError):
            # Present but unreadable or malformed. Still declared, so the
            # failure is loud at load rather than an absence nobody sees.
            return True, None

    return probe


def _bone_positions(desc: CharacterDescriptor) -> dict[str, tuple[float, float]]:
    """Absolute ``(x, y)`` per bone, in view_box units.

    Bone transforms are parent-relative, so a bone's position is the sum along
    its parent chain. A cycle or a dangling parent stops the walk rather than
    looping — a malformed rig is #78's business, not this function's.
    """
    by_name = {b.name: b for b in desc.bones}
    out: dict[str, tuple[float, float]] = {}
    for bone in desc.bones:
        x = y = 0.0
        seen: set[str] = set()
        cursor: Bone | None = bone
        while cursor is not None and cursor.name not in seen:
            seen.add(cursor.name)
            x += cursor.x
            y += cursor.y
            cursor = by_name.get(cursor.parent) if cursor.parent else None
        out[bone.name] = (x, y)
    return out


def _record_missing_parts(
    entity: AssetRef,
    missing: list[tuple[str, str, str]],
    *,
    drawn: set[str],
    into: list[AssetResolutionJSON] | None,
) -> None:
    """Record every declared part whose art is not on disk, as a fallback.

    A skin declares an inventory, and a slot that ends up with nothing to draw
    is a hole in the picture. Recording it here routes it through the one place
    that decides what a stand-in costs: audible always, fatal under
    ``strict_assets`` (an#76). Raising from the compiler instead would put a
    second policy next to that one.

    A slot that still drew *something* — one attachment missing out of several,
    as when a rig ships open eyes but no closed ones — is reported separately
    and NOT as a fallback, because the frame is not wrong, only the inventory
    is incomplete. Conflating the two would make every rig without a blink
    refuse to render under ``strict_assets``.
    """
    if into is None or not missing:
        return
    empty = [m for m in missing if m[0] not in drawn]
    partial = [m for m in missing if m[0] in drawn]
    for slot_name, attachment, path in empty:
        into.append(
            AssetResolutionJSON(
                id=f"{entity.id}/{slot_name}",
                kind="part",
                store=entity.store,
                ref=entity.ref,
                resolved="missing",
                fallback=True,
                detail=(
                    f"slot {slot_name!r} declares attachment {attachment!r} at "
                    f"{path!r}, which is not in the store — the slot draws nothing"
                ),
            )
        )
    for slot_name, attachment, path in partial:
        into.append(
            AssetResolutionJSON(
                id=f"{entity.id}/{slot_name}",
                kind="part",
                store=entity.store,
                ref=entity.ref,
                resolved="incomplete",
                fallback=False,
                detail=(
                    f"slot {slot_name!r} is missing attachment {attachment!r} at "
                    f"{path!r}; the slot still draws, but that key cannot be swapped to"
                ),
            )
        )


def _rig_origin(bones: dict[str, tuple[float, float]]) -> tuple[float, float]:
    """The point in view_box space that the entity's placement refers to.

    The centre of the rig's bone extent, **not** the root bone. The scene root
    positions a character on x only and leaves y at 0, so this point is what
    lands at the frame's vertical centre — and a rig whose root is its ground
    contact (the default puts it at the feet, y=980) would therefore hang its
    whole body above the placement point, head off-frame.

    Centring on the extent makes framing independent of where an author chose
    to put the root, which is a rigging decision and should not be a framing
    one. On the default rig it lands at y=700, within 20 units of the torso
    bone — i.e. it reproduces the convention the deleted `torso_y = 0.0`
    literal encoded, without hardcoding a bone name.
    """
    if not bones:
        return (0.0, 0.0)
    xs = [x for x, _ in bones.values()]
    ys = [y for _, y in bones.values()]
    return ((min(xs) + max(xs)) / 2.0, (min(ys) + max(ys)) / 2.0)


def _build_svg_character_subtree(
    entity: AssetRef,
    desc_data: dict[str, Any],
    *,
    textures: dict[str, AssetJSON],
    probe: Callable[[str], tuple[bool, tuple[float, float] | None]] | None = None,
    resolutions: list[AssetResolutionJSON] | None = None,
) -> NodeJSON:
    """Build the scene subtree for a character, **from its descriptor's rig**.

    Every part's position comes from a bone, every part's extent from its own
    art, and both are scaled by one uniform factor. Nothing here is a module
    constant: the seven ``_SVG_*_SIZE`` values and the four y-offset literals
    this replaced are gone, and gutting ``bones``/``slots``/``skins``/``view_box``
    now changes the output — which it provably did not before (an#73).

    A slot whose art is not on disk is recorded in ``resolutions`` as a fallback,
    which makes it audible by default and fatal under ``strict_assets`` — the
    same treatment a missing *character* already got (an#33), now reaching
    inside the descriptor to the individual part (an#76). It is recorded rather
    than raised here because the decision belongs to one place, and that place
    is :func:`_raise_or_warn_on_asset_fallbacks`.

    ``probe(src) -> (exists, size)`` answers whether a part's art is on disk and
    what size it rasterises at. Existence decides whether a texture is declared
    at all; size lets the sprite's box be the art's own extent rather than a
    guess. With neither, the attachment's declared ``width``/``height`` are
    used, and failing that the runtime's ``contain`` fit draws the art at its
    natural shape — never stretched to a fabricated box.
    """
    desc = CharacterDescriptor.model_validate(
        migrate(dict(desc_data), kind=CHARACTER_DOCUMENT_KIND.name)
    )
    ref = entity.ref or entity.id
    _, _, _, view_box_height = desc.view_box
    k = SCENE_PX_PER_VIEW_BOX / float(view_box_height or 1)

    skin = desc.skins.get("default") or next(iter(desc.skins.values()), Skin())
    bones = _bone_positions(desc)
    origin = _rig_origin(bones)
    # Shared with `an.characters.play` so a `play` resolves against the
    # nesting the builder actually uses (an#7 review).
    nests_under = primary_slot_per_bone(desc)

    # If the head art has its own face baked in (DiceBear / hand-drawn full
    # avatars), the separate eye/brow/mouth sprites double up with the baked
    # features. Lip-sync stays audio-only for these; hand-rig for dialogue.
    # `face_overlay` is the DECLARED fact (0.3.0, an#87) — the old vendor-name
    # check on metadata.art_provenance lives on only inside the migration.
    head_has_face = not desc.face_overlay

    def _register(slot_name: str, attachment_name: str, attachment: Attachment) -> str:
        # Slot-qualified on purpose: attachment names are a PER-SLOT namespace
        # (both eye slots carry `open`/`closed`), and the old `{entity}.{name}`
        # alias space was silently first-wins on cross-slot collision.
        alias = f"{entity.id}.{slot_name}.{attachment_name}"
        return _register_texture(textures, alias, _svg_asset_src(ref, attachment.path))

    # Every attachment in the skin is registered, not just the active one, so a
    # swap has its texture already loaded when the key changes.
    #
    # Except the ones whose art is not there. A skin declares an inventory —
    # `eye_l_closed` is in the default skin and in REQUIRED_PARTS, but the bench
    # rigs do not ship it — and declaring a texture the staging step cannot find
    # makes the render fail at load over art no node draws. Registering only
    # what resolves keeps the compiler from fabricating; reporting the gap in
    # the art package is `an character validate`'s job (#78), not this one's.
    aliases: dict[str, dict[str, str]] = {}
    missing_art: list[tuple[str, str, str]] = []
    for slot_name, attachments in skin.slots.items():
        resolved_here: dict[str, str] = {}
        for name, att in attachments.items():
            src = _svg_asset_src(ref, att.path)
            if probe is not None and not probe(src)[0]:
                missing_art.append((slot_name, name, att.path))
                continue
            resolved_here[name] = _register(slot_name, name, att)
        aliases[slot_name] = resolved_here

    nodes: dict[str, NodeJSON] = {}
    children_of: dict[str, list[NodeJSON]] = {}

    for slot in sorted(desc.slots, key=lambda s: (s.draw_order, s.name)):
        parent = nests_under.get(slot.bone)
        nested = parent is not None and parent != slot.name
        # Baked face: drop every slot nested under the HEAD BONE's primary
        # slot — keyed on the bone (the rig's skeleton contract), not on the
        # slot name "head": a rig whose head slot is named otherwise used to
        # get its face overlays (and their blinks) back (an#88 review).
        if nested and head_has_face and parent == nests_under.get("head"):
            continue

        resolved = drawn_attachment(desc, skin, slot)
        if resolved is None or resolved[0] not in aliases.get(slot.name, {}):
            continue
        attachment_name, attachment = resolved

        # Position = the slot's bone, plus the attachment's own offset from it.
        # Both are needed: five face parts share one `head` bone, so the bone
        # alone would stack them, and the offset alone would ignore the rig.
        bone_x, bone_y = bones.get(slot.bone, (0.0, 0.0))
        if nested:
            parent_x, parent_y = bones.get(parent, (0.0, 0.0))
            bone_x, bone_y = bone_x - parent_x, bone_y - parent_y
        else:
            bone_x, bone_y = bone_x - origin[0], bone_y - origin[1]
        bone_x += attachment.x
        bone_y += attachment.y

        src = _svg_asset_src(ref, attachment.path)
        extent = (probe(src)[1] if probe else None) or (
            (attachment.width, attachment.height)
            if attachment.width and attachment.height
            else None
        )
        visual = VisualJSON(
            kind="svg_sprite",
            asset_id=aliases[slot.name][attachment_name],
            anchor_x=attachment.anchor[0],
            anchor_y=attachment.anchor[1],
            fit=CONTAIN_FIT,
            **({"width": extent[0] * k, "height": extent[1] * k} if extent else {}),
        )
        # The per-slot PROJECTION of the descriptor's asset_sets (an#87): a
        # channel projects onto every slot whose attachments its keys name —
        # `viseme` lands on the mouth because the mouth's attachments carry
        # the viseme map's values, `eyelid` lands on BOTH eye slots because
        # both carry `open`/`closed`. No slot names appear here: the skin is
        # the binding. Keys whose art did not resolve are absent from the map
        # (an inventory gap — `_record_missing_parts` records it; a key a
        # channel actually USES escalates via the fallback machinery).
        projected = {
            channel: resolved
            for channel, key_map in desc.asset_sets.items()
            if (
                resolved := {
                    key: aliases[slot.name][name]
                    for key, name in key_map.items()
                    if name in aliases[slot.name]
                }
            )
        }
        if projected:
            visual.asset_sets = projected

        node = NodeJSON(
            name=slot.name,
            transform=TransformJSON(x=bone_x * k, y=bone_y * k),
            visual=visual,
        )
        nodes[slot.name] = node
        children_of.setdefault(parent if nested else "", []).append(node)

    _record_missing_parts(entity, missing_art, drawn=set(nodes), into=resolutions)

    for parent_name, kids in children_of.items():
        if parent_name and parent_name in nodes:
            nodes[parent_name].children = kids

    return NodeJSON(
        name=entity.id,
        transform=TransformJSON(),
        children=children_of.get("", []),
    )


# -----------------------------------------------------------------------------
# Action → animations + timeline tracks
# -----------------------------------------------------------------------------


def _compile_actions(
    actions: list[Action],
    shot_duration: float,
    *,
    vocab: _SwapVocabulary | None = None,
    resolutions: list[AssetResolutionJSON] | None = None,
    fps: int = 30,
    step_hz: float | None = None,
) -> tuple[dict[str, AnimationClipJSON], list[TrackJSON]]:
    """Flatten authoring actions and convert to animation clips.

    Tweens and plays compile per action. **Set actions compile per
    (target, property) group into step channels that HOLD from each set until
    the next action on that target/property** — the next set joins the same
    channel as a keyframe; a tween ends the hold at its start; with nothing
    following, the hold runs to the shot end (an#87) — the viseme-clip shape.

    Precedence at an instant where both apply is fixed by TRACK ORDER, which
    the evaluators read later-wins: every hold clip is placed BEFORE the
    track's per-action clips, so **an active tween governs** — at the shared
    handoff instant the tween's first frame shows (an end-inclusive hold
    listed after the tween used to mask it, measured), and a set authored
    inside a running tween's window takes effect when that window ends, not
    mid-tween. The previous
    per-set 0.001s placement window had two defects: a set at a
    non-frame-aligned time (``at=3.02`` @30fps, window [3.02, 3.021] between
    samples) silently never fired, and when one did fire its persistence was
    an accident of stateful forward rendering, false under backward scrubbing.

    ``vocab`` (when compiling a real shot) enables the swap checks: an
    authored action on a non-transform property must name a declared asset
    set and key of its target — see :func:`_check_swap_action`.
    """
    animations: dict[str, AnimationClipJSON] = {}
    placed_by_track: dict[str, list[PlacedClipJSON]] = {}

    flat_list: list[FlatAction] = []
    for action in actions:
        flat_list.extend(flatten(action))

    swap_props = _swap_property_names(flat_list)
    if vocab is not None:
        flat_list = [
            flat
            for flat in flat_list
            if _check_swap_action(flat, vocab=vocab, resolutions=resolutions)
        ]

    set_groups: dict[tuple[str, str], list[FlatAction]] = {}
    tween_starts: dict[tuple[str, str], list[float]] = {}
    hold_by_track: dict[str, list[PlacedClipJSON]] = {}
    ordinal = 0
    for flat in flat_list:
        if isinstance(flat.action, SetAction):
            key = (flat.action.target, flat.action.property)
            set_groups.setdefault(key, []).append(flat)
            continue
        if isinstance(flat.action, TweenAction):
            key = (flat.action.target, flat.action.property)
            tween_starts.setdefault(key, []).append(flat.start)
        anim_id, track_root, placed = _compile_one(flat, ordinal=ordinal)
        ordinal += 1
        if anim_id is not None and anim_id not in animations:
            animations[anim_id] = _build_anim_for(
                flat,
                anim_id,
                swap_properties=swap_props,
                vocab=vocab,
                fps=fps,
                step_hz=step_hz,
            )
        if (
            isinstance(flat.action, PlayAction)
            and flat.action.duration is None
            and anim_id is not None
            and animations[anim_id].loop_mode == "loop"
        ):
            # A LOOP with no window to fill is a loop that never loops: the
            # placement defaulted to the animation's natural duration, so
            # `play("gale", "idle_breath")` stopped after one cycle — issue
            # #7's title, one layer up (an#7 review). "Keep going" means to
            # the shot end; both evaluators divide the window by `speed`, so
            # the window is stretched by it to land there.
            placed = placed.model_copy(
                update={
                    "duration": max(0.001, (shot_duration - flat.start) * placed.speed)
                }
            )
        placed_by_track.setdefault(track_root, []).append(placed)

    for (target, prop), group in set_groups.items():
        group.sort(key=lambda f: f.start)
        boundaries = sorted(tween_starts.get((target, prop), []))
        # A set holds until the NEXT ACTION on the same (target, property):
        # the next set joins the same channel as a keyframe, but a tween ends
        # the hold at its start so the tween governs from there and its end
        # value persists after it (the runtime's stateful hold, unchanged).
        # Holding to the shot end regardless would let a `set` placed AFTER
        # the tween clips in the track mask the tween for the whole shot —
        # measured, and the most common authoring shape ("set the start
        # pose, then animate") was a no-op tween (an#87 review).
        runs: list[list[FlatAction]] = []
        for flat in group:
            if runs and not any(
                runs[-1][0].start <= b <= flat.start for b in boundaries
            ):
                runs[-1].append(flat)
            else:
                runs.append([flat])
        for run in runs:
            first = run[0].start
            end = next((b for b in boundaries if b >= first), shot_duration)
            anim_id = f"__set__{ordinal}"
            ordinal += 1
            kfs = []
            for flat in run:
                value = flat.action.value
                _check_keyframe_value(value, target=target, prop=prop)
                if flat.start > shot_duration:
                    warnings.warn(
                        f"set on {target!r}:{prop!r} at t={flat.start} is past "
                        f"the shot's end ({shot_duration}s) and can never show.",
                        CutoutCompileWarning,
                        stacklevel=3,
                    )
                kfs.append(
                    KeyframeJSON(time=flat.start - first, value=value, easing="step")
                )
            duration = max(0.001, end - first)
            animations[anim_id] = AnimationClipJSON(
                name=anim_id,
                duration=duration,
                channels=[ChannelJSON(target=target, property=prop, keyframes=kfs)],
            )
            hold_by_track.setdefault(_track_root_of(target), []).append(
                PlacedClipJSON(
                    animation_id=anim_id, start_time=first, duration=duration
                )
            )
    # Holds FIRST in every track — see the docstring: later-wins evaluation
    # must let an active tween override a hold at the shared instant.
    for root, holds in hold_by_track.items():
        placed_by_track[root] = holds + placed_by_track.get(root, [])

    # Re-pass: every referenced animation must actually exist. Since an#7
    # every `play` mints its own resolved clip, so this can only fire on a
    # placement built by hand; it once fabricated an empty clip instead,
    # which is how `play` came to look wired up while animating nothing.
    for placed_list in placed_by_track.values():
        for p in placed_list:
            if p.animation_id not in animations:
                raise CutoutCompileError(
                    f"placement references animation {p.animation_id!r}, which "
                    "no clip defines."
                )

    tracks = [
        TrackJSON(target_root=root, clips=clips)
        for root, clips in placed_by_track.items()
    ]
    return animations, tracks


def _compile_one(
    flat: FlatAction, *, ordinal: int
) -> tuple[str | None, str, PlacedClipJSON]:
    """Convert one non-set FlatAction into (animation_id, track_root, placed).

    Set actions never reach here — they compile per (target, property) group
    in :func:`_compile_actions` (an#87).
    """
    action = flat.action
    if isinstance(action, TweenAction):
        anim_id = f"__tween__{ordinal}"
        placed = PlacedClipJSON(
            animation_id=anim_id,
            start_time=flat.start,
            duration=action.duration,
        )
        return anim_id, _track_root_of(action.target), placed
    if isinstance(action, PlayAction):
        # Per-INSTANCE clip (an#7): two plays of one descriptor animation
        # must not share a clip, or the second's loop/speed silently wins
        # for both. `duration=None` keeps the animation's natural duration
        # (the runtime reads a null placement duration as the clip's own).
        anim_id = f"__play__{ordinal}"
        placed = PlacedClipJSON(
            animation_id=anim_id,
            start_time=flat.start,
            duration=action.duration,
            speed=action.speed,
        )
        return anim_id, _track_root_of(action.target), placed
    raise TypeError(f"unsupported FlatAction.action type: {type(action).__name__}")


def _check_keyframe_value(value: Any, *, target: str, prop: str) -> Any:
    """Refuse keyframe values the two evaluators would disagree on.

    ``bool`` is the trap: Python's ``isinstance(True, int)`` would lerp it while
    JS's ``typeof true === 'boolean'`` snaps it — a channel whose value
    interpolates in the spec and snaps in the browser. ``None`` is the other:
    the Python spec would carry it into the pose while the runtime drops null
    values (``runtime.js`` ``evaluateTimeline``), so the two sides render
    different pictures. Neither has a meaning worth keeping: a discrete state
    is a string key, a numeric one is an ``int``/``float``.
    """
    if value is None or isinstance(value, bool):
        raise CutoutCompileError(
            f"keyframe on {target!r}:{prop!r} has value {value!r}; keyframe "
            "values must be numbers (int/float) or string keys — bool and None "
            "evaluate differently in the Python spec and the JS runtime, so "
            "the compiler refuses them rather than pick a side silently."
        )
    return value


def _swap_property_names(flat_list: list[FlatAction]) -> frozenset[str]:
    """Property names in ``flat_list`` that are swap sets, not transforms.

    A property outside the transform vocabulary names an asset set (an#87).
    Derived from the actions rather than the descriptors so the STEP-EASING
    rule below applies even when compiling without a mall.
    """
    out = set()
    for flat in flat_list:
        prop = getattr(flat.action, "property", None)
        if prop and prop not in _PROPERTY_REST_VALUES:
            out.add(prop)
    return frozenset(out)


def _build_anim_for(
    flat: FlatAction,
    anim_id: str,
    *,
    swap_properties: frozenset[str] = frozenset(),
    vocab: _SwapVocabulary | None = None,
    fps: int = 30,
    step_hz: float | None = None,
) -> AnimationClipJSON:
    action = flat.action
    if isinstance(action, PlayAction):
        return _resolve_play(action, anim_id=anim_id, vocab=vocab, fps=fps)
    if isinstance(action, TweenAction):
        from_value = (
            action.from_value
            if action.from_value is not None
            else _rest_value_for(action.property, action.target)
        )
        _check_keyframe_value(from_value, target=action.target, prop=action.property)
        _check_keyframe_value(
            action.to_value, target=action.target, prop=action.property
        )
        easing = _easing_to_json(action.easing)
        # Only an easing the author actually WROTE earns a warning:
        # TweenAction's default is 'ease_in_out', so a swap tween with no
        # easing given would otherwise be told it "asked for" one.
        authored_non_step = (
            action.property in swap_properties
            and "easing" in action.model_fields_set
            and easing != "step"
        )
        if action.property in swap_properties:
            easing = "step"
        if authored_non_step:
            # Swap channels are stepped by FORMAT, not by taste — Spine's
            # attachment keyframes carry {time, name} and no curve field at
            # all. The evaluator already refuses to ease a non-numeric value
            # (the snap is time-based, an#86), so forcing step here changes
            # no pixel; what it changes is honesty — the serialized scene
            # says what will happen. Warn so the author learns the rule
            # rather than wondering where their easing went.
            warnings.warn(
                f"tween on {action.target!r}:{action.property!r} asked for "
                f"easing {action.easing!r}, but {action.property!r} is a swap "
                "set and swap channels are always step-interpolated (a "
                "discrete key cannot be eased). Compiling with easing='step'.",
                CutoutCompileWarning,
                stacklevel=2,
            )
        keyframes = [
            KeyframeJSON(time=0.0, value=from_value, easing=easing),
            KeyframeJSON(time=action.duration, value=action.to_value),
        ]
        # The CONTRACT is this guard: swap properties are never stepped here
        # (they are stepped by format). `_stepped_keyframes`' own non-numeric
        # early return is the defence behind it, so the two are redundant by
        # design — drop this one and the swap test still passes (an#89 review).
        if step_hz is not None and action.property not in swap_properties:
            keyframes = _stepped_keyframes(
                keyframes, start=flat.start, duration=action.duration, step_hz=step_hz
            )
        return AnimationClipJSON(
            name=anim_id,
            duration=action.duration,
            channels=[
                ChannelJSON(
                    target=action.target,
                    property=action.property,
                    keyframes=keyframes,
                )
            ],
        )
    raise TypeError(f"unsupported anim build for {type(action).__name__}")


def step_times(start: float, duration: float, step_hz: float) -> list[float]:
    """Clip-local times at which a stepped tween updates its pose (an#89).

    The grid is SHOT-wide — multiples of ``1/step_hz`` on the shot's clock,
    shared by every tween in the shot — not the tween's own: "on twos" means
    every character changes pose on the same frames, so a tween starting at
    0.033 s updates at the next grid point, not 0.033 s later. (Shots compile
    independently, so the grid restarts at each cut.) Local 0 (the tween's
    start) and ``duration`` (where the end value lands) are always present, so
    a tween shorter than one step is a single step to its end value.

    ``step_hz`` must be positive: with a non-positive rate the walk below never
    reaches ``duration`` — an infinite loop, not an error — so it is refused.

    >>> step_times(0.0, 0.3, 10)
    [0.0, 0.1, 0.2, 0.3]
    >>> [round(t, 3) for t in step_times(0.05, 0.3, 10)]
    [0.0, 0.05, 0.15, 0.25, 0.3]
    >>> step_times(0.0, 0.02, 10)
    [0.0, 0.02]
    """
    if not step_hz > 0:
        raise ValueError(f"step_hz must be positive; got {step_hz!r}")
    eps = 1e-9
    j = math.ceil(start * step_hz - eps)
    times = [0.0]
    while True:
        t = j / step_hz - start
        if t >= duration - eps:
            break
        if t > eps:
            times.append(t)
        j += 1
    times.append(float(duration))
    return times


def _stepped_keyframes(
    keyframes: list[KeyframeJSON], *, start: float, duration: float, step_hz: float
) -> list[KeyframeJSON]:
    """Resample a numeric tween's curve onto the step grid, step-eased.

    The curve is evaluated through the Python spec (`channel.evaluate`) — the
    same evaluator the parity tests hold against the runtime — so a stepped
    tween shows exactly the values the smooth one would at each grid time,
    then holds. Non-numeric (swap) values are left alone: they are stepped by
    format already, and easing never applied to them.
    """
    from an.adapters.cutout.channel import Channel, Keyframe, evaluate

    if not all(isinstance(k.value, (int, float)) for k in keyframes):
        return keyframes
    channel = Channel(
        "_",
        "_",
        [
            Keyframe(
                k.time,
                k.value,
                tuple(k.easing) if isinstance(k.easing, list) else k.easing,
            )
            for k in keyframes
        ],
    )
    return [
        KeyframeJSON(time=t, value=float(evaluate(channel, t)), easing="step")
        for t in step_times(start, duration, step_hz)
    ]


def _resolve_play(
    action: PlayAction, *, anim_id: str, vocab: _SwapVocabulary | None, fps: int
) -> AnimationClipJSON:
    """A ``play`` becomes a clip built from the descriptor animation's tracks
    (an#7). Resolution — which node, which set, which key, and every way it
    can fail — is :func:`an.characters.play.resolve_play`, shared with
    ``an validate`` so the two cannot disagree; this function only converts
    the resolved tracks into channel values.

    Two conversions a naive copy gets wrong (the research reasoned them out,
    `tests/test_play.py` pins them):

    - **Units and reference.** A ``bone:<b>.<prop>`` track is a DEVIATION in
      view-box units (rotation in degrees) around the bone's rest; a channel
      carries ABSOLUTE scene values (radians). So every value is
      ``rest + deviation * k`` (positions scale by the rig's view_box → pixel
      factor; rotation by pi/180), read off the built node's transform — a
      naive copy would put the torso at y≈±2 instead of bobbing around its
      rest, and `rotation_deg` is not a runtime property at all.
    - **Attachment swaps.** A ``slot:<s>.attachment`` track names
      ATTACHMENTS; a swap channel carries set KEYS. The whole track resolves
      to ONE set (never split per frame across two channels) and rides the
      same runtime swap path an authored ``set`` does.

    Sine tracks are sampled at the frame rate with linear easing, always
    closing the cycle at the clip end; step and linear tracks map 1:1.
    ``loop`` is the action's override or, when the action says nothing, the
    animation's own.
    """
    entity_id = _track_root_of(action.target)
    if vocab is None or entity_id not in vocab.descriptors:
        raise CutoutCompileError(
            f"play of {action.animation!r} on {entity_id!r}: named animations "
            "live in a character descriptor's `animations`, and this entity "
            "has no descriptor (a procedural rig has none). Use tween / set."
        )
    desc = vocab.descriptors[entity_id]
    try:
        resolved = resolve_play(
            desc, action.animation, art_exists=vocab.art_exists.get(entity_id)
        )
    except PlayResolutionError as e:
        raise CutoutCompileError(
            f"play of {action.animation!r} on {entity_id!r}: " + "; ".join(e.problems)
        ) from e
    anim = resolved.animation
    k = vocab.entity_scale.get(entity_id, 1.0)
    duration = max(0.001, float(anim.duration))
    channels: list[ChannelJSON] = []
    for rt in resolved.tracks:
        path = (
            entity_id
            if isinstance(rt, BoneTrack) and rt.slot is None
            else f"{entity_id}/{slot_node_path(desc, rt.slot)}"
        )
        if path not in vocab.paths:
            # Resolution mirrors the rig builder, so this is a bug in one of
            # the two rather than an authoring error — say which node.
            raise CutoutCompileError(
                f"play of {action.animation!r}: track {rt.track.target!r} "
                f"resolved to node {path!r}, which the built scene does not "
                f"carry (built: {sorted(p for p in vocab.paths if p.startswith(entity_id))})."
            )
        if isinstance(rt, BoneTrack):
            rest_value = float(getattr(vocab.node_transforms[path], rt.property))
            scale = rt.unit * (k if rt.rig_scaled else 1.0)
            if rt.track.type == "sine":
                kfs = [
                    KeyframeJSON(
                        time=t, value=rest_value + dev * scale, easing="linear"
                    )
                    for t, dev in sampled_deviations(rt.track, duration, fps)
                ]
            else:
                easing = "step" if rt.track.type == "step" else "linear"
                kfs = [
                    KeyframeJSON(
                        time=float(ft),
                        value=rest_value + float(fv) * scale,
                        easing=easing,
                    )
                    for ft, fv in rt.track.frames
                ]
            channels.append(
                ChannelJSON(target=path, property=rt.property, keyframes=kfs)
            )
        else:
            channels.append(
                ChannelJSON(
                    target=path,
                    property=rt.set_name,
                    keyframes=[
                        KeyframeJSON(time=t, value=key, easing="step")
                        for t, key in rt.frames
                    ],
                )
            )
    loop = action.loop if action.loop is not None else bool(anim.loop)
    return AnimationClipJSON(
        name=anim_id,
        duration=duration,
        loop_mode="loop" if loop else "once",
        channels=channels,
    )


def _check_swap_action(
    flat: FlatAction,
    *,
    vocab: _SwapVocabulary,
    resolutions: list[AssetResolutionJSON] | None,
) -> bool:
    """Validate one authored action's swap references; decide if it compiles.

    Returns True to keep the action, False to drop it (with a fallback record
    — the usage-aware escalation of an#87: a key the author USES whose art is
    missing is a wrong picture wearing a right one's clothes, so it joins the
    an#33/#76 machinery and turns fatal under ``strict_assets``; an inventory
    gap nobody references stays a non-fatal 'incomplete').

    Raises for the mistakes that are never a stand-in: an undeclared set, an
    undeclared key, a target that cannot carry the set. Transform properties
    pass through untouched — their targets stay runtime-checked, as before.
    """
    action = flat.action
    prop = getattr(action, "property", None)
    if prop is None or prop in _PROPERTY_REST_VALUES:
        return True
    if not isinstance(action, (SetAction, TweenAction)):
        return True
    target = action.target
    entity_id = _track_root_of(target)
    values = (
        [action.value]
        if isinstance(action, SetAction)
        else [v for v in (action.from_value, action.to_value) if v is not None]
    )

    declared_sets = vocab.declared.get(entity_id)
    if declared_sets is None:
        # No descriptor: what the BUILT nodes declare is the vocabulary — the
        # procedural rig's drawn mouth carries its `viseme` set on its visual,
        # exactly as an SVG rig's projections do. Exact key match, like every
        # other set: a lowercase code used to be silently drawn as rest.
        declared_sets = {}
        for path, sets in vocab.node_sets.items():
            if path.split("/", 1)[0] == entity_id:
                for set_name, key_map in sets.items():
                    declared_sets.setdefault(set_name, frozenset())
                    declared_sets[set_name] = declared_sets[set_name] | frozenset(
                        key_map
                    )
        if not declared_sets:
            raise CutoutCompileError(
                f"action targets {target!r}:{prop!r}, which is not a transform "
                f"property, and {entity_id!r} declares no asset sets (no "
                "descriptor, and no built node carries a set). Transform "
                f"properties are: {sorted(TRANSFORM_PROPERTIES)}."
            )

    if prop not in declared_sets:
        raise CutoutCompileError(
            f"action targets {target!r}:{prop!r}, but {entity_id!r}'s "
            f"descriptor declares no asset set named {prop!r} (it has: "
            f"{sorted(declared_sets)}). A property that is not a transform "
            "must name a declared swap set."
        )
    for v in values:
        if not isinstance(v, str) or v not in declared_sets[prop]:
            raise CutoutCompileError(
                f"action sets {target!r}:{prop!r} to {v!r}, which is not a "
                f"declared key of that set (it has: "
                f"{sorted(declared_sets[prop])})."
            )
    if target not in vocab.paths:
        raise CutoutCompileError(
            f"action targets {target!r}, which is not a node in the built "
            f"scene. Known paths: {sorted(vocab.paths)}"
        )

    node_map = vocab.node_sets.get(target, {}).get(prop)
    if node_map is None:
        capable = vocab.swap_capable_paths(entity_id, prop)
        if capable:
            raise CutoutCompileError(
                f"action targets {target!r}:{prop!r}, but the {prop!r} set "
                f"resolves on {capable}, not on that node. Target one of "
                "those paths."
            )
        _record_used_swap_fallback(
            resolutions,
            entity_id,
            target,
            prop,
            detail=(
                f"the {prop!r} set is declared but none of its art resolved, "
                f"so the authored swap on {target!r} cannot be shown; the "
                "channel was dropped"
            ),
        )
        return False
    missing = [str(v) for v in values if str(v) not in node_map]
    if missing:
        _record_used_swap_fallback(
            resolutions,
            entity_id,
            target,
            prop,
            detail=(
                f"the authored swap uses key(s) {missing} of the {prop!r} "
                f"set, whose art did not resolve on {target!r} (resolved "
                f"keys: {sorted(node_map)}); the channel was dropped"
            ),
        )
        return False
    return True


def _record_used_swap_fallback(
    resolutions: list[AssetResolutionJSON] | None,
    entity_id: str,
    target: str,
    prop: str,
    *,
    detail: str,
) -> None:
    """A USED swap key with missing art joins the fallback bucket (an#87).

    fallback=True is the load-bearing bit: `_raise_or_warn_on_asset_fallbacks`
    only surfaces fallback entries, so this is what makes the drop audible by
    default and fatal under ``strict_assets`` — where an unreferenced
    inventory gap stays a mute 'incomplete' record, deliberately.
    """
    if resolutions is None:
        return
    resolutions.append(
        AssetResolutionJSON(
            id=entity_id,
            kind="swap",
            store="characters",
            ref=f"{target}:{prop}",
            resolved="dropped",
            fallback=True,
            detail=detail,
        )
    )


def _easing_to_json(spec: Any) -> Any:
    if spec is None:
        return None
    if isinstance(spec, str):
        return spec
    if isinstance(spec, (list, tuple)):
        return list(spec)
    return None


def _track_root_of(target: str) -> str:
    """The first segment of a target path is the track root (the entity name)."""
    return target.split("/", 1)[0] if target else ""


# -----------------------------------------------------------------------------
# Phase 4: dialogue → viseme channels on the speaker's mouth node
# -----------------------------------------------------------------------------


def _baked_face_speakers(shot: Shot, mall: Mapping[str, Mapping] | None) -> set[str]:
    """Return the entity ids whose backing descriptor declares a baked face.

    Used to suppress viseme channels for characters that don't have an
    overlay mouth node (DiceBear / external avatars). See
    ``_build_svg_character_subtree`` for the matching scene-tree branch.

    Reads the **migrated, validated** descriptor's declared ``face_overlay``
    fact — the predecessor read the RAW store dict's ``art_provenance`` with
    no ``kind`` guard, so (a) a migration-seeded field was invisible to it,
    and (b) a legacy ``parts``-rig carrying ``art_provenance="dicebear"`` got
    a full procedural mouth built and its viseme channel suppressed — a drawn
    mouth that never moved, silently. Only descriptor-backed entities can
    declare a baked face now; the two consumer sites read one model.
    """
    if not mall:
        return set()
    chars_store = mall.get("characters") or {}
    out: set[str] = set()
    for entity in shot.entities:
        if entity.kind != "character":
            continue
        ref = entity.ref
        if ref is None or ref not in chars_store:
            continue
        try:
            desc_data = chars_store[ref]
        except KeyError:
            continue
        if (
            not isinstance(desc_data, dict)
            or desc_data.get("kind") != "CharacterDescriptor"
        ):
            continue
        desc = CharacterDescriptor.model_validate(
            migrate(dict(desc_data), kind=CHARACTER_DOCUMENT_KIND.name)
        )
        if not desc.face_overlay:
            out.add(entity.id)
    return out


def _add_viseme_clips(
    shot: Shot,
    animations: dict[str, AnimationClipJSON],
    tracks: list[TrackJSON],
    *,
    mall: Mapping[str, Mapping] | None = None,
    vocab: _SwapVocabulary | None = None,
) -> None:
    """For each dialogue line with a viseme_track, emit a step swap channel on
    every node of the speaker that can apply the ``viseme`` set.

    Side-effects ``animations`` (adds named clips) and ``tracks`` (appends to
    or creates the speaker's track).

    The target is discovered from the BUILT scene, not a path literal (an#87):
    a node applies ``viseme`` when its visual carries the set's projection (an
    SVG mouth) or is the procedural drawn mouth. The old
    ``f"{speaker}/head/mouth"`` literal was the last place the compiler
    hardcoded where a mouth lives.

    Two kinds of speaker get no viseme channel, for the same reason: there is no
    mouth node for it to target.

    - Speakers whose descriptor declares ``face_overlay=False`` (DiceBear /
      external avatars) — the face is drawn into the head SVG, so there is no
      overlay mouth.
    - **Speakers with no viseme-capable node in the built scene.** One
      condition, two cases that are indistinguishable from here: the
      off-screen-narrator idiom (a speaker deliberately not an entity — the
      standing workaround while ``Shot.narration`` is unimplemented), and a
      character who IS on screen but whose rig has no mouth, which an
      entity-membership check misses and sends to a hard render failure.

    The second kind WARNS rather than passing in silence, because it cannot be
    told apart from a typo: ``speaker="charlei"`` against an on-screen
    ``charlie`` otherwise loses its lip-sync quietly while the audio still
    plays. Naming the scene's actual mouths makes the typo obvious.

    Codes are upper-cased at EMISSION (the Rhubarb convention) — case used to
    be normalised in the runtime's sprite path only, so a lowercase ``'a'``
    swapped correctly on an SVG rig and silently drew rest on a procedural
    one. And a code the target cannot show is DROPPED here with a warning,
    never carried into the scene: the runtime now throws on an unknown swap
    key (the loud half of an#87), so compiled scenes must be total.
    """
    face_baked = _baked_face_speakers(shot, mall)
    track_lookup: dict[str, TrackJSON] = {t.target_root: t for t in tracks}
    for i, line in enumerate(shot.dialogue):
        if line.viseme_track is None or not line.viseme_track.keyframes:
            continue
        if line.start is None or line.duration is None:
            # No timing assigned (audio pipeline didn't run); skip silently.
            continue
        speaker = line.speaker
        if speaker in face_baked:
            continue
        mouth_paths = (
            vocab.swap_capable_paths(speaker, VISEME_CHANNEL)
            if vocab is not None
            else []
        )
        if not mouth_paths:
            if vocab is None:
                continue
            all_mouths = sorted(
                p for p, sets in vocab.node_sets.items() if VISEME_CHANNEL in sets
            )
            warnings.warn(
                f"shot {shot.id!r} dialogue line {i} is spoken by {speaker!r}, "
                "which has no viseme-capable mouth node in the scene: it gets "
                "audio but no lip-sync. Expected for an off-screen narrator. "
                f"If it was not, the scene's mouths are: {all_mouths or 'none'}.",
                CutoutCompileWarning,
                stacklevel=2,
            )
            continue

        # Build viseme keyframes (step-easing) — but cap density so adjacent
        # keyframes are at least _MIN_VISEME_GAP_S apart. Reduces the
        # "twitchy" look of per-character distribution at high densities.
        raw = [
            (float(kf.time), str(kf.viseme).upper())
            for kf in line.viseme_track.keyframes
        ]
        condensed: list[tuple[float, str]] = []
        for t, v in raw:
            if condensed and (t - condensed[-1][0]) < _MIN_VISEME_GAP_S:
                continue
            condensed.append((t, v))

        for target in mouth_paths:
            mapped = set(vocab.node_sets[target][VISEME_CHANNEL])
            rest = vocab.rest_key(target, VISEME_CHANNEL)
            usable = [(t, v) for t, v in condensed if v in mapped]
            dropped = sorted({v for _, v in condensed} - mapped)
            if dropped:
                warnings.warn(
                    f"shot {shot.id!r} dialogue line {i}: viseme code(s) "
                    f"{dropped} have no resolved art on {target!r} (it has: "
                    f"{sorted(mapped)}); those keyframes were dropped, so the "
                    "mouth holds its previous shape through them.",
                    CutoutCompileWarning,
                    stacklevel=2,
                )
            if rest is None:
                warnings.warn(
                    f"shot {shot.id!r} dialogue line {i}: {target!r} has no "
                    "rest key in its viseme set (no key maps to the node's "
                    "default attachment, and there is no 'X'), so no viseme "
                    "channel was emitted for it — a mouth that cannot close "
                    "should not start talking.",
                    CutoutCompileWarning,
                    stacklevel=2,
                )
                continue
            kfs: list[KeyframeJSON] = [
                KeyframeJSON(time=max(0.0, t), value=v, easing="step")
                for t, v in usable
                if t < line.duration
            ]
            # The rest key is an INVARIANT, not a conditionally-appended
            # keyframe: a raw keyframe landing at (or clamping to) exactly
            # line.duration used to suppress the append, freezing the mouth
            # in its last viseme forever after the line. And it is DERIVED
            # (the key whose art is the node's default attachment), not the
            # literal 'X' — a set keyed by MPEG-4 numbers or Azure names
            # closes its mouth too.
            kfs.append(KeyframeJSON(time=line.duration, value=rest, easing="step"))

            anim_id = f"__viseme__{shot.id}_{i}_{target.replace('/', '.')}"
            animations[anim_id] = AnimationClipJSON(
                name=anim_id,
                duration=line.duration,
                channels=[
                    ChannelJSON(target=target, property=VISEME_CHANNEL, keyframes=kfs),
                ],
            )
            track = track_lookup.get(speaker)
            if track is None:
                track = TrackJSON(target_root=speaker, clips=[])
                tracks.append(track)
                track_lookup[speaker] = track
            track.clips.append(
                PlacedClipJSON(
                    animation_id=anim_id,
                    start_time=float(line.start),
                    duration=float(line.duration),
                )
            )

        # Emotion-driven eyebrow expression — set both brows' rotation while
        # the line is active, restore to neutral at the end. Node existence is
        # checked (an#87): a rig whose brow art failed to resolve drops the
        # node silently, and an unchecked channel then hard-crashed at frame
        # time in applyPose — the exact policy the mouth check above exists
        # for, previously applied to only one of the two emissions.
        emotion = (line.emotion or "").lower().strip()
        if emotion in _EMOTION_BROWS:
            track = track_lookup.get(speaker)
            tilt_l, tilt_r = _EMOTION_BROWS[emotion]
            for brow_name, tilt in (("left_brow", tilt_l), ("right_brow", tilt_r)):
                brow_target = f"{speaker}/head/{brow_name}"
                if vocab is not None and brow_target not in vocab.paths:
                    warnings.warn(
                        f"shot {shot.id!r} dialogue line {i} carries emotion "
                        f"{emotion!r}, but {brow_target!r} is not in the built "
                        "scene (brow art missing?); its channel was not "
                        "emitted.",
                        CutoutCompileWarning,
                        stacklevel=2,
                    )
                    continue
                emo_anim_id = f"__emo__{shot.id}_{i}_{brow_name}"
                animations[emo_anim_id] = AnimationClipJSON(
                    name=emo_anim_id,
                    duration=line.duration,
                    channels=[
                        ChannelJSON(
                            target=brow_target,
                            property="rotation",
                            keyframes=[
                                KeyframeJSON(time=0.0, value=tilt, easing="step"),
                                KeyframeJSON(
                                    time=line.duration, value=0.0, easing="step"
                                ),
                            ],
                        )
                    ],
                )
                if track is None:
                    track = TrackJSON(target_root=speaker, clips=[])
                    tracks.append(track)
                    track_lookup[speaker] = track
                track.clips.append(
                    PlacedClipJSON(
                        animation_id=emo_anim_id,
                        start_time=float(line.start),
                        duration=float(line.duration),
                    )
                )


# -----------------------------------------------------------------------------
# Blinks: compiled per eye (an#88)
# -----------------------------------------------------------------------------


def _add_blink_clips(
    shot: Shot,
    animations: dict[str, AnimationClipJSON],
    tracks: list[TrackJSON],
    *,
    vocab: _SwapVocabulary,
    fps: int,
    mall: Mapping[str, Mapping] | None = None,
) -> dict[str, float]:
    """Emit blink clips per eye node of every character; return the phases
    used, per entity, for the compiled scene's meta.

    A character whose descriptor declares ``face_overlay=False`` never
    blinks, whatever eye nodes the rig builder produced — the policy lives
    here, not in a naming coincidence of the rig builder.

    Mechanism splits by what the eye can do (research §6):

    - an eye whose visual projects the ``eyelid`` set with both ``OPEN`` and
      ``CLOSED`` resolved, and whose rest attachment IS the open one, swaps
      ART — a step channel through the one swap implementation, CLOSED for
      the central half of each blink window;
    - any other eye (the procedural drawn eye; a descriptor rig without
      closed-eye art) gets the sine SQUASH the runtime used to apply, as a
      ``scale_y`` channel sampled at the frame times — a tween, not a swap,
      so "one swap implementation" holds without contortions;
    - an eye that rests CLOSED (a sleeping character) does not blink at all:
      the author closed it, and the old runtime never opened it either.

    **One clip per blink window, not one whole-shot fill.** Outside a window
    the pose then carries NO eye value, so an authored eye channel's end
    value persists exactly as every other property's does (the runtime's
    stateful hold); a whole-shot ``1.0`` fill snapped an authored ``scale_y``
    back the frame after its tween ended (an#88 review). Each clip is
    extended to the first frame time at or after the window's end so the
    return-to-rest keyframe is applied on a rendered frame — the runtime's
    per-frame reset, reproduced at exactly the frames that matter.

    The clips go at the FRONT of the entity's track: later-wins evaluation
    then lets an authored eye channel override a blink, where the old
    post-pose reset clobbered any authored ``scale_y`` on every frame.
    """
    phases: dict[str, float] = {}
    baked = _baked_face_speakers(shot, mall)
    track_lookup: dict[str, TrackJSON] = {t.target_root: t for t in tracks}
    for entity in shot.entities:
        if entity.kind != "character" or entity.id in baked:
            continue
        eyes = sorted(
            p
            for p in vocab.paths
            if p.split("/", 1)[0] == entity.id
            and p.rsplit("/", 1)[-1] in EYE_NODE_NAMES
        )
        if not eyes:
            continue
        windows = _blink_windows(entity.id, shot.duration)
        phases[entity.id] = blink_phase(entity.id)
        placed: list[PlacedClipJSON] = []
        for path in eyes:
            eyelid = vocab.node_sets.get(path, {}).get(EYELID_CHANNEL) or {}
            has_art = "OPEN" in eyelid and "CLOSED" in eyelid
            rest = vocab.rest_key(path, EYELID_CHANNEL) if has_art else None
            if has_art and rest not in (None, "OPEN"):
                continue  # rests closed: the author's call, not a blink's
            use_swap = has_art and rest == "OPEN"
            for start, end in windows:
                clip_start = max(0.0, start)
                # Extend to the first rendered frame at/after the window end
                # (capped at the shot), so the rest keyframe is APPLIED.
                clip_end = min(shot.duration, math.ceil(end * fps - 1e-9) / fps)
                if clip_end <= clip_start:
                    continue
                span = end - start
                if use_swap:
                    lo, hi = _EYELID_CLOSED_SPAN
                    t_closed, t_open = start + lo * span, start + hi * span
                    initial = "CLOSED" if t_closed <= clip_start < t_open else "OPEN"
                    kfs = [KeyframeJSON(time=0.0, value=initial, easing="step")]
                    for time, key in ((t_closed, "CLOSED"), (t_open, "OPEN")):
                        if clip_start < time <= clip_end:
                            kfs.append(
                                KeyframeJSON(
                                    time=time - clip_start, value=key, easing="step"
                                )
                            )
                    prop = EYELID_CHANNEL
                else:

                    def squash(time: float) -> float:
                        u = (time - start) / span
                        if u <= 0.0 or u >= 1.0:
                            return 1.0
                        return 1.0 - _BLINK_DEPTH * math.sin(u * math.pi)

                    first_f = math.ceil(clip_start * fps - 1e-9)
                    last_f = math.floor(clip_end * fps + 1e-9)
                    times = sorted(
                        {clip_start, clip_end}
                        | {f / fps for f in range(first_f, last_f + 1)}
                    )
                    kfs = [
                        KeyframeJSON(
                            time=time - clip_start, value=squash(time), easing="linear"
                        )
                        for time in times
                        if clip_start <= time <= clip_end
                    ]
                    prop = "scale_y"
                anim_id = f"__blink__{shot.id}_{path.replace('/', '.')}_{len(placed)}"
                duration = max(0.001, clip_end - clip_start)
                animations[anim_id] = AnimationClipJSON(
                    name=anim_id,
                    duration=duration,
                    channels=[ChannelJSON(target=path, property=prop, keyframes=kfs)],
                )
                placed.append(
                    PlacedClipJSON(
                        animation_id=anim_id, start_time=clip_start, duration=duration
                    )
                )
        if not placed:
            continue
        track = track_lookup.get(entity.id)
        if track is None:
            track = TrackJSON(target_root=entity.id, clips=[])
            tracks.append(track)
            track_lookup[entity.id] = track
        track.clips[:0] = placed
    return phases


# -----------------------------------------------------------------------------
# Phase 7: camera moves wired to root-container scale animation
# -----------------------------------------------------------------------------


# How much each named camera move zooms (final scale relative to start).
_CAMERA_MOVES: dict[str, tuple[float, float]] = {
    "hold": (1.0, 1.0),
    "push_in": (1.0, 1.25),
    "pull_out": (1.0, 0.8),
    "zoom_in": (1.0, 1.5),
    "zoom_out": (1.0, 0.7),
}


def _add_camera_clips(
    shot: Shot,
    animations: dict[str, AnimationClipJSON],
    tracks: list[TrackJSON],
) -> None:
    """If shot.camera.move is a known named move, emit a scale tween on the
    synthetic scene root over the shot's full duration.

    The synthetic root container in the JS runtime sits at canvas center and
    scales the entire scene; per-character motion remains independent.
    """
    if shot.camera is None or shot.camera.move is None:
        return
    # Normalise BEFORE the emptiness test, or the guard grows an arbitrary seam:
    # `move=""` fell through the falsiness check and was ignored, while
    # `move="  "` reached the lookup and raised. Same input, two behaviours.
    move = shot.camera.move.strip()
    if not move:
        return
    if move == "hold":
        return  # a real, correct no-op — not an unknown move
    if move not in _CAMERA_MOVES:
        raise CutoutCompileError(
            f"shot {shot.id!r} asks for camera.move={move!r}, which the cutout "
            f"renderer does not implement (it has: {sorted(_CAMERA_MOVES)}). "
            "A translating camera — pan, track, whip-pan — needs a real 2D "
            "camera node with per-layer parallax, which is planned (see "
            "https://github.com/thorwhalen/an/issues/9); today the camera is a "
            "scale tween on the scene root and cannot move sideways."
        )
    start_scale, end_scale = _CAMERA_MOVES[move]
    duration = max(0.001, float(shot.duration))
    for axis in ("scale_x", "scale_y"):
        anim_id = f"__camera__{shot.id}_{axis}"
        animations[anim_id] = AnimationClipJSON(
            name=anim_id,
            duration=duration,
            channels=[
                ChannelJSON(
                    target="root",
                    property=axis,
                    keyframes=[
                        KeyframeJSON(time=0.0, value=start_scale, easing="ease_in_out"),
                        KeyframeJSON(time=duration, value=end_scale),
                    ],
                )
            ],
        )
        tracks.append(
            TrackJSON(
                target_root="__camera__",
                clips=[
                    PlacedClipJSON(
                        animation_id=anim_id, start_time=0.0, duration=duration
                    )
                ],
            )
        )
