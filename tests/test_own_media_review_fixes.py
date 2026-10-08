"""Review fixes for SPEC4's BM1 half — each test fails without its fix.

No network, no database:

  1. train-soul trains a FACE model on the owner's uploads only, never on
     scraped own posts or generated pictures;
  2. a relabelled competitor/niche row carries the BRAND's post, picture,
     handle and engagement (and the import stores the brand's picture for it);
  3. limit=N still means "the brand's real photos" (own posts included); only
     likeness=True narrows to the owner's uploads;
  4. "keep the image" finds the kept photo even after it left the rotation pool;
  5. a large photo (blurry or sharp) does not beat the sharpness floor or the
     least-used rotation — size only breaks ties;
  6. an own-post import does not make the next render pay to re-describe the
     hero from the same owner photos;
  7. photos uploaded before migration 072 are hashed so a platform's re-encode
     of them is a duplicate, not a second paid read and a twin in rotation;
  8. render-pool processing is off the event loop and a pool re-choice reuses
     the processed bytes it already holds.
"""

from __future__ import annotations

import asyncio
import io
from uuid import UUID

import numpy as np
import pytest
from PIL import Image, ImageFilter

from james_os import db as db_module
from james_os import design_templates as dt
from james_os import hero_context, own_media, photo_pick

pytestmark = pytest.mark.nodb

BRAND = UUID("945dbd37-4ec9-464e-9af5-459aad44eec9")


def _jpeg(w, h, seed=1, *, blur=0) -> bytes:
    rng = np.random.default_rng(seed)
    small = rng.integers(0, 256, (h // 8 + 1, w // 8 + 1, 3), dtype=np.uint8)
    img = Image.fromarray(np.repeat(np.repeat(small, 8, 0), 8, 1)[:h, :w], "RGB")
    if blur:
        img = img.filter(ImageFilter.GaussianBlur(blur))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=90)
    return buf.getvalue()


@pytest.fixture(autouse=True)
def _clean():
    def wipe():
        hero_context._CACHE.clear()
        hero_context._BYTES_CACHE.clear()
        hero_context._BYTES_AT.clear()
        hero_context._URL_BYTES.clear()
        hero_context._DESCRIBED.clear()
        photo_pick._REF_MEASURE.clear()
        own_media._LEGACY_SKIP.clear()
    wipe()
    tok = db_module._request_tenant.set(None)
    yield
    db_module._request_tenant.reset(tok)
    wipe()


def _row(uri, origin="owner_upload", source_type="upload", created="2026-10-01T00:00:00+00:00"):
    r = {"uri": uri, "source_type": source_type, "created_at": created, "quality": {},
         "tags": [] if origin == "owner_upload" else ["own-post", f"origin:{origin}"]}
    if origin != "owner_upload":
        r["origin"] = origin
    return r


class _Http:
    bodies: dict = {}
    gets: list = []

    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def get(self, url, **k):
        _Http.gets.append(url)
        body = _Http.bodies[url]

        class R:
            content = body

            def raise_for_status(self):
                pass
        return R()


@pytest.fixture
def lib(monkeypatch):
    """A brand library of N rows, every download faked, the describe recorded."""
    state = {"rows": [], "describe": 0}

    async def list_media(role="", tenant_id=None):
        return list(state["rows"]) if role == "hero_photo" else []

    async def describe(urls):
        state["describe"] += 1
        return {"description": "a woman in her 30s"}

    async def public(url, *, allow_http=False):
        return True

    monkeypatch.setattr(hero_context, "list_media", list_media)
    monkeypatch.setattr(hero_context, "describe_hero_from_photos", describe)
    monkeypatch.setattr("james_os.netguard.url_is_public", public)
    monkeypatch.setattr(hero_context.httpx, "AsyncClient", _Http)
    _Http.bodies, _Http.gets = {}, []
    return state


def _fill(state, rows, body=None):
    state["rows"] = rows
    b = body or _jpeg(1080, 1350, seed=3)
    for r in rows:
        _Http.bodies[r["uri"]] = b


# ── 1. train-soul ───────────────────────────────────────────────────────────


@pytest.fixture
def soul(monkeypatch):
    from james_os import higgsfield_souls

    sent = []

    async def create_reference(name, image_urls):
        sent.append(list(image_urls))
        return {"reference_id": "ref-1", "status": "queued", "trained_on": len(image_urls)}

    monkeypatch.setattr(higgsfield_souls, "configured", lambda: True)
    monkeypatch.setattr(higgsfield_souls, "create_reference", create_reference)
    return sent


async def test_train_soul_never_trains_the_face_model_on_scraped_or_generated_photos(
        monkeypatch, soul):
    from james_os import media, templates_api

    rows = ([_row(f"https://s.test/ig{i}.jpg", "own_instagram") for i in range(8)]
            + [_row("https://s.test/gen.jpg", source_type="generated")]
            + [_row(f"https://s.test/up{i}.jpg") for i in range(5)])

    async def list_media(role="", tenant_id=None):
        return rows
    monkeypatch.setattr(media, "list_media", list_media)
    out = await templates_api.higgsfield_train_soul(templates_api.TrainSoulRequest(name="Hero"))
    assert out["ok"] is True
    assert soul == [[f"https://s.test/up{i}.jpg" for i in range(5)]]


async def test_train_soul_counts_only_owner_uploads_toward_the_minimum(monkeypatch, soul):
    from james_os import media, templates_api

    rows = ([_row(f"https://s.test/ig{i}.jpg", "own_instagram") for i in range(50)]
            + [_row("https://s.test/up0.jpg")])

    async def list_media(role="", tenant_id=None):
        return rows
    monkeypatch.setattr(media, "list_media", list_media)
    out = await templates_api.higgsfield_train_soul(templates_api.TrainSoulRequest())
    assert out["ok"] is False and "found 1" in out["error"]
    assert soul == [], "50 scraped posts must not start a paid face-training run"


# ── 2. the relabel carries the brand's identity ─────────────────────────────

SPEC = {"status": "ok", "kind": "graphic_card", "background": {"treatment": "solid"},
        "elements": [{"role": "headline", "box": {"x": .1, "y": .1, "w": .8, "h": .3}}]}


class _SaveConn:
    def __init__(self, hit):
        self.hit, self.calls = hit, []

    async def fetchrow(self, sql, *a):
        self.calls.append((sql, a))
        return self.hit

    async def fetchval(self, sql, *a):
        self.calls.append((sql, a))
        return True if sql.lstrip().startswith("UPDATE") else None


def _acquire(monkeypatch, module, conn):
    class _Ctx:
        async def __aenter__(self):
            return conn

        async def __aexit__(self, *a):
            return False
    monkeypatch.setattr(module, "acquire", lambda *a, **k: _Ctx())


async def test_a_relabelled_row_shows_the_brands_post_not_the_rivals(monkeypatch):
    import json

    conn = _SaveConn({"id": "t-rival", "source_kind": "competitor", "house_layout_id": None})
    _acquire(monkeypatch, dt, conn)
    spec = {**SPEC, "source_image": {"sha256": "ab" * 32, "dhash": "0f0f0f0f0f0f0f0f",
                                     "origin_ref": "1789", "image_key": "x.jpg"}}
    out: dict = {}
    await dt.save(BRAND, spec, source_kind="own", source_url="https://www.instagram.com/p/MINE/",
                  source_image_uri="https://store.test/brand/own-post.jpg",
                  source_handle="mybrandgolf", source_platform="instagram",
                  source_engagement=0.07, outcome=out)
    assert out["relabelled"] is True
    sql, args = next(c for c in conn.calls if c[0].lstrip().startswith("UPDATE"))
    for col in ("source_url", "source_image_uri", "source_handle", "source_platform",
                "source_engagement", "source_post_id", "spec = spec ||"):
        assert col in sql, col
    assert "https://www.instagram.com/p/MINE/" in args
    assert "https://store.test/brand/own-post.jpg" in args
    assert "mybrandgolf" in args and "instagram" in args and 0.07 in args
    merged = json.loads(args[-1])
    assert merged["relabelled_from"] == "competitor"
    assert merged["source_image"]["origin_ref"] == "1789", \
        "the brand's image identity rides on the spec so a re-import is recognised"


class _World:
    def __init__(self, monkeypatch, *, held, outcome):
        self.calls = []
        w = self

        async def by_fp(tenant, fp):
            return held

        async def store(tenant, data, mime):
            w.calls.append(("store", tenant))
            return f"https://store.test/{tenant}/own.jpg", f"supabase://{tenant}/own.jpg"

        async def save(tenant, spec, **kw):
            w.calls.append(("save", kw["source_image_uri"]))
            kw["outcome"].update(outcome)
            return outcome["template_id"]

        def discard(path):
            w.calls.append(("discard", path))

        monkeypatch.setattr(own_media, "_template_by_fingerprint", by_fp)
        monkeypatch.setattr(own_media, "_store", store)
        monkeypatch.setattr(own_media, "_discard", discard)
        monkeypatch.setattr(dt, "save", save)


def _keep(monkeypatch):
    from james_os.house_layouts import family_key
    from james_os.layout_types import classify

    req = own_media.OwnMediaImport(url="https://cdn.test/a.jpg", origin="own_instagram",
                                   origin_url="https://www.instagram.com/p/MINE/",
                                   origin_ref="1789", handle="mybrandgolf")
    m = own_media.measure(_jpeg(1080, 1350, seed=9))
    return own_media._keep_template(BRAND, req, dict(SPEC), m, "instagram", "a.jpg",
                                    dt=dt, classify=classify, family_key=family_key)


async def test_an_import_that_relabels_stores_the_brands_own_picture(monkeypatch):
    w = _World(monkeypatch, held={"id": "t-rival", "source_kind": "competitor"},
               outcome={"template_id": "t-rival", "matched_kind": "competitor",
                        "relabelled": True, "created": False})
    out = await _keep(monkeypatch)
    assert ("store", BRAND) in w.calls
    assert ("save", f"https://store.test/{BRAND}/own.jpg") in w.calls
    assert out["relabelled"] is True and out["stored_image_uri"].endswith("/own.jpg")
    assert not any(c[0] == "discard" for c in w.calls)


async def test_a_shape_that_stays_someone_elses_discards_the_unused_copy(monkeypatch):
    w = _World(monkeypatch, held={"id": "t-adopted", "source_kind": "niche"},
               outcome={"template_id": "t-adopted", "matched_kind": "niche",
                        "relabelled": False, "created": False})
    out = await _keep(monkeypatch)
    assert ("discard", f"supabase://{BRAND}/own.jpg") in w.calls
    assert out["stored_image_uri"] == ""


async def test_a_shape_the_brand_already_owns_stores_nothing(monkeypatch):
    w = _World(monkeypatch, held={"id": "t-own", "source_kind": "own"},
               outcome={"template_id": "t-own", "matched_kind": "own",
                        "relabelled": False, "created": False})
    await _keep(monkeypatch)
    assert not any(c[0] == "store" for c in w.calls)


# ── 3. limit=N is "real photos"; likeness=True is "the owner's uploads" ─────


async def test_a_library_of_only_own_posts_still_has_photos(lib):
    _fill(lib, [_row(f"https://s.test/ig{i}.jpg", "own_instagram") for i in range(40)])
    assert await hero_context.get_hero_photo_files(BRAND, limit=1), \
        "competitor_kickoff._has_hero_photos must see the brand's own photos"
    assert len(await hero_context.get_hero_photo_files(BRAND)) == 3, \
        "theme propose / palette read the brand's own photos"
    assert await hero_context.get_hero_photo_files(BRAND, likeness=True) == [], \
        "but never as the hero's face"


async def test_competitor_kickoff_sees_photos_for_an_own_post_library(lib):
    from james_os import competitor_kickoff

    _fill(lib, [_row(f"https://s.test/ig{i}.jpg", "own_instagram") for i in range(4)])
    assert await competitor_kickoff._has_hero_photos(BRAND) is True


def test_the_true_likeness_callers_ask_for_likeness():
    import inspect

    from james_os import main, story_video, video_pipeline

    assert inspect.getsource(story_video).count("_hero_files(likeness=True)") == 3
    assert "get_hero_photo_files(tenant_id, likeness=True)" in inspect.getsource(video_pipeline)
    assert "get_hero_photo_files(tenant_id=_img_tid, likeness=True)" in inspect.getsource(main)


async def test_likeness_refs_are_the_owners_uploads_only(lib):
    _fill(lib, [_row("https://s.test/ig.jpg", "own_instagram"), _row("https://s.test/up.jpg")])
    got = [u for u, _b in await hero_context.get_hero_photo_files(BRAND, likeness=True)]
    assert got == ["https://s.test/up.jpg"]


# ── 4. keep the image ───────────────────────────────────────────────────────


async def test_a_kept_photo_is_found_after_it_left_the_rotation_pool(monkeypatch, lib):
    from james_os import template_clone

    monkeypatch.setattr(hero_context, "_DESIGNED_POOL_MAX", 4)
    _fill(lib, [_row(f"https://s.test/p{i}.jpg", "own_instagram") for i in range(12)])
    counts: dict = {}

    async def use_counts(tenant_id):
        return dict(counts)
    monkeypatch.setattr(photo_pick, "_use_counts", use_counts)

    pool1 = [u for u, _b in await hero_context.get_hero_photo_files(BRAND, limit=None)]
    kept = pool1[0]
    counts[kept] = 1                             # the card used it
    hero_context.invalidate_cache(BRAND)         # an import arrives
    pool2 = [u for u, _b in await hero_context.get_hero_photo_files(BRAND, limit=None)]
    assert kept not in pool2, "precondition: the used photo left the least-used pool"
    assert await template_clone._hero_by_key(BRAND, kept) == _Http.bodies[kept]


async def test_library_photo_fetches_only_this_tenants_own_photos(lib):
    _fill(lib, [_row("https://s.test/mine.jpg")])
    _Http.bodies["https://evil.test/x.jpg"] = _jpeg(1080, 1350, seed=99)
    assert await hero_context.library_photo(BRAND, "https://evil.test/x.jpg") is None
    assert await hero_context.library_photo(BRAND, "abc123") is None
    got = await hero_context.library_photo(BRAND, "https://s.test/mine.jpg")
    assert got and got[0] == "https://s.test/mine.jpg"


def test_the_designed_redo_falls_back_to_the_library_for_a_forced_photo():
    import inspect

    from james_os import main

    src = inspect.getsource(main._generate_designed_post_image)
    assert "library_photo(tenant_id, force_photo)" in src


# ── 5. size is a tie-break, not a pre-filter ────────────────────────────────


@pytest.fixture
def ledger(monkeypatch):
    counts: dict = {}

    async def use_counts(tenant_id):
        return dict(counts)
    monkeypatch.setattr(photo_pick, "_use_counts", use_counts)
    return counts


async def test_one_large_blurry_photo_does_not_beat_twelve_sharp_ones(ledger):
    small = [(f"https://s/small{i}", _jpeg(960, 1280, seed=20 + i)) for i in range(12)]
    big_soft = ("https://s/big", _jpeg(1200, 1600, seed=5, blur=12))
    assert photo_pick.sharpness_score(big_soft[1]) < photo_pick._SHARP_FLOOR
    picks = []
    for _ in range(10):
        k, _b = await photo_pick.pick_hero_bytes(small + [big_soft])
        picks.append(k)
        ledger[k] = ledger.get(k, 0) + 1
    assert "https://s/big" not in picks
    assert len(set(picks)) == 10, "the rotation still reaches every sharp photo"


async def test_one_large_sharp_photo_does_not_win_every_pick(ledger):
    small = [(f"https://s/small{i}", _jpeg(960, 1280, seed=40 + i)) for i in range(6)]
    big = ("https://s/big", _jpeg(1200, 1600, seed=6))
    picks = []
    for _ in range(7):
        k, _b = await photo_pick.pick_hero_bytes(small + [big])
        picks.append(k)
        ledger[k] = ledger.get(k, 0) + 1
    assert picks[0] == "https://s/big", "on a tie the big-enough photo wins"
    assert picks.count("https://s/big") == 1 and len(set(picks)) == 7


async def test_the_url_picker_ranks_size_inside_the_rotation_too(monkeypatch, ledger):
    urls = ["https://s/a", "https://s/b", "https://s/big"]
    for u in urls:
        photo_pick._URL_SHARPNESS[u] = 100.0
        photo_pick._URL_SHAPE[u] = (False, 1440 if u.endswith("big") else 800)
    try:
        assert await photo_pick.pick_hero_url(urls) == "https://s/big"
        ledger["https://s/big"] = 1
        assert await photo_pick.pick_hero_url(urls) in {"https://s/a", "https://s/b"}
    finally:
        for u in urls:
            photo_pick._URL_SHARPNESS.pop(u, None)
            photo_pick._URL_SHAPE.pop(u, None)


# ── 6. an import does not re-buy the hero description ───────────────────────


async def test_an_own_post_import_does_not_pay_to_redescribe_the_same_owner_photos(lib):
    rows = [_row("https://s.test/up1.jpg"), _row("https://s.test/up2.jpg")]
    _fill(lib, rows)
    await hero_context.get_hero_context(BRAND)
    assert lib["describe"] == 1
    for i in range(3):                               # three imported own posts
        rows.append(_row(f"https://s.test/ig{i}.jpg", "own_instagram"))
        hero_context.invalidate_cache(BRAND)
        ctx = await hero_context.get_hero_context(BRAND)
        assert f"https://s.test/ig{i}.jpg" in ctx.photo_urls, "the library still refreshes"
    await asyncio.gather(*(hero_context.get_hero_context(BRAND) for _ in range(5)))
    assert lib["describe"] == 1
    assert ctx.description == "a woman in her 30s"

    rows.append(_row("https://s.test/up3.jpg"))      # a NEW owner upload re-describes
    hero_context.invalidate_cache(BRAND)
    await hero_context.get_hero_context(BRAND)
    assert lib["describe"] == 2


# ── 7. legacy uploads are hashed for the near-duplicate check ───────────────


class _LegacyConn:
    def __init__(self, rows):
        self.rows, self.sql = rows, []

    async def fetch(self, sql, *a):
        self.sql.append(sql)
        if "dhash IS NULL" in sql:
            return [{"id": r["id"], "uri": r["uri"]} for r in self.rows if r["dhash"] is None]
        if "dhash IS NOT NULL" in sql:
            return [{"id": r["id"], "dhash": r["dhash"], "width": 1, "height": 1}
                    for r in self.rows if r["dhash"]]
        return []

    async def fetchrow(self, sql, *a):
        return None

    async def fetchval(self, sql, *a):
        return None

    async def execute(self, sql, *a):
        self.sql.append(sql)
        if sql.startswith("UPDATE media_assets SET dhash"):
            for r in self.rows:
                if r["id"] == a[0] and r["dhash"] is None:
                    r["dhash"], r["sha256"] = a[1], a[2]


async def test_an_instagram_copy_of_a_pre_072_upload_is_a_duplicate(monkeypatch):
    original = _jpeg(1600, 2000, seed=77)
    img = Image.open(io.BytesIO(original)).resize((1080, 1350))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=70)
    ig_copy = buf.getvalue()

    rows = [{"id": "m-legacy", "uri": "https://s.test/legacy.jpg", "dhash": None, "sha256": None}]
    conn = _LegacyConn(rows)
    _acquire(monkeypatch, own_media, conn)
    fetched = []

    async def fetch(url):
        fetched.append(url)
        return original
    monkeypatch.setattr(own_media, "fetch_image", fetch)

    m = own_media.measure(ig_copy)
    assert m["sha256"] != own_media.measure(original)["sha256"]
    got = await own_media.find_duplicate(BRAND, m["sha256"], m["dhash"])
    assert got == {"media_id": "m-legacy", "width": 1, "height": 1, "match": "dhash"}
    assert rows[0]["dhash"] and len(rows[0]["sha256"]) == 64, "the hash is written back"

    await own_media.find_duplicate(BRAND, m["sha256"], m["dhash"])
    assert fetched == ["https://s.test/legacy.jpg"], "a legacy photo is fetched once, ever"


async def test_a_dead_legacy_link_is_marked_and_a_network_fault_is_left_for_later(monkeypatch):
    rows = [{"id": "m-gone", "uri": "https://s.test/gone.jpg", "dhash": None, "sha256": None},
            {"id": "m-flaky", "uri": "https://s.test/flaky.jpg", "dhash": None, "sha256": None}]
    _acquire(monkeypatch, own_media, _LegacyConn(rows))

    async def fetch(url):
        if "gone" in url:
            raise own_media.Refused(422, "fetch_404")
        raise own_media.Retryable("fetch_error:ConnectError")
    monkeypatch.setattr(own_media, "fetch_image", fetch)
    assert await own_media.backfill_legacy_hashes(BRAND) == 1
    assert rows[0]["dhash"] == "" and rows[1]["dhash"] is None
    assert ("945dbd37-4ec9-464e-9af5-459aad44eec9", "m-flaky") in own_media._LEGACY_SKIP


# ── 8. pool processing off the loop, processed bytes reused ─────────────────


async def test_a_pool_rechoice_reuses_processed_bytes_and_works_off_the_loop(monkeypatch, lib):
    _fill(lib, [_row(f"https://s.test/p{i}.jpg") for i in range(5)])
    threaded = []
    real_to_thread = asyncio.to_thread

    async def to_thread(fn, *a, **k):
        threaded.append(getattr(fn, "__name__", ""))
        return await real_to_thread(fn, *a, **k)
    monkeypatch.setattr(asyncio, "to_thread", to_thread)

    await hero_context.get_hero_photo_files(BRAND, limit=None)
    assert threaded.count("_render_image") == 5, "decode/resize/encode runs in a thread"
    assert len(_Http.gets) == 5
    hero_context.invalidate_cache(BRAND)              # an import, or the 15-minute TTL
    hero_context._BYTES_AT.clear()
    await hero_context.get_hero_photo_files(BRAND, limit=None)
    assert len(_Http.gets) == 5, "photos already held are not downloaded again"
    assert threaded.count("_render_image") == 5, "nor re-encoded"


async def test_a_pick_measures_each_library_photo_once(monkeypatch, ledger):
    refs = [(f"https://s/p{i}", _jpeg(1080, 1350, seed=60 + i)) for i in range(4)]
    n = {"sharp": 0}
    real = photo_pick.sharpness_score

    def counted(b):
        n["sharp"] += 1
        return real(b)
    monkeypatch.setattr(photo_pick, "sharpness_score", counted)
    for _ in range(3):
        await photo_pick.pick_hero_bytes(refs, BRAND)
    assert n["sharp"] == 4
