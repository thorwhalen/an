# Stage Pan

```yaml meta
title: Stage Pan
author: ''
duration: 0.5
fps: 24
resolution:
  width: 320
  height: 240
default_renderer: cutout
```

## Shot pan (cutout)

```yaml shot
duration: 0.5
camera:
  keys:
  - at: 0.0
    x: 0.0
    y: 0.0
    zoom: 1.0
    rotation: 0.0
    easing: linear
  - at: 0.5
    x: 60.0
    y: 0.0
    zoom: 1.0
    rotation: 0.0
```

```yaml entities
- kind: environment
  id: depths
  store: environments
  ref: depths
```
