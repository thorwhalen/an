"""Schema and semantic validators."""

from __future__ import annotations

from an.ir.schema import Dialogue, Meta, SceneIR, Shot
from an.ir.validate import validate_schema, validate_semantic


def test_schema_validation_clean_doc():
    scene = SceneIR(
        meta=Meta(title="x", duration=3.0),
        timeline=[Shot(id="s1", duration=3.0)],
    )
    report = validate_schema(scene)
    assert report.passed
    assert report.findings == []


def test_schema_validation_catches_bad_types():
    bad = {
        "meta": {"title": "x"},
        "timeline": [{"id": "s1", "duration": "not-a-number"}],
    }
    report = validate_schema(bad)
    assert not report.passed
    assert any("duration" in f.ir_path for f in report.findings)


def test_semantic_flags_duplicate_shot_ids():
    scene = SceneIR(
        timeline=[
            Shot(id="dup", duration=1.0),
            Shot(id="dup", duration=1.0),
        ]
    )
    report = validate_semantic(scene)
    assert not report.passed
    assert any("duplicate shot id" in f.description for f in report.findings)


def test_semantic_flags_zero_duration():
    scene = SceneIR(timeline=[Shot(id="s1", duration=0.0)])
    report = validate_semantic(scene)
    assert not report.passed


def test_semantic_warns_on_missing_voice():
    scene = SceneIR(
        timeline=[
            Shot(
                id="s1",
                duration=2.0,
                dialogue=[Dialogue(speaker="x", text="hi", voice_ref="missing")],
            )
        ]
    )
    report = validate_semantic(scene, available_voices={})
    # voice missing is a warning, not error
    assert report.passed
    assert any("voice" in f.description for f in report.findings)


def test_semantic_errors_on_missing_speaker():
    scene = SceneIR(
        timeline=[
            Shot(
                id="s1",
                duration=2.0,
                dialogue=[Dialogue(speaker="", text="hi")],
            )
        ]
    )
    report = validate_semantic(scene)
    assert not report.passed
