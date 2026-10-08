"""Pinning a catalogue layout, and the brand's view of the catalogue.

BM2's showcase lists the newest house layouts that suit a brand
(GET /v1/house-layouts/for-brand) and renders a chosen one
(POST /v1/generate with house_layout_id). The things that would go wrong
quietly here:

  * a pin that silently renders some OTHER learned layout (or the nine) while
    the post says it is the pinned one;
  * the id handed back being the catalogue's, so mark_used moves nothing;
  * a brand's fork that the owner paused, or QA retired, drawn anyway;
  * the brand view leaking or mis-ordering — tenant, tier, latest-first;
  * an undrawable upload landing in the APPROVED pool;
  * a verdict for a layout this brand does not hold answering "ok".

All without a database: queries are routed to stand-ins by their SQL.
"""

import asyncio
import contextlib
import json
import logging
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError

from james_os import api_v1
from james_os import design_templates as dt
from james_os import house_layouts as hl

pytestmark = pytest.mark.nodb

HID = "11111111-2222-3333-4444-555555555555"
TENANT = UUID("0000000b-0000-0000-0000-00000000beef")


def _el(role, x, y, w, h, size="md"):
    return {"role": role, "box": {"x": x, "y": y, "w": w, "h": h}, "size": size,
            "align": "left", "text": "x"}


GOOD = {"status": "ok", "kind": "graphic_card", "background": {"treatment": "full_bleed_photo"},
        "elements": [_el("headline", .05, .6, .9, .2, "xl")], "decorations": []}
# A solid card with one line in its top tenth: kept, never drawn.
HOLE = {"status": "ok", "kind": "graphic_card", "background": {"treatment": "solid"},
        "elements": [_el("headline", .05, .05, .9, .1, "lg")], "decorations": []}


def _house(**over):
    row = {"id": HID, "kind": "graphic_card", "spec": json.dumps(GOOD), "fingerprint": "fp-h",
           "source_kind": "curated", "source_url": "https://ref", "source_image_uri": "",
           "status": "approved"}
    row.update(over)
    return row


def _wire(monkeypatch, *, house=None, insert="own-1", existing="own-old",
          mine_status="active", mine_h=HID):
    """One stand-in connection for both scopes; records the tenant of every
    acquire() and every statement, routed by SQL."""
    seen = {"tenants": [], "sql": [], "counted": []}

    class _C:
        async def fetchrow(self, sql, *a):
            seen["sql"].append(sql)
            if "FROM house_layouts" in sql:
                return house
            if "FROM design_templates" in sql:
                return {"status": mine_status, "h": mine_h} if mine_status else None
            return None

        async def fetchval(self, sql, *a):
            seen["sql"].append(sql)
            if "INSERT INTO design_templates" in sql:
                return insert
            if "FROM design_templates" in sql:
                return existing
            return None

        async def execute(self, sql, *a):
            seen["sql"].append(sql)
            if "adopted_count" in sql:
                seen["counted"].append(a[0])

        async def fetch(self, sql, *a):
            return []

    @contextlib.asynccontextmanager
    async def _acq(tenant_id=None):
        seen["tenants"].append(tenant_id)
        yield _C()

    monkeypatch.setattr(dt, "acquire", _acq)
    monkeypatch.setattr(hl, "acquire", _acq)
    return seen


# ── B1: pick_house ─────────────────────────────────────────────────────────


def test_a_pin_returns_the_BRANDS_row_with_its_provenance(monkeypatch):
    seen = _wire(monkeypatch, house=_house())
    got = asyncio.run(dt.pick_house(TENANT, HID))
    assert got["id"] == "own-1", "the brand row is what mark_used must move"
    assert got["house_layout_id"] == HID
    assert got["source_platform"] == "house"
    assert got["source_kind"] == "reference", "curated is translated for the brand library"
    assert set(got) >= {"id", "spec", "kind", "source_handle", "source_url",
                        "source_platform", "source_kind"}, "pick()'s shape"
    # the catalogue on the unscoped connection, the fork on the tenant's
    assert seen["tenants"][0] is None and TENANT in seen["tenants"]


@pytest.mark.parametrize("house", [
    None,                                   # missing
    _house(status="candidate"),             # not approved
    _house(status="rejected"),
    _house(spec=json.dumps(HOLE)),          # not drawable
])
def test_a_pin_that_cannot_be_drawn_is_None_so_the_nine_take_it(monkeypatch, house):
    seen = _wire(monkeypatch, house=house)
    assert asyncio.run(dt.pick_house(TENANT, HID)) is None
    assert not any("INSERT INTO design_templates" in s for s in seen["sql"]), \
        "nothing is forked for a layout that will not be drawn"


@pytest.mark.parametrize("status", ["paused", "retired"])
def test_a_pin_never_overrules_the_owner_or_design_QA(monkeypatch, status):
    """The brand already holds this layout and it is switched off for it."""
    _wire(monkeypatch, house=_house(), insert=None, existing="own-old", mine_status=status)
    assert asyncio.run(dt.pick_house(TENANT, HID)) is None


def test_a_pin_reuses_the_fork_the_brand_already_holds(monkeypatch):
    seen = _wire(monkeypatch, house=_house(), insert=None, existing="own-old")
    got = asyncio.run(dt.pick_house(TENANT, HID))
    assert got["id"] == "own-old"
    assert seen["counted"] == [], "a reused fork is not a new adoption"


def test_a_pin_on_a_shape_the_brand_learned_itself_still_names_the_pin(monkeypatch):
    _wire(monkeypatch, house=_house(), insert=None, existing="own-self", mine_h=None)
    got = asyncio.run(dt.pick_house(TENANT, HID))
    assert got["id"] == "own-self" and got["house_layout_id"] == HID


def test_no_tenant_or_no_id_is_None_without_touching_anything(monkeypatch):
    seen = _wire(monkeypatch, house=_house())
    assert asyncio.run(dt.pick_house(None, HID)) is None
    assert asyncio.run(dt.pick_house(TENANT, "")) is None
    assert seen["tenants"] == []


# ── B3c: adopt_one counts what it inserts ─────────────────────────────────


def test_a_pick_time_adoption_counts_toward_adopted_count(monkeypatch):
    seen = _wire(monkeypatch, house=_house())
    asyncio.run(dt.pick_house(TENANT, HID))
    assert seen["counted"] == [HID]


def test_a_failed_counter_never_costs_the_adoption(monkeypatch):
    class _Pool:
        async def execute(self, sql, *a):
            raise RuntimeError("catalogue unavailable")

    @contextlib.asynccontextmanager
    async def _acq(tenant_id=None):
        yield _Pool()

    class _Tenant:
        async def fetchval(self, sql, *a):
            return "own-9"

    monkeypatch.setattr(hl, "acquire", _acq)
    assert asyncio.run(hl.adopt_one(_Tenant(), _house())) == "own-9"


# ── B1: the generation path carries the pin ───────────────────────────────


def test_the_request_takes_an_empty_or_uuid_house_layout_id():
    assert api_v1.GenerateRequest(brief="x").house_layout_id == ""
    assert api_v1.GenerateRequest(brief="x", house_layout_id=" ").house_layout_id == ""
    assert api_v1.GenerateRequest(
        brief="x", house_layout_id=HID.upper()).house_layout_id == HID
    for bad in ("abc", "1; DROP TABLE x", "1234"):
        with pytest.raises(ValidationError):
            api_v1.GenerateRequest(brief="x", house_layout_id=bad)


async def test_a_malformed_id_is_a_422_at_the_route():
    app = FastAPI()
    app.include_router(api_v1.router)
    app.dependency_overrides[api_v1.require_service] = lambda: TENANT
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.post("/v1/generate", json={"brief": "x", "house_layout_id": "nope"})
    assert r.status_code == 422


def test_the_job_hands_the_pin_to_the_post_maker(monkeypatch):
    from james_os import autopilot_bulk

    got = {}

    async def _make(idea, platform, tenant_id, **kw):
        got.update(kw, tenant=tenant_id)
        return {"action_id": "a1", "status": "pending"}

    monkeypatch.setattr(autopilot_bulk, "_make_text_post", _make)
    req = api_v1.GenerateRequest(brief="x", image_kind="designed", force_format="learned",
                                 house_layout_id=HID)
    api_v1._JOBS["j1"] = {"id": "j1", "status": "queued"}
    asyncio.run(api_v1._run_generate("j1", TENANT, req))
    assert got["house_layout_id"] == HID and got["force_format"] == "learned"
    assert got["tenant"] == TENANT


def test_the_post_maker_hands_the_pin_to_the_designed_renderer(monkeypatch):
    from james_os import autopilot_bulk, main

    got = {}

    class _Draft:
        action_id, draft, status, platform, voice_score, note = "a1", "d", "pending", "ig", 1, ""

    async def _content(brief, tenant_id):
        return _Draft()

    async def _designed(*a, **kw):
        got.update(kw)
        return "file://x.png", "learned"

    monkeypatch.setattr(autopilot_bulk, "generate_content", _content)
    monkeypatch.setattr(main, "_generate_designed_post_image", _designed)
    asyncio.run(autopilot_bulk._make_text_post(
        {"topic": "t", "title": "t"}, "instagram", TENANT, image_kind="designed",
        force_format="learned", house_layout_id=HID))
    assert got["house_layout_id"] == HID


def test_the_designed_renderer_hands_the_pin_to_the_learned_one(monkeypatch):
    from james_os import main

    got = {}

    async def _learned(*a, **kw):
        got.update(kw)
        return "file://l.png", "learned"

    monkeypatch.setattr(main, "_generate_learned_post_image", _learned)
    out = asyncio.run(main._generate_designed_post_image(
        "a1", "t", "d", TENANT, force_format="learned", house_layout_id=HID))
    assert out == ("file://l.png", "learned") and got["house_layout_id"] == HID


def _stub_render(monkeypatch, *, picked, qa=None):
    """Everything _generate_learned_post_image touches, stubbed. Returns what
    was written to the action and which picker was asked."""
    from james_os import main, media, spec_render, template_clone

    seen = {"payload": {}, "pick": [], "pick_house": [], "used": []}

    async def _pick(tenant_id):
        seen["pick"].append(tenant_id)
        return picked

    async def _pick_house(tenant_id, hid):
        seen["pick_house"].append((tenant_id, hid))
        return picked

    async def _fill(spec, tenant_id, ref, **kw):
        return {"headline": "Hello"}

    async def _hero(tenant_id, headline, spec):
        return b"hero", False, "hero-1"

    async def _extra(*a, **kw):
        return []

    async def _palette(tenant_id):
        return {}

    async def _none(*a, **kw):
        return None

    class _Store:
        def save(self, tenant, png, name):
            return f"file://{name}", f"/tmp/{name}"

    class _Conn:
        async def execute(self, sql, action_id, payload):
            seen["payload"].update(json.loads(payload))

    @contextlib.asynccontextmanager
    async def _acq(tenant_id=None):
        yield _Conn()

    async def _used(tenant_id, tid):
        seen["used"].append(tid)

    monkeypatch.setattr(dt, "pick", _pick)
    monkeypatch.setattr(dt, "pick_house", _pick_house)
    monkeypatch.setattr(dt, "mark_used", _used)
    monkeypatch.setattr(dt, "mark_qa", _none)
    monkeypatch.setattr(template_clone, "_fill_copy", _fill)
    monkeypatch.setattr(template_clone, "_hero_or_placeholder", _hero)
    monkeypatch.setattr(template_clone, "extra_photos_for", _extra)
    monkeypatch.setattr(template_clone, "_brand_palette", _palette)
    monkeypatch.setattr(template_clone, "_brand_logo", _none)
    monkeypatch.setattr(spec_render, "render_spec", lambda *a, **k: (b"png", "graphic_card"))
    monkeypatch.setattr(media, "storage", lambda: _Store())
    monkeypatch.setattr(media, "create_media", _none)
    monkeypatch.setattr(main, "acquire", _acq)
    monkeypatch.setattr(main.settings, "design_qa_enabled", qa is not None)
    if qa is not None:
        from james_os import render_reviewer

        async def _review(png, mime=""):
            return qa
        monkeypatch.setattr(render_reviewer, "review_post", _review)
    return seen


def _picked(**over):
    d = {"id": "own-1", "spec": GOOD, "kind": "graphic_card", "source_handle": "",
         "source_url": "https://ref", "source_platform": "house", "source_kind": "reference",
         "house_layout_id": HID}
    d.update(over)
    return d


def test_a_pinned_render_uses_pick_house_and_stamps_the_pin(monkeypatch):
    from james_os import main

    seen = _stub_render(monkeypatch, picked=_picked())
    out = asyncio.run(main._generate_learned_post_image(
        "a1", "topic", "draft", TENANT, house_layout_id=HID))
    assert out[1] == "learned"
    assert seen["pick"] == [] and seen["pick_house"] == [(TENANT, HID)]
    p = seen["payload"]
    assert p["image_format"] == "learned"
    assert p["design_template_id"] == "own-1", "the BRAND row, as today"
    src = p["design_template_source"]
    assert src["house_layout_id"] == HID
    assert {"kind", "handle", "url", "platform"} <= set(src)
    assert p["house_layout_pinned"] is True
    assert seen["used"] == ["own-1"]


def test_an_unpinned_fork_is_stamped_but_not_marked_pinned(monkeypatch):
    from james_os import main

    seen = _stub_render(monkeypatch, picked=_picked())
    asyncio.run(main._generate_learned_post_image("a1", "topic", "draft", TENANT))
    assert seen["pick"] == [TENANT] and seen["pick_house"] == []
    assert seen["payload"]["design_template_source"]["house_layout_id"] == HID
    assert "house_layout_pinned" not in seen["payload"]


def test_a_brands_own_layout_carries_no_house_id(monkeypatch):
    from james_os import main

    seen = _stub_render(monkeypatch, picked=_picked(house_layout_id="", source_platform="ig"))
    asyncio.run(main._generate_learned_post_image("a1", "topic", "draft", TENANT))
    assert "house_layout_id" not in seen["payload"]["design_template_source"]


def test_a_pin_that_cannot_be_drawn_falls_back_to_the_nine_not_to_pick(monkeypatch):
    from james_os import main

    seen = _stub_render(monkeypatch, picked=None)
    out = asyncio.run(main._generate_learned_post_image(
        "a1", "topic", "draft", TENANT, house_layout_id=HID))
    assert out is None
    assert seen["pick"] == [], "never quietly substitute another learned layout"


# ── B6: design QA that did not run is said out loud ───────────────────────


def test_an_unreviewed_learned_render_is_logged(monkeypatch, caplog):
    from james_os import main

    _stub_render(monkeypatch, picked=_picked(), qa={"status": "failed"})
    with caplog.at_level(logging.WARNING):
        out = asyncio.run(main._generate_learned_post_image("a1", "t", "d", TENANT))
    assert out is not None, "pass/fail behaviour is unchanged: it still ships"
    msgs = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert any("own-1" in m and "'failed'" in m for m in msgs), msgs


def test_a_reviewed_render_logs_no_QA_warning(monkeypatch, caplog):
    from james_os import main

    _stub_render(monkeypatch, picked=_picked(), qa={"status": "ok", "passed": True})
    with caplog.at_level(logging.WARNING):
        asyncio.run(main._generate_learned_post_image("a1", "t", "d", TENANT))
    assert not any("design QA did not review" in r.getMessage() for r in caplog.records)


# ── B3a/b: freshness and the stop word ────────────────────────────────────


NOW = datetime(2026, 10, 8, tzinfo=UTC)


def test_fresh_outranks_old_within_a_niche_tier_but_never_across_one():
    new, old = NOW - timedelta(days=2), NOW - timedelta(days=40)
    assert hl.fit_rank("golf", {}, ["golf"], "offer_card", new, now=NOW) > \
        hl.fit_rank("golf", {}, ["golf"], "offer_card", old, now=NOW)
    # a fresh untagged row does not jump an old on-niche one
    assert hl.fit_rank("golf", {}, ["golf"], "offer_card", old, now=NOW) > \
        hl.fit_rank("golf", {}, [], "offer_card", new, now=NOW)
    # freshness sits BEFORE the type evidence
    assert hl.fit_rank("golf", {}, ["golf"], "stat_card", new, now=NOW) > \
        hl.fit_rank("golf", {"offer_card": 9}, ["golf"], "offer_card", old, now=NOW)


@pytest.mark.parametrize("when,fresh", [
    (NOW - timedelta(days=13), 1), (NOW - timedelta(days=15), 0),
    ((NOW - timedelta(days=1)).isoformat(), 1), ("2026-10-07T00:00:00Z", 1),
    ((NOW - timedelta(days=1)).replace(tzinfo=None), 1),
    (None, 0), ("", 0), ("not a date", 0), (12, 0),
])
def test_is_fresh_is_tolerant(when, fresh):
    assert hl.is_fresh(when, now=NOW) == fresh
    assert hl.HOUSE_FRESH_DAYS == 14


def test_candidates_put_the_latest_first_within_a_tier(monkeypatch):
    rows = [
        {"id": "old", "fingerprint": "f1", "family_key": "", "niches": ["golf"],
         "layout_type": "offer_card", "spec": "{}", "created_at": NOW - timedelta(days=60)},
        {"id": "new", "fingerprint": "f2", "family_key": "", "niches": ["golf"],
         "layout_type": "offer_card", "spec": "{}", "created_at": datetime.now(UTC)},
        {"id": "newoff", "fingerprint": "f3", "family_key": "", "niches": ["politics"],
         "layout_type": "offer_card", "spec": "{}", "created_at": datetime.now(UTC)},
    ]

    class _Pool:
        async def fetch(self, sql, *a):
            assert "created_at" in sql.split("FROM house_layouts")[0]
            return rows

    class _Mine:
        async def fetch(self, sql, *a):
            return []

    @contextlib.asynccontextmanager
    async def _acq(tenant_id=None):
        yield _Pool()

    monkeypatch.setattr(hl, "acquire", _acq)
    got = asyncio.run(hl.candidates(_Mine(), "t", niches=["golf"]))
    assert [r["id"] for r in got] == ["new", "old", "newoff"]


def test_commercial_alone_is_not_a_niche_match():
    assert hl.niche_rank("commercial spaceport", ["commercial real estate"]) == 0
    assert hl.niche_rank("commercial real estate", ["real estate"]) >= 2
    assert "commercial" not in hl.niche_tokens("commercial spaceport")


# ── B2: the brand's view of the catalogue ─────────────────────────────────


def _cat(i, *, tags=(), days=1, spec=GOOD, status="approved", fp=None, title="", label="photo offer"):
    return {"id": f"00000000-0000-0000-0000-{i:012d}", "kind": "graphic_card",
            "spec": json.dumps(spec), "fingerprint": fp or f"fp{i}", "source_kind": "curated",
            "source_image_uri": f"https://img/{i}.png", "niches": list(tags),
            "layout_type": "offer_card", "label": label, "title": title,
            "created_at": NOW - timedelta(days=days), "status": status}


def _wire_view(monkeypatch, catalogue, mine=(), niches=("Golf Resort",)):
    seen = {"tenants": [], "pool_args": []}

    class _C:
        def __init__(self, tenant):
            self.tenant = tenant

        async def fetch(self, sql, *a):
            if "FROM competitors" in sql:
                return [{"niche": n} for n in niches]
            if "FROM design_templates" in sql:
                assert self.tenant is not None, "holdings are read on the TENANT's connection"
                return list(mine)
            if "FROM house_layouts" in sql:
                assert self.tenant is None
                assert "status = 'approved'" in sql
                seen["pool_args"].append(a)
                return list(catalogue)
            raise AssertionError(sql)

    @contextlib.asynccontextmanager
    async def _acq(tenant_id=None):
        seen["tenants"].append(tenant_id)
        yield _C(tenant_id)

    monkeypatch.setattr(hl, "acquire", _acq)
    return seen


def test_the_view_is_tiered_then_latest_first(monkeypatch):
    rows = [_cat(1, tags=["golf"], days=30), _cat(2, days=1), _cat(3, tags=["politics"], days=0),
            _cat(4, tags=["golf"], days=2), _cat(5, days=9)]
    seen = _wire_view(monkeypatch, rows)
    out = asyncio.run(hl.for_brand(TENANT, limit=8))
    assert out["niche"] == ["Golf Resort"]
    got = [(d["id"][-1], d["tier"]) for d in out["layouts"]]
    assert got == [("4", "niche"), ("1", "niche"), ("2", "general"), ("5", "general"),
                   ("3", "off_niche")]
    assert seen["tenants"] == [TENANT, None]
    first = out["layouts"][0]
    assert set(first) == {"id", "created_at", "niches", "type", "name", "source_kind",
                          "image_url", "tier", "already_forked"}
    assert first["type"] == "offer_card" and first["name"] == "photo offer"
    assert first["image_url"] == "https://img/4.png" and first["already_forked"] is False
    assert first["created_at"].startswith("2026-10-06")


def test_the_view_leaves_out_what_the_brand_already_drew_or_cannot_draw(monkeypatch):
    rows = [_cat(1), _cat(2), _cat(3, spec=HOLE), _cat(4), _cat(5, fp="shape-mine"), _cat(6)]
    mine = [
        {"h": rows[0]["id"], "fingerprint": "fp1", "times_used": 3, "status": "active"},
        {"h": rows[1]["id"], "fingerprint": "fp2", "times_used": 0, "status": "active"},
        {"h": rows[3]["id"], "fingerprint": "fp4", "times_used": 0, "status": "paused"},
        {"h": None, "fingerprint": "shape-mine", "times_used": 0, "status": "active"},
    ]
    _wire_view(monkeypatch, rows, mine)
    out = asyncio.run(hl.for_brand(TENANT))
    by = {d["id"][-1]: d for d in out["layouts"]}
    assert set(by) == {"2", "5", "6"}, "drawn (1), undrawable (3), paused (4) are out"
    assert by["2"]["already_forked"] and by["5"]["already_forked"]
    assert not by["6"]["already_forked"]


def test_the_view_honours_the_limit_and_names_untitled_rows(monkeypatch):
    rows = [_cat(i, days=i, label="", title="T" if i == 0 else "") for i in range(30)]
    _wire_view(monkeypatch, rows)
    out = asyncio.run(hl.for_brand(TENANT, limit=50))
    assert len(out["layouts"]) == hl.FOR_BRAND_MAX == 20
    assert out["layouts"][0]["name"] == "T"
    assert out["layouts"][1]["name"] == "offer"
    assert len(asyncio.run(hl.for_brand(TENANT, limit=3))["layouts"]) == 3


def test_the_view_presorts_by_the_brands_tags_in_sql(monkeypatch):
    seen = _wire_view(monkeypatch, [_cat(1)])
    asyncio.run(hl.for_brand(TENANT))
    tags, pool = seen["pool_args"][0]
    assert "golf resort" in tags and pool == hl.CANDIDATE_POOL


def test_no_tenant_no_view(monkeypatch):
    seen = _wire_view(monkeypatch, [_cat(1)])
    assert asyncio.run(hl.for_brand(None)) == {"niche": [], "layouts": []}
    assert seen["tenants"] == []


def _v1_app(*, override=True) -> FastAPI:
    app = FastAPI()
    app.include_router(api_v1.router)
    if override:
        app.dependency_overrides[api_v1.require_service] = lambda: TENANT
    return app


async def _call(app, method, url, **kw):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        return await c.request(method, url, **kw)


async def test_the_route_is_tenant_bound_and_not_swallowed(monkeypatch):
    got = {}

    async def _view(tenant_id, *, limit):
        got.update(tenant=tenant_id, limit=limit)
        return {"niche": [], "layouts": []}

    monkeypatch.setattr(hl, "for_brand", _view)
    r = await _call(_v1_app(), "GET", "/v1/house-layouts/for-brand")
    assert r.status_code == 200, r.text
    assert got == {"tenant": TENANT, "limit": 8}
    r = await _call(_v1_app(), "GET", "/v1/house-layouts/for-brand?limit=20")
    assert r.status_code == 200 and got["limit"] == 20
    for bad in (0, 21, -1):
        r = await _call(_v1_app(), "GET", f"/v1/house-layouts/for-brand?limit={bad}")
        assert r.status_code == 422, bad


async def test_the_route_takes_the_tenant_from_the_bound_key_only(monkeypatch):
    """No override: the real require_service. A brand key yields ITS tenant,
    whatever X-Tenant-Id says; no key is refused."""
    from james_os.config import settings

    got = {}

    async def _view(tenant_id, *, limit):
        got["tenant"] = tenant_id
        return {"niche": [], "layouts": []}

    monkeypatch.setattr(hl, "for_brand", _view)
    monkeypatch.setattr(settings, "service_api_key", "brand-key-for-test")
    monkeypatch.setattr(settings, "service_api_tenant_id", TENANT)
    r = await _call(_v1_app(override=False), "GET", "/v1/house-layouts/for-brand",
                    headers={"Authorization": "Bearer brand-key-for-test",
                             "X-Tenant-Id": "0000000c-0000-0000-0000-000000000000"})
    assert r.status_code == 200, r.text
    assert str(got["tenant"]) == str(TENANT)
    r = await _call(_v1_app(override=False), "GET", "/v1/house-layouts/for-brand")
    assert r.status_code == 401


# ── B4: uploads must be drawable to be approved ──────────────────────────


class _IngestConn:
    def __init__(self):
        self.calls = []

    async def fetchrow(self, sql, *a):
        self.calls.append((sql, a))
        return {"id": HID, "fresh": True}


def _wire_ingest(monkeypatch, spec):
    import james_os.design_cloner as dc

    conn = _IngestConn()

    @contextlib.asynccontextmanager
    async def _acq(tenant_id=None):
        yield conn

    async def _extract(_image):
        return spec

    monkeypatch.setattr(hl, "acquire", _acq)
    monkeypatch.setattr(dc, "extract_template_spec", _extract)
    return conn


def test_an_undrawable_upload_is_held_as_a_candidate_with_the_reason(monkeypatch):
    conn = _wire_ingest(monkeypatch, HOLE)
    out = asyncio.run(hl.ingest(b"x", approve=True, by="curator", note="from the deck"))
    assert out["ok"] is True and out["drawable"] is False
    assert out["status"] == "candidate"
    _, args = conn.calls[-1]
    assert "candidate" in args and "approved" not in args
    note = next(a for a in args if isinstance(a, str) and a.startswith("not drawable"))
    assert "solid card" in note and "from the deck" in note
    assert args[-1] == "", "reviewed_by is not stamped on a row nobody approved"


def test_a_drawable_upload_is_approved_exactly_as_before(monkeypatch):
    conn = _wire_ingest(monkeypatch, GOOD)
    out = asyncio.run(hl.ingest(b"x", approve=True, by="curator"))
    assert out["status"] == "approved" and out["drawable"] is True
    _, args = conn.calls[-1]
    assert "approved" in args and args[-1] == "curator"
    assert not any(isinstance(a, str) and a.startswith("not drawable") for a in args)
    out = asyncio.run(hl.ingest(b"x", approve=False))
    assert out["status"] == "candidate" and out["drawable"] is True


def test_undrawable_reasons_read_like_drawable():
    assert hl.undrawable_reason(GOOD) == ""
    assert "solid card" in hl.undrawable_reason(HOLE)
    assert hl.undrawable_reason({}) == "no usable layout was read"


# ── B5: a verdict that taught nothing says so ─────────────────────────────


def _wire_verdict(monkeypatch, row):
    seen = {}

    class _C:
        async def fetchrow(self, sql, *a):
            seen["sql"], seen["args"] = sql, a
            return row

    @contextlib.asynccontextmanager
    async def _acq(tenant_id=None):
        seen["tenant"] = tenant_id
        yield _C()

    monkeypatch.setattr(dt, "acquire", _acq)
    return seen


def test_mark_verdict_returns_the_counts_or_None(monkeypatch):
    seen = _wire_verdict(monkeypatch, {"approvals": 4, "rejections": 1})
    assert asyncio.run(dt.mark_verdict(TENANT, "t1", True)) == {"approvals": 4, "rejections": 1}
    assert "approvals = approvals + 1" in seen["sql"] and "RETURNING" in seen["sql"]
    assert seen["tenant"] == TENANT
    _wire_verdict(monkeypatch, None)
    assert asyncio.run(dt.mark_verdict(TENANT, "t1", False)) is None


async def test_the_verdict_route_404s_for_a_layout_the_brand_does_not_hold(monkeypatch):
    tid = "22222222-2222-2222-2222-222222222222"
    _wire_verdict(monkeypatch, None)
    r = await _call(_v1_app(), "POST", f"/v1/design-templates/{tid}/verdict",
                    json={"approved": True})
    assert r.status_code == 404 and "no such layout for this brand" in r.text
    _wire_verdict(monkeypatch, {"approvals": 2, "rejections": 0})
    r = await _call(_v1_app(), "POST", f"/v1/design-templates/{tid}/verdict",
                    json={"approved": True})
    assert r.status_code == 200
    assert r.json() == {"ok": True, "approvals": 2, "rejections": 0}


# ── the brand's view shows only what is the brand's own to see ────────────


def test_the_view_hides_other_brands_tags_and_promoted_images(monkeypatch):
    other = "99999999-9999-9999-9999-999999999999"
    rows = [
        dict(_cat(1, tags=["golf", "political candidates"]), promoted_from=None),
        dict(_cat(2, tags=["commercial spaceport"], days=2), promoted_from=other),
        dict(_cat(3, days=3), promoted_from=str(TENANT).upper()),
        dict(_cat(4, days=4), source_image_uri="/media-files/house/abc.png"),
    ]
    seen = _wire_view(monkeypatch, rows)
    out = asyncio.run(hl.for_brand(TENANT))
    by = {d["id"][-1]: d for d in out["layouts"]}
    # ranked on every tag, shown only the brand's own
    assert by["1"]["tier"] == "niche" and by["1"]["niches"] == ["golf"]
    assert by["2"]["tier"] == "off_niche" and by["2"]["niches"] == []
    # another brand's promoted image is a path into ITS media; this brand's own
    # promotion and catalogue-owned images are shown
    assert by["2"]["image_url"] == ""
    assert by["3"]["image_url"] == "https://img/3.png"
    assert by["4"]["image_url"] == "/media-files/house/abc.png"
    assert by["1"]["image_url"] == "https://img/1.png"
    assert seen["tenants"] == [TENANT, None]


def test_approving_a_held_upload_keeps_its_not_drawable_note(monkeypatch):
    seen = {}

    class _C:
        async def fetchval(self, sql, *a):
            seen["sql"], seen["args"] = sql, a
            return HID

    @contextlib.asynccontextmanager
    async def _acq(tenant_id=None):
        yield _C()

    monkeypatch.setattr(hl, "acquire", _acq)
    assert asyncio.run(hl.review(HID, "approved", by="curator")) == \
        {"ok": True, "status": "approved"}
    sql = " ".join(seen["sql"].split())
    assert "LIKE 'not drawable:%'" in sql and "THEN review_note ELSE $4" in sql
    assert seen["args"] == (HID, "approved", "curator", "")


# ── house_layout_pinned describes the picture, not the lineage ────────────


_PINNED_PARENT = {
    "topic": "t", "content": "words", "image_format": "learned",
    "image_url": "https://old.png", "design_template_id": "brand-row",
    "design_template_source": {"kind": "niche", "house_layout_id": HID},
    "house_layout_pinned": True, "clone_spec": {"kind": "graphic_card"}, "version": "1",
}


def _wire_regenerate(monkeypatch, parent):
    from james_os import main as _main

    inserted: list[dict] = []
    rebuilt: list[dict] = []

    class _C:
        async def fetchrow(self, sql, *a):
            return {"payload": json.dumps(parent), "rejection_reason_code": ""}

        async def fetchval(self, sql, *a):
            inserted.append(json.loads(a[0]))
            return "new-id"

    @contextlib.asynccontextmanager
    async def _acq(tenant_id=None):
        yield _C()

    async def _designed(new_id, topic, content, tenant_id, **kw):
        return "https://nine.png", "quote"

    async def _rebuild(new_id, payload, feedback, tenant_id, **kw):
        rebuilt.append(payload)
        return "https://same.png", "learned"

    monkeypatch.setattr(api_v1, "acquire", _acq)
    monkeypatch.setattr(api_v1, "_rebuild_cloned_action", _rebuild)
    monkeypatch.setattr(_main, "_generate_designed_post_image", _designed)
    return inserted, rebuilt


@pytest.mark.parametrize("feedback,in_place", [
    ("i hate this design, new layout please", False),
    ("make it brighter", True),
])
def test_a_redo_does_not_inherit_the_pinned_flag(monkeypatch, feedback, in_place):
    inserted, rebuilt = _wire_regenerate(monkeypatch, _PINNED_PARENT)
    api_v1._JOBS["j-pin"] = {"id": "j-pin", "status": "queued"}
    try:
        asyncio.run(api_v1._run_regenerate("j-pin", TENANT, UUID(HID), feedback, ""))
        assert api_v1._JOBS["j-pin"]["status"] == "done", api_v1._JOBS["j-pin"]
    finally:
        api_v1._JOBS.pop("j-pin", None)
    assert "house_layout_pinned" not in inserted[0]
    # rebuilt in place is the SAME layout, and is handed the parent's payload,
    # from which _rebuild_cloned_action re-stamps the flag
    assert bool(rebuilt) is in_place
    if in_place:
        assert rebuilt[0]["house_layout_pinned"] is True


@pytest.mark.parametrize("pinned", [True, False])
def test_an_in_place_rebuild_restamps_the_flag_only_for_a_pinned_parent(monkeypatch, pinned):
    from james_os import template_clone

    written: dict = {}

    class _Conn:
        async def execute(self, sql, new_id, payload):
            written.update(json.loads(payload))

    @contextlib.asynccontextmanager
    async def _acq(tenant_id=None):
        yield _Conn()

    class _Store:
        def save(self, tenant, png, name):
            return f"file://{name}", "/tmp/x"

    async def _rebuild(payload, feedback, tenant_id, **kw):
        return {"png": b"p", "kind": "graphic_card", "spec": {}, "content": {}}

    monkeypatch.setattr(api_v1, "acquire", _acq)
    monkeypatch.setattr(template_clone, "rebuild_design", _rebuild)
    monkeypatch.setattr("james_os.media.storage", lambda: _Store())
    parent = dict(_PINNED_PARENT)
    if not pinned:
        parent.pop("house_layout_pinned")
    served, fmt = asyncio.run(api_v1._rebuild_cloned_action("new", parent, "brighter", TENANT))
    assert fmt == "learned"
    assert written["design_template_source"]["house_layout_id"] == HID
    assert written.get("house_layout_pinned") is (True if pinned else None)


def test_an_owner_image_edit_drops_the_pinned_flag(monkeypatch):
    from uuid import uuid4

    wrote: list = []

    class _C:
        async def fetchrow(self, sql, *a):
            return {"status": "pending", "payload": json.dumps(_PINNED_PARENT),
                    "image_url": "https://old.png", "original_image_url": None}

        async def execute(self, sql, *a):
            wrote.append((sql, a))
            return "UPDATE 1"

    @contextlib.asynccontextmanager
    async def _acq(tenant_id=None):
        yield _C()

    class Up:
        filename, content_type = "edited.png", "image/png"

        async def read(self):
            return b"\x89PNG\r\n\x1a\n" + b"\x00" * 64

    monkeypatch.setattr(api_v1, "acquire", _acq)
    monkeypatch.setattr("james_os.media.storage",
                        lambda: type("S", (), {"save": lambda self, t, d, n: ("https://e.png", "/tmp/e")})())
    asyncio.run(api_v1.v1_post_set_image(uuid4(), TENANT, file=Up(), doc="{}", slide=None))
    _sql, args = wrote[-1]
    drop = args[2]
    assert {"design_template_id", "design_template_source", "house_layout_pinned"} <= set(drop)
