# Graded Field

```yaml meta
title: Graded Field
author: ''
duration: 0.5
fps: 24
resolution:
  width: 320
  height: 240
default_style: cutout
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
- kind: set
  target: field/torso
  property: scale_x
  value: 4.0
  at: 0.0
- kind: set
  target: field/torso
  property: scale_y
  value: 2.5
  at: 0.0
- kind: tween
  target: field/arm_r
  property: x
  from: -110
  to: 110
  duration: 0.5
```
