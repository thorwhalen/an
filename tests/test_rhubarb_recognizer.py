"""The Rhubarb recognizer follows the language (an#96, epic #9 defect 5a).

These tests need NO rhubarb binary: `subprocess.run` is stubbed and the binary
path is injected. That is the point — `tests/test_rhubarb_lipsync.py` is
marker-skipped (a module-level `skipif`) wherever the binary is absent, so a guard that lived there would
run on no CI machine. Verified at Rhubarb's source: `PhoneticRecognizer.cpp`
`UNUSED(dialog)`; `PocketSphinxRecognizer.cpp` mixes `{defaultLM, dialogLM}` at
`{0.1, 0.9}`. Passing `--dialogFile` to `phonetic` therefore did nothing, and
the old default did exactly that for English.
"""

from __future__ import annotations

import json
import subprocess

import pytest

from an.audio import rhubarb_lipsync as rl
from an.audio.providers import make_lipsync
from an.audio.tts import AudioClip


def _stub_run(monkeypatch, *, cues=({"start": 0.0, "end": 0.4, "value": "A"},)):
    """Record the argv, what the temp dir held at call time, and write the
    JSON rhubarb would have written."""
    import os

    seen = {}

    def run(cmd, **kw):
        seen["argv"] = list(cmd)
        out = cmd[cmd.index("-o") + 1]
        seen["files"] = sorted(os.listdir(os.path.dirname(out)))
        with open(out, "w", encoding="utf-8") as f:
            json.dump({"mouthCues": list(cues)}, f)
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(rl.subprocess, "run", run)
    return seen


def _audio():
    return AudioClip(bytes_=b"RIFF" + b"\x00" * 64, duration=0.5, voice_id="v", transcript="hi")


def test_english_uses_pocketsphinx_and_passes_the_dialog_file(monkeypatch, tmp_path):
    seen = _stub_run(monkeypatch)
    ls = rl.RhubarbLipSync(binary_path="/bin/rhubarb")  # language defaults to "en"
    ls.align(_audio(), "hello there")
    argv = seen["argv"]
    assert argv[argv.index("-r") + 1] == "pocketSphinx"
    assert "--dialogFile" in argv
    dialog = argv[argv.index("--dialogFile") + 1]
    # The transcript was actually written where the argv points (inside the
    # temp dir, gone after align returns) — assert on the argv shape, and that
    # the file name is the one the provider writes.
    assert dialog.endswith("transcript.txt")


@pytest.mark.parametrize("language", ["fr", "de-DE", "ja"])
def test_non_english_uses_phonetic_and_writes_no_transcript(monkeypatch, language):
    """A transcript on disk that nothing reads is a lie for the next reader."""
    seen = _stub_run(monkeypatch)
    ls = rl.RhubarbLipSync(binary_path="/bin/rhubarb", language=language)
    ls.align(_audio(), "bonjour")
    argv = seen["argv"]
    assert argv[argv.index("-r") + 1] == "phonetic"
    assert "--dialogFile" not in argv
    # Not merely unpassed: never written (a surviving mutant wrote it anyway).
    assert "transcript.txt" not in seen["files"], seen["files"]


@pytest.mark.parametrize("language,expected", [("en_US", "pocketSphinx"), ("EN", "pocketSphinx"), ("en-", "pocketSphinx")])
def test_locale_spellings_normalise(language, expected):
    assert rl.recognizer_for(language) == expected


def test_an_empty_language_is_refused_not_read_as_non_english():
    with pytest.raises(ValueError, match="BCP-47"):
        rl.recognizer_for("")
    with pytest.raises(ValueError, match="BCP-47"):
        rl.RhubarbLipSync(binary_path="/bin/rhubarb", language="")


def test_an_explicit_recognizer_overrides_the_language_rule(monkeypatch):
    seen = _stub_run(monkeypatch)
    ls = rl.RhubarbLipSync(binary_path="/bin/rhubarb", language="fr", recognizer="pocketSphinx")
    ls.align(_audio(), "hello")
    assert seen["argv"][seen["argv"].index("-r") + 1] == "pocketSphinx"
    assert "--dialogFile" in seen["argv"]
    with pytest.raises(ValueError, match="recognizer"):
        rl.RhubarbLipSync(binary_path="/bin/rhubarb", recognizer="sphinx4")


def test_the_name_carries_the_recognizer_so_the_cache_key_moves():
    """The viseme cache key hashes `lipsync.name`; a fix that changes the
    recognizer must not replay the old recognizer's track."""
    en = rl.RhubarbLipSync(binary_path="/bin/rhubarb")
    fr = rl.RhubarbLipSync(binary_path="/bin/rhubarb", language="fr")
    assert en.name == "rhubarb:pocketSphinx" and fr.name == "rhubarb:phonetic"
    assert en.name != "rhubarb", "the pre-an#96 name would replay stale phonetic tracks"


def test_the_factory_carries_the_language(monkeypatch):
    monkeypatch.setattr(rl.shutil, "which", lambda name: "/bin/rhubarb")
    assert make_lipsync("rhubarb").recognizer == "pocketSphinx"
    assert make_lipsync("rhubarb", language="es").recognizer == "phonetic"
    # Providers that ignore the language still accept it.
    assert make_lipsync("offline", language="es").name == "offline"


def test_the_cli_carries_the_language_to_the_factory(monkeypatch):
    """`an render --language fr --lipsync rhubarb` must reach `make_lipsync(language=)`
    — the research promised the CLI half and the first draft delivered only
    the factory (an#96 review)."""
    from pathlib import Path

    from an import tools

    seen = {}

    def fake(project_dir, **kw):
        seen.update(kw)
        return Path("out.mp4")

    monkeypatch.setattr(tools, "_render_project", fake)
    tools.render("proj")
    assert seen["language"] == "en"
    tools.render("proj", language="fr")
    assert seen["language"] == "fr"


def test_render_resolves_a_named_lipsync_with_the_language(monkeypatch, tmp_path):
    """`render()` resolves a provider NAME through the factory with the language;
    an instance is passed through untouched."""
    from an import init
    from an import render as render_mod
    from an.audio import providers
    from an.ir.schema import Dialogue, Meta, SceneIR, Shot
    from an.project import load

    root = init(tmp_path / "p")
    proj = load(root)
    proj.scene = SceneIR(
        meta=Meta(title="t", duration=1.0, fps=12),
        timeline=[Shot(id="s", renderer="cutout", duration=1.0, dialogue=[Dialogue(speaker="c", text="hi")])],
    )
    proj.mall["scenes"]["main"] = proj.scene
    seen = {}

    class _Stop(Exception):
        pass

    def fake_make(name, *, language="en"):
        seen["args"] = (name, language)
        raise _Stop

    monkeypatch.setattr(providers, "make_lipsync", fake_make)
    with pytest.raises(_Stop):
        render_mod.render(proj, lipsync="rhubarb", language="de")
    assert seen["args"] == ("rhubarb", "de")


def test_whisper_does_not_select_anything_by_language(monkeypatch):
    """faster-whisper's word times come from DTW inside the one MIT model; the
    factory swallows `language` and the provider must not grow a per-language
    weight selection without the allowlist the `an-dev-lipsync` skill demands."""
    import inspect

    from an.audio import whisper_lipsync as wl
    from an.audio.providers import make_lipsync

    assert "language" not in inspect.signature(wl.WhisperLipSync.__init__).parameters
    # No `language=` is ever passed to the model and no per-language table exists.
    src = inspect.getsource(wl)
    assert "language=" not in src and "ALIGN_MODELS" not in src
    assert isinstance(make_lipsync("whisper", language="fr"), wl.WhisperLipSync)


def test_the_track_still_ends_on_rest_and_has_no_words(monkeypatch):
    _stub_run(monkeypatch)
    track = rl.RhubarbLipSync(binary_path="/bin/rhubarb").align(_audio(), "hi")
    assert [v.code for v in track.visemes] == ["A", "X"]
    assert track.words is None, "Rhubarb's JSON is mouth cues only"
