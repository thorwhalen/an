"""Markdown ↔ IR sync round-trip + disk-level reconciliation."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from an.ir.schema import SceneIR
from an.ir.sync import ir_to_markdown, markdown_to_ir, sync


def test_md_to_ir_extracts_meta_and_dialogue():
    md = """# Demo

```yaml meta
title: Demo
duration: 5
fps: 24
```

## Shot s1 (cutout)

```yaml shot
duration: 5
```

```dialogue
charlie: hello there
maya: hi
```
"""
    scene = markdown_to_ir(md)
    assert scene.meta.title == "Demo"
    assert scene.meta.duration == 5
    assert scene.meta.fps == 24
    assert len(scene.timeline) == 1
    assert scene.timeline[0].id == "s1"
    assert scene.timeline[0].style == "cutout"
    assert [d.speaker for d in scene.timeline[0].dialogue] == ["charlie", "maya"]
    assert scene.timeline[0].dialogue[0].text == "hello there"


def test_ir_to_md_round_trip_preserves_structure():
    src_md = """# RT

```yaml meta
title: RT
duration: 3
fps: 30
```

## Shot a (cutout)

```yaml shot
duration: 3
```

```dialogue
charlie: hi
```
"""
    scene_a = markdown_to_ir(src_md)
    regenerated_md = ir_to_markdown(scene_a)
    scene_b = markdown_to_ir(regenerated_md)

    assert scene_a.meta.title == scene_b.meta.title
    assert scene_a.meta.duration == scene_b.meta.duration
    assert len(scene_a.timeline) == len(scene_b.timeline)
    assert scene_a.timeline[0].id == scene_b.timeline[0].id
    assert scene_a.timeline[0].dialogue[0].text == scene_b.timeline[0].dialogue[0].text


def test_sync_writes_json_when_only_md_exists():
    with tempfile.TemporaryDirectory() as d:
        pdir = Path(d)
        (pdir / "scene.md").write_text(
            "# X\n\n```yaml meta\ntitle: X\nduration: 1\n```\n\n"
            "## Shot s1 (cutout)\n\n```yaml shot\nduration: 1\n```\n",
            encoding="utf-8",
        )
        result = sync(pdir)
        assert result.wrote_json
        assert (pdir / "ir" / "scene.json").exists()
        data = json.loads((pdir / "ir" / "scene.json").read_text())
        scene = SceneIR.model_validate(data)
        assert scene.meta.title == "X"


def test_sync_writes_md_when_only_json_exists():
    with tempfile.TemporaryDirectory() as d:
        pdir = Path(d)
        scene = SceneIR()
        scene.meta.title = "JsonFirst"
        (pdir / "ir").mkdir(parents=True)
        (pdir / "ir" / "scene.json").write_text(scene.model_dump_json(), encoding="utf-8")
        result = sync(pdir)
        assert result.wrote_md
        assert (pdir / "scene.md").exists()
        assert "JsonFirst" in (pdir / "scene.md").read_text()
