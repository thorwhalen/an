# Prop Swap

```yaml meta
title: Prop Swap
author: ''
duration: 0.5
fps: 24
resolution:
  width: 320
  height: 240
default_renderer: cutout
```

## Shot lamp (cutout)

```yaml shot
duration: 0.5
```

```yaml entities
- kind: prop
  id: lamp
  store: props
  ref: lamp
  stage:
    at:
    - 0.0
    - 90.0
    scale: 0.55
```

```yaml actions
- kind: set
  target: lamp/body
  property: lamp
  value: 'on'
  at: 0.25
```
