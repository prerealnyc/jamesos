"""Editing a learned (or cloned) post keeps its layout.

The owner's rule: "fix the image edit so learned posts keep their layout". Before
this, an edit of a learned post either drew a DIFFERENT learned layout (the fresh
render path), refused to swap its photo while claiming there was no other one
(the rebuild always reused the photo), ignored "make it brighter" (only the copy
could change), and left every other platform showing the version being replaced.
"""

import asyncio
import contextlib
import json

import pytest

from james_os import api_v1, template_clone


@pytest.fixture(autouse=True)
def fresh_pool():
    """No DB here — shadow conftest's autouse Postgres fixture."""
    yield


SPEC = {
    "status": "ok", "kind": "graphic_card",
    "background": {"treatment": "full_bleed_photo", "scrim": "bottom", "photo_box": None},
    "palette": {"bg": "#111318", "accent": "#c9a24b", "ink": "#ffffff"},
    "elements": [
        {"role": "headline", "box": {"x": .07, "y": .6, "w": .86, "h": .2}, "align": "left",
         "size": "xl", "weight": "black", "case": "none", "color": "#ffffff"},
        {"role": "byline", "box": {"x": .07, "y": .85, "w": .5, "h": .05}, "align": "left",
         "size": "sm", "weight": "bold", "case": "none", "color": "#ffffff"},
        {"role": "byline", "box": {"x": .07, "y": .91, "w": .5, "h": .05}, "align": "left",
         "size": "sm", "weight": "bold", "case": "none", "color": "#ffffff"},
    ],
    "decorations": [{"type": "bar", "box": {"x": .07, "y": .57, "w": .2, "h": .006}, "color": "#c9a24b"}],
}


def _prepared():
    from james_os.design_templates import prepare
    return prepare(SPEC)


# ------------------------------------------------------------------ the look


def test_a_look_edit_changes_only_the_look():
    orig = _prepared()
    edited = json.loads(json.dumps(orig))
    edited["elements"][0]["size"] = "xxl"
    edited["elements"][0]["color"] = "#ff0000"
    edited["background"]["scrim"] = "none"                 # "brighter"
    edited["background"]["treatment"] = "solid"            # NOT a look change
    out = template_clone.merge_look_edit(orig, edited)
    assert out["elements"][0]["size"] == "xxl" and out["elements"][0]["color"] == "#ff0000"
    assert out["background"]["scrim"] == "none"
    assert out["background"]["treatment"] == "full_bleed_photo", "the photo treatment is the layout"
    assert [e["role"] for e in out["elements"]] == [e["role"] for e in orig["elements"]]


def test_a_bad_look_edit_changes_nothing():
    orig = _prepared()
    # a dropped line, a renamed role, junk values, a new line: none of it lands
    edited = {"elements": [{"role": "kicker", "size": "xxl"}, {"role": "byline", "size": "huge",
                                                                "color": "red", "box": {"x": 3}}],
              "background": {"scrim": "sideways"}, "palette": {"bg": "not-a-colour"}}
    assert template_clone.merge_look_edit(orig, edited) == orig
    assert template_clone.merge_look_edit(orig, "not json") == orig


# ------------------------------------------------------------------ the rebuild


def _stub_rebuild(monkeypatch, *, photos=(("hero-a", b"A"), ("hero-b", b"B")), palette=None):
    calls = {"palette": 0, "look": [], "copy": [], "picked_excluding": None, "renders": []}

    async def _palette(tenant_id):
        calls["palette"] += 1
        return palette

    async def _look(spec, feedback):
        calls["look"].append(feedback)
        out = json.loads(json.dumps(spec))
        out["background"]["scrim"] = "none"
        return out

    async def _copy(content, feedback, tenant_id):
        calls["copy"].append(feedback)
        return dict(content)

    async def _handle(content, feedback, roles, tenant_id):
        return content

    async def _by_key(tenant_id, key):
        return dict(photos).get(key)

    async def _files(tenant_id=None, limit=None):
        return list(photos)

    async def _pick(refs, tenant_id=None, exclude=()):
        calls["picked_excluding"] = tuple(exclude)
        rest = [r for r in refs if r[0] not in exclude]
        return rest[0] if rest else (refs[0] if refs else None)

    async def _logo(tenant_id):
        return None

    def _render(spec, content, *, hero_bytes=None, logo_bytes=None, palette=None):
        from james_os import image_compose
        calls["renders"].append((image_compose._w(), image_compose._h(), hero_bytes, palette))
        return b"png", "graphic_card"

    from james_os import hero_context, photo_pick
    monkeypatch.setattr(template_clone, "_brand_palette", _palette)
    monkeypatch.setattr(template_clone, "edit_spec_look", _look)
    monkeypatch.setattr(template_clone, "_edit_clone_copy", _copy)
    monkeypatch.setattr(template_clone, "_apply_handle_request", _handle)
    monkeypatch.setattr(template_clone, "_hero_by_key", _by_key)
    monkeypatch.setattr(template_clone, "_brand_logo", _logo)
    monkeypatch.setattr(template_clone, "render_spec", _render)
    monkeypatch.setattr(hero_context, "get_hero_photo_files", _files)
    monkeypatch.setattr(photo_pick, "pick_hero_bytes", _pick)
    return calls


def _payload(**extra):
    spec = _prepared()
    return {"image_format": "learned", "clone_spec": spec, "hero_photo_key": "hero-a",
            "clone_content": {e["role"]: "x" for e in spec["elements"]}, "topic": "t", **extra}


def test_brighter_keeps_the_layout_and_the_photo_and_changes_the_look(monkeypatch):
    calls = _stub_rebuild(monkeypatch)
    out = asyncio.run(template_clone.rebuild_design(_payload(), "make it brighter", "t"))
    assert out["hero_key"] == "hero-a" and calls["renders"][0][2] == b"A", "same photo"
    assert out["spec"]["background"]["scrim"] == "none", "the look change landed"
    assert [e["role"] for e in out["spec"]["elements"]] == ["headline", "byline", "byline#2"]
    assert calls["look"] == ["make it brighter"] and calls["copy"] == ["make it brighter"]


def test_a_swap_takes_a_different_photo_in_the_same_layout(monkeypatch):
    """The rebuild always reused the photo, so BM2 saw the same key come back and
    told the owner there was no other photo — with one sitting in the library."""
    calls = _stub_rebuild(monkeypatch)
    out = asyncio.run(template_clone.rebuild_design(
        _payload(), "use a different photo", "t", exclude_photo_keys=("hero-a",)))
    assert calls["picked_excluding"] == ("hero-a",)
    assert out["hero_key"] == "hero-b" and calls["renders"][0][2] == b"B"


def test_a_swap_with_no_other_photo_says_so_by_keeping_it(monkeypatch):
    _stub_rebuild(monkeypatch, photos=(("hero-a", b"A"),))
    out = asyncio.run(template_clone.rebuild_design(
        _payload(), "use a different photo", "t", exclude_photo_keys=("hero-a",)))
    assert out["hero_key"] == "hero-a", "the unchanged key is how the caller knows"


def test_every_platform_shape_is_rebuilt_with_it(monkeypatch):
    calls = _stub_rebuild(monkeypatch)
    out = asyncio.run(template_clone.rebuild_design(
        _payload(), "", "t", sizes=((1600, 900), (1080, 1920))))
    assert set(out["by_size"]) == {"1600x900", "1080x1920"}
    assert [(w, h) for (w, h, _b, _p) in calls["renders"][1:]] == [(1600, 900), (1080, 1920)]


def test_brand_colours_go_on_once_so_an_owner_colour_sticks(monkeypatch):
    """Painted in brand colours at render time, "make the headline red" was
    mapped straight back to the nearest brand colour."""
    calls = _stub_rebuild(monkeypatch, palette=[{"role": "primary", "hex": "#0a2240"}])
    first = asyncio.run(template_clone.rebuild_design(_payload(), "", "t"))
    assert calls["palette"] == 1 and first["spec"]["brand_colours"] is True
    assert all(p is None for (*_x, p) in calls["renders"]), "drawn without repainting"
    asyncio.run(template_clone.rebuild_design(_payload(clone_spec=first["spec"]), "", "t"))
    assert calls["palette"] == 1, "an already-branded layout is not repainted"


# ------------------------------------------------------------------ the regenerate row


def test_the_rebuilt_row_carries_the_new_photo_look_and_shapes(monkeypatch):
    written: dict = {}

    class _Conn:
        async def execute(self, sql, new_id, payload):
            written.update(json.loads(payload))

    @contextlib.asynccontextmanager
    async def _acquire(tenant_id=None):
        yield _Conn()

    class _Store:
        def save(self, tenant, png, name):
            return f"file://{name}", "/tmp/x"

    edited = dict(_prepared(), brand_colours=True)

    async def _rebuild(payload, feedback, tenant_id, **kw):
        assert kw["exclude_photo_keys"] == ("hero-a",) and kw["sizes"] == ((1600, 900),)
        return {"png": b"p", "kind": "graphic_card", "spec": edited,
                "content": {"headline": "new"}, "hero_key": "hero-b",
                "by_size": {"1600x900": b"w"}}

    monkeypatch.setattr(api_v1, "acquire", _acquire)
    monkeypatch.setattr(template_clone, "rebuild_design", _rebuild)
    monkeypatch.setattr("james_os.media.storage", lambda: _Store())
    served, fmt = asyncio.run(api_v1._rebuild_cloned_action(
        "new", _payload(design_template_id="tpl-1"), "use a different photo", "t",
        exclude=("hero-a",), extra_sizes=((1600, 900),)))
    assert fmt == "learned" and written["design_template_id"] == "tpl-1"
    assert written["hero_photo_key"] == "hero-b"
    assert written["clone_spec"] == edited and written["clone_content"] == {"headline": "new"}
    assert written["image_urls_by_size"] == {"1600x900": "file://rebuild-1600x900.png"}


def test_a_redo_never_inherits_the_old_platform_shapes():
    import inspect

    src = inspect.getsource(api_v1._run_regenerate)
    popped = src[src.index("for k in ("):src.index("new_payload.pop(k, None)")]
    assert '"image_urls_by_size"' in popped


def test_owner_edits_have_their_own_cap():
    """The cap exists to stop an automatic loop with no human in it. An owner's
    third edit was refused, fell back to a fresh render, and lost the layout."""
    assert api_v1._regen_cap(False) == api_v1.MAX_REGEN_VERSION == 3
    assert api_v1._regen_cap(True) > api_v1.MAX_REGEN_VERSION


# ------------------------------------------------------------------ the words on the card


def _capture_llm(monkeypatch):
    seen: dict = {}

    class _LLM:
        async def complete_json(self, *, system, messages, max_tokens, temperature):
            seen["system"], seen["user"] = system, messages[0]["content"]
            return {"headline": "Notah Begay joins the club", "byline": "Turtleback"}

    async def _voice(tenant_id):
        return "warm, local", "Turtleback Mountain — golf resort and model homes"

    from james_os import llm, main
    monkeypatch.setattr(llm, "get_llm", lambda: _LLM())
    monkeypatch.setattr(main, "_brand_voice_and_profile", _voice)
    return seen


def test_a_learned_card_is_written_about_its_own_post(monkeypatch):
    """The first live learned post was about the tournament ambassador and its
    card said "Model Homes Tour": its own topic went in as a reference angle,
    marked inspiration only, so the brand profile wrote the card."""
    seen = _capture_llm(monkeypatch)
    spec = {"elements": [{"role": "headline"}, {"role": "byline"}]}
    asyncio.run(template_clone._fill_copy(
        spec, "t", {}, subject="Turn the Notah Begay ambassador into a real storyline",
        caption="Notah Begay is our new ambassador. Here is why that matters."))
    assert "THIS POST IS ABOUT" in seen["user"]
    assert "Notah Begay ambassador" in seen["user"] and "why that matters" in seen["user"]
    assert "inspiration only" not in seen["user"]


def test_cloning_still_borrows_only_the_structure(monkeypatch):
    seen = _capture_llm(monkeypatch)
    spec = {"elements": [{"role": "headline"}]}
    asyncio.run(template_clone._fill_copy(spec, "t", {"topic": "a rival's product launch"}))
    assert "inspiration only" in seen["user"] and "THIS POST IS ABOUT" not in seen["user"]


def test_the_learned_renderer_hands_over_the_post_not_a_reference(monkeypatch):
    from james_os import design_templates, main

    got: dict = {}

    class _Stop(Exception):
        pass

    async def _pick(tenant_id):
        return {"id": "tpl", "spec": SPEC, "source_kind": "competitor"}

    async def _fill(spec, tenant_id, ref, **kw):
        got.update(ref=ref, **kw)
        raise _Stop

    monkeypatch.setattr(design_templates, "pick", _pick)
    monkeypatch.setattr(template_clone, "_fill_copy", _fill)
    with pytest.raises(_Stop):
        asyncio.run(main._generate_learned_post_image(
            "a1", "Turn the Notah Begay ambassador into a real storyline",
            "Notah Begay is our new ambassador.", "t"))
    assert got["subject"].startswith("Turn the Notah Begay") and got["caption"].startswith("Notah Begay")
    assert not got["ref"], "the post's own topic is not a reference angle"


def test_a_rebuild_that_must_rewrite_the_card_writes_it_about_the_post(monkeypatch):
    _stub_rebuild(monkeypatch)
    got: dict = {}

    async def _fill(spec, tenant_id, ref, **kw):
        got.update(kw)
        return {e["role"]: "x" for e in spec["elements"]}

    async def _no_bytes(url):
        return None

    monkeypatch.setattr(template_clone, "_fill_copy", _fill)
    monkeypatch.setattr(template_clone, "_fetch_bytes", _no_bytes)
    payload = _payload(clone_content={}, topic="Sunrise tee times", content="Book the first tee.")
    asyncio.run(template_clone.rebuild_design(payload, "", "t"))
    assert got["subject"] == "Sunrise tee times" and got["caption"] == "Book the first tee."
