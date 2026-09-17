"""The niche-reference shelf: what lands on it, and what must never happen to it.

A reference is a picture the niche rewarded, kept as a layout option. The rules
that matter are the ones that keep it from being mistaken for a competitor:

  * it lives under a shelf whose status is 'reference', which no scraping loop
    ever asks for;
  * the same picture twice is one row (a daily scan re-reads the same wall);
  * the layout learner files it as `source_kind='niche'`, so provenance can say
    what it really is.
"""

from uuid import uuid4

import pytest_asyncio

from james_os import competitors, design_templates as dt, niche_reference as nr
from james_os.db import acquire

# The smallest real PNG: a 1x1 pixel. The point of these tests is the shelf,
# not the picture, and sniff() only ever looks at the first bytes.
PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4890000000a"
    "49444154789c6360000002000100ffff03000006000557bfabd40000000049454e44ae4260 82"
    .replace(" ", "")
)


@pytest_asyncio.fixture(autouse=True)
async def clean_shelves():
    """The suite's own Postgres runs as a superuser, which BYPASSES the row
    policies these tables carry in production — so a leftover row from another
    test is visible here even though a real brand could never see it. Start
    each test from empty and the assertions are about this code, not about
    what ran before it."""
    async with acquire() as conn:
        await conn.execute(
            "TRUNCATE competitor_post_analysis, competitor_posts, competitors, "
            "design_templates RESTART IDENTITY CASCADE")
    yield


async def _tenant(name: str = "niche-ref") -> str:
    async with acquire(None) as conn:
        return str(await conn.fetchval(
            "INSERT INTO tenants (name, slug) VALUES ($1, $2) RETURNING id",
            name, f"{name}-{uuid4().hex[:12]}"))


# ------------------------------------------------------------------ sniff


def test_a_file_that_is_not_an_image_is_refused_whatever_it_claims():
    """The form's content-type is the caller's word for it. Only the bytes
    decide, because whatever we store here is served back to a browser."""
    assert nr.sniff(PNG) == "png"
    assert nr.sniff(b"\xff\xd8\xff\xe0 JFIF") == "jpg"
    assert nr.sniff(b"RIFF\x00\x00\x00\x00WEBPVP8 ") == "webp"
    assert nr.sniff(b"%PDF-1.7 not a picture") == ""
    assert nr.sniff(b"") == ""


def test_the_same_picture_gets_the_same_key_and_two_pictures_do_not():
    assert nr.key_for("onc-1", "https://x/y") == nr.key_for("onc-1", "https://other")
    assert nr.key_for("", "https://x/y") != nr.key_for("", "https://x/z")
    assert nr.key_for("", "") == ""


# ------------------------------------------------------------------ the shelf


async def test_the_shelf_is_one_row_per_tenant_and_is_never_scraped():
    tenant = await _tenant()
    first = await nr.shelf(tenant, niche="golf resort")
    again = await nr.shelf(tenant, niche="golf resort")
    assert first["id"] == again["id"], "a second scan must reuse the shelf"
    assert first["status"] == "reference"

    # Every scraping loop asks for 'tracked'. The shelf has no account behind
    # it, so it must not appear in that answer — otherwise sync, media and the
    # analysis pass would all try to scrape a handle that does not exist.
    tracked = await competitors.list_competitors(status="tracked", tenant_id=tenant)
    assert [c["handle"] for c in tracked] == []
    # The studio asks for both, and gets both.
    both = await competitors.list_competitors(status="tracked,reference", tenant_id=tenant)
    assert [c["handle"] for c in both] == [nr.SHELF_HANDLE]


async def test_a_picture_is_kept_once_however_many_scans_see_it():
    tenant = await _tenant()
    first = await nr.save_reference(
        tenant, image=PNG, source_url="https://example.test/p/1", platform="facebook",
        author="Desert Willow Golf Resort", interactions=1122, fmt="info graphic",
        hook="STREAMING SCHEDULE", why="a schedule people screenshot", ref="onc-1")
    assert first["stored"] is True

    second = await nr.save_reference(
        tenant, image=PNG, source_url="https://example.test/p/1", platform="facebook",
        interactions=1200, ref="onc-1")
    assert second["stored"] is False
    assert second["reason"] == "already held"
    assert second["post_id"] == first["post_id"]

    async with acquire(tenant) as conn:
        rows = await conn.fetch("SELECT caption, likes, stored_media_url, media_type "
                                "FROM competitor_posts")
    assert len(rows) == 1
    assert rows[0]["media_type"] == "image"
    assert rows[0]["stored_media_url"], "a reference we cannot show is no reference"
    assert "Desert Willow" in rows[0]["caption"]
    assert rows[0]["likes"] == 1122


async def test_the_read_the_caller_paid_for_is_kept_beside_the_picture():
    """The eye that judged the picture on-niche also named its format and its
    words. Re-reading it here would be paying twice for the same sentence."""
    tenant = await _tenant()
    await nr.save_reference(
        tenant, image=PNG, source_url="https://example.test/p/2", platform="instagram",
        fmt="quote card", hook="20 UNDER PAR", why="one stat, huge type",
        recipe="giant numeral over a course photo", ref="onc-2")
    async with acquire(tenant) as conn:
        row = await conn.fetchrow(
            "SELECT a.format, a.hook, a.why_it_works, a.transferable_pattern, a.status "
            "FROM competitor_post_analysis a")
    assert row["status"] == "ok"
    assert row["format"] == "quote card"
    assert row["hook"] == "20 UNDER PAR"
    assert "course photo" in row["transferable_pattern"]


async def test_a_picture_with_no_id_and_a_file_that_is_not_an_image_are_refused():
    tenant = await _tenant()
    assert (await nr.save_reference(tenant, image=PNG, ref="", source_url=""))["stored"] is False
    assert (await nr.save_reference(
        tenant, image=b"%PDF-1.7", ref="onc-3"))["reason"] == "not an image"
    assert (await nr.save_reference(
        tenant, image=b"", ref="onc-4"))["reason"] == "empty image"
    async with acquire(tenant) as conn:
        assert await conn.fetchval("SELECT count(*) FROM competitor_posts") == 0


async def test_a_reference_is_stamped_with_the_brand_that_filed_it():
    """Isolation itself is the row policy's job (and the suite's superuser
    connection bypasses it), so what is checkable here is the stamp the policy
    reads: the shelf and the picture must both carry the filing tenant."""
    tenant = await _tenant("ref-a")
    await nr.save_reference(tenant, image=PNG, source_url="https://example.test/p/9",
                            platform="facebook", ref="onc-9")
    async with acquire(tenant) as conn:
        rows = await conn.fetch(
            "SELECT p.tenant_id AS post_tenant, c.tenant_id AS shelf_tenant "
            "FROM competitor_posts p JOIN competitors c ON c.id = p.competitor_id")
    assert len(rows) == 1
    assert str(rows[0]["post_tenant"]) == tenant
    assert str(rows[0]["shelf_tenant"]) == tenant
    assert (await nr.count(tenant))["held"] == 1


# ------------------------------------------------- what the learner makes of it


async def test_the_learner_files_a_reference_as_niche_not_as_a_competitor():
    """A layout off this shelf is not "what @handle does" — nobody tracks the
    account, and the platform often never named one."""
    tenant = await _tenant()
    await nr.save_reference(tenant, image=PNG, source_url="https://example.test/p/5",
                            platform="facebook", interactions=900, ref="onc-5")
    # A real competitor's post, for contrast.
    async with acquire(tenant) as conn:
        cid = await conn.fetchval(
            "INSERT INTO competitors (platform, handle, name, status, discovered_via) "
            "VALUES ('instagram', 'rival', 'Rival', 'tracked', 'manual') RETURNING id")
        await conn.execute(
            "INSERT INTO competitor_posts (competitor_id, platform, post_id, url, "
            "media_type, stored_media_url, engagement_rate) "
            "VALUES ($1, 'instagram', 'ig-1', 'https://ig/1', 'image', 'stored://x', 0.2)",
            cid)

    stills = await dt._unlearned_competitor_stills(tenant, 10)
    by_handle = {s["handle"]: s for s in stills}
    assert by_handle["rival"]["shelf_status"] == "tracked"
    assert by_handle[nr.SHELF_HANDLE]["shelf_status"] == "reference"
    assert by_handle[nr.SHELF_HANDLE]["discovered_via"] == nr.VIA
    # The competitor's post has a real engagement rate and leads; the reference
    # has none (no follower base to divide by) and is ordered by its own
    # interaction count instead of being dropped to the bottom arbitrarily.
    assert stills[0]["handle"] == "rival"
