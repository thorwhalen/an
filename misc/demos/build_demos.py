"""Render one short clip per shipped capability, and write the gallery that explains them.

A feature nobody can see is a feature nobody believes. This builds a fixed set of
**self-contained** demo projects — every character is synthesized offline, every scene is
authored here — renders each to mp4, converts each to a GIF that survives GitHub's markdown,
and emits ``GALLERY.md`` naming, per demo, the exact command / argument / code that makes it
happen.

Deliberately **not** the bench corpus. The corpus exists to make a deliberate degradation
move a declared number; these exist to be looked at. Sharing scenes between the two would
make one of them a hostage of the other.

Offline and free by construction: ``use_dicebear=False`` (no network, and no CC-BY style to
attribute) and ``tts="offline"`` (no paid API — this script must stay runnable by an
unattended agent that happens to have keys in its environment).

Run::

    python misc/demos/build_demos.py                # everything
    python misc/demos/build_demos.py lipsync camera # named demos only
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import textwrap
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

REPO_ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = REPO_ROOT / "misc" / "demos" / "out"

#: GIF recipe for flat cutout art. `dither=none` because dithering a flat fill invents
#: texture that is not in the render, and a limited palette because these frames genuinely
#: hold few colours — the whole point of `frame_distinct_colours` being a small number.
#: 12 rather than the render's 24. A GIF stores whole frames, so halving the
#: rate halves the file; at this scale nothing in these clips moves fast enough
#: for the drop to read. The mp4 beside each GIF keeps the full rate.
GIF_FPS: int = 12
GIF_WIDTH: int = 480
GIF_MAX_COLOURS: int = 128

#: Refuse to publish a GIF larger than this. GitHub renders bigger ones, but a
#: reader on a phone pays for every byte and a demo nobody waits for is not a
#: demo.
GIF_WARN_BYTES: int = 1_500_000

#: Every demo renders at this size unless it says otherwise. Small enough that a GIF stays
#: under a megabyte, large enough that the rig reads.
DEMO_RESOLUTION: tuple[int, int] = (480, 270)
DEMO_FPS: int = 24


@dataclass(frozen=True, slots=True)
class Demo:
    """One clip, and the sentence that says what makes it happen."""

    slug: str
    title: str
    shows: str
    how: str
    build: Callable[[Path], Path]
    #: ffmpeg `crop` expression applied to the GIF only, when the thing being
    #: demonstrated is smaller than the frame. Stated in the gallery, never
    #: silent — a crop is a claim about where to look.
    crop: str = ""


def _scene(body: str) -> str:
    return textwrap.dedent(body).lstrip()


def _meta(title: str, duration: float) -> str:
    """The `yaml meta` block. The title is JSON-quoted: YAML chokes on a bare colon."""
    w, h = DEMO_RESOLUTION
    return _scene(
        f"""
        # {title}

        ```yaml meta
        title: {json.dumps(title)}
        author: an
        duration: {duration}
        fps: {DEMO_FPS}
        resolution:
          width: {w}
          height: {h}
        default_style: cutout
        ```
        """
    )


def _project(work: Path, *, scene_md: str, characters: tuple[str, ...]) -> Path:
    """A throwaway `an` project with synthesized characters and the given scene."""
    from an.characters import new_character

    project = work
    (project / "assets" / "characters").mkdir(parents=True, exist_ok=True)
    for name in characters:
        new_character(
            project / "assets" / "characters",
            name=name,
            seed=name,
            use_dicebear=False,  # offline, and no third-party licence to carry
            overwrite=True,
        )
    (project / "scene.md").write_text(scene_md, encoding="utf-8")
    return project


def _render(project: Path, **kwargs) -> Path:
    from an.project import load
    from an.render import render

    return Path(render(load(project), tts="offline", lipsync="offline", **kwargs))


def to_gif(mp4: Path, gif: Path, *, crop: str = "") -> Path:
    """mp4 -> GIF, with a palette generated from the clip's own colours."""
    crop_clause = f"crop={crop}," if crop else ""
    vf = (
        f"{crop_clause}fps={GIF_FPS},scale={GIF_WIDTH}:-1:flags=neighbor"
        f",split[a][b];"
        f"[a]palettegen=max_colors={GIF_MAX_COLOURS}[p];[b][p]paletteuse=dither=none"
    )
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-i", str(mp4), "-vf", vf, str(gif)],
        check=True,
    )
    return gif


# -----------------------------------------------------------------------------
# The demos
# -----------------------------------------------------------------------------


def _entities(*names: str) -> str:
    rows = "\n".join(
        f"- kind: character\n  id: {n}\n  store: characters\n  ref: {n}" for n in names
    )
    return "```yaml entities\n" + rows + "\n```\n"


def _shot(shot_id: str, duration: float, *, camera: str | None = None) -> str:
    cam = f"\ncamera:\n  move: {camera}" if camera else ""
    return (
        f"## Shot {shot_id} (cutout)\n\n```yaml shot\nduration: {duration}{cam}\n```\n"
    )


def _build_text_to_video(work: Path) -> Path:
    md = (
        _meta("From text to video", 3.0)
        + "\n"
        + _shot("s1", 3.0)
        + "\n"
        + _entities("charlie")
        + "\n```dialogue\ncharlie: This whole shot is twenty lines of markdown.\n```\n"
    )
    return _render(_project(work, scene_md=md, characters=("charlie",)))


def _build_lipsync(work: Path) -> Path:
    md = (
        _meta("Lip-sync from the audio, not by hand", 5.0)
        + "\n"
        + _shot("s1", 5.0)
        + "\n"
        + _entities("maya")
        + "\n```dialogue\nmaya: Every mouth shape you see was placed by the "
        "viseme track, not by a keyframe anybody drew.\n```\n"
    )
    return _render(_project(work, scene_md=md, characters=("maya",)))


#: The emotions the demo grid shows, in reading order — four of the ten
#: presets in `an.expression.presets`, the ones whose faces are furthest apart.
GRID_EMOTIONS: tuple[str, ...] = ("neutral", "happy", "angry", "surprised")

#: Head-and-shoulders, as an ffmpeg `crop` expression applied to one pane. The
#: rig places the character centred, head in the upper half, so this is a
#: property of the rig rather than of any one scene.
PANE_CROP: str = "in_w/3:in_h/2:in_w/3:0"
#: Tighter: the head alone, for the face demos where a brow move is the subject.
FACE_CROP: str = "in_w/4:in_h/3:3*in_w/8:0"


def _tile_2x2(clips: list[Path], out: Path, *, crop: str = PANE_CROP) -> Path:
    """Play four equal-sized clips at once, in a 2x2 grid.

    Side by side rather than one after another, because a brow tilt of 0.15 rad
    is a few pixels: sequentially the reader has to hold the previous face in
    their head, and in a grid they do not. `xstack` rather than a burned-in
    label per pane, because `drawtext` needs a freetype-enabled ffmpeg and this
    script must run on whichever one a contributor has. The pane order lives in
    the gallery text instead.
    """
    inputs: list[str] = []
    for clip in clips:
        inputs += ["-i", str(clip)]
    # Crop EACH pane before stacking, not the grid afterwards: one crop over a
    # 2x2 grid straddles the seam between panes.
    crops = "".join(f"[{i}:v]crop={crop}[p{i}];" for i in range(len(clips)))
    panes = "".join(f"[p{i}]" for i in range(len(clips)))
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            *inputs,
            "-filter_complex",
            f"{crops}{panes}xstack=inputs={len(clips)}:"
            "layout=0_0|w0_0|0_h0|w0_h0:fill=white[v]",
            "-map",
            "[v]",
            "-an",
            str(out),
        ],
        check=True,
    )
    return out


def _expression_grid(work: Path, presets: tuple[str, ...], *, line: str | None) -> Path:
    """Four one-shot renders, one preset each, played simultaneously — silent
    (the expression alone) or all saying `line` (the mouth form under it)."""
    clips: list[Path] = []
    for preset in presets:
        md = (
            _meta(f"expression: {preset}", 2.0)
            + "\n"
            + _shot("s1", 2.0)
            + "\n"
            + _entities("charlie")
            + f"\n```yaml actions\n- kind: expression\n  target: charlie\n  preset: {preset}\n  blend: 0.25\n```\n"
            + (f"\n```dialogue\ncharlie: {line}\n```\n" if line else "")
        )
        pane = work / preset
        clips.append(_render(_project(pane, scene_md=md, characters=("charlie",))))
    return _tile_2x2(clips, work / "grid.mp4", crop=FACE_CROP)


def _build_expressions(work: Path) -> Path:
    return _expression_grid(work, GRID_EMOTIONS, line=None)


def _build_expressions_more(work: Path) -> Path:
    return _expression_grid(work, ("sad", "afraid", "thinking", "skeptical"), line=None)


def _build_emotion_visemes(work: Path) -> Path:
    """The same line twice, side by side: the neutral mouth set on the left,
    the `viseme@happy` variant the `happy` preset selects on the right."""
    line = "Every shape you see is the same shape, drawn twice."
    clips: list[Path] = []
    for preset in ("neutral", "happy"):
        md = (
            _meta(f"mouth form: {preset}", 3.0)
            + "\n"
            + _shot("s1", 3.0)
            + "\n"
            + _entities("maya")
            + f"\n```yaml actions\n- kind: expression\n  target: maya\n  preset: {preset}\n  blend: 0.0\n```\n"
            + f"\n```dialogue\nmaya: {line}\n```\n"
        )
        clips.append(
            _render(_project(work / preset, scene_md=md, characters=("maya",)))
        )
    out = work / "side_by_side.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-i",
            str(clips[0]),
            "-i",
            str(clips[1]),
            "-filter_complex",
            f"[0:v]crop={FACE_CROP}[a];[1:v]crop={FACE_CROP}[b];[a][b]hstack=inputs=2",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-an",
            str(out),
        ],
        check=True,
    )
    return out


def _build_gaze(work: Path) -> Path:
    """The same authored sweep twice, side by side: the pupils alone on the
    left (a character that has the eye stack but whose saccades are held at
    centre by compiling with a zero-amplitude generator is not a knob the
    scene has — so the left pane is a rig WITHOUT the stack, whose eyes are
    the single pre-Wave-6 drawing and cannot move), the sweep plus ambient
    saccades on the right (a rig with the stack)."""
    actions = (
        "\n```yaml actions\n"
        "- kind: expression\n  target: maya\n  axes: {gaze_x: -1.0}\n  duration: 1.0\n  blend: 0.3\n"
        "- kind: expression\n  target: maya\n  axes: {gaze_x: 1.0}\n  duration: 1.0\n  blend: 0.3\n  start: 1.0\n"
        "- kind: expression\n  target: maya\n  axes: {gaze_y: 1.0}\n  duration: 1.0\n  blend: 0.3\n  start: 2.0\n"
        "```\n"
    )
    md = _meta("Gaze: an authored sweep, with and without the eye stack", 3.5) + "\n" + _shot("s1", 3.5) + "\n" + _entities("maya") + actions
    clips = []
    for variant, gaze in (("without", False), ("with", True)):
        project = work / variant
        (project / "assets" / "characters").mkdir(parents=True, exist_ok=True)
        from an.characters import new_character

        new_character(project / "assets" / "characters", name="maya", seed="maya", use_dicebear=False, overwrite=True, gaze=gaze)
        (project / "scene.md").write_text(md, encoding="utf-8")
        clips.append(_render(project))
    out = work / "side_by_side.mp4"
    subprocess.run(
        [
            "ffmpeg", "-v", "error", "-y", "-i", str(clips[0]), "-i", str(clips[1]),
            "-filter_complex",
            f"[0:v]crop={FACE_CROP}[a];[1:v]crop={FACE_CROP}[b];[a][b]hstack=inputs=2",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-an", str(out),
        ],
        check=True,
    )
    return out


def _build_gaze_plus_expression(work: Path) -> Path:
    """`angry` held while an authored gaze sweeps — two contributors on one
    face, summed by the solver, plus the ambient saccades."""
    actions = (
        "\n```yaml actions\n"
        "- kind: expression\n  target: charlie\n  preset: angry\n  blend: 0.25\n"
        "- kind: expression\n  target: charlie\n  axes: {gaze_x: -1.0}\n  duration: 1.2\n  blend: 0.4\n  start: 0.5\n"
        "- kind: expression\n  target: charlie\n  axes: {gaze_x: 1.0}\n  duration: 1.2\n  blend: 0.4\n  start: 1.7\n"
        "```\n"
    )
    md = _meta("Gaze plus expression", 3.0) + "\n" + _shot("s1", 3.0) + "\n" + _entities("charlie") + actions
    return _render(_project(work, scene_md=md, characters=("charlie",)))


def _build_camera(work: Path) -> Path:
    parts = [_meta("The camera moves the compiler implements", 8.0)]
    for i, move in enumerate(("hold", "push_in", "pull_out", "zoom_in"), start=1):
        parts += [
            "\n",
            _shot(f"s{i}", 2.0, camera=move),
            "\n",
            _entities("maya"),
            f"\n```dialogue\nmaya: {move}\n```\n",
        ]
    return _render(_project(work, scene_md="".join(parts), characters=("maya",)))


def _build_alpha(work: Path) -> Path:
    md = (
        _meta("A tween on :alpha", 4.0)
        + "\n"
        + _shot("s1", 4.0)
        + "\n"
        + _entities("charlie")
        + "\n```yaml actions\n"
        "- kind: tween\n  target: charlie\n  property: alpha\n"
        "  from: 1.0\n  to: 0.0\n  duration: 1.5\n  start: 0.5\n"
        "- kind: tween\n  target: charlie\n  property: alpha\n"
        "  from: 0.0\n  to: 1.0\n  duration: 1.5\n  start: 2.5\n"
        "```\n"
    )
    return _render(_project(work, scene_md=md, characters=("charlie",)))


def _build_composition(work: Path) -> Path:
    md = (
        _meta("Composed motion, flattened to one timeline", 4.0)
        + "\n"
        + _shot("s1", 4.0)
        + "\n"
        + _entities("maya")
        + "\n```yaml actions\n"
        "- kind: tween\n  target: maya/arm_l\n  property: rotation\n"
        "  from: 0.0\n  to: -1.2\n  duration: 1.0\n  start: 0.2\n"
        "- kind: tween\n  target: maya/arm_r\n  property: rotation\n"
        "  from: 0.0\n  to: 1.2\n  duration: 1.0\n  start: 0.2\n"
        "- kind: tween\n  target: maya\n  property: y\n"
        "  from: 0.0\n  to: -28.0\n  duration: 0.6\n  start: 1.4\n"
        "- kind: tween\n  target: maya\n  property: y\n"
        "  from: -28.0\n  to: 0.0\n  duration: 0.6\n  start: 2.0\n"
        "- kind: tween\n  target: maya\n  property: rotation\n"
        "  from: 0.0\n  to: 0.25\n  duration: 1.2\n  start: 2.6\n"
        "```\n"
    )
    return _render(_project(work, scene_md=md, characters=("maya",)))


def _build_swap_channels(work: Path) -> Path:
    """Swap channels from scene.md (an#87): the committed `gale` art package
    carries a multi-key `hands` set and a `body_facing` set that no renderer
    or compiler code knows by name — they animate purely as descriptor data."""
    chars_dir = work / "assets" / "characters"
    chars_dir.mkdir(parents=True, exist_ok=True)
    shutil.copytree(
        REPO_ROOT / "tests" / "fixtures" / "characters" / "gale",
        chars_dir / "gale",
        dirs_exist_ok=True,
    )
    md = (
        _meta("Swap channels: named keys, not keyframes", 4.0)
        + "\n"
        + _shot("s1", 4.0)
        + "\n"
        + _entities("gale")
        + "\n```yaml actions\n"
        "- kind: set\n  target: gale/left_hand\n  property: hands\n"
        "  value: fist\n  at: 0.0\n"
        "- kind: set\n  target: gale/left_hand\n  property: hands\n"
        "  value: palm\n  at: 1.0\n"
        "- kind: set\n  target: gale/left_hand\n  property: hands\n"
        "  value: point\n  at: 2.0\n"
        "- kind: set\n  target: gale/torso\n  property: body_facing\n"
        "  value: left\n  at: 1.5\n"
        "- kind: set\n  target: gale/torso\n  property: body_facing\n"
        "  value: right\n  at: 2.5\n"
        "- kind: set\n  target: gale/torso\n  property: body_facing\n"
        "  value: front\n  at: 3.4\n"
        "```\n"
    )
    (work / "scene.md").write_text(md, encoding="utf-8")
    return _render(work)


def _build_play(work: Path) -> Path:
    """`play` of a descriptor animation (an#7): the seeded `idle_breath` loops
    to the shot end from ONE line, and two `blink`s ride the eyelid swap set."""
    chars_dir = work / "assets" / "characters"
    chars_dir.mkdir(parents=True, exist_ok=True)
    shutil.copytree(
        REPO_ROOT / "tests" / "fixtures" / "characters" / "gale",
        chars_dir / "gale",
        dirs_exist_ok=True,
    )
    md = (
        _meta("Play: a descriptor animation from one line", 6.0)
        + "\n"
        + _shot("s1", 6.0)
        + "\n"
        + _entities("gale")
        + "\n```yaml actions\n"
        "- kind: play\n  target: gale\n  animation: idle_breath\n"
        "- kind: play\n  target: gale\n  animation: blink\n  start: 1.5\n"
        "- kind: play\n  target: gale\n  animation: blink\n  start: 4.2\n"
        "```\n"
    )
    (work / "scene.md").write_text(md, encoding="utf-8")
    return _render(work)


#: The demo's step rate. At DEMO_FPS=24 "on twos" is 12 Hz — exactly GIF_FPS, so
#: a 12 fps GIF of it is indistinguishable from smooth. 6 Hz ("on fours") holds
#: each pose for two GIF frames, which is the least stepping the gallery format
#: can show. Stated here so the demo text never claims "twos" for what is shown.
STEPPED_DEMO_HZ: float = 6.0


def _build_stepped_timing(work: Path) -> Path:
    """Smooth (left) against stepped (right): the same scene rendered twice,
    once with `step_hz` and once without, side by side (an#89). The camera
    push-in is smooth in BOTH halves — it is exempt by construction."""
    md = (
        _meta("Stepped timing: on fours, camera on ones", 3.0)
        + "\n"
        + _shot("s1", 3.0, camera="push_in")
        + "\n"
        + _entities("charlie")
        + "\n```yaml actions\n"
        "- kind: tween\n  target: charlie\n  property: x\n  from: -160\n"
        "  to: 160\n  duration: 3.0\n  easing: ease_in_out\n"
        "- kind: tween\n  target: charlie/arm_l\n  property: rotation\n"
        "  to: 1.2\n  duration: 1.5\n  easing: ease_in_out\n"
        "- kind: tween\n  target: charlie/arm_l\n  property: rotation\n"
        "  from: 1.2\n  to: 0.0\n  duration: 1.5\n  start: 1.5\n"
        "```\n"
    )
    smooth = _render(_project(work / "smooth", scene_md=md, characters=("charlie",)))
    stepped = _render(
        _project(work / "stepped", scene_md=md, characters=("charlie",)),
        step_hz=STEPPED_DEMO_HZ,
    )
    out = work / "side_by_side.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-i",
            str(smooth),
            "-i",
            str(stepped),
            "-filter_complex",
            "[0:v][1:v]hstack=inputs=2",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(out),
        ],
        check=True,
    )
    return out


def _build_lipsync_coarticulation(work: Path) -> Path:
    """The same line twice, side by side: the pre-an#97 condenser (left) against
    the co-articulation passes (right). The mouth ART is identical; only which
    shape shows, and when, differs."""
    from an.adapters.cutout import compile as compile_mod

    line = "Hold the shape, then vote. The vowel wins the window."
    # The synthesized rig's chin sits behind the torso; an absolute `set` on
    # the head's y (rest minus a lift) puts the whole mouth in the pane. The
    # rest is read off a compile so the value is the rig's, not a guess.
    from an.adapters.cutout.compile import compile_shot
    from an.ir.schema import AssetRef, Shot
    from an.project import load

    probe = _project(
        work / "probe",
        scene_md=_meta("probe", 1.0)
        + "\n"
        + _shot("p", 1.0)
        + "\n"
        + _entities("maya"),
        characters=("maya",),
    )
    shot = Shot(
        id="p",
        style="cutout",
        duration=1.0,
        entities=[
            AssetRef(kind="character", id="maya", store="characters", ref="maya")
        ],
    )
    js = compile_shot(
        shot,
        mall=load(probe).mall,
        fps=DEMO_FPS,
        width=DEMO_RESOLUTION[0],
        height=DEMO_RESOLUTION[1],
    )
    ent = next(c for c in js.scene.children if c.name == "maya")
    head_rest_y = next(c for c in ent.children if c.name == "head").transform.y
    lifted = head_rest_y - 16
    md = (
        _meta("Lip-sync co-articulation: hold and vote", 4.0)
        + "\n"
        + _shot("s1", 4.0)
        + "\n"
        + _entities("maya")
        + f"\n```yaml actions\n- kind: set\n  target: maya/head\n  property: y\n  value: {lifted:g}\n  at: 0.0\n```\n"
        + f"\n```dialogue\nmaya: {line}\n```\n"
    )
    original = compile_mod.COARTICULATION_ENABLED
    compile_mod.COARTICULATION_ENABLED = False
    try:
        before = _render(_project(work / "before", scene_md=md, characters=("maya",)))
    finally:
        compile_mod.COARTICULATION_ENABLED = original
    after = _render(_project(work / "after", scene_md=md, characters=("maya",)))
    out = work / "side_by_side.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-i",
            str(before),
            "-i",
            str(after),
            "-filter_complex",
            f"[0:v]crop={PANE_CROP}[a];[1:v]crop={PANE_CROP}[b];[a][b]hstack=inputs=2",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-an",
            str(out),
        ],
        check=True,
    )
    return out


def _copy_example(rel: str) -> Callable[[Path], Path]:
    def build(work: Path) -> Path:
        src = REPO_ROOT / rel
        if not src.exists():
            raise FileNotFoundError(
                f"{rel} is a build product and is not in the checkout. Run its "
                "example builder first — see the demo's `how` line."
            )
        dst = work / src.name
        work.mkdir(parents=True, exist_ok=True)
        shutil.copy(src, dst)
        return dst

    return build


DEMOS: tuple[Demo, ...] = (
    Demo(
        slug="text-to-video",
        title="From text to video",
        shows=(
            "A whole shot — character, dialogue, timing — is twenty lines of markdown. "
            "`scene.md` is the file a human edits; `ir/scene.json` is the SSOT an agent "
            "edits; the two are reconciled, never both hand-written."
        ),
        how="`an render <project>` — the scene above is the entire input.",
        build=_build_text_to_video,
    ),
    Demo(
        slug="lipsync",
        title="Lip-sync from the audio, not by hand",
        shows=(
            "Nobody keyframed a mouth. The audio pipeline synthesizes the line, a "
            "lip-sync provider turns it into visemes, and the compiler emits a "
            "`viseme` channel the runtime applies by swapping the mouth texture."
        ),
        how=(
            "`an render <project> --tts offline --lipsync offline`. Swap either "
            "provider by name (`elevenlabs`, `mac_say`; `whisper`, `rhubarb`) and the "
            "content-hash cache re-synthesizes only what actually changed."
        ),
        build=_build_lipsync,
    ),
    Demo(
        slug="expressions",
        title="Four expressions, one silent character",
        shows=(
            "A character holding an expression with nothing to say. Panes, reading "
            "order: **neutral · happy** on top, **angry · surprised** below. What "
            "moves is the face solver's output: brow height and angle (the two sides "
            "rotate in opposite screen directions for one axis sign), the eyelid key "
            "off one threshold ladder, and the mouth's resting form — `happy` selects "
            "the character's `viseme@happy` set, so its closed mouth is a different "
            "drawing. The blend ramps in over 0.25 s. They play at once because a brow "
            "move is a few pixels; sequentially you would have to hold the previous "
            "face in your head."
        ),
        how=(
            "`- kind: expression / target: charlie / preset: angry` in the shot's "
            "`yaml actions` (or `an.ir.expression('charlie', 'angry')`). Presets live in "
            "`an/expression/presets.py`; the solver is `_add_face_clips` in "
            "`an/adapters/cutout/compile.py`, one channel per (node, property). Four "
            "separate renders, cropped to the face and tiled — no labels are burned in."
        ),
        build=_build_expressions,
    ),
    Demo(
        slug="expressions-more",
        title="Four more: sad, afraid, thinking, skeptical",
        shows=(
            "The other half of the vocabulary that a cutout face can carry. Panes, "
            "reading order: **sad · afraid** on top, **thinking · skeptical** below. "
            "`thinking` and `skeptical` are asymmetric — one brow up, the other "
            "not — and prefer no mouth form, so they keep the neutral mouth; `sad` "
            "selects `viseme@sad`. The two presets not shown anywhere, `disgusted` and "
            "`amused`, differ from their neighbours mainly by a mouth form the silent "
            "rest barely shows — a limit of the medium, said here rather than hidden. "
            "`afraid` prefers a `viseme@afraid` set no default character draws, so its "
            "mouth here is the neutral one (a speaking line would say so in a warning)."
        ),
        how=(
            "Same as above with the other preset names; `axes: {brow_height_l: 0.5}` "
            "layers a per-axis override on any preset, `intensity: 0.5` scales the "
            "whole thing. `an validate` refuses an unknown preset or axis by name."
        ),
        build=_build_expressions_more,
    ),
    Demo(
        slug="emotion-visemes",
        title="The same line under two mouth forms",
        shows=(
            "One line spoken twice, side by side, cropped to the face: the neutral "
            "mouth set on the left, the `viseme@happy` variant on the right, selected "
            "for the whole line by the `happy` expression the character holds. Every "
            "viseme keyframe is identical in both panes — same times, same keys — "
            "and only which SET the key indexes differs at the mouth; the brows and "
            "lids carry the preset too, as in every expression. A character without the variant "
            "falls back to the neutral set with a warning naming what was missing; "
            "`an character new` draws `happy` and `sad` variants by default and "
            "`an character mouths --variants angry` adds more."
        ),
        how=(
            "`- kind: expression / target: maya / preset: happy` over a shot with a "
            "dialogue line → `resolve_mouth_set` in `an/expression/binding.py` picks "
            "`viseme@happy`, and `_add_viseme_clips` emits the line's channel on that "
            "property. The brows and lids move too; watch the mouth's corners."
        ),
        build=_build_emotion_visemes,
    ),
    Demo(
        slug="gaze",
        title="Gaze: the pupils move, on a rig that has them",
        shows=(
            "The same authored sweep twice, side by side, cropped to the face — "
            "left, then right, then down — on a rig WITHOUT the eye stack (left "
            "pane: the eye is one drawing with the pupil baked in, so nothing "
            "moves; gaze is a validated no-op there) and on a rig WITH it (right "
            "pane: sclera, pupil and lid are three slots; the pupils follow the "
            "sweep, clamped inside the white by the descriptor's travel; the "
            "ambient saccades every pupil rig makes, seeded by the character's "
            "name, ride underneath — at this size they are a pixel or two, so "
            "watch the full-rate mp4 rather than the GIF for them). Blinks still "
            "close over the pupil "
            "because the closed lid is a filled drawing."
        ),
        how=(
            "`- kind: expression / target: maya / axes: {gaze_x: -1.0} / duration: 1.0` "
            "(gaze is two expression axes, `gaze_x`/`gaze_y`, no preset carries them); "
            "`an character new` draws the eye stack by default, `an character add-gaze "
            "<name>` adds it to an older rig. Saccades: `an/adapters/cutout/gaze.py`, "
            "seed stamped in the compiled scene's `meta.gaze_seeds`."
        ),
        crop="",
        build=_build_gaze,
    ),
    Demo(
        slug="gaze-plus-expression",
        title="Angry, and looking around",
        shows=(
            "`angry` held for the whole shot while an authored gaze sweeps left then "
            "right — two contributors on one face, summed at compile time by the "
            "face solver (the brows stay furrowed while the pupils travel), with the "
            "ambient saccades riding on top. Order-independent by construction: "
            "the same pose comes out with the actions listed the other way round."
        ),
        how=(
            "Two `expression` actions on one entity — one with `preset: angry`, one "
            "with `axes: {gaze_x: …}` — overlapping in time. `_add_face_clips` emits "
            "one channel per (node, property): brows from the preset, pupils from the "
            "gaze axes plus the saccade generator."
        ),
        crop=FACE_CROP,
        build=_build_gaze_plus_expression,
    ),
    Demo(
        slug="camera",
        title="The camera moves that actually exist",
        shows=(
            "`hold`, `push_in`, `pull_out`, `zoom_in` — and that is the whole list. "
            "The camera is a scale tween on the scene root, so it cannot translate: "
            "there is no pan. An unrecognised move **raises** rather than rendering "
            "nothing, which is how `pan_left` once came to be documented and dead."
        ),
        how=(
            "`camera: {move: push_in}` in the shot block → `_add_camera_clips`. "
            "The four shots run in the order listed above; the scale change is what "
            "you are watching for."
        ),
        build=_build_camera,
    ),
    Demo(
        slug="alpha",
        title="A tween on :alpha",
        shows=(
            "Fade out, fade in. `alpha` is a node property, not a fill argument — "
            "which is what makes entrances and exits available to every visual kind "
            "at once. A tween on a property the runtime does not implement throws "
            "instead of rendering nothing."
        ),
        how="a `kind: tween` on `property: alpha` in the `yaml actions` block.",
        build=_build_alpha,
    ),
    Demo(
        slug="composition",
        title="Composed motion, flattened to one timeline",
        shows=(
            "Arms up, a hop, a lean — authored as independent overlapping tweens. "
            "Authoring is fluent (`sequence` / `parallel` / `tween`); the canonical "
            "form is a flat list of actions with absolute times, and verifiers and "
            "renderers only ever see the flat form."
        ),
        how="`yaml actions` entries with `start:` → `an.ir.compose` → the flattened timeline.",
        build=_build_composition,
    ),
    Demo(
        slug="swap-channels",
        title="Swap channels: named keys, not keyframes",
        shows=(
            "A hand changes fist → palm → point and the torso turns left, right, "
            "and back — six `set` lines naming KEYS of the character's declared "
            "`hands` and `body_facing` asset sets. Neither set name appears in "
            "the renderer or the compiler: the descriptor declares the sets, the "
            "skin carries the art, and the one generic swap path applies them — "
            "the same path lip-sync's `viseme` set rides (an#87)."
        ),
        how=(
            "`{kind: set, target: gale/left_hand, property: hands, value: fist}` "
            "in `yaml actions`; the set names come from the character's "
            "`asset_sets`, validated at compile with the declared keys named in "
            "every error."
        ),
        build=_build_swap_channels,
    ),
    Demo(
        slug="play-animation",
        title="Play: a descriptor animation from one line",
        shows=(
            "The character breathes for the whole shot — torso bob, head tilt, "
            "weight shift — and blinks twice on cue, from three `play` lines. "
            "`idle_breath` and `blink` are the animations every descriptor "
            "carries; `play` resolves them into channels around the rig's rest "
            "pose (bone tracks) and through the eyelid swap set (slot tracks). "
            "The looping breath has no `duration`, so it runs to the shot end (an#7)."
        ),
        how=(
            "`{kind: play, target: gale, animation: idle_breath}` in `yaml actions`; "
            "`an validate` and the compiler share one resolver, so a play that "
            "cannot resolve is refused before any render with the reason named."
        ),
        build=_build_play,
    ),
    Demo(
        slug="lipsync-coarticulation",
        title="Lip-sync co-articulation: hold and vote",
        shows=(
            "The same line twice, side by side, cropped to the face: the OLD "
            "condenser on the left — every mouth shape arriving inside a 0.14 s "
            "window was dropped, so a consonant cluster collapsed to whichever "
            "shape came first and the closures and open vowels a viewer reads "
            "were the ones lost — against the co-articulation passes on the "
            "right: duplicates merged, sub-frame tongue shapes dropped, every "
            "shape 2/24 s ahead of its sound (two frames at this demo's 24 fps), a "
            "beat to close before rest, and a hold that VOTES (the shape with the "
            "largest in-window span × dominance wins, shown from the window start). The mouth art is identical in both panes; only which "
            "shape shows, and when, differs (an#97)."
        ),
        how=(
            "`an render` — the passes are the product; the left pane flips "
            "`an.adapters.cutout.compile.COARTICULATION_ENABLED` for one render, "
            "the way the bench's levers rebind a module global. The rules live in "
            "`an/adapters/cutout/coarticulate.py` (doctested)."
        ),
        build=_build_lipsync_coarticulation,
    ),
    Demo(
        slug="stepped-timing",
        title="Stepped timing: on fours, camera on ones",
        shows=(
            "The same shot twice, side by side: smooth on the left, `step_hz: 6` "
            "on the right — the character's slide and arm swing update six times "
            "a second and HOLD between updates, while the camera push-in stays "
            "smooth in both halves because the camera is exempt by construction. "
            "(6 Hz, 'on fours' at 24 fps, is the least stepping a 12 fps GIF can "
            "show; 'on twos' would be 12 Hz here and invisible in this format.)"
        ),
        how=(
            "`step_hz: 6` in `yaml meta` (or per shot in `yaml shot`), or "
            "`an render <dir> --step-hz 6`. Tweens are resampled onto a shot-wide "
            "pose grid of step-eased keyframes; blinks, `play` clips, swap "
            "channels and the camera are never stepped (an#89)."
        ),
        build=_build_stepped_timing,
    ),
    Demo(
        slug="svg-promote",
        title="A hand-drawn SVG becomes a lip-syncable character",
        shows=(
            "One SVG drawn in the Pose Animator convention, promoted into a sliced, "
            "rigged character with a synthesized nine-shape mouth set — then rendered "
            "through the same runtime any other character uses."
        ),
        how="`python examples/promote_demo/build.py` — `an.characters.promote`.",
        build=_copy_example("examples/promote_demo/output/main.mp4"),
    ),
    Demo(
        slug="two-characters",
        title="Two characters, dialogue, parallel render",
        shows=(
            "Two generated characters holding a conversation, each shot rendered in "
            "its own Chromium context and concatenated in timeline order."
        ),
        how="`python examples/character_gallery/build.py` — `an render --parallel auto`.",
        build=_copy_example("examples/character_gallery/videos/cartoon.mp4"),
    ),
)


# -----------------------------------------------------------------------------
# Driver
# -----------------------------------------------------------------------------


def build_one(demo: Demo, *, out_dir: Path) -> dict:
    """Render one demo and convert it, returning what the gallery needs."""
    work = out_dir / "_work" / demo.slug
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)
    mp4_src = demo.build(work)
    mp4 = out_dir / f"{demo.slug}.mp4"
    shutil.copy(mp4_src, mp4)
    gif = to_gif(mp4, out_dir / f"{demo.slug}.gif", crop=demo.crop)
    if gif.stat().st_size > GIF_WARN_BYTES:
        print(
            f"    WARNING: {gif.name} is {gif.stat().st_size // 1024} KB "
            f"(over {GIF_WARN_BYTES // 1024} KB) — shorten the clip or drop "
            "GIF_FPS rather than shipping it."
        )
    shutil.rmtree(work, ignore_errors=True)
    return {
        "slug": demo.slug,
        "title": demo.title,
        "shows": demo.shows,
        "how": demo.how,
        "mp4": mp4,
        "gif": gif,
        "mp4_bytes": mp4.stat().st_size,
        "gif_bytes": gif.stat().st_size,
    }


GALLERY_HEADER = """# The gallery — what `an` can actually do, on video

Every clip below was produced by `python misc/demos/build_demos.py`, offline and free:
characters are synthesized locally (`use_dicebear=False`), speech is the offline TTS
provider, and nothing here calls a paid API. Re-running it reproduces every file.

Each entry says **what you are looking at** and **the exact thing that makes it happen** —
a command, an argument, or the function that reads the field. Where a capability is
narrower than it looks, that is said here rather than left for you to discover.

> These are **not** the bench corpus. The corpus exists to make a deliberate degradation
> move a number declared in advance (`an bench`); these exist to be looked at. Sharing
> scenes between the two would make one a hostage of the other.
"""


def write_gallery(entries: list[dict], *, out_dir: Path, asset_base: str = "") -> Path:
    """The markdown that explains the clips, ready to paste into a discussion."""
    lines = [GALLERY_HEADER]
    for e in entries:
        url = f"{asset_base}{e['gif'].name}" if asset_base else e["gif"].name
        lines.append(
            f"\n---\n\n## {e['title']}\n\n"
            f"![{e['title']}]({url})\n\n"
            f"{e['shows']}\n\n"
            f"**How:** {e['how']}\n\n"
            f"<sub>`{e['gif'].name}` {e['gif_bytes'] // 1024} KB · "
            f"`{e['mp4'].name}` {e['mp4_bytes'] // 1024} KB</sub>\n"
        )
    path = out_dir / "GALLERY.md"
    path.write_text("".join(lines), encoding="utf-8")
    return path


def main(argv: list[str]) -> int:
    wanted = set(argv) or {d.slug for d in DEMOS}
    unknown = wanted - {d.slug for d in DEMOS}
    if unknown:
        print(f"unknown demo(s) {sorted(unknown)}; have {[d.slug for d in DEMOS]}")
        return 2
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    entries: list[dict] = []
    for demo in DEMOS:
        if demo.slug not in wanted:
            continue
        print(f"  {demo.slug} …", flush=True)
        try:
            entries.append(build_one(demo, out_dir=OUT_DIR))
        except Exception as e:  # noqa: BLE001 — reported, never swallowed
            print(f"    FAILED: {type(e).__name__}: {e}")
    if entries:
        print(f"\ngallery: {write_gallery(entries, out_dir=OUT_DIR)}")
    return 0 if len(entries) == len(wanted) else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
