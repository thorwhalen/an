# Park Bench Cartoon

```yaml meta
title: Park Bench Cartoon
author: Thor Whalen
duration: 12.0
fps: 24
resolution:
  width: 640
  height: 360
default_renderer: cutout
```

## Shot s1 (cutout)

```yaml shot
duration: 6.0
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
  move: hold
```

```yaml entities
- kind: environment
  id: park_bg
  store: environments
  ref: park
- kind: character
  id: charlie
  store: characters
  ref: charlie-v1
- kind: character
  id: maya
  store: characters
  ref: maya-v1
```

```dialogue
charlie [thinking]: Did you ever wonder why we always meet here?
```

## Shot s2 (cutout)

```yaml shot
duration: 6.0
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
- kind: environment
  id: park_bg
  store: environments
  ref: park
- kind: character
  id: charlie
  store: characters
  ref: charlie-v1
- kind: character
  id: maya
  store: characters
  ref: maya-v1
```

```dialogue
maya [happy]: Because the pigeons trust us, and honestly, I love our little spot.
```
