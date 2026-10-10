"""BM2 reaches the draft behind any of its pieces — not only the newest 200."""

import asyncio
import contextlib

import pytest
from fastapi import HTTPException

from james_os import api_v1


@pytest.fixture(autouse=True)
def fresh_pool():
    yield


def _db(monkeypatch, rows):
    seen = {}

    class _Conn:
        # *args, not (sql, url): production added a tenant-scoping $2 to this
        # query, so it now passes (sql, url, tenant_id). A double pinned to the
        # old arity fails with TypeError and reads like a code bug.
        async def fetchrow(self, sql, *args):
            url = args[0] if args else None
            seen["sql"], seen["url"] = sql, url
            seen["params"] = args
            return rows.get(url)

    @contextlib.asynccontextmanager
    async def _acquire(tenant_id=None):
        yield _Conn()

    monkeypatch.setattr(api_v1, "acquire", _acquire)
    return seen


def test_a_draft_is_found_by_its_picture_wherever_it_sits(monkeypatch):
    seen = _db(monkeypatch, {"https://cdn/x.png": {"id": "7b0e6a1e-0000-4000-8000-000000000009",
                                                   "status": "pending"}})
    out = asyncio.run(api_v1.v1_queue_find_by_image("https://cdn/x.png", "t"))
    assert out == {"id": "7b0e6a1e-0000-4000-8000-000000000009", "status": "pending"}
    assert "LIMIT 1" in seen["sql"] and "image_url" in seen["sql"] and "media_url" in seen["sql"]
    assert "status" not in seen["sql"].split("WHERE")[1].split("ORDER")[0].replace("status FROM", ""), \
        "not limited to the queue's newest window — any draft with the picture"


def test_no_draft_is_a_404_and_no_url_a_422(monkeypatch):
    _db(monkeypatch, {})
    with pytest.raises(HTTPException) as e:
        asyncio.run(api_v1.v1_queue_find_by_image("https://cdn/none.png", "t"))
    assert e.value.status_code == 404
    with pytest.raises(HTTPException) as e2:
        asyncio.run(api_v1.v1_queue_find_by_image("  ", "t"))
    assert e2.value.status_code == 422
