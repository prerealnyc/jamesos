"""The brand's OWN posts get a lane of their own.

A brand that already posts a recognisable kind of thing should get more of it.
`design_templates.source_kind` has allowed 'own' since the table was created and
nothing had ever written it — the slot was designed and left empty.

Filling it needed the picker widened first, and that is the part worth testing.
The lane partition used to be the BOOLEAN `(source_kind = 'niche')`, so an 'own'
row would have joined the competitor lane silently: ranked against competitors
on `source_engagement`, which is 0.0 for an own post, and governed by no share
at all. Writing the rows without widening the picker would have been worse than
not writing them.
"""

import asyncio
import json as _json

import pytest

from james_os import design_templates as dt
from tests.test_design_templates import _el, _picker_over, _read


def _rows(**counts):
    """Rows as the picker's query returns them, for any mix of lanes.

    counts: n_other/n_niche/n_own and used_other/used_niche/used_own.
    """
    spec = _read([_el("headline", .05, .6, .9, .2, "xl")])
    out = []
    for lane, kind in (("other", "competitor"), ("niche", "niche"), ("own", "own")):
        for i in range(counts.get(f"n_{lane}", 0)):
            out.append({
                "id": f"{lane[0]}{i}", "spec": _json.dumps(spec), "kind": "graphic_card",
                "source_handle": lane, "source_url": "", "source_platform": "instagram",
                "source_kind": kind, "times_used": counts.get(f"used_{lane}", 0),
            })
    return out


def test_an_own_layout_is_drawn_at_all(monkeypatch):
    """Behind a wall of competitor layouts it would never surface on rank alone —
    own posts have no engagement RATE to be ranked on."""
    _picker_over(_rows(n_other=193, n_own=4), monkeypatch)
    assert asyncio.run(dt.pick("t"))["source_kind"] == "own"


def test_the_own_lane_is_a_minority_too(monkeypatch):
    """More of what you already make, not only what you already make."""
    # own already over its share of what has been used -> the majority draws
    _picker_over(_rows(n_other=10, n_own=10, used_other=1, used_own=3), monkeypatch)
    assert asyncio.run(dt.pick("t"))["source_kind"] == "competitor"
    # under its share -> own draws
    _picker_over(_rows(n_other=10, n_own=10, used_other=9, used_own=1), monkeypatch)
    assert asyncio.run(dt.pick("t"))["source_kind"] == "own"


def test_three_lanes_coexist_and_the_hungriest_eats(monkeypatch):
    """niche and own are governed independently; whichever is furthest below its
    share goes first."""
    # own starved, niche satisfied
    _picker_over(_rows(n_other=10, n_niche=10, n_own=10,
                       used_other=6, used_niche=4, used_own=0), monkeypatch)
    assert asyncio.run(dt.pick("t"))["source_kind"] == "own"
    # niche starved, own satisfied
    _picker_over(_rows(n_other=10, n_niche=10, n_own=10,
                       used_other=6, used_niche=0, used_own=4), monkeypatch)
    assert asyncio.run(dt.pick("t"))["source_kind"] == "niche"


def test_competitors_keep_the_majority(monkeypatch):
    """Two governed minorities at 0.25 leave the ungoverned lane the largest
    single share. Fixing starvation must not invert it — twice."""
    assert dt.NICHE_SHARE + dt.OWN_SHARE < 1 - (dt.NICHE_SHARE + dt.OWN_SHARE) + 0.01
    _picker_over(_rows(n_other=10, n_niche=10, n_own=10,
                       used_other=2, used_niche=4, used_own=4), monkeypatch)
    assert asyncio.run(dt.pick("t"))["source_kind"] == "competitor"


def test_a_brand_with_only_its_own_layouts_still_draws_them(monkeypatch):
    _picker_over(_rows(n_own=5), monkeypatch)
    assert asyncio.run(dt.pick("t"))["source_kind"] == "own"


def test_an_own_layout_is_never_shared_into_the_house_catalogue(monkeypatch):
    """A brand's own designs are its own. house_layouts gates on a whitelist, so
    'own' is refused without anyone having to remember to exclude it."""
    from james_os import house_layouts

    assert "own" not in house_layouts.SHAREABLE
    assert set(house_layouts.SHAREABLE) == {"competitor", "niche", "reference"}


def test_the_lane_key_is_defined_once():
    """The SQL partition and the Python split must not be able to disagree —
    they did before, and that is how an unwritten kind would have slipped into
    the competitor lane ungoverned."""
    assert "'own'" in dt._LANE_SQL and "'niche'" in dt._LANE_SQL
    assert dt._lane_of("own") == "own"
    assert dt._lane_of("niche") == "niche"
    assert dt._lane_of("competitor") == "other"
    assert dt._lane_of("reference") == "other"
    assert dt._lane_of(None) == "other"
