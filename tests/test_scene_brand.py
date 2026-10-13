"""A generated scene that looks like the brand, not like stock.

The generative door draws a fresh picture from a description. What ties it to
the brand: the caller's composed scene description (`scene_prompt`), the brand's
colours, and the brand's own photos as STYLE references on gpt-image-1's edit
endpoint. The things that would go wrong quietly here:

  * the references treated as a person to reproduce (the hero-likeness prompt)
    instead of a look to match;
  * a reference failure costing the post instead of falling back to text;
  * a shape gpt-image-1 cannot draw slipping through and landing as landscape;
  * the post not saying which of the brand's photos set its look.

All without a database or a network: storage, the DB and the image model are
stand-ins.
"""

import base64
import contextlib
import json
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from james_os import api_v1, brand_identity, hero_context, imagegen, main, media, photo_subject

pytestmark = pytest.mark.nodb

_NATIVE = {"1:1": (1024, 1024), "9:16": (1024, 1536), "16:9": (1536, 1024)}


def _real_png(aspect="1:1") -> bytes:
    """A real picture at gpt-image-1's native size for that canvas."""
    from io import BytesIO

    from PIL import Image

    buf = BytesIO()
    Image.new("RGB", _NATIVE.get(aspect, (1024, 1024)), (40, 90, 160)).save(buf, format="PNG")
    return buf.getvalue()


_PNG = _real_png()
_REFS = [(f"https://cdn.example/brand/photo-{i}.jpg", f"bytes-{i}".encode()) for i in range(5)]


@pytest.fixture
def scene(monkeypatch):
    seen = {"refs_call": None, "plain_call": None, "payload": {}, "refs_err": "",
            "held": [], "palette": [{"role": "accent", "hex": "#2E80E4", "label": "Sky blue"},
                                    {"role": "background", "hex": "#0B1D12", "label": "Pine"}]}

    async def _photos(tenant_id=None, limit=None, *, likeness=False):
        seen["photos_args"] = (limit, likeness)
        return list(seen.get("library", _REFS))

    async def _stored_palette(tenant_id=None):
        return seen["palette"]

    async def _ctx(tenant_id=None):
        return SimpleNamespace(held_back_urls=list(seen["held"]))

    async def _ranked_record(tenant_id, topic, refs):
        seen["ranked_on"] = topic
        return list(reversed(refs))

    async def _with_refs(topic, *, references, **kw):
        seen["refs_call"] = {"topic": topic, "references": references, **kw}
        seen.setdefault("draws", []).append(("refs", kw.get("aspect")))
        if seen["refs_err"]:
            return None, {}, seen["refs_err"]
        return _real_png(kw.get("aspect", "1:1")), {"prompt": "refs prompt"}, ""

    async def _plain(topic, **kw):
        seen["plain_call"] = {"topic": topic, **kw}
        seen.setdefault("draws", []).append(("plain", kw.get("aspect")))
        return _real_png(kw.get("aspect", "1:1")), {"prompt": "plain prompt"}, ""

    async def _no_logo(tenant_id, png):
        return png, False

    async def _none(*a, **kw):
        return None

    class _Store:
        def save(self, tenant, png, name):
            return f"file://{name}", f"/tmp/{name}"

    class _Conn:
        async def execute(self, sql, action_id, payload):
            seen["payload"].update(json.loads(payload))

    @contextlib.asynccontextmanager
    async def _acq(tenant_id=None):
        yield _Conn()

    monkeypatch.setattr(hero_context, "get_hero_photo_files", _photos)
    monkeypatch.setattr(photo_subject, "subject_ranked", _ranked_record)
    monkeypatch.setattr(brand_identity, "get_brand_palette", _stored_palette)
    monkeypatch.setattr(hero_context, "get_hero_context", _ctx)
    monkeypatch.setattr(imagegen, "generate_post_image_with_refs", _with_refs)
    monkeypatch.setattr(imagegen, "generate_post_image", _plain)
    monkeypatch.setattr(media, "storage", lambda: _Store())
    monkeypatch.setattr(media, "create_media", _none)
    monkeypatch.setattr(main, "acquire", _acq)
    monkeypatch.setattr(main, "_scene_logo", _no_logo)
    return seen


async def _draw(**kw):
    args = {"scene_prompt": "", "aspect": "", "feedback": ""}
    args.update(kw)
    return await main._generate_scene_post_image(
        "act-1", "a hawk over the fairway", "Caption about the morning round.", "tenant-1",
        **args)


async def test_the_brands_own_photos_set_the_look(scene):
    uri, kind = await _draw(scene_prompt="A red-tailed hawk gliding over a desert fairway at dawn.")
    assert kind == "scene" and uri == "file://scene-1080x1080.png"
    call = scene["refs_call"]
    assert call["ref_mode"] == "brand_style"
    # real library photos, never likeness references; best subject matches first
    assert scene["photos_args"] == (main.SCENE_REF_POOL, False)
    assert call["references"] == list(reversed(_REFS))[: main.SCENE_REF_PHOTOS]
    assert scene["plain_call"] is None
    # the composed description leads; the brand's colours ride along
    assert call["topic"].startswith("A red-tailed hawk gliding over a desert fairway at dawn.")
    assert "#2E80E4" in call["topic"] and "Pine" in call["topic"]
    assert call["aspect"] == "1:1"
    assert call["prompt_cap"] == main.SCENE_PROMPT_CAP
    # references are matched on the scene alone, never on the colour line
    assert scene["ranked_on"] == "A red-tailed hawk gliding over a desert fairway at dawn."
    # provenance: which of the brand's photos set the look
    assert scene["payload"]["scene_reference_photos"] == [n for n, _ in call["references"]]
    assert scene["payload"]["image_url"] == "file://scene-1080x1080.png"


async def test_no_description_falls_back_to_the_topic(scene):
    await _draw()
    assert scene["refs_call"]["topic"].startswith("a hawk over the fairway")


async def test_the_requested_shape_is_drawn(scene):
    await _draw(aspect="9:16")
    assert scene["refs_call"]["aspect"] == "9:16"
    assert scene["payload"]["image_urls_by_size"] == {"1080x1920": "file://scene-1080x1920.png"}


async def test_an_empty_library_draws_from_text_alone(scene):
    scene["library"] = []
    await _draw(scene_prompt="Two swallows over a lake.")
    assert scene["refs_call"] is None
    assert scene["plain_call"]["topic"].startswith("Two swallows over a lake.")
    assert scene["payload"]["scene_reference_photos"] == []


async def test_a_failed_reference_draw_falls_back_and_says_so(scene):
    scene["refs_err"] = "image edit failed: 400"
    uri, _ = await _draw(scene_prompt="A heron at first light.")
    assert uri == "file://scene-1080x1080.png"
    assert scene["plain_call"]["topic"].startswith("A heron at first light.")
    # the picture was NOT drawn from the references, so the post must not claim it was
    assert scene["payload"]["scene_reference_photos"] == []


async def test_both_paths_failing_raises_so_the_post_gets_a_real_photo(scene, monkeypatch):
    scene["refs_err"] = "edit failed"

    async def _plain_fails(topic, **kw):
        return None, {}, "generation failed"

    monkeypatch.setattr(imagegen, "generate_post_image", _plain_fails)
    with pytest.raises(RuntimeError, match="generation failed"):
        await _draw()


def test_the_request_only_takes_shapes_gpt_image_can_draw():
    assert api_v1.GenerateRequest(brief="x").scene_prompt == ""
    assert api_v1.GenerateRequest(brief="x").scene_aspect == ""
    assert api_v1.GenerateRequest(brief="x", scene_aspect="9:16").scene_aspect == "9:16"
    assert api_v1.GenerateRequest(brief="x", scene_aspect="4:5").scene_aspect == "4:5"
    with pytest.raises(ValidationError):
        api_v1.GenerateRequest(brief="x", scene_aspect="3:2")
    with pytest.raises(ValidationError):
        api_v1.GenerateRequest(brief="x", scene_prompt="y" * 2001)


# ------------------------------------------------------------ the edit prompt


@pytest.fixture
def edit_client(monkeypatch):
    seen = {}

    async def _edit(**kw):
        seen.update(kw)
        return SimpleNamespace(data=[SimpleNamespace(b64_json=base64.b64encode(_PNG).decode())])

    async def _directive(tenant_id):
        return ""

    async def _record(*a, **kw):
        return None

    client = SimpleNamespace(images=SimpleNamespace(edit=_edit))
    monkeypatch.setattr(imagegen, "_client", lambda: client)
    monkeypatch.setattr(imagegen, "_brand_visual_directive", _directive)
    monkeypatch.setattr(imagegen.spend, "record_images", _record)
    return seen


async def test_brand_style_references_are_a_look_not_a_person(edit_client):
    png, meta, err = await imagegen.generate_post_image_with_refs(
        "A hawk over a fairway.", references=_REFS[:2], aspect="1:1", ref_mode="brand_style")
    assert png == _PNG and not err
    prompt = edit_client["prompt"]
    assert "brand's own photography" in prompt and "do not copy any reference" in prompt
    assert "same face" not in prompt
    assert meta["used_refs"] == 2


async def test_the_person_mode_is_unchanged_for_existing_callers(edit_client):
    await imagegen.generate_post_image_with_refs("A walk on the beach.", references=_REFS[:1])
    prompt = edit_client["prompt"]
    assert "same face" in prompt and "brand's own photography" not in prompt
    assert len(prompt) <= 1000


async def test_no_stored_palette_means_no_colour_line(scene):
    """ensure_brand_palette's neutral floor must never pose as the brand's colours."""
    scene["palette"] = None
    await _draw(scene_prompt="A kingfisher on a reed.")
    assert scene["refs_call"]["topic"] == "A kingfisher on a reed."


async def test_a_flyer_is_never_a_reference(scene):
    scene["held"] = [_REFS[4][0], _REFS[3][0]]  # text-heavy / screenshot photos
    await _draw(scene_prompt="A swallow over a lake.")
    names = [n for n, _ in scene["refs_call"]["references"]]
    assert _REFS[4][0] not in names and _REFS[3][0] not in names
    assert len(names) == main.SCENE_REF_PHOTOS


async def test_only_flyers_in_the_library_draws_from_text(scene):
    scene["held"] = [n for n, _ in _REFS]
    await _draw(scene_prompt="A swallow over a lake.")
    assert scene["refs_call"] is None and scene["plain_call"] is not None


async def test_the_brands_guidelines_survive_a_long_scene(monkeypatch, edit_client):
    async def _directive(tenant_id):
        return "Never show alcohol. Warm natural light only."

    monkeypatch.setattr(imagegen, "_brand_visual_directive", _directive)
    long_scene = ("A wide desert fairway at first light, a hawk banking high above the "
                  "flagstick, long shadows across the green, ") * 6
    await imagegen.generate_post_image_with_refs(
        long_scene, references=_REFS[:2], brief="Caption context.", aspect="1:1",
        ref_mode="brand_style", prompt_cap=main.SCENE_PROMPT_CAP)
    prompt = edit_client["prompt"]
    assert "Never show alcohol" in prompt
    assert prompt.endswith(imagegen._BRAND_STYLE_REF_NOTE)


async def test_people_in_the_references_are_never_reproduced(edit_client):
    await imagegen.generate_post_image_with_refs(
        "Guests dining on a terrace.", references=_REFS[:1], ref_mode="brand_style")
    assert "Never reproduce any person or face" in edit_client["prompt"]
    assert "unidentifiable people" in edit_client["prompt"]
