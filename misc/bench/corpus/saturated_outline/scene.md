# Saturated Outline

```yaml meta
title: Saturated Outline
author: ''
duration: 0.5
fps: 24
resolution:
  width: 320
  height: 240
default_style: cutout
```

## Shot plates (cutout)

```yaml shot
duration: 0.5
```

```yaml entities
- kind: character
  id: plates
  store: characters
  ref: saturated-rig
```

```yaml actions
- kind: tween
  target: plates/head
  property: rotation
  from: -0.15
  to: 0.15
  duration: 0.5
```
