"""Record/replay for the one paid call in `an` — the vision judge.

**Replay is the default and a miss is an error.** Recording is the explicit
opt-in `AN_LIVE_API_TESTS=1`, which is the same switch that opts a test out of
the offline network guard. There is exactly **one** switch on purpose: a second
env var would be a second SSOT for "may this run spend?", and the whole reason
this repo's gate is a positive opt-in rather than key-presence is that a key
being present is not consent.

The memoization point is `an.verify.vision.judge_envelope`, one level in from
the seam, because a memoizer hands the store only `(key, return_value)` — a
seam returning a bare `str` can record nothing beside the reply, and the
provenance that makes a cassette auditable would be unwritable.

The key comes from `an.verify.vision.judge_key`, which both the recording and
the replaying path use. One key function, or record-vs-replay drift is a thing
that can happen.
"""

from __future__ import annotations

import json
from pathlib import Path

from an.verify.vision import CassetteMiss, judge_envelope, judge_key

#: Committed. What makes the replay node free, hermetic and available to
#: everyone — including CI, where it runs on a plain `pytest -q` with the
#: offline guard armed.
CASSETTE_DIR: Path = Path(__file__).resolve().parent / "cassettes" / "vision"

#: Frozen. NOT the golden corpus: that has a re-bless lifecycle (a Chromium
#: bump is a new path and a deliberate re-bless, and goldens are re-written
#: through `an`'s own PNG writer), and re-encoding moves `sha256(file bytes)` —
#: which is what a cassette key hashes — at zero pixel change. A cassette keyed
#: on goldens would go red on an unrelated renderer PR, repairable only by a
#: credentialled human spending a real call.
FRAMES_DIR: Path = Path(__file__).resolve().parent / "fixtures" / "vision_frames"

#: A recording made against a different major SDK version may not describe the
#: same request. Checked at replay time rather than trusted.
_RECORDED_WITH = "anthropic"


class VisionCassetteStore:
    """A `MutableMapping`-shaped JSON store, one file per key."""

    def __init__(self, root: Path = CASSETTE_DIR) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        return self.root / f"{key}.json"

    def __contains__(self, key: str) -> bool:
        return self._path(key).is_file()

    def __getitem__(self, key: str) -> dict:
        path = self._path(key)
        if not path.is_file():
            raise KeyError(key)
        return json.loads(path.read_text(encoding="utf-8"))

    def __setitem__(self, key: str, value: dict) -> None:
        self._path(key).write_text(
            json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )


def frame_bytes() -> list[bytes]:
    """The frozen fixture frames, in a fixed order — order is semantic."""
    return [p.read_bytes() for p in sorted(FRAMES_DIR.glob("*.png"))]


def _inventory() -> str:
    return "\n".join(
        f"    {p.name}  {len(p.read_bytes())} bytes"
        for p in sorted(FRAMES_DIR.glob("*.png"))
    ) or "    (the fixture directory is empty)"


def _major(version: str | None) -> str | None:
    return version.split(".", 1)[0] if version else None


def memoized_judge(*, replay_only: bool = True, store: VisionCassetteStore | None = None):
    """A `judge_frames`-shaped callable backed by the cassette store.

    ``replay_only=True`` — the default, and what the free node uses — raises
    :class:`CassetteMiss` rather than reaching the network. That is what lets a
    test *prove* it did not spend instead of asserting it via a marker.
    """
    cassettes = store or VisionCassetteStore()

    def judge(*args, **kwargs) -> str:
        key = judge_key(*args, **kwargs)
        if key in cassettes:
            envelope = cassettes[key]
            recorded = _major((envelope.get("recorded_with") or {}).get(_RECORDED_WITH))
            from an.verify.vision import _anthropic_version

            current = _major(_anthropic_version())
            if recorded is not None and current is not None and recorded != current:
                raise CassetteMiss(
                    f"cassette {key} was recorded against {_RECORDED_WITH} "
                    f"{recorded}.x and this run has {current}.x. A recording "
                    "made against a different major version may not describe "
                    "the same request; re-record it deliberately."
                )
            return envelope["reply"]
        if replay_only:
            raise CassetteMiss(
                f"no cassette for key {key}.\n\n"
                "This node is replay-only: it must never reach the network, so "
                "a miss is an error rather than a fallthrough to a real call. "
                "Re-record with:\n\n"
                "    AN_LIVE_API_TESTS=1 pytest -q -m live_api\n\n"
                "The frames it keyed on are:\n" + _inventory()
            )
        envelope = judge_envelope(*args, **kwargs)
        cassettes[key] = envelope
        return envelope["reply"]

    return judge
