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


# ------------------------- the shared catalogue as a SOURCE, not a top-up
#
# Roy, 2026-10-07: "I do not want the user to pick templates. We are building an
# intelligent system, so it should adopt and pick it by itself."
#
# The old contract was "top the brand up when its library falls under
# STOCK_BELOW". That asked whether the shelf was LOW, never whether a layout
# FITS, so a brand with 242 layouts could not see a new template however well it
# matched — and in production none of the five brands was ever under the line, so
# 29 curated layouts reached nobody for a week. The threshold is gone. These
# tests pin what replaced it.


def _spec_for(roles, treatment="full_bleed_photo"):
    return {"kind": "graphic_card", "background": {"treatment": treatment},
            "elements": [{"role": r, "box": {"x": .05, "y": .6, "w": .9, "h": .2},
                          "size": "xl", "align": "left", "text": "x"} for r in roles]}


def _house_row(i, *, tags=None, ltype="offer_card", roles=("cta", "headline", "subhead")):
    import json as _json
    return {"id": f"h{i}", "kind": "graphic_card", "spec": _json.dumps(_spec_for(roles)),
            "fingerprint": f"hf{i}", "source_kind": "curated", "source_url": "",
            "source_image_uri": "", "niches": list(tags or []), "layout_type": ltype,
            "score": 0, "adopted_count": 0}


def _wire(monkeypatch, own_rows, house_rows, *, brand_niche="golf resort"):
    """Route each query by its SQL so the picker, the niche read, the
    already-taken read and the catalogue read each get the right shape."""
    import contextlib

    seen = {"adopted": [], "catalogue_reads": 0}

    class _C:
        async def fetch(self, sql, *a):
            if "FROM competitors" in sql:
                return [{"niche": brand_niche}] if brand_niche else []
            if "house_layout_id::text" in sql:
                return []                      # this brand has taken none yet
            if "FROM house_layouts" in sql:
                seen["catalogue_reads"] += 1
                return house_rows
            return own_rows
        async def fetchval(self, sql, *a):
            if "INSERT INTO design_templates" in sql:
                seen["adopted"].append(a[-1])  # house_layout_id is the last arg
                return "new-row"
            return None
        async def execute(self, sql, *a):
            return None

    @contextlib.asynccontextmanager
    async def _acq(tenant_id=None):
        yield _C()

    monkeypatch.setattr(dt, "acquire", _acq)
    from james_os import house_layouts as hl
    monkeypatch.setattr(hl, "acquire", _acq)
    return seen


def test_a_well_stocked_brand_DOES_consult_the_catalogue(monkeypatch):
    """The inversion. The previous contract asserted the opposite — that a brand
    at or above the line never looks — and that is precisely the behaviour that
    left 29 curated layouts unreachable by every brand."""
    import asyncio

    seen = _wire(monkeypatch, _lane_rows(n_other=50, n_niche=0), [_house_row(1, tags=["golf"])])
    got = asyncio.run(dt.pick("t"))
    assert seen["catalogue_reads"] >= 1, "a stocked brand must still see new templates"
    assert got is not None


def test_a_day_one_brand_draws_entirely_from_the_catalogue(monkeypatch):
    """Nothing of its own, so no competing lane and no share to govern. This is
    what 'a new brand should get good templates immediately' has to mean."""
    import asyncio

    seen = _wire(monkeypatch, [], [_house_row(i, tags=["golf"]) for i in range(5)])
    got = asyncio.run(dt.pick("t"))
    assert got is not None, "a brand with no library of its own must still draw"
    assert got["source_platform"] == "house"
    assert seen["adopted"], "and it must take ownership of what it drew"


def test_the_pick_is_decided_by_FIT_not_by_order(monkeypatch):
    """A golf brand whose own library is all offers. Offered a testimonial tagged
    for knitting first and a golf-tagged offer last, it must take the offer."""
    import asyncio

    own = _lane_rows(n_other=4, n_niche=0)
    for r in own:                      # its evidence: it makes offers
        r["spec"] = __import__("json").dumps(_spec_for(["cta", "headline", "subhead"]))
    house = [_house_row(1, tags=["knitting"], ltype="testimonial", roles=("byline", "headline")),
             _house_row(2, tags=[], ltype="testimonial", roles=("byline", "headline")),
             _house_row(3, tags=["golf"], ltype="offer_card")]
    _wire(monkeypatch, own, house)
    got = asyncio.run(dt.pick("t"))
    assert got["id"] == "h3", f"fit must beat position, got {got['id']}"


def test_an_off_niche_layout_loses_to_an_untagged_one(monkeypatch):
    import asyncio

    # Three candidates, because below MIN_LIBRARY across BOTH sources the right
    # answer is the nine formats, not a ranking question.
    house = [_house_row(1, tags=["political candidates"]),
             _house_row(2, tags=[]),
             _house_row(3, tags=["knitting"])]
    _wire(monkeypatch, [], house)
    assert asyncio.run(dt.pick("t"))["id"] == "h2", "untagged beats tagged-for-someone-else"


def test_the_catalogue_cannot_take_over_a_brand_that_has_its_own(monkeypatch):
    """HOUSE_SHARE bounds it once there IS a library to compete with. Without
    this, catalogue rows have no last_used_at and the picker's NULLS-FIRST
    rotation would hand them every slot until they were all spent."""
    import asyncio

    own = _lane_rows(n_other=6, n_niche=0, used_other=10)
    for g in own:                      # most use has already come FROM the house
        g["house_layout_id"] = "x"
    _wire(monkeypatch, own, [_house_row(1, tags=["golf"])])
    got = asyncio.run(dt.pick("t"))
    assert got["source_platform"] != "house", "over its share, the brand's own lane draws"


def test_what_it_draws_it_owns(monkeypatch):
    """Copy-on-pick. The counters the picker sorts on are per-brand and a shared
    row cannot hold them, so the winner is forked at the moment it is used — not
    in a speculative batch of eight."""
    import asyncio

    seen = _wire(monkeypatch, [], [_house_row(7, tags=["golf"]),
                                   _house_row(8, tags=[]), _house_row(9, tags=[])])
    got = asyncio.run(dt.pick("t"))
    assert got["id"] == "h7", "the best-fitting one is the one drawn"
    assert seen["adopted"] == ["h7"], "and ONLY that row is copied in — not a batch"


def test_a_catalogue_that_raises_never_costs_a_brand_its_pick(monkeypatch):
    """Drawing a post matters; reaching the catalogue is a nicety."""
    import asyncio
    import contextlib

    own = _lane_rows(n_other=5, n_niche=0)

    class _C:
        async def fetch(self, sql, *a):
            if "FROM house_layouts" in sql or "FROM competitors" in sql:
                raise RuntimeError("catalogue unreachable")
            if "house_layout_id::text" in sql:
                return []
            return own
        async def fetchval(self, sql, *a):
            return None
        async def execute(self, sql, *a):
            return None

    @contextlib.asynccontextmanager
    async def _acq(tenant_id=None):
        yield _C()

    monkeypatch.setattr(dt, "acquire", _acq)
    from james_os import house_layouts as hl
    monkeypatch.setattr(hl, "acquire", _acq)
    assert asyncio.run(dt.pick("t")) is not None


def test_nothing_anywhere_still_means_the_nine_formats(monkeypatch):
    """Below MIN_LIBRARY across BOTH sources, None is the honest answer — two
    learned layouts on rotation look more repetitive than the nine, not less."""
    import asyncio

    _wire(monkeypatch, _lane_rows(n_other=1, n_niche=0), [])
    assert asyncio.run(dt.pick("t")) is None


def test_the_floor_counts_the_catalogue_too(monkeypatch):
    """One of its own plus three in the catalogue clears MIN_LIBRARY=3. Counting
    only the brand's own rows would tell a day-one brand it has nothing."""
    import asyncio

    _wire(monkeypatch, _lane_rows(n_other=1, n_niche=0),
          [_house_row(i, tags=["golf"]) for i in range(3)])
    assert asyncio.run(dt.pick("t")) is not None
