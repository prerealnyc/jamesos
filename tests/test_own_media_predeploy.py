"""Pre-deploy review of own-post intake: what an OpenAI error says about ONE
picture versus the whole engine, and the post-link canon.

No network, no database. What these hold:

  * describe() stamps a photo ({}) only on an answer about the picture itself
    (413, 415, a 400 naming the image); a 400 saying OpenAI could not DOWNLOAD
    it is a picture-scoped retry; everything account-wide (401/403, 404
    model_not_found, 408/409/429, quota and billing, any 5xx, an unattributable
    400) is an engine-scoped NO_ANSWER;
  * backfill() skips past picture-scoped retries and stops at engine-scoped ones;
  * post_key's canon is the same string the legacy SQL computes from source_url.
"""

from __future__ import annotations

from uuid import UUID

import httpx
import pytest

from james_os import db as db_module
from james_os import own_media, photo_subject
from james_os.config import settings

pytestmark = pytest.mark.nodb

BRAND = UUID("945dbd37-4ec9-464e-9af5-459aad44eec9")


@pytest.fixture(autouse=True)
def _clean():
    tok = db_module._request_tenant.set(None)
    yield
    db_module._request_tenant.reset(tok)


def _answering(monkeypatch, status, body):
    monkeypatch.setattr(settings, "openai_api_key", "sk-test", raising=False)

    class Client:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, *a, **k):
            req = httpx.Request("POST", url)
            if isinstance(body, (bytes, str)):
                return httpx.Response(status, request=req, content=body)
            return httpx.Response(status, request=req, json=body)
    monkeypatch.setattr(photo_subject.httpx, "AsyncClient", Client)


def _err(code=None, etype="invalid_request_error", message=""):
    return {"error": {"code": code, "type": etype, "message": message, "param": None}}


PICTURE_FINAL = [
    (413, _err(None, message="Request too large")),
    (415, _err(None, message="Unsupported media type")),
    (400, _err("invalid_image_format", message="You uploaded an unsupported image.")),
    (400, _err("image_parse_error", message="Could not parse the image.")),
    (400, _err("invalid_image", message="The image is not valid.")),
    (400, _err(None, message="You uploaded an unsupported image. Please make sure your "
                             "image is valid.")),
    (400, _err(None, message="Invalid image.")),
    (400, _err("image_too_large", message="Image too large.")),
]


@pytest.mark.parametrize("status,body", PICTURE_FINAL)
async def test_an_answer_about_the_picture_is_final(monkeypatch, status, body):
    _answering(monkeypatch, status, body)
    assert await photo_subject.describe("https://s.test/x.heic") == {}


PICTURE_RETRY = [
    (400, _err("invalid_image_url", message="Timeout while downloading https://s.test/x.jpg.")),
    (400, _err("invalid_image_url", message="Error while downloading https://s.test/x.jpg.")),
    (400, _err(None, message="Timeout while downloading https://s.test/x.jpg.")),
]


@pytest.mark.parametrize("status,body", PICTURE_RETRY)
async def test_a_failed_download_of_the_picture_is_a_picture_scoped_retry(monkeypatch, status, body):
    _answering(monkeypatch, status, body)
    got = await photo_subject.describe("https://s.test/x.jpg")
    assert str(got.get(photo_subject.NO_ANSWER) or "").startswith(photo_subject.PICTURE_RETRY), got


ENGINE = [
    (401, _err("invalid_api_key", message="Incorrect API key provided")),
    (403, _err("unsupported_country_region_territory", message="Country not supported")),
    (404, _err("model_not_found", message="The model `gpt-4o-mini` does not exist")),
    (408, _err(None, message="Request timed out")),
    (409, _err(None, message="Conflict")),
    (429, _err("insufficient_quota", "insufficient_quota", "You exceeded your current quota")),
    (429, _err("rate_limit_exceeded", "requests", "Rate limit reached")),
    (400, _err("billing_hard_limit_reached", message="Billing hard limit has been reached")),
    # a quota code wins even when the message also mentions the image
    (400, _err("insufficient_quota", message="Invalid image request: quota exceeded")),
    (400, _err("model_not_found", message="The model does not exist")),
    (404, {"error": "Not found"}),
    (500, _err(None, "server_error", "The server had an error")),
    (503, _err(None, message="Service unavailable")),
]


@pytest.mark.parametrize("status,body", ENGINE)
async def test_an_account_wide_answer_is_an_engine_scoped_no_answer(monkeypatch, status, body):
    _answering(monkeypatch, status, body)
    got = await photo_subject.describe("https://s.test/x.jpg")
    reason = got.get(photo_subject.NO_ANSWER)
    assert reason and not str(reason).startswith(photo_subject.PICTURE_RETRY), got
    assert str(reason).startswith(f"http_{status}")


class _Ctx:
    def __init__(self, conn):
        self.conn = conn

    async def __aenter__(self):
        return self.conn

    async def __aexit__(self, *a):
        return False


class _Conn:
    def __init__(self, rows):
        self.rows, self.sql = list(rows), []

    async def execute(self, sql, *a):
        self.sql.append((" ".join(sql.split()), a))

    async def fetch(self, sql, *a):
        return list(self.rows)


class _Emb:
    model_name = "voyage"

    async def embed(self, texts):
        return [[0.1] * 4]


async def test_backfill_reads_past_a_picture_it_could_not_fetch_and_stops_at_the_engine(monkeypatch):
    rows = [{"id": UUID(int=i), "uri": f"https://s.test/{i}.jpg"} for i in range(6)]
    conn = _Conn(rows)
    monkeypatch.setattr(photo_subject, "acquire", lambda t=None, **k: _Ctx(conn))
    monkeypatch.setattr(photo_subject, "_real_embedder", lambda: _Emb())
    answers = {
        "https://s.test/0.jpg": {photo_subject.NO_ANSWER: "picture:http_400:invalid_image_url"},
        "https://s.test/1.jpg": {"caption": "a green", "place": "", "subject": "a green",
                                 "setting": "outdoor", "tags": ["golf"], "people": 0},
        "https://s.test/2.jpg": {photo_subject.NO_ANSWER: "picture:http_400:invalid_image_url"},
        "https://s.test/3.jpg": {photo_subject.NO_ANSWER: "http_429:insufficient_quota"},
    }
    calls = []

    async def describe(url):
        calls.append(url)
        return answers.get(url, {"caption": "never reached"})
    monkeypatch.setattr(photo_subject, "describe", describe)
    out = await photo_subject.backfill(BRAND)
    assert calls == [f"https://s.test/{i}.jpg" for i in range(4)], \
        "past the two pictures OpenAI could not fetch, stopped at the quota answer"
    assert out["read"] == 1 and out["picture_retry"] == 2
    assert out["deferred"] == "http_429:insufficient_quota"
    stamped = [a[0] for q, a in conn.sql if "subject_read_at" in q]
    assert stamped == [UUID(int=1)], "only the photo that was actually read is stamped"


def _sql_canon(source_url: str) -> str:
    """rtrim(split_part(split_part(source_url, '#', 1), '?', 1), '/')"""
    return source_url.split("#", 1)[0].split("?", 1)[0].rstrip("/")


PATH_IDENTIFIED = [
    "https://www.instagram.com/p/AbC_1-x/?igsh=1#c",
    "https://www.instagram.com/somehandle/reel/Zz9yy00/",
    "https://www.tiktok.com/@somehandle/video/7300000000000000000?lang=en",
    "https://www.facebook.com/somepage/posts/123456/?__cft__=x",
    "https://www.facebook.com/somepage/videos/987/",
    "https://www.facebook.com/reel/555",
    "https://fb.watch/AbCdEf/",
]


@pytest.mark.parametrize("u", PATH_IDENTIFIED)
def test_a_path_identified_link_keeps_the_canon_the_sql_computes(u):
    canon, _ = own_media.post_key(u)
    assert canon and canon == _sql_canon(u)


async def test_a_query_identified_facebook_link_never_reaches_the_legacy_query(monkeypatch):
    seen = []

    class Conn:
        async def fetchrow(self, sql, *a):
            seen.append(a)
            return {"id": "t-legacy"}
    monkeypatch.setattr(own_media, "acquire", lambda t=None, **k: _Ctx(Conn()))
    for u in ("https://www.facebook.com/permalink.php?story_fbid=BBB&id=123",
              "https://www.facebook.com/photo.php?fbid=222",
              "https://www.facebook.com/watch/?v=888"):
        assert await own_media._legacy_own_template(BRAND, u) is None, u
    assert seen == []
    # a path-identified one still asks, with the SQL's own canon
    assert await own_media._legacy_own_template(
        BRAND, "https://www.facebook.com/somepage/posts/123/") == \
        {"template_id": "t-legacy", "legacy": True}
    assert seen == [("https://www.facebook.com/somepage/posts/123", "")]


@pytest.mark.parametrize("body", [_err(None, message="Invalid value for 'max_tokens'"), _err(None, message="x"),
                                  b"<html>bad gateway-ish</html>",
                                  _err("content_policy_violation", message="Your input image may contain content")])
async def test_a_400_on_neither_list_is_left_unread_without_stopping_the_backfill(monkeypatch, body):
    """Stamping it would lose a photo for good; an engine-scoped retry would
    stop every backfill at the head of the queue. Picture-scoped: unread, skipped."""
    _answering(monkeypatch, 400, body)
    got = await photo_subject.describe("https://s.test/x.jpg")
    assert str(got.get(photo_subject.NO_ANSWER)).startswith(photo_subject.PICTURE_RETRY + "http_400"), got
