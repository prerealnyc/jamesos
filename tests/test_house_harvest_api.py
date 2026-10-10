"""The harvest routes on /v1, driven over HTTP without a database.

Only the /v1 router is mounted (no app lifespan, so no pool), and the engine
functions behind it are stubbed where a test is about the ROUTE: its status
codes, its defaults, its gate, and that its literal paths are not swallowed by
a /house-layouts/{id} route.
"""

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from james_os import api_v1, house_harvest, house_layouts
from james_os.config import settings

pytestmark = pytest.mark.nodb

KEY = "onc:" + "b" * 40
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
FORM = {"source_key": KEY, "niches": "golf resort,golf", "run_id": "run-7",
        "by": "harvest:nightly"}


def _app(*, curator=True) -> FastAPI:
    app = FastAPI()
    app.include_router(api_v1.router)
    if curator:
        app.dependency_overrides[api_v1.require_curator] = lambda: True
    return app


async def _call(app, method, url, **kw):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        return await c.request(method, url, **kw)


@pytest.fixture
def ingest_spy(monkeypatch):
    seen = []

    async def fake(image, **kw):
        seen.append({"image": image, **kw})
        return {"verdict": "held", "reason": "approve_off"}
    monkeypatch.setattr(house_harvest, "harvest_ingest", fake)
    return seen


# ── POST /v1/house-layouts/harvest ─────────────────────────────────────────


async def test_one_image_reaches_the_engine_with_approve_OFF_by_default(ingest_spy):
    r = await _call(_app(), "POST", "/v1/house-layouts/harvest", data=FORM,
                    files={"file": ("a.png", PNG, "image/png")})
    assert r.status_code == 200, r.text
    assert r.json()["verdict"] == "held"
    got = ingest_spy[0]
    assert got["approve"] is False, "fail closed: approve must default to false"
    assert got["dry_run"] is False
    assert got["image"] == PNG
    assert got["niches"] == ["golf resort", "golf"]
    assert got["source_key"] == KEY and got["run_id"] == "run-7"


async def test_explicit_flags_policy_and_meta_are_passed_through(ingest_spy):
    data = {**FORM, "approve": "true", "dry_run": "true", "source_media": "INSTAGRAM",
            "policy": '{"family_per_niche": 3}', "meta": '{"author": "x"}',
            "source_url": "https://ig/p/1", "title": "@x · INSTAGRAM · 2026-10-05"}
    r = await _call(_app(), "POST", "/v1/house-layouts/harvest", data=data,
                    files={"file": ("a.png", PNG, "image/png")})
    assert r.status_code == 200, r.text
    got = ingest_spy[0]
    assert got["approve"] is True and got["dry_run"] is True
    assert got["policy"] == {"family_per_niche": 3} and got["meta"] == {"author": "x"}
    assert got["source_media"] == "INSTAGRAM" and got["source_url"] == "https://ig/p/1"


@pytest.mark.parametrize("files", [
    None,
    [("file", ("a.png", PNG, "image/png")), ("file", ("b.png", PNG, "image/png"))],
])
async def test_exactly_one_file(ingest_spy, files):
    r = await _call(_app(), "POST", "/v1/house-layouts/harvest", data=FORM, files=files)
    assert r.status_code == 400, r.text
    assert ingest_spy == []


@pytest.mark.parametrize("over", [
    {"approve": "maybe"}, {"dry_run": "2"}, {"policy": "{nope"}, {"meta": "[1,2]"},
])
async def test_malformed_fields_are_400(ingest_spy, over):
    r = await _call(_app(), "POST", "/v1/house-layouts/harvest", data={**FORM, **over},
                    files={"file": ("a.png", PNG, "image/png")})
    assert r.status_code == 400, r.text
    assert ingest_spy == []


async def _real(monkeypatch, data, payload):
    """Through the REAL engine function: validation refuses before any
    database or vision call, so a tripwire on both proves the order."""
    async def no_vision(*a, **k):
        raise AssertionError("vision must not be called")

    def no_db(*a, **k):
        raise AssertionError("no database for a refused request")
    import james_os.design_cloner as dc
    monkeypatch.setattr(dc, "extract_template_spec", no_vision)
    monkeypatch.setattr(house_harvest, "acquire", no_db)
    return await _call(_app(), "POST", "/v1/house-layouts/harvest", data=data,
                       files={"file": ("x.bin", payload, "application/octet-stream")})


async def test_missing_or_invalid_fields_are_400(monkeypatch):
    for bad in ({**FORM, "source_key": "onc:nothex"}, {**FORM, "niches": ""},
                {**FORM, "run_id": ""}, {**FORM, "by": "someone"},
                {k: v for k, v in FORM.items() if k != "source_key"}):
        r = await _real(monkeypatch, bad, PNG)
        assert r.status_code == 400, (bad, r.text)


async def test_over_15mb_is_413(monkeypatch):
    r = await _real(monkeypatch, FORM, PNG + b"\x00" * house_harvest.MAX_BYTES)
    assert r.status_code == 413, r.text


async def test_not_an_image_is_415(monkeypatch):
    r = await _real(monkeypatch, FORM, b"<svg xmlns='http://www.w3.org/2000/svg'/>")
    assert r.status_code == 415, r.text


# ── the gate ──────────────────────────────────────────────────────────────


async def test_the_platform_key_is_required(monkeypatch, ingest_spy):
    monkeypatch.setattr(settings, "service_api_platform_key", "")
    r = await _call(_app(curator=False), "POST", "/v1/house-layouts/harvest", data=FORM,
                    files={"file": ("a.png", PNG, "image/png")})
    assert r.status_code == 503
    monkeypatch.setattr(settings, "service_api_platform_key", "platform-secret")
    for auth in ("Bearer brand-key", None):
        headers = {"Authorization": auth} if auth else {}
        for method, url in (("POST", "/v1/house-layouts/harvest"),
                            ("GET", "/v1/house-layouts/harvest/stats"),
                            ("POST", "/v1/house-layouts/harvest/revoke")):
            r = await _call(_app(curator=False), method, url, headers=headers,
                            **({"data": FORM, "files": {"file": ("a.png", PNG, "image/png")}}
                               if url.endswith("/harvest") else
                               {"json": {"run_id": "r"}} if method == "POST" else {}))
            assert r.status_code == 403, (method, url, auth, r.status_code)
    r = await _call(_app(curator=False), "POST", "/v1/house-layouts/harvest", data=FORM,
                    files={"file": ("a.png", PNG, "image/png")},
                    headers={"Authorization": "Bearer platform-secret"})
    assert r.status_code == 200, r.text
    assert len(ingest_spy) == 1


# ── stats, revoke, catalogue filters — and route order ────────────────────


async def test_stats_is_its_own_route_with_repeatable_niche(monkeypatch):
    seen = []

    async def fake(niches):
        seen.append(niches)
        return {"niches": {}, "total_approved": 0, "harvested_approved": 0}
    monkeypatch.setattr(house_harvest, "harvest_stats", fake)
    r = await _call(_app(), "GET",
                    "/v1/house-layouts/harvest/stats?niche=golf&niche=real%20estate")
    assert r.status_code == 200, r.text
    assert seen == [["golf", "real estate"]]


async def test_revoke_is_its_own_route(monkeypatch):
    seen = []

    async def fake(run_id, by):
        seen.append((run_id, by))
        return {"run_id": run_id, "rejected": 1, "skipped_human_reviewed": 0}
    monkeypatch.setattr(house_harvest, "harvest_revoke", fake)
    r = await _call(_app(), "POST", "/v1/house-layouts/harvest/revoke",
                    json={"run_id": "run-7", "by": "harvest:manual:roy"})
    assert r.status_code == 200, r.text
    assert seen == [("run-7", "harvest:manual:roy")]


async def test_revoke_with_a_blank_run_id_is_400(monkeypatch):
    def no_db(*a, **k):
        raise AssertionError("no database for a refused request")
    monkeypatch.setattr(house_harvest, "acquire", no_db)
    r = await _call(_app(), "POST", "/v1/house-layouts/harvest/revoke",
                    json={"run_id": "  ", "by": "x"})
    assert r.status_code == 400


def _real_routes(routes):
    """Every actual route, in registration order.

    `include_router` used to flatten its routes straight into the parent's
    `routes`; newer Starlette (1.7, which CI resolves) leaves a wrapper object
    there instead — it carries its own `.routes` and has no `.name`, so asking
    the first MATCHING ENTRY for its name raised
    `AttributeError: '_IncludedRouter' object has no attribute 'name'` in CI
    while passing locally on an older pin. Descend through anything that holds
    routes of its own, so what we match against is always a real route and the
    assertions below never touch a wrapper.
    """
    out = []
    for r in routes:
        inner = getattr(r, "routes", None)
        if inner:
            out.extend(_real_routes(inner))
        else:
            out.append(r)
    return out


def test_no_parameterised_route_can_shadow_the_harvest_paths():
    """Starlette matches in registration order; a /house-layouts/{id}... route
    with the same method and segment count would win if it came first."""
    import re

    from starlette.routing import Match
    app = _app()
    want = {("POST", "/v1/house-layouts/harvest"): "v1_house_layouts_harvest",
            ("GET", "/v1/house-layouts/harvest/stats"): "v1_house_layouts_harvest_stats",
            ("POST", "/v1/house-layouts/harvest/revoke"): "v1_house_layouts_harvest_revoke"}
    for (method, path), name in want.items():
        scope = {"type": "http", "method": method, "path": path, "root_path": ""}
        first = next((r for r in _real_routes(app.router.routes)
                      if getattr(r, "matches", None)
                      and r.matches(scope)[0] == Match.FULL), None)
        assert first is not None, (path, "no route matched at all")
        assert getattr(first, "name", None) == name, (
            path, type(first).__name__, getattr(first, "name", None))
        assert not re.search(r"\{", first.path)


async def test_the_catalogue_takes_the_harvest_filters(monkeypatch):
    seen = []

    async def fake(**kw):
        seen.append(kw)
        return {"layouts": []}
    monkeypatch.setattr(house_layouts, "catalogue", fake)
    r = await _call(_app(), "GET", "/v1/house-layouts?harvested=true&run_id=%20run-7%20")
    assert r.status_code == 200, r.text
    assert seen[-1]["harvested"] is True and seen[-1]["run_id"] == "run-7"
    await _call(_app(), "GET", "/v1/house-layouts")
    assert seen[-1]["harvested"] is None and seen[-1]["run_id"] == ""


# ── one niche, one key: /harvest and /harvest/stats agree on comma tags ────

LABEL = "luxury golf travel, golf resorts"   # BM2's profile-fallback label


async def test_a_comma_tag_is_one_niche_on_harvest_AND_on_stats(monkeypatch, ingest_spy):
    """BM2's fallback label joins focus_area and niche with ', '. Split on every
    comma, /harvest keyed caps and the lock on the fragment 'luxury golf travel'
    while /harvest/stats looked the whole label up and matched nothing — the
    niche sat in seed mode forever. Sent as repeatable `niche` fields, a tag is
    never split, and both routes canonicalise it to the SAME key."""
    r = await _call(_app(), "POST", "/v1/house-layouts/harvest",
                    data={**{k: v for k, v in FORM.items() if k != "niches"},
                          "niche": [LABEL, "golf"]},
                    files={"file": ("a.png", PNG, "image/png")})
    assert r.status_code == 200, r.text
    sent = ingest_spy[0]["niches"]
    stored = house_harvest.parse_niches(sent)
    assert stored == ["luxury golf travel golf resorts", "golf"]

    asked = {}

    class _Conn:
        async def fetch(self, sql, *a):
            asked.setdefault("tags", a[0])
            return [{"tag": stored[0], "status": "approved", "layout_type": "offer_card",
                     "held_reason": "", "n": 30, "recent": 0}] \
                if "count(DISTINCT" not in sql else []

        async def fetchrow(self, sql, *a):
            return {"total_approved": 30, "harvested_approved": 30}

    import contextlib

    @contextlib.asynccontextmanager
    async def _acq(tenant_id=None):
        yield _Conn()
    monkeypatch.setattr(house_harvest, "acquire", _acq)
    r = await _call(_app(), "GET", "/v1/house-layouts/harvest/stats",
                    params={"niche": [LABEL]})
    assert r.status_code == 200, r.text
    assert asked["tags"] == [stored[0]], "stats must key the niche as /harvest stored it"
    body = r.json()["niches"]
    assert body[stored[0]]["approved"] == 30


async def test_a_label_with_many_commas_is_not_refused_as_too_many_tags(ingest_spy):
    """Split on commas, a 3-part label plus two tag names made 5 tags → 400 →
    BM2 recorded a FINAL rejected_input and lost an image it had paid to
    screen. As repeatable fields it is 3 tags."""
    r = await _call(_app(), "POST", "/v1/house-layouts/harvest",
                    data={**{k: v for k, v in FORM.items() if k != "niches"},
                          "niche": ["golf courses, luxury travel, resorts", "golf", "travel"]},
                    files={"file": ("a.png", PNG, "image/png")})
    assert r.status_code == 200, r.text
    assert house_harvest.parse_niches(ingest_spy[0]["niches"]) == [
        "golf courses luxury travel resorts", "golf", "travel"]


async def test_niches_legacy_string_still_splits_and_repeated_niches_do_not(ingest_spy):
    r = await _call(_app(), "POST", "/v1/house-layouts/harvest", data=FORM,
                    files={"file": ("a.png", PNG, "image/png")})
    assert r.status_code == 200, r.text
    assert ingest_spy[-1]["niches"] == ["golf resort", "golf"]
    r = await _call(_app(), "POST", "/v1/house-layouts/harvest",
                    data={**FORM, "niches": [LABEL, "golf"]},
                    files={"file": ("a.png", PNG, "image/png")})
    assert r.status_code == 200, r.text
    assert ingest_spy[-1]["niches"] == [LABEL, "golf"]


async def test_niche_and_niches_together_are_400(ingest_spy):
    r = await _call(_app(), "POST", "/v1/house-layouts/harvest",
                    data={**FORM, "niche": ["golf"]},
                    files={"file": ("a.png", PNG, "image/png")})
    assert r.status_code == 400, r.text
    assert ingest_spy == []


def test_a_caller_that_strips_commas_itself_lands_on_the_same_key():
    """The other way BM2 may fix it — replace ',' with ' ' in its own tag
    cleaning and keep the comma-joined field: the key is identical."""
    bm2_clean = " ".join(LABEL.replace(",", " ").lower().split())
    assert house_harvest.parse_niches(f"{bm2_clean},golf")[0] \
        == house_harvest.parse_niches([LABEL, "golf"])[0] \
        == house_harvest.canon_niche(LABEL)
