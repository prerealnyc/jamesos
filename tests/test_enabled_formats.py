"""Per-brand allowed-formats clamp (imagegen._clamp_format): the rotation-for-
variety gate that keeps james-os from producing a template the Brand Manager
admin disabled. Pure logic — no DB, so it runs without the test Postgres."""

from james_os.imagegen import _clamp_format


def test_no_restriction_keeps_the_pick():
    # None (no setting synced) and an empty set both mean "no restriction"
    assert _clamp_format("statement", None) == "statement"
    assert _clamp_format("bold_statement", set()) == "bold_statement"


def test_allowed_pick_is_kept():
    assert _clamp_format("bold_statement", {"bold_statement", "full_bleed"}) == "bold_statement"


def test_disabled_swaps_within_family_for_variety():
    # photo card disabled -> an allowed photo card
    assert _clamp_format("statement", {"full_bleed", "hero_quote"}) in {"full_bleed", "hero_quote"}
    # text card disabled -> an allowed text card
    assert _clamp_format("brand_quote", {"big_stat", "bold_statement"}) in {"big_stat", "bold_statement"}
    # photo carousel disabled -> the allowed carousel sibling
    assert _clamp_format("carousel", {"text_carousel"}) == "text_carousel"


def test_whole_family_off_falls_back_cross_family():
    # only a photo format is allowed; a text pick has to cross over rather than
    # render a disabled card
    assert _clamp_format("bold_statement", {"full_bleed"}) == "full_bleed"


def test_rotation_spreads_across_allowed():
    # over many calls a disabled pick should land on BOTH allowed siblings
    got = {_clamp_format("full_bleed", {"hero_quote", "editorial_split"}) for _ in range(40)}
    assert got == {"hero_quote", "editorial_split"}
