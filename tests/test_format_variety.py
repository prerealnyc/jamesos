"""Format variety (SPEC3 B1-B4): what a post becomes when the learned layout it
asked for misses, what a brand with nothing enabled gets, what a photo layout
with no photo becomes, and the art director's brand-neutral brief.

No database, no model, no network: every collaborator of the designed path is
replaced, and the art director itself is the probe — it records what it was
asked to draw and stops the render there.
"""

from io import BytesIO
from types import SimpleNamespace

import pytest
from PIL import Image

from james_os import api_v1, autopilot_bulk, brand_identity, format_fallback, imagegen
from james_os import main as m
from james_os.designed_render import PHOTO_FORMATS, _photoless_ground, render_designed

pytestmark = pytest.mark.nodb

# The 8-key set most live brands actually carry (minus the
# "learned" slot, which get_enabled_formats strips).
PHOTO_BRAND = {"carousel", "editorial_split", "framed_print", "full_bleed", "hero_quote",
               "minimal_over", "statement"}
# James (Tenant Zero): text-only by his own setting.
JAMES = {"big_stat", "bold_statement", "brand_quote", "text_carousel"}


class _Stop(Exception):
    """Raised by the fake art director: the decision under test is already made."""


@pytest.fixture
def rig(monkeypatch):
    """The designed path with every collaborator faked. Returns a namespace the
    test configures (allowed set, learned outcome, miss reason) and reads back
    (what the art director was asked, what was stamped, how often learned ran)."""
    r = SimpleNamespace(allowed=None, learned=None, learned_raises=False,
                        reason="thin_library", director=None, stamps=[], learned_calls=0,
                        served=[], pinned=False, learned_kw=[])

    async def _learned(*a, **k):
        r.learned_calls += 1
        r.learned_kw.append(k)
        if r.learned_raises:
            raise RuntimeError("boom")
        if isinstance(r.learned, list):
            return r.learned.pop(0)
        return r.learned

    async def _served(_aid, _tid):
        # the layout each successive learned render drew (default: none known)
        return (r.served.pop(0) if r.served else ""), r.pinned

    async def _allowed(_tid=None):
        return None if r.allowed is None else set(r.allowed)

    async def _intel(_tid=None):
        return None

    async def _voice(_tid):
        return "", ""

    async def _director(*a, **k):
        r.director = k
        raise _Stop

    async def _reason(_tid, _since):
        return r.reason

    async def _stamp(_aid, _tid, data):
        r.stamps.append(data)

    monkeypatch.setattr(m, "_generate_learned_post_image", _learned)
    monkeypatch.setattr(m, "_brand_voice_and_profile", _voice)
    monkeypatch.setattr(brand_identity, "get_enabled_formats", _allowed)
    monkeypatch.setattr(brand_identity, "get_design_intel_enabled", _intel)
    monkeypatch.setattr(imagegen, "direct_designed_image", _director)
    monkeypatch.setattr(format_fallback, "miss_reason", _reason)
    monkeypatch.setattr(format_fallback, "stamp", _stamp)
    monkeypatch.setattr(format_fallback, "served_layout", _served)
    return r


async def _run(**kw):
    """Drive the designed path; returns its result, or None when the fake art
    director stopped it (the usual case — the decision is what's under test)."""
    try:
        return await m._generate_designed_post_image("act-1", "topic", "draft", "tid", **kw)
    except _Stop:
        return None


# ── format_fallback: what may stand in for a missed learned layout ─────────

def test_a_fallback_must_be_drawable_and_allowed():
    assert format_fallback.usable_fallback("full_bleed", PHOTO_BRAND) == "full_bleed"
    assert format_fallback.usable_fallback("FULL_BLEED", None) == "full_bleed"
    # a format the brand turned off is no better than the free pick it replaces
    assert format_fallback.usable_fallback("bold_statement", PHOTO_BRAND) == ""
    # "learned" is the slot that just missed; junk is junk
    assert format_fallback.usable_fallback("learned", None) == ""
    assert format_fallback.usable_fallback("nonsense", None) == ""
    assert format_fallback.usable_fallback("", None) == ""


def test_an_empty_allowed_set_means_photo_layouts():
    assert format_fallback.effective_allowed(set()) == set(PHOTO_FORMATS)
    assert format_fallback.effective_allowed(None) is None
    assert format_fallback.effective_allowed(JAMES) == JAMES
    assert format_fallback.usable_fallback("hero_quote", set()) == "hero_quote"
    assert format_fallback.usable_fallback("brand_quote", set()) == ""


async def test_fallback_for_reads_the_brands_set(monkeypatch):
    async def _allowed(_tid=None):
        return set()
    monkeypatch.setattr(brand_identity, "get_enabled_formats", _allowed)
    # nothing enabled and nothing usable named → full_bleed, never a free pick
    assert await format_fallback.fallback_for("t", "") == "full_bleed"
    assert await format_fallback.fallback_for("t", "brand_quote") == "full_bleed"
    assert await format_fallback.fallback_for("t", "minimal_over") == "minimal_over"


async def test_fallback_for_fails_open_to_the_format_check(monkeypatch):
    async def _boom(_tid=None):
        raise RuntimeError("db down")
    monkeypatch.setattr(brand_identity, "get_enabled_formats", _boom)
    assert await format_fallback.fallback_for("t", "editorial_split") == "editorial_split"
    assert await format_fallback.fallback_for("t", "") == ""


async def test_miss_reason_without_design_qa_is_a_thin_library(monkeypatch):
    from james_os.config import settings
    monkeypatch.setattr(settings, "design_qa_enabled", False)
    assert await format_fallback.miss_reason("t", format_fallback.now()) == "thin_library"


# ── B1: a learned miss keeps BM2's rotation pick ────────────────────────

async def test_a_thin_library_miss_draws_bm2s_pick(rig):
    rig.allowed = PHOTO_BRAND
    await _run(force_format="learned", fallback_format="editorial_split")
    assert rig.learned_calls == 1
    assert rig.director["force_format"] == "editorial_split"
    assert rig.stamps == [{"requested_format": "learned", "fallback_reason": "thin_library",
                           "fallback_format": "editorial_split"}]


async def test_a_qa_miss_is_recorded_as_one(rig):
    rig.allowed, rig.reason = PHOTO_BRAND, "qa_failed"
    await _run(force_format="learned", fallback_format="full_bleed")
    assert rig.director["force_format"] == "full_bleed"
    assert rig.stamps[-1]["fallback_reason"] == "qa_failed"


async def test_an_error_in_the_learned_path_is_recorded_as_one(rig):
    rig.allowed, rig.learned_raises = PHOTO_BRAND, True
    await _run(force_format="learned", fallback_format="minimal_over")
    assert rig.director["force_format"] == "minimal_over"
    assert rig.stamps[-1]["fallback_reason"] == "error"


async def test_a_fallback_the_brand_does_not_allow_is_not_drawn(rig):
    rig.allowed = PHOTO_BRAND
    await _run(force_format="learned", fallback_format="bold_statement")
    # back to the art director — still clamped to the brand's set
    assert rig.director["force_format"] == ""
    assert rig.director["allowed"] == PHOTO_BRAND
    assert rig.stamps[-1]["fallback_format"] == ""


async def test_no_fallback_sent_keeps_todays_free_pick(rig):
    rig.allowed = PHOTO_BRAND
    await _run(force_format="learned")
    assert rig.director["force_format"] == ""
    assert rig.stamps[-1]["fallback_reason"] == "thin_library"


async def test_a_served_learned_layout_is_stamped_none(rig):
    rig.allowed, rig.learned = PHOTO_BRAND, ("https://x/learned.png", "learned")
    out = await _run(force_format="learned", fallback_format="full_bleed")
    assert out == ("https://x/learned.png", "learned")
    assert rig.director is None
    assert rig.stamps == [{"requested_format": "learned", "fallback_reason": "none"}]


def test_fallback_reasons_are_specs_closed_vocabulary():
    # BM2 reads fallback_reason; a value outside SPEC3's set is one it cannot read.
    assert format_fallback.REASONS == ("none", "thin_library", "qa_failed", "error")


# ── P4(c): BM2's avoid_template_id is honoured, without giving up the slot ──

async def test_a_third_draw_of_the_avoided_layout_is_redrawn(rig):
    rig.allowed = PHOTO_BRAND
    rig.learned = [("https://x/t.png", "learned"), ("https://x/u.png", "learned")]
    rig.served = ["tpl-T"]
    out = await _run(force_format="learned", fallback_format="full_bleed",
                     avoid_template_id="tpl-T", feedback="warmer")
    assert rig.learned_calls == 2
    assert out == ("https://x/u.png", "learned")
    assert rig.learned_kw[0] == rig.learned_kw[1]      # same guidance, same sizes
    assert {"avoided_template_id": "tpl-T", "avoid_redrawn": True} in rig.stamps
    assert rig.stamps[-1] == {"requested_format": "learned", "fallback_reason": "none"}
    assert rig.director is None


async def test_another_layout_is_not_redrawn(rig):
    rig.allowed, rig.learned, rig.served = PHOTO_BRAND, ("https://x/u.png", "learned"), ["tpl-U"]
    out = await _run(force_format="learned", avoid_template_id="tpl-T")
    assert rig.learned_calls == 1
    assert out == ("https://x/u.png", "learned")
    assert rig.stamps == [{"requested_format": "learned", "fallback_reason": "none"}]


async def test_a_pinned_house_layout_is_never_redrawn(rig):
    rig.allowed, rig.learned, rig.served = PHOTO_BRAND, ("https://x/t.png", "learned"), ["tpl-T"]
    rig.pinned = True
    out = await _run(force_format="learned", avoid_template_id="tpl-T")
    assert rig.learned_calls == 1
    assert out == ("https://x/t.png", "learned")


async def test_a_redraw_that_misses_keeps_the_first_render(rig):
    rig.allowed, rig.served = PHOTO_BRAND, ["tpl-T"]
    rig.learned = [("https://x/t.png", "learned"), None]   # one-row lane / QA strike
    out = await _run(force_format="learned", fallback_format="full_bleed",
                     avoid_template_id="tpl-T")
    assert rig.learned_calls == 2
    assert out == ("https://x/t.png", "learned")         # the slot is never given up
    assert {"avoided_template_id": "tpl-T", "avoid_redrawn": False} in rig.stamps
    assert rig.director is None


async def test_unrepeated_survives_a_redraw_that_throws(monkeypatch):
    async def _served(_a, _t):
        return "tpl-T", False

    async def _stamp(*_a):
        return None

    async def _boom():
        raise RuntimeError("render failed")

    monkeypatch.setattr(format_fallback, "served_layout", _served)
    monkeypatch.setattr(format_fallback, "stamp", _stamp)
    first = ("https://x/t.png", "learned")
    assert await format_fallback.unrepeated("a", "t", "tpl-T", first, _boom) == first
    assert await format_fallback.unrepeated("a", "t", "", first, _boom) == first


def test_the_learned_gate_is_untouched_by_the_fallback_bookkeeping():
    # SPEC3 B1: the hunk sits AFTER the learned call, so a rebase onto COPILOT's
    # caller edits stays clean — the gate still follows the block's own comment.
    import inspect
    src = inspect.getsource(m._generate_designed_post_image).splitlines()
    gate = next(i for i, ln in enumerate(src)
                if ln.strip().startswith('if force_format == "learned" and not base_spec'))
    assert src[gate - 1].strip().startswith("# takes this path")
    assert src[gate + 1].strip() == "try:"
    assert src[gate + 2].strip() == "_learned = await _generate_learned_post_image("


async def test_a_post_that_never_asked_for_learned_is_untouched(rig):
    rig.allowed = PHOTO_BRAND
    await _run(force_format="hero_quote", fallback_format="full_bleed")
    assert rig.learned_calls == 0
    assert rig.director["force_format"] == "hero_quote"
    assert rig.stamps == []


async def test_a_redo_never_takes_the_learned_slot(rig):
    rig.allowed = PHOTO_BRAND
    await _run(force_format="learned", fallback_format="full_bleed", _qa_attempt=1)
    assert rig.learned_calls == 0
    assert rig.stamps == []


# ── B2: nothing enabled → learned, then a photo layout; never a bare photo ──

async def test_nothing_enabled_tries_learned_first(rig):
    rig.allowed, rig.learned = set(), ("https://x/l.png", "learned")
    out = await _run()
    assert out == ("https://x/l.png", "learned")
    assert rig.learned_calls == 1
    assert rig.stamps[-1]["fallback_reason"] == "none"
    assert rig.stamps[-1]["allowed_empty"] is True


async def test_nothing_enabled_then_full_bleed_over_photo_layouts(rig):
    rig.allowed = set()
    out = await _run()
    assert out != ("", "")                       # the old bare-photo short-circuit
    assert rig.director["force_format"] == "full_bleed"
    assert rig.director["allowed"] == set(PHOTO_FORMATS)
    assert rig.stamps[-1] == {"requested_format": "learned", "fallback_reason": "thin_library",
                              "fallback_format": "full_bleed", "allowed_empty": True}


async def test_nothing_enabled_learned_order_is_tried_once_and_keeps_its_fallback(rig):
    rig.allowed = set()
    await _run(force_format="learned", fallback_format="hero_quote")
    assert rig.learned_calls == 1                # not tried a second time
    assert rig.director["force_format"] == "hero_quote"


async def test_nothing_enabled_qa_retry_lets_the_director_choose_a_photo_layout(rig):
    rig.allowed = set()
    await _run(_qa_attempt=1, avoid="full_bleed")
    assert rig.learned_calls == 0
    assert rig.director["force_format"] == ""
    assert rig.director["allowed"] == set(PHOTO_FORMATS)


@pytest.mark.parametrize("force", ["", "hero_quote"])
async def test_nothing_enabled_regeneration_never_takes_a_learned_layout(rig, force):
    # /v1/regenerate "new layout, the text covers the face" on a photoless card:
    # no base_spec, no photo to keep — the art director must still get the
    # owner's correction, and no "learned" is claimed for it.
    rig.allowed, rig.learned = set(), ("https://x/l.png", "learned")
    await _run(force_format=force, feedback="new layout, the text covers the face",
               exclude_photos=("k1",), is_redo=True)
    assert rig.learned_calls == 0
    assert rig.stamps == []
    assert rig.director["force_format"] == force
    assert rig.director["feedback"] == "new layout, the text covers the face"
    assert rig.director["allowed"] == set(PHOTO_FORMATS)


@pytest.mark.parametrize("force", ["bold_statement", "carousel", "text_carousel"])
async def test_nothing_enabled_a_named_format_is_not_answered_with_learned(rig, force):
    # e.g. a BM2 redo of a poster order: it named a format (BM2 never asks a redo
    # for learned), so it gets a photo layout, not the learned slot.
    rig.allowed, rig.learned = set(), ("https://x/l.png", "learned")
    await _run(force_format=force)
    assert rig.learned_calls == 0
    assert rig.stamps == []
    assert rig.director["force_format"] == "full_bleed"


async def test_nothing_enabled_redo_with_a_named_format_gets_a_photo_layout(rig):
    rig.allowed = set()
    await _run(force_format="bold_statement", feedback="make it bolder", is_redo=True)
    assert rig.learned_calls == 0
    assert rig.director["force_format"] == "full_bleed"


def test_regenerate_marks_its_designed_call_as_a_redo():
    import inspect
    src = inspect.getsource(api_v1._run_regenerate)
    call = src[src.index("_main._generate_designed_post_image("):]
    assert "is_redo=True" in call[:call.index("job[\"result\"]")]


async def test_james_text_only_set_is_honoured(rig):
    rig.allowed = JAMES
    await _run()
    assert rig.learned_calls == 0
    assert rig.director["allowed"] == JAMES
    assert rig.director["force_format"] == ""


# ── B3: a photo layout with no photo ────────────────────────────────────

_SPEC = {"quote": "Built to last", "headline": "Built to last", "statement": "Built to last"}
_PAL = [{"role": "background", "hex": "#0E3B2E"}, {"role": "ink", "hex": "#FFFFFF"},
        {"role": "accent", "hex": "#E3A72F"}]


@pytest.mark.parametrize("fmt", sorted(PHOTO_FORMATS))
def test_photoless_layout_keeps_its_layout_when_brand_quote_is_off(fmt):
    png, used = render_designed(fmt, dict(_SPEC), kit={}, hero_bytes=None, palette=_PAL,
                                allowed=PHOTO_BRAND)
    assert used == fmt
    assert Image.open(BytesIO(png)).size[0] > 0


def test_photoless_layout_unrestricted_brand_keeps_todays_card():
    _png, used = render_designed("full_bleed", dict(_SPEC), kit={}, hero_bytes=None,
                                 palette=_PAL)
    assert used == "brand_quote"


def test_photoless_layout_for_a_brand_that_allows_the_quote_card():
    _png, used = render_designed("hero_quote", dict(_SPEC), kit={}, hero_bytes=None,
                                 palette=_PAL, allowed=JAMES)
    assert used == "brand_quote"


def test_an_unknown_format_takes_the_brands_first_photo_layout():
    _png, used = render_designed("learned", dict(_SPEC), kit={}, hero_bytes=None,
                                 palette=_PAL, allowed={"minimal_over", "statement"})
    assert used == "minimal_over"


def test_the_palette_ground_is_the_brands_own_and_not_flat():
    from james_os import image_compose
    with image_compose.canvas(1080, 1350):
        img = Image.open(BytesIO(_photoless_ground({"palette": _PAL}))).convert("RGB")
    assert img.size == (1080, 1350)
    # the foot of the wash sits in the brand's ground/accent family, never navy
    r, g, b = img.getpixel((1070, 1340))
    assert r > b and g > b
    # lit upper-left, deeper lower-right: a gradient, not a flat fill
    assert img.getpixel((270, 270)) != img.getpixel((1070, 1340))


def test_the_palette_ground_follows_the_canvas():
    from james_os import image_compose
    with image_compose.canvas(1600, 900):
        img = Image.open(BytesIO(_photoless_ground({"palette": _PAL})))
    assert img.size == (1600, 900)


# ── B4: the art director's brief is brand-neutral ───────────────────────

def test_the_director_no_longer_names_a_house_style():
    p = imagegen._DESIGN_DIRECTOR_SYSTEM
    assert "house style" not in p
    assert "lead the rotation" not in p
    assert "FORMAT PRIORITY" not in p
    assert "VARY formats" in p
    assert "PREFER it over a" in p and "text-only card" in p


def test_a_text_only_brand_still_gets_only_text_cards():
    for _ in range(20):
        assert imagegen._clamp_format("full_bleed", JAMES) in JAMES   # all text-only


# ── B1 threading: /v1/generate → _make_text_post → the designed path ──────

def test_generate_request_takes_the_new_fields():
    req = api_v1.GenerateRequest(brief="b", image_kind="designed", force_format="learned",
                                 fallback_format="full_bleed")
    assert req.fallback_format == "full_bleed"
    assert api_v1.GenerateRequest(brief="b").fallback_format == ""


async def _capture_run(monkeypatch, **fields):
    seen = {}

    async def _make_text_post(idea, platform, tenant_id, **kw):
        seen.update(kw)
        return {"action_id": "a", "status": "queued", "voice_score": None}

    monkeypatch.setattr(autopilot_bulk, "_make_text_post", _make_text_post)
    api_v1._JOBS["job-v"] = {"status": "queued"}
    try:
        await api_v1._run_generate("job-v", "tid", api_v1.GenerateRequest(brief="b", **fields))
        assert api_v1._JOBS["job-v"]["status"] == "done"
    finally:
        api_v1._JOBS.pop("job-v", None)
    return seen


async def test_run_generate_threads_the_fields(monkeypatch):
    seen = await _capture_run(monkeypatch, image_kind="designed", force_format="learned",
                              fallback_format="hero_quote")
    assert seen["force_format"] == "learned"
    assert seen["fallback_format"] == "hero_quote"


async def test_an_avoid_template_id_from_bm2_is_threaded(monkeypatch):
    seen = await _capture_run(monkeypatch, image_kind="designed", force_format="learned",
                              fallback_format="hero_quote", avoid_template_id="tpl-2")
    assert seen["force_format"] == "learned"
    assert seen["avoid_template_id"] == "tpl-2"


async def test_a_repeat_hint_never_skips_the_learned_slot(rig):
    rig.allowed, rig.learned = PHOTO_BRAND, ("https://x/learned.png", "learned")
    # an unreadable served layout ("") is not the avoided one: no redraw, no skip
    out = await _run(force_format="learned", fallback_format="framed_print",
                     avoid_template_id="tpl-T")
    assert rig.learned_calls == 1
    assert out == ("https://x/learned.png", "learned")


async def test_run_generate_sends_nothing_new_when_unset(monkeypatch):
    seen = await _capture_run(monkeypatch, image_kind="designed")
    assert "fallback_format" not in seen and "avoid_template_id" not in seen


async def test_make_text_post_threads_the_fields(monkeypatch):
    seen = {}

    async def _content(_brief, _tid):
        return SimpleNamespace(action_id="a1", draft="draft", note="", platform="instagram",
                               voice_score=None, status="queued")

    async def _designed(*a, **kw):
        seen.update(kw)
        return "https://x/d.png", "full_bleed"

    monkeypatch.setattr(autopilot_bulk, "generate_content", _content)
    monkeypatch.setattr(m, "_generate_designed_post_image", _designed)
    out = await autopilot_bulk._make_text_post(
        {"topic": "t", "title": "t"}, "instagram", None, image_kind="designed",
        force_format="learned", fallback_format="full_bleed", avoid_template_id="tpl-9")
    assert out["format"] == "full_bleed"
    assert seen["fallback_format"] == "full_bleed"
    assert seen["avoid_template_id"] == "tpl-9"

    seen.clear()
    await autopilot_bulk._make_text_post({"topic": "t"}, "instagram", None,
                                         image_kind="designed")
    assert "fallback_format" not in seen and "avoid_template_id" not in seen


def test_a_pinned_house_layout_is_never_swapped_for_an_unrepeated_one():
    """The house showcase pins one catalogue layout; avoid_template_id must not
    replace it (the re-pick would ignore the pin)."""
    import inspect
    from james_os import main as m

    src = inspect.getsource(m._generate_designed_post_image)
    assert "if _learned and avoid_template_id and not house_layout_id:" in src
