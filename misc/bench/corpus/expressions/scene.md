# Expressions

```yaml meta
title: Expressions
author: ''
duration: 2.0
fps: 24
resolution:
  width: 320
  height: 240
default_style: cutout
```

## Shot neutral (cutout)

```yaml shot
duration: 0.25
```

```yaml entities
- kind: character
  id: face
  store: characters
  ref: face
```

```yaml actions
- kind: set
  target: face
  property: y
  value: 45
  at: 0.0
- kind: expression
  target: face
  preset: neutral
  blend: 0.0
```

## Shot happy (cutout)

```yaml shot
duration: 0.25
```

```yaml entities
- kind: character
  id: face
  store: characters
  ref: face
```

```yaml actions
- kind: set
  target: face
  property: y
  value: 45
  at: 0.0
- kind: expression
  target: face
  preset: happy
  blend: 0.0
```

## Shot sad (cutout)

```yaml shot
duration: 0.25
```

```yaml entities
- kind: character
  id: face
  store: characters
  ref: face
```

```yaml actions
- kind: set
  target: face
  property: y
  value: 45
  at: 0.0
- kind: expression
  target: face
  preset: sad
  blend: 0.0
```

## Shot angry (cutout)

```yaml shot
duration: 0.25
```

```yaml entities
- kind: character
  id: face
  store: characters
  ref: face
```

```yaml actions
- kind: set
  target: face
  property: y
  value: 45
  at: 0.0
- kind: expression
  target: face
  preset: angry
  blend: 0.0
```

## Shot surprised (cutout)

```yaml shot
duration: 0.25
```

```yaml entities
- kind: character
  id: face
  store: characters
  ref: face
```

```yaml actions
- kind: set
  target: face
  property: y
  value: 45
  at: 0.0
- kind: expression
  target: face
  preset: surprised
  blend: 0.0
```

## Shot afraid (cutout)

```yaml shot
duration: 0.25
```

```yaml entities
- kind: character
  id: face
  store: characters
  ref: face
```

```yaml actions
- kind: set
  target: face
  property: y
  value: 45
  at: 0.0
- kind: expression
  target: face
  preset: afraid
  blend: 0.0
```

## Shot thinking (cutout)

```yaml shot
duration: 0.25
```

```yaml entities
- kind: character
  id: face
  store: characters
  ref: face
```

```yaml actions
- kind: set
  target: face
  property: y
  value: 45
  at: 0.0
- kind: expression
  target: face
  preset: thinking
  blend: 0.0
```

## Shot skeptical (cutout)

```yaml shot
duration: 0.25
```

```yaml entities
- kind: character
  id: face
  store: characters
  ref: face
```

```yaml actions
- kind: set
  target: face
  property: y
  value: 45
  at: 0.0
- kind: expression
  target: face
  preset: skeptical
  blend: 0.0
```
