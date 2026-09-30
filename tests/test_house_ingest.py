"""Uploading a reference image into the shared catalogue.

The point of the upload path: learning only from competitors caps the catalogue at
what competitors happen to post, so a brand wanting a kind of post nobody in its
niche makes has nowhere to get it. These tests cover the parts that are easy to
get silently wrong — the tenant never appearing, an unreadable image being
reported as success, and a re-upload quietly doubling the pool.
"""

import json
from types import SimpleNamespace

import pytest

from james_os import house_layouts


class _Conn:
    """Records what was executed, and answers the INSERT ... RETURNING."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple]] = []
        self.fresh = True

    async def fetchrow(self, sql, *args):
        self.calls.append((sql, args))
        return {"id": "11111111-1111-1111-1111-111111111111", "fresh": self.fresh}

    async def fetchval(self, sql, *args):
        self.calls.append((sql, args))
        return "11111111-1111-1111-1111-111111111111"

    async def execute(self, sql, *args):
        self.calls.append((sql, args))

    async def fetch(self, sql, *args):
        self.calls.append((sql, args))
        return []


class _Acquire:
    """Stands in for db.acquire(), remembering the tenant each block asked for."""

    def __init__(self, conn: _Conn) -> None:
        self.conn = conn
        self.tenants: list = []

    def __call__(self, tenant=None):
        self.tenants.append(tenant)
        return self

    async def __aenter__(self):
        return self.conn

    async def __aexit__(self, *a):
        return False


@pytest.fixture
def wired(monkeypatch):
    conn = _Conn()
    acq = _Acquire(conn)
    monkeypatch.setattr(house_layouts, "acquire", acq)
    return SimpleNamespace(conn=conn, acquire=acq)


def _stub_extract(monkeypatch, spec):
    async def fake(_image):
        return spec
    import james_os.design_cloner as dc
    monkeypatch.setattr(dc, "extract_template_spec", fake)


GOOD = {
    "kind": "graphic_card",
    "background": {"treatment": "solid"},
    "elements": [
        {"role": "kicker", "text": "NEW", "box": {"x": 0.1, "y": 0.1, "w": 0.4, "h": 0.06}},
        {"role": "headline", "text": "A statement", "box": {"x": 0.1, "y": 0.2, "w": 0.8, "h": 0.2}},
        {"role": "subhead", "text": "and a line under it", "box": {"x": 0.1, "y": 0.45, "w": 0.7, "h": 0.1}},
    ],
}


@pytest.mark.asyncio
async def test_an_upload_never_touches_a_tenant(wired, monkeypatch):
    """THE isolation property. An uploaded layout belongs to the platform from the
    moment it arrives: it is not a brand's row promoted later, so it must never
    pass through a tenant scope where it could pick up or leak brand material."""
    _stub_extract(monkeypatch, GOOD)
    out = await house_layouts.ingest(b"\x89PNG fake", title="ref.png", by="a@b.c")
    assert out["ok"] is True
    assert wired.acquire.tenants == [None], wired.acquire.tenants


@pytest.mark.asyncio
async def test_it_names_the_layout_on_the_way_in(wired, monkeypatch):
    """The curation screen filters on layout_type, so it has to be set at insert
    time — a row that arrives unnamed is invisible to every type filter."""
    _stub_extract(monkeypatch, GOOD)
    out = await house_layouts.ingest(b"\x89PNG fake")
    assert out["layout_type"] == "announcement"
    assert out["label"] == "typographic announcement"
    sql, args = wired.conn.calls[-1]
    assert "layout_type" in sql
    assert "announcement" in args


@pytest.mark.asyncio
async def test_it_arrives_as_curated_not_as_a_brands_own(wired, monkeypatch):
    _stub_extract(monkeypatch, GOOD)
    await house_layouts.ingest(b"\x89PNG fake")
    sql, _ = wired.conn.calls[-1]
    assert "'curated'" in sql


@pytest.mark.asyncio
async def test_an_unreadable_image_is_a_clean_no_not_a_success(wired, monkeypatch):
    """Plenty of real images are photographs with no layout in them. That has to
    come back as a reportable reason per file, so one bad image in a batch of
    twelve does not fail the other eleven and does not silently count as added."""
    for junk in (None, {}, {"elements": []}):
        _stub_extract(monkeypatch, junk)
        out = await house_layouts.ingest(b"\x89PNG fake")
        assert out["ok"] is False, junk
        assert out["reason"]
    # nothing was written for any of them
    assert wired.conn.calls == []


@pytest.mark.asyncio
async def test_approve_skips_the_queue_and_default_does_not(wired, monkeypatch):
    _stub_extract(monkeypatch, GOOD)
    assert (await house_layouts.ingest(b"x", approve=True))["status"] == "approved"
    assert (await house_layouts.ingest(b"x", approve=False))["status"] == "candidate"


@pytest.mark.asyncio
async def test_a_reupload_updates_rather_than_twins(wired, monkeypatch):
    """Idempotent on the fingerprint. The curator who drags the same folder in
    twice must not end up with a pool of pairs."""
    _stub_extract(monkeypatch, GOOD)
    await house_layouts.ingest(b"x", title="first")
    sql, _ = wired.conn.calls[-1]
    assert "ON CONFLICT (fingerprint)" in sql
    assert "DO UPDATE SET" in sql
    # and the second one is reported as the duplicate it is
    wired.conn.fresh = False
    assert (await house_layouts.ingest(b"x"))["duplicate"] is True


@pytest.mark.asyncio
async def test_niche_tags_are_trimmed_and_capped(wired, monkeypatch):
    _stub_extract(monkeypatch, GOOD)
    await house_layouts.ingest(
        b"x", niches=["  golf ", "", "   ", "real estate"] + [f"n{i}" for i in range(20)])
    _, args = wired.conn.calls[-1]
    tags = next(a for a in args if isinstance(a, list))
    assert "golf" in tags and "real estate" in tags
    assert "" not in tags
    assert len(tags) <= 12


@pytest.mark.asyncio
async def test_the_spec_is_stored_as_json_not_as_a_python_repr(wired, monkeypatch):
    """A dict passed to a jsonb parameter would arrive as "{'a': 1}" — single
    quotes, not JSON — and never parse back."""
    _stub_extract(monkeypatch, GOOD)
    await house_layouts.ingest(b"x")
    _, args = wired.conn.calls[-1]
    blob = next(a for a in args if isinstance(a, str) and a.startswith("{"))
    assert json.loads(blob)["elements"][1]["role"] == "headline"
