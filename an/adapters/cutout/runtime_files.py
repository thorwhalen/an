"""Locate the bundled cutout JS runtime files.

The runtime ships under ``an/data/cutout_runtime/`` and is consumed by the
headless renderer in Phase 2C. This module exposes paths so callers don't
hard-code the layout.

>>> p = runtime_dir()
>>> p.is_dir()
True
>>> (p / "index.html").is_file()
True
"""

from __future__ import annotations

import importlib.resources
from pathlib import Path


def runtime_dir() -> Path:
    """Return the directory containing index.html + runtime.js.

    Uses importlib.resources so it works from a wheel install too.
    """
    # Use the files() API (Python 3.9+) which works for both source trees
    # and wheels.
    res = importlib.resources.files("an.data.cutout_runtime")
    # MultiplexedPath etc. — coerce to a real Path for FS operations.
    # In practice for an editable install this is a normal directory.
    return Path(str(res))


def runtime_index_html() -> Path:
    """Path to ``index.html``."""
    return runtime_dir() / "index.html"


def runtime_js() -> Path:
    """Path to ``runtime.js``."""
    return runtime_dir() / "runtime.js"
