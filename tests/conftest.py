"""Shared pytest configuration — the ``live_api`` gate.

Almost the whole suite is offline by design: TTS and lip-sync default to local
providers, and rendering is ffmpeg + a headless browser. A handful of tests
instead exercise the **real paid APIs** (ElevenLabs, Anthropic) to catch contract
drift that a stub would hide. Those are marked ``live_api`` and governed here.

**A key being present is not consent to spend.** These tests used to be gated on
"the SDK is installed and an API key is exported", which is satisfied by every
developer machine and every agent session that has ever sourced a shell profile —
so a plain ``pytest -q`` silently made real, billed calls. That is what this gate
exists to stop, and it is why the opt-in is a *separate, positive* signal rather
than an inference from the key.

A ``live_api`` test runs only when ALL of these hold:

- :data:`LIVE_API_ENV_VAR` is set to a truthy value — the explicit "yes, spend
  money on this run" signal. Nothing else implies it.
- ``CI`` is unset. CI must never spend, whatever else is configured.
- The test's own SDK and credential are available (each test declares its own).

So the default everywhere — laptop, agent session, CI — is *skip*. Running them is
a deliberate act:

    AN_LIVE_API_TESTS=1 pytest -q -m live_api

and ``pytest -q -m "not live_api"`` is always safe.
"""

from __future__ import annotations

import os

import pytest

#: Set this truthy to opt a run in to real, billed API calls.
LIVE_API_ENV_VAR = "AN_LIVE_API_TESTS"

_TRUTHY = frozenset({"1", "true", "yes", "on"})


def live_api_enabled() -> bool:
    """Whether this run has explicitly opted in to paid API calls.

    >>> live_api_enabled() or True  # never asserts on the ambient environment
    True
    """
    if os.environ.get("CI"):
        return False  # CI never spends, whatever else is set
    return (os.environ.get(LIVE_API_ENV_VAR) or "").strip().lower() in _TRUTHY


#: Apply to any test that makes a real, billed call:
#: ``pytestmark = [requires_live_api, pytest.mark.skipif(...)]`` for its own
#: SDK/credential needs.
requires_live_api = pytest.mark.skipif(
    not live_api_enabled(),
    reason=(
        f"real paid API call — set {LIVE_API_ENV_VAR}=1 to opt in "
        "(a key being present is not consent to spend)"
    ),
)


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "live_api: makes a real, billed API call; skipped unless "
        f"{LIVE_API_ENV_VAR} is truthy and CI is unset",
    )
