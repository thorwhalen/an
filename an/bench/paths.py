"""Where the bench reads its corpus from and writes its ledger to.

One module owns every path so the "bench needs a source checkout" constraint is
stated once, with a typed error, rather than surfacing as a ``FileNotFoundError``
from inside ``shutil.copytree``.
"""

from __future__ import annotations

import subprocess
from datetime import datetime, timezone
from pathlib import Path

#: Ledger rows live here, one file per (date, commit). Append-only by
#: convention: an existing row is evidence about a commit, and editing it
#: rewrites history that `an bench --compare` (an#40) reads as fact.
LEDGER_DIRNAME: str = "misc/bench/ledger"

#: Golden frames (an#38 fills this; the path convention ships now so the
#: cassette work and the corpus work do not have to agree on it later).
GOLDEN_DIRNAME: str = "misc/bench/golden"

#: Directories that must exist for a checkout to be benchable.
_REPO_MARKERS: tuple[str, ...] = ("examples", "an", "misc")


class BenchLayoutError(RuntimeError):
    """The bench was run somewhere it cannot find the corpus."""


def repo_root() -> Path:
    """The source checkout `an` was imported from.

    Raises rather than returning a plausible-but-wrong path, because the
    failure it guards is running the bench against an installed wheel: the
    corpus lives under ``examples/``, which is not packaged, so the first
    symptom would be a missing-fixture error three frames deep.

    >>> repo_root().name
    'an'
    """
    root = Path(__file__).resolve().parents[2]
    missing = [m for m in _REPO_MARKERS if not (root / m).is_dir()]
    if missing:
        raise BenchLayoutError(
            f"`an bench` needs a source checkout, not an installed wheel: "
            f"{root} is missing {missing}. The corpus lives under `examples/`, "
            "which is not packaged. Clone the repo and run the bench from there."
        )
    return root


def ledger_dir(root: Path | None = None) -> Path:
    """The ledger directory, created if absent."""
    d = (root or repo_root()) / LEDGER_DIRNAME
    d.mkdir(parents=True, exist_ok=True)
    return d


def golden_dir(root: Path | None = None) -> Path:
    """The golden-frame directory (an#38)."""
    return (root or repo_root()) / GOLDEN_DIRNAME


def golden_path(scene: str, frame_key: str, chromium_build: str, *, root=None) -> Path:
    """Where one golden frame lives.

    Keyed on the **Chromium build alone** — no platform or arch segment. The
    cross-architecture verdict measured those segments to be inert (zero
    differing pixels and zero differing PNG bytes across arm64 macOS, x86-64
    Linux and arm64 Linux, across two different SwiftShader JIT backends), so
    carrying them would force one committed copy per platform for no
    information. What the convention keeps is its real benefit: a Playwright
    bump becomes a **new path requiring a deliberate re-bless** rather than a
    red test with no explanation.

    >>> golden_path("s", "f0", "140.0.7339.16").name
    'f0-chromium140.0.7339.16.png'
    """
    return golden_dir(root) / scene / f"{frame_key}-chromium{chromium_build}.png"


def git_state(root: Path | None = None) -> dict:
    """``sha`` / ``branch`` / ``dirty`` for the checkout, or ``None``s off-git."""
    r = root or repo_root()

    def git(*args: str) -> str | None:
        try:
            out = subprocess.run(
                ["git", *args], cwd=r, capture_output=True, text=True, check=False
            )
        except OSError:
            return None
        return out.stdout.strip() if out.returncode == 0 else None

    status = git("status", "--porcelain")
    return {
        "sha": git("rev-parse", "HEAD"),
        "branch": git("rev-parse", "--abbrev-ref", "HEAD"),
        "dirty": bool(status) if status is not None else None,
    }


def ledger_path(*, root: Path | None = None, git: dict | None = None) -> Path:
    """``<date>-<sha>[-dirty].json``.

    The ``-dirty`` suffix is not decoration: a row measured against uncommitted
    edits describes no commit, and a filename that claims one would be read by
    an#40 as that commit's evidence.

    >>> ledger_path(git={"sha": "abc1234def", "dirty": True}).name.endswith("-dirty.json")
    True
    """
    r = root or repo_root()
    state = git if git is not None else git_state(r)
    sha = (state.get("sha") or "nogit")[:7]
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    suffix = "-dirty" if state.get("dirty") else ""
    return ledger_dir(r) / f"{stamp}-{sha}{suffix}.json"
