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


# ---------------------------------------------------------------------------
# The offline network guard, Python side.
#
# Adapted near-verbatim from illustration's guard (illustration/conftest.py).
# Deliberately not a third shape: same three patch points, same address
# predicate, same BaseException, same append-then-raise, same teardown
# assertion, same split into two module-level functions so both halves are
# individually testable. Only the opt-out marker differs (`live_api` here).
#
# The RECORDING half is the load-bearing one, and it matters more here than it
# does in illustration, because `an` degrades network failures silently in its
# own code: `an/characters/factory.py` catches the RuntimeError from
# `fetch_dicebear` and falls back to generated geometry. A refusal alone gets
# absorbed and the test stays green; asserting the record at teardown is what
# actually holds the line.
#
# This covers Python only. The cutout renderer drives Chromium, which fetches
# from another process — see `hermetic_browser` below.
# ---------------------------------------------------------------------------

import ipaddress
import socket


class OutboundNetworkAttempt(BaseException):
    """An offline test tried to talk to a non-local host.

    Derived from ``BaseException`` rather than ``Exception`` on purpose: this
    package's fail-soft paths (`new_character`'s ``except RuntimeError``, the
    verifiers' broad handlers) would otherwise catch it and the attempt would
    vanish into a passing test.
    """


#: Hostnames that mean "this machine" without a DNS round trip.
LOCAL_HOSTNAMES = frozenset(
    {"", "localhost", "localhost.localdomain", "ip6-localhost", "ip6-loopback"}
)


def _is_local_address(address) -> bool:
    """True when ``address`` is loopback, unspecified, or not an IP endpoint.

    Non-tuple addresses (AF_UNIX paths, AF_NETLINK ints) are local by
    construction. A bare hostname that is not a known loopback alias counts as
    outbound, because resolving it is itself a network round trip.

    >>> _is_local_address(("127.0.0.1", 8000))
    True
    >>> _is_local_address(("api.dicebear.com", 443))
    False
    >>> _is_local_address(("::1", 80))
    True
    """
    if not isinstance(address, (tuple, list)) or not address:
        return True
    host = address[0]
    if host is None:
        return True
    host = str(host)
    if host in LOCAL_HOSTNAMES:
        return True
    try:
        ip = ipaddress.ip_address(host.split("%", 1)[0])
    except ValueError:
        return False  # an unresolved name — looking it up is already outbound
    return ip.is_loopback or ip.is_unspecified


def install_network_guard(monkeypatch) -> list:
    """Refuse and record every non-local socket use; return the record list.

    Split out of the fixture so both halves of the guard are reachable from a
    test — see ``tests/test_offline_guard.py``.
    """
    attempts: list[str] = []
    real_connect = socket.socket.connect
    real_connect_ex = socket.socket.connect_ex
    real_getaddrinfo = socket.getaddrinfo

    def refuse(what, target):
        attempts.append(f"{what} {target}")
        raise OutboundNetworkAttempt(
            f"Offline test attempted {what} to {target!r}. This suite is "
            "hermetic: stub the seam that fetches (e.g. pass use_dicebear=False), "
            "or mark the test `live_api` if it genuinely must reach the network."
        )

    def connect(self, address, *args, **kwargs):
        if not _is_local_address(address):
            refuse("connect", str(address))
        return real_connect(self, address, *args, **kwargs)

    def connect_ex(self, address, *args, **kwargs):
        if not _is_local_address(address):
            refuse("connect", str(address))
        return real_connect_ex(self, address, *args, **kwargs)

    def getaddrinfo(host, port, *args, **kwargs):
        if not _is_local_address((host, port)):
            refuse("DNS lookup", str(host))
        return real_getaddrinfo(host, port, *args, **kwargs)

    monkeypatch.setattr(socket.socket, "connect", connect)
    monkeypatch.setattr(socket.socket, "connect_ex", connect_ex)
    monkeypatch.setattr(socket, "getaddrinfo", getaddrinfo)
    return attempts


def fail_on_outbound_attempts(attempts) -> None:
    """Fail the test if the guard recorded anything — the swallow-proof half."""
    if attempts:
        pytest.fail(
            "Offline test performed outbound network I/O: "
            + "; ".join(sorted(set(attempts)))
        )


@pytest.fixture(autouse=True)
def _no_outbound_network(request, monkeypatch):
    """Fail the test if it tries to reach a non-local host.

    Loopback stays open on purpose: the cutout renderer serves its runtime from
    its own ``http://127.0.0.1:<port>`` server (``_serve_dir``), and the preview
    tests fetch from it. Tests marked ``live_api`` opt out.
    """
    if request.node.get_closest_marker("live_api") is not None:
        yield []
        return
    attempts = install_network_guard(monkeypatch)
    yield attempts
    fail_on_outbound_attempts(attempts)


# ---------------------------------------------------------------------------
# The offline network guard, browser side.
#
# A socket patch cannot see Chromium: it fetches from another process. Measured
# — with only the Python guard installed, the cutout render tests all PASS while
# Chromium downloads the engine from a CDN. Playwright route interception is
# what closes that, and it is the only mechanism that can distinguish "we
# vendored the engine" from "we vendored it and the page actually uses it".
#
# Not autouse: it is opt-in per test, because it wraps Playwright's Browser
# rather than a global, and only the render/preview tests drive a browser.
# ---------------------------------------------------------------------------

#: What a rendered page may reach: an's own _serve_dir, and nothing else.
BROWSER_LOCAL_HOSTS = frozenset({"127.0.0.1", "::1", "localhost", "[::1]"})

#: Schemes that never leave the machine.
BROWSER_LOCAL_SCHEMES = ("data:", "blob:", "file:", "about:")


def _is_local_browser_url(url: str) -> bool:
    """True when a browser request stays on this machine.

    >>> _is_local_browser_url("http://127.0.0.1:53219/index.html")
    True
    >>> _is_local_browser_url("https://cdn.jsdelivr.net/npm/pixi.js@7.4.2/dist/pixi.min.js")
    False
    >>> _is_local_browser_url("data:image/png;base64,iVBORw0K")
    True
    """
    if url.startswith(BROWSER_LOCAL_SCHEMES):
        return True
    from urllib.parse import urlparse

    return (urlparse(url).hostname or "") in BROWSER_LOCAL_HOSTS


@pytest.fixture
def hermetic_browser(monkeypatch):
    """Abort every non-loopback browser request; yield the (allowed, blocked) record.

    Wraps ``Browser.new_page`` because the renderer exposes no hook to install a
    route. Yields a dict with ``allowed`` and ``blocked`` URL lists so a test can
    assert on *what* was requested, not merely that nothing failed — the same
    record-as-well-as-refuse discipline as the Python guard.
    """
    playwright_api = pytest.importorskip("playwright.sync_api")

    record = {"allowed": [], "blocked": []}
    real_new_page = playwright_api.Browser.new_page

    def _route(route, request):
        url = request.url
        if _is_local_browser_url(url):
            record["allowed"].append(url)
            route.fallback()
        else:
            record["blocked"].append(url)
            route.abort("blockedbyclient")

    def new_page(self, *args, **kwargs):
        page = real_new_page(self, *args, **kwargs)
        page.route("**/*", _route)
        return page

    monkeypatch.setattr(playwright_api.Browser, "new_page", new_page)
    yield record
