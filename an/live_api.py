"""The one switch that says "yes, this run may spend money".

A key being present is not consent to spend. ``ELEVEN_API_KEY`` (or
``ANTHROPIC_API_KEY``) is exported by every developer shell that has ever
sourced a profile, and by every unattended agent session started from one — so
"has a key" describes the exact population that must *not* be billed silently.
The federation adopted a rule about this after a real incident (video_gen
decision D-vg-audio-02): a code path that can reach a paid API needs an
**explicit positive opt-in env var in addition to the key**.

:data:`LIVE_API_ENV_VAR` is that signal, and this module is its single
definition. ``tests/conftest.py`` reads it for the ``live_api`` marker, and
``examples/character_gallery/build.py`` reads it before choosing a paid TTS
provider — an example is not a test, but it is the file with the most footfall,
and a clean checkout has a cold audio cache, so every line it speaks is a new
charge.

There is deliberately **one** switch, not one per caller: a second variable
would be a second answer to "may this run spend?", and the two would drift.

>>> LIVE_API_ENV_VAR
'AN_LIVE_API_TESTS'
"""

from __future__ import annotations

import os

#: Set this truthy to opt a run in to real, billed API calls.
LIVE_API_ENV_VAR: str = "AN_LIVE_API_TESTS"

#: Accepted spellings of "yes". Anything else — including an empty string, the
#: shape an unset-but-exported variable takes — is "no".
TRUTHY_VALUES: frozenset[str] = frozenset({"1", "true", "yes", "on"})

#: Set by every CI provider we care about. CI must never spend, whatever else
#: is configured, because nobody is watching the bill in a CI run.
CI_ENV_VAR: str = "CI"


def live_api_enabled(env: dict | None = None) -> bool:
    """Whether this run has explicitly opted in to paid API calls.

    ``env`` defaults to the process environment; pass a mapping to ask the
    question of a hypothetical one without mutating ``os.environ``.

    >>> live_api_enabled({})
    False
    >>> live_api_enabled({"ELEVEN_API_KEY": "sk-real-key"})  # a key is not consent
    False
    >>> live_api_enabled({LIVE_API_ENV_VAR: "1"})
    True
    >>> live_api_enabled({LIVE_API_ENV_VAR: "1", CI_ENV_VAR: "true"})
    False
    """
    environ = os.environ if env is None else env
    if environ.get(CI_ENV_VAR):
        return False  # CI never spends, whatever else is set
    return (environ.get(LIVE_API_ENV_VAR) or "").strip().lower() in TRUTHY_VALUES
