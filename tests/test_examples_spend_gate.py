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

from an.live_api import LIVE_API_ENV_VAR, live_api_enabled

ROOT = Path(__file__).resolve().parents[1]
GALLERY_BUILD = ROOT / "examples" / "character_gallery" / "build.py"

#: Where a script a user or an agent is invited to run directly can live.
#: Globbed for **every** `.py` rather than for `build.py`, because the previous
#: spelling (`examples/*/build.py` plus one hardcoded path) simply did not scan
#: an example named `run.py`, `make.py` or `demo.py` — a registry guard that
#: silently declines to look at a file is indistinguishable from one that
#: cleared it.
SCRIPT_GLOBS: tuple[str, ...] = ("examples/**/*.py", "misc/demos/**/*.py")


def _authored(path: Path) -> bool:
    """Whether a path is a hand-written script rather than a build product.

    ``examples/*/.an/render_work/.../runtime/__init__.py`` is emitted by a
    render, and every such directory is dot-prefixed — so the rule is "no
    dot-directory in the path", which needs no gitignore parsing and no
    allowlist to keep in sync.
    """
    return not any(part.startswith(".") for part in path.relative_to(ROOT).parts)


#: Every script a user or an agent is invited to run directly. A paid provider
#: named in one of these must be reachable only through the opt-in.
RUNNABLE_SCRIPTS: tuple[Path, ...] = tuple(
    sorted({p for glob in SCRIPT_GLOBS for p in ROOT.glob(glob) if _authored(p)})
)

#: Provider names that cost money when they are chosen.
PAID_PROVIDERS: tuple[str, ...] = ("elevenlabs",)

#: The gate a paid script has to CALL. Read off the function rather than
#: spelled, so renaming it cannot leave this guard quietly asserting a name
#: nothing uses any more.
GATE_FUNCTION: str = live_api_enabled.__name__

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


def _calls_the_gate(tree: ast.AST) -> bool:
    """Whether the parsed script actually CALLS the opt-in, not merely names it.

    The predicate this replaces was ``LIVE_API_ENV_VAR in text or
    "live_api_enabled" in text`` — a substring search over the whole file,
    docstrings included. ``examples/character_gallery/build.py`` documents its
    own usage as ``AN_LIVE_API_TESTS=1 ELEVEN_API_KEY=... python ...`` in its
    module docstring, and that one line satisfied the assertion on its own: the
    test's own declared mutation (delete the ``live_api_enabled`` call) left it
    GREEN. A new script could be added with the literal an#63 defect in it plus
    a reassuring sentence, and the suite would stay green — which is the one
    thing this test exists to stop.
    """
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        called = (
            func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
        )
        if called == GATE_FUNCTION:
            return True
    return False


def test_the_script_scan_reaches_the_scripts_it_is_supposed_to_guard():
    """MUTATION: narrow the globs back to `examples/*/build.py`.

    A registry guard is only worth its parametrisation if the registry is
    populated; an empty or truncated `RUNNABLE_SCRIPTS` is a green suite that
    checked nothing. The gallery is named explicitly because it is the file the
    whole issue is about, and the render products under `.an/` are named because
    excluding them is a deliberate rule rather than an accident of the glob.
    """
    assert GALLERY_BUILD in RUNNABLE_SCRIPTS
    assert ROOT / "misc" / "demos" / "build_demos.py" in RUNNABLE_SCRIPTS
    assert ROOT / "examples" / "promote_demo" / "build.py" in RUNNABLE_SCRIPTS
    assert not [p for p in RUNNABLE_SCRIPTS if ".an" in p.parts], (
        "render products are not scripts anybody runs"
    )


def ungated_paid_providers(text: str, path: Path) -> list[str]:
    """The paid providers a script can choose without CALLING the opt-in.

    ONE function, called by both the registry sweep below and the negative test
    after it — deliberately, because a negative test that pins only a private
    helper does not guard the sweep that is supposed to use it. Weaken this back
    to a substring search and BOTH go red; leave it correct but stop calling it
    from the sweep and the sweep's own parametrised cases go red instead. There
    is no arrangement of the two where the property quietly stops being checked.

    A provider is CHOSEN by passing its exact name, so the question is asked of
    the parsed constants rather than of the characters: a demo caption that
    explains `elevenlabs` in prose is documentation, not a code path, and
    `misc/demos/build_demos.py` is exactly that case while rendering
    `tts="offline"` throughout. The gate is asked of the call graph for the
    same reason — prose is not a code path in either direction.
    """
    tree = ast.parse(text, filename=str(path))
    named = sorted(
        {
            node.value
            for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and node.value in PAID_PROVIDERS
        }
    )
    if not named or _calls_the_gate(tree):
        return []
    return named


@pytest.mark.parametrize(
    "script", RUNNABLE_SCRIPTS, ids=lambda p: p.relative_to(ROOT).as_posix()
)
def test_no_runnable_script_names_a_paid_provider_without_the_gate(script):
    """The sibling builders, so the shape cannot come back in another file.

    MUTATION: delete the `live_api_enabled` call from the gallery builder.

    A script that mentions a paid provider at all must also CALL the opt-in;
    one that mentions neither (``promote_demo``, which renders with the offline
    defaults) passes without having to say anything.
    """
    ungated = ungated_paid_providers(script.read_text(encoding="utf-8"), script)

    assert ungated == [], (
        f"{script.relative_to(ROOT)} can choose {ungated} but never calls "
        f"{GATE_FUNCTION}() — a key being present is not consent to spend, and "
        "saying so in a docstring is not a gate"
    )


def test_mentioning_the_switch_in_prose_does_not_satisfy_the_gate():
    """MUTATION: assert on the file's TEXT rather than on its call graph.

    Built from the REAL gallery with every `live_api_enabled` call renamed away
    — the exact file the old predicate cleared, because the usage line in its
    module docstring satisfied `LIVE_API_ENV_VAR in text` on its own and the
    declared mutation "delete the gate call" left this suite green. Using the
    real file rather than a synthetic one keeps the fixture honest: it is
    vacuous for precisely the reason the original was.

    This matters most for a script that does not exist yet — the gallery's own
    per-function tests cover the gallery, and for a NEW builder the registry
    sweep is the only protection there is.
    """
    text = GALLERY_BUILD.read_text(encoding="utf-8")
    sabotaged = text.replace(GATE_FUNCTION, "_gate_removed_by_this_test")

    assert LIVE_API_ENV_VAR in sabotaged, (
        "the fixture must be one the OLD text predicate would have cleared, or "
        "it proves nothing about the change"
    )
    assert ungated_paid_providers(sabotaged, GALLERY_BUILD) == ["elevenlabs"], (
        "a file with no CALL to the predicate must read as ungated, however "
        "often its docstring names the switch"
    )


def test_a_scaffolded_project_does_not_default_to_a_paid_provider(tmp_path):
    """MUTATION: put `tts = "elevenlabs"` back in `_ANIMA_TOML_TEMPLATE`.

    `an init` writes an `an.toml` that nothing parses — `an.project.load` never
    opens it and the package imports no TOML reader — so today this cannot
    spend. It was still the only place in the repo naming a paid provider as a
    project DEFAULT, in the file a user opens to learn what the defaults are.
    Wiring config reading is the natural next step for a file that exists and is
    documented, and on that day every project ever created by `an init` would
    default to a billed provider, on a path `an.live_api` never sees.

    Asserted over the file a real `init` produces rather than over the template
    string, so a second scaffolding route cannot reintroduce it unnoticed.
    """
    from an.project import init

    root = init(tmp_path / "scaffolded", name="scaffolded")
    config = (root / "an.toml").read_text(encoding="utf-8")

    for provider in PAID_PROVIDERS:
        assert provider not in config, (
            f"`an init` scaffolds {provider} as a project default; a config a "
            "user reads to learn the defaults must not state one that bills"
        )
