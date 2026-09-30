"""The layout taxonomy: naming what a catalogue layout IS.

The engine records two kinds and the entire 454-layout corpus collapses into
them, which is why the library reads as an undifferentiated pile. These tests pin
the finer names against the signatures actually MEASURED in that corpus
(2026-09-30), not against invented ones — a taxonomy that classifies nothing real
is decoration.
"""

from james_os.layout_types import classify, describe, display_name, roles_of


def _spec(treatment: str, roles: list[str], regions: int = 0) -> dict:
    bg: dict = {"treatment": treatment}
    if regions:
        bg["photo_boxes"] = [{"x": 0, "y": 0, "w": 0.3, "h": 0.3}] * regions
    return {"kind": "graphic_card", "background": bg,
            "elements": [{"role": r, "box": {"x": 0.1, "y": 0.1, "w": 0.5, "h": 0.1}}
                         for r in roles]}


# The eight commonest signatures in the real corpus, with their counts, so a
# future change that re-buckets the bulk of the library fails here loudly.
def test_the_corpus_top_signatures_get_distinct_names():
    measured = [
        ("full_bleed_photo", ["headline"], "photo statement"),                       # 140
        ("full_bleed_photo", ["headline", "subhead"], "photo statement pair"),       # 52
        ("solid", ["headline", "subhead"], "typographic statement pair"),            # 20
        ("photo_with_scrim", ["headline", "subhead"], "scrimmed statement pair"),    # 16
        ("solid", ["headline"], "typographic statement"),                            # 15
        ("full_bleed_photo", ["headline", "kicker"], "photo labelled statement"),     # 13
        ("photo_with_scrim", ["kicker", "headline", "subhead"], "scrimmed announcement"),  # 11
        ("full_bleed_photo", ["cta", "headline", "subhead"], "photo offer"),          # 8
    ]
    for treatment, roles, want in measured:
        assert classify(_spec(treatment, roles))["label"] == want, (treatment, roles)

    # ...and they are genuinely distinct: eight signatures, eight labels.
    labels = {classify(_spec(t, r))["label"] for t, r, _ in measured}
    assert len(labels) == len(measured)


def test_a_cta_outranks_everything_else():
    """An offer is an offer however much else is on the card — the writer must
    supply a call to action, and that is the fact the type exists to carry."""
    got = classify(_spec("full_bleed_photo",
                         ["byline", "cta", "headline", "kicker", "stat", "subhead"]))
    assert got["type"] == "offer_card"


def test_a_stat_without_a_cta_is_a_stat_card():
    assert classify(_spec("full_bleed_photo", ["stat"]))["type"] == "stat_card"
    assert classify(_spec("solid", ["headline", "stat"]))["type"] == "stat_card"
    # but with a cta it is an offer, not a stat
    assert classify(_spec("solid", ["cta", "stat"]))["type"] == "offer_card"


def test_a_byline_makes_it_a_testimonial():
    got = classify(_spec("photo_with_scrim", ["byline", "headline", "subhead"]))
    assert got["type"] == "testimonial"


def test_multi_photo_regions_are_named_in_front():
    """A collage is a different job for the writer: several moments, not one."""
    assert classify(_spec("solid", ["headline"], regions=3))["label"] == \
        "3-up typographic statement"
    assert classify(_spec("solid", ["headline"], regions=6))["regions"] == 6
    # one region is not a collage — it is the ordinary single-photo case
    assert classify(_spec("solid", ["headline"], regions=1))["label"] == \
        "typographic statement"


def test_repeat_numbering_is_stripped_from_roles():
    """A layout with two calls to action ('cta', 'cta#2') is still an offer."""
    spec = {"background": {"treatment": "solid"},
            "elements": [{"role": "cta"}, {"role": "cta#2"}, {"role": "headline"}]}
    assert roles_of(spec) == frozenset({"cta", "headline"})
    assert classify(spec)["type"] == "offer_card"


def test_the_display_name_is_not_derived_from_the_slug():
    """'labelled_photo' printed on a solid background said 'photo labelled photo'.
    The readable name is held apart from the machine slug for exactly that."""
    assert display_name("labelled") == "labelled statement"
    assert classify(_spec("solid", ["headline", "kicker"]))["label"] == \
        "typographic labelled statement"


def test_it_never_raises_and_always_answers():
    """An unrecognised shape must be a filterable value, not an exception — the
    curation screen shows every row it holds, including the odd ones."""
    for junk in (None, {}, [], "", 7, {"elements": "not a list"},
                 {"elements": [None, 3, {"no_role": 1}]},
                 {"background": None, "elements": [{"role": ""}]}):
        got = classify(junk)  # type: ignore[arg-type]
        assert isinstance(got, dict)
        assert got["type"]
        assert got["label"]

    assert classify(None)["type"] == "other"  # type: ignore[arg-type]
    assert describe("nonexistent_type")


def test_an_unknown_background_is_treated_as_a_photo():
    """The engine's commonest treatment by a wide margin; an unrecognised value
    guesses the majority case rather than inventing a bucket."""
    assert classify(_spec("something_new", ["headline"]))["ground"] == "photo"
