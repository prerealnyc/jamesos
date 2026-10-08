"""Layout-TYPE freshness in the catalogue ranking.

Measured 2026-10-08: every house adoption so far was an offer_card or a
testimonial while statement, stat_card, labelled, statement_pair and
announcement layouts sat unused. A type the brand has NOT drawn lately now wins
a tie — inside the same niche / recency / outcome tier, never across it.
"""
import asyncio
import json

from james_os import design_templates as dt
from james_os import house_layouts as hl
from tests.test_house_picker_volume import _ConnMine, _house_row, _wire, _spec


def test_a_type_the_brand_just_drew_sorts_behind_an_equal_fit(monkeypatch):
    rows = [_house_row(1, ltype="offer_card"), _house_row(2, ltype="stat_card")]
    _wire(monkeypatch, [], rows)
    got = asyncio.run(hl.candidates(_ConnMine([]), "t", niches=["golf"],
                                    recent_types=["offer_card", "testimonial"]))
    assert [r["id"] for r in got] == ["h2", "h1"]


def test_type_freshness_never_beats_a_better_niche_fit(monkeypatch):
    rows = [_house_row(1, ltype="offer_card"),
            _house_row(2, ltype="stat_card", tags=("knitting",))]
    _wire(monkeypatch, [], rows)
    got = asyncio.run(hl.candidates(_ConnMine([]), "t", niches=["golf"],
                                    recent_types=["offer_card"]))
    assert got[0]["id"] == "h1", "an off-niche fresh type must not jump an on-niche row"


def test_type_freshness_ranks_ahead_of_family_freshness(monkeypatch):
    # h1: fresh family but a recently drawn type; h2: recent family but a fresh type
    rows = [_house_row(1, ltype="offer_card", fam="famA"),
            _house_row(2, ltype="statement", fam="famB")]
    _wire(monkeypatch, [], rows)
    got = asyncio.run(hl.candidates(_ConnMine([]), "t", niches=["golf"],
                                    recent_families=["famB"], recent_types=["offer_card"]))
    assert [r["id"] for r in got] == ["h2", "h1"]


def test_callers_that_pass_no_recent_types_keep_todays_order(monkeypatch):
    _wire(monkeypatch, [], [_house_row(1), _house_row(2, ltype="stat_card")])
    got = asyncio.run(hl.candidates(_ConnMine([]), "t", niches=["golf"]))
    assert [r["id"] for r in got] == ["h1", "h2"]


def test_recent_types_come_from_the_latest_drawable_draws():
    class _Conn:
        async def fetch(self, sql, *a):
            assert "ORDER BY last_used_at DESC" in sql and "FROM design_templates" in sql
            return [{"spec": json.dumps(_spec())}, {"spec": "{}"}, {"spec": json.dumps(_spec())}]

    got = asyncio.run(dt._recent_types(_Conn(), hl))
    assert got and all(isinstance(t, str) and t for t in got)
    assert len(got) == 2, "the undrawable '{}' row is skipped"


def test_recent_types_never_cost_a_pick():
    class _Broken:
        async def fetch(self, sql, *a):
            raise RuntimeError("db down")

    assert asyncio.run(dt._recent_types(_Broken(), hl)) == []
