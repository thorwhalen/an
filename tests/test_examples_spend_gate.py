"""an#63 — an example must not bill whoever runs it because a key is exported.

``examples/character_gallery/build.py`` is the file with the most footfall in
this repo, and it used to choose ElevenLabs on key-presence alone:
``tts = "elevenlabs" if os.environ.get("ELEVEN_API_KEY") else "offline"``. A key
is exported by every developer shell that sources a profile and by every
unattended agent session started from one, so that gate was satisfied by exactly
the population that must not be billed silently. No money was spent finding it —
the repo's audio cache was warm — which is what makes it easy to miss: the
charge lands on a clean checkout, where every line of dialogue is new.

The rule these tests pin is the federation's (video_gen decision
D-vg-audio-02): **a path that can reach a paid API needs an explicit positive
opt-in env var in addition to the key.** Here that is
:data:`an.live_api.LIVE_API_ENV_VAR`.

The example is loaded from its path rather than imported: ``examples/`` is not a
package, and it is precisely the file a user runs directly.
"""

from __future__ import annotations

import ast
import importlib.util
import sys
from pathlib import Path

import pytest

from an.live_api import LIVE_API_ENV_VAR

ROOT = Path(__file__).resolve().parents[1]
GALLERY_BUILD = ROOT / "examples" / "character_gallery" / "build.py"

#: Every script a user or an agent is invited to run directly. A paid provider
#: named in one of these must be reachable only through the opt-in.
RUNNABLE_SCRIPTS: tuple[Path, ...] = tuple(
    sorted(ROOT.glob("examples/*/build.py"))
    + [ROOT / "misc" / "demos" / "build_demos.py"]
)

#: Provider names that cost money when they are chosen.
PAID_PROVIDERS: tuple[str, ...] = ("elevenlabs",)

_A_KEY = {"ELEVEN_API_KEY": "sk-not-a-real-key"}
_OPTED_IN = {LIVE_API_ENV_VAR: "1"}


@pytest.fixture(scope="module")
def gallery():
    """The example module, loaded by path and never executed."""
    spec = importlib.util.spec_from_file_location("_an_gallery_build", GALLERY_BUILD)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
        yield module
    finally:
        sys.modules.pop(spec.name, None)


def test_a_key_alone_does_not_choose_a_paid_provider(gallery):
    """MUTATION: `tts = "elevenlabs" if env.get("ELEVEN_API_KEY") else "offline"`.

    The literal defect an#63 reports, restated as an assertion.
    """
    provider, why = gallery._tts_choice(dict(_A_KEY))

    assert provider == "offline", "a key being present is not consent to spend"
    assert LIVE_API_ENV_VAR in why, "the reason must name the switch to flip"


def test_the_opt_in_plus_the_key_chooses_the_paid_provider(gallery):
    """MUTATION: return "offline" unconditionally.

    The gate has to stay a gate rather than become a refusal: an example that
    can never produce real speech would be honest and useless, and the next
    person to want speech would remove the gate.
    """
    provider, why = gallery._tts_choice({**_A_KEY, **_OPTED_IN})

    assert provider == "elevenlabs"
    assert "ELEVEN_API_KEY" in why


def test_the_opt_in_without_a_key_stays_offline(gallery):
    """Opting in is permission to spend, not a claim to have credentials."""
    provider, why = gallery._tts_choice(dict(_OPTED_IN))

    assert provider == "offline"
    assert "ELEVEN_API_KEY" in why


def test_ci_never_spends_however_the_environment_is_configured(gallery):
    """The `CI` half of the gate, which the example inherits from `an.live_api`."""
    provider, _ = gallery._tts_choice({**_A_KEY, **_OPTED_IN, "CI": "true"})

    assert provider == "offline"


def test_whisper_is_behind_the_same_switch(gallery):
    """MUTATION: choose whisper whenever `faster_whisper` imports.

    Whisper costs nothing, so this is not about money — its first run downloads
    model weights, which is the same class of thing an unattended run should not
    do because a package happened to be installed. One switch rather than a
    second env var: two answers to "may this run do something expensive" drift.
    """
    provider, why = gallery._lipsync_choice({})

    assert provider == "offline"
    assert LIVE_API_ENV_VAR in why


@pytest.mark.parametrize("script", RUNNABLE_SCRIPTS, ids=lambda p: p.name)
def test_no_runnable_script_names_a_paid_provider_without_the_gate(script):
    """The sibling builders, so the shape cannot come back in another file.

    MUTATION: delete the `live_api_enabled` call from the gallery builder.

    A script that mentions a paid provider at all must also mention the opt-in;
    one that mentions neither (``promote_demo``, which renders with the offline
    defaults) passes without having to say anything.
    """
    text = script.read_text(encoding="utf-8")
    # A provider is CHOSEN by passing its exact name, so the question is asked
    # of the parsed constants rather than of the characters. A demo caption that
    # explains `elevenlabs` in prose is documentation, not a code path, and a
    # substring search cannot tell the two apart — `misc/demos/build_demos.py`
    # is exactly that case and renders `tts="offline"` throughout.
    named = sorted(
        {
            node.value
            for node in ast.walk(ast.parse(text, filename=str(script)))
            if isinstance(node, ast.Constant) and node.value in PAID_PROVIDERS
        }
    )
    if not named:
        return
    assert LIVE_API_ENV_VAR in text or "live_api_enabled" in text, (
        f"{script.relative_to(ROOT)} can choose {named} but never consults the "
        f"{LIVE_API_ENV_VAR} opt-in — a key being present is not consent to spend"
    )
