"""Matching a shared layout to the brands it suits.

Niche tags are FREE TEXT on both sides and nobody agreed a vocabulary. The live
values, read from production 2026-10-02: brands carry 'golf resort',
'commercial real estate', 'tour packages, travel and holidays', 'political
candidates', 'commercial spaceport'; the pool carries 'golf' and 'real estate',
and 12 of its 17 rows carry nothing at all.

Whole-string equality — and Postgres `&&` on the arrays, which is the obvious
implementation — matches NONE of those pairs. That is the failure this file
exists to stop: niche targeting that looks implemented and behaves randomly.
"""

import pytest

from james_os import house_layouts as hl


# ---------------------------------------------------------- the real pairings

@pytest.mark.parametrize("brand,tags,why", [
    ("golf resort", ["golf"], "Turtleback, exactly as production holds it"),
    ("commercial real estate", ["real estate"], "James Prendamano"),
    ("New York commercial real estate", ["real estate"], "a longer brand phrase"),
    ("tour packages, travel and holidays", ["travel"], "comma phrase vs one word"),
    ("curated India travel packages", ["travel"], "the other Trouvailler"),
])
def test_the_live_brand_and_tag_pairs_actually_match(brand, tags, why):
    assert hl.niche_rank(brand, tags) >= 2, why
    # and the obvious implementation would have missed every one of them
    assert not (set([brand]) & set(tags)), "whole-string equality would find nothing"


def test_an_off_niche_layout_ranks_below_an_untagged_one():
    """Untagged means 'generic, suits anybody'; tagged-for-someone-else does not.
    Getting this order wrong hands a golf resort a layout tagged for politics
    ahead of a neutral one."""
    assert hl.niche_rank("golf resort", []) > hl.niche_rank("golf resort", ["political candidates"])


def test_more_shared_words_rank_higher():
    # Not "commercial real estate" any more: "commercial" is a stop word (it is
    # the only word "commercial spaceport" shares with real estate), so that
    # phrase and "real estate" now match on the same two words.
    assert (hl.niche_rank("luxury golf resort", ["luxury golf resort"])
            > hl.niche_rank("luxury golf resort", ["golf resort"])
            > hl.niche_rank("luxury golf resort", ["golf"])
            > hl.niche_rank("luxury golf resort", ["politics"]))


def test_filler_words_do_not_create_a_match():
    """'tour PACKAGES' and 'software packages' share a word that means nothing.
    Without a stoplist those two read as the same niche."""
    assert hl.niche_rank("tour packages, travel and holidays", ["software packages"]) == 0
    assert hl.niche_rank("New York commercial real estate", ["New York bakery"]) == 0


def test_matching_is_case_and_punctuation_blind():
    assert hl.niche_rank("GOLF RESORT", ["Golf"]) >= 2
    assert hl.niche_rank("real-estate", ["real estate"]) >= 2


@pytest.mark.parametrize("junk", [None, "", [], [""], ["   "], [None]])
def test_a_brand_with_no_niche_still_gets_a_usable_ranking(junk):
    """Most brands will have no niche recorded for a while. That must degrade to
    'untagged first', never to an exception in the path that stocks a library."""
    assert hl.niche_rank(junk, []) == 1
    assert hl.niche_rank(junk, ["golf"]) == 0
    assert hl.niche_rank("golf", junk) == 1


def test_tokens_drop_short_and_filler_words():
    assert hl.niche_tokens("the art of golf in New York") == {"art", "golf"}


# ------------------------------------------------- ranking inside adopt()

class _Conn:
    def __init__(self, rows, niche_rows=None):
        self.rows, self.niche_rows, self.inserted = rows, niche_rows or [], []
    async def fetch(self, sql, *a):
        if "FROM competitors" in sql:
            return self.niche_rows
        return self.rows
    async def fetchval(self, sql, *a):
        self.inserted.append(a)
        return f"new-{len(self.inserted)}"
    async def execute(self, sql, *a):
        return None


def _wire(monkeypatch, rows, niche_rows=None):
    import contextlib
    conn = _Conn(rows, niche_rows)
    seen = []

    @contextlib.asynccontextmanager
    async def _acq(tenant_id=None):
        seen.append(tenant_id)
        yield conn

    monkeypatch.setattr(hl, "acquire", _acq)
    return conn, seen


def _row(i, tags):
    return {"id": f"h{i}", "kind": "graphic_card", "spec": "{}", "fingerprint": f"f{i}",
            "source_kind": "curated", "source_url": "", "source_image_uri": "",
            "niches": tags}


@pytest.mark.asyncio
async def test_adopt_takes_the_matching_layouts_first(monkeypatch):
    """THE behaviour. The batch is 8; offer 3 off-niche, 1 untagged, 1 matching,
    and ask for 2 — the matching one and the untagged one must be what lands."""
    rows = [_row(1, ["political candidates"]), _row(2, ["cooking"]),
            _row(3, []), _row(4, ["golf"]), _row(5, ["knitting"])]
    conn, _ = _wire(monkeypatch, rows, niche_rows=[{"niche": "golf resort"}])
    out = await hl.adopt("t", limit=2)
    assert out["adopted"] == 2
    took = [a[2] for a in conn.inserted]           # fingerprint is arg 3
    assert took == ["f4", "f3"], f"expected the golf one then the untagged one, got {took}"
    assert out["matched_on"] == ["golf resort"]


@pytest.mark.asyncio
async def test_a_brand_with_no_recorded_niche_still_adopts(monkeypatch):
    """Ranking, not filtering: no niche must not mean no layouts."""
    conn, _ = _wire(monkeypatch, [_row(1, ["golf"]), _row(2, [])], niche_rows=[])
    out = await hl.adopt("t", limit=2)
    assert out["adopted"] == 2


@pytest.mark.asyncio
async def test_an_explicit_niche_overrides_the_brands_own(monkeypatch):
    conn, _ = _wire(monkeypatch, [_row(1, ["golf"]), _row(2, ["travel"])],
                    niche_rows=[{"niche": "golf resort"}])
    out = await hl.adopt("t", limit=1, niches=["travel blogging"])
    assert [a[2] for a in conn.inserted] == ["f2"]
    assert out["matched_on"] == ["travel blogging"]


@pytest.mark.asyncio
async def test_the_brands_niche_is_read_under_its_OWN_tenant(monkeypatch):
    """competitors is RLS-scoped. Reading the niche on the catalogue's
    tenant-less connection would return zero rows silently and every brand would
    look nicheless — the bug would be invisible."""
    conn, seen = _wire(monkeypatch, [_row(1, [])], niche_rows=[{"niche": "golf"}])
    await hl.adopt("tenant-A", limit=1)
    assert seen[0] == "tenant-A", "the niche read must be tenant-scoped"
    assert None in seen, "the catalogue read must be tenant-less"


@pytest.mark.asyncio
async def test_an_unreadable_niche_does_not_break_stocking(monkeypatch):
    import contextlib

    class _Boom(_Conn):
        async def fetch(self, sql, *a):
            if "FROM competitors" in sql:
                raise RuntimeError("no competitors table for this tenant")
            return self.rows

    conn = _Boom([_row(1, [])])

    @contextlib.asynccontextmanager
    async def _acq(tenant_id=None):
        yield conn

    monkeypatch.setattr(hl, "acquire", _acq)
    out = await hl.adopt("t", limit=1)
    assert out["adopted"] == 1
