"""markdown_to_ir parses ```yaml entities``` blocks; round-trip preserves them."""

from __future__ import annotations

from an.ir.schema import AssetRef, SceneIR, Shot
from an.ir.sync import ir_to_markdown, markdown_to_ir


_MD_WITH_ENTITIES = """# Demo

```yaml meta
title: Demo
duration: 1
```

## Shot s1 (cutout)

```yaml shot
duration: 1
```

```yaml entities
- kind: character
  id: charlie
  store: characters
  ref: charlie-v1
- kind: character
  id: maya
  store: characters
  ref: maya-v1
```
"""


def test_entities_block_is_parsed():
    scene = markdown_to_ir(_MD_WITH_ENTITIES)
    shot = scene.timeline[0]
    assert len(shot.entities) == 2
    assert shot.entities[0].id == "charlie"
    assert shot.entities[0].ref == "charlie-v1"
    assert shot.entities[1].id == "maya"


def test_entities_round_trip_through_md():
    src = markdown_to_ir(_MD_WITH_ENTITIES)
    regenerated = ir_to_markdown(src)
    reparsed = markdown_to_ir(regenerated)
    assert len(reparsed.timeline[0].entities) == 2
    assert reparsed.timeline[0].entities[0].id == "charlie"
    assert reparsed.timeline[0].entities[1].id == "maya"


def test_no_entities_block_yields_empty_list():
    scene = markdown_to_ir(
        "# X\n\n```yaml meta\ntitle: X\nduration: 1\n```\n\n"
        "## Shot s1 (cutout)\n\n```yaml shot\nduration: 1\n```\n"
    )
    assert scene.timeline[0].entities == []


def test_entity_round_trip_via_constructed_ir():
    scene = SceneIR(
        timeline=[
            Shot(
                id="s1",
                renderer="cutout",
                duration=1.0,
                entities=[
                    AssetRef(
                        kind="character",
                        id="x",
                        store="characters",
                        ref="x-v1",
                    )
                ],
            )
        ]
    )
    md = ir_to_markdown(scene)
    assert "yaml entities" in md
    reparsed = markdown_to_ir(md)
    assert reparsed.timeline[0].entities[0].id == "x"
