"""Tests for the an.characters package.

The doctests in each module cover most of the unit-level surface; these
pytest tests cover cross-module integration and disk-io paths.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from an.characters import (
    CharacterDescriptor,
    MOUTH_SHAPES,
    REQUIRED_PARTS,
    blink_animation,
    breath_animation,
    extract_part,
    extract_pivots,
    fetch_dicebear,
    generate_default_mouths,
    new_character,
    normalize_svg,
    promote,
    validate_character,
    write_default_mouths,
)
from an.characters.dicebear import wrap_dicebear_for_an
from an.characters.idle import (
    DEFAULT_BREATH_AMPLITUDE_PX,
    DEFAULT_BREATH_PERIOD_S,
    evaluate_track,
    random_blink_schedule,
)
from an.characters.svg_utils import promote_inkscape_labels_to_ids
from xml.etree import ElementTree as ET


# -----------------------------------------------------------------------------
# Schema
# -----------------------------------------------------------------------------


class TestSchema:
    def test_default_character_has_full_mouth_set(self):
        c = CharacterDescriptor(name="x")
        mouth_attachments = c.skins["default"].slots["mouth"]
        assert sorted(mouth_attachments) == [f"mouth_{s}" for s in sorted(MOUTH_SHAPES)]

    def test_viseme_map_uppercase_keys(self):
        c = CharacterDescriptor(name="x")
        for letter in ("A", "B", "C", "D", "E", "F", "G", "H", "X"):
            assert letter in c.viseme_map
            assert c.viseme_map[letter] == f"mouth_{letter.lower()}"

    def test_default_animations_present(self):
        c = CharacterDescriptor(name="x")
        assert "idle_breath" in c.animations
        assert "blink" in c.animations

    def test_round_trip(self):
        c = CharacterDescriptor(name="maya", display_name="Maya")
        raw = c.model_dump_json()
        back = CharacterDescriptor.model_validate_json(raw)
        assert back.name == "maya"
        assert back.display_name == "Maya"
        # Mouth attachments survive round-trip
        assert set(back.skins["default"].slots["mouth"]) == {
            f"mouth_{s}" for s in MOUTH_SHAPES
        }

    def test_track_target_validation(self):
        from an.characters.schema import AnimationTrack

        with pytest.raises(ValueError):
            AnimationTrack(target="nope:foo")
        AnimationTrack(target="bone:torso.y")
        AnimationTrack(target="slot:eye_l.attachment")

    def test_extra_fields_allowed(self):
        # Schema-evol pillar: unknown fields don't break parsing.
        raw = {
            "kind": "CharacterDescriptor",
            "name": "x",
            "future_field": {"some": "value"},
        }
        c = CharacterDescriptor.model_validate(raw)
        assert c.name == "x"


# -----------------------------------------------------------------------------
# SVG utilities
# -----------------------------------------------------------------------------


class TestSvgUtils:
    SAMPLE = """<svg xmlns="http://www.w3.org/2000/svg"
                 xmlns:inkscape="http://www.inkscape.org/namespaces/inkscape"
                 viewBox="0 0 100 100">
      <g id="skeleton">
        <circle id="neck" cx="50" cy="40" r="2"/>
        <circle id="shoulder_l" cx="35" cy="50" r="2"/>
      </g>
      <g id="illustration">
        <g id="head"><circle cx="50" cy="40" r="20"/></g>
        <g inkscape:label="Left arm" id=""><rect x="10" y="40" width="10" height="20"/></g>
      </g>
    </svg>"""

    def test_normalize_promotes_labels(self):
        import io

        tree = normalize_svg(io.StringIO(self.SAMPLE))
        root = tree.getroot()
        # The labelled group with empty id should now have id="Left_arm"
        ids = {g.get("id") for g in root.iter("{http://www.w3.org/2000/svg}g")}
        assert "Left_arm" in ids

    def test_extract_pivots(self):
        import io

        pivots = extract_pivots(io.StringIO(self.SAMPLE))
        assert pivots == {"neck": (50.0, 40.0), "shoulder_l": (35.0, 50.0)}

    def test_extract_part(self):
        import io

        tree = normalize_svg(io.StringIO(self.SAMPLE))
        part = extract_part(tree, "head")
        out = ET.tostring(part.getroot()).decode()
        assert "head" in out
        assert "shoulder_l" not in out

    def test_extract_missing_raises(self):
        import io

        with pytest.raises(KeyError):
            extract_part(io.StringIO(self.SAMPLE), "no_such_part")


# -----------------------------------------------------------------------------
# Mouth-set generator
# -----------------------------------------------------------------------------


class TestMouthSet:
    def test_all_nine_shapes_emitted(self):
        svgs = generate_default_mouths()
        assert sorted(svgs) == [f"mouth_{s}" for s in sorted(MOUTH_SHAPES)]

    def test_each_svg_has_viewbox(self):
        for name, svg in generate_default_mouths().items():
            assert "viewBox" in svg, f"{name} missing viewBox"
            assert "<svg" in svg

    def test_palette_override(self):
        svgs = generate_default_mouths(palette={"lip": "#ff0000"})
        # The lip color appears as a stroke on the path
        assert "#ff0000" in svgs["mouth_a"]

    def test_write_to_disk(self, tmp_path):
        paths = write_default_mouths(tmp_path)
        assert len(paths) == 9
        for p in paths:
            assert p.exists() and p.stat().st_size > 0


# -----------------------------------------------------------------------------
# Idle animations
# -----------------------------------------------------------------------------


class TestIdle:
    def test_breath_defaults(self):
        a = breath_animation()
        assert a.name == "idle_breath"
        assert a.loop is True
        targets = [t.target for t in a.tracks]
        assert "bone:torso.y" in targets
        assert "bone:head.rotation_deg" in targets
        # weight-shift is on by default
        assert "bone:torso.x" in targets

    def test_breath_evaluation_zero_at_t0(self):
        a = breath_animation()
        torso_y = next(t for t in a.tracks if t.target == "bone:torso.y")
        assert abs(evaluate_track(torso_y, 0.0, a.duration)) < 1e-9

    def test_breath_evaluation_peak(self):
        a = breath_animation()
        torso_y = next(t for t in a.tracks if t.target == "bone:torso.y")
        # quarter-cycle should be the peak amplitude
        v = evaluate_track(torso_y, a.duration / 4.0, a.duration)
        assert abs(v - DEFAULT_BREATH_AMPLITUDE_PX) < 1e-6

    def test_breath_period_default(self):
        a = breath_animation()
        # Both breath and weight-shift periods influence duration; the
        # weight-shift is longer.
        assert a.duration >= DEFAULT_BREATH_PERIOD_S

    def test_blink_step_frames(self):
        a = blink_animation()
        assert len(a.tracks) == 2
        # Step animation: at t=0 eyes are open
        for tr in a.tracks:
            assert "open" in str(tr.frames[0][1])
            # somewhere in the middle the eye is closed
            assert any("closed" in str(v) for _, v in tr.frames)

    def test_random_blink_schedule_in_range(self):
        ts = random_blink_schedule(20.0, seed=42)
        assert all(0 <= t < 20 for t in ts)
        assert ts == sorted(ts)


# -----------------------------------------------------------------------------
# DiceBear wrapper (offline-safe parts)
# -----------------------------------------------------------------------------


class TestDicebearWrap:
    def test_wrap_produces_skeleton_and_illustration(self):
        wrapped = wrap_dicebear_for_an(
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 80 80">'
            '<circle cx="40" cy="40" r="35"/></svg>',
            name="x",
        )
        assert 'id="skeleton"' in wrapped
        assert 'id="illustration"' in wrapped
        for part in ("head", "torso", "arm_l", "arm_r", "leg_l", "leg_r"):
            assert f'id="{part}"' in wrapped

    def test_fetch_dicebear_is_a_safe_function_call(self, monkeypatch):
        # Don't actually hit the network in unit tests.
        called = {}

        def fake_urlopen(url, timeout=10.0):
            called["url"] = url

            class _Resp:
                def __enter__(self):
                    return self

                def __exit__(self, *a):
                    return False

                def read(self):
                    return b'<svg xmlns="http://www.w3.org/2000/svg"></svg>'

            return _Resp()

        monkeypatch.setattr(
            "urllib.request.urlopen", fake_urlopen
        )
        out = fetch_dicebear("seed-x", style="adventurer")
        assert "<svg" in out
        assert "seed=seed-x" in called["url"]
        assert "9.x/adventurer/svg" in called["url"]


# -----------------------------------------------------------------------------
# Factory: end-to-end character creation
# -----------------------------------------------------------------------------


class TestNewCharacter:
    def test_offline_creates_complete_character(self, tmp_path):
        desc = new_character(tmp_path, name="maya", use_dicebear=False)
        assert desc.exists()
        # Required parts present
        parts_dir = tmp_path / "maya" / "parts"
        for part in REQUIRED_PARTS:
            assert (parts_dir / f"{part}.svg").exists(), f"missing {part}"
        # All 9 mouths present
        for shape in MOUTH_SHAPES:
            assert (parts_dir / "mouth" / f"mouth_{shape}.svg").exists()

    def test_validate_passes_after_new(self, tmp_path):
        new_character(tmp_path, name="maya", use_dicebear=False)
        report = validate_character(tmp_path / "maya")
        assert report.passed, report.format()
        assert not report.missing_parts
        assert not report.missing_mouths

    def test_overwrite_required_when_exists(self, tmp_path):
        new_character(tmp_path, name="bob", use_dicebear=False)
        with pytest.raises(FileExistsError):
            new_character(tmp_path, name="bob", use_dicebear=False)
        # Succeeds with overwrite=True
        new_character(tmp_path, name="bob", use_dicebear=False, overwrite=True)


# -----------------------------------------------------------------------------
# Promote
# -----------------------------------------------------------------------------


class TestPromote:
    def test_promote_falls_back_to_new(self, tmp_path):
        # No source SVG → promote should still produce a working character
        # (it falls back to new_character).
        #
        # `use_dicebear=False` is load-bearing, not tidiness: the fallback
        # otherwise calls the DiceBear API, and `new_character` catches the
        # failure and generates geometry anyway — so this test passed
        # identically whether or not the network was there, while quietly
        # depending on it. The offline guard in conftest.py is what surfaced it.
        chars = tmp_path / "assets" / "characters"
        chars.mkdir(parents=True)
        desc = promote(tmp_path, entity="amy", as_="amy-v1", use_dicebear=False)
        assert desc.exists()
        report = validate_character(chars / "amy-v1")
        assert report.passed, report.format()

    def test_promote_uses_existing_svg_dummy(self, tmp_path):
        # placeholder so the next method's name doesn't collide.
        pass


# -----------------------------------------------------------------------------
# Recording (Playwright + ffmpeg required; skip cleanly if missing)
# -----------------------------------------------------------------------------


class TestRecord:
    def test_record_character_produces_mp4(self, tmp_path):
        try:
            from playwright.sync_api import sync_playwright  # noqa: F401
        except ImportError:
            pytest.skip("playwright not installed")
        import shutil

        if shutil.which("ffmpeg") is None:
            pytest.skip("ffmpeg not on PATH")

        from an.characters.record import record_character

        new_character(tmp_path, name="rex", use_dicebear=False)
        # Short recording so the test is fast (~3 s).
        out = record_character(
            tmp_path / "rex",
            duration_s=2.0,
            size=(320, 240),
            out_mp4=tmp_path / "rex.mp4",
        )
        assert out.exists()
        assert out.stat().st_size > 1024, "mp4 should be at least 1 KB"


class TestParallelRender:
    """Phase 11c: per-shot concurrency."""

    def test_resolve_serial_default(self):
        from an.render import _resolve_parallel

        assert _resolve_parallel(None, n_shots=4) == 1
        assert _resolve_parallel(1, n_shots=4) == 1
        assert _resolve_parallel("", n_shots=4) == 1

    def test_resolve_explicit_n(self):
        from an.render import _resolve_parallel

        assert _resolve_parallel(3, n_shots=10) == 3
        assert _resolve_parallel("3", n_shots=10) == 3
        # n_shots clamps the upper bound
        assert _resolve_parallel(10, n_shots=2) == 2

    def test_resolve_auto(self):
        from an.render import _resolve_parallel, DEFAULT_PARALLEL_CAP

        # auto = min(n_shots, cpu, cap)
        assert _resolve_parallel("auto", n_shots=1) == 1
        result = _resolve_parallel("auto", n_shots=100)
        assert 1 <= result <= DEFAULT_PARALLEL_CAP

    def test_resolve_invalid(self):
        from an.render import _resolve_parallel

        # garbage → falls back to serial
        assert _resolve_parallel("garbage", n_shots=4) == 1


class TestSvgCharacterCompile:
    """Phase 11b: cutout compiler emits svg_sprite visuals + asset table."""

    def test_descriptor_drives_svg_rig(self, tmp_path):
        from an.adapters.cutout.compile import compile_shot
        from an.ir.schema import AssetRef, Shot
        from an.stores.characters import CharactersStore

        # Build a real character on disk.
        new_character(tmp_path, name="maya", use_dicebear=False)
        store = CharactersStore(tmp_path)
        assert "maya" in store, "store should see character.json"

        shot = Shot(
            id="s1",
            style="cutout",
            duration=2.0,
            entities=[AssetRef(id="maya", kind="character", store="characters", ref="maya")],
        )
        scene = compile_shot(shot, mall={"characters": store})

        # Asset table should contain the head, mouth-X, etc.
        assert "maya.head" in scene.assets.textures
        assert "maya.mouth_x" in scene.assets.textures
        # Texture src is relative to the runtime root.
        assert scene.assets.textures["maya.head"].src == "characters/maya/parts/head.svg"

        # Scene tree contains svg_sprite visuals.
        maya_node = scene.scene.children[0]
        assert maya_node.name == "maya"
        kinds = {c.name: (c.visual.kind if c.visual else None) for c in maya_node.children}
        assert kinds["head"] == "svg_sprite"
        assert kinds["torso"] == "svg_sprite"
        # Head has children: eyes/brows/mouth.
        head = next(c for c in maya_node.children if c.name == "head")
        head_kinds = {c.name: (c.visual.kind if c.visual else None) for c in head.children}
        assert head_kinds["mouth"] == "svg_sprite"
        # Mouth visual carries the viseme map.
        mouth = next(c for c in head.children if c.name == "mouth")
        assert mouth.visual.viseme_assets is not None
        assert mouth.visual.viseme_assets["A"] == "maya.mouth_a"
        assert mouth.visual.viseme_assets["X"] == "maya.mouth_x"

    def test_no_descriptor_falls_back_to_procedural(self):
        """Existing scenes with no character.json keep the procedural rig."""
        from an.adapters.cutout.compile import compile_shot
        from an.ir.schema import AssetRef, Shot

        shot = Shot(
            id="s1",
            style="cutout",
            duration=2.0,
            entities=[AssetRef(id="bob", kind="character", store="characters", ref="bob")],
        )
        scene = compile_shot(shot, mall={"characters": {}})
        # No textures registered — procedural rig is texture-free.
        assert scene.assets.textures == {}
        bob = scene.scene.children[0]
        head = next(c for c in bob.children if c.name == "head")
        # Old kind=ellipse is preserved (no regression).
        assert head.visual.kind == "ellipse"


class TestPromoteSecond:
    def test_promote_uses_existing_svg(self, tmp_path):
        chars = tmp_path / "assets" / "characters"
        chars.mkdir(parents=True)
        # Pre-place a minimal source SVG with only torso + head
        svg = (
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1024 1024">'
            '<g id="skeleton"><circle id="neck" cx="512" cy="400" r="3"/></g>'
            '<g id="illustration">'
            '<g id="head"><circle cx="512" cy="400" r="100"/></g>'
            '<g id="torso"><rect x="412" y="500" width="200" height="200"/></g>'
            "</g></svg>"
        )
        (chars / "raw").mkdir()
        (chars / "raw" / "raw.svg").write_text(svg, encoding="utf-8")
        desc = promote(tmp_path, entity="raw", as_="raw-v1")
        assert desc.exists()
        # Sliced parts inherit the source's geometry
        parts = chars / "raw-v1" / "parts"
        assert (parts / "head.svg").exists()
        assert (parts / "torso.svg").exists()
        # Eyes should be synthesized since the source didn't have them
        assert (parts / "eye_l_open.svg").exists()
        # Mouth set is the offline fallback
        for shape in MOUTH_SHAPES:
            assert (parts / "mouth" / f"mouth_{shape}.svg").exists()
