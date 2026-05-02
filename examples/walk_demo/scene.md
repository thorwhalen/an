# Walk Demo

```yaml meta
title: Walk Demo
author: ''
duration: 4.0
fps: 24
resolution:
  width: 640
  height: 360
default_style: cutout
```

## Shot walk (cutout)

```yaml shot
duration: 4.0
camera:
  position:
  - 0.0
  - 0.0
  - 0.0
  target:
  - 0.0
  - 0.0
  - 0.0
  focal_length: 50.0
  move: push_in
```

```yaml entities
- kind: character
  id: alpha
  store: characters
  ref: alpha-v1
```

```yaml actions
- kind: tween
  target: alpha
  property: x
  to: 250
  duration: 3.5
  from: -250
- kind: tween
  target: alpha/torso
  property: rotation
  to: 0.05
  duration: 0.5
  from: -0.05
- kind: tween
  target: alpha/torso
  property: rotation
  to: -0.05
  duration: 0.5
  start: 0.5
- kind: tween
  target: alpha/torso
  property: rotation
  to: 0.05
  duration: 0.5
  start: 1.0
- kind: tween
  target: alpha/torso
  property: rotation
  to: -0.05
  duration: 0.5
  start: 1.5
- kind: tween
  target: alpha/torso
  property: rotation
  to: 0.05
  duration: 0.5
  start: 2.0
- kind: tween
  target: alpha/torso
  property: rotation
  to: -0.05
  duration: 0.5
  start: 2.5
- kind: tween
  target: alpha/torso
  property: rotation
  to: 0.0
  duration: 0.5
  start: 3.0
```

```dialogue
alpha: Off I go, on a quick stroll.
```
