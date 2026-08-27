"""Build the character gallery + a real cartoon using the new tools.

Run::

    python examples/character_gallery/build.py

What it does:

1. Generates two **offline** characters into ``cartoon/assets/characters/``.
   Offline because the procedural mouth animation works on those (DiceBear
   avatars suppress the mouth overlay — see SESSION_HANDOFF.md §3).
2. Validates each character.
3. Runs the silhouette test pairwise.
4. Writes a per-character ``preview.html`` for documentation.
5. **Renders the cartoon** at ``cartoon/scene.md`` to mp4 via
   ``an render --parallel auto``, copies the result to
   ``videos/cartoon.mp4``. This is the real demo of the new tools — two
   SVG-textured characters speaking with lip-sync, rendered through the
   same Pixi runtime any production scene uses.
6. Builds an ``index.html`` that embeds the cartoon at the top.

Idempotent: re-running rebuilds.

**This script spends no money by default, and a key does not change that.**
Real (billed) ElevenLabs speech needs the explicit opt-in *as well as* the
key — ``AN_LIVE_API_TESTS=1 ELEVEN_API_KEY=... python examples/character_gallery/build.py``
— and whisper lip-sync, which is free but downloads model weights, rides the
same switch. The chosen providers and the reason for each are printed before
anything is synthesized, so an unwanted run can be stopped with Ctrl-C. See
:mod:`an.live_api`.
"""

from __future__ import annotations

import os
import shutil
import sys
from collections.abc import Mapping
from pathlib import Path

from an.characters import (
    new_character,
    validate_character,
)
from an.characters.cli import _write_preview_html
from an.characters.silhouette import (
    compare_silhouettes,
    render_silhouette,
)
from an.live_api import LIVE_API_ENV_VAR, live_api_enabled
from an.render import render_project


HERE = Path(__file__).parent.resolve()
#: How to spell this script on a command line, for the hint it prints.
SCRIPT_REL = "examples/character_gallery/build.py"
CARTOON_PROJECT = HERE / "cartoon"
CARTOON_CHARS_DIR = CARTOON_PROJECT / "assets" / "characters"
VIDEOS_DIR = HERE / "videos"

# Offline characters (mouth animation works). Two distinct seeds → different
# palettes. Names match the entity refs in ``cartoon/scene.md``.
CHARACTERS: list[dict[str, object]] = [
    {"name": "maya", "seed": "maya-warm", "offline": True},
    {"name": "charlie", "seed": "charlie-bingo", "offline": True},
]


def _build_one(spec: dict[str, object], out_dir: Path) -> Path:
    name = str(spec["name"])
    print(f"  → {name} (offline={spec['offline']})")
    return new_character(
        out_dir,
        name=name,
        seed=str(spec["seed"]),
        use_dicebear=not bool(spec["offline"]),
        overwrite=True,
    )


def _silhouette_pair(a_dir: Path, b_dir: Path) -> float:
    a_png = render_silhouette(a_dir / f"{a_dir.name}.svg", a_dir / "silhouette.png")
    b_png = render_silhouette(b_dir / f"{b_dir.name}.svg", b_dir / "silhouette.png")
    return compare_silhouettes(a_png, b_png)


def _tts_choice(env: Mapping[str, str]) -> tuple[str, str]:
    """The TTS provider this environment has *asked* for, and why.

    ``elevenlabs`` — which bills per character of dialogue — needs BOTH the
    opt-in and the key. A key alone is not consent: ``ELEVEN_API_KEY`` is
    exported by every shell that sources a profile, so key-presence is
    satisfied by exactly the unattended runs that must not be billed. The
    repo's audio cache makes it easy to miss; the charge lands on a clean
    checkout, where every line is new.
    """
    key = env.get("ELEVEN_API_KEY") or env.get("ELEVENLABS_API_KEY")
    if not live_api_enabled(env):
        why = f"{LIVE_API_ENV_VAR} is not set" + (
            " — a key being present is not consent to spend"
            if key
            else " (and no ELEVEN_API_KEY either)"
        )
        return "offline", why
    if not key:
        return "offline", f"{LIVE_API_ENV_VAR} is set but ELEVEN_API_KEY is not"
    return "elevenlabs", f"{LIVE_API_ENV_VAR}=1 and ELEVEN_API_KEY are both set"


def _lipsync_choice(env: Mapping[str, str]) -> tuple[str, str]:
    """The lip-sync provider this environment has asked for, and why.

    ``whisper`` costs no money, so this is not a spend gate — but its first run
    DOWNLOADS model weights (hundreds of MB), which is the same class of thing
    an unattended run should not do because a package happened to be
    importable. It rides the one opt-in rather than a second env var, because a
    second switch is a second answer to "may this run do something expensive".
    """
    if not live_api_enabled(env):
        return "offline", f"{LIVE_API_ENV_VAR} is not set (whisper downloads weights)"
    try:
        import faster_whisper  # noqa: F401
    except ImportError:
        return "offline", f"{LIVE_API_ENV_VAR}=1 but faster-whisper is not installed"
    return "whisper", f"{LIVE_API_ENV_VAR}=1 and faster-whisper is importable"


def _announce_providers() -> tuple[str, str]:
    """Say what will be used and why, BEFORE anything is synthesized."""
    tts, tts_why = _tts_choice(os.environ)
    lipsync, lipsync_why = _lipsync_choice(os.environ)
    print("\nProviders:")
    print(f"  tts     = {tts:<10} ({tts_why})")
    print(f"  lipsync = {lipsync:<10} ({lipsync_why})")
    if tts == "elevenlabs":
        print(
            "  → this run WILL make real, billed ElevenLabs calls for every "
            "line of dialogue. Ctrl-C now to stop it."
        )
    else:
        print(
            f"  → nothing here spends money. For real speech: "
            f"{LIVE_API_ENV_VAR}=1 ELEVEN_API_KEY=... python {SCRIPT_REL}"
        )
    return tts, lipsync


def _render_cartoon() -> Path:
    """Render cartoon/scene.md to mp4 and copy to videos/cartoon.mp4."""
    tts, lipsync = _announce_providers()
    print(
        f"\nRendering cartoon at {CARTOON_PROJECT} "
        f"(parallel auto, tts={tts}, lipsync={lipsync}):"
    )
    output_path = render_project(
        CARTOON_PROJECT, tts=tts, lipsync=lipsync, parallel="auto"
    )
    VIDEOS_DIR.mkdir(parents=True, exist_ok=True)
    target = VIDEOS_DIR / "cartoon.mp4"
    shutil.copy(output_path, target)
    print(f"  {target} ({target.stat().st_size // 1024} KB)")
    return target


def _write_index(names: list[str], cartoon_mp4: Path | None) -> Path:
    cartoon_block = ""
    if cartoon_mp4 is not None and cartoon_mp4.exists():
        cartoon_block = f"""
<div class="hero">
  <h2>Cartoon: <em>Procedural</em></h2>
  <p class="meta">Two offline characters speaking, rendered through the
  Pixi SVG-texture runtime introduced in Phase 11b. The same pipeline any
  production scene uses.</p>
  <video src="videos/{cartoon_mp4.name}" controls autoplay loop muted
         playsinline style="width:100%;background:#0f1115;border-radius:8px"></video>
</div>
"""
    cards: list[str] = []
    for n in names:
        cards.append(
            f'<div class="card">'
            f"<h3>{n}</h3>"
            f'<p><a href="cartoon/assets/characters/{n}/preview.html">live preview</a></p>'
            f"</div>"
        )
    grid = "\n".join(cards)
    html = f"""<!doctype html><html><head><meta charset="utf-8">
<title>an — character gallery</title>
<style>
  body {{ font-family: -apple-system, system-ui, sans-serif;
          background:#1a1d21; color:#d8dae0; padding:32px; max-width:920px; margin:auto; }}
  a {{ color:#7eb6ff; }}
  h1, h2, h3 {{ font-weight: 500; }}
  .hero {{ background:#23262b; border:1px solid #2f333a; border-radius:8px;
           padding:24px; margin: 24px 0; }}
  .grid {{ display:grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
           gap: 16px; }}
  .card {{ background:#23262b; border:1px solid #2f333a; border-radius:8px; padding:12px; }}
  .meta {{ color:#8a909a; font-size: 14px; }}
</style></head><body>
<h1>character gallery</h1>
<p>Built by <code>examples/character_gallery/build.py</code>. Demonstrates
the Phase 11 character authoring + SVG-texture rendering pipeline.</p>
{cartoon_block}
<h2>Per-character previews</h2>
<p class="meta">Click through to inspect individual characters' parts and
the 9-shape mouth set used for lip-sync.</p>
<div class="grid">{grid}</div>
</body></html>"""
    out = HERE / "index.html"
    out.write_text(html, encoding="utf-8")
    return out


def main() -> int:
    print("Building characters into:", CARTOON_CHARS_DIR)
    CARTOON_CHARS_DIR.mkdir(parents=True, exist_ok=True)

    # 1. Build characters
    for spec in CHARACTERS:
        _build_one(spec, CARTOON_CHARS_DIR)
    names = [str(s["name"]) for s in CHARACTERS]

    # 2. Validate
    print("\nValidating:")
    all_ok = True
    for n in names:
        report = validate_character(CARTOON_CHARS_DIR / n)
        all_ok = all_ok and report.passed
        print(_indent(report.format(), "  "))

    # 3. Silhouette comparisons (informational)
    print("\nSilhouette test (pairwise IoU; lower = more visually distinct):")
    try:
        for i, a in enumerate(names):
            for b in names[i + 1 :]:
                score = _silhouette_pair(CARTOON_CHARS_DIR / a, CARTOON_CHARS_DIR / b)
                verdict = (
                    "very similar"
                    if score >= 0.75
                    else "moderately similar"
                    if score >= 0.5
                    else "distinct"
                )
                print(f"  {a:>10} vs {b:<10}  IoU = {score:.3f}  ({verdict})")
    except Exception as e:
        print(f"  silhouette skipped: {e}")

    # 4. Per-character preview pages
    print("\nPreview pages:")
    for n in names:
        path = _write_preview_html(CARTOON_CHARS_DIR / n, name=n)
        print(f"  {path}")

    # 5. Render the cartoon — this is the real demo
    cartoon_mp4: Path | None = None
    try:
        cartoon_mp4 = _render_cartoon()
    except Exception as e:
        print(f"\ncartoon render failed: {e}")
        print("(skipping cartoon embed in index.html)")

    # 6. Top-level gallery index
    index = _write_index(names, cartoon_mp4)
    print(f"\nGallery index: {index}")
    print("Open it in a browser to see the cartoon + per-character previews.")

    return 0 if all_ok else 1


def _indent(text: str, prefix: str) -> str:
    return "\n".join(prefix + line for line in text.splitlines())


if __name__ == "__main__":
    sys.exit(main())
