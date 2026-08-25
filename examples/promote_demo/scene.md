# promote_demo — hand-drawn → promoted character

A one-shot scene that uses ``maya-promoted``, a character built by running
``build.py`` over the hand-drawn SVG at
``assets/characters/raw_maya/raw_maya.svg``.

```yaml meta
title: promote_demo
author: ''
duration: 3.0
fps: 24
resolution:
  width: 480
  height: 360
default_renderer: cutout
```

## Shot s1 (cutout)

```yaml shot
duration: 3.0
camera:
  move: hold
```

```yaml entities
- kind: environment
  id: bg
  store: environments
  ref: park
- kind: character
  id: maya
  store: characters
  ref: maya-promoted
```

```dialogue
maya: I started life as one SVG, and now I have a rig.
```
