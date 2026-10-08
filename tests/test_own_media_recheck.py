"""Second review round on own-post intake: the six re-checked findings.

No network, no database. What these hold:

  1. hero-led cards: a scraped photo with a person in it is never "the hero"
     (covered in test_own_media_library: scraped people never crowd the owner);
  2. a caption read that got NO answer (no key, 429/5xx, timeout) leaves the
     photo unread, so a later backfill retries it — only a real answer stamps;
  3. only a read that positively says PHOTOGRAPH becomes a hero photo: a text
     card whose roles were all dropped, a solid card or a collage does not, and
     an empty / refused read is a retry, not a photo;
  4. a paid verdict that leaves no picture row (undrawable, a template whose
     shape was already held) is remembered, so a re-send never pays again; a
     read that outlasts the timeout is not cancelled and a retry joins it; the
     legacy hashing runs in the background, not in line on the request;
  5. photo-mode batches never post a held-back flyer or screenshot;
  6. the designed card's Unsplash fallback anchors to the render's tenant.
"""

from __future__ import annotations

import asyncio
import inspect
import io
import json
from pathlib import Path
from uuid import UUID

import pytest
from PIL import Image

from james_os import db as db_module
from james_os import design_templates as dt
from james_os import hero_context, own_media, photo_subject, stock_photo
from james_os.config import settings
from james_os.design_cloner import _sanitize

pytestmark = pytest.mark.nodb

BRAND = UUID("945dbd37-4ec9-464e-9af5-459aad44eec9")


def _jpeg(w=1080, h=1350, seed=1) -> bytes:
    import numpy as np

    rng = np.random.default_rng(seed)
    small = rng.integers(0, 256, (h // 8 + 1, w // 8 + 1, 3), dtype=np.uint8)
    img = Image.fromarray(np.repeat(np.repeat(small, 8, 0), 8, 1)[:h, :w], "RGB")
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=90)
    return buf.getvalue()


@pytest.fixture(autouse=True)
def _clean():
    tok = db_module._request_tenant.set(None)
    own_media._READS.clear()
    own_media._LEGACY_TASKS.clear()
    yield
    own_media._READS.clear()
    own_media._LEGACY_TASKS.clear()
    db_module._request_tenant.reset(tok)


class _Ctx:
    def __init__(self, conn, tenants, t):
        self.conn = conn
        tenants.append(t)

    async def __aenter__(self):
        return self.conn

    async def __aexit__(self, *a):
        return False


def _acquire(monkeypatch, module, conn):
    tenants: list = []
    monkeypatch.setattr(module, "acquire", lambda t=None, **k: _Ctx(conn, tenants, t))
    return tenants


# ── 2. a caption read with no answer is retried, never buried ───────────────


class _CapConn:
    def __init__(self, rows=()):
        self.rows, self.sql = list(rows), []

    async def execute(self, sql, *a):
        self.sql.append(" ".join(sql.split()))

    async def fetch(self, sql, *a):
        return list(self.rows)


class _Emb:
    model_name = "voyage"

    async def embed(self, texts):
        return [[0.1] * 4]


async def test_describe_says_when_no_answer_was_obtained(monkeypatch):
    monkeypatch.setattr(settings, "openai_api_key", "", raising=False)
    assert (await photo_subject.describe("https://s.test/x.jpg")).get(photo_subject.NO_ANSWER)

    monkeypatch.setattr(settings, "openai_api_key", "sk-test", raising=False)

    class Boom:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, *a, **k):
            import httpx
            raise httpx.ReadTimeout("slow")
    monkeypatch.setattr(photo_subject.httpx, "AsyncClient", Boom)
    got = await photo_subject.describe("https://s.test/x.jpg")
    assert got.get(photo_subject.NO_ANSWER) == "ReadTimeout"

    class Refusal(Boom):
        async def post(self, *a, **k):
            class R:
                def raise_for_status(self):
                    pass

                def json(self):
                    return {"choices": [{"message": {"content": None}}]}
            return R()
    monkeypatch.setattr(photo_subject.httpx, "AsyncClient", Refusal)
    assert await photo_subject.describe("https://s.test/x.jpg") == {}, \
        "the model answered (nothing usable): a final answer"


@pytest.mark.parametrize("reason", ["no_key", "HTTPStatusError", "ReadTimeout"])
async def test_a_photo_whose_read_got_no_answer_stays_unread(monkeypatch, reason):
    conn = _CapConn()
    _acquire(monkeypatch, photo_subject, conn)

    async def no_answer(url):
        return {photo_subject.NO_ANSWER: reason}
    monkeypatch.setattr(photo_subject, "describe", no_answer)
    got = await photo_subject.read_one(UUID(int=7), "https://s.test/x.jpg", BRAND, emb=_Emb())
    assert got["status"] == "retry" and got["has_person"] is None
    assert not any("subject_read_at" in q for q in conn.sql), \
        "stamping it would hide the photo from every later backfill"


async def test_a_real_non_answer_is_still_stamped_once(monkeypatch):
    conn = _CapConn()
    _acquire(monkeypatch, photo_subject, conn)

    async def blank(url):
        return {}
    monkeypatch.setattr(photo_subject, "describe", blank)
    got = await photo_subject.read_one(UUID(int=7), "https://s.test/x.jpg", BRAND, emb=_Emb())
    assert got["status"] == "unreadable"
    assert any("subject_read_at = now()" in q for q in conn.sql)


async def test_backfill_stops_at_an_outage_and_leaves_the_rest_for_later(monkeypatch):
    rows = [{"id": UUID(int=i), "uri": f"https://s.test/{i}.jpg"} for i in range(5)]
    conn = _CapConn(rows)
    _acquire(monkeypatch, photo_subject, conn)
    monkeypatch.setattr(photo_subject, "_real_embedder", lambda: _Emb())
    calls = []

    async def rate_limited(url):
        calls.append(url)
        return {photo_subject.NO_ANSWER: "HTTPStatusError"}
    monkeypatch.setattr(photo_subject, "describe", rate_limited)
    out = await photo_subject.backfill(BRAND)
    assert out["deferred"] == "HTTPStatusError" and out["read"] == 0
    assert len(calls) == 1, "no point hammering a model that is not answering"
    assert not any("subject_read_at" in q for q in conn.sql)


# ── 3 + 4. the split, and never paying twice ────────────────────────────────

REQ = {"url": "https://scontent.cdninstagram.com/v/t51/777_888_n.jpg?sig=x",
       "origin": "own_instagram", "origin_url": "https://www.instagram.com/p/XYZ/",
       "origin_ref": "998877", "handle": "examplegolfclub", "platform": "instagram"}


def _box(y, h):
    return {"x": 0.08, "y": y, "w": 0.84, "h": h}


# Real shapes, through design_cloner's own sanitiser.
TEXT_CARD_ROLES_DROPPED = _sanitize({
    "kind": "graphic_card", "background": {"treatment": "solid"},
    "palette": {"bg": "#0a3d2a", "accent": "#e3c16f", "ink": "#ffffff"},
    "elements": [{"role": "not_a_role", "box": _box(0.1, 0.3)}],
    "design_notes": "a green quote card with centred serif type"})
SOLID_PHOTOFORWARD = _sanitize({
    "kind": "photo_forward", "background": {"treatment": "solid"},
    "palette": {"bg": "#202020", "accent": "#ff6600", "ink": "#ffffff"},
    "design_notes": "flat colour field"})
COLLAGE = _sanitize({
    "kind": "photo_forward", "background": {"treatment": "full_bleed_photo", "photo_boxes": [
        {"x": 0, "y": 0, "w": .5, "h": .5}, {"x": .5, "y": 0, "w": .5, "h": .5},
        {"x": 0, "y": .5, "w": .5, "h": .5}, {"x": .5, "y": .5, "w": .5, "h": .5}]},
    "palette": {"bg": "#111111", "accent": "#22aa44", "ink": "#ffffff"},
    "design_notes": "four photos in a 2x2 grid"})
EMPTY_REPLY = _sanitize({})
PHOTO = _sanitize({
    "kind": "photo_forward", "background": {"treatment": "full_bleed_photo"},
    "palette": {"bg": "#334455", "accent": "#aabbcc", "ink": "#ffffff"},
    "design_notes": "a fairway at dusk, no text"})
DESIGNED = _sanitize({
    "kind": "graphic_card", "background": {"treatment": "solid"},
    "palette": {"bg": "#0a3d2a", "accent": "#e3c16f", "ink": "#ffffff"},
    "elements": [{"role": "headline", "box": _box(0.10, 0.30), "size": "xxl"},
                 {"role": "subhead", "box": _box(0.60, 0.20)}],
    "decorations": [{"type": "bar", "box": _box(0.45, 0.01)}],
    "design_notes": "bold headline over a bar"})


class _Seams:
    """The import's seams. The library is STATEFUL: what record_seen writes,
    find_by_origin_ref / find_duplicate (the real ones) read back."""

    def __init__(self, monkeypatch, spec, *, outcome=None, held=None, image=None):
        self.calls: list[tuple] = []
        self.seen: list[dict] = []
        self.spec = spec
        self.image = image or _jpeg(seed=3)
        s = self

        class Conn:
            async def fetchrow(self, sql, *a):
                if "FROM own_media_seen" in sql:
                    for r in s.seen:
                        if ("origin_ref" in sql and (r["origin"], r["origin_ref"],
                                                    r["image_key"]) == a) or \
                                ("sha256 = $1" in sql and r["sha256"] == a[0]):
                            return {"body": r["body"]}
                return None

            async def fetchval(self, sql, *a):
                return None

            async def fetch(self, sql, *a):
                if "FROM own_media_seen" in sql:
                    return [{"seen_dhash": r["dhash"], "body": r["body"]} for r in s.seen]
                return []

            async def execute(self, sql, *a):
                if "INSERT INTO own_media_seen" in sql:
                    s.calls.append(("seen_write",))
                    if not any(r["sha256"] == a[3] for r in s.seen):
                        s.seen.append({"origin": a[0], "origin_ref": a[1], "image_key": a[2],
                                       "sha256": a[3], "dhash": a[4], "verdict": a[5],
                                       "body": a[6]})
        self.tenants = _acquire(monkeypatch, own_media, Conn())

        async def fetch(url):
            s.calls.append(("fetch",))
            return s.image

        async def backfill(tenant_id, **kw):
            return 0

        async def by_fp(tenant, fp):
            return held

        async def store(tenant, data, mime):
            s.calls.append(("store",))
            return f"https://store.test/{tenant}/x.jpg", f"supabase://{tenant}/x.jpg"

        async def insert(tenant, **kw):
            s.calls.append(("insert",))
            return "m-new", True

        async def save(tenant, spec, **kw):
            s.calls.append(("save",))
            kw["outcome"].update(outcome or {"template_id": "t-new", "created": True,
                                             "relabelled": False, "matched_kind": ""})
            return kw["outcome"]["template_id"]

        async def read_one(media_id, uri, tenant, **kw):
            return {"status": "read", "caption": "x", "has_person": False}

        monkeypatch.setattr(own_media, "fetch_image", fetch)
        monkeypatch.setattr(own_media, "backfill_legacy_hashes", backfill)
        monkeypatch.setattr(own_media, "_template_by_fingerprint", by_fp)
        monkeypatch.setattr(own_media, "_store", store)
        monkeypatch.setattr(own_media, "insert_photo", insert)
        monkeypatch.setattr(own_media, "_discard", lambda p: None)
        monkeypatch.setattr(dt, "save", save)
        monkeypatch.setattr("james_os.photo_subject.read_one", read_one)
        monkeypatch.setattr("james_os.hero_context.invalidate_cache", lambda t=None: None)

    async def extract(self, image, *, mime="image/jpeg"):
        self.calls.append(("extract",))
        return self.spec

    def n(self, kind):
        return sum(1 for c in self.calls if c[0] == kind)

    async def send(self, **over):
        return await own_media.import_own_media(
            BRAND, own_media.OwnMediaImport(**{**REQ, **over}), extract=self.extract)


@pytest.mark.parametrize("spec,name", [(TEXT_CARD_ROLES_DROPPED, "text card, roles dropped"),
                                       (SOLID_PHOTOFORWARD, "solid background"),
                                       (COLLAGE, "2x2 collage")])
async def test_a_read_that_is_not_a_photograph_never_becomes_a_hero_photo(monkeypatch, spec, name):
    w = _Seams(monkeypatch, spec)
    status, out = await w.send()
    assert status == 200 and out["verdict"] == "undrawable", (name, out)
    assert out["reason"] == "not_a_photo"
    assert w.n("insert") == 0 and w.n("store") == 0, name


async def test_an_empty_or_refused_read_is_a_retry_not_a_photo(monkeypatch):
    assert own_media._empty_read(EMPTY_REPLY)
    w = _Seams(monkeypatch, EMPTY_REPLY)
    status, out = await w.send()
    assert status == 503 and out == {"verdict": "retry", "reason": "vision_empty"}
    assert w.n("insert") == 0 and w.n("seen_write") == 0


async def test_a_real_photograph_is_still_a_photo(monkeypatch):
    assert not own_media._empty_read(PHOTO)
    w = _Seams(monkeypatch, PHOTO)
    status, out = await w.send()
    assert status == 200 and out["verdict"] == "photo" and w.n("insert") == 1
    assert w.n("seen_write") == 0, "the photo row itself names the picture"


async def test_an_undrawable_post_is_paid_for_once(monkeypatch):
    w = _Seams(monkeypatch, COLLAGE)
    status, first = await w.send()
    assert first["verdict"] == "undrawable" and w.n("extract") == 1
    # BM2 timed out / lost its ledger and sends the same image again
    status, again = await w.send()
    assert status == 200 and again["verdict"] == "undrawable" and again["seen"] is True
    assert again["match"] == "origin_ref"
    assert w.n("extract") == 1 and w.n("fetch") == 1, "answered before the download"
    # the same picture under another link (a re-signed CDN URL, another ref)
    status, other = await w.send(url="https://scontent.cdninstagram.com/v/t51/other_n.jpg",
                                 origin_ref="1")
    assert other["verdict"] == "undrawable" and other["match"] == "sha256"
    assert w.n("extract") == 1
    assert w.tenants and set(w.tenants) == {BRAND}


async def test_a_template_whose_shape_was_already_held_is_paid_for_once(monkeypatch):
    w = _Seams(monkeypatch, DESIGNED, held={"id": "t-own", "source_kind": "own"},
               outcome={"template_id": "t-own", "created": False, "relabelled": False,
                        "matched_kind": "own"})
    _s, first = await w.send()
    assert first["verdict"] == "template" and first["template_id"] == "t-own"
    _s, again = await w.send()
    assert again["verdict"] == "template" and again["template_id"] == "t-own"
    assert again["seen"] is True and w.n("extract") == 1 and w.n("save") == 1


async def test_a_new_template_needs_no_seen_record(monkeypatch):
    w = _Seams(monkeypatch, DESIGNED)
    _s, out = await w.send()
    assert out["verdict"] == "template" and out["created"] is True
    assert w.n("seen_write") == 0, "the template row's source_image names the picture"


async def test_a_reencoded_copy_of_an_undrawable_post_is_matched_by_dhash(monkeypatch):
    original = _jpeg(1600, 2000, seed=41)
    w = _Seams(monkeypatch, COLLAGE, image=original)
    await w.send()
    img = Image.open(io.BytesIO(original)).resize((1080, 1350))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=70)
    w.image = buf.getvalue()
    _s, out = await w.send(url="https://scontent.cdninstagram.com/v/t51/re_n.jpg", origin_ref="2")
    assert out["verdict"] == "undrawable" and out["match"] == "dhash"
    assert w.n("extract") == 1


async def test_a_read_that_outlasts_the_timeout_is_not_cancelled_and_a_retry_joins_it(
        monkeypatch):
    monkeypatch.setattr(own_media, "EXTRACT_TIMEOUT_S", 0.05)
    w = _Seams(monkeypatch, PHOTO)
    gate = asyncio.Event()
    finished = []

    async def slow(image, *, mime="image/jpeg"):
        w.calls.append(("extract",))
        await gate.wait()
        finished.append(True)
        return PHOTO
    w.extract = slow
    status, out = await w.send()
    assert status == 503 and out["reason"] == "vision_timeout"
    await asyncio.sleep(0)
    gate.set()
    await asyncio.sleep(0.01)
    assert finished == [True], "the billed read ran to completion"
    status, out = await w.send()
    assert status == 200 and out["verdict"] == "photo"
    assert w.n("extract") == 1, "the retry used the read already paid for"
    assert not own_media._READS, "a consumed read is not kept"


async def test_a_failed_read_is_not_reused(monkeypatch):
    w = _Seams(monkeypatch, PHOTO)
    n = {"c": 0}

    async def flaky(image, *, mime="image/jpeg"):
        n["c"] += 1
        if n["c"] == 1:
            raise RuntimeError("openai 500")
        return PHOTO
    w.extract = flaky
    assert (await w.send())[0] == 503
    assert (await w.send())[1]["verdict"] == "photo" and n["c"] == 2


async def test_the_legacy_hashing_runs_in_the_background_not_in_line(monkeypatch):
    """60 sequential downloads in line made the first import outlast BM2's
    120 s timeout. The import now waits at most LEGACY_WAIT_S and the hashing
    carries on for the next one."""
    monkeypatch.setattr(own_media, "LEGACY_WAIT_S", 0.02)
    gate = asyncio.Event()
    runs = []

    async def slow_backfill(tenant_id, **kw):
        runs.append(tenant_id)
        await gate.wait()
        return 0
    monkeypatch.setattr(own_media, "backfill_legacy_hashes", slow_backfill)

    class Conn:
        async def fetchrow(self, sql, *a):
            return None

        async def fetchval(self, sql, *a):
            return None

        async def fetch(self, sql, *a):
            return []
    _acquire(monkeypatch, own_media, Conn())
    dh = own_media.dhash(_jpeg(seed=8))
    try:
        # in line, this would wait on the hashing for as long as it takes
        assert await asyncio.wait_for(own_media.find_duplicate(BRAND, "a" * 64, dh), 1.0) is None
        # a second import joins the SAME running task rather than starting another
        assert await asyncio.wait_for(own_media.find_duplicate(BRAND, "b" * 64, dh), 1.0) is None
    except TimeoutError:
        gate.set()
        pytest.fail("the import waited in line for the legacy hashing")
    assert runs == [BRAND]
    task = next(iter(own_media._LEGACY_TASKS.values()))
    assert not task.done(), "a timeout must not cancel the hashing"
    gate.set()
    await asyncio.wait_for(task, 1)


def test_072_creates_the_seen_ledger_with_row_security():
    sql = (Path(__file__).resolve().parent.parent / "migrations"
           / "072_media_provenance.sql").read_text()
    assert "CREATE TABLE IF NOT EXISTS own_media_seen" in sql
    assert "own_media_seen (tenant_id, sha256)" in sql
    assert "ALTER TABLE own_media_seen FORCE ROW LEVEL SECURITY" in sql
    src = inspect.getsource(own_media.record_seen)
    cols = src.split("INSERT INTO own_media_seen (", 1)[1].split(")", 1)[0]
    for c in (x.strip().strip('"').strip() for x in cols.replace('"', " ").split(",")):
        assert f"  {c} " in sql, c


# ── 5. photo-mode batches skip held-back pictures ───────────────────────────


def test_rotation_urls_keeps_flyers_and_screenshots_out_while_anything_else_is_there():
    ctx = hero_context.HeroContext(
        description="", photo_count=3,
        photo_urls=["https://s.test/a.jpg", "https://s.test/flyer.jpg", "https://s.test/shot.png"],
        video_urls=[], held_back_urls=["https://s.test/flyer.jpg", "https://s.test/shot.png"])
    got = hero_context.rotation_urls(ctx)
    assert got == ["https://s.test/a.jpg"]
    # a 10-post batch on this library never attaches the flyer or the screenshot
    assert {got[i % len(got)] for i in range(10)} == {"https://s.test/a.jpg"}
    only_held = hero_context.HeroContext(
        description="", photo_count=1, photo_urls=["https://s.test/flyer.jpg"], video_urls=[],
        held_back_urls=["https://s.test/flyer.jpg"])
    assert hero_context.rotation_urls(only_held) == ["https://s.test/flyer.jpg"], \
        "the same fallback as the render pool: a library of only flyers still answers"
    assert hero_context.rotation_urls(None) == []


def test_both_photo_mode_batch_builders_rotate_through_rotation_urls():
    from james_os import main

    src = inspect.getsource(main)
    assert "hero_urls = list(ctx.photo_urls)" not in src
    assert src.count("hero_urls = rotation_urls(ctx)") == 2
    # ...and the third: the queued photo post picker filters with the same rule
    from james_os import autopilot_bulk
    picker = inspect.getsource(autopilot_bulk._attach_image_to_action)
    assert "if not held_back(m)] or real" in picker


async def test_the_queued_photo_post_picker_skips_held_back_photos_too(monkeypatch):
    """The third builder (autopilot's photo slots and /post/attach-image):
    a scraped flyer (has_text) or screenshot is never the picked photo while
    anything else is there — with the same fallback as rotation_urls."""
    from james_os import autopilot_bulk, media, photo_pick

    lib = [
        {"uri": "https://s.test/flyer.jpg", "source_type": "upload",
         "quality": {"has_text": True, "in_rotation": False}},
        {"uri": "https://s.test/shot.png", "source_type": "upload",
         "quality": json.dumps({"screenshot": True})},
        {"uri": "https://s.test/gen.jpg", "source_type": "generated", "quality": {}},
        {"uri": "https://s.test/a.jpg", "source_type": "upload", "quality": {}},
    ]
    pools = []

    async def listed(role, tenant_id=None, **kw):
        return list(lib)

    async def pick(urls, tenant_id):
        pools.append(list(urls))
        return urls[0]

    class Conn:
        async def execute(self, sql, *a):
            pass
    monkeypatch.setattr(media, "list_media", listed)
    monkeypatch.setattr(photo_pick, "pick_hero_url", pick)
    _acquire(monkeypatch, autopilot_bulk, Conn())
    got = await autopilot_bulk._attach_image_to_action(UUID(int=1), {}, "instagram", "x", BRAND)
    assert got == "https://s.test/a.jpg" and pools[-1] == ["https://s.test/a.jpg"]
    assert hero_context.rotation_urls(hero_context.HeroContext(
        description="", photo_count=3,
        photo_urls=["https://s.test/flyer.jpg", "https://s.test/shot.png", "https://s.test/a.jpg"],
        video_urls=[], held_back_urls=["https://s.test/flyer.jpg", "https://s.test/shot.png"],
    )) == pools[-1], "the same pool rotation_urls would give"

    lib[:] = lib[:2]
    await autopilot_bulk._attach_image_to_action(UUID(int=2), {}, "instagram", "x", BRAND)
    assert pools[-1] == ["https://s.test/flyer.jpg", "https://s.test/shot.png"], \
        "a library of only held-back photos still answers (rotation_urls' fallback)"


# ── 6. the designed card's Unsplash fallback knows its tenant ───────────────


def test_the_designed_card_stock_fallback_passes_the_tenant():
    from james_os import main

    src = inspect.getsource(main._generate_designed_post_image)
    call = src.split("await fetch_unsplash_hero_credited(", 1)[1].split(")", 1)[0]
    assert "tenant_id=tenant_id" in call, call


async def test_a_background_render_with_no_request_tenant_anchors_like_acquire(monkeypatch):
    async def profile(tid):
        return None
    monkeypatch.setattr("james_os.brands.get_brand_profile", profile)
    assert db_module._request_tenant.get() is None
    # no tenant anywhere → the tenant acquire() would read as (the default),
    # whose configured industry is its own — not the generic anchor
    assert await stock_photo.tenant_anchor(None) == await stock_photo.tenant_anchor(
        settings.default_tenant_id)
    if (settings.brand_industry or "").strip():
        assert await stock_photo.tenant_anchor(None) != stock_photo._GENERIC_ANCHOR
    seen = []

    async def profile2(tid):
        seen.append(tid)
        return {"identity": {"niche": "golf course", "niche_confirmed_at": "x"}}
    monkeypatch.setattr("james_os.brands.get_brand_profile", profile2)
    assert await stock_photo.tenant_anchor(BRAND) == "golf course"
    assert seen == [BRAND]


def test_the_seen_answer_repeats_the_first_verdict():
    body = json.dumps({"verdict": "undrawable", "layout_type": "collage", "width": 1, "height": 2})
    assert own_media._seen_answer({"body": body})["verdict"] == "undrawable"
    assert own_media._seen_answer({"body": "{}"}) is None
    assert own_media._seen_answer(None) is None


# Pre-deploy review: a 400 that names nothing (body {"message": "x"}) and a 404
# (model_not_found on chat/completions) are account-wide, no longer final —
# only an answer about the picture itself stamps it (see test_own_media_predeploy).
@pytest.mark.parametrize("code,final", [(400, False), (404, False), (415, True), (413, True),
                                        (429, False), (401, False), (503, False)])
async def test_a_refusal_of_this_picture_is_final_but_a_busy_api_is_not(monkeypatch, code, final):
    """A permanent answer about the picture (413/415) must stamp the photo, or
    one bad picture at the head of the queue stops every backfill."""
    import httpx
    monkeypatch.setattr(settings, "openai_api_key", "sk-test", raising=False)

    class Status:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, *a, **k):
            req = httpx.Request("POST", url)
            return httpx.Response(code, request=req, json={"error": {"message": "x"}})
    monkeypatch.setattr(photo_subject.httpx, "AsyncClient", Status)
    got = await photo_subject.describe("https://s.test/x.heic")
    if final:
        assert got == {}, f"HTTP {code} refuses this picture: a final answer"
    elif code == 400:  # a bare 400 names nothing: unread, but the backfill goes on
        assert got.get(photo_subject.NO_ANSWER) == photo_subject.PICTURE_RETRY + "http_400"
    else:
        assert got.get(photo_subject.NO_ANSWER) == f"http_{code}"


# ------------------------------------------- the older reference route's rows


def test_post_key_strips_the_link_and_names_the_instagram_post():
    assert own_media.post_key("https://www.instagram.com/p/AbC_1-x/?igsh=1#c") == \
        ("https://www.instagram.com/p/AbC_1-x", "AbC_1-x")
    assert own_media.post_key("https://instagram.com/somehandle/reel/Zz9yy00/")[1] == "Zz9yy00"
    assert own_media.post_key("https://www.facebook.com/x/posts/123/") == \
        ("https://www.facebook.com/x/posts/123", "")
    assert own_media.post_key("") == ("", "") and own_media.post_key("not a link") == ("", "")
    # a Facebook link identified only by its query never collapses to one canon
    for u in ("https://www.facebook.com/permalink.php?story_fbid=AAA&id=123",
              "https://m.facebook.com/story.php?story_fbid=AAA&id=123",
              "https://www.facebook.com/photo.php?fbid=111",
              "https://www.facebook.com/photo/?fbid=111&set=a.1",
              "https://www.facebook.com/watch/?v=999",
              "https://web.facebook.com/video.php?v=999",
              "https://fb.watch/?v=1"):
        assert own_media.post_key(u) == ("", ""), u


async def test_a_retry_that_omits_the_slide_is_never_taken_for_the_cover(monkeypatch):
    """BM2's retry pass sends no slide: it may be slide 2 of a post whose cover
    the older route learned. At most a deduped re-read, never a wrong duplicate."""
    assert own_media.OwnMediaImport(**REQ).slide is None
    w = _LegacySeams(monkeypatch, PHOTO)
    status, out = await w.send(origin_ref="XYZ:2")
    assert status == 200 and out["verdict"] == "photo"
    assert w.legacy_sql == [] and w.n("fetch") == 1


async def test_a_query_identified_facebook_post_is_not_matched_to_another(monkeypatch):
    """One legacy own row for permalink.php?story_fbid=AAA must not answer post
    BBB's cover: the post match is skipped and the cover is read."""
    w = _LegacySeams(monkeypatch, PHOTO)
    status, out = await w.send(
        slide=0, origin="own_facebook", platform="facebook", origin_ref="BBB",
        origin_url="https://www.facebook.com/permalink.php?story_fbid=BBB&id=123")
    assert status == 200 and out["verdict"] == "photo"
    assert w.legacy_sql == [] and w.n("extract") == 1


class _LegacySeams(_Seams):
    """A tenant whose own template was minted by the OLDER route from this post."""

    def __init__(self, monkeypatch, spec, legacy_id="t-legacy", **kw):
        super().__init__(monkeypatch, spec, **kw)
        s = self
        s.legacy_sql: list[tuple] = []

        class Conn:
            async def fetchrow(self, sql, *a):
                if "FROM design_templates" in sql and "source_url" in sql:
                    s.legacy_sql.append((sql, a))
                    return {"id": legacy_id} if legacy_id else None
                return None

            async def fetch(self, sql, *a):
                return []

            async def fetchval(self, sql, *a):
                return None

            async def execute(self, sql, *a):
                s.calls.append(("seen_write",))
        self.tenants = _acquire(monkeypatch, own_media, Conn())


async def test_a_post_the_older_route_already_learned_is_not_read_again(monkeypatch):
    w = _LegacySeams(monkeypatch, DESIGNED)
    status, out = await w.send(slide=0)
    assert status == 200 and out["verdict"] == "duplicate" and out["match"] == "post"
    assert out["template_id"] == "t-legacy"
    assert w.n("fetch") == 0 and w.n("extract") == 0 and w.n("save") == 0, \
        "answered before the download and the paid read"
    sql, params = w.legacy_sql[0]
    assert params == ("https://www.instagram.com/p/XYZ", "XYZ")
    assert "source_image" in sql, "rows this module minted are matched by origin_ref, not the link"


async def test_a_later_slide_of_that_post_is_still_taken_in(monkeypatch):
    """The older route read only the post's cover: slide 2 is a new picture."""
    w = _LegacySeams(monkeypatch, PHOTO)
    status, out = await w.send(slide=1, origin_ref="XYZ:1")
    assert status == 200 and out["verdict"] == "photo"
    assert w.legacy_sql == [] and w.n("extract") == 1


async def test_a_post_with_no_older_template_is_read_as_usual(monkeypatch):
    w = _LegacySeams(monkeypatch, DESIGNED, legacy_id=None)
    status, out = await w.send(slide=0)
    assert status == 200 and out["verdict"] == "template" and w.n("extract") == 1
    assert len(w.legacy_sql) == 1
