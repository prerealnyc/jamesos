"""The design template library's rules — dedup, usability, never-delete.

Pure logic; the database-touching paths are exercised against a live tenant.
"""

import copy

import pytest

from james_os import design_templates as dt


@pytest.fixture(autouse=True)
def fresh_pool():
    """No DB here — shadow conftest's autouse Postgres fixture."""
    yield


SPEC = {
    "status": "ok",
    "kind": "graphic_card",
    "background": {"treatment": "solid", "scrim": "none", "photo_box": None},
    "palette": {"bg": "#111318", "accent": "#c9a24b", "ink": "#ffffff"},
    "elements": [
        {"role": "kicker", "box": {"x": 0.07, "y": 0.06, "w": 0.86, "h": 0.07},
         "align": "left", "size": "sm", "weight": "bold", "case": "upper", "color": "#c9a24b"},
        {"role": "headline", "box": {"x": 0.07, "y": 0.55, "w": 0.86, "h": 0.22},
         "align": "left", "size": "xxl", "weight": "black", "case": "none", "color": "#ffffff"},
    ],
    "decorations": [{"type": "bar", "box": {"x": 0.07, "y": 0.52, "w": 0.2, "h": 0.006},
                     "color": "#c9a24b"}],
}


# ------------------------------------------------------------------ dedup


def test_the_same_layout_in_other_colours_is_one_template():
    """Every template is rebranded to the brand's palette before it is drawn,
    so the same arrangement in different colours is the same template — the
    library grows by NEW layouts, not by recolourings of one."""
    recoloured = copy.deepcopy(SPEC)
    recoloured["palette"] = {"bg": "#ffffff", "accent": "#0055ff", "ink": "#000000"}
    for e in recoloured["elements"]:
        e["color"] = "#123456"
    recoloured["decorations"][0]["color"] = "#abcdef"
    assert dt.fingerprint(recoloured) == dt.fingerprint(SPEC)


def test_a_pixel_of_jitter_is_still_the_same_layout():
    """A layout read twice never comes back with identical boxes. Rounded to 5%
    of the canvas, it is one row."""
    jittered = copy.deepcopy(SPEC)
    jittered["elements"][1]["box"]["x"] = 0.071
    jittered["elements"][1]["box"]["y"] = 0.552
    assert dt.fingerprint(jittered) == dt.fingerprint(SPEC)


def test_a_genuinely_different_layout_is_a_different_template():
    moved = copy.deepcopy(SPEC)
    moved["elements"][1]["box"]["y"] = 0.15          # headline to the top
    assert dt.fingerprint(moved) != dt.fingerprint(SPEC)

    photo = copy.deepcopy(SPEC)
    photo["background"]["treatment"] = "full_bleed_photo"
    assert dt.fingerprint(photo) != dt.fingerprint(SPEC)


def test_element_order_does_not_make_a_new_layout():
    """The same blocks listed in a different order are the same design."""
    flipped = copy.deepcopy(SPEC)
    flipped["elements"].reverse()
    assert dt.fingerprint(flipped) == dt.fingerprint(SPEC)


# -------------------------------------------------------------- usability


def test_an_honest_empty_read_is_never_kept():
    """design_cloner reports 'no_key' / 'failed' rather than faking a spec. Those
    must not become templates that render a blank card."""
    assert dt.usable(SPEC) is True
    assert dt.usable({**SPEC, "status": "no_key"}) is False
    assert dt.usable({**SPEC, "status": "failed"}) is False
    assert dt.usable({**SPEC, "elements": []}) is False
    assert dt.usable(None) is False


# ------------------------------------------------------ the owner's rules


def test_nothing_is_ever_deleted():
    """"Don't throw away any layouts." There is no delete in the service, and
    the migration does not grant the app DELETE on the table — so it is a
    guarantee of the database, not a convention a later change could break."""
    import inspect
    from pathlib import Path

    src = inspect.getsource(dt)
    assert "DELETE FROM design_templates" not in src.upper().replace("\n", " ")
    migration = (Path(__file__).parent.parent / "migrations" / "060_design_templates.sql").read_text()
    grant = [ln for ln in migration.splitlines() if "GRANT" in ln and "design_templates" in ln]
    assert grant, "the migration must grant the app role"
    assert all("DELETE" not in ln.upper() for ln in grant), "the app must not be able to DELETE"


def test_a_failing_layout_is_retired_not_removed():
    """Three design-QA failures take a layout out of rotation. It keeps its row,
    its source and its record — a later renderer may draw it fine."""
    import inspect

    src = inspect.getsource(dt.mark_qa)
    # the SQL statement, not the word — the docstring says "never deleted"
    assert "retired" in src and "DELETE FROM" not in src.upper()
    assert dt.RETIRE_AFTER_QA_FAILS >= 2


def test_learning_reads_our_copy_not_the_expiring_source():
    """In-house: a layout is learned from competitor_posts.stored_media_url —
    our durable copy — never from media_url, which for Instagram and TikTok
    expires within days."""
    import inspect

    src = inspect.getsource(dt._unlearned_competitor_stills)
    assert "stored_media_url" in src
    assert "p.media_url" not in src, "must never learn from the expiring source URL"


# ------------------------------------------------------------------ reading


def _run_learner(monkeypatch, reads, *, library_grows=True):
    """Drive learn_from_competitors over fake posts whose reads return `reads`
    in order; record what was marked read and what was noted as a failure."""
    import asyncio

    from james_os import design_cloner, template_clone

    posts = [{"id": f"p{i}", "stored_media_url": f"file://p{i}.png", "url": "",
              "platform": "instagram", "engagement_rate": 0.1, "handle": "peer"}
             for i in range(len(reads))]
    marked, failed, saved = [], [], []
    seq = iter(reads)
    size = {"n": 0}

    async def _stills(tenant_id, limit):
        return posts

    async def _fetch(uri):
        return b"png"

    async def _extract(img):
        r = next(seq)
        if isinstance(r, Exception):
            raise r
        return r

    async def _count(tenant_id, active_only=True):
        return size["n"]

    async def _save(tenant_id, spec, **kw):
        saved.append(kw["source_post_id"])
        if library_grows:
            size["n"] += 1
        return "t-" + kw["source_post_id"]

    async def _mark(tenant_id, post_id):
        marked.append(post_id)

    async def _note(tenant_id, post_id):
        failed.append(post_id)

    monkeypatch.setattr(dt, "_unlearned_competitor_stills", _stills)
    monkeypatch.setattr(template_clone, "_fetch_bytes", _fetch)
    monkeypatch.setattr(design_cloner, "extract_template_spec", _extract)
    monkeypatch.setattr(dt, "count", _count)
    monkeypatch.setattr(dt, "save", _save)
    monkeypatch.setattr(dt, "_mark_read", _mark)
    monkeypatch.setattr(dt, "_note_failed_read", _note)
    out = asyncio.run(dt.learn_from_competitors("tenant"))
    return out, marked, failed, saved


def test_each_post_is_read_once_whatever_it_yields(monkeypatch):
    """A read is a paid vision call. A post that produced a duplicate or no
    layout used to stay 'unread' and was re-billed on every run."""
    no_layout = {"status": "ok", "elements": []}
    out, marked, failed, saved = _run_learner(
        monkeypatch, [SPEC, SPEC, no_layout], library_grows=False)
    assert marked == ["p0", "p1", "p2"], "new, duplicate AND no-layout reads are all final"
    assert failed == []


def test_only_unread_posts_are_picked_up():
    import inspect

    assert "template_read_at IS NULL" in inspect.getsource(dt._unlearned_competitor_stills)


def test_a_failed_read_is_retried_not_thrown_away(monkeypatch):
    """A rate limit or a timeout says nothing about the post. Marking it read
    would lose that layout for good — "don't throw away any layouts"."""
    out, marked, failed, saved = _run_learner(
        monkeypatch, [{"status": "failed", "error": "429"}, RuntimeError("timeout"), SPEC])
    assert marked == ["p2"], "only the post that was actually read is final"
    assert failed == ["p0", "p1"], "both outages count toward a retry, not a verdict"
    assert out["failed"] == 2 and out["learned"] == 1


def test_no_vision_key_reads_nothing_and_marks_nothing(monkeypatch):
    """Without a key every post would come back empty. Marking them would mean
    the whole back catalogue is skipped the day the key is finally set."""
    out, marked, failed, saved = _run_learner(
        monkeypatch, [{"status": "no_key"}, SPEC, SPEC])
    assert marked == [] and failed == [] and saved == []
    assert out["reason"] == "no_key"


def test_a_post_that_always_fails_is_given_up_on_eventually():
    """Retrying forever would spend a slot of every run on one broken post."""
    import inspect

    assert dt.MAX_READ_ATTEMPTS >= 2
    src = inspect.getsource(dt._note_failed_read)
    assert "template_read_attempts + 1" in src and "MAX_READ_ATTEMPTS" in src
    sql = open("migrations/060_design_templates.sql").read()
    assert "template_read_attempts int NOT NULL DEFAULT 0" in sql


# ------------------------------------------------------------------ redo


def test_a_learned_post_is_redone_in_its_own_layout(monkeypatch):
    """"Make the headline shorter" on a learned post used to fall through to the
    art director — the owner got an unrelated card and lost the layout they were
    correcting. It is rebuilt in its own design, like a cloned post, and stays
    tied to its library layout so the verdict on the redo still reaches it."""
    import asyncio
    import contextlib
    import inspect
    import json as _json

    from james_os import api_v1, template_clone

    assert api_v1._rebuilds_in_place({"image_format": "learned"}, "keep")
    assert not api_v1._rebuilds_in_place({"image_format": "learned"}, "new")
    assert "_rebuilds_in_place(" in inspect.getsource(api_v1._run_regenerate)

    written: dict = {}

    class _Conn:
        async def execute(self, sql, new_id, payload):
            written.update(_json.loads(payload))

    @contextlib.asynccontextmanager
    async def _acquire(tenant_id=None):
        yield _Conn()

    class _Store:
        def save(self, tenant, png, name):
            return "file://redo.png", "/tmp/redo.png"

    async def _rebuild(payload, feedback, tenant_id, **kw):
        return {"png": b"png", "kind": "graphic_card", "spec": payload["clone_spec"],
                "content": payload["clone_content"], "hero_key": payload["hero_photo_key"],
                "by_size": {}}

    monkeypatch.setattr(api_v1, "acquire", _acquire)
    monkeypatch.setattr(template_clone, "rebuild_design", _rebuild)
    monkeypatch.setattr("james_os.media.storage", lambda: _Store())
    payload = {
        "image_format": "learned", "design_template_id": "tpl-1",
        "design_template_source": {"kind": "competitor", "handle": "rival"},
        "clone_spec": SPEC, "clone_content": {"headline": "Old line"},
        "hero_photo_key": "hero-7",
    }
    served, fmt = asyncio.run(api_v1._rebuild_cloned_action("new-1", payload, "shorter", "t"))
    assert fmt == "learned"
    assert written["image_format"] == "learned"
    assert written["design_template_id"] == "tpl-1"
    assert written["design_template_source"]["handle"] == "rival"
    assert "cloned_from_competitor" not in written
    assert written["clone_spec"] == SPEC and written["hero_photo_key"] == "hero-7"


def test_the_art_director_is_never_offered_the_learned_slot(monkeypatch):
    """BM2 syncs its enabled templates, which include "learned". That is a slot,
    not a layout: offered to the art director it could be chosen, and the family
    clamp could pick it at random — a format nothing can draw."""
    import asyncio
    import contextlib
    import json as _json

    from james_os import brand_identity, db

    class _Conn:
        async def fetchval(self, sql):
            return _json.dumps({"enabled_formats": ["big_stat", "learned", "full_bleed"]})

    @contextlib.asynccontextmanager
    async def _acquire(tenant_id=None):
        yield _Conn()

    monkeypatch.setattr(db, "acquire", _acquire)
    got = asyncio.run(brand_identity.get_enabled_formats("t"))
    assert got == {"big_stat", "full_bleed"}

    # only-learned-on means no hand-built layout is allowed: an EMPTY set, which
    # the renderer reads as "no designed image" once the library can't serve one
    class _Only(_Conn):
        async def fetchval(self, sql):
            return _json.dumps({"enabled_formats": ["learned"]})

    @contextlib.asynccontextmanager
    async def _acquire_only(tenant_id=None):
        yield _Only()

    monkeypatch.setattr(db, "acquire", _acquire_only)
    assert asyncio.run(brand_identity.get_enabled_formats("t")) == set()


def test_the_app_cannot_delete_a_layout():
    """A GRANT without DELETE is not enough on Supabase: default privileges on
    `public` hand the app role DELETE on every new table. The migration has to
    take it away explicitly, or "never deleted" is only a convention."""
    sql = open("migrations/060_design_templates.sql").read()
    assert "REVOKE DELETE, TRUNCATE ON design_templates FROM james_app" in sql


# ------------------------------------------------------------------ drawing a read layout
# Shapes taken from the first live library (tristatecommercial, prerealinvestments,
# commercial_observer on 11 Sep): what a vision read of a real post looks like.


def _el(role, x, y, w, h, size="md"):
    return {"role": role, "box": {"x": x, "y": y, "w": w, "h": h}, "size": size,
            "align": "left", "weight": "bold", "case": "none", "color": "#ffffff"}


def _read(elements, decorations=(), treatment="full_bleed_photo"):
    return {"status": "ok", "kind": "graphic_card", "palette": {"bg": "#111", "ink": "#fff"},
            "background": {"treatment": treatment, "scrim": "bottom", "photo_box": None},
            "elements": list(elements), "decorations": list(decorations)}


def test_repeated_roles_become_separate_lines():
    """Three byline slots printed the brand name three times."""
    spec = _read([_el("headline", .05, .05, .5, .1, "xl"), _el("byline", .05, .80, .4, .05),
                  _el("byline", .05, .86, .4, .05), _el("byline", .05, .92, .4, .05)])
    roles = [e["role"] for e in dt.prepare(spec)["elements"]]
    assert roles == ["headline", "byline", "byline#2", "byline#3"]


def test_colliding_boxes_keep_the_more_important_line():
    """Two subheads half on top of each other garbled into one smear."""
    spec = _read([_el("headline", .05, .05, .4, .1, "xl"), _el("subhead", .05, .15, .4, .1),
                  _el("subhead", .05, .20, .4, .1), _el("stat", .05, .25, .4, .1)])
    kept = dt.prepare(spec)["elements"]
    boxes = [e["box"] for e in kept]
    assert all(dt._overlap(a, b) <= dt.MAX_TEXT_OVERLAP
               for i, a in enumerate(boxes) for b in boxes[i + 1:])
    assert [e["role"] for e in kept][0] == "headline" and any(e["role"] == "stat" for e in kept), \
        "the stat outranks a second subhead"


def test_an_empty_frame_is_dropped_and_a_framed_line_is_kept():
    """A frame that held an inset photo is an empty rectangle on its own."""
    empty = {"type": "frame", "box": {"x": .3, "y": .4, "w": .4, "h": .3}, "color": "#0056ff"}
    around = {"type": "frame", "box": {"x": .0, "y": .0, "w": .6, "h": .2}, "color": "#fff"}
    spec = _read([_el("headline", .05, .05, .5, .1, "xl")], [empty, around])
    assert dt.prepare(spec)["decorations"] == [around]


def test_prepare_is_idempotent():
    spec = _read([_el("headline", .05, .05, .5, .1, "xl"), _el("byline", .05, .8, .4, .05),
                  _el("byline", .05, .86, .4, .05)])
    once = dt.prepare(spec)
    assert dt.prepare(once) == once


def test_a_card_with_a_hole_in_it_is_kept_but_not_drawn():
    """A white card whose photo the read missed: headline in the top fifth,
    nothing below. Kept (nothing read is thrown away), never drawn."""
    card = _read([_el("headline", .05, .05, .9, .1, "lg"), _el("byline", .05, .16, .9, .05, "sm")],
                 treatment="solid")
    assert dt.usable(card) and not dt.drawable(card)
    # the same text span over a photo is a real photo-led layout
    assert dt.drawable({**card, "background": {"treatment": "full_bleed_photo", "photo_box": None}})
    # and a solid card whose text uses the page is fine
    tall = _read([_el("kicker", .5, .1, .5, .05, "sm"), _el("headline", .1, .2, .8, .1, "lg"),
                  _el("byline", .1, .8, .8, .05, "sm")], treatment="solid")
    assert dt.drawable(tall)


def test_the_picker_only_counts_and_draws_drawable_layouts(monkeypatch):
    import asyncio
    import contextlib
    import json as _json

    hole = _read([_el("headline", .05, .05, .9, .1, "lg")], treatment="solid")
    good = _read([_el("headline", .05, .6, .9, .2, "xl")])
    rows_seen: list = []

    def _row(i, spec):
        return {"id": f"t{i}", "spec": _json.dumps(spec), "kind": "graphic_card",
                "source_handle": "peer", "source_url": "", "source_platform": "instagram",
                "source_kind": "competitor"}

    def _conn_for(rows):
        class _C:
            async def fetch(self, sql, *args):
                rows_seen.append(sql)
                return rows

        @contextlib.asynccontextmanager
        async def _acq(tenant_id=None):
            yield _C()
        return _acq

    # hole first in rotation order: skipped, the next drawable one is picked
    monkeypatch.setattr(dt, "acquire", _conn_for([_row(0, hole), _row(1, good), _row(2, good), _row(3, good)]))
    got = asyncio.run(dt.pick("t"))
    assert got["id"] == "t1"
    # three rows but only two drawable: below the minimum, so the nine are used
    monkeypatch.setattr(dt, "acquire", _conn_for([_row(0, hole), _row(1, good), _row(2, good)]))
    assert asyncio.run(dt.pick("t")) is None


# ------------------------------------------------- the picker's two lanes


def _lane_rows(n_other: int, n_niche: int, used_other: int = 0, used_niche: int = 0):
    """Rows as the picker's query returns them: both lanes, already rank-ordered."""
    import json as _json

    spec = _read([_el("headline", .05, .6, .9, .2, "xl")])
    out = []
    for i in range(n_other):
        out.append({"id": f"c{i}", "spec": _json.dumps(spec), "kind": "graphic_card",
                    "source_handle": "rival", "source_url": "", "source_platform": "instagram",
                    "source_kind": "competitor", "times_used": used_other})
    for i in range(n_niche):
        out.append({"id": f"n{i}", "spec": _json.dumps(spec), "kind": "graphic_card",
                    "source_handle": "niche", "source_url": "", "source_platform": "niche",
                    "source_kind": "niche", "times_used": used_niche})
    return out


def _picker_over(rows, monkeypatch):
    import contextlib

    class _C:
        async def fetch(self, sql, *args):
            return rows

    @contextlib.asynccontextmanager
    async def _acq(tenant_id=None):
        yield _C()

    monkeypatch.setattr(dt, "acquire", _acq)


def test_a_niche_layout_is_drawn_even_behind_a_wall_of_competitor_layouts(monkeypatch):
    """The starvation this fix exists for.

    `source_engagement` is a RATE, and a niche reference has no follower base to
    divide by, so every monitoring-learned layout carries 0.0 by construction —
    not because it performed badly. Ranked against competitor layouts that scored
    above zero, the whole lane sank: measured on Trouvailler 2026-09-28, 30 niche
    layouts and ZERO of them in the top 100 the picker considered.
    """
    import asyncio

    _picker_over(_lane_rows(n_other=193, n_niche=30), monkeypatch)
    got = asyncio.run(dt.pick("t"))
    assert got is not None
    assert got["source_kind"] == "niche", "an unused niche lane must get the first turn"


def test_the_niche_lane_is_a_MINORITY_of_picks_not_a_takeover(monkeypatch):
    """Fixing starvation must not invert it. A brand's own tracked competitors
    are the closer comparison; monitoring is the wider net."""
    import asyncio

    # niche already over its share of what has been used -> the other lane draws
    _picker_over(_lane_rows(n_other=10, n_niche=10, used_other=1, used_niche=3), monkeypatch)
    got = asyncio.run(dt.pick("t"))
    assert got["source_kind"] == "competitor"

    # under its share -> niche draws
    _picker_over(_lane_rows(n_other=10, n_niche=10, used_other=9, used_niche=1), monkeypatch)
    assert asyncio.run(dt.pick("t"))["source_kind"] == "niche"


def test_a_brand_with_only_competitor_layouts_picks_exactly_as_before(monkeypatch):
    """No niche lane means no lane choice at all — the old path, untouched."""
    import asyncio

    _picker_over(_lane_rows(n_other=6, n_niche=0), monkeypatch)
    got = asyncio.run(dt.pick("t"))
    assert got["source_kind"] == "competitor" and got["id"] == "c0"


def test_a_brand_with_only_niche_layouts_still_draws_them(monkeypatch):
    import asyncio

    _picker_over(_lane_rows(n_other=0, n_niche=5), monkeypatch)
    assert asyncio.run(dt.pick("t"))["source_kind"] == "niche"


def test_the_minimum_library_counts_BOTH_lanes_together(monkeypatch):
    """MIN_LIBRARY is about how repetitive the feed looks, which does not care
    which lane a layout came from."""
    import asyncio

    _picker_over(_lane_rows(n_other=1, n_niche=1), monkeypatch)
    assert asyncio.run(dt.pick("t")) is None, "2 drawable layouts is below the minimum"
    _picker_over(_lane_rows(n_other=2, n_niche=1), monkeypatch)
    assert asyncio.run(dt.pick("t")) is not None, "3 across both lanes clears it"


# ------------------------------------------- an owner switching one layout off


def _pause_conn(monkeypatch, status: str, seen: list):
    import contextlib

    class _C:
        async def fetchrow(self, sql, *a):
            return None if status is None else {"status": status}

        async def execute(self, sql, *a):
            seen.append(a)

    @contextlib.asynccontextmanager
    async def _acq(tenant_id=None):
        yield _C()

    monkeypatch.setattr(dt, "acquire", _acq)


def test_pausing_a_layout_is_not_the_same_event_as_qa_retiring_it(monkeypatch):
    """'retired' means design QA gave up after repeated render failures. If an
    owner's "not this one" reused that value, the QA record would stop meaning
    anything — you could no longer ask how many layouts the renderer rejected."""
    import asyncio

    seen: list = []
    _pause_conn(monkeypatch, "active", seen)
    out = asyncio.run(dt.set_paused("t", "abc", True))
    assert out["status"] == "paused" and out["changed"] is True
    assert any("paused" in str(a) for a in seen)


def test_un_pausing_restores_active(monkeypatch):
    import asyncio

    seen: list = []
    _pause_conn(monkeypatch, "paused", seen)
    out = asyncio.run(dt.set_paused("t", "abc", False))
    assert out["status"] == "active" and out["changed"] is True


def test_a_layout_retired_by_qa_cannot_be_switched_back_on_from_the_screen(monkeypatch):
    """Un-pausing a retired layout would quietly overturn a verdict the renderer
    reached on evidence. That is not a decision a toggle should make."""
    import asyncio

    seen: list = []
    _pause_conn(monkeypatch, "retired", seen)
    out = asyncio.run(dt.set_paused("t", "abc", False))
    assert out["changed"] is False
    assert out["status"] == "retired"
    assert seen == [], "a retired layout must not be written to at all"


def test_pausing_a_layout_that_does_not_exist_reports_nothing(monkeypatch):
    import asyncio

    _pause_conn(monkeypatch, None, [])
    assert asyncio.run(dt.set_paused("t", "nope", True)) is None


# ------------------------------------- stocking a thin brand from the catalogue
#
# STOCK_BELOW exists because MIN_LIBRARY was being asked two different questions.
# Measured against production 2026-10-01: the thinnest brand held 4 drawable
# layouts and MIN_LIBRARY was 3, so NO brand was ever short enough for the
# adopt-on-pick path to fire — the catalogue was wired up and inert. Raising
# MIN_LIBRARY instead would have been actively worse, and the test named
# ...keeps_its_own_layouts_when... below is the one that pins why.


def _stocking_picker(rows, monkeypatch, *, adopted: int, after=None):
    """The picker over `rows`, with a house catalogue that adopts `adopted` rows.

    `after` is what the SECOND _pick_once call sees, so a test can model a top-up
    that genuinely added layouts as well as one that added none.
    """
    import contextlib

    seen = {"adopt_calls": 0, "fetches": 0}
    state = {"rows": rows}

    class _C:
        async def fetch(self, sql, *args):
            seen["fetches"] += 1
            return state["rows"]

    @contextlib.asynccontextmanager
    async def _acq(tenant_id=None):
        yield _C()

    async def _adopt(tenant_id, **kw):
        seen["adopt_calls"] += 1
        if adopted and after is not None:
            state["rows"] = after
        return {"adopted": adopted}

    monkeypatch.setattr(dt, "acquire", _acq)
    from james_os import house_layouts as hl
    monkeypatch.setattr(hl, "adopt", _adopt)
    return seen


def test_a_well_stocked_brand_is_never_asked_to_adopt(monkeypatch):
    """The common case must cost exactly one query. A brand at or above
    STOCK_BELOW is not short, so the catalogue is not consulted at all."""
    import asyncio

    seen = _stocking_picker(_lane_rows(n_other=dt.STOCK_BELOW, n_niche=0),
                            monkeypatch, adopted=0)
    got = asyncio.run(dt.pick("t"))
    assert got is not None
    assert seen["adopt_calls"] == 0, "a stocked brand must not touch the catalogue"
    assert seen["fetches"] == 1, "and must not pay for a second pick"


def test_a_thin_brand_tops_itself_up_even_though_it_could_already_draw(monkeypatch):
    """THE behaviour change. 4 drawable layouts clears MIN_LIBRARY, so the old
    code — which only stocked a brand when the pick came back empty — left this
    brand thin forever. It is below STOCK_BELOW, so it now stocks itself."""
    import asyncio

    thin = _lane_rows(n_other=4, n_niche=0)
    assert len(thin) >= dt.MIN_LIBRARY, "fixture must be able to draw already"
    assert len(thin) < dt.STOCK_BELOW, "fixture must be short"
    seen = _stocking_picker(thin, monkeypatch, adopted=8,
                            after=_lane_rows(n_other=12, n_niche=0))
    assert asyncio.run(dt.pick("t")) is not None
    assert seen["adopt_calls"] == 1


def test_a_thin_brand_keeps_its_own_layouts_when_the_catalogue_adds_nothing(monkeypatch):
    """THE REGRESSION GUARD, and the reason MIN_LIBRARY was not simply raised.

    A brand under the stock line whose top-up finds an empty or already-drained
    catalogue must still draw from what it has. Raising MIN_LIBRARY to 12 would
    have made exactly this brand — 4 of its own layouts, a 4-layout pool — stop
    using learned layouts altogether and fall back to the nine formats. Stocking
    may only ever improve the answer.
    """
    import asyncio

    seen = _stocking_picker(_lane_rows(n_other=4, n_niche=0), monkeypatch, adopted=0)
    got = asyncio.run(dt.pick("t"))
    assert seen["adopt_calls"] == 1, "it should have tried"
    assert got is not None, "an empty catalogue must not cost the brand its own layouts"
    assert got["source_kind"] == "competitor"


def test_a_brand_with_nothing_drawable_still_falls_back_to_the_nine_formats(monkeypatch):
    """Below MIN_LIBRARY with no help available, None is the right answer — the
    nine hand-built formats are better than rotating two lucky reads."""
    import asyncio

    seen = _stocking_picker(_lane_rows(n_other=1, n_niche=0), monkeypatch, adopted=0)
    assert asyncio.run(dt.pick("t")) is None
    assert seen["adopt_calls"] == 1


def test_shortness_is_counted_across_both_lanes_not_within_one(monkeypatch):
    """`good` is narrowed to a single lane before the pick is returned, so taking
    the count at the end would report one lane's size. A brand holding
    STOCK_BELOW layouts split across the two lanes is NOT short."""
    import asyncio

    half = dt.STOCK_BELOW // 2
    seen = _stocking_picker(_lane_rows(n_other=half, n_niche=dt.STOCK_BELOW - half),
                            monkeypatch, adopted=0)
    assert asyncio.run(dt.pick("t")) is not None
    assert seen["adopt_calls"] == 0, "both lanes together clear the stock line"


def test_a_catalogue_that_raises_never_breaks_a_pick(monkeypatch):
    """Stocking is a nicety; producing a post is not. A catalogue that throws
    must leave the brand's own pick untouched."""
    import asyncio

    import contextlib

    class _C:
        async def fetch(self, sql, *args):
            return _lane_rows(n_other=4, n_niche=0)

    @contextlib.asynccontextmanager
    async def _acq(tenant_id=None):
        yield _C()

    async def _boom(tenant_id, **kw):
        raise RuntimeError("catalogue unreachable")

    monkeypatch.setattr(dt, "acquire", _acq)
    from james_os import house_layouts as hl
    monkeypatch.setattr(hl, "adopt", _boom)
    assert asyncio.run(dt.pick("t")) is not None


def test_the_stock_line_sits_above_the_draw_floor(monkeypatch):
    """They answer different questions, and the ordering is what makes stocking
    able to fire at all. If they were equal, a brand would only ever stock itself
    at the moment it could no longer draw — the inert state this replaced."""
    assert dt.STOCK_BELOW > dt.MIN_LIBRARY
