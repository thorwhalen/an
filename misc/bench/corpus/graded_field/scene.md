# Graded Field

```yaml meta
title: Graded Field
author: ''
duration: 0.5
fps: 24
resolution:
  width: 320
  height: 240
default_renderer: cutout
```

## Shot field (cutout)

```yaml shot
duration: 0.5
```

```yaml entities
- kind: character
  id: field
  store: characters
  ref: graded-field-rig
```

```yaml actions
# Re-authored for the rig contract (an#73). These scales exist to make the
# gradient cover a large, fixed share of the frame while leaving the flat block
# visible — the fixture measures banding against flatness, so a torso that
# swallows the block measures nothing. The compiler used to force a 110x130 box
# and 4.0/2.5 produced 440x325; the box is now the art's own 512x512 at
# k=345/1024, i.e. 172.5x172.5, so these reproduce the same 440x325 rendering.
- kind: set
  target: field/torso
  property: scale_x
  value: 2.551
  at: 0.0
- kind: set
  target: field/torso
  property: scale_y
  value: 1.884
  at: 0.0
# The moving element has to sit OVER the gradient, or the two golden frames are
# pixel-identical and the second tests nothing — which is what the bless guard
# caught here. `arm_r` is a white marker and the flat block is white, so with
# the torso now anchored at its hip (rig-driven, an#73) the gradient occupies
# the lower half and the arm had to come down with it.
- kind: set
  target: field/arm_r
  property: y
  value: 60.0
  at: 0.0
- kind: tween
  target: field/arm_r
  property: x
  from: -110
  to: 110
  duration: 0.5
```
