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

    gate_src = inspect.getsource(api_v1._run_regenerate)
    gate = gate_src[gate_src.index("rebuild_in_place = ("):gate_src.index("if rebuild_in_place:")]
    assert '"learned"' in gate and 'layout != "new"' in gate

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

    async def _rebuild(payload, feedback, tenant_id):
        return b"png", "graphic_card"

    monkeypatch.setattr(api_v1, "acquire", _acquire)
    monkeypatch.setattr(template_clone, "rebuild_cloned", _rebuild)
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
