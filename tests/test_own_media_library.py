"""The photo library plumbing that decides whether imported own photos are ever
USED — and the save() relabel that lets an own post land as 'own'.

No network, no database. What these hold:

  * save(): an own post matching a competitor/niche row relabels it 'own' only
    while nobody else holds it (not adopted, not in the catalogue); otherwise it
    reports the match and inserts nothing;
  * hero_context: caches keyed by the tenant acquire() would use (the request's
    when no arg), invalidation clears every '<tenant>:*' bytes key, the render
    pool is the whole library least-used first at native resolution, and the
    likeness paths see the owner's uploads only;
  * photo_pick: screenshots and sub-1080 photos lose to real ones, with a
    fallback when nothing qualifies;
  * stock_photo: a thin query anchors to THIS tenant, never Tenant Zero's industry,
    and the Unsplash credit survives the 2-tuple paths;
  * main: the upload stores under the request tenant and its analysis runs as
    the row's tenant, captioning hero photos instead of video-perceiving them.
"""

from __future__ import annotations

import io
from uuid import UUID

import pytest
from PIL import Image

from james_os import db as db_module
from james_os import design_templates as dt
from james_os import hero_context, photo_pick, photo_subject, stock_photo
from james_os.config import settings

pytestmark = pytest.mark.nodb

BRAND_B = UUID("945dbd37-4ec9-464e-9af5-459aad44eec9")
TENANT_ZERO = settings.default_tenant_id


def _jpeg(w, h, seed=1, *, exif_orientation=None) -> bytes:
    import numpy as np

    rng = np.random.default_rng(seed)
    small = rng.integers(0, 256, (h // 8 + 1, w // 8 + 1, 3), dtype=np.uint8)
    img = Image.fromarray(np.repeat(np.repeat(small, 8, 0), 8, 1)[:h, :w], "RGB")
    buf = io.BytesIO()
    if exif_orientation:
        ex = Image.Exif()
        ex[0x0112] = exif_orientation
        img.save(buf, "JPEG", quality=90, exif=ex.tobytes())
    else:
        img.save(buf, "JPEG", quality=90)
    return buf.getvalue()


def _size(b: bytes) -> tuple[int, int]:
    return Image.open(io.BytesIO(b)).size


@pytest.fixture(autouse=True)
def _clean_caches():
    hero_context._CACHE.clear()
    hero_context._BYTES_CACHE.clear()
    hero_context._BYTES_AT.clear()
    hero_context._URL_BYTES.clear()
    hero_context._DESCRIBED.clear()
    tok = db_module._request_tenant.set(None)
    yield
    db_module._request_tenant.reset(tok)
    hero_context._CACHE.clear()
    hero_context._BYTES_CACHE.clear()
    hero_context._BYTES_AT.clear()
    hero_context._URL_BYTES.clear()
    hero_context._DESCRIBED.clear()


# ── B3: save() ──────────────────────────────────────────────────────────────

SPEC = {"status": "ok", "kind": "graphic_card",
        "background": {"treatment": "solid"},
        "elements": [{"role": "headline", "box": {"x": .1, "y": .1, "w": .8, "h": .3}}]}


class _SaveConn:
    def __init__(self, hit=None, relabel=True, insert="t-new"):
        self.hit, self.relabel, self.insert, self.sql = hit, relabel, insert, []

    async def fetchrow(self, sql, *a):
        self.sql.append(sql)
        return self.hit

    async def fetchval(self, sql, *a):
        self.sql.append(sql)
        if sql.lstrip().startswith("UPDATE"):
            return True if self.relabel else None
        if "INSERT" in sql:
            return self.insert
        return None


def _patch_acquire(monkeypatch, conn, module=dt):
    class _Ctx:
        async def __aenter__(self):
            return conn

        async def __aexit__(self, *a):
            return False
    monkeypatch.setattr(module, "acquire", lambda *a, **k: _Ctx())


async def test_an_own_post_relabels_an_unclaimed_competitor_row(monkeypatch):
    conn = _SaveConn(hit={"id": "t-comp", "source_kind": "competitor", "house_layout_id": None})
    _patch_acquire(monkeypatch, conn)
    out: dict = {}
    assert await dt.save(BRAND_B, SPEC, source_kind="own", outcome=out) == "t-comp"
    assert out == {"template_id": "t-comp", "matched_kind": "competitor",
                   "relabelled": True, "created": False}
    upd = next(s for s in conn.sql if s.lstrip().startswith("UPDATE"))
    assert "source_kind = 'own'" in upd and "house_layout_id IS NULL" in upd
    assert "house_layouts" in upd, "a shape the catalogue holds is not relabelled"
    assert not any("INSERT" in s for s in conn.sql)


async def test_an_adopted_or_catalogued_row_is_left_alone_and_reported(monkeypatch):
    conn = _SaveConn(hit={"id": "t-adopt", "source_kind": "niche", "house_layout_id": "h-1"})
    _patch_acquire(monkeypatch, conn)
    out: dict = {}
    assert await dt.save(BRAND_B, SPEC, source_kind="own", outcome=out) == "t-adopt"
    assert out["matched_kind"] == "niche" and out["relabelled"] is False
    assert not any(s.lstrip().startswith("UPDATE") for s in conn.sql)

    conn = _SaveConn(hit={"id": "t-cat", "source_kind": "competitor", "house_layout_id": None},
                     relabel=False)                     # the catalogue holds the shape
    _patch_acquire(monkeypatch, conn)
    out = {}
    await dt.save(BRAND_B, SPEC, source_kind="own", outcome=out)
    assert out["relabelled"] is False and out["matched_kind"] == "competitor"


async def test_a_competitor_save_never_relabels_and_a_new_shape_inserts(monkeypatch):
    conn = _SaveConn(hit={"id": "t-own", "source_kind": "own", "house_layout_id": None})
    _patch_acquire(monkeypatch, conn)
    assert await dt.save(BRAND_B, SPEC, source_kind="competitor") == "t-own"
    assert not any(s.lstrip().startswith("UPDATE") for s in conn.sql)

    conn = _SaveConn(hit=None)
    _patch_acquire(monkeypatch, conn)
    out: dict = {}
    assert await dt.save(BRAND_B, SPEC, source_kind="own", outcome=out) == "t-new"
    assert out["created"] is True and out["matched_kind"] == ""


# ── B5: hero_context ────────────────────────────────────────────────────────


def _row(uri, origin=None, tags=(), source_type="upload", w=None, h=None, quality=None,
         created="2026-10-01T00:00:00+00:00"):
    r = {"uri": uri, "source_type": source_type, "tags": list(tags), "created_at": created,
         "width": w, "height": h, "quality": quality or {}}
    if origin:
        r["origin"] = origin
    return r


LIB = [
    _row("https://s.test/own1.jpg", "own_instagram", ["own-post", "origin:own_instagram"],
         created="2026-10-05T00:00:00+00:00"),
    _row("https://s.test/up1.jpg", "owner_upload", created="2026-09-01T00:00:00+00:00"),
    _row("https://s.test/gen.jpg", source_type="generated"),
    _row("https://s.test/shot.png", "owner_upload", quality={"screenshot": True},
         created="2026-10-07T00:00:00+00:00"),
    # a scraped row read BEFORE migration 072: no origin column, only the tags
    _row("https://s.test/own2.jpg", None, ["own-post", "origin:own_instagram"]),
]


@pytest.fixture
def library(monkeypatch):
    seen = {"list": [], "describe": []}

    async def list_media(role="", tenant_id=None):
        seen["list"].append((role, tenant_id, db_module._request_tenant.get()))
        return LIB if role == "hero_photo" else []

    async def describe(urls):
        seen["describe"].append(list(urls))
        return {"description": "a man in his 40s"}

    monkeypatch.setattr(hero_context, "list_media", list_media)
    monkeypatch.setattr(hero_context, "describe_hero_from_photos", describe)
    return seen


async def test_the_likeness_paths_see_only_the_owners_uploads(library):
    ctx = await hero_context.get_hero_context(BRAND_B)
    assert "https://s.test/gen.jpg" not in ctx.photo_urls
    assert set(ctx.likeness_urls) == {"https://s.test/up1.jpg", "https://s.test/shot.png"}
    assert library["describe"] == [ctx.likeness_urls], "the hero is described from uploads only"
    assert "https://s.test/own1.jpg" in ctx.photo_urls and "https://s.test/own2.jpg" in ctx.photo_urls
    assert hero_context.origin_of(LIB[4]) == "own_instagram", "pre-072 rows fall back to the tags"


async def test_the_render_order_puts_screenshots_last_and_uploads_before_scrapes(library):
    ctx = await hero_context.get_hero_context(BRAND_B)
    assert ctx.photo_urls[-1] == "https://s.test/shot.png"
    assert ctx.photo_urls[0] == "https://s.test/up1.jpg"


async def test_a_brand_with_only_scraped_photos_gets_no_hero_description(monkeypatch, library):
    async def own_only(role="", tenant_id=None):
        return [LIB[0]] if role == "hero_photo" else []
    monkeypatch.setattr(hero_context, "list_media", own_only)
    ctx = await hero_context.get_hero_context(BRAND_B)
    assert ctx.photo_urls == ["https://s.test/own1.jpg"] and ctx.likeness_urls == []
    assert ctx.description == "" and library["describe"] == []


async def test_a_call_with_no_tenant_inside_brand_Bs_request_is_cached_as_B(library):
    db_module.set_request_tenant(BRAND_B)
    await hero_context.get_hero_context()
    assert str(BRAND_B) in hero_context._CACHE
    assert str(TENANT_ZERO) not in hero_context._CACHE, "brand B's photos must not be filed under Tenant Zero"


def test_invalidate_clears_every_bytes_pool_for_that_tenant_only():
    hero_context._CACHE.update({str(BRAND_B): object(), str(TENANT_ZERO): object()})
    hero_context._BYTES_CACHE.update({f"{BRAND_B}:render:24": [], f"{BRAND_B}:ref:3": [],
                                      f"{TENANT_ZERO}:render:24": []})
    hero_context.invalidate_cache(BRAND_B)
    assert str(BRAND_B) not in hero_context._CACHE and str(TENANT_ZERO) in hero_context._CACHE
    assert list(hero_context._BYTES_CACHE) == [f"{TENANT_ZERO}:render:24"]
    db_module.set_request_tenant(TENANT_ZERO)
    hero_context.invalidate_cache()          # no arg → the request's tenant
    assert hero_context._BYTES_CACHE == {}


class _Http:
    bodies: dict = {}

    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def get(self, url, **k):
        class R:
            content = _Http.bodies[url]

            def raise_for_status(self):
                pass
        return R()


@pytest.fixture
def downloads(monkeypatch):
    async def public(url, *, allow_http=False):
        return True
    monkeypatch.setattr("james_os.netguard.url_is_public", public)
    monkeypatch.setattr(hero_context.httpx, "AsyncClient", _Http)
    big = _jpeg(1440, 1800, seed=2)
    _Http.bodies = {r["uri"]: big for r in LIB}
    return big


async def test_render_photos_stay_native_and_ai_references_stay_1024(library, downloads):
    render = await hero_context.get_hero_photo_files(BRAND_B, limit=None)
    assert render and all(b is downloads for _u, b in render), "native bytes, untouched"
    refs = await hero_context.get_hero_photo_files(BRAND_B, limit=3, likeness=True)
    assert {u for u, _b in refs} <= {"https://s.test/up1.jpg", "https://s.test/shot.png"}
    assert all(max(_size(b)) <= 1024 for _u, b in refs)
    # limit=N without likeness is "the brand's real photos", own posts included
    some = await hero_context.get_hero_photo_files(BRAND_B, limit=3)
    assert "https://s.test/own1.jpg" in {u for u, _b in some}
    assert all(max(_size(b)) <= 1024 for _u, b in some)


async def test_the_render_pool_is_chosen_least_used_first_from_the_whole_library(
        monkeypatch, library, downloads):
    monkeypatch.setattr(hero_context, "_DESIGNED_POOL_MAX", 2)

    async def counts(tenant_id):
        return {"https://s.test/up1.jpg": 9, "https://s.test/own1.jpg": 4}
    monkeypatch.setattr(photo_pick, "_use_counts", counts)
    got = [u for u, _b in await hero_context.get_hero_photo_files(BRAND_B, limit=None)]
    # half the pool is the hero's (the owner upload, however used); the other
    # half is the never-used photo, however old — not the newest 24. The
    # screenshot stays out while anything else is there.
    assert got == ["https://s.test/up1.jpg", "https://s.test/own2.jpg"]


def _bulk(n, prefix, origin, *, w=1080, h=1350, quality=None, has_person=None):
    rows = []
    for i in range(n):
        r = _row(f"https://s.test/{prefix}{i}.jpg", origin,
                 [] if origin == "owner_upload" else ["own-post", f"origin:{origin}"],
                 w=w, h=h, quality=quality, created=f"2026-10-0{1 + i % 8}T00:00:00+00:00")
        if has_person is not None:
            r["has_person"] = has_person
        rows.append(r)
    return rows


async def test_a_big_own_post_import_cannot_push_the_owners_uploads_out_of_the_pool(
        monkeypatch, library, downloads):
    """20 owner photos each used once in the window, then 150 never-used own
    posts arrive: least-used-first alone filled all 24 slots with scrapes."""
    ups = _bulk(20, "up", "owner_upload", w=1440, h=1800)
    own = _bulk(150, "own", "own_instagram")

    async def lib(role="", tenant_id=None):
        return ups + own if role == "hero_photo" else []
    monkeypatch.setattr(hero_context, "list_media", lib)
    _Http.bodies = {r["uri"]: _Http.bodies["https://s.test/up1.jpg"] for r in ups + own}

    async def counts(tenant_id):
        return {r["uri"]: 1 for r in ups}
    monkeypatch.setattr(photo_pick, "_use_counts", counts)
    pool = [u for u, _b in await hero_context.get_hero_photo_files(BRAND_B, limit=None)]
    assert len(pool) == hero_context._DESIGNED_POOL_MAX
    assert sum("/up" in u for u in pool) == hero_context._DESIGNED_POOL_MAX // 2
    assert sum("/own" in u for u in pool) == hero_context._DESIGNED_POOL_MAX // 2, \
        "the never-used own posts still rotate through the other half"


async def test_a_hero_led_card_picks_the_hero_before_a_less_used_scrape(monkeypatch, library):
    """hero_quote / statement 'place the hero's real photo': the owner's uploads
    beat a never-used venue shot — and a scraped photo with a person in it is
    not the hero either (rule 7)."""
    ups = _bulk(2, "up", "owner_upload")
    venue = _bulk(3, "venue", "own_instagram", has_person=False)
    person = _bulk(1, "staff", "own_instagram", has_person=True)

    async def lib(role="", tenant_id=None):
        return ups + venue + person if role == "hero_photo" else []
    monkeypatch.setattr(hero_context, "list_media", lib)
    keys = await hero_context.hero_led_keys(BRAND_B)
    assert keys == {r["uri"] for r in ups}

    async def counts(tenant_id):
        return {r["uri"]: 3 for r in ups}
    monkeypatch.setattr(photo_pick, "_use_counts", counts)
    refs = [(r["uri"], _jpeg(1080, 1350, seed=20 + i))
            for i, r in enumerate(ups + venue + person)]
    for _ in range(6):
        got = await photo_pick.pick_hero_bytes(refs, BRAND_B, prefer=keys)
        assert got[0] in keys
    # no preference (any other format) → plain least-used rotation
    assert (await photo_pick.pick_hero_bytes(refs, BRAND_B))[0] in {r["uri"] for r in venue + person}
    # every preferred photo excluded → the rest of the library, not nothing
    got = await photo_pick.pick_hero_bytes(refs, BRAND_B, exclude=list(keys), prefer=keys)
    assert got and got[0] in {r["uri"] for r in venue + person}


async def test_scraped_people_never_crowd_the_owner_out_of_hero_led_cards(
        monkeypatch, library, downloads):
    """3 owner uploads used once, 40 scraped golfers (has_person) and 80 venue
    shots: the hero-led set is the owner's 3, the reserved half of the render
    pool goes to them, and twelve hero_quote picks in a row show the owner."""
    ups = _bulk(3, "up", "owner_upload", w=1440, h=1800)
    golfers = _bulk(40, "golfer", "own_instagram", has_person=True)
    venue = _bulk(80, "venue", "own_instagram", has_person=False)

    async def lib(role="", tenant_id=None):
        return ups + golfers + venue if role == "hero_photo" else []
    monkeypatch.setattr(hero_context, "list_media", lib)
    _Http.bodies = {r["uri"]: _Http.bodies["https://s.test/up1.jpg"]
                    for r in ups + golfers + venue}
    keys = await hero_context.hero_led_keys(BRAND_B)
    assert keys == {r["uri"] for r in ups}
    ctx = await hero_context.get_hero_context(BRAND_B)
    assert not set(ctx.hero_urls) & {r["uri"] for r in golfers}
    assert not set(ctx.likeness_urls) & {r["uri"] for r in golfers}

    used = {r["uri"]: 1 for r in ups}

    async def counts(tenant_id):
        return dict(used)
    monkeypatch.setattr(photo_pick, "_use_counts", counts)
    pool = await hero_context.get_hero_photo_files(BRAND_B, limit=None)
    assert {r["uri"] for r in ups} <= {u for u, _b in pool}, "the owner keeps the reserved half"
    for _ in range(12):
        got = await photo_pick.pick_hero_bytes(pool, BRAND_B, prefer=keys)
        assert got[0] in keys
        used[got[0]] = used.get(got[0], 0) + 1


async def test_a_brand_with_no_hero_photos_has_no_preference(monkeypatch, library):
    async def lib(role="", tenant_id=None):
        return _bulk(3, "own", "own_instagram") if role == "hero_photo" else []
    monkeypatch.setattr(hero_context, "list_media", lib)
    assert await hero_context.hero_led_keys(BRAND_B) == frozenset()


async def test_text_on_photo_stays_out_of_the_render_pool_while_anything_else_is_there(
        monkeypatch, library, downloads):
    clean = _bulk(2, "clean", "own_instagram")
    flyer = _bulk(2, "flyer", "own_instagram",
                  quality={"has_text": True, "in_rotation": False, "reasons": ["has_text"]})

    async def lib(role="", tenant_id=None):
        return (flyer + clean) if role == "hero_photo" else []
    monkeypatch.setattr(hero_context, "list_media", lib)
    _Http.bodies = {r["uri"]: _Http.bodies["https://s.test/up1.jpg"] for r in clean + flyer}
    pool = {u for u, _b in await hero_context.get_hero_photo_files(BRAND_B, limit=None)}
    assert pool == {r["uri"] for r in clean}
    ctx = await hero_context.get_hero_context(BRAND_B)
    assert set(ctx.photo_urls[-2:]) == {r["uri"] for r in flyer}, "kept in the library, ranked last"

    async def flyers_only(role="", tenant_id=None):
        return flyer if role == "hero_photo" else []
    monkeypatch.setattr(hero_context, "list_media", flyers_only)
    hero_context.invalidate_cache(BRAND_B)
    pool = {u for u, _b in await hero_context.get_hero_photo_files(BRAND_B, limit=None)}
    assert pool == {r["uri"] for r in flyer}, "all the library has: better than nothing"


def test_the_render_variant_reduces_only_huge_photos_and_uprights_them():
    huge = _jpeg(3000, 2000)
    assert _size(hero_context._render_image(huge)) == (2160, 1440)
    pano = _jpeg(4000, 1000)        # reducing it would take the short side under 1350
    assert hero_context._render_image(pano) == pano
    rot = _jpeg(1350, 1080, exif_orientation=6)
    assert _size(hero_context._render_image(rot)) == (1080, 1350)
    assert _size(hero_context._shrink_image(huge))[0] == 1024


@pytest.mark.parametrize("w,h", [(8064, 6048), (8160, 6120)])
def test_a_48_or_50_megapixel_phone_photo_is_reduced_not_dropped(w, h):
    """48 MP iPhone Pro / 50 MP Samsung: over the old 40 MP cap, which returned
    None and silently emptied the render pool of every such upload."""
    big = Image.new("RGB", (w, h), (90, 120, 150))
    buf = io.BytesIO()
    big.save(buf, "JPEG", quality=80)
    del big
    out = hero_context._render_image(buf.getvalue())
    assert out is not None
    ow, oh = _size(out)
    assert ow == 2160 and oh in {round(h * 2160 / w), int(h * 2160 / w)}


def test_a_large_png_is_resized_and_a_bomb_falls_back_to_the_reference_copy(monkeypatch):
    png = io.BytesIO()
    Image.new("RGB", (3000, 2000), (10, 200, 30)).save(png, "PNG")
    monkeypatch.setattr(hero_context, "_RENDER_MAX_PIXELS", 1_000_000)
    assert _size(hero_context._render_image(png.getvalue())) == (2160, 1440)
    # past Pillow's own bomb limit (shrunk here) — the 1024 copy, not None
    import warnings
    monkeypatch.setattr(Image, "MAX_IMAGE_PIXELS", 4_000_000)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", Image.DecompressionBombWarning)
        got = hero_context._render_image(png.getvalue())
    assert got is not None and max(_size(got)) == 1024
    assert hero_context._render_image(b"not an image") is None


# ── B5: photo_pick ──────────────────────────────────────────────────────────


@pytest.fixture
def no_ledger(monkeypatch):
    async def counts(tenant_id):
        return {}
    monkeypatch.setattr(photo_pick, "_use_counts", counts)


async def test_a_screenshot_and_a_thumbnail_lose_to_a_real_photo(no_ledger):
    shot = _jpeg(1170, 2532, seed=4)
    thumb = _jpeg(640, 800, seed=5)
    real = _jpeg(1080, 1350, seed=6)
    refs = [("https://s/shot", shot), ("https://s/thumb", thumb), ("https://s/real", real)]
    for _ in range(5):
        assert (await photo_pick.pick_hero_bytes(refs))[0] == "https://s/real"


async def test_when_nothing_qualifies_the_picker_still_answers(no_ledger):
    thumbs = [("https://s/a", _jpeg(640, 800, seed=7)), ("https://s/b", _jpeg(600, 750, seed=8))]
    assert (await photo_pick.pick_hero_bytes(thumbs))[0] in {"https://s/a", "https://s/b"}
    only_shot = [("https://s/shot", _jpeg(1170, 2532, seed=9))]
    assert (await photo_pick.pick_hero_bytes(only_shot))[0] == "https://s/shot"


def test_the_profile_mark_and_hero_cards_use_the_owners_photos():
    """The avatar next to @handle is the account's face: owner uploads only
    (no mark rather than a scraped venue shot). Hero-led cards prefer them."""
    import inspect

    from james_os import main
    src = inspect.getsource(main._generate_designed_post_image)
    mark = src[src.index("if profile_bytes is None:"):]
    mark = mark[:mark.index("except Exception")]
    assert "likeness=True" in mark and "limit=None" not in mark
    assert "prefer=(await hero_led_keys(tenant_id)) if fmt in HERO_FORMATS" in src


def test_screenshot_heuristics():
    assert photo_pick.looks_like_screenshot(_jpeg(1290, 2796))          # an exact phone size
    assert not photo_pick.looks_like_screenshot(_jpeg(1080, 1920))      # a 9:16 story export
    assert not photo_pick.looks_like_screenshot(_jpeg(1080, 1350))
    # screen-tall with a flat status bar
    img = Image.open(io.BytesIO(_jpeg(1000, 2100, seed=3)))
    img.paste((250, 250, 250), (0, 0, 1000, 120))
    buf = io.BytesIO()
    img.save(buf, "PNG")
    assert photo_pick.looks_like_screenshot(buf.getvalue())


# ── B5: stock_photo ─────────────────────────────────────────────────────────


async def test_a_thin_query_anchors_to_this_tenant_never_tenant_zero(monkeypatch):
    async def profile(tid):
        return {"identity": {"niche": "golf course", "niche_confirmed_at": "2026-09-01",
                             "location": "New Mexico"}} if tid == BRAND_B else None
    monkeypatch.setattr("james_os.brands.get_brand_profile", profile)
    assert stock_photo._build_query("#golf @x") == "modern professional"
    assert "real estate" not in stock_photo._build_query("🏌️")
    assert await stock_photo.tenant_anchor(BRAND_B) == "golf course New Mexico"
    other = UUID("3e6f1c31-41de-4c74-88da-474066f0cc32")
    assert await stock_photo.tenant_anchor(other) == "modern professional"
    assert "real estate" in await stock_photo.tenant_anchor(TENANT_ZERO)
    db_module.set_request_tenant(BRAND_B)
    assert await stock_photo.tenant_anchor() == "golf course New Mexico"


async def test_the_search_uses_the_tenant_anchor_and_the_credit_survives(monkeypatch):
    async def profile(tid):
        return {"identity": {"niche": "golf course", "niche_confirmed_at": "x"}}
    monkeypatch.setattr("james_os.brands.get_brand_profile", profile)
    monkeypatch.setattr(stock_photo.settings, "unsplash_access_key", "k", raising=False)

    async def public(url, *, allow_http=False):
        return True
    monkeypatch.setattr(stock_photo, "url_is_public", public)
    queries = []

    class C:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, params=None, headers=None):
            class R:
                status_code = 200
                content = b"jpeg"

                def raise_for_status(self):
                    pass

                def json(self):
                    return {"results": [{"id": "p1", "urls": {"regular": "https://images.unsplash.com/p1"},
                                         "links": {"download_location": "https://api.unsplash.com/d",
                                                   "html": "https://unsplash.com/photos/p1"},
                                         "user": {"name": "Ann", "links": {"html": "https://unsplash.com/@ann"}}}]}
            if params:
                queries.append(params["query"])
            return R()
    monkeypatch.setattr(stock_photo.httpx, "AsyncClient", C)
    got = await stock_photo.fetch_unsplash_hero("#tbt", tenant_id=BRAND_B)
    assert got[0] == "https://images.unsplash.com/p1"
    assert queries == ["golf course"]
    assert stock_photo.credit_for(got[0])["photographer"] == "Ann"
    from james_os.template_clone import _image_source
    src = _image_source(got[0], False, b"x")
    assert src["image_source"] == "unsplash" and src["hero_credit"]["photographer"] == "Ann"
    assert _image_source("https://s.test/up1.jpg", False, b"x") == {"image_source": "library"}
    assert _image_source("", True, b"x") == {"image_source": "generated"}


# ── B5: main.py upload + analysis ───────────────────────────────────────────


async def test_the_upload_analysis_runs_as_the_rows_tenant_and_captions_hero_photos(monkeypatch):
    from james_os import main

    seen = []

    async def get_asset(media_id, tenant):
        seen.append(("get", tenant))
        return {"role": "hero_photo", "source_type": "upload", "file_path": "supabase://x",
                "uri": "https://s.test/x.jpg"}

    async def describe(media_id, tenant):
        seen.append(("describe", tenant))

    async def read_one(media_id, uri, tenant, **k):
        seen.append(("caption", tenant, uri))
        return {"status": "read"}

    async def status(media_id, st, tenant):
        seen.append(("status", st, tenant))

    async def no_perception(*a, **k):
        raise AssertionError("a still must not go through video perception")

    monkeypatch.setattr(main, "get_media_for_analysis", get_asset)
    monkeypatch.setattr(main, "set_analysis_status", status)
    monkeypatch.setattr(main, "analyze_file", no_perception)
    monkeypatch.setattr("james_os.reel_vision.describe_media_asset", describe)
    monkeypatch.setattr(photo_subject, "read_one", read_one)
    await main._run_media_analysis(UUID(int=7), BRAND_B)
    assert seen == [("get", BRAND_B), ("describe", BRAND_B),
                    ("caption", BRAND_B, "https://s.test/x.jpg"), ("status", "done", BRAND_B)]


async def test_a_new_upload_is_stored_under_the_request_tenant(monkeypatch):
    from fastapi import BackgroundTasks

    from james_os import main

    stored, created, busted = [], [], []

    class Store:
        def save(self, tenant, data, name):
            stored.append(tenant)
            return f"https://s.test/{tenant}/x.jpg", f"supabase://{tenant}/x.jpg"

    class Conn:
        async def fetchrow(self, *a):
            return None

    class Ctx:
        async def __aenter__(self):
            return Conn()

        async def __aexit__(self, *a):
            return False

    async def create(**kw):
        created.append(kw)
        return {"id": str(UUID(int=9)), **kw}

    monkeypatch.setattr(main, "media_storage", lambda: Store())
    monkeypatch.setattr(main, "acquire", lambda *a, **k: Ctx())
    monkeypatch.setattr(main, "create_media", create)
    monkeypatch.setattr(hero_context, "invalidate_cache", lambda t=None: busted.append(t))

    class F:
        filename, content_type = "me.jpg", "image/jpeg"

        async def read(self):
            return _jpeg(1080, 1350, seed=11)

    db_module.set_request_tenant(BRAND_B)
    bg = BackgroundTasks()
    await main.media_upload(bg, file=F(), role="hero_photo", title="", platform="", notes="",
                            tags="")
    assert stored == [str(BRAND_B)], "a new upload is filed under its own brand, not Tenant Zero"
    prov = created[0]["provenance"]
    assert (prov["width"], prov["height"], prov["orientation"]) == (1080, 1350, "portrait")
    assert len(prov["sha256"]) == 64 and prov["quality"]["in_rotation"] is True
    assert busted == [BRAND_B]
    assert bg.tasks[0].args[1] == BRAND_B, "analysis is told the row's tenant"


# ── photo_subject.read_one + create_media ───────────────────────────────────


async def test_read_one_writes_the_caption_and_whether_a_person_is_in_it(monkeypatch):
    calls = []

    class Conn:
        async def execute(self, sql, *a):
            calls.append((" ".join(sql.split())[:40], a))

    class Ctx:
        def __init__(self, t):
            calls.append(("tenant", t))

        async def __aenter__(self):
            return Conn()

        async def __aexit__(self, *a):
            return False

    class Emb:
        model_name = "voyage"

        async def embed(self, texts):
            return [[0.1] * 4]

    async def describe(url):
        return {"caption": "A man on a fairway.", "subject": "golfer", "people": 1}

    monkeypatch.setattr(photo_subject, "acquire", lambda t=None, **k: Ctx(t))
    monkeypatch.setattr(photo_subject, "describe", describe)
    got = await photo_subject.read_one(UUID(int=3), "https://s/x.jpg", BRAND_B, emb=Emb())
    assert got == {"status": "read", "caption": "golfer · A man on a fairway.", "has_person": True}
    assert ("tenant", BRAND_B) in calls
    assert any("has_person" in c[0] for c in calls)

    async def blank(url):
        return {}
    monkeypatch.setattr(photo_subject, "describe", blank)
    assert (await photo_subject.read_one(UUID(int=3), "u", BRAND_B, emb=Emb()))["status"] == "unreadable"


async def test_create_media_writes_provenance_and_survives_a_database_without_072(monkeypatch):
    from james_os import media

    sqls = []

    class UndefinedColumnError(Exception):
        pass

    class Conn:
        async def fetchrow(self, sql, *a):
            sqls.append((sql, a))
            if "width" in sql:
                raise UndefinedColumnError("column width does not exist")
            return {"id": UUID(int=1), "tenant_id": BRAND_B, "role": "hero_photo"}

    class Ctx:
        async def __aenter__(self):
            return Conn()

        async def __aexit__(self, *a):
            return False

    monkeypatch.setattr(media, "acquire", lambda *a, **k: Ctx())
    out = await media.create_media(role="hero_photo", source_type="upload", uri="u",
                                   provenance={"width": 10, "height": 20, "quality": {"a": 1},
                                               "bogus": 1}, tenant_id=BRAND_B)
    assert out["id"] == str(UUID(int=1))
    assert "width" in sqls[0][0] and "bogus" not in sqls[0][0] and "::jsonb" in sqls[0][0]
    assert "width" not in sqls[1][0], "retried without the provenance columns"
    sqls.clear()
    await media.create_media(role="hero_photo", source_type="upload", uri="u")
    assert len(sqls) == 1 and "width" not in sqls[0][0], "older callers' INSERT is unchanged"
