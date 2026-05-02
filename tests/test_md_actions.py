"""markdown_to_ir parses ``yaml actions`` blocks; round-trip preserves them."""

from __future__ import annotations

from an.ir.compose import flatten
from an.ir.schema import SequenceAction, SetAction, TweenAction
from an.ir.sync import ir_to_markdown, markdown_to_ir


_MD_WITH_ACTIONS = """# Actions Demo

```yaml meta
title: Actions Demo
duration: 5
```

## Shot s1 (cutout)

```yaml shot
duration: 5
```

```yaml actions
- {kind: tween, target: alpha, property: x, to: 100, duration: 2.0}
- {kind: tween, target: alpha/torso, property: rotation, to: 0.3, duration: 1.0, start: 1.0, easing: ease_out}
- {kind: set, target: alpha/head, property: y, value: -10, at: 3.0}
```
"""


def test_actions_block_parsed():
    scene = markdown_to_ir(_MD_WITH_ACTIONS)
    actions = scene.timeline[0].actions
    assert len(actions) == 3


def test_tween_with_start_wraps_in_sequence():
    """Action #2 has start=1.0 so it should be sequence(delay(1.0), tween(...))."""
    scene = markdown_to_ir(_MD_WITH_ACTIONS)
    actions = scene.timeline[0].actions
    a1 = actions[1]
    # Wrapped form
    assert isinstance(a1, SequenceAction)
    assert len(a1.children) == 2
    inner = a1.children[1]
    assert isinstance(inner, TweenAction)
    assert inner.target == "alpha/torso"
    assert inner.easing == "ease_out"


def test_actions_flatten_produces_correct_absolute_times():
    scene = markdown_to_ir(_MD_WITH_ACTIONS)
    actions = scene.timeline[0].actions
    flat0 = flatten(actions[0])
    flat1 = flatten(actions[1])
    flat2 = flatten(actions[2])
    assert flat0[0].start == 0.0
    assert flat0[0].end == 2.0
    # action #1 has start=1.0 → flat starts at 1.0
    assert flat1[0].start == 1.0
    assert flat1[0].end == 2.0
    # set has at=3.0
    assert flat2[0].start == 3.0


def test_set_action_uses_at():
    scene = markdown_to_ir(_MD_WITH_ACTIONS)
    a2 = scene.timeline[0].actions[2]
    assert isinstance(a2, SetAction)
    assert a2.at == 3.0
    assert a2.value == -10


def test_md_to_ir_to_md_round_trip():
    scene1 = markdown_to_ir(_MD_WITH_ACTIONS)
    md2 = ir_to_markdown(scene1)
    scene2 = markdown_to_ir(md2)
    a1, a2 = scene1.timeline[0].actions, scene2.timeline[0].actions
    assert len(a1) == len(a2)
    # Compare the flattened forms (canonical), since composition wrappers
    # may shift between the two passes.
    flat1 = [(round(f.start, 3), round(f.end, 3), type(f.action).__name__)
             for action in a1 for f in flatten(action)]
    flat2 = [(round(f.start, 3), round(f.end, 3), type(f.action).__name__)
             for action in a2 for f in flatten(action)]
    assert flat1 == flat2


def test_no_actions_block_yields_empty_list():
    scene = markdown_to_ir(
        "# X\n\n```yaml meta\ntitle: X\nduration: 1\n```\n\n"
        "## Shot s1 (cutout)\n\n```yaml shot\nduration: 1\n```\n"
    )
    assert scene.timeline[0].actions == []


def test_unknown_action_kind_raises():
    import pytest
    bad_md = """# X

```yaml meta
title: X
duration: 1
```

## Shot s1 (cutout)

```yaml shot
duration: 1
```

```yaml actions
- {kind: bogus, target: a, property: b, value: 1}
```
"""
    with pytest.raises(ValueError, match="kind must be"):
        markdown_to_ir(bad_md)
