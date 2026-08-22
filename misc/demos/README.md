# The demo gallery

One short clip per shipped capability, each with the exact command, argument or
function that makes it happen.

```bash
python misc/demos/build_demos.py              # everything
python misc/demos/build_demos.py camera alpha # named demos only
```

Output lands in `misc/demos/out/` (gitignored): an `.mp4` and a `.gif` per demo,
plus `GALLERY.md` — the markdown that explains them, ready to paste into a
discussion.

## Three rules this directory keeps

**Offline and free, by construction.** Characters are synthesized locally
(`use_dicebear=False`, so no network and no third-party licence to carry) and
speech is the offline TTS provider. Nothing here can reach a paid API — which
matters because an unattended agent session is exactly "not CI, has keys".

**Self-contained.** Every demo builds its own throwaway project from a scene
authored in the script. Two entries are the exception and say so: they copy an
`examples/` build product, and refuse with the command that produces it when it
is absent.

**Not the bench corpus.** `misc/bench/corpus/` exists to make a deliberate
degradation move a number declared in advance; these exist to be looked at.
Sharing scenes between the two would make one a hostage of the other — a demo
that gets prettier is a corpus that stopped measuring what it measured.

## The media is not committed

`misc/` ships in the sdist, so a megabyte of GIFs would ride along with every
`pip install`. They are published as **GitHub release assets** and referenced by
URL from the discussion instead. Re-running the script reproduces them.

## Adding a demo

Append a `Demo` to `DEMOS` with:

- `shows` — what you are looking at, **including where the capability is
  narrower than it looks**. Half these entries name a limit; that is the point.
  A demo that oversells is worse than no demo, because the reader finds out
  later and trusts the rest less.
- `how` — the command, the argument, or the function that reads the field.
  Someone should be able to go straight from the clip to the code.
- `build(work) -> Path` — render into `work`, return the mp4.

Optional `crop` trims the GIF when the subject is smaller than the frame; say so
in `how`, because a crop is a claim about where to look.

**No labels are burned into any clip.** `drawtext` needs a freetype-enabled
ffmpeg build and this script must run on whichever one a contributor has — so
ordering and pane layout are described in `shows` instead. `an` cannot label a
clip from the inside either: there is no text visual kind until Wave 8 of #9.
