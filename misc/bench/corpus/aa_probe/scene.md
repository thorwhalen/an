# AA Probe

```yaml meta
title: AA Probe
author: ''
duration: 0.5
fps: 24
resolution:
  width: 320
  height: 240
default_style: cutout
```

## Shot probe (cutout)

```yaml shot
duration: 0.5
```

```yaml entities
- kind: character
  id: probe
  store: characters
  ref: probe-rig
```

```yaml actions
- kind: set
  target: probe/torso
  property: rotation
  value: 0.12217304763960307
  at: 0.0
- kind: set
  target: probe/left_arm
  property: rotation
  value: 0.4014257279586958
  at: 0.0
- kind: set
  target: probe/right_arm
  property: rotation
  value: 0.7853981633974483
  at: 0.0
- kind: tween
  target: probe/left_leg
  property: x
  from: -120
  to: 120
  duration: 0.5
```
