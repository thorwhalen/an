"""Inline emotion in dialogue lines: `speaker [emotion]: text`."""

from __future__ import annotations

from an.ir.sync import ir_to_markdown, markdown_to_ir


_MD = """# X

```yaml meta
title: X
duration: 1
```

## Shot s1 (cutout)

```yaml shot
duration: 1
```

```dialogue
charlie [happy]: Hi there.
maya: No emotion.
charlie [skeptical]: Really?
```
"""


def test_emotion_parsed():
    scene = markdown_to_ir(_MD)
    lines = scene.timeline[0].dialogue
    assert lines[0].speaker == "charlie"
    assert lines[0].emotion == "happy"
    assert lines[0].text == "Hi there."
    assert lines[1].emotion is None
    assert lines[1].text == "No emotion."
    assert lines[2].emotion == "skeptical"


def test_emotion_round_trips():
    scene1 = markdown_to_ir(_MD)
    md2 = ir_to_markdown(scene1)
    scene2 = markdown_to_ir(md2)
    e1 = [(d.speaker, d.emotion) for d in scene1.timeline[0].dialogue]
    e2 = [(d.speaker, d.emotion) for d in scene2.timeline[0].dialogue]
    assert e1 == e2


def test_old_style_dialogue_still_parses():
    """Lines without [emotion] still work — backwards compatible."""
    md = """# X

```yaml meta
title: X
duration: 1
```

## Shot s1 (cutout)

```yaml shot
duration: 1
```

```dialogue
charlie: still works
```
"""
    scene = markdown_to_ir(md)
    line = scene.timeline[0].dialogue[0]
    assert line.speaker == "charlie"
    assert line.emotion is None
    assert line.text == "still works"
