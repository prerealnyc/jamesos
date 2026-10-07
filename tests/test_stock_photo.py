"""Unsplash stock-photo hero fallback — contract tests.

Proves the honesty contract (no key => no network; any failure => None) and the
Unsplash API-Terms compliance (Client-ID auth, mandatory download trigger),
without touching the network — httpx.AsyncClient is faked.
"""

from __future__ import annotations

import httpx

from james_os import stock_photo


class _FakeResp:
    def __init__(self, status=200, json_data=None, content=b""):
        self.status_code = status
        self._json = json_data or {}
        self.content = content

    def json(self):
        return self._json

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("err", request=None, response=None)  # type: ignore[arg-type]


class _FakeClient:
    """Async-context httpx stand-in; routes GETs through a handler + records calls."""

    calls: list = []

    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def get(self, url, params=None, headers=None):
        _FakeClient.calls.append({"url": url, "params": params, "headers": headers})
        return _FakeClient._handler(url, params, headers)


_SEARCH_JSON = {
    "results": [
        {
            "id": "abc123",
            "urls": {"regular": "https://images.unsplash.com/photo-abc?w=1080"},
            "links": {"download_location": "https://api.unsplash.com/photos/abc123/download",
                      "html": "https://unsplash.com/photos/abc123"},
            "user": {"name": "Jane Doe", "links": {"html": "https://unsplash.com/@jane"}},
        }
    ]
}


def _install(monkeypatch, handler, key="testkey"):
    _FakeClient.calls = []
    _FakeClient._handler = staticmethod(handler)
    monkeypatch.setattr(stock_photo.settings, "unsplash_access_key", key, raising=False)
    monkeypatch.setattr(stock_photo.httpx, "AsyncClient", _FakeClient)
    # images.unsplash.com is public; skip the real DNS/SSRF check in the test.
    async def _ok(url, **k):
        return True
    monkeypatch.setattr(stock_photo, "url_is_public", _ok)


async def test_no_key_makes_no_network_call(monkeypatch):
    def _boom(*a, **k):  # any network attempt is a failure
        raise AssertionError("network call made despite no key")
    monkeypatch.setattr(stock_photo.settings, "unsplash_access_key", "", raising=False)
    monkeypatch.setattr(stock_photo.httpx, "AsyncClient", _boom)
    assert await stock_photo.fetch_unsplash_hero("golf new mexico") is None


async def test_happy_path_returns_tuple_client_id_and_download_trigger(monkeypatch):
    def handler(url, params, headers):
        if url == stock_photo._SEARCH_URL:
            return _FakeResp(200, _SEARCH_JSON)
        if "images.unsplash.com" in url:
            return _FakeResp(200, content=b"JPEGBYTES")
        # download_location trigger
        return _FakeResp(200, {"url": "https://cdn/dl"})
    _install(monkeypatch, handler)

    got = await stock_photo.fetch_unsplash_hero("staten island waterfront home")
    assert got is not None
    url, data = got
    assert url.startswith("http")
    assert data == b"JPEGBYTES"
    # Every api.unsplash.com call must use Client-ID auth, NOT Bearer.
    api_calls = [c for c in _FakeClient.calls if "api.unsplash.com" in c["url"]]
    assert api_calls, "expected api.unsplash.com calls"
    for c in api_calls:
        auth = (c["headers"] or {}).get("Authorization", "")
        assert auth.startswith("Client-ID "), f"bad auth header: {auth!r}"
    # The mandatory download-registration trigger fired.
    assert any(c["url"].endswith("/download") for c in _FakeClient.calls)


async def test_rate_limited_returns_none(monkeypatch):
    def handler(url, params, headers):
        return _FakeResp(429, {})
    _install(monkeypatch, handler)
    assert await stock_photo.fetch_unsplash_hero("anything") is None


async def test_empty_results_returns_none(monkeypatch):
    def handler(url, params, headers):
        return _FakeResp(200, {"results": []})
    _install(monkeypatch, handler)
    assert await stock_photo.fetch_unsplash_hero("anything") is None


async def test_exclude_skips_the_only_result(monkeypatch):
    def handler(url, params, headers):
        return _FakeResp(200, _SEARCH_JSON)
    _install(monkeypatch, handler)
    only = _SEARCH_JSON["results"][0]["urls"]["regular"]
    assert await stock_photo.fetch_unsplash_hero("x", exclude=[only]) is None


def test_query_builder_strips_noise():
    q = stock_photo._build_query("Check out #golf @brand https://x.co our New Mexico open 🏌️")
    assert "#" not in q and "@" not in q and "http" not in q
    assert "Mexico" in q or "golf" in q
