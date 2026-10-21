"""Bad input to /v1/media/rehost must say so, and must not read as our outage.

The endpoint answered 502 "could not fetch source media" to everything. One
caller shipped a reserved placeholder url and got 29 of those in a single day,
which looked like this service failing and so went unexamined for days. The
input was wrong; only the status code was ours.

`raise_for_status()` sat inside a blanket `except Exception` that mapped every
outcome to 502, so a 404 from the source and a dead socket were indistinguishable.
The source's status is now read outside that handler.

These are pure-logic: the hostname matcher is a function, and the fetch is
mocked. Nothing here resolves a name or opens a socket — an earlier draft of
these cases asserted on cdn.myexample.com through the real netguard and was
really asserting that a made-up hostname does not exist in DNS.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

pytestmark = pytest.mark.nodb

from james_os.api_v1 import _placeholder_host  # noqa: E402


# ── the hostname matcher ──────────────────────────────────────────────────────

@pytest.mark.parametrize("url", [
    "https://example.com/mock-agentopus.mp4",   # the url that caused all 29
    "https://example.net/a.mp4",
    "https://example.org/a.mp4",
    "http://localhost/a.mp4",
    "http://localhost:8000/a.mp4",
    "https://cdn.example.com/a.mp4",
    "https://anything.test/a.mp4",
    "https://anything.invalid/a.mp4",
    "https://printer.local/a.mp4",
    "https://EXAMPLE.COM/a.mp4",                # case
    "https://example.com./a.mp4",               # trailing root dot
])
def test_reserved_hosts_are_recognised(url):
    assert _placeholder_host(url), f"{url} should be refused as a placeholder"


@pytest.mark.parametrize("url", [
    "https://cdn.myexample.com/a.mp4",          # a substring test would block this
    "https://example.community/a.mp4",          # and this
    "https://notexample.com/a.mp4",
    "https://evil.com/?x=example.com",          # a substring test would MISS this
    "https://example.com.attacker.net/a.mp4",   # and this
    "https://opusclip-prod.s3.amazonaws.com/x.mp4",
    "https://scontent.cdninstagram.com/v/x.jpg",
])
def test_real_hosts_are_not_mistaken_for_placeholders(url):
    assert _placeholder_host(url) == "", f"{url} is a real host and must be fetched"


def test_a_url_with_no_host_is_not_a_placeholder():
    """It is caught by the http(s) check instead; this must not crash."""
    assert _placeholder_host("http:///a.mp4") == ""
    assert _placeholder_host("not a url") == ""


# ── the endpoint's status codes ───────────────────────────────────────────────

class _Resp:
    def __init__(self, status_code=200, content=b"xx", content_type="video/mp4"):
        self.status_code = status_code
        self.content = content
        self.headers = {"content-type": content_type}

    def raise_for_status(self):           # must no longer be what decides
        raise AssertionError("the endpoint must read status_code, not raise_for_status")


class _Client:
    """Records whether a fetch was attempted at all."""

    def __init__(self, resp=None, exc=None, log=None):
        self._resp, self._exc, self._log = resp, exc, log if log is not None else []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def get(self, url, **kw):
        self._log.append(url)
        if self._exc:
            raise self._exc
        return self._resp


async def _call(monkeypatch, *, url, status="public", resp=None, exc=None):
    """Drive the endpoint with the network and storage mocked out."""
    import httpx

    from james_os import api_v1

    fetched: list[str] = []
    monkeypatch.setattr(
        httpx, "AsyncClient",
        lambda *a, **kw: _Client(resp=resp or _Resp(), exc=exc, log=fetched),
    )

    async def fake_status(u, *, allow_http=False):
        return status

    import james_os.netguard as netguard
    monkeypatch.setattr(netguard, "url_public_status", fake_status)

    class _Store:
        def save(self, tenant, data, name):
            return (f"https://ours/storage/{name}", name)

    import james_os.media as media
    monkeypatch.setattr(media, "storage", lambda: _Store())

    from fastapi import HTTPException
    body = api_v1.MediaRehost(url=url, label="clip")
    try:
        out = await api_v1.v1_media_rehost(body, "00000000-0000-0000-0000-000000000001")
        return None, out, fetched
    except HTTPException as exc_:
        return exc_, None, fetched


@pytest.mark.asyncio
async def test_a_placeholder_url_is_422_and_is_never_fetched(monkeypatch):
    """The whole bug, in one case."""
    err, _out, fetched = await _call(
        monkeypatch, url="https://example.com/mock-agentopus.mp4")
    assert err is not None and err.status_code == 422
    assert fetched == [], "a placeholder host must not cost a fetch"


@pytest.mark.asyncio
async def test_a_source_404_is_422_not_502(monkeypatch):
    err, _out, fetched = await _call(
        monkeypatch, url="https://real.test-host.example-cdn.com/a.mp4",
        resp=_Resp(status_code=404, content=b""))
    assert err is not None and err.status_code == 422
    assert "404" in str(err.detail)
    assert fetched, "it must actually have tried"


@pytest.mark.asyncio
async def test_a_source_503_is_still_502(monkeypatch):
    """A real outage at the source must keep reading as one."""
    err, _out, _ = await _call(
        monkeypatch, url="https://real.test-host.example-cdn.com/a.mp4",
        resp=_Resp(status_code=503, content=b""))
    assert err is not None and err.status_code == 502
    assert "503" in str(err.detail)


@pytest.mark.asyncio
async def test_a_transport_failure_is_502(monkeypatch):
    import httpx
    err, _out, _ = await _call(
        monkeypatch, url="https://real.test-host.example-cdn.com/a.mp4",
        exc=httpx.ConnectError("no route"))
    assert err is not None and err.status_code == 502
    assert "could not fetch" in str(err.detail)


@pytest.mark.asyncio
async def test_a_private_address_is_422_and_is_never_fetched(monkeypatch):
    err, _out, fetched = await _call(
        monkeypatch, url="https://internal.corp.example-cdn.com/a.mp4",
        status="blocked")
    assert err is not None and err.status_code == 422
    assert fetched == [], "the SSRF guard must refuse BEFORE the fetch"


@pytest.mark.asyncio
async def test_an_unresolvable_host_is_502_so_a_dns_blip_is_retryable(monkeypatch):
    err, _out, fetched = await _call(
        monkeypatch, url="https://gone.example-cdn.com/a.mp4", status="unresolved")
    assert err is not None and err.status_code == 502
    assert fetched == []


@pytest.mark.asyncio
async def test_an_empty_200_body_is_422(monkeypatch):
    """Not our failure, and retrying it will not help."""
    err, _out, _ = await _call(
        monkeypatch, url="https://real.test-host.example-cdn.com/a.mp4",
        resp=_Resp(status_code=200, content=b""))
    assert err is not None and err.status_code == 422


@pytest.mark.asyncio
async def test_a_good_url_still_rehosts(monkeypatch):
    """The happy path is untouched — including the extension fix it carries."""
    _err, out, fetched = await _call(
        monkeypatch, url="https://real.test-host.example-cdn.com/a.jpg",
        resp=_Resp(status_code=200, content=b"jpegbytes", content_type="image/jpeg"))
    assert out is not None, "a good url must still be re-hosted"
    assert out["rehosted"] is True and out["durable"] is True
    assert out["bytes"] == len(b"jpegbytes")
    assert out["url"].endswith(".jpg"), "the content-type still picks the extension"
    assert fetched


@pytest.mark.asyncio
async def test_our_own_storage_is_a_noop_and_needs_no_network(monkeypatch):
    _err, out, fetched = await _call(
        monkeypatch,
        url="https://x.supabase.co/storage/v1/object/public/media/a.mp4")
    assert out is not None and out["rehosted"] is False and out["durable"] is True
    assert fetched == []


@pytest.mark.asyncio
async def test_a_non_http_url_is_400(monkeypatch):
    err, _out, _ = await _call(monkeypatch, url="file:///etc/passwd")
    assert err is not None and err.status_code == 400
