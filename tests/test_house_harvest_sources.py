"""The harvest from more than one source (SPEC2): source_key prefixes, the
WEB/TEMPLATE media vocabulary, store_image and spec_hint.

What these pin, because each is easy to get silently wrong:
  * exactly onc/igh/ggl/bnb are accepted; the reserved prefixes (pin, fba, cva)
    and every other spelling are refused BEFORE any database or vision call;
  * harvest_meta.source is the prefix of the key, whatever meta claims;
  * store_image=false writes the row but keeps no copy of the picture;
  * spec_hint is stored and NEVER trusted: every gate runs on the vision read;
    it is sized like BM2 sizes it (compact UTF-8), and an oversize one is
    dropped (spec_hint_dropped='oversize'), never a reason to refuse the image;
  * the route passes both options through with store_image defaulting to true.
No database: queries are answered by the recorder in test_house_harvest.
"""

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from james_os import api_v1
from james_os import house_harvest as hh
from james_os import house_layouts as hl
from tests.test_house_harvest import GOOD, HOLE, ID, PNG, _Acquire, _Conn

pytestmark = pytest.mark.nodb


@pytest.fixture
def rig(monkeypatch):
    """The recorder of test_house_harvest: SQL answered by _Conn, the vision read
    and the media store stubbed and counted."""
    def make(spec=GOOD, **conn_kw):
        conn = _Conn(**conn_kw)
        acq = _Acquire(conn)
        monkeypatch.setattr(hh, "acquire", acq)
        reads, saved = [], []

        async def extract(image, *, mime="image/jpeg"):
            reads.append(mime)
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

HEX = "c" * 40
# Bannerbear-style layer geometry: what a `bnb` upload may carry as its hint.
OBJECTS = [{"name": "headline", "type": "text", "x": 60, "y": 80, "width": 960,
            "height": 200, "font_family": "Inter"},
           {"name": "photo", "type": "image", "x": 0, "y": 400, "width": 1080,
            "height": 680}]


def test_the_module_under_test_is_this_checkout():
    assert Path(hh.__file__).resolve().is_relative_to(Path(__file__).resolve().parents[1])


def _go(**kw):
    args = dict(source_key="onc:" + HEX, niches=["golf resort", "golf"], run_id="run-1",
                by="harvest:nightly", approve=True)
    args.update(kw)
    image = args.pop("image", PNG)
    return asyncio.run(hh.harvest_ingest(image, **args))


def _insert(r):
    return next(c for c in r.conn.calls if "INSERT INTO house_layouts" in c[1])


def _stored_meta(r) -> dict:
    return json.loads(_insert(r)[2][-1])


# ── source_key prefixes ───────────────────────────────────────────────────


def test_the_prefixes_are_one_shared_exported_constant():
    assert hh.SOURCE_PREFIXES == ("onc", "igh", "ggl", "bnb")
    assert "SOURCE_PREFIXES" in hh.__all__
    for reserved in ("pin", "fba", "cva"):
        assert reserved not in hh.SOURCE_PREFIXES, "reserved, not accepted yet"
        assert reserved in Path(hh.__file__).read_text(), "named as reserved in the source"
    for p in hh.SOURCE_PREFIXES:
        assert hh.SOURCE_KEY_RE.match(f"{p}:{HEX}")


@pytest.mark.parametrize("prefix", ["onc", "igh", "ggl", "bnb"])
def test_every_source_is_accepted_and_recorded_as_harvest_meta_source(rig, prefix):
    r = rig()
    out = _go(source_key=f"{prefix}:{HEX}")
    assert out["verdict"] == "approved", out
    assert r.reads, "the vision read ran"
    _, _, args = _insert(r)
    assert args[12] == f"{prefix}:{HEX}", "the full key is the row's source_key"
    assert _stored_meta(r)["source"] == prefix


@pytest.mark.parametrize("key", [
    "pin:" + HEX, "fba:" + HEX, "cva:" + HEX,          # reserved, not built
    "apf:" + HEX, "cnv:" + HEX, "fbad:" + HEX,          # other maps' spellings
    "ONC:" + HEX, "igh:" + HEX.upper(), "ggl:" + HEX[:-1], "bnb:" + HEX + "0",
    "bb:" + "uid123", " igh:" + HEX, "igh" + HEX, "",
])
def test_any_other_key_is_a_400_before_anything_runs(rig, key):
    r = rig()
    with pytest.raises(hh.HarvestInputError) as exc:
        _go(source_key=key)
    assert exc.value.status == 400
    assert "'bnb:'" in str(exc.value), "the message names what IS accepted"
    assert r.reads == [] and r.acquire.tenants == []


def test_the_source_is_derived_from_the_key_not_taken_from_meta(rig):
    r = rig()
    _go(source_key="ggl:" + HEX, meta={"source": "onc", "author": "example.com"})
    meta = _stored_meta(r)
    assert meta["source"] == "ggl" and meta["author"] == "example.com"


def test_a_duplicate_key_from_a_new_source_still_costs_no_vision(rig):
    r = rig(known={"id": ID, "status": "approved", "niches": ["golf"]})
    out = _go(source_key="igh:" + HEX)
    assert (out["verdict"], out["reason"], out["vision_called"]) == \
        ("duplicate", "source_key", False)
    assert r.reads == []


# ── source_media vocabulary ───────────────────────────────────────────────


@pytest.mark.parametrize("given,stored", [
    ("WEB", "WEB"), ("template", "TEMPLATE"), (" Web ", "WEB"),
    ("INSTAGRAM", "INSTAGRAM"), ("PINTEREST", "OTHER"), ("", "OTHER"),
])
def test_media_gains_web_and_template(rig, given, stored):
    r = rig()
    _go(source_media=given)
    assert _stored_meta(r)["source_media"] == stored
    assert {"WEB", "TEMPLATE"} <= set(hh.MEDIA)


# ── store_image ───────────────────────────────────────────────────────────


def test_store_image_defaults_to_true(rig):
    r = rig()
    out = _go()
    assert r.saved and out["stored_image_uri"].startswith("/media/house/")
    assert _stored_meta(r)["image_stored"] is True


@pytest.mark.parametrize("counts,verdict", [({}, "approved"), ({"family_in_niche": 2}, "held")])
def test_store_image_false_writes_the_row_but_no_picture(rig, counts, verdict):
    r = rig(counts=counts)
    out = _go(source_key="ggl:" + HEX, source_media="WEB", store_image=False,
              source_url="https://example.com/blog/post")
    assert out["verdict"] == verdict
    assert out["house_layout_id"] == ID, "the row IS written"
    assert out["stored_image_uri"] == ""
    assert r.saved == [], "no copy of a third party's web image is kept"
    assert not any("source_image_uri" in s for s in r.conn.sqls())
    _, _, args = _insert(r)
    assert args[4] == "https://example.com/blog/post", "the source link is kept"
    meta = _stored_meta(r)
    assert meta["image_stored"] is False and meta["sha256"]
    # source_key read, then the one lock+counts+insert block — no third for storage
    assert r.acquire.tenants == [None, None]


def test_store_image_false_on_a_dry_run_still_writes_nothing(rig):
    r = rig()
    out = _go(store_image=False, spec_hint=OBJECTS, dry_run=True)
    assert out["verdict"] == "approved" and out["dry_run"] is True
    assert not any("INSERT" in s or "UPDATE" in s for s in r.conn.sqls())
    assert r.saved == []


# ── spec_hint ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize("hint", [OBJECTS, {"objects": OBJECTS, "width": 1080}])
def test_spec_hint_is_stored_in_harvest_meta(rig, hint):
    r = rig()
    out = _go(source_key="bnb:" + HEX, source_media="TEMPLATE", spec_hint=hint,
              meta={"author": "bannerbear:owner", "rubric_version": "2026-10-07"})
    assert out["verdict"] == "approved"
    meta = _stored_meta(r)
    assert meta["spec_hint"] == hint
    assert meta["source"] == "bnb" and meta["source_media"] == "TEMPLATE"
    assert meta["rubric_version"] == "2026-10-07", "provenance from meta is kept"


def test_spec_hint_is_never_trusted_for_a_gate(rig):
    """A hint describing a perfectly good card does not rescue an image whose
    vision read says it is not drawable — and does not replace the stored spec."""
    r = rig(spec=HOLE)
    out = _go(source_key="bnb:" + HEX, spec_hint=OBJECTS)
    assert (out["verdict"], out["reason"]) == ("rejected", "not_drawable")
    assert r.reads, "the vision read still runs"
    assert r.conn.writes() == []

    r = rig(spec=GOOD)
    _go(source_key="bnb:" + HEX, spec_hint={"status": "ok", "elements": [], "kind": "x"})
    stored_spec = json.loads(_insert(r)[2][1])
    assert stored_spec == GOOD, "the row's spec is the vision read, never the hint"


def test_no_hint_means_no_spec_hint_key_even_if_meta_smuggles_one(rig):
    r = rig()
    _go(meta={"spec_hint": {"elements": "x"}})
    assert "spec_hint" not in _stored_meta(r)


@pytest.mark.parametrize("hint", ["objects", 3, True])
def test_a_bad_spec_hint_is_a_400_before_anything_runs(rig, hint):
    r = rig()
    with pytest.raises(hh.HarvestInputError) as exc:
        _go(spec_hint=hint)
    assert exc.value.status == 400
    assert r.reads == [] and r.acquire.tenants == []


def test_spec_hint_has_its_own_budget_apart_from_meta(rig):
    r = rig()
    big = [{"pad": "x" * 12_000}]        # > meta's 4 KB, < the hint's 16 KB
    assert _go(spec_hint=big)["verdict"] == "approved"
    assert _stored_meta(r)["spec_hint"] == big


# ── spec_hint is sized like BM2 sizes it (finding: BM1 re-measured with
# json.dumps defaults — ", " / ": " and \uXXXX — so a hint BM2 had trimmed to
# fit read ~9-15% larger here, was a 400, and BM2 then closed the image as the
# FINAL verdict rejected_input). ─────────────────────────────────────────────

def _compact(obj) -> bytes:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode()


def _bm2_shaped_hint(n_layers: int = 80) -> dict:
    """What BM2's harvest_sources.spec_hint() sends for a big Bannerbear
    template: layers dropped from the end until the COMPACT UTF-8 blob fits 16 KB.
    The text carries an em dash and curly quotes, as real copy does."""
    objs = [{"id": f"layer-{i:03d}", "name": f"headline_{i}", "type": "text",
             "left": 40 + i, "top": 60 + 3 * i, "width": 1000, "height": 120,
             "font-family": "Playfair Display", "font-size": 48, "color": "#1A2B3C",
             "text-align": "center",
             "text": f"Tee off at dawn \u2014 the course\u2019s \u201cfront nine\u201d {i}"}
            for i in range(n_layers)]
    slim = list(objs)
    while slim:
        hint = {"objects": slim, "layers": n_layers}
        if len(_compact(hint)) <= hh.MAX_SPEC_HINT_BYTES:
            return hint
        slim = slim[:-1]
    raise AssertionError("unreachable")


def test_a_hint_bm2_trimmed_to_fit_is_kept_whole(rig):
    hint = _bm2_shaped_hint()
    assert len(hint["objects"]) < 80, "precondition: BM2 had to trim it"
    assert 15_000 < len(_compact(hint)) <= hh.MAX_SPEC_HINT_BYTES
    assert len(json.dumps(hint).encode()) > hh.MAX_SPEC_HINT_BYTES, \
        "precondition: json.dumps defaults would have called it oversize"
    assert hh.spec_hint_size(hint) == len(_compact(hint))
    r = rig()
    out = _go(source_key="bnb:" + HEX, source_media="TEMPLATE", spec_hint=hint)
    assert out["verdict"] == "approved", out
    meta = _stored_meta(r)
    assert meta["spec_hint"] == hint, "stored whole, non-ASCII intact"
    assert "spec_hint_dropped" not in meta


def test_a_16000_byte_compact_non_ascii_hint_is_accepted(rig):
    pad = "\u00e9" * 7_990                 # é: 2 bytes each in UTF-8, 6 as \u00e9
    hint = [{"t": pad}]
    assert 15_900 < len(_compact(hint)) <= 16_000
    r = rig()
    assert _go(spec_hint=hint)["verdict"] == "approved"
    assert _stored_meta(r)["spec_hint"] == hint


def test_the_limit_is_exactly_16384_compact_bytes(rig):
    base = len(_compact([{"p": ""}]))
    at = [{"p": "x" * (hh.MAX_SPEC_HINT_BYTES - base)}]
    over = [{"p": "x" * (hh.MAX_SPEC_HINT_BYTES - base + 1)}]
    assert hh.spec_hint_size(at) == hh.MAX_SPEC_HINT_BYTES
    r = rig()
    _go(spec_hint=at)
    assert _stored_meta(r)["spec_hint"] == at
    r = rig()
    _go(spec_hint=over)
    meta = _stored_meta(r)
    assert "spec_hint" not in meta and meta["spec_hint_dropped"] == "oversize"


def test_an_oversize_hint_is_dropped_never_a_reason_to_refuse_the_image(rig, caplog):
    """The hint is never trusted, so losing it costs nothing; a 400 would cost
    the image for good (BM2: 400 -> final rejected_input)."""
    r = rig()
    big = {"objects": [{"pad": "x" * (hh.MAX_SPEC_HINT_BYTES + 1)}]}
    with caplog.at_level("WARNING", logger=hh.logger.name):
        out = _go(source_key="bnb:" + HEX, source_media="TEMPLATE", spec_hint=big)
    assert out["verdict"] == "approved", out
    assert r.reads, "the vision read still runs"
    meta = _stored_meta(r)
    assert "spec_hint" not in meta
    assert meta["spec_hint_dropped"] == "oversize"
    assert any("spec_hint dropped" in m for m in caplog.messages)


def test_spec_hint_dropped_is_derived_never_taken_from_meta(rig):
    r = rig()
    _go(meta={"spec_hint_dropped": "oversize"}, spec_hint=OBJECTS)
    meta = _stored_meta(r)
    assert meta["spec_hint"] == OBJECTS and "spec_hint_dropped" not in meta


# ── the catalogue listing stays lean ──────────────────────────────────────


def test_the_catalogue_projection_leaves_the_hint_out():
    cols = hl._CATALOGUE_COLS.split("source_key,", 1)[1]
    assert "harvest_meta - 'spec_hint'" in cols and "AS harvest_meta" in cols


# ── the route ─────────────────────────────────────────────────────────────

FORM = {"source_key": "ggl:" + HEX, "niches": "golf resort,golf", "run_id": "run-7",
        "by": "harvest:nightly"}


def _app() -> FastAPI:
    app = FastAPI()
    app.include_router(api_v1.router)
    app.dependency_overrides[api_v1.require_curator] = lambda: True
    return app


async def _post(data):
    async with AsyncClient(transport=ASGITransport(app=_app()), base_url="http://t") as c:
        return await c.post("/v1/house-layouts/harvest", data=data,
                            files={"file": ("a.png", PNG, "image/png")})


@pytest.fixture
def ingest_spy(monkeypatch):
    seen = []

    async def fake(image, **kw):
        seen.append(kw)
        return {"verdict": "held", "reason": "approve_off"}
    monkeypatch.setattr(hh, "harvest_ingest", fake)
    return seen


@pytest.mark.parametrize("raw,want", [(None, True), ("", True), ("  ", True), ("true", True),
                                      ("false", False), ("0", False), ("off", False)])
async def test_store_image_form_field_defaults_true(ingest_spy, raw, want):
    data = dict(FORM) if raw is None else {**FORM, "store_image": raw}
    r = await _post(data)
    assert r.status_code == 200, r.text
    assert ingest_spy[-1]["store_image"] is want
    assert ingest_spy[-1]["spec_hint"] is None


async def test_spec_hint_form_field_is_parsed_and_passed(ingest_spy):
    r = await _post({**FORM, "source_key": "bnb:" + HEX, "source_media": "TEMPLATE",
                     "spec_hint": json.dumps(OBJECTS)})
    assert r.status_code == 200, r.text
    assert ingest_spy[-1]["spec_hint"] == OBJECTS
    assert ingest_spy[-1]["source_media"] == "TEMPLATE"


@pytest.mark.parametrize("over", [{"store_image": "maybe"}, {"spec_hint": "{nope"},
                                  {"spec_hint": "42"}, {"spec_hint": '"objects"'}])
async def test_malformed_new_fields_are_400(ingest_spy, over):
    r = await _post({**FORM, **over})
    assert r.status_code == 400, r.text
    assert ingest_spy == []


async def test_through_the_real_engine_a_web_image_is_written_without_a_copy(rig):
    r = rig()
    resp = await _post({**FORM, "source_media": "WEB", "store_image": "false",
                        "approve": "true", "source_url": "https://example.com/p/1",
                        "meta": json.dumps({"author": "example.com"})})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["verdict"] == "approved" and body["stored_image_uri"] == ""
    assert r.saved == []
    meta = _stored_meta(r)
    assert (meta["source"], meta["source_media"], meta["image_stored"]) == ("ggl", "WEB", False)


async def test_through_the_route_bm2s_compact_hint_is_accepted_and_stored(rig):
    """The form string exactly as BM2 sends it (compact, ensure_ascii=False)."""
    hint = _bm2_shaped_hint()
    raw = _compact(hint).decode()
    assert len(raw.encode()) <= hh.MAX_SPEC_HINT_BYTES
    r = rig()
    resp = await _post({**FORM, "source_key": "bnb:" + HEX, "source_media": "TEMPLATE",
                        "approve": "true", "spec_hint": raw})
    assert resp.status_code == 200, resp.text
    assert resp.json()["verdict"] == "approved"
    assert _stored_meta(r)["spec_hint"] == hint


async def test_through_the_route_an_oversize_hint_is_dropped_not_a_400(rig):
    r = rig()
    raw = json.dumps([{"pad": "x" * (hh.MAX_SPEC_HINT_BYTES + 1)}])
    resp = await _post({**FORM, "approve": "true", "spec_hint": raw})
    assert resp.status_code == 200, resp.text
    assert resp.json()["verdict"] == "approved"
    meta = _stored_meta(r)
    assert "spec_hint" not in meta and meta["spec_hint_dropped"] == "oversize"


@pytest.mark.parametrize("over", [
    {"source_key": "pin:" + HEX},
    {"spec_hint": "[1,"},
])
async def test_refused_by_the_real_engine_before_any_db_or_vision(monkeypatch, over):
    async def no_vision(*a, **k):
        raise AssertionError("vision must not be called")

    def no_db(*a, **k):
        raise AssertionError("no database for a refused request")
    import james_os.design_cloner as dc
    monkeypatch.setattr(dc, "extract_template_spec", no_vision)
    monkeypatch.setattr(hh, "acquire", no_db)
    r = await _post({**FORM, **over})
    assert r.status_code == 400, r.text
