"""Where third-party art came from, and what it obliges (#14).

`an` composes work it did not create into a video its user ships. A licence
defect is the only failure in this package that reaches *backwards* through
completed work — a video shipped with an unattributed CC BY asset cannot be
un-shipped, whereas every rendering bug can be fixed forward.

Before this there was nowhere to record any of it: the descriptor's `metadata`
dict carried a comment saying it *could* hold a licence, and nothing ever did.
Meanwhile the default avatar style was CC BY 4.0, so every character created with
stock flags carried an undischarged obligation.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from an.characters.dicebear import DICEBEAR_DEFAULT_STYLE, DICEBEAR_STYLES
from an.characters.licenses import (
    DICEBEAR_STYLE_LICENSES,
    NO_ATTRIBUTION_REQUIRED,
    attribution_for,
    dicebear_source,
    requires_acknowledgement,
)
from an.credits import collect_credits
from an.ir.assets import AssetSource, requires_attribution


# ------------------------------------------------------- the shared vocabulary


def test_rights_field_names_match_illustration_exactly():
    """No rename table at the boundary — a rename table is where a field
    quietly stops being carried.

    `illustration` is the federation's image-retrieval package and its results
    are the most likely thing to become an `AssetSource`. Its own persistence
    layer already drops these fields (filed as illustration#14), which is
    precisely the failure this pinning is meant to prevent here.

    Pinned by literal rather than by import: `an` must not depend on
    `illustration`. That is the shape `artful` already uses for vocabulary shared
    across packages that must not depend on each other.
    """
    shared = {
        "provider", "id", "url", "license", "license_url", "attribution",
        "source_page_url", "author", "author_url", "cacheable",
    }
    missing = sorted(shared - set(AssetSource.model_fields))
    assert not missing, f"AssetSource is missing shared rights fields: {missing}"


def test_unknown_cost_is_none_not_zero():
    """`None` means unknown; `0.0` means we checked and it was free.

    Collapsing them is the federation's named cost-honesty failure: a
    `0.0`-because-unknown reads as "free" to everything downstream.
    """
    assert AssetSource(provider="p").cost_usd is None
    assert dicebear_source("lorelei", seed="x").cost_usd == 0.0


@pytest.mark.parametrize(
    "license,expected",
    [
        ("cc0-1.0", False), ("CC0-1.0", False), ("mit", False),
        ("cc-by-4.0", True), ("CC-BY-4.0", True), ("by-sa", True),
        (None, None), ("some-bespoke-thing", None),
    ],
)
def test_attribution_classification_distinguishes_unknown_from_no(license, expected):
    """Three-valued on purpose. "We don't know" is not "nothing owed"."""
    assert requires_attribution(AssetSource(provider="p", license=license)) is expected


# ----------------------------------------------------------- the licence table


#: Snapshot of what upstream actually publishes at the pinned major.
#:
#: External ground truth, and that is the entire point. The first version of the
#: test below compared `DICEBEAR_STYLES` against `DICEBEAR_STYLE_LICENSES` —
#: two hand-maintained lists, checked against each other — so a style missing
#: from BOTH was invisible to it. Four were: `dylan` and `toon-head` (both
#: CC BY 4.0, i.e. attribution-bearing), `glass` and `rings`. A guard whose
#: reference is another copy of the thing it guards can only confirm that
#: someone copied one list into the other.
_UPSTREAM_SNAPSHOT = Path(__file__).resolve().parents[1] / "an/data/dicebear_9x_styles.json"


def _upstream_styles() -> set[str]:
    import json

    return set(json.loads(_UPSTREAM_SNAPSHOT.read_text(encoding="utf-8"))["styles"])


def test_every_style_upstream_publishes_is_requestable_and_licensed():
    """Checked against upstream, not against ourselves.

    The DiceBear *software* licence (MIT) is a separate fact from each *style*
    licence — DiceBear splits them itself under `# Design` / `# Code` headings.
    Reading the top-level MIT and concluding the avatars are MIT is the trap
    this table exists to close, and it only closes it if the table is complete.
    """
    upstream = _upstream_styles()
    unlicensed = sorted(upstream - set(DICEBEAR_STYLE_LICENSES))
    assert not unlicensed, (
        f"upstream publishes {unlicensed} at the pinned major with no licence "
        "row — a style whose rights are unknown is a trap, and CC BY ones put a "
        "duty on the user"
    )
    unrequestable = sorted(upstream - set(DICEBEAR_STYLES))
    assert not unrequestable, (
        f"upstream publishes {unrequestable} but DICEBEAR_STYLES omits them"
    )


def test_the_table_claims_nothing_upstream_does_not_publish():
    """The other direction: a row for a style that does not exist is a lie too."""
    extra = sorted(set(DICEBEAR_STYLE_LICENSES) - _upstream_styles())
    assert not extra, f"licence rows for styles upstream does not publish: {extra}"


@pytest.mark.skipif(
    __import__("os").environ.get("CI") is not None, reason="no network in CI"
)
@pytest.mark.live
def test_the_upstream_snapshot_is_still_current():
    """Opt-in, networked: has upstream added a style since the snapshot?

    The snapshot is what makes the offline test honest; this is what stops the
    snapshot itself going stale. Marked `live` so the hermetic suite never runs
    it — a snapshot that silently drifts is the same defect one level up.
    """
    import json
    import urllib.request

    meta = json.loads(_UPSTREAM_SNAPSHOT.read_text(encoding="utf-8"))
    with urllib.request.urlopen(meta["source"], timeout=30) as r:
        data = json.load(r)
    live = {
        d["name"] for d in data
        if d["type"] == "dir" and d["name"] not in ("collection", "converter", "core")
    }
    new = sorted(live - set(meta["styles"]))
    assert not new, (
        f"upstream added {new} since {meta['captured']}. Read each one's LICENSE "
        "at the pinned major, add a row, and refresh the snapshot."
    )


def test_the_default_style_puts_no_obligation_on_the_user():
    """The whole point of moving it.

    The previous default was CC BY 4.0, so `an character new <name>` with no
    flags produced art whose licence obliged the *user* to credit an artist they
    had never heard of — recorded nowhere and displayed nowhere.
    """
    lic = DICEBEAR_STYLE_LICENSES[DICEBEAR_DEFAULT_STYLE]
    assert lic.license in NO_ATTRIBUTION_REQUIRED, (
        f"the default style {DICEBEAR_DEFAULT_STYLE!r} is {lic.license!r}, which "
        "obliges every user who renders with stock settings"
    )
    assert attribution_for(DICEBEAR_DEFAULT_STYLE) is None
    assert not requires_acknowledgement(DICEBEAR_DEFAULT_STYLE)


def test_an_unlisted_style_counts_as_requiring_acknowledgement():
    """An unverified licence is a refusal, not a warning."""
    assert requires_acknowledgement("some-style-nobody-checked")


def test_building_a_source_for_an_unlisted_style_raises():
    """Rather than returning an empty record that reads as "nothing owed"."""
    with pytest.raises(ValueError, match="no verified licence"):
        dicebear_source("some-style-nobody-checked", seed="x")


def test_the_attribution_text_is_dicebear_s_own_wording():
    """Including the remix half, which is what discharges CC BY's "indicate if
    changes were made" — `an` genuinely modifies what it ingests by wrapping the
    avatar into a rig."""
    text = attribution_for("adventurer")
    assert "Lisa Wischofsky" in text
    assert "CC BY 4.0" in text
    assert "Remix of the original" in text


# --------------------------------------------------------------- the gate


def test_a_cc_by_style_is_refused_without_an_explicit_acknowledgement(tmp_path):
    """Not paternalism: the duty falls on whoever ships the video, and making it
    an explicit flag is the difference between an informed choice and an
    unknowing violation."""
    from an.characters.factory import new_character

    with pytest.raises(ValueError, match="obliges"):
        new_character(tmp_path / "c", name="amy", style="adventurer")


def test_acknowledging_lets_it_through_and_the_message_says_what_is_owed(tmp_path):
    from an.characters.factory import new_character

    try:
        new_character(tmp_path / "c", name="amy", style="adventurer")
    except ValueError as e:
        assert "Remix of the original" in str(e), (
            "the refusal must show the exact text the user would owe, not just "
            "that something is owed"
        )
    # and with the flag, creation proceeds (offline art, no network)
    new_character(tmp_path / "d", name="amy", style="adventurer",
                  use_dicebear=False, acknowledge_attribution=True)


# ------------------------------------------------------------------- credits


def _descriptor_with(source: AssetSource):
    from an.characters.schema import CharacterDescriptor

    return CharacterDescriptor(name="x", source=source)


def test_credits_separates_owed_from_unverified_from_clear():
    """Three lists, never two.

    Folding "unverified" into "owed" cries wolf; folding it into "nothing owed"
    hides a real obligation. Neither is honest.
    """
    mall = {
        "characters": {
            "clear": _descriptor_with(dicebear_source("lorelei", seed="a")),
            "owed": _descriptor_with(dicebear_source("adventurer", seed="b")),
            "unknown": _descriptor_with(AssetSource(provider="p", license="mystery")),
        }
    }
    report = collect_credits(mall)
    assert [e.asset for e in report.owed] == ["characters/owed"]
    assert [e.asset for e in report.unverified] == ["characters/unknown"]
    assert len(report.entries) == 3


def test_the_credits_text_displays_the_attribution_verbatim():
    """A licence recorded and never displayed is not compliance."""
    mall = {"characters": {"bo": _descriptor_with(dicebear_source("adventurer", seed="b"))}}
    text = collect_credits(mall).format()
    assert "MUST BE DISPLAYED" in text
    assert "Lisa Wischofsky" in text


def test_credits_says_unknown_rather_than_implying_nothing_is_owed():
    mall = {"characters": {"x": _descriptor_with(AssetSource(provider="p"))}}
    text = collect_credits(mall).format()
    assert "UNVERIFIED" in text
    assert "not the same as unencumbered" in text


def test_a_project_with_no_third_party_art_says_so_plainly():
    assert "no third-party assets" in collect_credits({"characters": {}}).format()


def test_credits_is_a_registered_cli_command():
    """A provenance field with no consumer is decoration."""
    from an.tools import _dispatch_funcs

    assert any(f.__name__ == "credits" for f in _dispatch_funcs)


# ------------------- the producer, which had no coverage at all

@pytest.fixture
def stub_dicebear(monkeypatch):
    """Serve a real DiceBear fetch from memory, so the whole path runs offline."""

    def fake_urlopen(url, timeout=10.0):
        class _Resp:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self):
                return (
                    b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">'
                    b'<circle cx="50" cy="50" r="40" fill="#cfd"/></svg>'
                )

        return _Resp()

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)


def test_creating_a_character_actually_records_its_provenance(tmp_path, stub_dicebear):
    """The behaviour this whole change exists to add, and it had NO test.

    Every other test here hand-builds `CharacterDescriptor(name=..., source=...)`,
    so deleting `source=source` from `new_character` left the full suite green —
    the vocabulary was asserted and the producer was not. That is the same shape
    as the previous PR's vacuous tests, one layer over.

    This walks the real path: create → write to disk → read back → credits.
    """
    from an.characters.factory import new_character
    from an.characters.schema import CharacterDescriptor

    desc_path = new_character(tmp_path, name="maya", style="lorelei")
    on_disk = CharacterDescriptor.model_validate_json(desc_path.read_text(encoding="utf-8"))

    assert on_disk.source is not None, (
        "a character built from third-party art carries no provenance record"
    )
    assert on_disk.source.license == "cc0-1.0"
    assert on_disk.source.provider == "dicebear"
    assert on_disk.source.id == "lorelei/maya"


def test_an_acknowledged_cc_by_character_carries_the_attribution_it_owes(
    tmp_path, stub_dicebear
):
    from an.characters.factory import new_character
    from an.characters.schema import CharacterDescriptor

    desc_path = new_character(
        tmp_path, name="bo", style="adventurer", acknowledge_attribution=True
    )
    src = CharacterDescriptor.model_validate_json(desc_path.read_text(encoding="utf-8")).source
    assert src.license == "cc-by-4.0"
    assert "Lisa Wischofsky" in src.attribution, (
        "acknowledging the duty must RECORD what is owed, not merely permit it"
    )


def test_offline_art_records_nothing_because_nothing_is_owed(tmp_path):
    """The one case where "no third-party assets" is the true answer."""
    from an.characters.factory import new_character
    from an.characters.schema import CharacterDescriptor

    desc_path = new_character(tmp_path, name="amy", use_dicebear=False)
    assert CharacterDescriptor.model_validate_json(desc_path.read_text(encoding="utf-8")).source is None


# --------------- the legacy projects, which were told they owe nothing

def test_a_character_from_before_this_feature_still_reports_what_it_owes():
    """The users most at risk are the ones with no `source` field.

    Every character created before it existed used a CC BY default. Reporting
    those as "no third-party assets recorded" is not an absence of information —
    it is an affirmative FALSE COMPLIANCE STATEMENT, made to exactly the people
    who need the opposite. The evidence was in the same file all along:
    `metadata.dicebear_style`.
    """
    from an.characters.schema import CharacterDescriptor

    legacy = CharacterDescriptor(
        name="old",
        metadata={"art_provenance": "dicebear", "dicebear_style": "adventurer",
                  "dicebear_seed": "old"},
    )
    assert legacy.source is None  # as written before this feature existed

    report = collect_credits({"characters": {"old": legacy}})
    assert len(report.owed) == 1, (
        "a legacy CC BY character is reported as owing nothing"
    )
    assert "Lisa Wischofsky" in report.owed[0].source.attribution


def test_a_legacy_character_from_the_offline_fallback_owes_nothing():
    """Reconstruction must not invent an obligation where there is none."""
    from an.characters.schema import CharacterDescriptor

    legacy = CharacterDescriptor(
        name="old", metadata={"art_provenance": "fallback_geometric"}
    )
    assert collect_credits({"characters": {"old": legacy}}).entries == []


def test_a_legacy_character_of_an_unrecognised_style_is_unverified_not_clear():
    from an.characters.schema import CharacterDescriptor

    legacy = CharacterDescriptor(
        name="old", metadata={"dicebear_style": "some-style-nobody-checked"}
    )
    report = collect_credits({"characters": {"old": legacy}})
    assert len(report.unverified) == 1, "an unknown style must not read as clear"
