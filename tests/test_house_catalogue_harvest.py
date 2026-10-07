"""The curation screen's view of the harvest: catalogue() filters and columns,
and migration 071 staying additive.

No database: queries are answered by a recorder keyed on their SQL.
"""

import asyncio
import json
from pathlib import Path

import pytest

from james_os import house_layouts as hl

pytestmark = pytest.mark.nodb

MIGRATION = Path(__file__).resolve().parents[1] / "migrations" / "071_house_harvest.sql"


class UndefinedColumnError(Exception):
    """Named like asyncpg's, which is all catalogue() looks at."""


class _Conn:
    def __init__(self, *, missing_columns=False):
        self.calls: list[tuple[str, tuple]] = []
        self.missing_columns = missing_columns

    async def fetch(self, sql, *a):
        self.calls.append((sql, a))
        if "SELECT status, count(*)" in sql:
            return [{"status": "approved", "n": 3}, {"status": "candidate", "n": 2}]
        if "SELECT layout_type, count(*)" in sql:
            return [{"layout_type": "offer_card", "n": 5}]
        if self.missing_columns and "harvest_run_id," in sql:
            raise UndefinedColumnError('column "harvest_run_id" does not exist')
        row = {"id": "x", "spec": json.dumps({"kind": "graphic_card"}), "status": "approved"}
        if "harvest_meta" in sql:
            row |= {"harvest_run_id": "run-1", "source_key": "onc:" + "a" * 40,
                    "harvest_meta": json.dumps({"author": "a"}), "family_key": "f"}
        return [row]

    async def fetchval(self, sql, *a):
        self.calls.append((sql, a))
        if "count(*)" in sql:
            return 1
        return ["golf"]


def _wire(monkeypatch, conn):
    tenants = []

    class _Acq:
        def __call__(self, tenant=None, **kw):
            tenants.append(tenant)
            return self

        async def __aenter__(self):
            return conn

        async def __aexit__(self, *a):
            return False
    monkeypatch.setattr(hl, "acquire", _Acq())
    return tenants


def _page_sql(conn):
    return next((s, a) for s, a in conn.calls if "ORDER BY status, created_at DESC" in s)


def test_harvest_columns_are_returned_and_parsed(monkeypatch):
    conn = _Conn()
    tenants = _wire(monkeypatch, conn)
    out = asyncio.run(hl.catalogue())
    sql, _ = _page_sql(conn)
    for col in ("family_key", "review_note", "harvest_run_id", "source_key", "harvest_meta"):
        assert col in sql.split("FROM house_layouts")[0], col
    row = out["layouts"][0]
    assert row["harvest_meta"] == {"author": "a"}, "jsonb arrives parsed"
    assert row["spec"] == {"kind": "graphic_card"}
    assert out["matched"] == 1 and out["total"] == 5
    assert tenants and all(t is None for t in tenants)


def test_harvested_true_false_and_run_id_filter_the_page(monkeypatch):
    conn = _Conn()
    _wire(monkeypatch, conn)
    asyncio.run(hl.catalogue(harvested=True))
    sql, _ = _page_sql(conn)
    where = sql.split("WHERE", 1)[1].split("ORDER BY")[0]
    assert "harvest_run_id <> ''" in where and "uploaded_by LIKE 'harvest:%'" in where
    assert "NOT" not in where

    conn = _Conn()
    _wire(monkeypatch, conn)
    asyncio.run(hl.catalogue(harvested=False))
    sql, _ = _page_sql(conn)
    assert "NOT (harvest_run_id <> ''" in sql

    conn = _Conn()
    _wire(monkeypatch, conn)
    asyncio.run(hl.catalogue(run_id="run-1", status="approved"))
    sql, args = _page_sql(conn)
    assert "harvest_run_id = $2" in sql and args == ("approved", "run-1")
    # the matched count is filtered the same way
    msql, margs = next((s, a) for s, a in conn.calls
                       if s.startswith("SELECT count(*) FROM house_layouts"))
    assert "harvest_run_id = $2" in msql and margs == args


def test_no_filter_means_no_harvest_clause(monkeypatch):
    conn = _Conn()
    _wire(monkeypatch, conn)
    asyncio.run(hl.catalogue())
    sql, _ = _page_sql(conn)
    assert "WHERE" not in sql


def test_a_database_without_071_still_answers_the_unfiltered_screen(monkeypatch):
    conn = _Conn(missing_columns=True)
    _wire(monkeypatch, conn)
    out = asyncio.run(hl.catalogue())
    assert out["matched"] == 1
    # ...but a harvest filter on such a database is an error, never a silent "all rows"
    conn = _Conn(missing_columns=True)
    _wire(monkeypatch, conn)
    with pytest.raises(UndefinedColumnError):
        asyncio.run(hl.catalogue(harvested=True))


# ── migration 071 ─────────────────────────────────────────────────────────


def test_migration_071_is_additive_and_leaves_the_source_kind_check_alone():
    sql = MIGRATION.read_text()
    code = "\n".join(line.split("--")[0] for line in sql.splitlines()).lower()
    for col in ("source_key", "harvest_run_id", "harvest_meta"):
        assert f"add column if not exists {col}" in " ".join(code.split()), col
    assert "create unique index if not exists house_layouts_source_key_uq" in code
    assert "where source_key <> ''" in code
    assert "create index if not exists house_layouts_harvest_run_ix" in code
    assert "where harvest_run_id <> ''" in code
    for forbidden in ("drop ", "source_kind_check", "alter column", "delete ", "update "):
        assert forbidden not in code, forbidden
