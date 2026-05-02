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
from an.characters.silhouette import (
    compare_silhouettes,
    render_silhouette,
)


HERE = Path(__file__).parent.resolve()
CHARS_DIR = HERE / "assets" / "characters"

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


def _write_index(names: list[str]) -> Path:
    rows = "".join(
        f'<li><a href="assets/characters/{n}/preview.html">{n}</a></li>'
        for n in names
    )
    html = f"""<!doctype html><html><head><meta charset="utf-8">
<title>an — character gallery</title>
<style>
  body {{ font-family: -apple-system, system-ui, sans-serif;
          background:#1a1d21; color:#d8dae0; padding:32px; }}
  a {{ color:#7eb6ff; }}
</style></head><body>
<h1>character gallery</h1>
<p>Built by <code>examples/character_gallery/build.py</code>.</p>
<ul>{rows}</ul>
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

    # 4. Per-character previews + top-level index
    print("\nPreview pages:")
    for n in names:
        path = _write_preview_html(CHARS_DIR / n, name=n)
        print(f"  {path}")
    index = _write_index(names)
    print(f"\nGallery index: {index}")
    print("Open it in a browser to inspect each character.")

    return 0 if all_ok else 1


def textwrap_indent(text: str, prefix: str) -> str:
    return "\n".join(prefix + line for line in text.splitlines())


if __name__ == "__main__":
    sys.exit(main())
