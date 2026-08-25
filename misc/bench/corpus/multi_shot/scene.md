# Multi Shot

```yaml meta
title: Multi Shot
author: ''
duration: 0.5
fps: 24
resolution:
  width: 320
  height: 240
default_renderer: cutout
```

## Shot intro (cutout)

```yaml shot
duration: 0.25
```

```yaml entities
- kind: environment
  id: back
  store: environments
  ref: night
- kind: character
  id: ada
  store: characters
  ref: ada-rig
```

```yaml actions
- kind: tween
  target: ada
  property: x
  from: -70
  to: 70
  duration: 0.25
```

## Shot beat (cutout)

```yaml shot
duration: 0.25
```

```yaml entities
- kind: environment
  id: back
  store: environments
  ref: sunset
- kind: character
  id: ada
  store: characters
  ref: ada-rig
```

```yaml actions
- kind: tween
  target: ada
  property: y
  from: 40
  to: -40
  duration: 0.25
```
