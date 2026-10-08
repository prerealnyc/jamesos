"""The nightly harvest's gate into the house catalogue (house_harvest.py).

What these pin, because each is easy to get silently wrong:
  * the paid vision read never runs for an image the catalogue already holds;
  * a dry run writes nothing — no row, no stored picture;
  * the per-niche advisory lock is taken BEFORE the counts it protects;
  * every cap, the plain-photo and untyped gates, and the retry mapping;
  * every connection is the catalogue's, acquire(None) — no tenant, ever;
  * the picture is stored only for a row that was really written.
No database: queries are answered by a recorder keyed on their SQL.
"""

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from james_os import house_harvest as hh

pytestmark = pytest.mark.nodb

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 64
KEY = "onc:" + "a" * 40
ID = "22222222-2222-2222-2222-222222222222"


def test_the_module_under_test_is_this_checkout():
    """An editable install elsewhere would otherwise be what is tested."""
    assert Path(hh.__file__).resolve().is_relative_to(Path(__file__).resolve().parents[1])


def _el(role, x, y, w, h):
    return {"role": role, "box": {"x": x, "y": y, "w": w, "h": h}, "size": "lg",
            "align": "left", "text": "x"}


# kicker + headline + subhead on a solid card spanning 45% of the height:
# usable, drawable, typed 'announcement'.
GOOD = {"status": "ok", "kind": "graphic_card", "background": {"treatment": "solid"},
        "elements": [_el("kicker", .1, .1, .4, .06), _el("headline", .1, .2, .8, .2),
                     _el("subhead", .1, .45, .7, .1)], "decorations": []}
UNTYPED = {**GOOD, "elements": [_el("kicker", .1, .1, .4, .06), _el("subhead", .1, .45, .7, .1)]}
HOLE = {**GOOD, "elements": [_el("headline", .05, .05, .9, .1)]}
PHOTO = {"status": "ok", "kind": "photo_forward",
         "background": {"treatment": "full_bleed_photo"},
         "elements": [_el("headline", .05, .8, .9, .1)], "decorations": []}


class _Conn:
    def __init__(self, *, known=None, counts=None, fresh=True, stored="approved",
                 twin=None, insert_raises=None):
        self.calls: list[tuple[str, str, tuple]] = []
        self.known = known
        self.counts = counts or {}
        self.fresh = fresh
        self.stored = stored
        self.twin = twin
        self.insert_raises = insert_raises

    async def fetchrow(self, sql, *a):
        self.calls.append(("fetchrow", sql, a))
        if "WHERE source_key = $1" in sql:
            return self.known
        if "count(*) FILTER" in sql:
            return {"family_in_niche": 0, "family_global": 0, "type_in_niche": 0,
                    "niche_approved": 0, **self.counts}
        if "WHERE fingerprint = $1" in sql:
            return self.twin
        if "INSERT INTO house_layouts" in sql:
            if self.insert_raises:
                exc, self.insert_raises = self.insert_raises, None
                raise exc
            return {"id": ID, "fresh": self.fresh,
                    "status": a[10] if self.fresh else self.stored,
                    "niches": list(a[8]) + ([] if self.fresh else ["older"])}
        raise AssertionError(f"unexpected fetchrow: {sql}")

    async def execute(self, sql, *a):
        self.calls.append(("execute", sql, a))

    def sqls(self):
        return [s for _, s, _ in self.calls]

    def writes(self):
        return [s for s in self.sqls()
                if s.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE"))
                or "INSERT INTO" in s]


class _Acquire:
    def __init__(self, conn):
        self.conn = conn
        self.tenants: list = []

    def __call__(self, tenant=None, **kw):
        self.tenants.append(tenant)
        return self

    async def __aenter__(self):
        return self.conn

    async def __aexit__(self, *a):
        return False


@pytest.fixture
def rig(monkeypatch):
    def make(spec=GOOD, **conn_kw):
        conn = _Conn(**conn_kw)
        acq = _Acquire(conn)
        monkeypatch.setattr(hh, "acquire", acq)
        reads, saved = [], []

        async def extract(image, *, mime="image/jpeg"):
            reads.append(mime)
            if isinstance(spec, BaseException):
                raise spec
            if callable(spec):
                return await spec()
            return spec

        import james_os.design_cloner as dc
        monkeypatch.setattr(dc, "extract_template_spec", extract)

        class _Store:
            def save(self, tenant, data, name):
                saved.append((tenant, name, len(data)))
                return f"/media/{tenant}/{name}", "/tmp/x"

        import james_os.media as media
        monkeypatch.setattr(media, "storage", lambda: _Store())
        return SimpleNamespace(conn=conn, acquire=acq, reads=reads, saved=saved)
    return make


def _go(**kw):
    args = dict(source_key=KEY, niches=["Golf Resort", "golf"], run_id="run-1",
                by="harvest:nightly", approve=True)
    args.update(kw)
    image = args.pop("image", PNG)
    return asyncio.run(hh.harvest_ingest(image, **args))


# ── cost: nothing paid for twice ──────────────────────────────────────────


def test_a_known_source_key_is_a_duplicate_with_no_vision_call(rig):
    r = rig(known={"id": ID, "status": "approved", "niches": ["golf"]})
    out = _go()
    assert out["verdict"] == "duplicate" and out["reason"] == "source_key"
    assert out["vision_called"] is False and out["retryable"] is False
    assert out["house_layout_id"] == ID and out["stored_status"] == "approved"
    assert r.reads == [], "the paid read must not run for a known image"
    assert r.conn.writes() == [] and r.saved == []
    assert out["usage"] is None


# ── a dry run writes nothing ──────────────────────────────────────────────


def test_dry_run_decides_but_writes_nothing(rig):
    r = rig()
    out = _go(dry_run=True)
    assert out["verdict"] == "approved" and out["dry_run"] is True
    assert out["stored_status"] == "approved", "the status it WOULD have had"
    assert out["house_layout_id"] is None and out["stored_image_uri"] == ""
    assert r.reads, "a dry run does pay for the read — that is what it measures"
    assert not any("INSERT" in s or "UPDATE" in s for s in r.conn.sqls())
    assert r.saved == []


def test_dry_run_reports_a_shape_already_in_the_catalogue(rig):
    r = rig(twin={"id": ID, "status": "candidate", "niches": ["golf"]})
    out = _go(dry_run=True)
    assert (out["verdict"], out["reason"]) == ("duplicate", "fingerprint")
    assert out["stored_status"] == "candidate"
    assert not any("INSERT" in s for s in r.conn.sqls())


# ── the lock comes first ──────────────────────────────────────────────────


def test_the_advisory_lock_is_taken_before_the_counts_and_the_insert(rig):
    r = rig()
    _go()
    sqls = r.conn.sqls()
    lock = next(i for i, s in enumerate(sqls) if "pg_advisory_xact_lock" in s)
    count = next(i for i, s in enumerate(sqls) if "count(*) FILTER" in s)
    insert = next(i for i, s in enumerate(sqls) if "INSERT INTO house_layouts" in s)
    assert lock < count < insert
    _, sql, args = r.conn.calls[lock]
    assert "hashtext('house_harvest:' || $1)" in sql
    assert args == ("golf resort",), "locked on the exact PRIMARY niche tag"
    # counts are taken against the same primary tag, approved rows only
    _, csql, cargs = r.conn.calls[count]
    assert "status = 'approved'" in csql and cargs[0] == "golf resort"


def test_lock_counts_and_insert_share_one_acquire_block(rig):
    """pg_advisory_xact_lock is held only for its transaction; acquire() is one
    transaction. Splitting the block would release the lock before the write."""
    r = rig()
    out = _go()
    assert out["verdict"] == "approved"
    # source_key read, then ONE block for lock+counts+insert, then the image update
    assert r.acquire.tenants == [None, None, None]


# ── tenant-free ───────────────────────────────────────────────────────────


@pytest.mark.parametrize("case", ["approved", "held", "duplicate", "rejected", "retry", "dry"])
def test_every_connection_is_the_catalogues(rig, case):
    spec = {"rejected": HOLE, "retry": {"status": "failed"}}.get(case, GOOD)
    kw = {}
    if case == "held":
        kw["counts"] = {"family_in_niche": 2}
    if case == "duplicate":
        kw["known"] = {"id": ID, "status": "approved", "niches": []}
    r = rig(spec=spec, **kw)
    _go(dry_run=(case == "dry"))
    assert r.acquire.tenants and all(t is None for t in r.acquire.tenants), r.acquire.tenants


def test_no_brand_material_reaches_the_row(rig):
    r = rig()
    _go(meta={"author": "someone", "judge": {"designed": True}})
    _, sql, args = next(c for c in r.conn.calls if "INSERT INTO house_layouts" in c[1])
    assert "'niche'" in sql, "harvested rows are source_kind 'niche' — no new kind"
    assert "tenant" not in sql.lower() and "promoted_from" not in sql
    meta = json.loads(args[-1])
    assert meta["sha256"] and meta["source_media"] == "OTHER" and meta["author"] == "someone"


# ── the gates ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize("spec,reason", [
    ({"status": "ok", "kind": "graphic_card", "elements": []}, "unusable"),
    (HOLE, "not_drawable"),
    (PHOTO, "plain_photo"),
])
def test_what_is_not_a_layout_is_rejected_and_nothing_is_written(rig, spec, reason):
    r = rig(spec=spec)
    out = _go()
    assert (out["verdict"], out["reason"]) == ("rejected", reason)
    assert out["vision_called"] is True and out["retryable"] is False
    assert r.conn.writes() == [] and r.saved == []


def test_a_photo_with_a_decoration_is_not_a_plain_photo(rig):
    rig(spec={**PHOTO, "decorations": [{"type": "bar", "box": {"x": 0, "y": .9, "w": 1, "h": .02}}]})
    assert _go()["reason"] != "plain_photo"


def test_an_untyped_layout_is_held_not_approved(rig):
    r = rig(spec=UNTYPED)
    out = _go()
    assert (out["verdict"], out["reason"]) == ("held", "untyped")
    assert out["stored_status"] == "candidate"
    _, _, args = next(c for c in r.conn.calls if "INSERT INTO house_layouts" in c[1])
    assert args[10] == "candidate" and args[11] == "auto-capped:untyped"


@pytest.mark.parametrize("spec,reason", [
    ({"status": "no_key"}, "no_key"),
    ({"status": "failed", "error": "boom"}, "vision_failed"),
    (RuntimeError("openai down"), "vision_failed"),
    (None, "vision_failed"),
])
def test_a_failed_read_is_retryable_and_records_nothing(rig, spec, reason):
    r = rig(spec=spec)
    out = _go()
    assert (out["verdict"], out["reason"]) == ("retry", reason)
    assert out["retryable"] is True and out["vision_called"] is True
    assert r.conn.writes() == []


def test_a_slow_read_times_out_as_retry(rig, monkeypatch):
    async def slow():
        await asyncio.sleep(5)
        return GOOD
    r = rig(spec=slow)
    monkeypatch.setattr(hh, "EXTRACT_TIMEOUT_S", 0.01)
    out = _go()
    assert (out["verdict"], out["reason"], out["retryable"]) == ("retry", "timeout", True)
    assert r.conn.writes() == []


# ── the caps ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize("counts,reason", [
    ({"family_in_niche": 2}, "family_cap"),
    ({"family_global": 6}, "family_cap_global"),
    ({"niche_approved": 10, "type_in_niche": 4}, "type_share"),     # 5/11 > .35
])
def test_each_cap_holds_the_row_as_a_candidate(rig, counts, reason):
    r = rig(counts=counts)
    out = _go()
    assert (out["verdict"], out["reason"]) == ("held", reason)
    assert out["stored_status"] == "candidate"
    _, _, args = next(c for c in r.conn.calls if "INSERT INTO house_layouts" in c[1])
    assert args[10] == "candidate" and args[11] == f"auto-capped:{reason}"
    assert out["counts"]["family_in_niche"] == counts.get("family_in_niche", 0)


def test_type_share_only_applies_once_the_niche_has_a_pool():
    pol = hh.resolve_policy(None)
    small = {"family_in_niche": 0, "family_global": 0, "type_in_niche": 8, "niche_approved": 9}
    assert hh.decide(small, layout_type="offer_card", approve=True, policy=pol) == ("approved", "")
    under = {"family_in_niche": 0, "family_global": 0, "type_in_niche": 2, "niche_approved": 10}
    assert hh.decide(under, layout_type="offer_card", approve=True, policy=pol) == ("approved", "")


def test_a_harvested_row_is_stored_with_its_vocabulary_labels(rig):
    """The harvest tags a row with the brand's own phrase; a brand whose phrase
    differs shared no word with it. The labels are added from the words, with no
    vision call beyond the layout read, and the primary tag stays first because
    the balance caps count by it."""
    r = rig()
    out = _go(niches=["Golf Resort", "golf"])
    _, _sql, args = next(c for c in r.conn.calls if "INSERT INTO house_layouts" in c[1])
    assert args[8] == ["golf resort", "golf", "hospitality"]
    assert r.reads == ["image/png"], "one read: the layout's, nothing for the niche"
    lock = next(c for c in r.conn.calls if "pg_advisory_xact_lock" in c[1])
    assert lock[2] == ("golf resort",)
    assert out["niches"] == ["golf resort", "golf", "hospitality"]


def test_approve_defaults_off_and_holds(rig):
    r = rig()
    out = asyncio.run(hh.harvest_ingest(PNG, source_key=KEY, niches=["golf"],
                                        run_id="r", by="harvest:nightly"))
    assert (out["verdict"], out["reason"]) == ("held", "approve_off")
    assert out["stored_status"] == "candidate"
    assert r.saved, "a held row is still kept for review, with its picture"


def test_an_approved_row_carries_the_auto_review(rig):
    r = rig()
    out = _go()
    assert out["verdict"] == "approved" and out["reason"] == ""
    _, sql, args = next(c for c in r.conn.calls if "INSERT INTO house_layouts" in c[1])
    assert "'harvest:auto'" in sql
    assert args[10] == "approved"
    assert args[11].startswith("auto: drawable; family 1/2; announcement 1/1")
    assert args[12] == KEY and args[13] == "run-1"
    assert args[9] == "harvest:nightly"
    assert out["layout_type"] == "announcement" and out["family_key"] and out["fingerprint"]


def test_a_policy_moves_caps_only_within_their_range():
    pol = hh.resolve_policy({"family_per_niche": 99, "type_share": 0.01, "junk": 1})
    assert pol["family_per_niche"] == 10 and pol["type_share"] == 0.2
    assert "junk" not in pol
    with pytest.raises(hh.HarvestInputError):
        hh.resolve_policy({"family_global": "lots"})
    with pytest.raises(hh.HarvestInputError):
        hh.resolve_policy({"family_global": True})


def test_a_policy_is_applied(rig):
    rig(counts={"family_in_niche": 2})
    assert _go(policy={"family_per_niche": 3})["verdict"] == "approved"


# ── writes ────────────────────────────────────────────────────────────────


def test_the_picture_is_stored_only_after_a_new_row(rig):
    r = rig()
    out = _go()
    assert r.saved and r.saved[0][0] == "house"
    assert out["stored_image_uri"].startswith("/media/house/")
    sqls = r.conn.sqls()
    assert sqls.index(next(s for s in sqls if "INSERT INTO" in s)) < \
        sqls.index(next(s for s in sqls if "source_image_uri" in s))


def test_a_shape_already_held_is_a_duplicate_with_its_stored_status(rig):
    """ON CONFLICT unions the niches and leaves status alone; the verdict says so
    and reports what is STORED, not what was asked for."""
    r = rig(fresh=False, stored="candidate")
    out = _go()
    assert (out["verdict"], out["reason"]) == ("duplicate", "fingerprint")
    assert out["stored_status"] == "candidate"
    assert "older" in out["niches"], "the stored union is reported"
    assert r.saved == [], "no picture for a row that was not written"
    _, sql, _ = next(c for c in r.conn.calls if "INSERT INTO house_layouts" in c[1])
    conflict = sql.split("DO UPDATE SET")[1].split("RETURNING")[0]
    assert "EXCLUDED.niches" in conflict and "status" not in conflict


def test_a_source_key_race_is_a_duplicate(rig):
    class UniqueViolationError(Exception):
        constraint_name = "house_layouts_source_key_uq"

    r = rig(insert_raises=UniqueViolationError("dup"))
    r.conn.known = None
    calls = {"n": 0}
    orig = r.conn.fetchrow

    async def fetchrow(sql, *a):
        if "WHERE source_key = $1" in sql:
            calls["n"] += 1
            return None if calls["n"] == 1 else {"id": ID, "status": "approved", "niches": []}
        return await orig(sql, *a)
    r.conn.fetchrow = fetchrow
    out = _go()
    assert (out["verdict"], out["reason"]) == ("duplicate", "source_key")
    assert out["house_layout_id"] == ID and r.saved == []


def test_any_other_database_fault_propagates(rig):
    rig(insert_raises=RuntimeError("connection lost"))
    with pytest.raises(RuntimeError):
        _go()


# ── input validation (the route maps these to 400 / 413 / 415) ────────────


@pytest.mark.parametrize("kw", [
    {"source_key": "onc:XYZ"},
    {"source_key": "onc:" + "a" * 39},
    {"niches": []},
    {"niches": ["", "  "]},
    {"niches": ["a", "b", "c", "d", "e"]},
    {"niches": ["x" * 61]},
    {"run_id": ""},
    {"run_id": "r" * 65},
    {"by": "roy"},
    {"meta": {"blob": "x" * 5000}},
    {"image": b""},
])
def test_bad_input_is_a_400_before_anything_runs(rig, kw):
    r = rig()
    with pytest.raises(hh.HarvestInputError) as exc:
        _go(**kw)
    assert exc.value.status == 400
    assert r.reads == [] and r.acquire.tenants == []


def test_size_and_type_are_judged_on_the_bytes(rig):
    r = rig()
    with pytest.raises(hh.HarvestTooLarge) as big:
        _go(image=PNG + b"\x00" * hh.MAX_BYTES)
    assert big.value.status == 413
    with pytest.raises(hh.HarvestUnsupportedType) as svg:
        _go(image=b"<svg xmlns='http://www.w3.org/2000/svg'/>")
    assert svg.value.status == 415
    assert r.reads == []
    _go(image=JPEG)
    assert r.reads == ["image/jpeg"]


def test_niches_are_cleaned_and_the_first_is_primary():
    assert hh.parse_niches(" Golf Resort , golf,GOLF ,") == ["golf resort", "golf"]
    assert hh.parse_niches(["Real Estate"]) == ["real estate"]


# ── stats and revoke ──────────────────────────────────────────────────────


class _StatsConn:
    def __init__(self):
        self.calls = []

    async def fetch(self, sql, *a):
        self.calls.append((sql, a))
        if "count(DISTINCT h.family_key)" in sql:
            return [{"tag": "golf", "families": 3}]
        return [
            {"tag": "golf", "status": "approved", "layout_type": "offer_card",
             "held_reason": "", "n": 3, "recent": 2},
            {"tag": "golf", "status": "approved", "layout_type": "stat_card",
             "held_reason": "", "n": 1, "recent": 0},
            {"tag": "golf", "status": "candidate", "layout_type": "offer_card",
             "held_reason": "family_cap", "n": 2, "recent": 2},
            {"tag": "golf", "status": "candidate", "layout_type": "statement",
             "held_reason": "", "n": 1, "recent": 0},
        ]

    async def fetchrow(self, sql, *a):
        self.calls.append((sql, a))
        return {"total_approved": 29, "harvested_approved": 4}


def test_stats_per_niche(monkeypatch):
    conn = _StatsConn()
    acq = _Acquire(conn)
    monkeypatch.setattr(hh, "acquire", acq)
    out = asyncio.run(hh.harvest_stats([" Golf ", "golf", "real estate"]))
    g = out["niches"]["golf"]
    assert g["approved"] == 4 and g["held"] == 3 and g["families"] == 3
    assert g["held_by_reason"] == {"family_cap": 2, "awaiting_review": 1}
    assert g["harvested_last_24h"] == 2
    assert g["by_type"]["offer_card"] == {"approved": 3, "share": 0.75}
    assert out["niches"]["real estate"]["approved"] == 0
    assert out["total_approved"] == 29 and out["harvested_approved"] == 4
    assert acq.tenants == [None]
    sql, args = conn.calls[0]
    assert "tag = ANY(h.niches)" in sql, "membership is exact"
    assert args[0] == ["golf", "real estate"]


class _RevokeConn:
    def __init__(self):
        self.calls = []

    async def fetchval(self, sql, *a):
        self.calls.append((sql, a))
        return 5 if "UPDATE" in sql else 2


def test_revoke_touches_only_the_machines_own_decisions(monkeypatch):
    conn = _RevokeConn()
    acq = _Acquire(conn)
    monkeypatch.setattr(hh, "acquire", acq)
    out = asyncio.run(hh.harvest_revoke("run-1", "harvest:manual:roy@x"))
    assert out == {"run_id": "run-1", "rejected": 5, "skipped_human_reviewed": 2}
    sql, args = conn.calls[0]
    assert "status = 'rejected'" in sql
    assert "harvest_run_id = $1" in sql and "reviewed_by = $3" in sql
    assert "status <> 'rejected'" in sql, "idempotent: a second call rejects nothing new"
    assert args[0] == "run-1" and args[2] == "harvest:auto"
    assert acq.tenants == [None]
    with pytest.raises(hh.HarvestInputError):
        asyncio.run(hh.harvest_revoke("", "x"))
