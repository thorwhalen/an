"""User-facing character CLI subcommands.

Wired into the top-level ``an`` dispatcher via ``character_*`` dispatch-friendly
functions in :mod:`an.tools`. Each function here takes plain strings/bools
and returns a string for terminal display.

Subcommands (used as ``an character <verb> ...``):

- ``new``       — generate a fresh character from DiceBear or fallback art.
- ``mouths``    — regenerate the 9-shape default mouth set.
- ``validate``  — completeness check.
- ``silhouette``— rasterize silhouettes; for two characters, also IoU.
- ``preview``   — open an HTML viewer cycling visemes + idle animation.
"""

from __future__ import annotations

import json
import os
import tempfile
import textwrap
from pathlib import Path
from typing import Optional

from an.characters.factory import new_character as _new_character
from an.characters.validate import format_report as _format_report
from an.characters.validate import render_contract as _render_contract
from an.characters.validate import validate_character as _validate_character
from an.characters.mouth_set import write_default_mouths
from an.characters.dicebear import (
    DICEBEAR_DEFAULT_STYLE,
    DICEBEAR_STYLES,
)


def _resolve_target(out_dir: str | None) -> Path:
    if out_dir:
        return Path(out_dir)
    return Path.cwd() / "assets" / "characters"


def new(
    name: str,
    out_dir: str = "",
    seed: str = "",
    style: str = DICEBEAR_DEFAULT_STYLE,
    voice_ref: str = "",
    offline: bool = False,
    acknowledge_attribution: bool = False,
    overwrite: bool = False,
) -> str:
    """Create a new character at ``out_dir``/``name``.

    name: character id (used as the directory name and descriptor 'name')
    out_dir: parent directory; defaults to ./assets/characters
    seed: deterministic seed for DiceBear; defaults to ``name``
    style: DiceBear style. The default is CC0 — no attribution duty. Some styles
        are CC BY 4.0 and oblige whoever ships the video to credit the artist;
        those need --acknowledge-attribution. Run `an credits <project>` to see
        what a project owes.
    voice_ref: voice id stored in the descriptor's ``voice_ref`` field
    offline: skip DiceBear and use the deterministic geometric fallback
    acknowledge_attribution: accept the attribution duty of a CC BY style
    overwrite: replace an existing directory at the target
    """
    target = _resolve_target(out_dir)
    target.mkdir(parents=True, exist_ok=True)
    if style not in DICEBEAR_STYLES:
        return f"unknown DiceBear style: {style!r}. Known: {', '.join(DICEBEAR_STYLES)}"
    try:
        desc = _new_character(
            target,
            name=name,
            seed=seed or None,
            style=style,
            voice_ref=voice_ref or None,
            use_dicebear=not offline,
            acknowledge_attribution=acknowledge_attribution,
            overwrite=overwrite,
        )
    except ValueError as e:
        # A licence refusal is a message for a human, not a traceback. The
        # sibling branch above already returns its rejection as a string; a
        # gate added below the CLI without a flag above it made every CC BY
        # style unreachable AND ugly.
        return str(e)
    return f"created character at {desc.parent} (descriptor: {desc.name})"


def mouths(
    name: str,
    out_dir: str = "",
    palette: str = "",
) -> str:
    """Regenerate the default 9-shape mouth set for ``name``.

    Useful when you want to reset a character's mouth art to the offline
    fallback (e.g. after experimenting with hand-drawn mouths).

    name: character id
    out_dir: parent directory; defaults to ./assets/characters
    palette: optional JSON string to override colors, e.g. '{"lip":"#a44"}'
    """
    target = _resolve_target(out_dir) / name / "parts" / "mouth"
    palette_dict: dict[str, str] | None = None
    if palette:
        try:
            palette_dict = json.loads(palette)
        except json.JSONDecodeError as e:
            return f"invalid palette JSON: {e}"
    written = write_default_mouths(target, palette=palette_dict)
    return f"wrote {len(written)} mouth shapes to {target}"


def validate(name: str, out_dir: str = "") -> str:
    """Validate a character's directory structure and descriptor.

    name: character id
    out_dir: parent directory; defaults to ./assets/characters
    """
    target = _resolve_target(out_dir) / name
    report = _validate_character(target, name=name)
    return _format_report(report, name=name)


def silhouette(
    name: str,
    other: str = "",
    out_dir: str = "",
    output: str = "",
    size: int = 512,
) -> str:
    """Render a black silhouette for ``name`` (and optionally compare to ``other``).

    When two names are given, prints both silhouettes' paths and an IoU
    score (0..1; lower means more visually distinct).

    name: character id
    other: optional second character to compare against
    out_dir: parent directory; defaults to ./assets/characters
    output: output PNG path; defaults to <character_dir>/silhouette.png
    size: square output size in pixels (default 512)
    """
    from an.characters.silhouette import (
        render_silhouette,
        compare_silhouettes,
    )

    base = _resolve_target(out_dir)

    def _render(char_name: str) -> Path:
        cdir = base / char_name
        svg = cdir / f"{char_name}.svg"
        if not svg.exists():
            raise FileNotFoundError(svg)
        out_path = Path(output) if output and not other else cdir / "silhouette.png"
        return render_silhouette(svg, out_path, size=(size, size))

    a_path = _render(name)
    if not other:
        return f"silhouette: {a_path}"
    b_path = _render(other)
    score = compare_silhouettes(a_path, b_path, size=(size, size))
    verdict = (
        "very similar (consider redesigning)"
        if score >= 0.75
        else "moderately similar"
        if score >= 0.5
        else "distinct"
    )
    return (
        f"silhouettes:\n"
        f"  {name}: {a_path}\n"
        f"  {other}: {b_path}\n"
        f"  IoU: {score:.3f} — {verdict}"
    )


def preview(
    name: str,
    out_dir: str = "",
    open_browser: bool = False,
) -> str:
    """Render a small HTML viewer that previews all visemes + idle animation.

    The page includes the head SVG, cycles through ``mouth_a … mouth_x``
    once a second, and shows a ±2 px sine-wave breath on the head. Useful
    for eyeballing a character's mouth set before integrating into the
    main runtime.

    name: character id
    out_dir: parent directory; defaults to ./assets/characters
    open_browser: also open the file in the default browser
    """
    target = _resolve_target(out_dir) / name
    if not target.exists():
        return f"no such character: {target}"
    html_path = _write_preview_html(target, name=name)
    if open_browser:
        import webbrowser

        webbrowser.open(f"file://{html_path}")
    return f"preview: {html_path}"


_PREVIEW_TEMPLATE = textwrap.dedent(
    """\
    <!doctype html><html><head><meta charset="utf-8">
    <title>{name} — an character preview</title>
    <style>
      :root {{ color-scheme: dark; }}
      body {{
        font-family: -apple-system, system-ui, sans-serif;
        background: #1a1d21; color: #d8dae0;
        margin: 0; padding: 24px; display: grid; gap: 24px;
        grid-template-columns: 320px 1fr;
      }}
      h1, h2, h3 {{ font-weight: 500; margin: 0 0 8px; }}
      .panel {{
        background: #23262b; border-radius: 8px; padding: 16px;
        border: 1px solid #2f333a;
      }}
      .stage {{
        background: #0f1115; border-radius: 8px; min-height: 480px;
        display: grid; place-items: center; padding: 24px;
      }}
      .head-wrap {{ width: 320px; height: 320px; position: relative; }}
      .head-wrap img {{ width: 100%; height: 100%; }}
      .mouth-overlay {{
        position: absolute; left: 50%; top: 60%; transform: translate(-50%, -50%);
        width: 96px; height: 48px;
      }}
      .mouth-overlay img {{ width: 100%; height: 100%; }}
      .grid {{
        display: grid; grid-template-columns: repeat(3, 1fr); gap: 8px;
      }}
      .swatch {{
        background: #15171a; border: 1px solid #2f333a; border-radius: 6px;
        padding: 8px; text-align: center;
      }}
      .swatch img {{ width: 64px; height: 32px; }}
      code {{ background: #15171a; padding: 1px 6px; border-radius: 4px; }}
      .meta {{ font-size: 12px; color: #8a909a; }}
    </style>
    </head>
    <body>
    <div class="panel">
      <h1>{name}</h1>
      <p class="meta">{display_name}</p>
      <h3>Viseme set</h3>
      <div class="grid">
        {viseme_grid}
      </div>
      <h3 style="margin-top:16px">Idle animation</h3>
      <p class="meta">±2 px breath, 0.5° head tilt at 4 s period.</p>
      <h3 style="margin-top:16px">Pivots detected</h3>
      <ul class="meta">{pivots_list}</ul>
      <h3 style="margin-top:16px">Files</h3>
      <p class="meta">Descriptor: <code>character.json</code><br>
      Source SVG: <code>{name}.svg</code><br>
      Parts: <code>parts/</code></p>
    </div>
    <div class="stage">
      <div class="head-wrap" id="stage">
        <img src="parts/head.svg" id="head"/>
        <div class="mouth-overlay"><img id="mouth" src="parts/mouth/mouth_x.svg"/></div>
      </div>
    </div>
    <script>
      // Cycle visemes once per second.
      const shapes = {viseme_letters};
      let i = 0;
      const mouth = document.getElementById('mouth');
      setInterval(() => {{
        i = (i + 1) % shapes.length;
        mouth.src = `parts/mouth/mouth_${{shapes[i]}}.svg`;
      }}, 700);
      // ±2 px sine breath on head.
      const head = document.getElementById('stage');
      const t0 = performance.now();
      function tick() {{
        const t = (performance.now() - t0) / 1000;
        const dy = 2 * Math.sin(2 * Math.PI * t / 4);
        const rot = 0.5 * Math.sin(2 * Math.PI * (t / 4 + 0.25));
        head.style.transform = `translateY(${{dy.toFixed(2)}}px) rotate(${{rot.toFixed(2)}}deg)`;
        requestAnimationFrame(tick);
      }}
      requestAnimationFrame(tick);
    </script>
    </body></html>
    """
)


def _write_preview_html(target: Path, *, name: str) -> Path:
    desc = json.loads((target / "character.json").read_text(encoding="utf-8"))
    display_name = desc.get("display_name") or name
    pivots = desc.get("metadata", {}).get("pivots_detected", [])
    pivots_list = (
        "".join(f"<li>{p}</li>" for p in pivots) if pivots else "<li>(none)</li>"
    )
    viseme_grid = "".join(
        f'<div class="swatch"><img src="parts/mouth/mouth_{s}.svg"/>'
        f'<div class="meta">mouth_{s}</div></div>'
        for s in ("a", "b", "c", "d", "e", "f", "g", "h", "x")
    )
    viseme_letters = json.dumps(["a", "b", "c", "d", "e", "f", "g", "h", "x"])
    html = _PREVIEW_TEMPLATE.format(
        name=name,
        display_name=display_name,
        pivots_list=pivots_list,
        viseme_grid=viseme_grid,
        viseme_letters=viseme_letters,
    )
    out = target / "preview.html"
    out.write_text(html, encoding="utf-8")
    return out


def record(
    name: str,
    out_dir: str = "",
    output: str = "",
    duration: float = 8.0,
    width: int = 640,
    height: int = 480,
) -> str:
    """Record a character's preview HTML to mp4.

    Real video file showing the new SVG character art animating: cycles
    through all 9 visemes and applies the breath/head-tilt animation.

    name: character id
    out_dir: parent directory; defaults to ./assets/characters
    output: output mp4 path; defaults to <character_dir>/preview.mp4
    duration: recording length in seconds (default 8)
    width / height: video resolution (default 640x480)
    """
    from an.characters.record import record_character

    target = _resolve_target(out_dir) / name
    if not target.exists():
        return f"no such character: {target}"
    out = record_character(
        target,
        name=name,
        out_mp4=output or None,
        duration_s=duration,
        size=(width, height),
    )
    return f"recorded: {out}"


# Dispatch list. Mounted as a nested namespace ('character')
# by an/__main__.py so they appear as `an character new`, etc.


def contract() -> str:
    """Print the art-package contract an illustrator must satisfy.

    **Derived from the schema and the validator, never hand-written**, so it
    cannot drift from what `an character validate` actually enforces — a
    contract that disagrees with its checker is worse than none, because it
    gets a human paid for work that cannot land.
    """
    return _render_contract()


_dispatch_funcs = [
    new,
    mouths,
    validate,
    contract,
    silhouette,
    preview,
    record,
]
