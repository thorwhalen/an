# Demo media

**This is an orphan branch and it is not part of `main`.** It holds nothing but the
output of `misc/demos/build_demos.py`, so the discussion can reference stable
`raw.githubusercontent.com` URLs that serve real image content types — release assets
are served as `application/octet-stream`, which GitHub's image proxy will not render
inline.

Nothing here ships. `misc/` is in the sdist and `main` is not; keeping the media on a
branch of its own is what lets the gallery have pictures without every `pip install`
paying for them.

Regenerate from `main`:

```bash
python misc/demos/build_demos.py
```

Offline, free and deterministic — see `misc/demos/README.md` on `main`.
