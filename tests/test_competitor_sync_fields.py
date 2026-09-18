"""What we ask the vendor for, and what we do with what comes back.

The field list is not cosmetic: the vendor client validates the whole page
against its own model, so ONE field it types wrongly takes every post with it.
That is not hypothetical — asking TikTok for `duration` (typed `int`, returned
as `65.713`) raised a ValidationError for the entire page, and every TikTok
account in every tenant synced zero posts, silently.
"""

import pytest

from james_os.competitor_sync import _POST_FIELDS, _normalize

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
