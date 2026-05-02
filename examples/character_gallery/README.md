# character_gallery

Demonstrates the Phase 11a character authoring tools (`an.characters`).

## Run

```bash
python examples/character_gallery/build.py
```

Outputs three characters into `assets/characters/`, validates them, runs
the silhouette test pairwise, writes per-character preview pages, and
emits an `index.html` linking everything.

## What's shown

| Tool | Where it appears |
|---|---|
| `new_character` | builds three characters (DiceBear adventurer, DiceBear lorelei, offline geometric fallback) |
| `validate_character` | confirms each character's required parts + 9-shape mouth set |
| `render_silhouette` + `compare_silhouettes` | pairwise IoU table |
| `preview.html` writer | a self-contained viewer cycling all 9 visemes with breath/head-tilt animation |
| `record_preview_to_mp4` | records each preview HTML to `videos/<name>.mp4` (committed to the repo) |

The silhouette test will report IoU ≈ 1.0 for the v0.1 wrapping —
that's working as intended. The wrapper uses identical rectangular body
geometry for every character (only the head SVG varies); to discriminate
on silhouette you'd need to vary body proportions or add accessories.
The test telling you so is the point.

## Equivalent CLI invocation

```bash
an character new maya --seed maya-warm
an character new charlie --seed charlie-bingo --style lorelei
an character new robo --seed robo-001 --offline
an character validate maya
an character silhouette maya --other charlie
an character preview maya --open-browser
```

## Notes

- DiceBear calls need network. If unreachable, `new_character` falls back
  to a deterministic geometric face — the script still completes.
- `render_silhouette` uses Playwright/Chromium (already a project dep).
  Run `playwright install chromium` if you haven't.
- Outputs are gitignored; re-running rebuilds from scratch.
