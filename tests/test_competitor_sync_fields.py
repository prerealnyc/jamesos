"""What we ask the vendor for, and what we do with what comes back.

The field list is not cosmetic: the vendor client validates the whole page
against its own model, so ONE field it types wrongly takes every post with it.
That is not hypothetical — asking TikTok for `duration` (typed `int`, returned
as `65.713`) raised a ValidationError for the entire page, and every TikTok
account in every tenant synced zero posts, silently.
"""

import pytest

from james_os.competitor_sync import _POST_FIELDS, _normalize, media_choice

pytestmark = pytest.mark.nodb


def test_tiktok_never_asks_for_the_field_that_kills_the_page():
    """`duration` is one integer. The posts are the shelf."""
    assert "duration" not in _POST_FIELDS["tiktok"]
    # The fields that carry the post itself must still be there.
    for needed in ("id", "description", "like_count", "video_thumbnail", "video_url"):
        assert needed in _POST_FIELDS["tiktok"], needed


def test_a_tiktok_post_normalises_without_a_duration():
    post = {
        "id": "7333", "description": "3 automations that replaced a spreadsheet",
        "like_count": 1200, "comment_count": 44, "forward_count": 7, "play_count": 90_000,
        "created_at_date": "2026-09-01", "post_type": "video",
        "video_thumbnail": "https://cdn.example/t.jpg", "video_url": "https://cdn.example/v.mp4",
        "username": "milesreevesai",
    }
    out = _normalize("tiktok", "milesreevesai", post)
    assert out["post_id"] == "7333"
    assert out["media_type"] == "video"
    assert out["duration"] == 0, "absent is 0, not a crash"
    assert out["likes"] == 1200 and out["views"] == 90_000
    assert out["thumbnail_url"].startswith("https://")


def test_a_fractional_duration_would_still_normalise_if_it_ever_arrives():
    """Belt and braces: if a later client version starts sending it, the
    normaliser must take a float without complaint."""
    out = _normalize("tiktok", "x", {"id": "1", "duration": 65.713, "description": "",
                                     "like_count": 0, "comment_count": 0})
    assert out["duration"] == 65


def test_a_video_past_the_cap_keeps_its_cover_instead_of_nothing():
    """Downloading every video is expensive, so a cap is right — but SKIPPING
    the post means the design eye can never read it. skelon held 100 posts and
    28 pictures; the other 72 were invisible."""
    vid = {"media_type": "video", "media_url": "https://cdn/v.mp4",
           "thumbnail_url": "https://cdn/cover.jpg"}
    # inside the cap: the file itself
    assert media_choice(vid, 0, 3) == ("https://cdn/v.mp4", "video", "")
    # past it: the cover, as an IMAGE (so it is stored as .jpg and readable)
    assert media_choice(vid, 3, 3) == ("https://cdn/cover.jpg", "image", "")
    # past it with no cover at all: reported, not silently dropped
    src, kind, reason = media_choice({**vid, "thumbnail_url": ""}, 3, 3)
    assert (src, reason) == ("", "video cap, no cover")


def test_a_video_with_no_file_url_is_stored_as_the_picture_it_actually_is():
    """YouTube hands over no file. Storing its JPEG cover under .mp4 made it
    unreadable to the eye and unrenderable in the grid."""
    yt = {"media_type": "video", "media_url": "",
          "thumbnail_url": "https://i.ytimg.com/vi/x/maxresdefault.jpg"}
    assert media_choice(yt, 0, 3) == (yt["thumbnail_url"], "image", "")


def test_an_image_post_takes_its_file_and_a_post_with_nothing_says_so():
    img = {"media_type": "image", "media_url": "https://cdn/p.jpg", "thumbnail_url": ""}
    assert media_choice(img, 0, 3) == ("https://cdn/p.jpg", "image", "")
    assert media_choice({"media_type": "image"}, 0, 3) == ("", "image", "no media url")


def test_the_chain_ends_by_measuring_the_gap():
    """competitor_gaps was empty for two of three brands because the gap was
    only computed when somebody opened that view."""
    import inspect

    from james_os import competitor_sync

    src = inspect.getsource(competitor_sync.full_refresh)
    assert "competitor_gap" in src
    assert "content_gap(" in src
    assert '"steps": 5' in src, "the progress line must count the stage it now runs"
