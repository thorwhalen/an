"""The Rhubarb recognizer follows the language (an#96, epic #9 defect 5a).

These tests need NO rhubarb binary: `subprocess.run` is stubbed and the binary
path is injected. That is the point — `tests/test_rhubarb_lipsync.py` is
module-skipped wherever the binary is absent, so a guard that lived there would
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
    """Record the argv and write the JSON rhubarb would have written."""
    seen = {}

    def run(cmd, **kw):
        seen["argv"] = list(cmd)
        out = cmd[cmd.index("-o") + 1]
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


def test_the_track_still_ends_on_rest_and_has_no_words(monkeypatch):
    _stub_run(monkeypatch)
    track = rl.RhubarbLipSync(binary_path="/bin/rhubarb").align(_audio(), "hi")
    assert [v.code for v in track.visemes] == ["A", "X"]
    assert track.words is None, "Rhubarb's JSON is mouth cues only"
