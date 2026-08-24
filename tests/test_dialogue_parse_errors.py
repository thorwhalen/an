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


def test_the_error_names_the_shot_and_an_validate_reports_it(tmp_path):
    """`an validate` prints findings; it must not stack-dump on the one error
    it exists to report (an#96 review)."""
    from an import init
    from an.orchestrate import validate_project

    root = init(tmp_path / "p")
    (root / "scene.md").write_text(_md("charlie: fine", "maya (warm): oops"), encoding="utf-8")
    (root / "ir" / "scene.json").unlink()  # the md is the SSOT here; a fresh init's JSON would win the mtime tie
    report = validate_project(root)
    assert not report.passed
    (finding,) = [f for f in report.findings if f.severity == "error"]
    assert "shot 's1'" in finding.description and "maya (warm)" in finding.description


def test_every_speaking_corpus_scene_ir_is_reproducible_from_its_md():
    """The committed IR carries the offline visemes; it must be exactly what
    `scene.md` + the offline providers produce, or a md edit without a
    regeneration silently mutes the fixture (an#96 review)."""
    import json

    from an.audio.pipeline import produce_audio_for_scene
    from an.ir.sync import markdown_to_ir

    checked = 0
    for ir_path in sorted(ROOT.glob("misc/bench/corpus/*/ir/scene.json")):
        scene = markdown_to_ir((ir_path.parent.parent / "scene.md").read_text(encoding="utf-8"))
        if not any(s.dialogue for s in scene.timeline):
            continue
        produce_audio_for_scene(scene)
        regenerated = json.loads(scene.model_dump_json())
        committed = json.loads(ir_path.read_text(encoding="utf-8"))
        assert regenerated == committed, ir_path
        checked += 1
    assert checked >= 1


def test_comments_and_blank_lines_are_still_skipped():
    scene = markdown_to_ir(_md("# a comment", "", "charlie: hi", "   ", "maya [happy]: yo"))
    assert [d.text for d in scene.timeline[0].dialogue] == ["hi", "yo"]


def test_every_committed_scene_md_dialogue_line_parses():
    """The repo's own scenes are the first place the old silence hid."""
    fence = re.compile(r"```dialogue\n(.*?)```", re.S)
    offenders = []
    for md in sorted(ROOT.glob("examples/**/scene.md")) + sorted(ROOT.glob("misc/bench/corpus/*/scene.md")):
        for block in fence.finditer(md.read_text(encoding="utf-8")):
            for raw in block.group(1).splitlines():
                line = raw.strip()
                if line and not line.startswith("#") and not _DIALOGUE_LINE_RE.match(line):
                    offenders.append(f"{md.relative_to(ROOT)}: {line[:60]}")
    assert not offenders, offenders


def test_promote_demo_has_its_line_again():
    """Its IR carries the line. Whether visemes are stamped depends on whether
    someone ran the example (`auto_audio=True` persists them) — the bench does
    not care, because `_prepare_promote_demo` regenerates the staged IR from
    the md, so the fixture's picture and contract hash are a property of the
    tree, not of developer state."""
    import json

    ir = json.loads((ROOT / "examples/promote_demo/ir/scene.json").read_text(encoding="utf-8"))
    (line,) = ir["timeline"][0]["dialogue"]
    assert line["speaker"] == "maya" and line["text"].startswith("I started life")
