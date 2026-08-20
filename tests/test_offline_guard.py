"""Tests for the offline network guard itself.

A guard nobody tests is a guard that quietly stops working. This file exists so
that disabling any half of it turns the suite red — the same reason
``illustration`` ships one alongside the guard this was adapted from.

The load-bearing property under test is that refusing and *recording* are two
separate mechanisms. This package swallows network failures in its own code
(``new_character`` catches the ``RuntimeError`` from ``fetch_dicebear`` and
generates geometry instead), so a guard that only raised would be absorbed into
a passing test. That is not hypothetical: arming this guard is what revealed
that ``test_promote_falls_back_to_new`` had been calling the DiceBear API on
every run, and that three whisper tests were resolving models over the network.
"""

from __future__ import annotations

import socket

import pytest

from .conftest import (
    BROWSER_LOCAL_HOSTS,
    OutboundNetworkAttempt,
    _is_local_address,
    _is_local_browser_url,
    fail_on_outbound_attempts,
    install_network_guard,
)


# ------------------------------------------------------------------ predicate


@pytest.mark.parametrize(
    "address,expected",
    [
        (("127.0.0.1", 8000), True),
        (("::1", 8000), True),
        (("localhost", 80), True),
        (("0.0.0.0", 80), True),
        (("", 80), True),
        ((None, 80), True),
        ("/tmp/some.sock", True),  # AF_UNIX path — not an IP endpoint at all
        (("api.dicebear.com", 443), False),
        (("huggingface.co", 443), False),
        (("93.184.216.34", 80), False),
        (("2606:2800:220:1:248:1893:25c8:1946", 443), False),
    ],
)
def test_local_address_predicate(address, expected):
    assert _is_local_address(address) is expected


def test_an_unresolved_hostname_counts_as_outbound():
    """Resolving a name is itself a network round trip, so it cannot be allowed.

    This is the subtle one: a hostname is not an IP, so a predicate that only
    checked ``ip.is_loopback`` would let every DNS lookup through.
    """
    assert _is_local_address(("not-a-real-host.invalid", 443)) is False


# ---------------------------------------------------------------------- guard


def test_guard_refuses_and_records_a_dns_lookup(monkeypatch):
    attempts = install_network_guard(monkeypatch)
    with pytest.raises(OutboundNetworkAttempt):
        socket.getaddrinfo("api.dicebear.com", 443)
    assert attempts == ["DNS lookup api.dicebear.com"]


def test_guard_refuses_and_records_a_literal_ip_connect(monkeypatch):
    """A literal IP bypasses DNS entirely — the connect patch is what catches it."""
    attempts = install_network_guard(monkeypatch)
    s = socket.socket()
    try:
        with pytest.raises(OutboundNetworkAttempt):
            s.connect(("93.184.216.34", 80))
    finally:
        s.close()
    assert attempts == ["connect ('93.184.216.34', 80)"]


def test_guard_leaves_loopback_alone(monkeypatch):
    """Loopback must stay open: the renderer serves its own runtime over it."""
    attempts = install_network_guard(monkeypatch)
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    client = socket.socket()
    try:
        client.connect(server.getsockname())  # must not raise
    finally:
        client.close()
        server.close()
    assert attempts == []


def test_the_recording_half_survives_a_swallowed_exception(monkeypatch):
    """The reason the guard records instead of only raising.

    Simulates this package's own fail-soft pattern. ``OutboundNetworkAttempt``
    derives from ``BaseException`` so a bare ``except Exception`` cannot catch
    it — but even a handler that *could*, or code that retries and gives up,
    leaves the record behind, and teardown fails on the record.
    """
    attempts = install_network_guard(monkeypatch)
    try:
        socket.getaddrinfo("api.dicebear.com", 443)
    except Exception:  # noqa: BLE001 — deliberately the swallowing shape
        pytest.fail("BaseException-derived guard was caught by `except Exception`")
    except OutboundNetworkAttempt:
        pass
    assert attempts, "the attempt must be recorded even though it also raised"
    with pytest.raises(pytest.fail.Exception):
        fail_on_outbound_attempts(attempts)


def test_fail_on_outbound_attempts_is_silent_when_nothing_happened():
    fail_on_outbound_attempts([])  # must not raise


# -------------------------------------------------------------- browser guard


@pytest.mark.parametrize(
    "url,expected",
    [
        ("http://127.0.0.1:53219/index.html", True),
        ("http://localhost:8000/runtime.js", True),
        ("http://[::1]:8000/x.js", True),
        ("data:image/png;base64,iVBORw0K", True),
        ("blob:http://127.0.0.1/abc", True),
        ("file:///tmp/x.html", True),
        ("about:blank", True),
        ("https://cdn.jsdelivr.net/npm/pixi.js@7.4.2/dist/pixi.min.js", False),
        ("https://fonts.googleapis.com/css2?family=X", False),
        ("http://169.254.169.254/latest/meta-data/", False),
    ],
)
def test_browser_url_predicate(url, expected):
    assert _is_local_browser_url(url) is expected


def test_browser_local_hosts_covers_both_loopback_spellings():
    """The renderer binds 127.0.0.1; a v6-only machine would use ::1."""
    assert {"127.0.0.1", "::1"} <= BROWSER_LOCAL_HOSTS


# ------------------------------------------------------------------- coverage


def test_the_autouse_guard_is_actually_installed():
    """Meta-test: prove the guard is armed for an ordinary test like this one.

    Without this, deleting ``autouse=True`` would leave every test in the suite
    unguarded and nothing in this file would notice — every other test here
    installs its own guard via monkeypatch and would keep passing.

    Two constraints shape how this is written, and both were found by mutation
    testing this very test:

    - It must NOT request ``_no_outbound_network`` by name. Doing so installs
      the fixture regardless of ``autouse``, so the test passes on the mutant.
    - It must NOT trip the guard, because a recorded attempt makes the fixture's
      own teardown fail the test for succeeding.

    So it inspects the patch instead of exercising it: a guarded run has
    ``socket.getaddrinfo`` replaced by the closure from ``install_network_guard``.
    """
    qualname = getattr(socket.getaddrinfo, "__qualname__", "")
    assert qualname.startswith("install_network_guard."), (
        "socket.getaddrinfo is unpatched, so the autouse offline guard is NOT "
        f"armed for ordinary tests (qualname={qualname!r}). Every test in this "
        "suite is currently free to reach the network."
    )
