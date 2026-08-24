"""A dialogue line that does not parse is refused, never dropped (an#96).

`_extract_dialogue_block` used to `continue` past any line that failed the
`speaker [emotion]: text` regex. `examples/promote_demo` was mute for months
because its one line read `maya (warm): …` — the committed IR had
`dialogue: []` and nothing said so. Silence is the failure this test removes.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from an.ir.sync import _DIALOGUE_LINE_RE, markdown_to_ir

ROOT = Path(__file__).resolve().parents[1]

_TEMPLATE = """# X

```yaml meta
title: X
duration: 1
```

## Shot s1 (cutout)

```yaml shot
duration: 1
```

```dialogue
{lines}
```
"""


def _md(*lines):
    return _TEMPLATE.format(lines="\n".join(lines))


@pytest.mark.parametrize(
    "bad",
    [
        "maya (warm): I started life as one SVG.",  # the promote_demo typo
        "just some prose with no speaker",
        "maya [happy] forgot the colon",
        "[angry]: no speaker",
    ],
)
def test_an_unparseable_line_is_an_error_naming_the_line(bad):
    with pytest.raises(ValueError) as e:
        markdown_to_ir(_md("charlie: fine", bad))
    assert bad[:20] in str(e.value)
    assert "speaker" in str(e.value)


def test_comments_and_blank_lines_are_still_skipped():
    scene = markdown_to_ir(_md("# a comment", "", "charlie: hi", "   ", "maya [happy]: yo"))
    assert [d.text for d in scene.timeline[0].dialogue] == ["hi", "yo"]


def test_every_committed_scene_md_dialogue_line_parses():
    """The repo's own scenes are the first place the old silence hid."""
    fence = re.compile(r"```dialogue\n(.*?)```", re.S)
    offenders = []
    for md in sorted(ROOT.glob("examples/*/scene.md")) + sorted(ROOT.glob("misc/bench/corpus/*/scene.md")):
        for block in fence.finditer(md.read_text(encoding="utf-8")):
            for raw in block.group(1).splitlines():
                line = raw.strip()
                if line and not line.startswith("#") and not _DIALOGUE_LINE_RE.match(line):
                    offenders.append(f"{md.relative_to(ROOT)}: {line[:60]}")
    assert not offenders, offenders


def test_promote_demo_has_its_line_again():
    """Its committed IR carries the line (no visemes: the bench renders with
    `auto_audio=False` and nothing is stamped, so its golden and contract hash
    stay exactly where they were — the mouth moves when someone runs audio)."""
    import json

    ir = json.loads((ROOT / "examples/promote_demo/ir/scene.json").read_text(encoding="utf-8"))
    (line,) = ir["timeline"][0]["dialogue"]
    assert line["speaker"] == "maya" and line["text"].startswith("I started life")
    assert line["viseme_track"] is None
