"""``scene_contract_sha256``: the fact that decides whether two rows are comparable.

Two ledger rows measured on different scenes are not "one better and one
worse" — every metric in them is **uninterpretable** relative to the other.
``edge_transition_width``'s own docstring says so: the absolute value is
scene-dependent, because a deliberately 3px black outline is legitimately
non-flat.

So each row carries a hash of the thing that was actually rendered. Deliberately
a hash of the **staged compiled scene**, not of the project directory: the
directory carries scene mtimes and a decisions log that move on every load, and
hashing it would make every row incomparable with every other for reasons that
never reached a pixel.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any


def _iter_nodes(node: Any) -> Any:
    if isinstance(node, dict):
        yield node
        for child in node.get("children") or []:
            yield from _iter_nodes(child)


def count_drawable_entities(scene_json: dict) -> int:
    """Top-level entities under the synthetic root.

    Named for what it counts. "n_entities" is ambiguous in this codebase — the
    IR's ``shot.entities`` includes ``voice`` and ``style`` refs that configure
    the render rather than appearing in it, so the two numbers differ on every
    scene with dialogue. Both are recorded; only this one is inside the hash.

    >>> count_drawable_entities({"scene": {"children": [{}, {}]}})
    2
    """
    return len((scene_json.get("scene") or {}).get("children") or [])


def count_nodes(scene_json: dict) -> int:
    """Every node in the tree, root included.

    >>> count_nodes({"scene": {"children": [{"children": [{}]}]}})
    3
    """
    return sum(1 for _ in _iter_nodes(scene_json.get("scene") or {}))


def scene_contract_sha256(scene_json: dict) -> str:
    """A stable digest of what was rendered.

    Hashed over a *reduced* form rather than the whole document, and the
    reduction is the point: the digest must move when the picture's contract
    moves and stay put otherwise. Animation keyframe floats and asset paths are
    in; nothing time- or machine-dependent is, because the compiled scene is
    already free of both (every escaping set goes through ``sorted()``, the
    palette hash is ``sum(ord(c)) % 5`` rather than Python's ``hash()``, and
    the staged JSON is written with ``sort_keys=True``).

    >>> a = scene_contract_sha256({"meta": {"fps": 24}, "scene": {"children": []}})
    >>> a == scene_contract_sha256({"scene": {"children": []}, "meta": {"fps": 24}})
    True
    >>> len(a)
    64
    """
    payload = json.dumps(scene_json, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def scenes_contract_sha256(scene_jsons: Any) -> str:
    """The contract digest for a whole scene, across every shot in timeline order.

    A single-shot scene returns **exactly** :func:`scene_contract_sha256` of its
    one staged scene, so every row written before the corpus grew a multi-shot
    fixture stays comparable — ``an bench --compare`` refuses rows whose
    contract hash differs, and a gratuitous change here would retire the only
    committed row as evidence.

    A multi-shot scene hashes the ordered list of per-shot digests, because
    hashing only the first shot would let a change to the second one pass as
    "the same scene" — which is precisely the claim this digest exists to deny.

    >>> a = {"scene": {"children": []}}
    >>> scenes_contract_sha256([a]) == scene_contract_sha256(a)
    True
    >>> scenes_contract_sha256([a, a]) == scene_contract_sha256(a)
    False
    """
    digests = [scene_contract_sha256(js) for js in scene_jsons]
    if len(digests) == 1:
        return digests[0]
    payload = json.dumps(digests, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def frames_sha256(frame_paths: Any) -> str:
    """``sha256`` over the DECODED pixels of a frame sequence, never file bytes.

    The gate for ``encode_flicker_on_held_pixels``: without it, half-res-then-
    nearest-upscale — the most visible possible flat-art regression — reports a
    7.1x *improvement*, because a flattened render gives x264 large uniform
    skip regions.

    Decoded, not file bytes, for the reason the cross-architecture verdict
    records: Chromium 1187 -> 1223 changes 144/144 PNG files and **zero**
    pixels, so a file-byte digest goes red on the first Playwright bump for a
    reason unrelated to animation quality.
    """
    import numpy as np

    h = hashlib.sha256()
    arr = np.ascontiguousarray(frame_paths)
    h.update(arr.tobytes())
    return h.hexdigest()
