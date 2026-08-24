# Dialogue

```yaml meta
title: Dialogue
author: ''
duration: 1.0
fps: 24
resolution:
  width: 320
  height: 240
default_style: cutout
```

## Shot line (cutout)

```yaml shot
duration: 1.0
```

```yaml entities
- kind: character
  id: talker
  store: characters
  ref: talker-rig
```

```yaml actions
- kind: set
  target: talker/head
  property: y
  value: -89
  at: 0.0
```

```dialogue
talker: Hold the shape, then vote.
```
