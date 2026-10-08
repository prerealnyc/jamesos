"""The brand's own posted images: POST /v1/own-media/import and its engine.

No network, no database: the /v1 router is mounted alone with the service-key
dependency overridden to a fixed tenant, and every seam that would touch the
outside (the download, the vision read, storage, the library queries) is a
fake that RECORDS the tenant it was called with. The rules under test:

  * a read that could not happen (no key, failed, timeout) answers 503 'retry'
    and writes NOTHING — no stored object, no row, no template;
  * a designed post becomes the brand's OWN template; a plain photo becomes a
    hero_photo with provenance and a caption; an undrawable design is counted;
  * a picture already held (sha256 / dHash / origin_ref) is never paid for;
  * only the brand's own account's images, never BM's own renders, never a
    private address;
  * every write lands under the REQUEST tenant.
"""

from __future__ import annotations

import io
import json
from uuid import UUID

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from PIL import Image

from james_os import api_v1, own_media
from james_os import design_templates as dt

pytestmark = pytest.mark.nodb

TENANT = UUID("6febd4bb-f062-4f53-936b-802d648fc060")
MID = "11111111-2222-3333-4444-555555555555"
BODY = {"url": "https://scontent.cdninstagram.com/v/t51/123_456_n.jpg?sig=abc",
        "origin": "own_instagram", "origin_url": "https://www.instagram.com/p/ABC123/",
        "origin_ref": "3311223344", "handle": "examplegolfclub", "platform": "instagram",
        "taken_at": "2026-09-01T12:00:00Z", "rights": "own_account",
        "published_by_bm": False}


def _jpeg(w=1080, h=1350, seed=1, *, exif_orientation: int | None = None) -> bytes:
    import numpy as np

    # Blocky noise: sharp enough to pass the blur gate, distinct per seed.
    rng = np.random.default_rng(seed)
    small = rng.integers(0, 256, (h // 8 + 1, w // 8 + 1, 3), dtype=np.uint8)
    big = np.repeat(np.repeat(small, 8, axis=0), 8, axis=1)[:h, :w]
    img = Image.fromarray(big, "RGB")
    buf = io.BytesIO()
    if exif_orientation:
        ex = Image.Exif()
        ex[0x0112] = exif_orientation
        img.save(buf, "JPEG", quality=90, exif=ex.tobytes())
    else:
        img.save(buf, "JPEG", quality=90)
    return buf.getvalue()


PHOTO = _jpeg()


def _el(role, y, h, size="lg"):
    return {"role": role, "box": {"x": 0.08, "y": y, "w": 0.84, "h": h}, "align": "left",
            "size": size, "weight": "bold", "case": "none", "color": "#ffffff"}


DESIGNED = {"status": "ok", "kind": "graphic_card",
            "background": {"treatment": "solid", "scrim": "none", "photo_box": None},
            "elements": [_el("headline", 0.10, 0.30, "xxl"), _el("subhead", 0.60, 0.20)],
            "decorations": [{"type": "bar", "box": {"x": 0.08, "y": 0.45, "w": 0.2, "h": 0.01}}]}
PLAIN = {"status": "ok", "kind": "photo_forward",
         "background": {"treatment": "full_bleed_photo"},
         "elements": [_el("headline", 0.80, 0.08)], "decorations": []}
UNDRAWABLE = {"status": "ok", "kind": "graphic_card",
              "background": {"treatment": "solid", "photo_box": None},
              "elements": [_el("kicker", 0.02, 0.04, "sm"), _el("headline", 0.07, 0.08)],
              "decorations": [{"type": "bar", "box": {"x": 0.1, "y": 0.9, "w": 0.3, "h": 0.01}}]}


def _app() -> FastAPI:
    app = FastAPI()
    app.include_router(api_v1.router)
    app.dependency_overrides[api_v1.require_service] = lambda: TENANT
    return app


async def _post(body=None):
    async with AsyncClient(transport=ASGITransport(app=_app()), base_url="http://t") as c:
        return await c.post("/v1/own-media/import", json=body or BODY)


class World:
    """Every outside seam, faked and recorded."""

    def __init__(self, monkeypatch, *, spec=DESIGNED, image=PHOTO, seen=None, dup=None,
                 fp_exists=None, insert_dup=None):
        self.calls: list[tuple] = []
        self.spec = spec
        w = self

        async def fetch(url):
            w.calls.append(("fetch", url))
            if isinstance(image, Exception):
                raise image
            return image

        async def by_ref(tenant, origin, ref, key):
            w.calls.append(("by_ref", tenant, origin, ref, key))
            if isinstance(seen, Exception):
                raise seen
            return seen

        async def find_dup(tenant, sha, dh):
            w.calls.append(("find_dup", tenant, sha, dh))
            return dup

        async def by_fp(tenant, fp):
            w.calls.append(("by_fp", tenant))
            return fp_exists

        async def store(tenant, data, mime):
            w.calls.append(("store", tenant, len(data), mime))
            return f"https://store.test/{tenant}/abc.jpg", f"supabase://{tenant}/abc.jpg"

        async def insert(tenant, **kw):
            w.calls.append(("insert", tenant, kw))
            return (insert_dup, False) if insert_dup else (MID, True)

        async def save(tenant, spec, **kw):
            w.calls.append(("save", tenant, spec, kw))
            kw["outcome"].update(template_id="t-1", matched_kind="", relabelled=False,
                                 created=True)
            return "t-1"

        async def read_one(media_id, uri, tenant, **kw):
            w.calls.append(("caption", tenant, str(media_id), uri))
            return {"status": "read", "caption": "a golf green at dusk", "has_person": False}

        def bust(tenant=None):
            w.calls.append(("bust", tenant))

        def discard(path):
            w.calls.append(("discard", path))

        async def record_seen(tenant, req, m, key, body):
            w.calls.append(("seen", tenant, key, m["sha256"], dict(body)))

        monkeypatch.setattr(own_media, "fetch_image", fetch)
        monkeypatch.setattr(own_media, "find_by_origin_ref", by_ref)

        async def no_legacy(tenant, origin_url):
            return None
        monkeypatch.setattr(own_media, "_legacy_own_template", no_legacy)
        monkeypatch.setattr(own_media, "find_duplicate", find_dup)
        monkeypatch.setattr(own_media, "_template_by_fingerprint", by_fp)
        monkeypatch.setattr(own_media, "_store", store)
        monkeypatch.setattr(own_media, "insert_photo", insert)
        monkeypatch.setattr(own_media, "_discard", discard)
        monkeypatch.setattr(own_media, "record_seen", record_seen)
        own_media._READS.clear()
        monkeypatch.setattr(dt, "save", save)
        monkeypatch.setattr("james_os.photo_subject.read_one", read_one)
        monkeypatch.setattr("james_os.hero_context.invalidate_cache", bust)

        async def extract(image, *, mime="image/jpeg"):
            w.calls.append(("extract", mime))
            if isinstance(w.spec, Exception):
                raise w.spec
            return w.spec
        monkeypatch.setattr("james_os.design_cloner.extract_template_spec", extract)

    def kinds(self):
        return [c[0] for c in self.calls]

    def tenants(self):
        return {c[1] for c in self.calls if c[0] not in ("fetch", "extract", "discard")}


WRITES = {"store", "insert", "save", "caption", "bust"}


# ── the split ───────────────────────────────────────────────────────────────


@pytest.mark.parametrize("spec", [{"status": "no_key"}, {"status": "failed"},
                                  RuntimeError("openai 500")])
async def test_a_read_that_could_not_happen_is_503_retry_and_writes_nothing(monkeypatch, spec):
    w = World(monkeypatch, spec=spec)
    r = await _post()
    assert r.status_code == 503, r.text
    assert r.json()["verdict"] == "retry"
    assert not WRITES & set(w.kinds()), w.kinds()
    assert "extract" in w.kinds()


async def test_a_designed_post_becomes_the_brands_OWN_template(monkeypatch):
    w = World(monkeypatch, spec=DESIGNED)
    r = await _post()
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["verdict"] == "template" and out["template_id"] == "t-1"
    assert out["layout_type"] and "family_key" in out
    assert (out["width"], out["height"]) == (1080, 1350)
    save = next(c for c in w.calls if c[0] == "save")
    _, tenant, spec, kw = save
    assert tenant == TENANT
    assert kw["source_kind"] == "own"
    assert kw["source_url"] == BODY["origin_url"], "the permalink, not the expiring CDN link"
    assert kw["source_handle"] == "examplegolfclub" and kw["source_platform"] == "instagram"
    assert kw["source_image_uri"].startswith(f"https://store.test/{TENANT}/")
    # the picture's identity rides on the spec so a re-scrape is recognised
    assert spec["source_image"]["sha256"] and spec["source_image"]["origin_ref"] == "3311223344"
    assert dt.fingerprint(spec) == dt.fingerprint(DESIGNED), "provenance must not change the shape"
    assert "insert" not in w.kinds(), "a template is not also a hero photo"
    assert w.tenants() == {TENANT}


async def test_a_shape_already_held_stores_no_second_copy(monkeypatch):
    w = World(monkeypatch, spec=DESIGNED, fp_exists={"id": "t-old", "source_kind": "own"})
    r = await _post()
    assert r.json()["verdict"] == "template"
    assert "store" not in w.kinds()
    assert next(c for c in w.calls if c[0] == "save")[3]["source_image_uri"] == ""


@pytest.mark.parametrize("spec", [
    {"status": "ok", "kind": "photo_forward", "elements": [], "decorations": []},
    {"status": "ok", "kind": "photo_forward", "elements": [], "decorations": [],
     "logo_box": {"x": .8, "y": .9, "w": .1, "h": .05}}])
async def test_a_plain_photo_becomes_a_hero_photo_with_provenance_and_a_caption(monkeypatch, spec):
    w = World(monkeypatch, spec=spec)
    r = await _post()
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["verdict"] == "photo" and out["media_id"] == MID
    assert out["caption"] == "a golf green at dusk" and out["has_person"] is False
    assert out["in_rotation"] is True and out["orientation"] == "portrait"
    store = next(c for c in w.calls if c[0] == "store")
    assert store[1] == TENANT, "stored under the REQUEST tenant's prefix"
    _, tenant, kw = next(c for c in w.calls if c[0] == "insert")
    assert tenant == TENANT and kw["req"].rights == "own_account"
    assert kw["req"].origin == "own_instagram" and kw["platform"] == "instagram"
    assert kw["m"]["width"] == 1080 and len(kw["m"]["sha256"]) == 64
    assert kw["taken_at"] is not None
    assert ("bust", TENANT) in w.calls, "the brand's photo cache must see the new photo"
    assert ("caption", TENANT, MID, f"https://store.test/{TENANT}/abc.jpg") in w.calls, \
        "captioned on arrival (the caption path, not the video perception path)"
    assert "save" not in w.kinds()
    assert w.tenants() == {TENANT}
    q = kw["m"]["quality"]
    assert not q.get("has_text") and q["in_rotation"] is True
    assert bool(q.get("has_logo")) == bool(spec.get("logo_box")), "a logo is recorded, not held back"


async def test_a_photo_with_text_printed_on_it_is_kept_but_out_of_rotation(monkeypatch):
    """'GRAND OPENING SAT' across the picture: still the brand's post, but new
    copy drawn over it would overlap the old — flagged has_text, never rotated."""
    w = World(monkeypatch, spec=PLAIN)
    out = (await _post()).json()
    assert out["verdict"] == "photo"
    assert out["in_rotation"] is False and "has_text" in out["quality_reasons"]
    _, _t, kw = next(c for c in w.calls if c[0] == "insert")
    q = kw["m"]["quality"]
    assert q["has_text"] is True and q["text_elements"] == 1 and q["in_rotation"] is False
    assert "save" not in w.kinds()


async def test_a_photo_that_lost_the_insert_race_is_a_duplicate_and_its_copy_discarded(monkeypatch):
    w = World(monkeypatch, spec=PLAIN, insert_dup="m-existing")
    out = (await _post()).json()
    assert out["verdict"] == "duplicate" and out["media_id"] == "m-existing"
    assert "discard" in w.kinds() and "caption" not in w.kinds()


async def test_an_undrawable_design_is_counted_and_nothing_stored(monkeypatch):
    assert dt.usable(UNDRAWABLE) and not dt.drawable(UNDRAWABLE)
    w = World(monkeypatch, spec=UNDRAWABLE)
    r = await _post()
    assert r.status_code == 200 and r.json()["verdict"] == "undrawable"
    assert not WRITES & set(w.kinds())


# ── never paid twice ────────────────────────────────────────────────────────


async def test_a_picture_already_held_is_a_duplicate_without_a_paid_read(monkeypatch):
    w = World(monkeypatch, dup={"media_id": "m-9", "match": "dhash", "width": 1, "height": 1})
    r = await _post()
    out = r.json()
    assert r.status_code == 200 and out["verdict"] == "duplicate" and out["media_id"] == "m-9"
    assert "extract" not in w.kinds() and not WRITES & set(w.kinds())


async def test_a_rescrape_by_origin_ref_is_a_duplicate_without_a_download(monkeypatch):
    w = World(monkeypatch, seen={"media_id": "m-3", "width": 1080, "height": 1350})
    out = (await _post()).json()
    assert out["verdict"] == "duplicate" and out["match"] == "origin_ref"
    assert "fetch" not in w.kinds()
    _, tenant, origin, ref, key = next(c for c in w.calls if c[0] == "by_ref")
    assert (tenant, origin, ref, key) == (TENANT, "own_instagram", "3311223344", "123_456_n.jpg")


async def test_a_database_without_migration_072_is_retry_not_a_crash(monkeypatch):
    class UndefinedColumnError(Exception):
        pass
    w = World(monkeypatch, seen=UndefinedColumnError("column origin does not exist"))
    r = await _post()
    assert r.status_code == 503 and r.json()["reason"] == "migration_072_missing"
    assert "fetch" not in w.kinds()


# ── what may come in at all ─────────────────────────────────────────────────


@pytest.mark.parametrize("over,reason", [
    ({"rights": "mention"}, "rights_not_own_account"),
    ({"published_by_bm": True}, "published_by_bm"),
    ({"origin": "owner_upload"}, "origin_not_own"),
    ({"origin": "onclusive"}, "origin_not_own"),
    ({"url": "http://scontent.cdninstagram.com/x.jpg"}, "url_must_be_https"),
    ({"platform": "tiktok"}, "platform_origin_mismatch"),
    ({"handle": "@"}, "no_handle"),
])
async def test_only_the_brands_own_account_images_come_in(monkeypatch, over, reason):
    w = World(monkeypatch)
    r = await _post({**BODY, **over})
    assert r.status_code == 422, r.text
    assert r.json() == {"verdict": "rejected", "reason": reason}
    assert w.calls == [], "refused before any lookup or download"


async def test_a_dead_link_is_rejected_and_a_network_fault_is_retry(monkeypatch):
    World(monkeypatch, image=own_media.Refused(422, "fetch_403"))
    r = await _post()
    assert r.status_code == 422 and r.json()["reason"] == "fetch_403"
    World(monkeypatch, image=own_media.Retryable("fetch_error:ConnectError"))
    r = await _post()
    assert r.status_code == 503 and r.json()["verdict"] == "retry"


async def test_not_an_image_and_too_small_are_rejected_before_the_paid_read(monkeypatch):
    w = World(monkeypatch, image=b"GIF89a" + b"\x00" * 64)
    r = await _post()
    assert r.status_code == 415 and r.json()["reason"] == "not_an_image"
    w = World(monkeypatch, image=_jpeg(200, 300))
    r = await _post()
    assert r.status_code == 422 and r.json()["reason"] == "too_small"
    assert "extract" not in w.kinds()


async def test_the_route_needs_the_service_key():
    app = FastAPI()
    app.include_router(api_v1.router)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.post("/v1/own-media/import", json=BODY)
    assert r.status_code in (401, 503)


# ── the download ────────────────────────────────────────────────────────────


class _Resp:
    def __init__(self, status, body=b"", headers=None):
        self.status_code, self._body, self.headers = status, body, headers or {}

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def aiter_bytes(self, n):
        for i in range(0, len(self._body), n):
            yield self._body[i:i + n]


class _Client:
    script: dict = {}
    seen: list = []

    def __init__(self, *a, **k):
        assert k.get("follow_redirects") is False, "redirects are followed by hand"

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    def stream(self, method, url):
        _Client.seen.append(url)
        return _Client.script[url]


async def test_a_private_address_is_never_fetched(monkeypatch):
    import httpx

    async def public(url, *, allow_http=False):
        return "blocked" if "169.254" in url else "public"
    monkeypatch.setattr("james_os.netguard.url_public_status", public)
    _Client.seen = []
    monkeypatch.setattr(httpx, "AsyncClient", _Client)
    with pytest.raises(own_media.Refused) as e:
        await own_media.fetch_image("https://169.254.169.254/latest/meta-data")
    assert e.value.reason == "url_not_public" and _Client.seen == []


async def test_every_redirect_hop_is_checked(monkeypatch):
    import httpx

    async def public(url, *, allow_http=False):
        return "blocked" if "internal" in url else "public"
    monkeypatch.setattr("james_os.netguard.url_public_status", public)
    _Client.seen = []
    _Client.script = {"https://cdn.test/a.jpg": _Resp(302, headers={"location": "https://internal.test/x"})}
    monkeypatch.setattr(httpx, "AsyncClient", _Client)
    with pytest.raises(own_media.Refused) as e:
        await own_media.fetch_image("https://cdn.test/a.jpg")
    assert e.value.reason == "url_not_public"
    assert _Client.seen == ["https://cdn.test/a.jpg"], "the internal hop was never requested"


async def test_the_download_is_size_capped_and_follows_a_public_redirect(monkeypatch):
    import httpx

    async def public(url, *, allow_http=False):
        return "public"
    monkeypatch.setattr("james_os.netguard.url_public_status", public)
    monkeypatch.setattr(own_media, "MAX_BYTES", 100)
    _Client.script = {"https://cdn.test/a.jpg": _Resp(301, headers={"location": "/b.jpg"}),
                      "https://cdn.test/b.jpg": _Resp(200, b"x" * 60),
                      "https://cdn.test/big.jpg": _Resp(200, b"x" * 500),
                      "https://cdn.test/gone.jpg": _Resp(403),
                      "https://cdn.test/busy.jpg": _Resp(503)}
    monkeypatch.setattr(httpx, "AsyncClient", _Client)
    assert await own_media.fetch_image("https://cdn.test/a.jpg") == b"x" * 60
    with pytest.raises(own_media.Refused) as e:
        await own_media.fetch_image("https://cdn.test/big.jpg")
    assert e.value.status == 413
    with pytest.raises(own_media.Refused):
        await own_media.fetch_image("https://cdn.test/gone.jpg")
    with pytest.raises(own_media.Retryable):
        await own_media.fetch_image("https://cdn.test/busy.jpg")


async def test_a_dns_blip_is_retryable_not_a_final_url_not_public(monkeypatch):
    """EAI_AGAIN while resolving the CDN must not close the brand's image for
    good: BM2 ledgers 'url_not_public' as rejected, never retried."""
    import asyncio
    import socket

    import httpx

    from james_os import netguard

    async def flaky(self, host, port, **k):
        raise socket.gaierror(socket.EAI_AGAIN, "Temporary failure in name resolution")
    monkeypatch.setattr(asyncio.get_running_loop().__class__, "getaddrinfo", flaky)
    _Client.seen = []
    monkeypatch.setattr(httpx, "AsyncClient", _Client)
    assert await netguard.url_public_status("https://scontent.cdninstagram.com/a.jpg") == "unresolved"
    assert await netguard.url_is_public("https://scontent.cdninstagram.com/a.jpg") is False
    with pytest.raises(own_media.Retryable) as e:
        await own_media.fetch_image("https://scontent.cdninstagram.com/a.jpg")
    assert e.value.reason.startswith("fetch_"), "BM2 maps fetch_* 503s to a per-picture retry"
    assert _Client.seen == [], "nothing was requested"


async def test_the_guard_still_blocks_private_addresses_and_bad_schemes(monkeypatch):
    import asyncio
    import socket

    from james_os import netguard

    async def private(self, host, port, **k):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.5", port))]
    monkeypatch.setattr(asyncio.get_running_loop().__class__, "getaddrinfo", private)
    assert await netguard.url_public_status("https://cdn.test/a.jpg") == "blocked"
    assert await netguard.url_public_status("ftp://cdn.test/a.jpg") == "blocked"
    assert await netguard.url_public_status("https://cdn.test:99999/a.jpg") == "blocked"
    with pytest.raises(own_media.Refused) as e:
        await own_media.fetch_image("https://cdn.test/a.jpg")
    assert e.value.reason == "url_not_public"


# ── the file ────────────────────────────────────────────────────────────────


def test_dhash_names_the_same_picture_across_a_reencode_and_resize():
    a = _jpeg(1080, 1350, seed=3)
    img = Image.open(io.BytesIO(a)).resize((540, 675))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=60)
    ha, hb = own_media.dhash(a), own_media.dhash(buf.getvalue())
    assert len(ha) == 16 and own_media.near(ha, hb)
    assert not own_media.near(ha, own_media.dhash(_jpeg(1080, 1350, seed=99)))


def test_a_flat_picture_is_never_compared():
    buf = io.BytesIO()
    Image.new("RGB", (800, 800), (40, 40, 40)).save(buf, "PNG")
    h = own_media.dhash(buf.getvalue())
    assert own_media.dhash_flat(h) and not own_media.near(h, h)


def test_measure_keeps_original_bytes_and_uprights_an_exif_rotation_once():
    m = own_media.measure(PHOTO)
    assert m["data"] is PHOTO, "an upright file is stored byte-for-byte"
    assert (m["width"], m["height"], m["orientation"]) == (1080, 1350, "portrait")
    assert m["quality"]["in_rotation"] and not m["quality"]["screenshot"]
    rot = _jpeg(1350, 1080, exif_orientation=6)    # stored sideways, displays portrait
    m2 = own_media.measure(rot)
    assert (m2["width"], m2["height"]) == (1080, 1350)
    assert Image.open(io.BytesIO(m2["data"])).getexif().get(0x0112, 1) == 1
    assert m2["sha256"] != own_media.measure(PHOTO)["sha256"]


def test_measure_flags_what_should_not_join_the_rotation():
    small = own_media.measure(_jpeg(800, 1000))
    assert not small["quality"]["in_rotation"] and "below_1080" in small["quality"]["reasons"]
    shot = own_media.measure(_jpeg(1170, 2532))
    assert shot["quality"]["screenshot"] and not shot["quality"]["in_rotation"]


def test_measure_refuses_a_pixel_bomb_before_decoding(monkeypatch):
    monkeypatch.setattr(own_media, "MAX_PIXELS", 1000)
    with pytest.raises(own_media.Refused) as e:
        own_media.measure(PHOTO)
    assert e.value.status == 413


# ── the library queries, against a recording connection ─────────────────────


class _Conn:
    def __init__(self, answers):
        self.answers, self.sql = answers, []

    def _answer(self, kind, sql, args):
        self.sql.append((kind, " ".join(sql.split()), args))
        for needle, val in self.answers:
            if needle in sql:
                return val(args) if callable(val) else val
        return [] if kind == "fetch" else None

    async def fetchrow(self, sql, *a):
        return self._answer("fetchrow", sql, a)

    async def fetchval(self, sql, *a):
        return self._answer("fetchval", sql, a)

    async def fetch(self, sql, *a):
        return self._answer("fetch", sql, a)

    async def execute(self, sql, *a):
        return self._answer("execute", sql, a)


def _acquire_with(monkeypatch, conn, module=own_media):
    tenants = []

    class _Ctx:
        def __init__(self, t):
            tenants.append(t)

        async def __aenter__(self):
            return conn

        async def __aexit__(self, *a):
            return False

    monkeypatch.setattr(module, "acquire", lambda t=None, **k: _Ctx(t))
    return tenants


async def test_duplicate_by_exact_hash_matches_the_legacy_32_hex_upload_tag(monkeypatch):
    sha = "ab" * 32
    conn = _Conn([("FROM media_assets WHERE role = $1 AND (sha256",
                   lambda a: {"id": "m-legacy", "width": 10, "height": 10}
                   if a[2] == f"sha256:{sha[:32]}" else None)])
    tenants = _acquire_with(monkeypatch, conn)
    got = await own_media.find_duplicate(TENANT, sha, "")
    assert got["media_id"] == "m-legacy" and got["match"] == "sha256"
    assert tenants == [TENANT]


async def test_duplicate_by_dhash_across_photos_and_own_template_images(monkeypatch):
    base = own_media.dhash(_jpeg(seed=5))
    flipped_bit = f"{int(base, 16) ^ 0b111:016x}"
    conn = _Conn([("dhash IS NOT NULL", [{"id": "m-near", "dhash": flipped_bit,
                                          "width": 1, "height": 1}])])
    _acquire_with(monkeypatch, conn)
    got = await own_media.find_duplicate(TENANT, "c" * 64, base)
    assert got == {"media_id": "m-near", "width": 1, "height": 1, "match": "dhash"}

    conn = _Conn([("spec->'source_image' ? 'dhash'", [{"id": "t-near", "dhash": flipped_bit}])])
    _acquire_with(monkeypatch, conn)
    assert (await own_media.find_duplicate(TENANT, "c" * 64, base))["template_id"] == "t-near"

    far = f"{int(base, 16) ^ 0xFFFF:016x}"
    conn = _Conn([("dhash IS NOT NULL", [{"id": "m-far", "dhash": far, "width": 1, "height": 1}])])
    _acquire_with(monkeypatch, conn)
    assert await own_media.find_duplicate(TENANT, "c" * 64, base) is None


async def test_insert_photo_rechecks_under_a_lock_and_writes_full_provenance(monkeypatch):
    m = own_media.measure(PHOTO)
    req = own_media.OwnMediaImport(**BODY)
    conn = _Conn([("INSERT INTO media_assets", "m-new")])
    tenants = _acquire_with(monkeypatch, conn)
    mid, created = await own_media.insert_photo(TENANT, uri="https://s/x.jpg", file_path="p",
                                                m=m, req=req, platform="instagram", taken_at=None)
    assert (mid, created) == ("m-new", True) and tenants == [TENANT]
    kinds = [s[0] for s in conn.sql]
    assert kinds[0] == "execute" and "pg_advisory_xact_lock" in conn.sql[0][1]
    ins = next(s for s in conn.sql if "INSERT INTO media_assets" in s[1])
    args = ins[2]
    assert args[0] == "hero_photo"
    assert ["own-post", f"sha256:{m['sha256'][:32]}", "origin:own_instagram"] == args[6]
    assert "own_instagram" in args and "own_account" in args and m["sha256"] in args
    assert json.loads(args[-1])["image_key"] == "123_456_n.jpg"
    assert "'upload'" in ins[1], "source_type upload, so the library and analysis accept it"

    conn = _Conn([("SELECT id::text FROM media_assets WHERE role = $1", "m-old")])
    _acquire_with(monkeypatch, conn)
    assert await own_media.insert_photo(TENANT, uri="u", file_path="p", m=m, req=req,
                                        platform="instagram", taken_at=None) == ("m-old", False)
    assert not any("INSERT" in s[1] for s in conn.sql)


async def test_the_summary_groups_own_templates_by_layout_type(monkeypatch):
    from james_os.layout_types import classify

    rows = [{"id": f"t{i}", "spec": json.dumps(DESIGNED if i < 2 else PLAIN),
             "source_url": f"https://ig/p/{i}", "source_image_uri": f"https://s/{i}.jpg",
             "source_handle": "examplegolfclub", "source_platform": "instagram",
             "source_engagement": 0.0, "status": "active", "times_used": 0,
             "created_at": f"2026-10-0{i + 1}"} for i in range(3)]
    photo_rows = [{"origin": "own_instagram", "orientation": "portrait", "has_person": False,
                   "n": 4, "last": None},
                  {"origin": "owner_upload", "orientation": "landscape", "has_person": None,
                   "n": 2, "last": None}]
    conn = _Conn([("FROM media_assets", photo_rows), ("FROM design_templates", rows)])
    tenants = _acquire_with(monkeypatch, conn)
    out = await own_media.summary(TENANT, days=30)
    assert set(tenants) == {TENANT}
    assert out["photos"]["by_origin"] == {"own_instagram": 4, "owner_upload": 2}
    assert out["photos"]["own_posts"] == 4
    assert out["photos"]["by_has_person"] == {"yes": 0, "no": 4, "unknown": 2}
    groups = {g["layout_type"]: g for g in out["templates"]["by_layout_type"]}
    assert groups[classify(DESIGNED)["type"]]["count"] == 2
    assert out["templates"]["total"] == 3
    assert all("make_interval" in s[1] for s in conn.sql), "days narrows both queries"


async def test_an_infrastructure_fault_is_retry_not_a_verdict(monkeypatch):
    w = World(monkeypatch)

    async def boom(*a, **k):
        raise ConnectionError("pool exhausted")
    monkeypatch.setattr(own_media, "find_duplicate", boom)
    r = await _post()
    assert r.status_code == 503 and r.json() == {"verdict": "retry",
                                                 "reason": "internal:ConnectionError"}
    assert "extract" not in w.kinds() and not WRITES & set(w.kinds())
