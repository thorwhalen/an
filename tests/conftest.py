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

#: Markers that opt a test out of the offline network guard.
#:
#: TWO markers, because they are different promises and collapsing them would
#: weaken the one that matters:
#:
#: - ``live_api`` — this test SPENDS MONEY. Gated on an explicit positive opt-in
#:   env var as well, because a key being present is not consent to spend.
#: - ``live`` — this test reaches the network but costs nothing (checking that a
#:   pinned upstream snapshot has not drifted, say). It still must not run in the
#:   hermetic suite, but it needs no spending gate.
#:
#: Marking a free test ``live_api`` would be convenient and would quietly erode
#: what that marker promises.
_NETWORK_OPT_OUT_MARKERS = ("live_api", "live")

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
    config.addinivalue_line(
        "markers",
        "live: reaches the network but costs nothing; exempt from the offline "
        "guard, and skipped in CI",
    )
    config.addinivalue_line(
        "markers",
        "browser: needs a headless Chromium via Playwright; gated by the browser "
        f"gate below ({BROWSER_ENV_VAR})",
    )
    config.addinivalue_line(
        "markers",
        "ffmpeg: needs the ffmpeg binary on PATH; gated by the browser gate below",
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
    if any(request.node.get_closest_marker(m) for m in _NETWORK_OPT_OUT_MARKERS):
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
    # A plain import, not `importorskip`: every test that requests this fixture is
    # `browser`-marked, so the gate has already established Playwright is present.
    # An importorskip here could only turn an inconsistent state into a silent skip.
    import playwright.sync_api as playwright_api

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


# ---------------------------------------------------------------------------
# The browser gate.
#
# Rendering tests need a headless Chromium (via Playwright) and ffmpeg. Neither
# is installed in CI: `playwright` lives in the `cutout` extra and CI installs
# `.[dev]`. So these tests have never run there — every "verified by rendering"
# claim in this repo is verified on a developer machine (an#22).
#
# That is a deliberate choice, not an oversight: the tests take ~45 s locally,
# but making them run in CI costs a ~200 MB browser download plus an ffmpeg
# install on every push. The decision is to keep PR CI fast and run them
# on demand — `.github/workflows/browser-tests.yml`, dispatched manually.
#
# What is NOT acceptable is how that used to be implemented. Eleven test modules
# each opened with
#
#     playwright = pytest.importorskip("playwright.sync_api", ...)
#
# at MODULE level, which does not skip a browser test — it aborts the module
# import, so the tests are never COLLECTED at all. Measured on this commit's
# parent: 472 tests collected with Playwright installed, 438 without. Of the 34
# that vanished, roughly half need no browser whatsoever — the whole of
# `test_vision_verifier.py`'s JSON-parser suite, `an.verify.media`'s pure-numpy
# SSIM tests (the very primitives Wave 2's ledger is built on), and two
# `skip_render=True` orchestrator tests. They were collateral damage of a skip
# aimed at something else, and nothing reported it, because a test that is not
# collected does not appear in the skip count either.
#
# So the contract here is:
#
#   1. WHICH TESTS EXIST MUST NOT DEPEND ON WHAT IS INSTALLED. Collection always
#      succeeds. Playwright is imported inside test bodies and fixtures, never
#      at module scope. `tests/test_browser_gate.py` asserts the collected node
#      id set is identical with and without Playwright.
#   2. THE GATE IS A MARKER, and it is applied at `pytest_collection_modifyitems`
#      so every gated test is counted and carries a precise reason.
#   3. SKIPPING IS ANNOUNCED. `pytest_terminal_summary` prints one line saying
#      how many rendering tests ran and how many did not, so a green run never
#      quietly means "zero pixels were checked".
#   4. AN EXPLICIT OPT-IN THAT CANNOT BE HONOURED IS AN ERROR, NOT A SKIP. If
#      AN_BROWSER_TESTS is truthy and there is no Chromium, the run aborts. A CI
#      job whose `playwright install` silently failed must go red, not green
#      with 31 skips — that is the same failure this whole section exists to end.
# ---------------------------------------------------------------------------

import functools
import shutil

#: Set truthy to run the browser/rendering tests where they would otherwise be
#: skipped (this is what `.github/workflows/browser-tests.yml` sets); set falsy
#: to force them off on a machine that could run them.
BROWSER_ENV_VAR = "AN_BROWSER_TESTS"

#: How to get a browser, quoted verbatim in the skip reason so it is actionable.
_INSTALL_HINT = "pip install -e '.[cutout]' && playwright install chromium"


def _env_flag(env, name):
    """Return True/False for an explicitly-set flag, or None when unset.

    Tri-state on purpose: "unset" and "set to 0" are different instructions.

    >>> _env_flag({}, "X") is None
    True
    >>> _env_flag({"X": "1"}, "X")
    True
    >>> _env_flag({"X": "0"}, "X")
    False
    >>> _env_flag({"X": ""}, "X") is None
    True
    """
    raw = env.get(name)
    if raw is None or not raw.strip():
        return None
    return raw.strip().lower() in _TRUTHY


@functools.lru_cache(maxsize=1)
def chromium_available() -> bool:
    """Whether a Playwright Chromium can actually be launched.

    Cached: this launches a real browser, and eleven modules used to ask
    independently at import time.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return False
    try:
        with sync_playwright() as p:
            p.chromium.launch(args=["--no-sandbox"]).close()
        return True
    except Exception:
        return False


@functools.lru_cache(maxsize=1)
def ffmpeg_available() -> bool:
    """Whether the ffmpeg binary is on PATH."""
    return shutil.which("ffmpeg") is not None


def requirement_verdict(name, *, opt_in, available, ci, install_hint):
    """Decide what to do about one external requirement.

    Returns ``(action, message)`` where action is one of ``"run"``, ``"skip"``
    or ``"error"``. Pure, so the whole decision matrix is testable without
    touching the environment — see ``tests/test_browser_gate.py``.

    ``opt_in`` is tri-state: True (explicitly requested), False (explicitly
    disabled), None (no instruction).

    >>> requirement_verdict("x", opt_in=None, available=True, ci=False, install_hint="h")[0]
    'run'
    >>> requirement_verdict("x", opt_in=None, available=True, ci=True, install_hint="h")[0]
    'skip'
    >>> requirement_verdict("x", opt_in=True, available=False, ci=True, install_hint="h")[0]
    'error'
    >>> requirement_verdict("x", opt_in=False, available=True, ci=False, install_hint="h")[0]
    'skip'
    """
    if opt_in is False:
        return "skip", f"{name} tests disabled by {BROWSER_ENV_VAR}"
    if opt_in is True:
        if available:
            return "run", ""
        return (
            "error",
            f"{BROWSER_ENV_VAR} asked for {name} tests but {name} is unavailable. "
            f"This is an error rather than a skip on purpose: an explicit request "
            f"that silently degrades to a skip is how a green run comes to mean "
            f"nothing. Install it ({install_hint}) or unset {BROWSER_ENV_VAR}.",
        )
    if ci:
        return (
            "skip",
            f"CI installs no {name} (an#22). Set {BROWSER_ENV_VAR}=1 to opt in, or "
            f"dispatch .github/workflows/browser-tests.yml",
        )
    if not available:
        return "skip", f"no {name} — {install_hint}"
    return "run", ""


#: Populated at collection so `pytest_terminal_summary` can report honestly.
_GATE_REPORT: dict = {}


def _gate_verdicts(env=None):
    """The (action, message) verdict for each gated requirement."""
    env = os.environ if env is None else env
    opt_in = _env_flag(env, BROWSER_ENV_VAR)
    ci = bool(env.get("CI"))
    return {
        "browser": requirement_verdict(
            "headless browser",
            opt_in=opt_in,
            available=chromium_available(),
            ci=ci,
            install_hint=_INSTALL_HINT,
        ),
        "ffmpeg": requirement_verdict(
            "ffmpeg",
            opt_in=opt_in,
            available=ffmpeg_available(),
            ci=ci,
            install_hint="e.g. `brew install ffmpeg` / `apt-get install ffmpeg`",
        ),
    }


def pytest_collection_modifyitems(config, items):
    """Skip gated tests by marker — never by refusing to collect them."""
    verdicts = _gate_verdicts()
    for name, (action, message) in verdicts.items():
        if action == "error":
            raise pytest.UsageError(message)
    counts = {name: {"total": 0, "skipped": 0} for name in verdicts}
    for item in items:
        for name, (action, message) in verdicts.items():
            if item.get_closest_marker(name) is None:
                continue
            counts[name]["total"] += 1
            if action == "skip":
                counts[name]["skipped"] += 1
                item.add_marker(pytest.mark.skip(reason=message))
    _GATE_REPORT.clear()
    _GATE_REPORT.update(
        {name: dict(counts[name], reason=verdicts[name][1]) for name in verdicts}
    )


def pytest_terminal_summary(terminalreporter, exitstatus, config):
    """Say out loud how many rendering tests actually ran.

    Without this, "N passed" is silent about whether any pixel was ever looked
    at, which is exactly how this repo came to believe its renders were tested.
    """
    for name, info in sorted(_GATE_REPORT.items()):
        total, skipped = info["total"], info["skipped"]
        if not total:
            continue
        ran = total - skipped
        line = f"{name} tests: {total} collected, {ran} ran"
        if skipped:
            line += f", {skipped} skipped — {info['reason']}"
        terminalreporter.write_line(line)
