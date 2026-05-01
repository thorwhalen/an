# Park Bench Cartoon

```yaml meta
title: Park Bench Cartoon
author: Thor Whalen
duration: 12.0
fps: 24
resolution:
  width: 640
  height: 360
default_style: cutout
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
charlie: Did you ever wonder why we always meet here?
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
maya: Because the pigeons trust us.
```
