"""Voices store — pure JSON; one entry per voice.

A voice descriptor carries: provider (e.g. ``elevenlabs``), provider voice id,
display name, optional cloning source ref, and emotion presets.
"""

from __future__ import annotations

from an.stores._common import JsonDirStore


class VoicesStore(JsonDirStore):
    """JSON-only voice descriptors.

    >>> import tempfile
    >>> with tempfile.TemporaryDirectory() as d:
    ...     store = VoicesStore(d)
    ...     store['maya-warm'] = {'provider': 'elevenlabs', 'voice_id': 'xyz'}
    ...     'maya-warm' in store
    True
    """
