"""The picker fixes that must ship BEFORE the nightly harvest adds volume.

  R0.1  the brand's exclusions run IN SQL before the LIMIT, the pool is 400, and
        a family the brand has just drawn is not handed straight back;
  R0.2  a pick walks the best five catalogue rows instead of trusting house[0];
  R0.3  a second write of the same shape UNIONS its niches and reports the
        STORED status, not the requested one.

All without a database: queries are routed to stand-ins by their SQL.
"""

import asyncio
import contextlib
import json

import pytest

from james_os import design_templates as dt
from james_os import house_layouts as hl

pytestmark = pytest.mark.nodb


def _el(role, x, y, w, h, size="md"):
    return {"role": role, "box": {"x": x, "y": y, "w": w, "h": h}, "size": size,
            "align": "left", "text": "x"}


def _spec(roles=("cta", "headline", "subhead"), treatment="full_bleed_photo"):
    return {"status": "ok", "kind": "graphic_card", "background": {"treatment": treatment},
            "elements": [_el(r, .05, .6, .9, .2, "xl") for r in roles], "decorations": []}


# A solid card with one line in its top tenth: a card with a hole in it.
HOLE = {"status": "ok", "kind": "graphic_card", "background": {"treatment": "solid"},
        "elements": [_el("headline", .05, .05, .9, .1, "lg")], "decorations": []}


def _house_row(i, *, tags=("golf",), spec=None, fam="", ltype="offer_card"):
    return {"id": f"h{i}", "kind": "graphic_card", "spec": json.dumps(spec or _spec()),
            "fingerprint": f"hf{i}", "family_key": fam, "source_kind": "curated",
            "source_url": "", "source_image_uri": "", "niches": list(tags),
            "layout_type": ltype, "score": 0, "adopted_count": 0}


def _wire(monkeypatch, own_rows, house_rows, *, adopt=None, mine=()):
    """Route each query by its SQL. `adopt(house_id)` answers the adoption
    insert (default: every insert lands)."""
    seen = {"adopted": [], "pool_sql": [], "pool_args": [], "tenants": []}

    class _C:
        async def fetch(self, sql, *a):
            if "FROM competitors" in sql:
                return [{"niche": "Golf Resort"}]
            if "house_layout_id::text" in sql:
                return list(mine)
            if "FROM house_layouts" in sql:
                seen["pool_sql"].append(sql)
                seen["pool_args"].append(a)
                return house_rows
            return own_rows

        async def fetchval(self, sql, *a):
            if "INSERT INTO design_templates" in sql:
                seen["adopted"].append(a[-1])
                return (adopt or (lambda h: f"own-{h}"))(a[-1])
            return None

        async def execute(self, sql, *a):
            return None

    @contextlib.asynccontextmanager
    async def _acq(tenant_id=None):
        seen["tenants"].append(tenant_id)
        yield _C()

    monkeypatch.setattr(dt, "acquire", _acq)
    monkeypatch.setattr(hl, "acquire", _acq)
    return seen


# ── R0.1 ──────────────────────────────────────────────────────────────────


def test_the_exclusion_is_in_sql_before_a_400_row_limit(monkeypatch):
    """With the exclusion done in Python AFTER a LIMIT of 200, a brand that had
    taken the first 200 approved rows was offered nothing — and every row
    approved after the 200th (all of the harvest) was invisible to everyone."""
    seen = _wire(monkeypatch, [], [_house_row(1)],
                 mine=[{"h": "h9", "fingerprint": "fp-own"}])

    async def go():
        return await hl.candidates(_ConnMine([{"h": "h9", "fingerprint": "fp-own"}]),
                                   "t", niches=["Golf Resort", "golf"])
    asyncio.run(go())
    sql, args = seen["pool_sql"][-1], seen["pool_args"][-1]
    where, order = sql.split("ORDER BY")
    assert "NOT (id::text = ANY($1::text[]))" in where
    assert "fingerprint = ANY($2::text[])" in where and "fingerprint <> ''" in where
    assert "LIMIT $4" in order, "the exclusion must come before the LIMIT"
    assert args[0] == ["h9"] and args[1] == ["fp-own"]
    assert args[3] == hl.CANDIDATE_POOL == 400
    # the coarse pre-sort: exact tag overlap with the brand's niches, lowercased
    assert "(niches && $3::text[]) DESC" in order
    # ...then the brand's vocabulary labels, so rows tagged with a label (ingest
    # and the niche backfill write them) survive the LIMIT too
    assert args[2] == ["golf resort", "golf", "hospitality"]
    assert "family_key" in sql.split("FROM house_layouts")[0]
    assert order.index("niches &&") < order.index("approvals - rejections") \
        < order.index("adopted_count") < order.index("created_at DESC")


class _ConnMine:
    """The brand's own connection: only answers the already-taken read."""

    def __init__(self, rows):
        self.rows = rows

    async def fetch(self, sql, *a):
        assert "FROM design_templates" in sql
        return self.rows


def test_a_family_the_brand_just_drew_sorts_behind_an_equal_fit(monkeypatch):
    rows = [_house_row(1, fam="famX"), _house_row(2, fam="famY")]
    _wire(monkeypatch, [], rows)
    got = asyncio.run(hl.candidates(_ConnMine([]), "t", niches=["golf"],
                                    recent_families=["famX"]))
    assert [r["id"] for r in got] == ["h2", "h1"]
    # ...but never ahead of a BETTER niche fit: freshness ranks after niche
    rows = [_house_row(1, fam="famX"), _house_row(2, fam="famY", tags=("knitting",))]
    _wire(monkeypatch, [], rows)
    got = asyncio.run(hl.candidates(_ConnMine([]), "t", niches=["golf"],
                                    recent_families=["famX"]))
    assert got[0]["id"] == "h1"


def test_existing_callers_need_not_pass_recent_families(monkeypatch):
    _wire(monkeypatch, [], [_house_row(1), _house_row(2)])
    got = asyncio.run(hl.candidates(_ConnMine([]), "t", niches=["golf"]))
    assert [r["id"] for r in got] == ["h1", "h2"]


def test_one_family_never_appears_twice_running_while_another_is_left():
    rows = [{"id": "a", "family_key": "X"}, {"id": "b", "family_key": "X"},
            {"id": "c", "family_key": "Y"}, {"id": "d", "family_key": "X"}]
    assert [r["id"] for r in hl.interleave_families(rows)] == ["a", "c", "b", "d"]
    # no alternative left: the tail may repeat rather than drop a row
    assert len(hl.interleave_families(rows)) == 4
    # rows with no family never conflict, and the order is otherwise kept
    plain = [{"id": "a"}, {"id": "b", "family_key": ""}, {"id": "c"}]
    assert [r["id"] for r in hl.interleave_families(plain)] == ["a", "b", "c"]


def test_the_picker_passes_the_families_it_used_most_recently(monkeypatch):
    """The brand's own most-recently-used layout is of family F. Two equally
    fitting catalogue rows: one of family F, one not. The other is drawn."""
    own_spec = _spec(("cta", "headline", "subhead"))
    fam = hl.family_key(own_spec)
    own = [{"id": "o1", "spec": json.dumps(own_spec), "kind": "graphic_card",
            "source_handle": "", "source_url": "", "source_platform": "instagram",
            "source_kind": "competitor", "times_used": 0,
            "last_used_at": "2026-10-06T10:00:00"}]
    house = [_house_row(1, fam=fam), _house_row(2, fam="something-else")]
    seen = _wire(monkeypatch, own, house)
    got = asyncio.run(dt.pick("t"))
    assert got is not None
    assert seen["adopted"] == ["h2"]


# ── R0.2 ──────────────────────────────────────────────────────────────────


def test_an_undrawable_row_at_the_top_no_longer_switches_the_catalogue_off(monkeypatch):
    seen = _wire(monkeypatch, [], [_house_row(1, spec=HOLE), _house_row(2), _house_row(3)])
    got = asyncio.run(dt.pick("t"))
    assert seen["adopted"] == ["h2"], seen["adopted"]
    assert got["id"] == "own-h2" and got["source_platform"] == "house"


def test_a_row_the_brand_cannot_own_gives_way_to_the_next(monkeypatch):
    seen = _wire(monkeypatch, [], [_house_row(1), _house_row(2), _house_row(3)],
                 adopt=lambda h: None if h == "h1" else f"own-{h}")
    got = asyncio.run(dt.pick("t"))
    assert seen["adopted"] == ["h1", "h2"]
    assert got["id"] == "own-h2"


def test_only_the_best_five_are_tried(monkeypatch):
    """Six undrawable rows then a good one: the walk stops at five and the turn
    goes to the brand's own library — never an unbounded scan of the pool."""
    own = [{"id": f"o{i}", "spec": json.dumps(_spec()), "kind": "graphic_card",
            "source_handle": "", "source_url": "", "source_platform": "instagram",
            "source_kind": "competitor", "times_used": 0} for i in range(3)]
    house = [_house_row(i, spec=HOLE) for i in range(6)] + [_house_row(9)]
    seen = _wire(monkeypatch, own, house)
    got = asyncio.run(dt.pick("t"))
    assert seen["adopted"] == []
    assert got["source_platform"] != "house"
    assert dt.HOUSE_TRIES == 5


def test_a_day_one_brand_whose_catalogue_rows_all_fail_gets_the_nine_formats(monkeypatch):
    """No library of its own to fall back on: None, never an IndexError."""
    _wire(monkeypatch, [], [_house_row(i, spec=HOLE) for i in range(4)])
    assert asyncio.run(dt.pick("t")) is None


# ── R0.3 ──────────────────────────────────────────────────────────────────


class _IngestConn:
    def __init__(self, stored_status, fresh=False):
        self.calls = []
        self.stored_status = stored_status
        self.fresh = fresh

    async def fetchrow(self, sql, *a):
        self.calls.append((sql, a))
        return {"id": "11111111-1111-1111-1111-111111111111", "fresh": self.fresh,
                "status": self.stored_status}


def _wire_ingest(monkeypatch, conn):
    @contextlib.asynccontextmanager
    async def _acq(tenant_id=None):
        yield conn
    monkeypatch.setattr(hl, "acquire", _acq)

    async def fake(_image):
        return _spec(("kicker", "headline", "subhead"), treatment="solid") | {
            "elements": [_el("kicker", .1, .1, .4, .06), _el("headline", .1, .2, .8, .2),
                         _el("subhead", .1, .45, .7, .1)]}
    import james_os.design_cloner as dc
    monkeypatch.setattr(dc, "extract_template_spec", fake)


def test_a_second_write_unions_niches_instead_of_replacing_them(monkeypatch):
    conn = _IngestConn("approved")
    _wire_ingest(monkeypatch, conn)
    asyncio.run(hl.ingest(b"x", niches=["  Golf ", "golf", "Real Estate"]))
    sql, args = conn.calls[-1]
    conflict = sql.split("DO UPDATE SET")[1]
    assert "unnest(house_layouts.niches || EXCLUDED.niches)" in conflict
    assert "lower(btrim(" in conflict and f"LIMIT {hl.MAX_NICHES}" in conflict
    assert "THEN EXCLUDED.niches" not in conflict, "replacing niches is the old bug"
    assert "status" not in conflict.split("RETURNING")[0], "status is never touched on conflict"
    assert "RETURNING" in sql and "status" in sql.split("RETURNING")[1]
    tags = next(a for a in args if isinstance(a, list))
    assert tags == ["golf", "real estate"]


def test_the_reported_status_is_the_stored_one(monkeypatch):
    """Re-uploading an approved shape with approve=False leaves it approved;
    reporting the requested 'candidate' would be false."""
    _wire_ingest(monkeypatch, _IngestConn("approved"))
    out = asyncio.run(hl.ingest(b"x", approve=False))
    assert out["status"] == "approved" and out["duplicate"] is True
    _wire_ingest(monkeypatch, _IngestConn("candidate"))
    out = asyncio.run(hl.ingest(b"x", approve=True))
    assert out["status"] == "candidate"


def test_clean_niches():
    assert hl.clean_niches([" A ", None, "", "a", "B" * 80]) == ["a", "b" * 60]
    assert len(hl.clean_niches([f"n{i}" for i in range(30)])) == hl.MAX_NICHES


def test_recent_families_come_from_the_brands_latest_draws_not_the_lru_window(monkeypatch):
    """A lane bigger than PICK_WINDOW: the window keeps the 200 LEAST-recently-
    used rows, so the layouts the brand drew most recently (family F) are the
    very ones it leaves out. Freshness must still see F, so of two equally
    fitting catalogue rows the one NOT of family F is drawn."""
    old_spec = _spec(("headline",))
    f_spec = _spec(("headline", "cta"))
    fam_f = hl.family_key(f_spec)
    assert fam_f != hl.family_key(old_spec)

    def _own(i, spec, when):
        return {"id": f"o{i}", "spec": json.dumps(spec), "kind": "graphic_card",
                "source_handle": "", "source_url": "", "source_platform": "instagram",
                "source_kind": "competitor", "times_used": 0, "house_layout_id": None,
                "last_used_at": when}

    # what the LRU window returns: 200 rows, all of an old family
    window = [_own(i, old_spec, f"2026-09-{1 + i % 20:02d}T00:00:00") for i in range(200)]
    # what the brand actually drew last: six rows of family F, outside the window
    latest = [_own(900 + i, f_spec, "2026-10-06T10:00:00") for i in range(6)]
    house = [_house_row(1, fam=fam_f), _house_row(2, fam="something-else")]
    seen = {"adopted": [], "recent_sql": []}

    class _C:
        def __init__(self, tenant):
            self.tenant = tenant

        async def fetch(self, sql, *a):
            if "FROM competitors" in sql:
                return [{"niche": "Golf Resort"}]
            if "house_layout_id::text" in sql:
                return []
            if "FROM house_layouts" in sql:
                return house
            if "ORDER BY last_used_at DESC" in sql:
                seen["recent_sql"].append((sql, a, self.tenant))
                return latest
            return window

        async def fetchval(self, sql, *a):
            if "INSERT INTO design_templates" in sql:
                seen["adopted"].append(a[-1])
                return f"own-{a[-1]}"
            return None

        async def execute(self, sql, *a):
            return None

    @contextlib.asynccontextmanager
    async def _acq(tenant_id=None):
        yield _C(tenant_id)

    monkeypatch.setattr(dt, "acquire", _acq)
    monkeypatch.setattr(hl, "acquire", _acq)
    got = asyncio.run(dt.pick("t"))
    assert got is not None
    assert seen["recent_sql"], "recent families must be read by their own query"
    sql, args, tenant = seen["recent_sql"][0]
    assert tenant == "t", "recency is the brand's own, read on its connection"
    assert "FROM design_templates" in sql and "status = 'active'" in sql
    assert args[0] >= hl.RECENT_FAMILIES
    assert seen["adopted"] == ["h2"], seen["adopted"]


def test_recent_families_skip_undrawable_rows_and_never_cost_a_pick():
    good = _spec(("headline", "cta"))

    class _Rows:
        async def fetch(self, sql, *a):
            return [{"spec": json.dumps(HOLE)}, {"spec": "not json"}, {"spec": good}]

    class _Broken:
        async def fetch(self, sql, *a):
            raise RuntimeError("db down")

    assert asyncio.run(dt._recent_families(_Rows(), hl)) == [hl.family_key(good)]
    assert asyncio.run(dt._recent_families(_Broken(), hl)) == []


# ── R0.1: the pre-sort matches the harvest writer's spelling ─────────────────


TROUV = "tour packages, travel and holidays"


def test_brand_tags_carry_every_stored_spelling_of_a_comma_niche():
    """The harvest stores a comma niche as its pieces; curated rows keep it whole.
    The pre-sort has to overlap with both."""
    from james_os import house_harvest as hh

    tags = hl.brand_tag_keys([TROUV.title(), "  Golf   Resort "])
    stored_by_harvest = hh.parse_niches("tour packages,travel and holidays")
    assert stored_by_harvest == ["tour packages", "travel and holidays"]
    assert set(stored_by_harvest) <= set(tags)
    assert TROUV in tags                                  # curated/promoted (clean_niches)
    assert hh.canon_niche(TROUV) in tags                  # comma read as a space
    assert "golf resort" in tags                          # whitespace collapsed
    assert hl.brand_tag_keys("golf") == ["golf"]
    assert hl.brand_tag_keys([None, "", " , "]) == []


class _SqlPool:
    """Evaluates the candidates() pool query in Python: the exclusion, then
    ORDER BY (niches && $3) DESC, score DESC, adopted DESC, created_at DESC, LIMIT $4."""

    def __init__(self, rows):
        self.rows = rows
        self.args = None

    async def fetch(self, sql, *a):
        assert "FROM house_layouts" in sql
        self.args = a
        taken, held, tags, limit = a
        live = [r for r in self.rows
                if r["id"] not in taken and not (r["fingerprint"] and r["fingerprint"] in held)]
        live.sort(key=lambda r: (bool(set(r["niches"]) & set(tags)), r["score"],
                                 r["adopted_count"], r["created_at"]), reverse=True)
        return live[:limit]


def test_a_comma_niche_brand_keeps_its_older_harvested_rows_past_the_limit(monkeypatch):
    """A travel brand's niche is 'tour packages, travel and holidays'; the harvest
    stores its rows as ['tour packages', 'travel and holidays']. With the whole
    string as the only pre-sort tag, newer off-niche rows filled the LIMIT and the
    older travel rows were never seen by fit_rank."""
    from james_os import house_harvest as hh

    travel = hh.parse_niches("tour packages,travel and holidays")
    rows = []
    for i in range(3):                                   # the brand's own niche, oldest
        r = _house_row(100 + i, tags=travel)
        r["created_at"] = i
        rows.append(r)
    for i in range(20):                                  # newer, off-niche
        r = _house_row(i, tags=("golf",))
        r["created_at"] = 1000 + i
        rows.append(r)
    pool = _SqlPool(rows)

    @contextlib.asynccontextmanager
    async def _acq(tenant_id=None):
        assert tenant_id is None
        yield pool

    monkeypatch.setattr(hl, "acquire", _acq)
    monkeypatch.setattr(hl, "CANDIDATE_POOL", 5)
    got = asyncio.run(hl.candidates(_ConnMine([]), "t", niches=[TROUV], limit=5))
    assert set(travel) & set(pool.args[2]), "the pre-sort tags must overlap the stored tags"
    ids = [r["id"] for r in got]
    assert {"h100", "h101", "h102"} <= set(ids), ids
    assert set(ids[:3]) == {"h100", "h101", "h102"}, "on-niche rows rank first in fit_rank"
