"""Every post image prompt says who the brand is and what the post is about.

The image model used to get a style, a subject and (rarely) the brand's written
guidelines — never the brand itself, so a resort's picture and a tour operator's
looked like the same stock photo. imagegen.image_brief now carries a brand
background and the post's topic into every prompt built during a post's render.
The things that would go wrong quietly:

  * a brief leaking into another brand's render, or past the post it was for;
  * the added lines cutting the brand's guidelines off the end of the prompt;
  * the same-face reference prompt losing its instruction to a longer prompt;
  * a caller with no context getting a different prompt than before.
"""

import asyncio
import contextlib
from types import SimpleNamespace

import pytest

from james_os import api_v1, autopilot_bulk, brand_identity, imagegen

pytestmark = pytest.mark.nodb


def test_no_brief_means_the_prompt_is_exactly_as_before():
    prefix = imagegen.POST_STYLES[imagegen._DEFAULT_STYLE]
    out = imagegen._build_post_prompt("a hawk over a fairway", "the caption", imagegen._DEFAULT_STYLE,
                                      "warm natural light only")
    assert out == " ".join([
        prefix, "Subject: a hawk over a fairway.", "Context: the caption",
        "Follow the brand's visual guidelines exactly: warm natural light only",
    ])[:1300]


def test_the_brief_rides_after_the_subject():
    with imagegen.image_brief(brand="A desert golf resort in the high desert, for golfers and families",
                              topic="Sunrise tee times this weekend"):
        out = imagegen._build_post_prompt("a hawk over a fairway", "", imagegen._DEFAULT_STYLE)
    subj = out.index("Subject: a hawk over a fairway.")
    brand = out.index("Brand background")
    topic = out.index("This post is about: Sunrise tee times this weekend.")
    assert subj < brand < topic
    assert "never write it as text" in out


def test_the_brief_never_cuts_the_guidelines():
    long_brand = "A family-run golf resort in the high desert. " * 14  # ~630 chars
    guide = "Never show alcohol. Warm natural light only."
    with imagegen.image_brief(brand=long_brand, topic="Spring tournament week"):
        out = imagegen._build_post_prompt("a hawk over a fairway, " * 12, "caption context",
                                          imagegen._DEFAULT_STYLE, guide)
    assert out.endswith(guide)


def test_a_topic_already_in_the_subject_is_not_repeated():
    with imagegen.image_brief(brand="", topic="a hawk over a fairway"):
        out = imagegen._build_post_prompt("A hawk over a fairway at dawn", "", imagegen._DEFAULT_STYLE)
    assert "This post is about" not in out


def test_the_brief_ends_with_its_block():
    with imagegen.image_brief(brand="Somewhere", topic="Something"):
        pass
    out = imagegen._build_post_prompt("x", "", imagegen._DEFAULT_STYLE)
    assert "Brand background" not in out and "This post is about" not in out


async def test_concurrent_renders_never_see_each_others_brief():
    seen = {}

    async def render(name):
        with imagegen.image_brief(brand=f"Brand {name}", topic=f"Topic {name}"):
            await asyncio.sleep(0.01)
            seen[name] = imagegen._build_post_prompt("a scene", "", imagegen._DEFAULT_STYLE)

    await asyncio.gather(render("A"), render("B"))
    assert "Brand A" in seen["A"] and "Brand B" not in seen["A"]
    assert "Brand B" in seen["B"] and "Brand A" not in seen["B"]


@pytest.fixture
def edit_client(monkeypatch):
    seen = {}

    async def _edit(**kw):
        seen.update(kw)
        import base64
        return SimpleNamespace(data=[SimpleNamespace(b64_json=base64.b64encode(b"png").decode())])

    async def _directive(tenant_id):
        return ""

    async def _record(*a, **kw):
        return None

    monkeypatch.setattr(imagegen, "_client", lambda: SimpleNamespace(images=SimpleNamespace(edit=_edit)))
    monkeypatch.setattr(imagegen, "_brand_visual_directive", _directive)
    monkeypatch.setattr(imagegen.spend, "record_images", _record)
    return seen


async def test_the_same_face_prompt_keeps_its_instruction(edit_client):
    with imagegen.image_brief(brand="A long brand background. " * 30, topic="A long topic. " * 20):
        await imagegen.generate_post_image_with_refs("A walk on the beach.", references=[("u", b"x")])
    prompt = edit_client["prompt"]
    assert "same face" in prompt and "Brand background" not in prompt
    assert len(prompt) <= 1000


async def test_brand_style_scenes_carry_the_brief(edit_client):
    with imagegen.image_brief(brand="A desert golf resort", topic="Spring tournament week"):
        await imagegen.generate_post_image_with_refs("A hawk over a fairway.", references=[("u", b"x")],
                                                     ref_mode="brand_style", prompt_cap=3200)
    assert "Brand background" in edit_client["prompt"]
    assert "This post is about: Spring tournament week." in edit_client["prompt"]


# ------------------------------------------------------------ the engine's own line


@pytest.fixture
def tenant_db(monkeypatch):
    state = {"names": {"t1": "Desert Links Resort", "t2": "Desert Links Resort",
                       "t3": "Desert Links Resort", "tA": "Alpha Tours", "tB": "Bravo Golf",
                       "t0": "Tenant Zero"},
             "niches": ["golf resort"], "calls": 0, "fail": False, "acquired": []}

    class _Conn:
        def __init__(self, tid):
            self.tid = tid

        async def fetchrow(self, sql, *a):
            state["calls"] += 1
            if state["fail"]:
                raise RuntimeError("db down")
            return {"name": state["names"].get(str(self.tid), ""), "config": None}

    @contextlib.asynccontextmanager
    async def _acq(tenant_id=None):
        state["acquired"].append(tenant_id)
        yield _Conn(tenant_id)

    async def _niches(conn, tenant_id):
        return state["niches"]

    from james_os import db, house_layouts
    monkeypatch.setattr(db, "acquire", _acq)
    monkeypatch.setattr(house_layouts, "tenant_niches", _niches)
    brand_identity._BACKGROUND_CACHE.clear()
    yield state
    brand_identity._BACKGROUND_CACHE.clear()


async def test_the_engine_names_the_brand_and_its_niche(tenant_db):
    assert await brand_identity.brand_background("t1") == "Desert Links Resort, in golf resort"
    await brand_identity.brand_background("t1")
    assert tenant_db["calls"] == 1, "cached across a batch"


async def test_a_brand_with_no_niche_is_still_named(tenant_db):
    tenant_db["niches"] = []
    assert await brand_identity.brand_background("t2") == "Desert Links Resort"


async def test_a_db_failure_gives_no_background_and_is_not_remembered(tenant_db):
    tenant_db["fail"] = True
    assert await brand_identity.brand_background("t3") == ""
    tenant_db["fail"] = False
    assert await brand_identity.brand_background("t3") == "Desert Links Resort, in golf resort"


async def test_a_tenantless_call_uses_the_requests_brand_and_never_another(tenant_db):
    """Agent runs pass tenant None and rely on the request's tenant."""
    from james_os import db
    tok = db._request_tenant.set("tA")
    try:
        assert await brand_identity.brand_background(None) == "Alpha Tours, in golf resort"
    finally:
        db._request_tenant.reset(tok)
    tok = db._request_tenant.set("tB")
    try:
        assert await brand_identity.brand_background(None) == "Bravo Golf, in golf resort"
    finally:
        db._request_tenant.reset(tok)


async def test_a_placeholder_tenant_name_is_never_the_brand(tenant_db):
    assert await brand_identity.brand_background("t0") == "a brand in golf resort"


# ------------------------------------------------------------ in a real post


@pytest.fixture
def post(monkeypatch):
    seen = {}

    async def _content(brief, tenant_id):
        return SimpleNamespace(action_id="act-1", draft="Tee off at sunrise this Saturday.",
                               platform="instagram", note="", voice_score=0.9, status="pending")

    async def _designed(action_id, topic, draft_text, tenant_id, **kw):
        seen["brief"] = imagegen._IMAGE_BRIEF.get()
        return "file://card.png", "hero_quote"

    async def _background(tenant_id=None):
        return "Engine line for the brand"

    from james_os import main
    monkeypatch.setattr(autopilot_bulk, "generate_content", _content)
    monkeypatch.setattr(main, "_generate_designed_post_image", _designed)
    monkeypatch.setattr(brand_identity, "brand_background", _background)
    return seen


async def test_a_post_image_carries_brand_managers_background(post):
    await autopilot_bulk._make_text_post({"topic": "Sunrise tee times"}, "instagram", "t1",
                                         image_kind="designed",
                                         brand_context="A family golf resort in the high desert")
    assert post["brief"]["brand"] == "A family golf resort in the high desert"
    assert post["brief"]["topic"].startswith("Sunrise tee times — Tee off at sunrise")
    assert imagegen._IMAGE_BRIEF.get() is None, "the brief ends with the post"


async def test_without_brand_managers_background_the_engine_line_is_used(post):
    await autopilot_bulk._make_text_post({"topic": "Sunrise tee times"}, "instagram", "t1",
                                         image_kind="designed")
    assert post["brief"]["brand"] == "Engine line for the brand"


def test_the_requests_take_a_brand_background():
    assert api_v1.GenerateRequest(brief="x").brand_context == ""
    assert api_v1.GenerateRequest(brief="x", brand_context="A resort").brand_context == "A resort"
    assert api_v1.RegenerateBody().brand_context == ""
    with pytest.raises(Exception):
        api_v1.GenerateRequest(brief="x", brand_context="y" * 1501)


def test_a_queued_posts_topic_line():
    assert api_v1._post_topic_line({"topic": "Spring week", "content": "Join us for the spring week."}) \
        == "Spring week — Join us for the spring week."
    assert api_v1._post_topic_line({"topic": "Spring week", "content": "Spring week is here"}) \
        in ("Spring week", "Spring week — Spring week is here")
    assert api_v1._post_topic_line({}) == ""


# ------------------------------------------------------------ what the post is about


def test_a_writers_brief_is_never_the_topic():
    redo = "REDO — the owner REJECTED the previous version of this image. Their words: too small"
    assert imagegen.post_topic_line("Sunrise tee times", redo, "Tee off at sunrise.") == \
        "Sunrise tee times — Tee off at sunrise."
    assert imagegen.post_topic_line(f"post: {redo[:70]}", redo, "Spring week is here.") == \
        "Spring week is here."
    assert imagegen.post_topic_line("", "REPURPOSE — adapt the following to a carousel", "") == ""


def test_an_overlong_topic_gives_way_to_the_caption():
    assert imagegen.post_topic_line("", "x " * 150, "Tee off at sunrise.") == "Tee off at sunrise."


def test_contact_details_never_reach_the_image_model():
    with imagegen.image_brief(brand="Desert Links — call +1 (575) 555-0100, info@desertlinks.example",
                              topic="Tee times — book at https://desertlinks.example/tee or www.x.com"):
        out = imagegen._build_post_prompt("a hawk", "", imagegen._DEFAULT_STYLE)
    for leaked in ("555", "@", "http", "www."):
        assert leaked not in out[out.index("Brand background"):]
