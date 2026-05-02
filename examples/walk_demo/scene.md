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

```dialogue
alpha: Off I go, on a quick stroll.
```
