# park_bench_cartoon — the v0.1 demo target

A two-shot 2D cutout cartoon, two characters on a park bench, dialogue with
lip-sync, one slow camera push-in.

## Try it

```bash
cd examples/park_bench_cartoon

# Bootstrap the two characters into assets/characters/. Both offline
# (geometric) so each character gets an overlay mouth that animates with
# the dialogue. DiceBear avatars work too, but their faces are baked in
# and the lip-sync overlay is suppressed for them — see "Mouth animation
# and DiceBear" below.
PYENV_VERSION=p12 an character new charlie-v1 \
    --out-dir assets/characters --offline --overwrite
PYENV_VERSION=p12 an character new maya-v1 \
    --out-dir assets/characters --seed maya-warm --offline --overwrite

an sync .       # regenerate ir/scene.json from scene.md
an validate .   # schema + semantic validation
an render .     # → output/main.mp4
```

`an render --parallel auto .` renders the two shots concurrently when
both shot styles support thread-safe execution.

## Mouth animation and DiceBear

DiceBear avatars (`an character new <name> --style adventurer`, the
default) ship with a baked-in face: eyes, brows, and mouth are part of
the head SVG. To avoid awkward double-mouths, the cutout adapter
suppresses the overlay face parts (and the lip-sync channel) for any
character whose descriptor declares `face_overlay: false` — which
`an character new` sets for DiceBear avatars, and which the 0.3.0 migration
derives from the old `metadata.art_provenance` convention for stored
descriptors. Audio still plays — the mouth just doesn't move.

For dialogue-heavy production scenes, hand-rig characters following the
Pose Animator convention and run them through `an.characters.promote`
(see `examples/promote_demo/`). Hand-drawn / offline characters render
mouth animation as expected.
