"""check_requirements behaves whether deps are present or missing."""

from __future__ import annotations

from an.check_requirements import (
    check_requirements,
    format_report,
)


def test_returns_dict_with_known_tools():
    report = check_requirements()
    expected = {"ffmpeg", "node", "rhubarb", "playwright", "elevenlabs", "manim"}
    assert expected.issubset(set(report.keys()))


def test_each_entry_has_required_fields():
    report = check_requirements()
    for name, info in report.items():
        assert "installed" in info
        assert isinstance(info["installed"], bool)
        if not info["installed"]:
            # Missing tools should always offer guidance.
            assert info.get("install_hint"), f"{name} missing install_hint"


def test_format_report_produces_string():
    report = check_requirements()
    pretty = format_report(report)
    assert isinstance(pretty, str) and pretty
    # First word per line should be a status marker.
    for line in pretty.splitlines():
        if line.strip().startswith(("[OK]", "[--]")):
            assert " " in line


def test_does_not_raise_on_missing_tools():
    # Whatever environment we run in, this should never raise.
    check_requirements()
