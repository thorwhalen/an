"""Characters store — JSON descriptor + sidecar folder per character.

A character's ``meta.json`` carries name, art-style hint, default voice ref,
slot map (which body parts exist), and optional defaults. Binary parts (SVGs,
PNGs) are sidecars under the same directory.
"""

from __future__ import annotations

from an.stores._common import JsonSidecarStore


class CharactersStore(JsonSidecarStore):
    """Per-character directory store.

    >>> import tempfile
    >>> with tempfile.TemporaryDirectory() as d:
    ...     store = CharactersStore(d)
    ...     store['maya'] = {'name': 'Maya', 'voice_ref': 'maya-warm'}
    ...     store['maya']['name']
    'Maya'
    """
