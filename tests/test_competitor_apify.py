"""YouTube and LinkedIn competitors — the platforms Xpoz cannot reach.

Shapes here are the LIVE actor outputs, captured 2026-09-19 from
`streamers/youtube-scraper` and `apimaestro/linkedin-company-posts`, not the
docs. The normalised row must be the SAME shape the Xpoz path produces, because
`_upsert_post` reads it by key and a missing one is a KeyError mid-sync.
"""

import pytest

from james_os import competitor_apify as ca
from james_os.competitors import PLATFORMS, SCRAPE_PLATFORMS

pytestmark = pytest.mark.nodb

_UPSERT_KEYS = {"post_id", "url", "caption", "media_type", "media_url",
                "thumbnail_url", "duration", "likes", "comments", "shares",
                "views", "posted_at"}

YT = {
    "title": "A Week In Montenegro With My Biggest Competitors", "type": "video",
    "id": "nQz6sMBSK4A", "url": "https://www.youtube.com/watch?v=nQz6sMBSK4A",
    "thumbnailUrl": "https://i.ytimg.com/vi/nQz6sMBSK4A/maxresdefault.jpg",
    "viewCount": 13582, "date": "2026-09-10T15:49:02.000Z", "likes": 141,
    "commentsCount": 27, "channelName": "Liam Ottley", "channelUsername": "LiamOttley",
}
LI = {
    "activity_urn": "7426717933659136001",
    "full_urn": "urn:li:activity:7426704576625618944",
    "post_url": "https://www.linkedin.com/posts/liamottley_real-numbers-activity-7426704576625618944",
    "text": "Real numbers. Real case study. No fluff.",
    "media": {"type": "image", "items": [{"url": "https://media.licdn.com/dms/image/v2/abc"}]},
    "stats": {"total_reactions": 317, "like": 281, "comments": 23, "reposts": 14},
    "posted_at": {"relative": "7mo", "date": "2026-02-09 21:05:37", "timestamp": 1770667537131},
    "author": {"name": "Liam Ottley"},
}


def test_what_we_can_scrape_is_wider_than_what_we_can_search():
    """Xpoz searches three networks; a peer's real home is often a fourth."""
    assert set(PLATFORMS) == {"instagram", "tiktok", "twitter"}
    assert {"youtube", "linkedin"} <= set(SCRAPE_PLATFORMS)
    assert set(ca.PLATFORMS) == {"youtube", "linkedin"}


def test_a_youtube_video_becomes_the_same_row_the_xpoz_path_produces():
    row = ca._youtube_row("LiamOttley", YT)
    assert set(row) == _UPSERT_KEYS
    assert row["post_id"] == "nQz6sMBSK4A"
    assert row["caption"].startswith("A Week In Montenegro")
    assert row["media_type"] == "video"
    # YouTube hands over no file — the thumbnail is the still we keep, and it
    # is the designed card the layout reader can actually learn from.
    assert row["media_url"] == ""
    assert row["thumbnail_url"].endswith("maxresdefault.jpg")
    assert (row["views"], row["likes"], row["comments"]) == (13582, 141, 27)
    assert row["posted_at"].year == 2026


def test_a_linkedin_image_post_keeps_its_picture_and_its_reactions():
    row = ca._linkedin_row("morningside", LI)
    assert set(row) == _UPSERT_KEYS
    assert row["post_id"] == "urn:li:activity:7426704576625618944"
    assert row["media_type"] == "image"
    assert row["media_url"].startswith("https://media.licdn.com/")
    # total_reactions, not `like` — the post's real engagement includes
    # support/love/celebrate, and taking only likes understates every post.
    assert row["likes"] == 317
    assert (row["comments"], row["shares"]) == (23, 14)
    assert row["posted_at"].year == 2026


def test_a_text_only_linkedin_post_is_not_pretended_to_have_a_picture():
    row = ca._linkedin_row("morningside", {**LI, "media": None})
    assert row["media_type"] == "text"
    assert row["media_url"] == "" and row["thumbnail_url"] == ""


def test_a_post_with_no_id_at_all_is_dropped():
    assert ca._youtube_row("x", {"title": "no id"}) is None
    assert ca._linkedin_row("x", {"text": "no urn"}) is None


def test_the_handle_becomes_the_url_each_actor_actually_accepts():
    """The LinkedIn actors answer 'No posts found or wrong input' to a bare
    slug — measured, not assumed — so the URL is built, never hoped for."""
    assert ca.youtube_url("LiamOttley") == "https://www.youtube.com/@LiamOttley"
    assert ca.youtube_url("@LiamOttley") == "https://www.youtube.com/@LiamOttley"
    assert ca.youtube_url("UCui4jxDaMb53Gdh-AZUTPAg").endswith(
        "/channel/UCui4jxDaMb53Gdh-AZUTPAg")
    assert ca.youtube_url("https://www.youtube.com/@x") == "https://www.youtube.com/@x"
    company, profile = ca.linkedin_urls("morningside-ai")
    assert company == "https://www.linkedin.com/company/morningside-ai/"
    assert profile == "https://www.linkedin.com/in/morningside-ai/"


def test_an_undated_post_is_kept_and_an_old_one_is_not():
    """An actor that omits a date is not evidence the post is old; dropping
    those would quietly empty a shelf."""
    from datetime import datetime, timedelta, timezone
    now = datetime.now(timezone.utc)
    rows = [{"posted_at": None}, {"posted_at": now - timedelta(days=5)},
            {"posted_at": now - timedelta(days=400)}]
    kept = ca._recent(rows, 90)
    assert len(kept) == 2
    assert ca._recent(rows, 0) == rows


async def test_an_unknown_platform_or_missing_token_reports_instead_of_raising():
    rows, err = await ca.fetch_posts("instagram", "someone")
    assert rows == [] and "not an Apify platform" in err
    rows, err = await ca.fetch_posts("youtube", "")
    assert rows == [] and err == "no handle"
