"""Learning from results, per niche — and tagging what nobody tagged.

Measured 2026-10-08: no owner has ever sent a verdict on a learned post, and 20
of 1845 BM2 artifacts carry metrics, none of them learned. So the mechanism has
to be right at ZERO data (today's order, untouched) and at TINY data (smoothed,
not swung by one result) long before it is right at volume. These pin that,
the verdict hook, the /outcome contract with BM2, the upload's niche read and
the backfill. No database: queries are routed to stand-ins by their SQL (the
real upserts are in test_house_outcomes_db.py).
"""

import asyncio
import contextlib
import json
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from james_os import api_v1
from james_os import design_templates as dt
from james_os import house_layouts as hl
from james_os import niche_vocab as nv
from james_os.config import settings

pytestmark = pytest.mark.nodb

TENANT = UUID("0000000b-0000-0000-0000-00000000beef")
NOW = datetime.now(UTC)


def _spec():
    return {"status": "ok", "kind": "graphic_card",
            "background": {"treatment": "full_bleed_photo"},
            "elements": [{"role": "headline", "box": {"x": .05, "y": .6, "w": .9, "h": .2},
                          "size": "xl", "align": "left", "text": "x"}],
            "decorations": []}


def _row(i, *, tags=(), fam="", days=30, ltype="offer_card"):
    return {"id": f"00000000-0000-0000-0000-{i:012d}", "kind": "graphic_card",
            "spec": json.dumps(_spec()), "fingerprint": f"fp{i}", "family_key": fam,
            "source_kind": "curated", "source_url": "", "source_image_uri": f"https://img/{i}",
            "niches": list(tags), "layout_type": ltype, "score": 0, "adopted_count": 0,
            "created_at": NOW - timedelta(days=days), "label": "", "title": "",
            "promoted_from": None, "status": "approved"}


def _out(i, niche, *, a=0, r=0, m=0, lift=0.0):
    return {"h": f"00000000-0000-0000-0000-{i:012d}", "niche": niche, "approvals": a,
            "rejections": r, "measured": m, "lift_sum": lift}


class _Pool:
    """The catalogue connection: answers the candidate query and the ONE outcomes
    query (inside a savepoint), and counts the outcomes reads."""

    def __init__(self, rows, outcomes):
        self.rows, self.outcomes, self.outcome_reads = rows, outcomes, 0

    def transaction(self):
        @contextlib.asynccontextmanager
        async def _sp():
            yield
        return _sp()

    async def fetch(self, sql, *a):
        if "FROM house_layout_outcomes" in sql:
            self.outcome_reads += 1
            return list(self.outcomes)
        if "FROM competitors" in sql:
            return []
        if "FROM design_templates" in sql:
            return []
        assert "FROM house_layouts" in sql
        return list(self.rows)


def _wire_pool(monkeypatch, rows, outcomes=()):
    pool = _Pool(rows, outcomes)

    @contextlib.asynccontextmanager
    async def _acq(tenant_id=None):
        yield pool

    monkeypatch.setattr(hl, "acquire", _acq)
    return pool


class _Mine:
    async def fetch(self, sql, *a):
        return []


def _ids(rows):
    return [r["id"][-1] for r in rows]


# ── the score ───────────────────────────────────────────────────────────────


def test_no_data_is_exactly_neutral():
    assert hl.outcome_score([], ["golf"]) == hl.OUTCOME_NEUTRAL == 0.5
    assert hl.outcome_score([], []) == 0.5


def test_the_score_is_smoothed_and_per_niche():
    five = [{"niche": "golf", "approvals": 5}]
    assert hl.outcome_score(five, ["golf", "hospitality"]) == 0.86
    assert hl.outcome_score(five, ["real estate"]) == 0.5, "golf's verdicts are not real estate's"
    assert hl.outcome_score([{"niche": "golf", "rejections": 1}], ["golf"]) == 0.33
    assert hl.outcome_score([{"niche": "golf", "measured": 1, "lift_sum": 2.0}], ["golf"]) == 0.67
    # a brand with no label reads the '' bucket, and only that
    assert hl.outcome_score([{"niche": "", "approvals": 3}], []) == 0.8
    assert hl.outcome_score([{"niche": "", "approvals": 3}], ["golf"]) == 0.5


def _filed(brand_niche, *, a=0, r=0, m=0, lift=0.0, i=2):
    """Rows exactly as record_verdict/record_outcome file them: one per label."""
    return [_out(i, k, a=a, r=r, m=m, lift=lift) for k in hl.outcome_keys([brand_niche])]


@pytest.mark.parametrize("niche,n_labels", [
    ("golf", 1), ("commercial real estate", 2), ("golf resort", 2), ("parenting humor", 3)])
def test_one_result_counts_once_however_many_labels_the_brand_has(niche, n_labels):
    # Summing the reader's labels counted one rejection once per label: 0.33 for
    # 'golf', 0.25 for a 2-label brand, 0.20 for a 3-label one.
    assert len(hl.outcome_keys([niche])) == n_labels
    assert hl.outcome_score(_filed(niche, r=1), hl.outcome_keys([niche])) == 0.33
    assert hl.outcome_score(_filed(niche, m=1, lift=5.0), hl.outcome_keys([niche])) == 1.17
    five = hl.outcome_score(_filed(niche, a=5), hl.outcome_keys([niche]))
    assert five == 0.86, "the docstring's five-approvals figure, for every brand"


def test_a_brand_reads_its_best_evidenced_label_not_a_sum():
    # golf resort brand (golf + hospitality) after a golf-course brand filed 4
    # approvals under golf alone and it filed one rejection under both of its own
    rows = _filed("golf", a=4) + [_out(2, "hospitality", r=1)]
    rows[0]["rejections"] = 1
    assert hl.outcome_evidence(rows, ["golf", "hospitality"]) == (4, 1, 0, 0.0)
    assert hl.outcome_score(rows, ["golf", "hospitality"]) == round(5 / 7, 2)
    # equal evidence: the answer does not depend on the order rows came back in
    tie = [_out(2, "hospitality", a=2), _out(2, "golf", r=2)]
    assert hl.outcome_score(tie, ["golf", "hospitality"]) == \
        hl.outcome_score(list(reversed(tie)), ["golf", "hospitality"]) == 0.25


def test_the_sort_term_ignores_too_little_evidence_and_moves_in_steps():
    keys = ["golf"]
    assert hl.outcome_term([], keys) == hl.OUTCOME_NEUTRAL
    for few in ([_out(2, "golf", r=1)], [_out(2, "golf", a=1)],
                [_out(2, "golf", a=1, m=1, lift=5.0)]):
        assert hl.outcome_term(few, keys) == hl.OUTCOME_NEUTRAL, few
    assert hl.outcome_term([_out(2, "golf", a=5)], keys) == 0.9
    assert hl.outcome_term([_out(2, "golf", a=2, r=1)], keys) == 0.6
    # within a step of neutral is neutral: three ordinary posts move nothing
    assert hl.outcome_term([_out(2, "golf", m=3, lift=3.3)], keys) == 0.5


def test_fit_rank_without_an_outcome_keeps_its_behaviour():
    a = hl.fit_rank("golf", {}, ["golf"], "offer_card", NOW)
    assert a == hl.fit_rank("golf", {}, ["golf"], "offer_card", NOW, outcome=0.5)
    # the new term sits right after freshness: it never jumps niche or new
    assert hl.fit_rank("golf", {}, [], "offer_card", NOW, outcome=5.0) < \
        hl.fit_rank("golf", {}, ["golf"], "offer_card", NOW, outcome=0.1)
    old = NOW - timedelta(days=60)
    assert hl.fit_rank("golf", {}, ["golf"], "offer_card", old, outcome=5.0) < \
        hl.fit_rank("golf", {}, ["golf"], "offer_card", NOW, outcome=0.1)


# ── the picker ──────────────────────────────────────────────────────────────


def _mixed():
    return [_row(1, tags=["politics"]), _row(2), _row(3, tags=["golf"], days=60),
            _row(4, tags=["golf"], days=2), _row(5, fam="A"), _row(6, fam="A"),
            _row(7, tags=["golf resort"], days=60, ltype="stat_card")]


def test_zero_data_leaves_todays_order_unchanged(monkeypatch):
    rows = _mixed()
    _wire_pool(monkeypatch, rows, outcomes=[])
    got = asyncio.run(hl.candidates(_Mine(), "t", niches=["golf resort"], limit=10,
                                    profile={"offer_card": 2}))
    # the base order: niche (7 shares golf+hospitality and two words), new (4 is
    # two days old), then SQL order; one family never twice running (5, 1, 6)
    assert _ids(got) == ["7", "4", "3", "2", "5", "1", "6"]


def test_an_unreadable_outcomes_table_also_leaves_the_order(monkeypatch):
    rows = _mixed()
    pool = _wire_pool(monkeypatch, rows)

    async def _boom(sql, *a):
        if "house_layout_outcomes" in sql:
            raise RuntimeError('relation "house_layout_outcomes" does not exist')
        return list(rows)

    pool.fetch = _boom
    got = asyncio.run(hl.candidates(_Mine(), "t", niches=["golf resort"], limit=10,
                                    profile={"offer_card": 2}))
    assert _ids(got) == ["7", "4", "3", "2", "5", "1", "6"]


def test_results_are_read_in_one_query_for_every_candidate(monkeypatch):
    pool = _wire_pool(monkeypatch, _mixed())
    asyncio.run(hl.candidates(_Mine(), "t", niches=["golf"], limit=10))
    assert pool.outcome_reads == 1


def test_five_golf_approvals_lift_a_layout_for_a_golf_brand_only(monkeypatch):
    rows = [_row(1), _row(2)]            # equal: both untagged, same age
    ups = [_out(2, "golf", a=5), _out(2, "hospitality", a=5)]
    _wire_pool(monkeypatch, rows, ups)
    golf = asyncio.run(hl.candidates(_Mine(), "t", niches=["golf resort"], limit=5))
    assert _ids(golf) == ["2", "1"]
    estate = asyncio.run(hl.candidates(_Mine(), "t", niches=["commercial real estate"],
                                       limit=5))
    assert _ids(estate) == ["1", "2"], "what golf brands liked says nothing about real estate"


def test_one_rejection_barely_moves_a_layout(monkeypatch):
    # an on-niche layout with one rejection still beats an untagged one...
    rows = [_row(1), _row(2, tags=["golf"])]
    _wire_pool(monkeypatch, rows, [_out(2, "golf", r=1)])
    assert _ids(asyncio.run(hl.candidates(_Mine(), "t", niches=["golf"], limit=5)))[0] == "2"
    # ...and a well-approved one still beats an unrated equal after it
    rows = [_row(1), _row(2)]
    _wire_pool(monkeypatch, rows, [_out(2, "golf", a=5, r=1)])
    assert _ids(asyncio.run(hl.candidates(_Mine(), "t", niches=["golf"], limit=5))) == ["2", "1"]
    assert hl.outcome_score([{"niche": "golf", "approvals": 5, "rejections": 1}], ["golf"]) \
        == 0.75


@pytest.mark.parametrize("one", [dict(r=1), dict(a=1), dict(m=1, lift=5.0)])
def test_a_single_result_does_not_reorder_equal_rows(monkeypatch, one):
    # Smoothing alone was not enough: the sort is a strict tuple, so 0.33 vs
    # 0.5 still put a once-rejected row below EVERY unrated equal, and a single
    # approval (0.67) above all of them, overriding family rotation and type fit.
    rows = [_row(1, tags=["golf"]), _row(2, tags=["golf"]), _row(3, tags=["golf"])]
    _wire_pool(monkeypatch, rows, outcomes=[])
    base = _ids(asyncio.run(hl.candidates(_Mine(), "t", niches=["golf resort"], limit=5)))
    for i in (1, 3):
        _wire_pool(monkeypatch, rows, _filed("golf resort", i=i, **one))
        got = asyncio.run(hl.candidates(_Mine(), "t", niches=["golf resort"], limit=5))
        assert _ids(got) == base, (i, one)


def test_the_brands_view_ranks_results_after_freshness(monkeypatch):
    rows = [_row(1, tags=["golf"], days=1), _row(2, tags=["golf"], days=40),
            _row(3, tags=["golf"], days=50)]
    pool = _wire_pool(monkeypatch, rows, [_out(3, "golf", a=5)])

    async def _fetch(sql, *a):
        if "FROM competitors" in sql:
            return [{"niche": "golf resort"}]
        return await _Pool.fetch(pool, sql, *a)

    pool.fetch = _fetch
    out = asyncio.run(hl.for_brand(TENANT, limit=8))
    # fresh first whatever its results; then the rated row ahead of a newer unrated one
    assert [d["id"][-1] for d in out["layouts"]] == ["1", "3", "2"]


# ── the verdict hook ────────────────────────────────────────────────────────


def _wire_verdict(monkeypatch, row, *, record=None):
    seen = {"calls": []}

    class _C:
        async def fetchrow(self, sql, *a):
            seen["sql"] = sql
            return row

    @contextlib.asynccontextmanager
    async def _acq(tenant_id=None):
        yield _C()

    async def _niches(conn, tenant_id):
        return ["golf resort"]

    async def _record(hid, niches, approved):
        seen["calls"].append((hid, list(niches), approved))
        if record:
            raise record

    monkeypatch.setattr(dt, "acquire", _acq)
    monkeypatch.setattr(hl, "tenant_niches", _niches)
    monkeypatch.setattr(hl, "record_verdict", _record)
    return seen


def test_a_verdict_on_a_forked_layout_is_filed_on_the_catalogue_row(monkeypatch):
    seen = _wire_verdict(monkeypatch, {"approvals": 3, "rejections": 0,
                                       "house_layout_id": "hl-1"})
    assert asyncio.run(dt.mark_verdict(TENANT, "t1", True)) == {"approvals": 3, "rejections": 0}
    assert "house_layout_id" in seen["sql"].split("RETURNING")[1]
    assert seen["calls"] == [("hl-1", ["golf resort"], True)]


def test_a_layout_the_brand_learned_itself_files_nothing(monkeypatch):
    seen = _wire_verdict(monkeypatch, {"approvals": 1, "rejections": 0,
                                       "house_layout_id": None})
    asyncio.run(dt.mark_verdict(TENANT, "t1", False))
    assert seen["calls"] == []


def test_a_failure_to_file_never_fails_the_verdict(monkeypatch):
    seen = _wire_verdict(monkeypatch, {"approvals": 0, "rejections": 2,
                                       "house_layout_id": "hl-1"},
                         record=RuntimeError("house_layout_outcomes does not exist"))
    assert asyncio.run(dt.mark_verdict(TENANT, "t1", False)) == {"approvals": 0, "rejections": 2}
    assert seen["calls"] == [("hl-1", ["golf resort"], False)]


# ── /v1/design-templates/{id}/outcome ───────────────────────────────────────


def _v1_app() -> FastAPI:
    app = FastAPI()
    app.include_router(api_v1.router)
    app.dependency_overrides[api_v1.require_service] = lambda: TENANT
    return app


async def _call(app, method, url, **kw):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        return await c.request(method, url, **kw)


def _wire_outcome(monkeypatch, row, niches=("golf resort",), *, gone=False):
    seen = {"recorded": [], "tenants": []}

    class _C:
        async def fetchrow(self, sql, *a):
            assert "FROM design_templates" in sql
            return row

        async def fetch(self, sql, *a):
            assert "FROM competitors" in sql
            return [{"niche": n} for n in niches]

        async def execute(self, sql, *a):
            assert "INSERT INTO house_layout_outcomes" in sql
            assert "WHERE EXISTS (SELECT 1 FROM house_layouts" in sql
            seen["recorded"].append(a)
            # what Postgres answers: the rows the conditional INSERT wrote
            return f"INSERT 0 {0 if gone else len(a[1])}"

    @contextlib.asynccontextmanager
    async def _acq(tenant_id=None):
        seen["tenants"].append(tenant_id)
        yield _C()

    monkeypatch.setattr(hl, "acquire", _acq)
    return seen


TID = "22222222-2222-2222-2222-222222222222"
WHEN = "2026-10-08T12:00:00Z"


async def test_outcome_records_lift_per_canonical_niche(monkeypatch):
    seen = _wire_outcome(monkeypatch, {"h": "hl-9"})
    r = await _call(_v1_app(), "POST", f"/v1/design-templates/{TID}/outcome",
                    json={"engagement": 30, "baseline": 10, "measured_at": WHEN})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True and body["house_layout_id"] == "hl-9"
    assert body["niches"] == ["golf", "hospitality"]
    hid, keys, a, rej, m, lift = seen["recorded"][0]
    assert (hid, keys, a, rej, m, lift) == ("hl-9", ["golf", "hospitality"], 0, 0, 1, 3.0)
    assert seen["tenants"][0] == TENANT, "the template is looked up on the brand's connection"


async def test_outcome_on_a_discarded_catalogue_row_is_ok_and_records_nothing(monkeypatch):
    # The fork's house_layout_id has no FK; 'discard' hard-deletes the catalogue
    # row. That used to be a ForeignKeyViolation -> 500 on every retry by BM2.
    _wire_outcome(monkeypatch, {"h": "hl-gone"}, gone=True)
    r = await _call(_v1_app(), "POST", f"/v1/design-templates/{TID}/outcome",
                    json={"engagement": 3, "baseline": 1, "measured_at": WHEN})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True and body["recorded"] is False
    assert body["house_layout_id"] is None and body["niches"] == []
    assert body["reason"] == "catalogue row discarded"


async def test_a_racing_discard_is_the_same_answer_not_a_500(monkeypatch):
    import asyncpg

    seen = _wire_outcome(monkeypatch, {"h": "hl-gone"})

    @contextlib.asynccontextmanager
    async def _acq(tenant_id=None):
        class _C:
            async def fetchrow(self, sql, *a):
                return {"h": "hl-gone"}

            async def fetch(self, sql, *a):
                return [{"niche": "golf"}]

            async def execute(self, sql, *a):
                raise asyncpg.ForeignKeyViolationError("house_layout_id not present")
        seen["tenants"].append(tenant_id)
        yield _C()

    monkeypatch.setattr(hl, "acquire", _acq)
    out = await hl.template_outcome(TENANT, TID, 3, 1)
    assert out["ok"] is True and out["recorded"] is False


@pytest.mark.parametrize("eng,base,lift", [
    (100, 10, 5.0),       # clamped
    (0, 10, 0.0),
    (7, 0, 1.0),          # no baseline: counted, no information
    (7, None, 1.0),
])
async def test_outcome_lift_math_and_clamp(monkeypatch, eng, base, lift):
    seen = _wire_outcome(monkeypatch, {"h": "hl-9"})
    body = {"engagement": eng, "measured_at": WHEN}
    if base is not None:
        body["baseline"] = base
    r = await _call(_v1_app(), "POST", f"/v1/design-templates/{TID}/outcome", json=body)
    assert r.status_code == 200, r.text
    assert seen["recorded"][0][5] == lift and r.json()["lift"] == lift


async def test_outcome_404s_for_a_layout_the_brand_does_not_hold(monkeypatch):
    seen = _wire_outcome(monkeypatch, None)
    r = await _call(_v1_app(), "POST", f"/v1/design-templates/{TID}/outcome",
                    json={"engagement": 3, "baseline": 1, "measured_at": WHEN})
    assert r.status_code == 404 and "no such layout for this brand" in r.text
    assert seen["recorded"] == []


async def test_outcome_on_a_self_learned_layout_records_nothing_and_says_so(monkeypatch):
    seen = _wire_outcome(monkeypatch, {"h": None})
    r = await _call(_v1_app(), "POST", f"/v1/design-templates/{TID}/outcome",
                    json={"engagement": 3, "baseline": 1, "measured_at": WHEN})
    assert r.status_code == 200
    assert r.json()["house_layout_id"] is None and r.json()["niches"] == []
    assert seen["recorded"] == []


async def test_a_brand_with_no_mappable_niche_files_under_blank(monkeypatch):
    seen = _wire_outcome(monkeypatch, {"h": "hl-9"}, niches=())
    r = await _call(_v1_app(), "POST", f"/v1/design-templates/{TID}/outcome",
                    json={"engagement": 3, "baseline": 1, "measured_at": WHEN})
    assert r.json()["niches"] == [""] and seen["recorded"][0][1] == [""]


@pytest.mark.parametrize("body", [
    {"engagement": -1, "baseline": 1, "measured_at": WHEN},
    {"engagement": 1, "baseline": -1, "measured_at": WHEN},
    {"engagement": 1, "baseline": 1, "measured_at": "yesterday"},
    {"engagement": 1, "baseline": 1},
])
async def test_outcome_refuses_a_malformed_body(monkeypatch, body):
    seen = _wire_outcome(monkeypatch, {"h": "hl-9"})
    r = await _call(_v1_app(), "POST", f"/v1/design-templates/{TID}/outcome", json=body)
    assert r.status_code == 422 and seen["recorded"] == []


# ── the upload reads the niche ──────────────────────────────────────────────


def _wire_ingest(monkeypatch, *, inferred=None, boom=None):
    calls = {"insert": [], "infer": []}

    class _C:
        async def fetchrow(self, sql, *a):
            calls["insert"].append(a)
            return {"id": "new-id", "fresh": True, "status": "approved"}

    @contextlib.asynccontextmanager
    async def _acq(tenant_id=None):
        yield _C()

    async def _spec_read(image, **kw):
        return _spec()

    async def _infer(src):
        calls["infer"].append(src)
        if boom:
            raise boom
        return list(inferred or [])

    from james_os import design_cloner

    monkeypatch.setattr(hl, "acquire", _acq)
    monkeypatch.setattr(design_cloner, "extract_template_spec", _spec_read)
    monkeypatch.setattr(nv, "infer_from_image", _infer)
    return calls


def _stored_tags(calls):
    return next(a for a in calls["insert"][-1] if isinstance(a, list))


def _added_labels(calls):
    meta = next(a for a in calls["insert"][-1]
                if isinstance(a, str) and a.startswith('{"niche_labels"'))
    return json.loads(meta)["niche_labels"]


def test_an_untagged_upload_is_read_for_its_niche(monkeypatch):
    calls = _wire_ingest(monkeypatch, inferred=["commercial real estate"])
    out = asyncio.run(hl.ingest(b"img", image_uri="https://store/x.png", approve=True))
    assert calls["infer"] == ["https://store/x.png"], "the stored copy, not 15 MB inline"
    assert _stored_tags(calls) == ["commercial real estate", "real estate"]
    assert _added_labels(calls) == ["commercial real estate", "real estate"], \
        "nobody typed them: the harvest's caps and stats must not count them"
    assert out["niches_inferred"] is True


def test_a_local_upload_is_read_from_its_bytes(monkeypatch):
    calls = _wire_ingest(monkeypatch, inferred=["golf"])
    asyncio.run(hl.ingest(b"img", image_uri="/media-files/house/x.png"))
    assert calls["infer"] == [b"img"]


def test_typed_tags_are_kept_and_labelled_with_no_read(monkeypatch):
    calls = _wire_ingest(monkeypatch, inferred=["politics"])
    out = asyncio.run(hl.ingest(b"img", niches=["Golf Resort"]))
    assert calls["infer"] == []
    assert _stored_tags(calls) == ["golf resort", "golf", "hospitality"]
    assert _added_labels(calls) == ["golf", "hospitality"]
    assert out["niches_inferred"] is False


@pytest.mark.parametrize("inferred,boom", [([], None), (None, RuntimeError("down"))])
def test_a_failed_or_empty_read_stores_no_tags_and_never_fails_the_upload(
        monkeypatch, inferred, boom):
    calls = _wire_ingest(monkeypatch, inferred=inferred, boom=boom)
    out = asyncio.run(hl.ingest(b"img", approve=True))
    assert out["ok"] is True and _stored_tags(calls) == []


def test_the_harvest_adds_labels_to_its_text_tags_with_no_read():
    from james_os import house_harvest as hh

    assert hh._with_labels(["golf resort", "golf"]) == (
        ["golf resort", "golf", "hospitality"], ["hospitality"]), "typed golf is not added"
    stored, added = hh._with_labels(["tour packages", "travel and holidays"])
    assert stored[:2] == ["tour packages", "travel and holidays"], "primary tag stays first"
    assert "travel" in stored and "travel" in added
    assert "tour packages" not in added


# ── the backfill ────────────────────────────────────────────────────────────


class _BackfillConn:
    def __init__(self, rows):
        self.rows, self.writes, self.marks = rows, [], []

    async def execute(self, sql, *a):
        assert sql.startswith("UPDATE house_layouts") and "niche_read" in sql
        self.marks.append((a[0], json.loads(a[1])["status"]))

    async def fetch(self, sql, *a):
        assert "status = 'approved'" in sql
        return self.rows

    async def fetchval(self, sql, *a):
        assert sql.startswith("UPDATE house_layouts")
        self.writes.append((sql, a))
        return a[0]


def _wire_backfill(monkeypatch, rows, replies):
    conn = _BackfillConn(rows)
    seen = []

    @contextlib.asynccontextmanager
    async def _acq(tenant_id=None):
        yield conn

    async def _detail(uri):
        seen.append(uri)
        return replies[uri]

    monkeypatch.setattr(hl, "acquire", _acq)
    monkeypatch.setattr(nv, "infer_detail", _detail)
    monkeypatch.setattr(nv, "has_key", lambda: True)
    monkeypatch.setattr(settings, "pause_spend", False)
    return conn, seen


def _bf_rows():
    return [
        {"id": "a", "niches": [], "source_image_uri": "https://i/a"},
        {"id": "b", "niches": [], "source_image_uri": "https://i/b"},
        {"id": "c", "niches": [], "source_image_uri": "https://i/c"},
        {"id": "d", "niches": [], "source_image_uri": ""},
        {"id": "e", "niches": ["golf resort"], "source_image_uri": "https://i/e"},
        {"id": "f", "niches": ["golf", "hospitality"], "source_image_uri": ""},
        {"id": "g", "niches": ["knitting"], "source_image_uri": ""},
    ]


REPLIES = {"https://i/a": {"status": "ok", "labels": ["golf"]},
           "https://i/b": {"status": "ok", "labels": []},
           "https://i/c": {"status": "failed", "labels": []}}


def test_dry_run_counts_and_prices_and_touches_nothing(monkeypatch):
    conn, seen = _wire_backfill(monkeypatch, _bf_rows(), REPLIES)
    out = asyncio.run(hl.infer_niches_backfill(limit=2, dry_run=True))
    assert seen == [] and conn.writes == []
    assert out == {"scanned": 7, "inferred": 2, "labelled_from_text": 1,
                   "still_untagged": 2, "failed": 0, "dry_run": True, "judged_generic": 0,
                   "est_usd": round(2 * nv.est_usd_per_image(), 4)}


def test_the_backfill_reads_the_untagged_and_labels_the_tagged(monkeypatch):
    conn, seen = _wire_backfill(monkeypatch, _bf_rows(), REPLIES)
    out = asyncio.run(hl.infer_niches_backfill(limit=10))
    assert sorted(seen) == ["https://i/a", "https://i/b", "https://i/c"], "no image, no read"
    assert out["scanned"] == 7 and out["inferred"] == 1 and out["failed"] == 1
    assert out["labelled_from_text"] == 1 and out["still_untagged"] == 3
    assert out["est_usd"] == round(3 * nv.est_usd_per_image(), 4)
    writes = {a[0]: (sql, a) for sql, a in conn.writes}
    assert writes["a"][1][1] == ["golf"] and "cardinality(niches) = 0" in writes["a"][0]
    assert json.loads(writes["a"][1][2]) == ["golf"] and "niche_labels" in writes["a"][0]
    assert writes["e"][1][1] == ["golf resort", "golf", "hospitality"]
    assert json.loads(writes["e"][1][3]) == ["golf", "hospitality"]
    assert "niches = $3" in writes["e"][0], "a curator's retag since the read wins"
    assert set(writes) == {"a", "e"}
    assert sorted(conn.marks) == [("b", "generic"), ("c", "failed")], \
        "a read that wrote no tags is noted, so the next run moves on"
    assert out["judged_generic"] == 1


def test_the_paid_half_is_refused_without_a_key_or_under_pause(monkeypatch):
    conn, seen = _wire_backfill(monkeypatch, _bf_rows(), REPLIES)
    monkeypatch.setattr(settings, "pause_spend", True)
    out = asyncio.run(hl.infer_niches_backfill(limit=10))
    assert seen == [] and "PAUSE_SPEND" in out["vision_skipped"]
    assert out["labelled_from_text"] == 1, "the free half still runs"
    monkeypatch.setattr(settings, "pause_spend", False)
    monkeypatch.setattr(nv, "has_key", lambda: False)
    out = asyncio.run(hl.infer_niches_backfill(limit=10))
    assert seen == [] and "no OpenAI key" in out["vision_skipped"]


def test_the_backfill_reads_at_most_four_at_a_time(monkeypatch):
    rows = [{"id": f"r{i}", "niches": [], "source_image_uri": f"https://i/{i}"}
            for i in range(12)]
    conn = _BackfillConn(rows)
    live = {"now": 0, "peak": 0}

    @contextlib.asynccontextmanager
    async def _acq(tenant_id=None):
        yield conn

    async def _detail(uri):
        live["now"] += 1
        live["peak"] = max(live["peak"], live["now"])
        await asyncio.sleep(0.01)
        live["now"] -= 1
        return {"status": "ok", "labels": []}

    monkeypatch.setattr(hl, "acquire", _acq)
    monkeypatch.setattr(nv, "infer_detail", _detail)
    monkeypatch.setattr(nv, "has_key", lambda: True)
    monkeypatch.setattr(settings, "pause_spend", False)
    asyncio.run(hl.infer_niches_backfill(limit=12))
    assert live["peak"] == hl.INFER_CONCURRENCY == 4


async def test_the_backfill_route_is_platform_key_only(monkeypatch):
    got = {}

    async def _bf(*, limit, dry_run):
        got.update(limit=limit, dry_run=dry_run)
        return {"scanned": 0}

    monkeypatch.setattr(hl, "infer_niches_backfill", _bf)
    app = FastAPI()
    app.include_router(api_v1.router)
    app.dependency_overrides[api_v1.require_curator] = lambda: True
    r = await _call(app, "POST", "/v1/house-layouts/infer-niches?limit=50&dry_run=true")
    assert r.status_code == 200 and got == {"limit": 50, "dry_run": True}
    r = await _call(app, "POST", "/v1/house-layouts/infer-niches?limit=0")
    assert r.status_code == 422

    monkeypatch.setattr(settings, "service_api_platform_key", "platform-secret")
    bare = FastAPI()
    bare.include_router(api_v1.router)
    r = await _call(bare, "POST", "/v1/house-layouts/infer-niches",
                    headers={"Authorization": "Bearer some-tenant-key"})
    assert r.status_code == 403


def test_a_noted_read_is_not_paid_for_again_and_never_read_rows_go_first(monkeypatch):
    """Without a note, the newest untagged rows — generic quote cards, images
    whose copy is gone — were picked first on every run, forever."""
    from datetime import UTC, datetime, timedelta

    now = datetime.now(UTC)
    rows = [
        {"id": "gen", "niches": [], "source_image_uri": "https://i/gen",
         "read_note": json.dumps({"at": now.isoformat(), "status": "generic"})},
        {"id": "hot", "niches": [], "source_image_uri": "https://i/hot",
         "read_note": json.dumps({"at": (now - timedelta(days=1)).isoformat(), "status": "failed"})},
        {"id": "old", "niches": [], "source_image_uri": "https://i/old",
         "read_note": json.dumps({"at": (now - timedelta(days=9)).isoformat(), "status": "failed"})},
        {"id": "new", "niches": [], "source_image_uri": "https://i/new", "read_note": None},
    ]
    replies = {u: {"status": "ok", "labels": ["golf"]} for u in
               ("https://i/gen", "https://i/hot", "https://i/old", "https://i/new")}
    conn, seen = _wire_backfill(monkeypatch, rows, replies)
    out = asyncio.run(hl.infer_niches_backfill(limit=1))
    assert seen == ["https://i/new"], "never-read first; generic never; a recent failure cools off"
    assert out["judged_generic"] == 1
    conn, seen = _wire_backfill(monkeypatch, [r for r in rows if r["id"] != "new"], replies)
    asyncio.run(hl.infer_niches_backfill(limit=5))
    assert seen == ["https://i/old"], "a failure past its cool-off is tried again"


def test_a_slow_niche_read_never_holds_an_upload(monkeypatch):
    async def _hang(src):
        await asyncio.sleep(5)
        return ["golf"]

    monkeypatch.setattr(nv, "infer_from_image", _hang)
    monkeypatch.setattr(hl, "INGEST_READ_TIMEOUT", 0.05)
    stored, read, added = asyncio.run(hl._ingest_niches([], b"png", "https://i/x"))
    assert (stored, read, added) == ([], False, []), "stored untagged, upload goes on"
