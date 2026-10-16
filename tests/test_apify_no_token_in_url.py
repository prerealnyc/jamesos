"""The Apify key rides in the Authorization header — never in a URL.

What went wrong: every Apify call sent the key as `?token=...`. httpx logs each
request URL at INFO and puts it in every HTTPStatusError, so the key was
written in plain text into the Railway logs. Each call site is driven here
through a mocked transport (nothing reaches Apify) and every captured request
is checked: no key in the URL, no `token` param, and the Bearer header set.
"""

import httpx
import pytest

from james_os import competitor_apify, competitor_media, dashboard_api
from james_os.apify import ApifyTrendProvider
from james_os.config import settings

pytestmark = pytest.mark.nodb

KEY = "apify_api_FAKEKEY123"


def _handler(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    if path.endswith("/run-sync-get-dataset-items") or path.endswith("/dataset/items"):
        return httpx.Response(200, json=[{"id": "x"}])
    if path.endswith("/runs"):
        return httpx.Response(201, json={"data": {"id": "run1", "status": "READY"}})
    if "/actor-runs/" in path:
        return httpx.Response(200, json={"data": {"id": "run1", "status": "SUCCEEDED"}})
    if path.endswith("/users/me"):
        return httpx.Response(200, json={"data": {"username": "u", "plan": {"id": "p"}}})
    return httpx.Response(404)


@pytest.fixture
def captured(monkeypatch):
    seen: list[httpx.Request] = []
    real = httpx.AsyncClient

    def handler(request):
        seen.append(request)
        return _handler(request)

    def client(*a, **kw):
        kw["transport"] = httpx.MockTransport(handler)
        return real(*a, **kw)

    monkeypatch.setattr(httpx, "AsyncClient", client)
    monkeypatch.setattr(settings, "apify_api_key", KEY)
    return seen


def _assert_clean(seen):
    assert seen, "no request was made"
    for r in seen:
        assert KEY not in str(r.url)
        assert "token" not in r.url.params
        assert "token=" not in str(r.url)
        assert r.headers["Authorization"] == f"Bearer {KEY}"


async def test_trend_provider_run_sync(captured):
    items = await ApifyTrendProvider(KEY)._run_actor("apify~instagram-scraper", {"x": 1})
    assert items == [{"id": "x"}]
    _assert_clean(captured)
    assert captured[0].url.path.endswith("/acts/apify~instagram-scraper/run-sync-get-dataset-items")


async def test_competitor_apify_run_keeps_timeout_param(captured):
    items, err = await competitor_apify._run("streamers~youtube-scraper", {"x": 1})
    assert (items, err) == ([{"id": "x"}], "")
    _assert_clean(captured)
    assert captured[0].url.params.get("timeout") == str(competitor_apify._RUN_SECONDS)


async def test_competitor_media_start_poll_and_dataset(captured, monkeypatch):
    async def no_sleep(_s):
        return None

    monkeypatch.setattr(competitor_media.asyncio, "sleep", no_sleep)
    items, err = await competitor_media._run_actor("apify~instagram-scraper", {"x": 1})
    assert (items, err) == ([{"id": "x"}], "")
    paths = [r.url.path for r in captured]
    assert any(p.endswith("/runs") for p in paths)
    assert any(p.endswith("/actor-runs/run1") for p in paths)
    assert any(p.endswith("/actor-runs/run1/dataset/items") for p in paths)
    _assert_clean(captured)


async def test_competitor_media_abort_on_timeout(monkeypatch):
    seen: list[httpx.Request] = []
    real = httpx.AsyncClient

    def handler(request):
        seen.append(request)
        if request.url.path.endswith("/runs"):
            return httpx.Response(201, json={"data": {"id": "run1"}})
        return httpx.Response(200, json={"data": {"status": "RUNNING"}})

    monkeypatch.setattr(httpx, "AsyncClient",
                        lambda *a, **kw: real(*a, **{**kw, "transport": httpx.MockTransport(handler)}))
    monkeypatch.setattr(settings, "apify_api_key", KEY)
    monkeypatch.setattr(competitor_media, "_MAX_POLLS", 2)

    async def no_sleep(_s):
        return None

    monkeypatch.setattr(competitor_media.asyncio, "sleep", no_sleep)
    items, err = await competitor_media._run_actor("a~b", {})
    assert items == [] and "aborted" in err
    assert seen[-1].url.path.endswith("/abort")
    _assert_clean(seen)


async def test_dashboard_apify_probe(captured):
    async with httpx.AsyncClient() as c:
        out = await dashboard_api._probe(c, "apify")
    assert out["status"] == "ok"
    _assert_clean(captured)
