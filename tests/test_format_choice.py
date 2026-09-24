"""A travel brand that asked for photo posts should not get a quote card.

The clone path never consulted the brand's format choice — it reproduced
whatever art direction the competitor happened to use. So a travel brand whose
whole appeal is photography had its first posts arrive as black typographic
posters cloned off a competitor's quote card, and the choice it had made was
sitting in the database unread.
"""

import pytest

from james_os import template_clone as tc

pytestmark = pytest.mark.nodb

PHOTO_SPEC = {"background": {"treatment": "full_bleed_photo"},
              "elements": [{"role": "headline", "box": {"x": .1, "y": .1, "w": .8, "h": .2}}]}
POSTER_SPEC = {"background": {"treatment": "solid"},
               "elements": [{"role": "headline", "box": {"x": .1, "y": .1, "w": .8, "h": .2}}]}


def test_it_can_tell_a_poster_from_a_photo_post():
    assert tc._family_of(PHOTO_SPEC) == "photo"
    assert tc._family_of(POSTER_SPEC) == "poster"


def test_a_brand_that_chose_photo_posts_is_not_given_quote_cards():
    chose_photo = {"full_bleed", "minimal_over", "hero_quote"}
    assert tc._family_allowed("photo", chose_photo) is True
    assert tc._family_allowed("poster", chose_photo) is False


def test_a_brand_that_chose_posters_is_not_given_photo_posts():
    chose_posters = {"brand_quote", "bold_statement"}
    assert tc._family_allowed("poster", chose_posters) is True
    assert tc._family_allowed("photo", chose_posters) is False


def test_a_brand_that_chose_both_gets_both():
    both = {"full_bleed", "brand_quote"}
    assert tc._family_allowed("photo", both) is True
    assert tc._family_allowed("poster", both) is True


def test_no_choice_means_no_restriction():
    """A brand nobody has asked yet behaves exactly as it did before."""
    for allowed in (None, set()):
        assert tc._family_allowed("photo", allowed) is True
        assert tc._family_allowed("poster", allowed) is True


def test_a_selection_naming_no_designed_format_is_not_a_ban_on_everything():
    """Someone who only ticked writing and video has said nothing about card
    design; silently producing zero posts would be the wrong reading."""
    writing_only = {"blog", "essay", "reel"}
    assert tc._family_allowed("photo", writing_only) is True
    assert tc._family_allowed("poster", writing_only) is True
