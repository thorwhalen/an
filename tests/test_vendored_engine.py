"""The rendering engine ships with the package, and a render never reaches out.

Three things are asserted here, and the third is the only one that can tell
"we vendored the engine" apart from "we vendored it and the page actually uses
it":

1. The vendored bytes are exactly the published release — pinned by digest.
2. The MIT notice ships with them.
3. A real render succeeds with every non-loopback request aborted.

Before this, `index.html` fetched the engine from a CDN at render time, which
made a cold render need the network *and* made the per-shot content-hash cache
unsound: a third party could change the renderer without changing any cache key.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
RUNTIME_DIR = REPO_ROOT / "an/data/cutout_runtime"
VENDOR_DIR = RUNTIME_DIR / "vendor"

#: The published pixi.js@7.4.2 bundle. Obtained from the npm tarball
#: (https://registry.npmjs.org/pixi.js/-/pixi.js-7.4.2.tgz, whose sha512 matches
#: the registry's own `dist.integrity`), and byte-identical to the jsDelivr copy
#: the runtime used to fetch. Replace the file and this digest together.
PIXI_BUNDLE_SHA256 = "9ddba9cd78bc8610a1d445ec939393888be83925c78e40d66d9a17e98450228d"
PIXI_BUNDLE_BYTES = 456_133

#: The MIT licence text from the same tarball, byte-identical to the one at the
#: v7.4.2 git tag (verified: same digest).
PIXI_LICENSE_SHA256 = "5ce7447bc57f7349ffc48338782fbcabe613696e00712b20d66bc58e780f9473"


def _sha256(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def test_vendored_engine_is_the_published_bytes():
    """Pin the bundle by digest.

    A vendored dependency nobody can check is worse than a CDN one: at least a
    CDN URL names its version. If this fails after a deliberate upgrade, update
    the constants; if it fails otherwise, something rewrote the file — most
    likely a text-mode checkout on Windows, which is why `.gitattributes` marks
    it `-text`.
    """
    bundle = VENDOR_DIR / "pixi.min.js"
    assert bundle.is_file(), f"vendored engine missing at {bundle}"
    assert bundle.stat().st_size == PIXI_BUNDLE_BYTES
    assert _sha256(bundle) == PIXI_BUNDLE_SHA256


def test_the_mit_notice_ships_with_the_bytes():
    """MIT requires the copyright line AND the permission notice to travel with the code.

    The minified bundle's own banner names the licence and links to it, but
    carries neither — so it does not discharge the obligation on its own.
    """
    lic = VENDOR_DIR / "pixi.LICENSE.txt"
    assert lic.is_file(), f"licence notice missing at {lic}"
    assert _sha256(lic) == PIXI_LICENSE_SHA256
    text = lic.read_text()
    assert "Copyright (c) 2013-2023 Mathew Groves, Chad Engler" in text
    assert "The above copyright notice and this permission notice" in text

    banner = (VENDOR_DIR / "pixi.min.js").read_text(errors="ignore")[:400]
    assert "Copyright" not in banner, (
        "the bundle banner now carries a copyright line — re-check whether the "
        "separate notice file is still required before removing it"
    )


def test_no_runtime_file_fetches_from_the_network():
    """The whole point. Every URL in the runtime directory must be relative."""
    offenders = []
    for p in sorted(RUNTIME_DIR.rglob("*")):
        if not p.is_file() or p.suffix not in {".html", ".js"}:
            continue
        if p.is_relative_to(VENDOR_DIR):
            continue  # third-party bundle; its internals are not ours to police
        for lineno, line in enumerate(p.read_text(errors="ignore").splitlines(), 1):
            if "src=" in line and ("http://" in line or "https://" in line):
                offenders.append(f"{p.relative_to(REPO_ROOT)}:{lineno}: {line.strip()}")
    assert not offenders, "runtime file loads a remote script:\n" + "\n".join(offenders)


def test_force_include_is_a_complete_inventory_of_the_runtime_assets():
    """`packages = ["an"]` is what actually ships these; this list documents them.

    It is kept complete on purpose: the previous partial version omitted
    `preview.html` and therefore read as though preview.html was excluded from
    the wheel, which is the opposite of the truth. A runtime asset added and
    forgotten here fails this test.
    """
    # Parsed by hand rather than with tomllib, which is 3.11+ while this package
    # supports 3.10 — and adding `tomli` for one test would put a dependency in
    # the wheel to check the wheel's own manifest.
    text = (REPO_ROOT / "pyproject.toml").read_text()
    block = re.search(
        r"^\[tool\.hatch\.build\.targets\.wheel\.force-include\]\n(.*?)(?=^\[|\Z)",
        text,
        re.S | re.M,
    )
    assert block, "force-include table not found in pyproject.toml"
    declared = set(re.findall(r'^"([^"]+)"\s*=', block.group(1), re.M))
    assert declared, "force-include table parsed as empty — the regex has drifted"
    on_disk = {
        # .as_posix(), not str(): on Windows str() yields backslashes while the
        # TOML keys are forward-slash paths, so this compared two spellings of
        # the same set and failed on that leg only.
        p.relative_to(REPO_ROOT).as_posix()
        for p in RUNTIME_DIR.rglob("*")
        if p.is_file() and p.name != "__init__.py" and "__pycache__" not in p.parts
    }
    assert on_disk == declared, (
        f"runtime assets not declared: {sorted(on_disk - declared)}; "
        f"declared but absent: {sorted(declared - on_disk)}"
    )


# --------------------------------------------------------------------------
# The one that matters: a real render, with the outside world switched off.
# --------------------------------------------------------------------------

def _chromium_available() -> bool:
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


@pytest.mark.skipif(not _chromium_available(), reason="needs playwright chromium")
def test_a_render_succeeds_with_the_outside_world_switched_off(
    hermetic_browser, tmp_path
):
    """Aborts every non-loopback request and renders anyway.

    Before the engine was vendored this failed with
    ``Page.wait_for_function: Timeout`` and a blocked list containing exactly the
    CDN URL. That is the regression this locks down, and no cheaper test can:
    a Python socket guard cannot see Chromium's fetches at all.
    """
    from an import init
    from an.ir.schema import AssetRef, Meta, Resolution, SceneIR, Shot
    from an.orchestrate import render_project
    from an.project import load

    root = init(tmp_path / "hermetic")
    proj = load(root)
    proj.scene = SceneIR(
        meta=Meta(
            title="hermetic",
            duration=0.25,
            fps=8,
            resolution=Resolution(width=160, height=120),
        ),
        timeline=[
            Shot(
                id="s1",
                style="cutout",
                duration=0.25,
                entities=[
                    AssetRef(
                        kind="character", id="charlie", store="characters", ref="c-v1"
                    )
                ],
            )
        ],
    )
    proj.mall["scenes"]["main"] = proj.scene

    output = render_project(root, output_name="hermetic")

    assert output.exists(), "render failed with the network off"
    assert hermetic_browser["blocked"] == [], (
        "the render still reached for the network: "
        f"{hermetic_browser['blocked']}"
    )
    assert any("vendor/pixi.min.js" in u for u in hermetic_browser["allowed"]), (
        "the page never requested the vendored engine — it may be loading from "
        f"somewhere else entirely. Requested: {hermetic_browser['allowed']}"
    )
