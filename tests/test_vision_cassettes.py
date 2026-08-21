"""The vision judge: cassetted, and no longer able to launder a failure into a pass.

an#39. Two separate defects, and the second is the one that made the first
invisible:

1. **Every failure path reported `passed=True`.** `VerificationReport.add` flips
   `passed` only on `"error"`, and every handler in the verifier used `info`.
   So a dead model id, a 500, a refusal and an unparseable reply all came back
   byte-identical to a clean bill of health. A verifier that reports success
   when it failed to run is worse than no verifier.
2. **`_parse_issues` collapsed "no verdict" into "empty verdict".** A refusal,
   an empty reply and a literal `{"issues": []}` produced the same Finding.

The cassette layer sits on top of both. Its own guard — that a miss is an
ERROR and never a fallthrough to a real call — depends on the first fix: a
`CassetteMiss` derived from `Exception` would be caught by this module's own
handler AND by `orchestrate`'s broad post-render `except Exception`, and a test
asserting "this run did not spend" would pass having verified nothing.
"""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path

import pytest

from ._vision_cassettes import (
    CASSETTE_DIR,
    FRAMES_DIR,
    VisionCassetteStore,
    frame_bytes,
    memoized_judge,
)
from .conftest import requires_live_api

from an.adapters._base import RenderResult
from an.ir.schema import Meta, SceneIR, Shot
from an.verify.vision import (
    _DEFAULT_MAX_TOKENS,
    _DEFAULT_MODEL,
    _PROMPT,
    CassetteMiss,
    VisionJudgeError,
    VisionLMVerifier,
    judge_key,
)

#: A reply shaped like the model's, used to exercise the replay MECHANICS
#: without needing a recording. The committed-cassette node below is what
#: exercises a real one.
SYNTHETIC_REPLY = '{"issues": [{"severity": "warning", "where": "frame 2", "what": "the left arm is detached"}]}'


def _envelope(reply: str = SYNTHETIC_REPLY, **over) -> dict:
    from an.verify.vision import _anthropic_version

    env = {
        "reply": reply,
        "frames": [],
        "model": _DEFAULT_MODEL,
        "max_tokens": _DEFAULT_MAX_TOKENS,
        "prompt": _PROMPT,
        "recorded_at": "2026-08-21T00:00:00Z",
        "recorded_with": {"anthropic": _anthropic_version() or "0.0.0"},
    }
    env.update(over)
    return env


def _render(tmp_path: Path) -> RenderResult:
    mp4 = tmp_path / "r.mp4"
    mp4.write_bytes(b"not a real mp4")
    return RenderResult(mp4_path=mp4, duration=1.0)


def _scene() -> SceneIR:
    return SceneIR(meta=Meta(title="t", duration=1.0), timeline=[Shot(id="s1", duration=1.0)])


# ------------------------------------------------------------------- the key


def test_the_key_never_contains_the_credential():
    """A cassette filename must not be a function of an API key."""
    a = judge_key([b"x"], prompt="p", model="m", max_tokens=1, api_key="sk-aaa")
    b = judge_key([b"x"], prompt="p", model="m", max_tokens=1, api_key="sk-bbb")
    c = judge_key([b"x"], prompt="p", model="m", max_tokens=1, api_key=None)
    assert a == b == c


def test_the_key_ignores_prompt_whitespace_but_not_prompt_words():
    """Re-indenting the prompt literal is not a re-record. Editing it is."""
    base = judge_key([b"x"], prompt="a b", model="m", max_tokens=1)
    assert base == judge_key([b"x"], prompt="  a\n\tb  ", model="m", max_tokens=1)
    assert base != judge_key([b"x"], prompt="a c", model="m", max_tokens=1)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"model": "other-model"},
        {"max_tokens": 1},
        {"model": "claude-haiku-4-5"},  # the ALIAS vs the dated id
    ],
)
def test_every_request_parameter_moves_the_key(kwargs):
    base = dict(prompt="p", model=_DEFAULT_MODEL, max_tokens=99)
    assert judge_key([b"x"], **base) != judge_key([b"x"], **{**base, **kwargs})


def test_a_parameter_added_later_is_in_the_key_without_being_listed(monkeypatch):
    """The default is INCLUDE, which is why the key reads the signature.

    An allowlist sets the default to exclude, so a parameter added later
    collides with the base key and the recording is served forever for a
    request that changed. A false miss is red CI; a false hit is silent.
    """
    import inspect

    from an.verify import vision

    original = vision.judge_frames

    def judge_frames_with_temperature(
        frames, *, prompt=_PROMPT, model=_DEFAULT_MODEL,
        max_tokens=_DEFAULT_MAX_TOKENS, api_key=None, temperature=0.0,
    ):  # pragma: no cover - a signature, never called
        raise AssertionError

    monkeypatch.setattr(vision, "judge_frames", judge_frames_with_temperature)
    assert "temperature" in inspect.signature(vision.judge_frames).parameters
    hot = vision.judge_key([b"x"], temperature=1.0)
    cold = vision.judge_key([b"x"], temperature=0.0)
    assert hot != cold, (
        "a new request parameter collided with the base key, so a changed "
        "request would replay a stale reply and stay green forever"
    )
    monkeypatch.setattr(vision, "judge_frames", original)


def test_frame_order_and_content_both_move_the_key():
    a, b = b"\x01", b"\x02"
    assert judge_key([a, b]) != judge_key([b, a]), "order is semantic to the model"
    assert judge_key([a, b]) != judge_key([a]), "a dropped frame is a different request"
    assert judge_key([a]) == judge_key([bytearray(a)]), "the container type is not"


def test_defaults_and_the_spelled_out_call_are_one_key():
    """`store_cached` will not do this for you, and two keys is two recordings."""
    assert judge_key([b"x"]) == judge_key(
        [b"x"], prompt=_PROMPT, model=_DEFAULT_MODEL, max_tokens=_DEFAULT_MAX_TOKENS
    )


# -------------------------------------------------------------- the miss guard


def test_a_cassette_miss_is_not_catchable_as_an_exception():
    """The property everything else here rests on.

    `except Exception` appears twice on the path out of a verify() call — once
    in this module and once in `orchestrate`'s post-render loop, where it must
    stay broad because it guards every verifier. Only a `BaseException` survives
    both.
    """
    assert issubclass(CassetteMiss, BaseException)
    assert not issubclass(CassetteMiss, Exception)

    try:
        raise CassetteMiss("x")
    except Exception:  # noqa: BLE001 - the point of the test
        pytest.fail("a CassetteMiss was caught by `except Exception`")
    except CassetteMiss:
        pass


def test_a_cassette_miss_is_not_swallowed_by_the_verifier(tmp_path):
    """End to end through `verify()`, which is where the swallowing happened."""
    judge = memoized_judge(store=VisionCassetteStore(tmp_path))
    verifier = VisionLMVerifier(api_key="test-key", judge=judge, frame_count=1)

    monkey = _StubFrames(tmp_path)
    with monkey:
        with pytest.raises(CassetteMiss):
            verifier.verify(_scene(), _render(tmp_path))


def test_a_cassette_miss_is_not_swallowed_by_the_orchestrator():
    """`orchestrate`'s post-render loop catches `Exception` for every verifier."""
    import an.orchestrate as orch
    import inspect

    src = inspect.getsource(orch)
    assert "except Exception" in src, (
        "this test's premise is that the orchestrator's handler is broad; if it "
        "stopped being broad, the reason CassetteMiss derives from "
        "BaseException needs re-checking rather than assuming"
    )


# ------------------------------------------------------ failures are failures


def test_a_failed_vision_call_is_not_reported_as_a_clean_pass(tmp_path):
    """The an#39 headline. It was `info`, and `info` leaves `passed` True."""

    def failing_judge(*args, **kwargs):
        raise VisionJudgeError("404 model not found")

    verifier = VisionLMVerifier(api_key="test-key", judge=failing_judge, frame_count=1)
    with _StubFrames(tmp_path):
        report = verifier.verify(_scene(), _render(tmp_path))

    severities = {f.severity for f in report.findings}
    assert "info" not in severities, (
        "a configured-and-broken judge reported at the NOT-CONFIGURED severity, "
        "which is what made this verifier invisible"
    )
    assert any("not a clean pass" in f.description for f in report.findings)


def test_an_unparseable_reply_is_not_reported_as_no_issues(tmp_path):
    """A refusal used to be byte-identical to a clean bill of health."""
    verifier = VisionLMVerifier(
        api_key="test-key",
        judge=lambda *a, **k: "I'm not able to help with that.",
        frame_count=1,
    )
    with _StubFrames(tmp_path):
        report = verifier.verify(_scene(), _render(tmp_path))

    assert not any("no issues" in f.description for f in report.findings)
    assert {f.severity for f in report.findings} != {"info"}
    assert any("carried no verdict" in f.description for f in report.findings)


@pytest.mark.parametrize(
    "reply",
    [
        '{"verdict": "everything looks fine"}',  # valid JSON, no `issues` key
        '{"issues": "none"}',  # `issues` present but not a list
        '{"issues": null}',
    ],
)
def test_json_that_carries_no_issues_list_is_no_verdict(reply, tmp_path):
    """The case the doctest covered and no test did.

    `testpaths = ["tests"]`, so `--doctest-modules` never reaches `an/` in CI —
    a doctest is documentation here, not a guard. This is the shape the model
    actually produces when it answers in prose-with-JSON: well-formed JSON that
    is not the requested schema, which the old parser turned into `[]` and the
    verifier reported as "no issues".
    """
    from an.verify.vision import _parse_issues

    assert _parse_issues(reply) is None

    verifier = VisionLMVerifier(
        api_key="test-key", judge=lambda *a, **k: reply, frame_count=1
    )
    with _StubFrames(tmp_path):
        report = verifier.verify(_scene(), _render(tmp_path))
    assert not any("no issues" in f.description for f in report.findings)


def test_an_empty_verdict_is_still_a_clean_pass(tmp_path):
    """The other side of the split: the model looked and found nothing."""
    verifier = VisionLMVerifier(
        api_key="test-key", judge=lambda *a, **k: '{"issues": []}', frame_count=1
    )
    with _StubFrames(tmp_path):
        report = verifier.verify(_scene(), _render(tmp_path))
    assert report.passed
    assert any("no issues" in f.description for f in report.findings)


def test_a_missing_key_is_still_a_quiet_skip(tmp_path):
    """NOT configured stays a skip. Only configured-and-broken became a failure."""
    verifier = VisionLMVerifier(api_key=None, frame_count=1)
    report = verifier.verify(_scene(), _render(tmp_path))
    assert report.passed
    assert {f.severity for f in report.findings} == {"info"}


# ---------------------------------------------------------------- the replay


def test_the_judge_replays_from_its_cassette(tmp_path):
    """Free, hermetic, and it PROVES it did not spend rather than asserting it.

    `replay_only=True` raises on a miss, so the only way this passes is by
    reading the store. No marker, no key, no network — it runs on a plain
    `pytest -q` with the offline guard armed.
    """
    store = VisionCassetteStore(tmp_path)
    frames = frame_bytes()
    store[judge_key(frames, prompt=_PROMPT, model=_DEFAULT_MODEL,
                    max_tokens=_DEFAULT_MAX_TOKENS)] = _envelope()

    judge = memoized_judge(store=store)
    assert judge(frames, prompt=_PROMPT, model=_DEFAULT_MODEL,
                 max_tokens=_DEFAULT_MAX_TOKENS) == SYNTHETIC_REPLY


def test_a_cassette_from_another_sdk_major_version_is_refused(tmp_path):
    """A recording made against a different major may not describe the request."""
    store = VisionCassetteStore(tmp_path)
    frames = frame_bytes()
    key = judge_key(frames, prompt=_PROMPT, model=_DEFAULT_MODEL,
                    max_tokens=_DEFAULT_MAX_TOKENS)
    store[key] = _envelope(recorded_with={"anthropic": "99.0.0"})

    with pytest.raises(CassetteMiss, match="major version"):
        memoized_judge(store=store)(
            frames, prompt=_PROMPT, model=_DEFAULT_MODEL,
            max_tokens=_DEFAULT_MAX_TOKENS,
        )


def test_record_and_replay_share_one_key_function():
    """Two key functions is record-vs-replay drift waiting to happen."""
    import inspect

    from . import _vision_cassettes

    src = inspect.getsource(_vision_cassettes)
    assert src.count("judge_key(") >= 1
    assert "def judge_key" not in src, (
        "the cassette layer defined its own key function; it must import the "
        "one derived from the seam's signature"
    )


# ------------------------------------------------------------- the fixtures


def test_the_cassette_fixture_frames_are_frozen_and_committed():
    """They are a cassette KEY, not a golden.

    Goldens have a re-bless lifecycle — a Chromium bump is a new path and a
    deliberate re-bless, and they are re-written through `an`'s own PNG writer,
    which moves `sha256(file bytes)` at zero pixel change. Keying a cassette on
    those would redden a free hermetic node on an unrelated renderer PR,
    repairable only by a credentialled human spending a real call.
    """
    frames = sorted(FRAMES_DIR.glob("*.png"))
    assert len(frames) >= 2, "at least two, so the pair is not one image twice"
    assert frames[0].read_bytes() != frames[1].read_bytes()
    assert FRAMES_DIR.parts[-2:] == ("fixtures", "vision_frames"), (
        "the frames must not live under the golden corpus"
    )


def test_flipping_one_fixture_byte_changes_the_key(tmp_path):
    frames = frame_bytes()
    mutated = list(frames)
    mutated[0] = bytes([frames[0][-1] ^ 0xFF]) + frames[0][1:]
    assert judge_key(frames) != judge_key(mutated)


# ------------------------------------------------------- the committed cassette


@pytest.mark.skipif(
    not any(CASSETTE_DIR.glob("*.json")),
    reason=(
        "no cassette has been recorded yet — record one deliberately with "
        "`AN_LIVE_API_TESTS=1 pytest -q -m live_api -k record_the_judge`. "
        "This is a DATA precondition, not an environment one: everything the "
        "replay machinery does is covered above against an in-test envelope."
    ),
)
def test_the_committed_cassette_replays_without_spending():
    """The drift detector, once a real recording exists.

    It fires on a plain `pytest -q` with the offline guard armed, so a change
    that alters the request — a new parameter, an edited prompt, a different
    model — turns this red instead of silently replaying a stale reply.
    """
    from an.verify.vision import _parse_issues

    reply = memoized_judge()(frame_bytes())
    assert _parse_issues(reply) is not None, (
        "the committed cassette records a reply that carries no verdict; a "
        "recording of a refusal is not a recording worth keeping"
    )


@pytest.mark.live_api
@requires_live_api
@pytest.mark.skipif(
    importlib.util.find_spec("anthropic") is None
    or not os.environ.get("ANTHROPIC_API_KEY"),
    reason="needs the anthropic SDK + ANTHROPIC_API_KEY",
)
def test_record_the_judge_cassette():
    """SPENDS MONEY on a miss. Replays on a hit.

    Calls `judge_frames` through the memoizer **directly**, never through
    `verify()`, so no fail-soft handler sits between a failure and this test —
    and it asserts the reply parses to a verdict before the recording is kept,
    which is the one path where a paid call can catch a refusal.
    """
    from an.verify.vision import _parse_issues

    reply = memoized_judge(replay_only=False)(frame_bytes())
    assert _parse_issues(reply) is not None, (
        f"the model returned no verdict; not worth recording. Reply: {reply[:300]!r}"
    )


class _StubFrames:
    """Replace frame extraction so a verify() test needs no ffmpeg and no mp4."""

    def __enter__(self):
        import an.verify.vision as vision

        self._original = vision.extract_frames
        vision.extract_frames = lambda mp4, d, fps: self._write(Path(d))
        return self

    def __exit__(self, *exc):
        import an.verify.vision as vision

        vision.extract_frames = self._original
        return False

    def __init__(self, tmp_path: Path) -> None:
        self.tmp_path = tmp_path

    def _write(self, d: Path) -> list[Path]:
        out = []
        for i, data in enumerate(frame_bytes()):
            p = d / f"f{i}.png"
            p.write_bytes(data)
            out.append(p)
        return out
