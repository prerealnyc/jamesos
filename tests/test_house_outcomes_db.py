"""house_layout_outcomes against a real Postgres (the local docker one, :5433).

The stand-in tests in test_house_outcomes.py prove the ranking; these prove the
SQL: migration 073 applies (twice), the upsert accumulates per canonical niche,
the one-query read returns what was written, and discarding a catalogue row
takes its results with it. Every row written here is fingerprinted zztest- and
removed.
"""

import json
from pathlib import Path
from uuid import uuid4

import pytest

from james_os import house_harvest as hh
from james_os import house_layouts as hl
from james_os.db import acquire

MIGRATIONS = Path(__file__).resolve().parent.parent / "migrations"
# 073 references house_layouts, which a fresh local database may not have yet.
NEEDS = ("067_house_layouts.sql", "068_house_layout_types.sql",
         "071_house_harvest.sql", "073_house_layout_outcomes.sql")


async def _migrate():
    async with acquire() as conn:
        for name in NEEDS:
            await conn.execute((MIGRATIONS / name).read_text())


async def _house_row() -> str:
    async with acquire(None) as conn:
        return await conn.fetchval(
            "INSERT INTO house_layouts (kind, spec, fingerprint, source_kind, status) "
            "VALUES ('graphic_card', '{}'::jsonb, $1, 'curated', 'approved') RETURNING id::text",
            f"zztest-outcomes-{uuid4().hex}")


async def _drop(hid: str):
    async with acquire(None) as conn:
        await conn.execute("DELETE FROM house_layouts WHERE id = $1::uuid", hid)


@pytest.mark.asyncio
async def test_073_applies_twice_with_no_rls_and_app_grants():
    await _migrate()
    await _migrate()                        # idempotent
    async with acquire() as conn:
        rls = await conn.fetchval(
            "SELECT relrowsecurity FROM pg_class WHERE relname = 'house_layout_outcomes'")
        pk = await conn.fetch(
            """SELECT a.attname FROM pg_index i
                 JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = ANY(i.indkey)
                WHERE i.indrelid = 'house_layout_outcomes'::regclass AND i.indisprimary""")
        role = await conn.fetchval("SELECT 1 FROM pg_roles WHERE rolname = 'james_app'")
        grants = await conn.fetch(
            "SELECT privilege_type FROM information_schema.role_table_grants "
            "WHERE table_name = 'house_layout_outcomes' AND grantee = 'james_app'")
    assert rls is False
    assert {r["attname"] for r in pk} == {"house_layout_id", "niche"}
    if role:
        assert {g["privilege_type"] for g in grants} >= {"SELECT", "INSERT", "UPDATE"}
        assert "DELETE" not in {g["privilege_type"] for g in grants}


@pytest.mark.asyncio
async def test_verdicts_and_outcomes_accumulate_per_canonical_niche():
    await _migrate()
    hid = await _house_row()
    try:
        for _ in range(5):
            assert await hl.record_verdict(hid, ["golf resort"], True) == ["golf", "hospitality"]
        await hl.record_verdict(hid, ["golf resort"], False)
        await hl.record_outcome(hid, ["golf resort"], 3.0)
        await hl.record_outcome(hid, ["golf resort"], 9.0)          # clamped to 5
        await hl.record_verdict(hid, ["knitting"], True)            # no label -> ''

        got = await hl.outcomes_for([hid])
        rows = {o["niche"]: o for o in got[hid]}
        assert set(rows) == {"golf", "hospitality", ""}
        assert (rows["golf"]["approvals"], rows["golf"]["rejections"],
                rows["golf"]["measured"], rows["golf"]["lift_sum"]) == (5, 1, 2, 8.0)
        assert rows[""]["approvals"] == 1 and rows[""]["measured"] == 0
        # 5 approvals, 1 rejection, 2 measured (lift 3 + 5), each counted ONCE
        # for the brand that filed them under both of its labels
        assert hl.outcome_score(got[hid], ["golf", "hospitality"]) == round(
            (6 / 8) * ((8 + 2) / (2 + 2)), 2)
        assert hl.outcome_score(got[hid], hl.outcome_keys(["golf resort"])) == \
            hl.outcome_score(got[hid], ["golf"])
        assert hl.outcome_score(got[hid], ["real estate"]) == hl.OUTCOME_NEUTRAL
    finally:
        await _drop(hid)


@pytest.mark.asyncio
async def test_discarding_a_catalogue_row_takes_its_results_with_it():
    await _migrate()
    hid = await _house_row()
    await hl.record_outcome(hid, ["golf"], 1.0)
    await _drop(hid)
    async with acquire(None) as conn:
        left = await conn.fetchval(
            "SELECT count(*) FROM house_layout_outcomes WHERE house_layout_id = $1::uuid", hid)
    assert left == 0


@pytest.mark.asyncio
async def test_the_one_query_read_on_a_held_connection_survives_a_missing_table():
    """outcomes_for runs in a SAVEPOINT on the caller's connection: a failure must
    leave that connection's transaction usable for the caller."""
    async with acquire(None) as conn:
        await conn.execute("CREATE TEMP TABLE zztest_probe (x int) ON COMMIT DROP")
        orig = hl._OUTCOMES_SQL
        hl._OUTCOMES_SQL = "SELECT * FROM zztest_no_such_table WHERE $1::uuid[] IS NOT NULL"
        try:
            assert await hl.outcomes_for([str(uuid4())], conn=conn) == {}
        finally:
            hl._OUTCOMES_SQL = orig
        assert await conn.fetchval("SELECT count(*) FROM zztest_probe") == 0


@pytest.mark.asyncio
async def test_a_result_for_a_discarded_catalogue_row_writes_nothing_and_raises_nothing():
    """design_templates.house_layout_id has no FK and 'discard' hard-deletes the
    catalogue row: the plain INSERT raised ForeignKeyViolation (-> /outcome 500)."""
    await _migrate()
    gone = str(uuid4())
    assert await hl.record_outcome(gone, ["golf"], 2.0) == []
    assert await hl.record_verdict(gone, ["golf resort"], False) == []
    async with acquire(None) as conn:
        assert await conn.fetchval(
            "SELECT count(*) FROM house_layout_outcomes WHERE house_layout_id = $1::uuid",
            gone) == 0


# ── added vocabulary labels do not count toward the harvest's caps or stats ──


async def _raw_row(tags, added, *, fam, ltype, status="approved") -> str:
    async with acquire(None) as conn:
        return await conn.fetchval(
            "INSERT INTO house_layouts (kind, spec, fingerprint, family_key, source_kind, "
            "status, layout_type, niches, harvest_meta) VALUES ('graphic_card', '{}'::jsonb, "
            "$1, $2, 'niche', $3, $4, $5::text[], $6::jsonb) RETURNING id::text",
            f"zztest-labels-{uuid4().hex}", fam, status, ltype, tags,
            json.dumps({hl.LABELS_KEY: added}))


async def _counts(tag, fam, ltype) -> dict:
    async with acquire(None) as conn:
        return dict(await conn.fetchrow(hh._COUNTS_SQL, tag, fam, ltype))


@pytest.mark.asyncio
async def test_added_labels_do_not_count_toward_a_harvests_caps_or_its_stats():
    await _migrate()
    fam, ltype = f"zztest-fam-{uuid4().hex}", f"zztest_type_{uuid4().hex[:8]}"
    tag = f"zztest golf {uuid4().hex[:8]}"   # a tag no other run's rows carry
    before = await hh.harvest_stats([tag])
    ids = [
        # a 'golf resort' harvest row: golf is only an added label
        await _raw_row(["golf resort", tag, "hospitality"], [tag, "hospitality"],
                       fam=fam, ltype=ltype),
        # a curated upload the backfill read as golf: every tag is added
        await _raw_row([tag], [tag], fam=fam, ltype=ltype),
        # a real golf harvest row: counts
        await _raw_row([tag], [], fam=fam, ltype=ltype),
    ]
    try:
        c = await _counts(tag, fam, ltype)
        assert (c["family_in_niche"], c["type_in_niche"], c["niche_approved"]) == (1, 1, 1)
        assert c["family_global"] == 3, "the global family cap is not about niches"
        after = await hh.harvest_stats([tag])
        assert after["niches"][tag]["approved"] - before["niches"][tag]["approved"] == 1
    finally:
        for i in ids:
            await _drop(i)


def _harvest_args(fp, tags, added, key):
    return ("graphic_card", "{}", fp, "zztest-fam", "", "announcement", "", "",
            tags, "zztest", "approved", "", key, "zztest-run",
            json.dumps({"source": "zztest", hl.LABELS_KEY: added}))


@pytest.mark.asyncio
async def test_a_second_harvest_finding_the_shape_merges_the_added_labels():
    """The ON CONFLICT path: a 'golf' harvest finding a shape the 'golf resort'
    harvest stored makes golf a REAL tag of it; hospitality stays added."""
    await _migrate()
    fp = f"zztest-merge-{uuid4().hex}"
    first, first_added = hh._with_labels(["golf resort"])
    second, second_added = hh._with_labels(["golf"])
    async with acquire(None) as conn:
        row = await conn.fetchrow(hh._insert_sql(),
                                  *_harvest_args(fp, first, first_added, f"zz:{uuid4().hex}"))
        hid = row["id"]
        try:
            assert first_added == ["golf", "hospitality"]
            again = await conn.fetchrow(hh._insert_sql(), *_harvest_args(
                fp, second, second_added, f"zz:{uuid4().hex}"))
            # (same transaction, so `fresh` cannot tell; the id says it merged)
            assert again["id"] == hid
            meta = await conn.fetchval(
                "SELECT harvest_meta FROM house_layouts WHERE id = $1::uuid", hid)
            meta = json.loads(meta) if isinstance(meta, str) else meta
            assert list(again["niches"]) == ["golf resort", "golf", "hospitality"]
            assert meta[hl.LABELS_KEY] == ["hospitality"]
            assert meta["source"] == "zztest", "the first finder's provenance stays"
        finally:
            await _drop(hid)


@pytest.mark.asyncio
async def test_a_reupload_and_a_retag_keep_the_added_labels_honest(monkeypatch):
    """ingest's ON CONFLICT merge runs in Postgres, and a curator's retag makes
    every tag real."""
    from james_os import design_cloner
    from james_os import design_templates as dt

    await _migrate()
    fp = f"zztest-ingest-{uuid4().hex}"
    spec = {"kind": "graphic_card", "background": {"treatment": "solid"},
            "elements": [
                {"role": "kicker", "text": "NEW", "box": {"x": .1, "y": .1, "w": .4, "h": .06}},
                {"role": "headline", "text": "A statement",
                 "box": {"x": .1, "y": .2, "w": .8, "h": .2}},
                {"role": "subhead", "text": "and a line",
                 "box": {"x": .1, "y": .45, "w": .7, "h": .1}}]}

    async def _read(_image):
        return spec

    monkeypatch.setattr(design_cloner, "extract_template_spec", _read)
    monkeypatch.setattr(dt, "fingerprint", lambda _s: fp)

    async def _meta(hid):
        async with acquire(None) as conn:
            r = await conn.fetchrow(
                "SELECT niches, harvest_meta FROM house_layouts WHERE id = $1::uuid", hid)
        m = r["harvest_meta"]
        return list(r["niches"]), (json.loads(m) if isinstance(m, str) else m)

    out = await hl.ingest(b"x", niches=["Golf Resort"], approve=True)
    hid = out["house_layout_id"]
    try:
        assert await _meta(hid) == (["golf resort", "golf", "hospitality"],
                                    {hl.LABELS_KEY: ["golf", "hospitality"]})
        await hl.ingest(b"x", niches=["golf"], approve=True)
        niches, meta = await _meta(hid)
        assert niches == ["golf resort", "golf", "hospitality"]
        assert meta[hl.LABELS_KEY] == ["hospitality"], "typed golf is real from now on"
        await hl.retag(hid, niches=["hospitality"])
        assert await _meta(hid) == (["hospitality"], {})
    finally:
        await _drop(hid)
