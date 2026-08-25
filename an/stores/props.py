"""Props store — descriptor + sidecar folder per prop.

A prop is a rig whose art is not a person: a lamp, a sword, a sign. It is
stored exactly like a character — a JSON descriptor beside a ``parts/``
folder of SVGs — because the cutout compiler builds both from the same rig
builder, and the store is the ONLY thing that differs about where their art
lives (an#108). What differs is the *descriptor*: a prop declares no face,
no blink, and no humanoid skeleton.

Kept a separate store rather than a ``kind`` field on the characters store,
for the reason the compiler keeps them apart: ``an character validate``
scores a correct prop as 21 blocking findings, and the character
placeholder fallback draws a *person* where a lamp should be.
"""

from __future__ import annotations

from an.stores._common import JsonSidecarStore


class PropsStore(JsonSidecarStore):
    """Per-prop directory store.

    >>> import tempfile
    >>> with tempfile.TemporaryDirectory() as d:
    ...     store = PropsStore(d)
    ...     store['lamp'] = {'name': 'Desk lamp'}
    ...     store['lamp']['name']
    'Desk lamp'
    """

    META_NAME = "prop.json"
