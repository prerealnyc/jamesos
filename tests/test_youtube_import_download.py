"""YouTube import: the Apify key never reaches the screen, and a long video
comes in at a lower resolution instead of failing.

2026-10-14: an import card showed "Client error '403 Forbidden' for url
'https://api.apify.com/v2/acts/...runs?token=apify_api_…'" — the key rode in
the query string and httpx prints the URL. The same day a ~1 GB podcast died at
17 %: the actor kills each attempt at 300 s, and 720p over a residential proxy
cannot finish in that.
"""

import pytest

from james_os import long_form
from james_os.config import settings

pytestmark = pytest.mark.nodb

KEY = "apify_api_TESTKEY0123456789"


class _Resp:
    def __init__(self, payload, status=200):
        self._p, self.status_code = payload, status

    def json(self):
        return self._p

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


def _fake_client(outcomes, calls):
    """outcomes: per quality, (final_status, statusMessage, items)."""
    runs = {}

    class C:
        def __init__(self, *a, **kw):
            calls.append(("client", kw.get("headers") or {}))

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, json=None, params=None, **kw):
            calls.append(("post", url, json, params))
            q = json["quality"]
            rid = f"run-{q}"
            runs[rid] = outcomes[q]
            return _Resp({"data": {"id": rid, "status": "RUNNING"}})

        async def get(self, url, params=None, **kw):
            calls.append(("get", url, params))
            rid = url.split("/actor-runs/")[1].split("/")[0]
            status, msg, items = runs[rid]
            if url.endswith("/dataset/items"):
                return _Resp(items)
            return _Resp({"data": {"status": status, "statusMessage": msg}})

    return C


@pytest.fixture
def fast(monkeypatch):
    monkeypatch.setattr(settings, "apify_api_key", KEY)

    async def _no_sleep(*_a, **_k):
        return None

    monkeypatch.setattr(long_form.asyncio, "sleep", _no_sleep)


async def test_a_long_video_falls_back_to_a_smaller_rendition(fast, monkeypatch):
    calls = []
    item = [{"downloadUrl": "https://api.apify.com/v2/key-value-stores/x/records/v.mp4", "title": "Talk", "fileSizeBytes": 123}]
    import httpx
    monkeypatch.setattr(httpx, "AsyncClient", _fake_client({
        "720p": ("FAILED", "download too slow to finish in time", []),
        "480p": ("FAILED", "download too slow to finish in time", []),
        "360p": ("SUCCEEDED", "", item),
    }, calls))
    url, title, size, quality = await long_form._apify_youtube_resolve("https://youtu.be/abc")
    assert quality == "360p" and title == "Talk" and size == 123
    posts = [c for c in calls if c[0] == "post"]
    assert [p[2]["quality"] for p in posts] == ["720p", "480p", "360p"]
    assert all(p[2]["perVideoTimeoutSecs"] == 300 and p[2]["maxConcurrency"] == 1 for p in posts)
    # the key travels in a header, never in a URL or query string
    assert "token" not in url and KEY not in url
    assert all(not c[-1] for c in calls if c[0] in ("post", "get"))
    assert any(KEY in (c[1].get("Authorization") or "") for c in calls if c[0] == "client")


async def test_when_every_rendition_fails_the_reason_is_given_without_the_key(fast, monkeypatch):
    calls = []
    import httpx
    monkeypatch.setattr(httpx, "AsyncClient", _fake_client(
        {q: ("FAILED", "download too slow to finish in time", []) for q in ("720p", "480p", "360p")}, calls))
    with pytest.raises(RuntimeError) as e:
        await long_form._apify_youtube_resolve("https://youtu.be/abc")
    msg = str(e.value)
    assert "720p" in msg and "360p" in msg and "too slow" in msg
    assert KEY not in msg


def test_stored_errors_never_carry_the_key(monkeypatch):
    monkeypatch.setattr(settings, "apify_api_key", KEY)
    leaked = (f"Client error '403 Forbidden' for url 'https://api.apify.com/v2/acts/a~b/runs?token={KEY}' "
              "and an old one ?token=apify_api_OLDKEY&x=1")
    out = long_form._redact(leaked)
    assert KEY not in out and "OLDKEY" not in out
    assert out.count("token=[redacted]") == 2 and "&x=1" in out
