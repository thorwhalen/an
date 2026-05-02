"""Build a small character gallery showcasing the Phase 11a authoring tools.

Run::

    python examples/character_gallery/build.py

What it does:

1. Generates three characters into ``assets/characters/``:
   - ``maya``    — DiceBear ``adventurer`` style (network)
   - ``charlie`` — DiceBear ``lorelei`` style (network)
   - ``robo``    — offline geometric fallback (no network)
   The DiceBear ones gracefully fall back to the offline path if the API
   is unreachable, so the script always finishes.

2. Validates each character (parts present, mouth set present, pivots
   detected).

3. Runs the silhouette test pairwise. With the default v0.1 wrapping,
   bodies share the same rectangular geometry → IoU ≈ 1.0 across pairs.
   That's the test working as intended: it tells you you need to vary
   body geometry / accessories before two characters are visually
   distinct on the silhouette pass.

4. Writes a ``preview.html`` for each character and emits a top-level
   ``index.html`` linking them.

5. Prints a summary table.

Idempotent: re-running with ``overwrite=True`` blows away and rebuilds.
"""

from __future__ import annotations

import sys
from pathlib import Path

from an.characters import (
    new_character,
    validate_character,
)
from an.characters.cli import _write_preview_html
from an.characters.record import record_preview_to_mp4
from an.characters.silhouette import (
    compare_silhouettes,
    render_silhouette,
)


HERE = Path(__file__).parent.resolve()
CHARS_DIR = HERE / "assets" / "characters"
VIDEOS_DIR = HERE / "videos"

CHARACTERS: list[dict[str, object]] = [
    {"name": "maya", "seed": "maya-warm", "style": "adventurer", "offline": False},
    {"name": "charlie", "seed": "charlie-bingo", "style": "lorelei", "offline": False},
    {"name": "robo", "seed": "robo-001", "style": "adventurer", "offline": True},
]


def _build_one(spec: dict[str, object]) -> Path:
    name = str(spec["name"])
    print(f"  → {name} (style={spec['style']}, offline={spec['offline']})")
    return new_character(
        CHARS_DIR,
        name=name,
        seed=str(spec["seed"]),
        style=str(spec["style"]),
        use_dicebear=not bool(spec["offline"]),
        overwrite=True,
    )


def _validate_one(name: str) -> tuple[bool, str]:
    report = validate_character(CHARS_DIR / name)
    return report.passed, report.format()


def _silhouette_pair(a: str, b: str) -> float:
    a_dir = CHARS_DIR / a
    b_dir = CHARS_DIR / b
    a_png = render_silhouette(a_dir / f"{a}.svg", a_dir / "silhouette.png")
    b_png = render_silhouette(b_dir / f"{b}.svg", b_dir / "silhouette.png")
    return compare_silhouettes(a_png, b_png)


def _write_index(names: list[str], *, videos: list[Path] | None = None) -> Path:
    videos_set = {v.stem for v in (videos or [])}
    cards: list[str] = []
    for n in names:
        video_block = ""
        if n in videos_set:
            video_block = (
                f'<video src="videos/{n}.mp4" '
                'controls autoplay loop muted playsinline '
                'style="width:100%;background:#0f1115;border-radius:8px"></video>'
            )
        cards.append(
            f'<div class="card">'
            f"<h2>{n}</h2>"
            f"{video_block}"
            f'<p><a href="assets/characters/{n}/preview.html">live preview</a></p>'
            f"</div>"
        )
    grid = "\n".join(cards)
    html = f"""<!doctype html><html><head><meta charset="utf-8">
<title>an — character gallery</title>
<style>
  body {{ font-family: -apple-system, system-ui, sans-serif;
          background:#1a1d21; color:#d8dae0; padding:32px; }}
  a {{ color:#7eb6ff; }}
  h1 {{ font-weight: 500; }}
  .grid {{ display:grid; grid-template-columns: repeat(auto-fit, minmax(320px, 1fr));
           gap: 24px; margin-top: 32px; }}
  .card {{ background:#23262b; border:1px solid #2f333a; border-radius:8px; padding:16px; }}
  .card h2 {{ margin: 0 0 12px; font-weight: 500; }}
</style></head><body>
<h1>character gallery</h1>
<p>Built by <code>examples/character_gallery/build.py</code>.</p>
<p>Each video shows the new SVG character art animated by the preview HTML
(cycling all 9 visemes + sine-wave breath/head-tilt). Until Phase 11b wires
the SVG-texture path into the cutout runtime, this is the most honest
demonstration of what the new character art looks like in motion.</p>
<div class="grid">{grid}</div>
</body></html>"""
    out = HERE / "index.html"
    out.write_text(html, encoding="utf-8")
    return out


def main() -> int:
    print("Building characters into:", CHARS_DIR)
    CHARS_DIR.mkdir(parents=True, exist_ok=True)

    # 1. Build
    for spec in CHARACTERS:
        _build_one(spec)

    names = [str(s["name"]) for s in CHARACTERS]

    # 2. Validate
    print("\nValidating:")
    all_ok = True
    for n in names:
        ok, msg = _validate_one(n)
        all_ok = all_ok and ok
        print(textwrap_indent(msg, "  "))

    # 3. Silhouette comparisons
    print("\nSilhouette test (pairwise IoU; lower = more visually distinct):")
    try:
        for i, a in enumerate(names):
            for b in names[i + 1:]:
                score = _silhouette_pair(a, b)
                verdict = (
                    "very similar"
                    if score >= 0.75
                    else "moderately similar"
                    if score >= 0.5
                    else "distinct"
                )
                print(f"  {a:>10} vs {b:<10}  IoU = {score:.3f}  ({verdict})")
    except Exception as e:
        print(f"  silhouette comparison skipped: {e}")
        print("  (needs Playwright with Chromium installed: `playwright install chromium`)")

    # 4. Per-character previews + recordings
    print("\nPreview pages:")
    for n in names:
        path = _write_preview_html(CHARS_DIR / n, name=n)
        print(f"  {path}")

    # 5. Record each preview to mp4 (real video, real new-SVG character art).
    # Videos live in `videos/` (sibling of assets/) so they're outside the
    # standard examples/*/assets/ gitignore rule and CAN be checked in.
    VIDEOS_DIR.mkdir(parents=True, exist_ok=True)
    print(f"\nRecording previews to mp4 (~6 s each) in {VIDEOS_DIR}:")
    videos: list[Path] = []
    for n in names:
        try:
            mp4 = record_preview_to_mp4(
                CHARS_DIR / n / "preview.html",
                VIDEOS_DIR / f"{n}.mp4",
                duration_s=6.0,
                size=(480, 360),
            )
            videos.append(mp4)
            print(f"  {mp4} ({mp4.stat().st_size // 1024} KB)")
        except Exception as e:
            print(f"  skipped {n}: {e}")

    index = _write_index(names, videos=videos)
    print(f"\nGallery index: {index}")
    print("Open it in a browser to inspect each character.")

    return 0 if all_ok else 1


def textwrap_indent(text: str, prefix: str) -> str:
    return "\n".join(prefix + line for line in text.splitlines())


if __name__ == "__main__":
    sys.exit(main())
