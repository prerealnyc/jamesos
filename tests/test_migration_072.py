"""Migration 072 (media provenance): additive, idempotent, and in step with the
code that writes it.

The model↔migration drift lesson: a column the code writes with no migration is
a 42703 on every read. So the static tests check every column create_media's
provenance and own_media's INSERT name is added here; the database test applies
the file TWICE inside a transaction that is rolled back, so the shared test
database is left exactly as it was.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from james_os import media, own_media

SQL = (Path(__file__).resolve().parent.parent / "migrations" / "072_media_provenance.sql").read_text()
_CODE_ONLY = "\n".join(line.split("--", 1)[0] for line in SQL.splitlines())
ADDED = set(re.findall(r"ADD COLUMN IF NOT EXISTS\s+(\w+)", _CODE_ONLY))


@pytest.mark.nodb
def test_072_is_additive_only():
    for bad in (r"\bDROP\s+(TABLE|COLUMN|INDEX)\b", r"\bRENAME\b", r"\bDELETE\s+FROM\b",
                r"\bTRUNCATE\b", r"\bUPDATE\s+media_assets\b", r"\bALTER\s+COLUMN\b"):
        assert not re.search(bad, _CODE_ONLY, re.I), bad
    assert "ADD COLUMN IF NOT EXISTS" in _CODE_ONLY and "CREATE INDEX IF NOT EXISTS" in _CODE_ONLY


@pytest.mark.nodb
def test_every_column_the_code_writes_is_added_by_072():
    assert set(media.PROVENANCE_COLS) <= ADDED, set(media.PROVENANCE_COLS) - ADDED
    import inspect
    src = inspect.getsource(own_media.insert_photo)
    cols = re.search(r"INSERT INTO media_assets\s*\(([^)]*)\)", src).group(1)
    names = {c.strip() for c in cols.replace("\n", " ").split(",")}
    base = {"role", "source_type", "uri", "file_path", "title", "platform", "mime", "tags",
            "notes"}
    assert names - base <= ADDED, names - base - ADDED


@pytest.mark.nodb
def test_the_vocabularies_match_the_code():
    for origin in (*own_media.OWN_ORIGINS, "owner_upload", "stock", "generated"):
        assert f"'{origin}'" in _CODE_ONLY
    assert "'own_account'" in _CODE_ONLY
    assert "(tenant_id, sha256)" in _CODE_ONLY and "(tenant_id, origin)" in _CODE_ONLY


async def test_072_applies_twice_and_leaves_nothing_behind():
    """Against the suite's own local database, inside a transaction that is
    ROLLED BACK — the schema is checked, then put back as it was."""
    from james_os.db import acquire

    async with acquire() as conn:
        if not await conn.fetchval(
                "SELECT to_regclass('public.media_assets') IS NOT NULL"):
            pytest.skip("no media_assets table in the test database")
        before = {r["column_name"] for r in await conn.fetch(
            "SELECT column_name FROM information_schema.columns WHERE table_name = 'media_assets'")}
        tr = conn.transaction()
        await tr.start()
        try:
            await conn.execute(SQL)
            await conn.execute(SQL)          # idempotent
            after = {r["column_name"] for r in await conn.fetch(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'media_assets'")}
            assert ADDED <= after
            default = await conn.fetchval(
                "SELECT column_default FROM information_schema.columns "
                "WHERE table_name = 'media_assets' AND column_name = 'origin'")
            assert "owner_upload" in str(default)
        finally:
            await tr.rollback()
        still = {r["column_name"] for r in await conn.fetch(
            "SELECT column_name FROM information_schema.columns WHERE table_name = 'media_assets'")}
        assert still == before, "the rollback must leave the shared test database untouched"
