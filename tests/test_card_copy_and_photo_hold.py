"""Two leaks seen on held showcase posts (2026-10-08):
  * a card's byline printed 'John Doe Real Estate' — the copy model was asked
    for "the brand name" without being told it;
  * a talking-head reel cover of a news page (480px) was drawn as the photo."""
from james_os import hero_context
from james_os.template_clone import ground_copy, is_placeholder

FACTS = {"name": "A Real Brand", "handle": "@arealbrand", "website": ""}
NONE = {"name": "", "handle": "", "website": ""}


def test_template_filler_is_recognised_and_real_copy_is_not():
    for t in ("John Doe Real Estate", "Your Name @yourhandle", "@username", "Lorem ipsum dolor",
              "[Brand]", "{name}", "www.example.com", "Your Company"):
        assert is_placeholder(t), t
    for t in ("Malls Aren't Dead", "2 Years", "Book your tour", "Your Next Home Awaits",
              "Your trip, your pace", "No. 1 in the county"):
        assert not is_placeholder(t), t


def test_bylines_are_the_brands_facts_not_the_models():
    out = ground_copy({"headline": "Big news", "byline": "John Doe Real Estate",
                       "byline#2": "@yourhandle", "byline#3": "Licensed since 2004"},
                      ["headline", "byline", "byline#2", "byline#3"], FACTS)
    assert out == {"headline": "Big news", "byline": "A Real Brand",
                   "byline#2": "@arealbrand", "byline#3": "Licensed since 2004"}


def test_a_byline_the_brand_has_no_fact_for_is_left_out_not_invented():
    out = ground_copy({"headline": "Big news", "byline": "Omar Khan"}, ["headline", "byline"], NONE)
    assert out == {"headline": "Big news"}


def test_a_placeholder_in_any_slot_is_dropped():
    out = ground_copy({"headline": "Your Name Here", "cta": "Book now"}, ["headline", "cta"], NONE)
    assert out == {"cta": "Book now"}


def _photo(uri, w=1080, h=1350, caption="", **q):
    return {"uri": uri, "width": w, "height": h, "subject_caption": caption, "quality": q}


def test_a_reel_cover_and_a_screen_grab_are_held_back_a_feed_photo_is_not():
    assert hero_context.held_back(_photo("a", 480, 853))                      # reel cover
    assert hero_context.held_back(_photo("b", quality_unused=1, short_edge=480))
    assert hero_context.held_back(_photo("c", caption="A man in front of a news article"))
    assert hero_context.held_back(_photo("d", has_text=True))
    assert not hero_context.held_back(_photo("e", 1440, 817))                 # a landscape feed photo
    assert not hero_context.held_back(_photo("f", caption="a golf green at dusk"))
    assert not hero_context.held_back({"uri": "g"})                           # a legacy row, unmeasured


def test_rotation_keeps_held_photos_out_while_anything_else_is_there():
    class Ctx:
        photo_urls = ["cover", "good"]
        held_back_urls = ["cover"]
    assert hero_context.rotation_urls(Ctx()) == ["good"]
    Ctx.held_back_urls = ["cover", "good"]
    assert hero_context.rotation_urls(Ctx()) == ["cover", "good"], "nothing else: the library still posts"
