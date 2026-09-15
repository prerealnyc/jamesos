"""A card captured as layers is the card — and capturing never changes a card.

The card editor opens a generated card as a clean background plate plus every
line of text and every badge as its own movable layer (layer_capture). Those
layers are only worth offering if putting them back together IS the picture on
the board, so the central check here is fidelity: for every layout and several
canvas sizes, the plate with the captured layers drawn back matches the card as
rendered normally. And because the capture hooks PIL itself, the other half is
that outside a capture nothing a compositor draws changes at all.
"""

import asyncio
import json
import threading
from io import BytesIO

import pytest
from PIL import Image, ImageDraw, ImageFont

from james_os import image_compose, layer_capture as lc
from james_os.designed_render import render_designed
from james_os.spec_render import render_spec

pytestmark = pytest.mark.nodb

FORMATS = ["brand_quote", "hero_quote", "statement", "bold_statement", "big_stat",
           "full_bleed", "editorial_split", "minimal_over", "framed_print"]
SIZES = [(1080, 1350), (1080, 1080), (1080, 1920), (1600, 900)]
SPEC = {"quote": "Clarity is the catalyst for every great brand",
        "headline": "Clarity is the catalyst", "statement": "Clarity is the catalyst for growth",
        "stat": "87%", "stat_label": "OF BUYERS DECIDE ON FIRST SIGHT", "stat_sub": "2026 survey",
        "kicker": "SKELON AGENCY", "caption": "A brand is a promise kept", "emphasis": "catalyst",
        "byline_name": "Skelon Agency"}
KIT = {"display_name": "Skelon Agency", "handle": "skelonagency",
       "palette": [{"role": "background", "hex": "#101820"}, {"role": "ink", "hex": "#F5F1E8"},
                   {"role": "accent", "hex": "#C9A24B"}, {"role": "surface", "hex": "#1E2A36"}]}


def _photo() -> bytes:
    """A photo with detail — a flat colour would hide a plate that moved."""
    im = Image.new("RGB", (1400, 1000), (70, 95, 120))
    d = ImageDraw.Draw(im)
    for i in range(0, 1400, 70):
        d.rectangle((i, 0, i + 34, 1000), fill=(110 + i % 90, 80, 60))
    b = BytesIO()
    im.save(b, "JPEG", quality=92)
    return b.getvalue()


def _logo() -> bytes:
    im = Image.new("RGBA", (300, 120), (0, 0, 0, 0))
    ImageDraw.Draw(im).ellipse((10, 10, 110, 110), fill=(201, 162, 75, 255))
    ImageDraw.Draw(im).rectangle((130, 40, 290, 80), fill=(245, 241, 232, 255))
    b = BytesIO()
    im.save(b, "PNG")
    return b.getvalue()


PHOTO, LOGO = _photo(), _logo()


def _designed(fmt, size, spec=SPEC, logo=True):
    def call():
        with image_compose.canvas(*size):
            return render_designed(fmt, spec, kit=KIT, hero_bytes=PHOTO,
                                   profile_bytes=LOGO if logo else None,
                                   profile_is_logo=logo, handle="skelonagency",
                                   palette=KIT["palette"])
    return call


def _img(png) -> Image.Image:
    return Image.open(BytesIO(png[0] if isinstance(png, tuple) else png))


# ------------------------------------------------------------------ fidelity

@pytest.mark.parametrize("size", SIZES)
@pytest.mark.parametrize("fmt", FORMATS)
def test_the_layers_put_back_together_are_the_card(fmt, size):
    """Every layout, every shape: plate + layers == the card as rendered."""
    call = _designed(fmt, size)
    card = _img(call())
    cap = lc.capture(call, card.size)
    assert cap["texts"], f"{fmt} {size}: no text was captured"
    d = lc.drift(cap, card)
    assert d < 1.0, f"{fmt} {size}: layers drift {d:.2f} from the card"


@pytest.mark.parametrize("fmt", FORMATS)
def test_the_plate_has_no_words_on_it(fmt):
    """The plate is the card with its words lifted off — otherwise a moved line
    leaves its ghost behind."""
    call = _designed(fmt, (1080, 1350))
    card = _img(call()).convert("L")
    cap = lc.capture(call, card.size)
    plate = _img(cap["plate"]).convert("L")
    assert plate.tobytes() != card.tobytes()
    # where the first line was inked, the plate differs from the card
    l, t, r, b = (int(v) for v in cap["texts"][0]["ink"])
    box = (max(0, l), max(0, t), min(card.width, r), min(card.height, b))
    assert plate.crop(box).tobytes() != card.crop(box).tobytes()


def test_a_learned_layout_is_captured_too():
    spec = {
        "status": "ok", "kind": "graphic_card",
        "background": {"treatment": "full_bleed_photo", "scrim": "bottom", "photo_box": None},
        "palette": {"bg": "#111318", "accent": "#c9a24b", "ink": "#ffffff"},
        "elements": [
            {"role": "kicker", "box": {"x": 0.07, "y": 0.06, "w": 0.86, "h": 0.07},
             "align": "left", "size": "sm", "weight": "bold", "case": "upper", "color": "#c9a24b"},
            {"role": "headline", "box": {"x": 0.07, "y": 0.55, "w": 0.86, "h": 0.22},
             "align": "left", "size": "xxl", "weight": "black", "case": "none", "color": "#ffffff"},
        ],
        "decorations": [{"type": "bar", "box": {"x": 0.07, "y": 0.52, "w": 0.2, "h": 0.006},
                         "color": "#c9a24b"}],
        "logo_box": {"x": 0.75, "y": 0.88, "w": 0.18, "h": 0.07},
    }
    content = {"kicker": "Skelon agency", "headline": "Clarity is the catalyst"}
    for size in [(1080, 1350), (1080, 1920)]:
        def call():
            with image_compose.canvas(*size):
                return render_spec(spec, content, hero_bytes=PHOTO, logo_bytes=LOGO)
        card = _img(call())
        cap = lc.capture(call, card.size)
        words = " ".join(t["text"] for t in cap["texts"]).lower()
        assert "catalyst" in words and "skelon" in words
        assert [im["kind"] for im in cap["images"]] == ["logo"]
        assert lc.drift(cap, card) < 1.0


# -------------------------------------------------------------------- badges

def _kinds(cap):
    return [i["kind"] for i in cap["images"]]


def test_the_logo_is_its_own_layer_and_not_on_the_plate():
    call = _designed("statement", (1080, 1350))
    card = _img(call())
    cap = lc.capture(call, card.size)
    assert _kinds(cap) == ["logo", "photo"]
    im = cap["images"][0]
    assert im["w"] > 0 and im["h"] > 0
    badge = Image.open(BytesIO(im["png"]))
    assert badge.mode == "RGBA" and badge.size == (im["w"], im["h"])
    box = (im["x"], im["y"], im["x"] + im["w"], im["y"] + im["h"])
    assert _img(cap["plate"]).convert("RGB").crop(box).tobytes() != \
        card.convert("RGB").crop(box).tobytes(), "the logo was left on the plate"


def test_a_profile_photo_is_a_layer_too():
    def with_face():
        with image_compose.canvas(1080, 1350):
            return render_designed("statement", SPEC, kit=KIT, hero_bytes=PHOTO,
                                   profile_bytes=PHOTO, profile_is_logo=False,
                                   handle="skelonagency", palette=KIT["palette"])
    card = _img(with_face())
    cap = lc.capture(with_face, card.size)
    assert _kinds(cap) == ["profile", "photo"]
    assert lc.drift(cap, card) < 1.0


def test_a_card_with_no_mark_has_no_badge_layers():
    cap = lc.capture(_designed("statement", (1080, 1350), logo=False), (1080, 1350))
    assert _kinds(cap) == ["photo"]


@pytest.mark.parametrize("fmt", ["statement", "hero_quote", "editorial_split", "framed_print"])
def test_a_photo_panel_is_a_layer_that_can_move(fmt):
    """A photo on the card as a panel of its own — nothing drawn over it — is
    lifted off the plate with the shape it was laid down in (the rounded
    corners, the fade into the ground)."""
    call = _designed(fmt, (1080, 1350))
    card = _img(call())
    cap = lc.capture(call, card.size)
    photos = [i for i in cap["images"] if i["kind"] == "photo"]
    assert len(photos) == 1
    p = photos[0]
    box = (p["x"], p["y"], p["x"] + p["w"], p["y"] + p["h"])
    assert _img(cap["plate"]).convert("RGB").crop(box).tobytes() != card.convert("RGB").crop(box).tobytes()
    alpha = Image.open(BytesIO(p["png"])).getchannel("A")
    if fmt in ("statement", "framed_print"):           # rounded corners
        assert alpha.getpixel((0, 0)) == 0 and alpha.getpixel((p["w"] // 2, p["h"] // 2)) == 255
    if fmt == "hero_quote":                             # fades in from its left edge
        assert alpha.getpixel((0, p["h"] // 2)) == 0 and alpha.getpixel((p["w"] - 1, p["h"] // 2)) == 255
    assert lc.drift(cap, card) < 1.0


@pytest.mark.parametrize("fmt", ["full_bleed", "minimal_over"])
def test_a_photo_under_a_scrim_stays_in_the_plate(fmt):
    cap = lc.capture(_designed(fmt, (1080, 1350)), (1080, 1350))
    assert "photo" not in _kinds(cap)


def test_a_line_drawn_over_a_photo_stays_over_it():
    """At 16:9 the statement card's words run onto the top of its photo. Put back
    photo-last, the photo hid them; the stack keeps the order they were drawn in."""
    call = _designed("statement", (1600, 900))
    card = _img(call())
    cap = lc.capture(call, card.size)
    photo = next(i for i in cap["images"] if i["kind"] == "photo")
    over = [t for t in cap["texts"] if t["ink"][3] > photo["y"] and t["ink"][1] < photo["y"] + photo["h"]]
    assert over and all(t["z"] > photo["z"] for t in over)
    assert lc.drift(cap, card) < 1.0


def test_faces_are_named_the_way_the_editor_names_them():
    """"SemiBold" is 600, not the "bold" inside it; a variable font's family is
    not its default instance ("Cormorant Light")."""
    import glob
    import os

    got = {}
    for path in glob.glob(os.path.join(os.path.dirname(image_compose._ARCHIVO), "*.ttf")):
        got[os.path.basename(path)] = lc._face(ImageFont.truetype(path, 40))
    assert got["Montserrat-SemiBold.ttf"] == ("Montserrat", "600")
    assert got["Montserrat-ExtraBold.ttf"] == ("Montserrat", "800")
    assert got["Cormorant-Bold.ttf"] == ("Cormorant", "700")
    assert got["PlayfairDisplay-Black.ttf"] == ("Playfair Display", "900")
    assert got["ArchivoBlack-Regular.ttf"] == ("Archivo Black", "400")
    assert got["Anton-Regular.ttf"] == ("Anton", "400")
    assert got["Poppins-Medium.ttf"] == ("Poppins", "500")


# ---------------------------------------------------------------- text rules

def test_a_letter_spaced_line_keeps_its_spaces():
    """Drawn a character at a time, "SKELON AGENCY" came back as "SKELONAGENCY"."""
    font = ImageFont.truetype(image_compose._ARCHIVO, 40)
    img = Image.new("RGB", (800, 200), "black")

    def draw_spaced():
        d = ImageDraw.Draw(img)
        x = 40
        for ch in "SKELON AGENCY":
            d.text((x, 60), ch, fill="white", font=font)
            x += font.getlength(ch) + 6
        return b""
    cap = lc.capture(draw_spaced, img.size)
    assert [t["text"] for t in cap["texts"]] == ["SKELON AGENCY"]
    assert abs(cap["texts"][0]["tracking"] - 6) < 0.01


def test_words_drawn_one_at_a_time_are_one_line_until_the_colour_changes():
    """A line with one word in the accent colour is drawn word by word. The
    words around it are one line; the coloured word is a piece of its own."""
    font = ImageFont.truetype(image_compose._ARCHIVO, 60)
    img = Image.new("RGB", (1400, 200), "black")
    sp = font.getlength(" ")

    def draw():
        d = ImageDraw.Draw(img)
        x = 20
        for w, fill in (("Clarity", "white"), ("is", "white"), ("a", "white"),
                        ("catalyst", "#c9a24b"), ("today", "white")):
            d.text((x, 50), w, fill=fill, font=font)
            x += font.getlength(w) + round(sp)
        return b""
    cap = lc.capture(draw, img.size)
    assert [t["text"] for t in cap["texts"]] == ["Clarity is a", "catalyst", "today"]


def test_text_drawn_into_a_mask_is_not_a_layer():
    """Only what is drawn on the card is a layer — a glyph mask an effect is
    built from stays part of that effect."""
    font = ImageFont.truetype(image_compose._ARCHIVO, 40)
    card = Image.new("RGB", (400, 300), "black")
    mask = Image.new("L", (120, 60), 0)

    def draw():
        ImageDraw.Draw(mask).text((0, 0), "GLOW", fill=255, font=font)
        ImageDraw.Draw(card).text((10, 10), "CARD", fill="white", font=font)
        return b""
    cap = lc.capture(draw, card.size)
    assert [t["text"] for t in cap["texts"]] == ["CARD"]
    assert mask.getbbox() is not None, "the mask was still drawn"


def test_every_line_carries_the_baseline_it_stands_on():
    font = ImageFont.truetype(image_compose._ANTON, 120)
    card = Image.new("RGB", (800, 400), "black")
    cap = lc.capture(lambda: ImageDraw.Draw(card).text((40, 100), "STAT", fill="white", font=font),
                     card.size)
    t = cap["texts"][0]
    assert t["baseline"] == 100 + font.getmetrics()[0]
    # anchored at the baseline itself, the same line reports the same baseline
    cap2 = lc.capture(lambda: ImageDraw.Draw(card).text((40, t["baseline"]), "STAT", fill="white",
                                                        font=font, anchor="ls"), card.size)
    assert abs(cap2["texts"][0]["baseline"] - t["baseline"]) < 1.0


def test_multiline_text_is_one_layer_per_line():
    font = ImageFont.truetype(image_compose._ARCHIVO, 30)
    card = Image.new("RGB", (400, 300), "black")
    cap = lc.capture(lambda: ImageDraw.Draw(card).multiline_text(
        (10, 10), "ONE\nTWO\nTHREE", fill="white", font=font), card.size)
    assert [t["text"] for t in cap["texts"]] == ["ONE", "TWO", "THREE"]
    ys = [t["y_la"] for t in cap["texts"]]
    assert ys == sorted(ys) and len(set(ys)) == 3


# ------------------------------------------------------- nothing else changes

@pytest.mark.parametrize("fmt", FORMATS)
def test_outside_a_capture_every_card_is_drawn_exactly_as_before(fmt, monkeypatch):
    """With PIL's own text() and paste() put back, the card is byte-identical:
    the hook does nothing at all when no capture is running."""
    call = _designed(fmt, (1080, 1350))
    hooked = _img(call()).convert("RGB").tobytes()
    monkeypatch.setattr(ImageDraw.ImageDraw, "text", lc._orig_text)
    monkeypatch.setattr(Image.Image, "paste", lc._orig_paste)
    plain = _img(call()).convert("RGB").tobytes()
    assert hooked == plain


def test_a_capture_never_touches_a_card_drawn_at_the_same_time():
    """The capture is per context — a card rendered on another thread while one
    is being captured is drawn in full, words and logo included."""
    started, release = threading.Event(), threading.Event()
    out: dict = {}

    def slow_capture():
        def call():
            started.set()
            release.wait(5)
            return _designed("statement", (1080, 1350))()
        out["cap"] = lc.capture(call, (1080, 1350))

    t = threading.Thread(target=slow_capture)
    t.start()
    started.wait(5)
    try:
        normal = _img(_designed("statement", (1080, 1350))())
    finally:
        release.set()
        t.join(10)
    reference = _img(_designed("statement", (1080, 1350))())
    assert normal.convert("RGB").tobytes() == reference.convert("RGB").tobytes()
    assert out["cap"]["texts"] and out["cap"]["images"]


# ------------------------------------------------------------ existing cards

def test_a_card_whose_words_changed_is_not_offered_as_that_card():
    """Re-drawn from a spec that no longer matches the picture, the layers would
    open a different card than the one on the board — the gate refuses it."""
    card = _img(_designed("bold_statement", (1080, 1350))())
    other = dict(SPEC, statement="Something else entirely was said here")
    cap = lc.capture(_designed("bold_statement", (1080, 1350), spec=other), card.size)
    assert lc.drift(cap, card) > lc.MAX_DRIFT


def _store(monkeypatch):
    saved: list = []

    class Store:
        def save(self, tenant, data, name):
            saved.append(name)
            return f"https://cdn.example/{len(saved)}-{name}", f"/tmp/{name}"

    import james_os.media as media
    monkeypatch.setattr(media, "storage", lambda: Store())

    class Conn:
        async def execute(self, *a):
            saved.append(("sql", a[1:]))

    class Acq:
        async def __aenter__(self):
            return Conn()

        async def __aexit__(self, *a):
            return False

    import james_os.db as db
    monkeypatch.setattr(db, "acquire", lambda tenant: Acq())
    return saved


def test_keep_stores_the_plate_and_badges_and_stamps_the_picture(monkeypatch):
    saved = _store(monkeypatch)
    call = _designed("statement", (1080, 1350))
    cap = lc.capture(call, (1080, 1350))
    layers = asyncio.run(lc.keep("a1", "t1", cap, of="https://cdn.example/card.png"))
    assert layers["of"] == "https://cdn.example/card.png"
    assert layers["plate_url"].startswith("https://") and layers["canvas"] == [1080, 1350]
    assert [i["kind"] for i in layers["images"]] == ["logo", "photo"]
    assert all(i["url"].startswith("https://") for i in layers["images"])
    assert all(isinstance(i["z"], int) for i in layers["images"])
    assert all("_chars" not in t and "_font" not in t for t in layers["texts"])
    assert any(isinstance(s, tuple) and s[0] == "sql" for s in saved)


def test_kept_layers_are_served_only_for_the_picture_they_are_of(monkeypatch):
    """A redo drawn on another path, or a swapped photo, leaves the row showing a
    different picture — its old layers must be captured again, not served."""
    calls: list = []

    async def fake_fetch(url):
        calls.append(url)
        return None                          # nothing to re-draw against

    import james_os.template_clone as tc
    monkeypatch.setattr(tc, "_fetch_bytes", fake_fetch)
    kept = {"plate_url": "https://cdn.example/plate.png", "of": "https://cdn.example/a.png"}

    same = asyncio.run(lc.ensure_layers("a1", "t1", {"image_url": "https://cdn.example/a.png",
                                                      "render_layers": kept}))
    assert same is kept and calls == []

    # after an owner's save, image_url is the edit; the layers are of the original
    edited = {"image_url": "https://cdn.example/edit.png",
              "original_image_url": "https://cdn.example/a.png", "render_layers": kept}
    assert asyncio.run(lc.ensure_layers("a1", "t1", edited)) is kept

    other = asyncio.run(lc.ensure_layers("a1", "t1", {"image_url": "https://cdn.example/b.png",
                                                       "render_layers": kept}))
    assert other is None and calls == ["https://cdn.example/b.png"]


# ------------------------------------------------------------------ re-plate

def _other_photo() -> bytes:
    b = BytesIO()
    Image.new("RGB", (1200, 1200), (200, 40, 40)).save(b, "JPEG")
    return b.getvalue()


def test_another_photo_is_drawn_into_the_same_design(monkeypatch):
    """"Change the picture" keeps the frame, the scrim and every line where they
    were — only the photo is different — and writes nothing to the draft."""
    saved = _store(monkeypatch)

    async def ctx(tenant_id, hero):
        return {"kit": KIT, "handle": "skelonagency", "profile_bytes": LOGO,
                "profile_is_logo": True, "tuning": {}, "font_theme": None, "look": None,
                "text_color": "", "text_bold": False}
    monkeypatch.setattr(lc, "_designed_context", ctx)

    async def no_photo(tenant_id, key):
        return PHOTO
    monkeypatch.setattr(lc, "_photo", no_photo)
    payload = {"image_format": "framed_print", "image_spec": SPEC, "hero_photo_key": "k"}

    before = asyncio.run(lc._draw_again("t1", payload, (1080, 1350)))
    after = asyncio.run(lc.replate("t1", payload, _other_photo(), (1080, 1350)))
    assert after is not None and after["of"] == ""
    assert [(t["text"], t["x"], t["y"]) for t in after["texts"]] == \
        [(t["text"], t["x"], t["y"]) for t in before["texts"]]
    assert [i["kind"] for i in after["images"]] == ["photo"]
    assert not any(isinstance(x, tuple) and x[0] == "sql" for x in saved), "the draft was written"


def test_a_design_without_a_photo_has_nothing_to_replate():
    assert not lc.takes_photo({"image_format": "bold_statement", "image_spec": SPEC})
    assert lc.takes_photo({"image_format": "hero_quote", "image_spec": SPEC})
    assert lc.takes_photo({"image_format": "owner_edit",
                           "generated_parts": {"image_spec": dict(SPEC, format="minimal_over")}})
    assert not lc.takes_photo({"clone_spec": {"elements": [{"role": "headline"}],
                                              "background": {"treatment": "solid"}}})
    assert lc.takes_photo({"clone_spec": {"elements": [{"role": "headline"}],
                                          "background": {"treatment": "full_bleed_photo"}}})
    out = asyncio.run(lc.replate("t1", {"image_format": "big_stat", "image_spec": SPEC},
                                 _other_photo(), (1080, 1350)))
    assert out is None


def _row(monkeypatch, status="pending", payload=None):
    import json as _json

    from james_os import api_v1

    class Conn:
        async def fetchrow(self, *a):
            return {"status": status, "payload": _json.dumps(payload or {})}

    class Acq:
        async def __aenter__(self):
            return Conn()

        async def __aexit__(self, *a):
            return False

    monkeypatch.setattr(api_v1, "acquire", lambda tenant: Acq())
    return api_v1


def test_replate_is_for_a_card_still_awaiting_approval(monkeypatch):
    from uuid import uuid4

    from fastapi import HTTPException

    api_v1 = _row(monkeypatch, status="approved",
                  payload={"image_format": "hero_quote", "image_spec": SPEC})
    with pytest.raises(HTTPException) as e:
        asyncio.run(api_v1.v1_post_replate(uuid4(), api_v1.ReplateBody(photo_url="https://x/y.jpg"), "t"))
    assert e.value.status_code == 409


def test_replate_on_a_design_without_a_photo_fetches_nothing(monkeypatch):
    from uuid import uuid4

    api_v1 = _row(monkeypatch, payload={"image_format": "big_stat", "image_spec": SPEC})

    async def boom(url):
        raise AssertionError("fetched a photo for a design that has none")
    monkeypatch.setattr(api_v1, "_fetch_photo", boom)
    out = asyncio.run(api_v1.v1_post_replate(
        uuid4(), api_v1.ReplateBody(photo_url="https://x/y.jpg"), "t"))
    assert out == {"replated": False}


@pytest.mark.parametrize("url", ["http://example.com/a.jpg", "https://127.0.0.1/a.jpg",
                                 "https://169.254.169.254/latest", "file:///etc/passwd"])
def test_replate_only_fetches_public_https_photos(url):
    from fastapi import HTTPException

    from james_os import api_v1

    with pytest.raises(HTTPException) as e:
        asyncio.run(api_v1._fetch_photo(url))
    assert e.value.status_code == 422


# ------------------------------------------------- cards from an older renderer

def _png(im: Image.Image) -> bytes:
    b = BytesIO()
    im.save(b, "PNG")
    return b.getvalue()


def _older_version(cap, dx=5, dy=3) -> bytes:
    """The same card as an earlier renderer drew it: every line a few pixels
    over from where today's renderer puts it — the same words."""
    base = Image.open(BytesIO(cap["plate"])).convert("RGBA")
    shifted = dict(cap, texts=[dict(t, x=t["x"] + dx, y_la=t["y_la"] + dy) for t in cap["texts"]])
    return _png(lc.recompose(dict(shifted, plate=_png(base.convert("RGB")))))


def test_an_old_card_is_cut_into_pieces_of_itself():
    """Redrawn, an old card's lines land a few pixels off and the gate refuses
    it. Cut instead, its pieces put back together ARE the picture."""
    call = _designed("brand_quote", (1080, 1350))
    cap = lc.capture(call, (1080, 1350))
    old = _older_version(cap)
    assert lc.drift(cap, Image.open(BytesIO(old))) > lc.MAX_DRIFT
    cut = lc.dissect(old, cap)
    assert cut is not None and cut["cut"] and cut["texts"] == []
    assert lc.drift(cut, Image.open(BytesIO(old))) < 0.1
    kinds = [p["kind"] for p in cut["images"]]
    assert kinds.count("emblem") == 1 and kinds.count("text") == len(cap["texts"])
    labels = [p["label"] for p in cut["images"] if p["kind"] == "text"]
    assert labels == [t["text"].strip() for t in cap["texts"]]


def test_a_moved_piece_leaves_clean_background_behind():
    call = _designed("brand_quote", (1080, 1350))
    cap = lc.capture(call, (1080, 1350))
    cut = lc.dissect(_older_version(cap), cap)
    plate = Image.open(BytesIO(cut["plate"])).convert("RGB")
    words = _img(call()).convert("RGB")
    # where the words were, the plate is the background — none of them left on it
    t = cut["images"][2]
    box = (t["x"], t["y"], t["x"] + t["w"], t["y"] + t["h"])
    assert plate.crop(box).tobytes() != words.crop(box).tobytes()
    assert Image.open(BytesIO(t["png"])).mode == "RGBA"


def test_a_photo_panel_is_cut_with_its_own_shape():
    call = _designed("framed_print", (1080, 1350))
    cap = lc.capture(call, (1080, 1350))
    cut = lc.dissect(_older_version(cap), cap)
    photo = next(p for p in cut["images"] if p["kind"] == "photo")
    alpha = Image.open(BytesIO(photo["png"])).getchannel("A")
    assert alpha.getpixel((0, 0)) == 0 and alpha.getpixel((photo["w"] // 2, photo["h"] // 2)) == 255
    assert lc.drift(cut, Image.open(BytesIO(_older_version(cap)))) < 0.1


def test_a_card_whose_background_changed_is_not_cut():
    """Cut against a background that is not the card's, the pieces would carry
    boxes of the old background with them — so nothing is offered."""
    call = _designed("brand_quote", (1080, 1350))
    cap = lc.capture(call, (1080, 1350))
    other = dict(cap, plate=_png(Image.new("RGB", (1080, 1350), (230, 230, 230))))
    assert lc.dissect(_older_version(cap), other) is None


def test_lines_keep_their_dots_and_split_what_is_far_apart():
    import numpy as np

    fg = np.zeros((200, 1000), bool)
    fg[40:44, 100:110] = True            # the dot over an i
    fg[52:90, 100:300] = True            # its line
    fg[52:90, 330:420] = True            # the next word, one space along
    fg[52:90, 800:900] = True            # a date at the far right of the row
    fg[140:170, 100:400] = True          # the next line
    boxes = lc._lines(fg)
    assert (100, 40, 420, 90) in boxes, boxes
    assert (800, 52, 900, 90) in boxes and (100, 140, 400, 170) in boxes
    assert len(boxes) == 3


def test_a_photo_layout_that_had_no_photo_is_redrawn_as_it_came_out(monkeypatch):
    """The art director chose a photo layout; there was no photo, so the
    renderer drew the quote card — from the HEADLINE. Redrawn as a quote card
    it came out with the quote's words, and the card was refused."""
    async def ctx(tenant_id, hero):
        return {"kit": KIT, "handle": "skelonagency", "profile_bytes": None,
                "profile_is_logo": False, "tuning": {}, "font_theme": None, "look": None,
                "text_color": "", "text_bold": False}

    async def no_photo(tenant_id, key):
        return None
    monkeypatch.setattr(lc, "_designed_context", ctx)
    monkeypatch.setattr(lc, "_photo", no_photo)
    spec = {"format": "minimal_over", "quote": "Clarity is the catalyst",
            "headline": "Clarity is the catalyst.", "emphasis": ""}
    cap = asyncio.run(lc._draw_again("t1", {"image_format": "brand_quote", "image_spec": spec},
                                     (1080, 1350)))
    assert cap is not None
    assert any(t["text"].strip().endswith("CATALYST.") for t in cap["texts"]), \
        [t["text"] for t in cap["texts"]]


def test_an_old_card_opens_cut_when_its_redraw_is_off(monkeypatch):
    call = _designed("brand_quote", (1080, 1350))
    cap = lc.capture(call, (1080, 1350))
    old = _older_version(cap)
    kept = {}

    async def fetch(url):
        return old

    async def again(tenant_id, payload, canvas, **kw):
        return cap

    async def keep(action_id, tenant_id, c, *, of, drift_=0.0):
        kept.update(c=c, of=of, drift=drift_)
        return {"of": of, "cut": bool(c.get("cut"))}
    import james_os.template_clone as tc
    monkeypatch.setattr(tc, "_fetch_bytes", fetch)
    monkeypatch.setattr(lc, "_draw_again", again)
    monkeypatch.setattr(lc, "keep", keep)
    out = asyncio.run(lc.ensure_layers("a1", "t1", {"image_url": "https://cdn.example/old.png"}))
    assert out == {"of": "https://cdn.example/old.png", "cut": True}
    assert kept["drift"] < 0.1 and kept["c"]["images"]


def test_words_the_redraw_never_placed_are_not_silently_dropped():
    """Words are looked for near the redraw's own lines. A line somewhere else
    entirely would be left out of the pieces — so the card is refused instead
    of opening without it."""
    call = _designed("brand_quote", (1080, 1350))
    cap = lc.capture(call, (1080, 1350))
    old = Image.open(BytesIO(_older_version(cap))).convert("RGB")
    ImageDraw.Draw(old).text((80, 1230), "AN EXTRA LINE", fill="white",
                              font=ImageFont.truetype(image_compose._ANTON, 70))
    assert lc.dissect(_png(old), cap) is None


# ------------------------------------------------------------ carousel slides

DECK = {
    "cover": {"headline": "Five ways clarity wins", "emphasis": "clarity", "eyebrow": "SKELON",
              "count_promise": "5 ways"},
    "slides": [{"kind": "text", "section_label": "ONE", "headline": "Say the thing plainly"},
               {"kind": "stat", "section_label": "TWO", "stat": "87%", "headline": "decide on sight"},
               {"kind": "text", "section_label": "THREE", "headline": "Then say it again"}],
    "cta": {"action": "Ask for the deck", "ask": "DM us"},
}


def _deck_slides():
    from james_os.carousel_text import render_text_carousel

    return render_text_carousel(DECK, KIT, "skelonagency")


def test_a_carousel_is_captured_one_slide_at_a_time():
    """Every slide's own background and its own lines — in the order drawn."""
    slides = _deck_slides()
    size = _img(slides[0]).size
    caps = lc.capture_slides(_deck_slides, size)
    assert len(caps) == len(slides)
    for png, cap in zip(slides, caps):
        assert cap["texts"], "a slide with no lines captured"
        assert lc.drift(cap, _img(png)) < 1.0
    # each slide's lines are its own, not the deck's
    assert [t["text"] for t in caps[0]["texts"]] != [t["text"] for t in caps[1]["texts"]]


def test_capturing_a_deck_leaves_the_slides_it_returns_untouched():
    plain = _deck_slides()
    caps = lc.capture_slides(_deck_slides, _img(plain[0]).size)
    assert len(caps) == len(plain)
    again = _deck_slides()
    assert [_img(p).tobytes() for p in again] == [_img(p).tobytes() for p in plain]


# --------------------------------------------- a picture with no redraw at all

def test_a_slide_is_cut_against_its_own_background():
    """A carousel is drawn from a deck the draft does not keep, so there is
    nothing to redraw it from: its background is rebuilt from the slide itself."""
    slide = _deck_slides()[0]
    cut = lc.dissect_flat(slide)
    assert cut is not None and cut["cut"] and cut["images"]
    assert lc.drift(cut, _img(slide)) < 1.0
    plate = _img(cut["plate"]).convert("L")
    card = _img(slide).convert("L")
    box = cut["images"][-1]
    crop = (box["x"], box["y"], box["x"] + box["w"], box["y"] + box["h"])
    assert plate.crop(crop).tobytes() != card.crop(crop).tobytes(), "a piece was left on the background"


def test_a_photograph_is_not_guessed_at():
    """Rebuilding a background only works where the ground is smooth. A photo
    is refused rather than smeared."""
    b = BytesIO()
    Image.open(BytesIO(PHOTO)).convert("RGB").save(b, "PNG")
    assert lc.dissect_flat(b.getvalue()) is None


def test_which_slides_a_carousel_has():
    now = ["https://cdn/a.png", "https://cdn/b.png"]
    was = ["https://cdn/A.png", "https://cdn/B.png"]
    assert lc.slide_urls({"media_urls": now}) == (now, now)
    assert lc.slide_urls({"media_urls": now, "original_media_urls": was}) == (now, was)
    # a half-written list is ignored rather than mismatched with the slides
    assert lc.slide_urls({"media_urls": now, "original_media_urls": was[:1]}) == (now, now)


def test_a_slides_layers_are_kept_under_that_slide(monkeypatch):
    slide = _deck_slides()[1]
    kept = {}

    async def fetch(url):
        return slide

    async def keep(action_id, tenant_id, cap, *, of, drift_=0.0, slide=None):
        kept.update(of=of, slide=slide, cut=bool(cap.get("cut")))
        return {"of": of, "plate_url": "https://cdn.example/plate.png"}
    import james_os.template_clone as tc
    monkeypatch.setattr(tc, "_fetch_bytes", fetch)
    monkeypatch.setattr(lc, "keep", keep)
    payload = {"media_urls": ["https://cdn/0.png", "https://cdn/1.png"],
               "original_media_urls": ["https://cdn/O0.png", "https://cdn/O1.png"]}

    out = asyncio.run(lc.ensure_layers("a1", "t1", payload, slide=1))
    assert out and kept == {"of": "https://cdn/O1.png", "slide": 1, "cut": True}
    assert asyncio.run(lc.ensure_layers("a1", "t1", payload, slide=5)) is None

    # kept once, served from the draft after that
    payload["render_layers_slides"] = {"1": {"plate_url": "https://cdn/p.png", "of": "https://cdn/O1.png"}}
    again = asyncio.run(lc.ensure_layers("a1", "t1", payload, slide=1))
    assert again == payload["render_layers_slides"]["1"]


def _carousel_row(monkeypatch, status="pending", n=3, extra=None):
    """A carousel draft behind the endpoints, with a conn that records writes."""
    import json as _json

    from james_os import api_v1

    payload = {"image_format": "carousel", "caption": "A deck.",
               "media_urls": [f"https://cdn.example/s{i}.png" for i in range(n)],
               "image_url": "https://cdn.example/s0.png", **(extra or {})}
    wrote = []

    class Conn:
        async def fetchrow(self, *a):
            return {"status": status, "payload": _json.dumps(payload),
                    "image_url": payload.get("image_url"), "original_image_url": None}

        async def execute(self, sql, *args):
            wrote.append((sql, args))
            return "UPDATE 1"

    class Acq:
        async def __aenter__(self):
            return Conn()

        async def __aexit__(self, *a):
            return False

    monkeypatch.setattr(api_v1, "acquire", lambda tenant: Acq())
    return api_v1, payload, wrote


def test_one_slide_of_a_carousel_opens_as_a_card_of_its_own(monkeypatch):
    from uuid import uuid4

    api_v1, payload, _ = _carousel_row(
        monkeypatch, extra={"original_media_urls": [f"https://cdn.example/O{i}.png" for i in range(3)],
                            "edit_layers_slides": {"1": {"version": 1, "mode": "layers"}}})

    async def layers(action_id, tenant_id, p, slide=None):
        return {"plate_url": "https://cdn.example/plate.png", "of": p["original_media_urls"][slide]}
    monkeypatch.setattr("james_os.layer_capture.ensure_layers", layers)

    out = asyncio.run(api_v1.v1_post_layers(uuid4(), "t", slide=1))
    assert out["is_carousel"] is False and out["slide"] == 1 and out["slide_count"] == 3
    assert out["image_url"] == "https://cdn.example/s1.png"
    assert out["original_image_url"] == "https://cdn.example/O1.png"
    assert out["saved"] == {"version": 1, "mode": "layers"}
    assert out["render_layers"]["of"] == "https://cdn.example/O1.png"

    whole = asyncio.run(api_v1.v1_post_layers(uuid4(), "t"))
    assert whole["is_carousel"] is True and whole["render_layers"] is None and "slide" not in whole


@pytest.mark.parametrize("slide", [3, -1])
def test_a_slide_that_is_not_in_the_deck_is_refused(monkeypatch, slide):
    from uuid import uuid4

    from fastapi import HTTPException

    api_v1, _p, _w = _carousel_row(monkeypatch)
    with pytest.raises(HTTPException) as e:
        asyncio.run(api_v1.v1_post_layers(uuid4(), "t", slide=slide))
    assert e.value.status_code == 404


def test_saving_a_slide_replaces_that_slide_only(monkeypatch):
    from uuid import uuid4

    api_v1, _p, wrote = _carousel_row(monkeypatch)

    class Up:
        filename, content_type = "edited.png", "image/png"

        async def read(self):
            return b"\x89PNG\r\n\x1a\n" + b"\x00" * 64

    class Store:
        def save(self, tenant, data, name):
            return "https://cdn.example/edited-slide.png", "/tmp/x.png"
    monkeypatch.setattr("james_os.media.storage", lambda: Store())

    out = asyncio.run(api_v1.v1_post_set_image(uuid4(), "t", file=Up(), doc="{}", slide=2))
    assert out == {"ok": True, "id": out["id"], "image_url": "https://cdn.example/edited-slide.png",
                   "slide": 2}
    sql, args = wrote[-1]
    assert "ARRAY['media_urls', $3::text]" in sql and "original_media_urls" in sql
    assert args[1] == "https://cdn.example/edited-slide.png" and args[2] == "2"
    patch = json.loads(args[4])
    assert patch["image_edited_by_owner"] is True and "image_url" not in patch, \
        "only the cover is mirrored into image_url"
    assert args[5] == "", "the cover's per-network renders are only dropped when the cover changes"


def test_saving_the_cover_mirrors_it_and_drops_the_stale_shapes(monkeypatch):
    from uuid import uuid4

    api_v1, _p, wrote = _carousel_row(monkeypatch)

    class Up:
        filename, content_type = "edited.png", "image/png"

        async def read(self):
            return b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
    monkeypatch.setattr("james_os.media.storage",
                        lambda: type("S", (), {"save": lambda self, t, d, n: ("https://cdn.example/c.png", "/tmp/c")})())

    asyncio.run(api_v1.v1_post_set_image(uuid4(), "t", file=Up(), doc="{}", slide=0))
    _sql, args = wrote[-1]
    patch = json.loads(args[4])
    assert patch["image_url"] == patch["media_url"] == "https://cdn.example/c.png"
    assert args[5] == "image_urls_by_size"


# ------------------------------------------------- a cut piece, back into words

def _piece_of(text, face, size, fill="#f4f6f8", tracking=0.0, at=(120, 300)):
    """A cut piece as dissect makes one: the words on transparency, and its box."""
    font = ImageFont.truetype(face, size)
    tile = Image.new("RGBA", (1080, 400), (0, 0, 0, 0))
    d = ImageDraw.Draw(tile)
    if tracking:
        x = 20.0
        for ch in text:
            lc._orig_text(d, (x, 60), ch, fill, font, None)
            x += font.getlength(ch) + tracking
    else:
        lc._orig_text(d, (20, 60), text, fill, font, None)
    box = tile.getbbox()
    piece = tile.crop(box)
    b = BytesIO()
    piece.save(b, "PNG")
    return b.getvalue(), {"x": at[0], "y": at[1], "w": piece.width, "h": piece.height,
                          "kind": "text", "url": "https://cdn.example/piece.png"}


def _ink_at(rec, size_px=(1080, 900)):
    """Where the returned line's letters actually land when it is set."""
    canvas = Image.new("RGB", size_px, "black")
    d = ImageDraw.Draw(canvas)
    font = ImageFont.truetype(rec["font_path"], rec["size"])
    top = rec["baseline"] - font.getmetrics()[0]
    if rec["tracking"]:
        x = rec["x"]
        for ch in rec["text"]:
            lc._orig_text(d, (x, top), ch, "#ffffff", font, None)
            x += font.getlength(ch) + rec["tracking"]
    else:
        lc._orig_text(d, (rec["x"], top), rec["text"], "#ffffff", font, None)
    return canvas.convert("L").point(lambda v: 255 if v > 40 else 0).getbbox()


def test_a_piece_becomes_a_line_that_stands_where_it_stood():
    png, box = _piece_of("Skeleton Makes", image_compose._ARCHIVO, 96)
    rec = lc.as_words(png, "Skeleton Makes", box, None)
    assert rec is not None
    assert rec["family"] == "Archivo Black" and abs(rec["size"] - 96) <= 2
    l, t, r, b = _ink_at(rec)
    assert abs(l - box["x"]) <= 2 and abs(t - box["y"]) <= 2, (l, t, box)
    assert abs((r - l) - box["w"]) <= 4 and abs((b - t) - box["h"]) <= 3


def test_the_letter_spacing_of_a_kicker_comes_back():
    png, box = _piece_of("SKELON AGENCY", image_compose._ARCHIVO, 30, tracking=8.0)
    rec = lc.as_words(png, "SKELON AGENCY", box, None)
    assert rec is not None and abs(rec["tracking"] - 8.0) < 1.5
    l, _t, r, _b = _ink_at(rec)
    assert abs((r - l) - box["w"]) <= 4, "a tracked line must come back the same width"


def test_the_colour_of_the_words_comes_back():
    png, box = _piece_of("Branding", image_compose._ARCHIVO, 80, fill="#c8a46b")
    rec = lc.as_words(png, "Branding", box, None)
    assert rec is not None and rec["fill"].lower() == "#c8a46b"


def test_a_piece_with_no_words_stays_a_piece():
    bar = Image.new("RGBA", (120, 8), (200, 164, 107, 255))
    b = BytesIO()
    bar.save(b, "PNG")
    box = {"x": 90, "y": 400, "w": 120, "h": 8}
    assert lc.as_words(b.getvalue(), "", box, None) is None


def test_the_face_is_chosen_by_how_the_letters_actually_set():
    """Two faces of very different width: the one the piece was set in wins."""
    png, box = _piece_of("HANDLE", image_compose._ANTON, 90)
    rec = lc.as_words(png, "HANDLE", box, None)
    assert rec is not None and rec["family"] == "Anton", rec["family"]


def test_only_a_piece_of_this_card_can_be_read(monkeypatch):
    from uuid import uuid4

    from fastapi import HTTPException

    api_v1, _payload, _wrote = _carousel_row(
        monkeypatch, extra={"render_layers": {"images": [{"url": "https://cdn.example/mine.png",
                                                          "x": 1, "y": 2, "w": 3, "h": 4}]}})
    with pytest.raises(HTTPException) as e:
        asyncio.run(api_v1.v1_post_piece_words(
            uuid4(), api_v1.PieceBody(url="https://cdn.example/someone-elses.png"), "t"))
    assert e.value.status_code == 404


def test_the_words_of_a_piece_are_read_once(monkeypatch):
    from uuid import uuid4

    kept = {"words": "READ ONCE", "line": {"text": "READ ONCE", "size": 40}}
    api_v1, _payload, _wrote = _carousel_row(
        monkeypatch, extra={"render_layers": {"images": [{"url": "https://cdn.example/mine.png",
                                                          "x": 1, "y": 2, "w": 3, "h": 4}]},
                            "piece_words": {"https://cdn.example/mine.png": kept}})

    async def never(png):
        raise AssertionError("the words were read again")
    monkeypatch.setattr(lc, "read_words", never)

    out = asyncio.run(api_v1.v1_post_piece_words(
        uuid4(), api_v1.PieceBody(url="https://cdn.example/mine.png"), "t"))
    assert out == kept


# --------------------------------------------------- a card that will not cut

def test_a_card_that_cannot_be_cut_is_only_tried_once(monkeypatch):
    """Capturing is three to five seconds of redrawing and comparing. A card
    that will not come apart will not come apart next time either — and without
    a note, the editor paid that on EVERY open of the same card, forever. Seven
    of the sixty-four cards in the queue were doing exactly that on 2026-09-15.

    A CRASH is deliberately not noted: that can be a storage blip, and a note
    would make one bad minute permanent for the card."""
    png = BytesIO()
    Image.new("RGB", (1080, 1350), "white").save(png, format="PNG")
    payload = {"image_url": "https://example.test/card.png"}
    noted: list = []
    tries: list = []

    async def fake_bytes(url):
        return png.getvalue()

    async def wont_draw(tenant_id, p, canvas):
        tries.append(canvas)
        return None  # redrew and there was nothing to cut

    async def fake_note(action_id, tenant_id, of):
        noted.append(of)
        payload["no_layers_for"] = of
        return None

    monkeypatch.setattr("james_os.template_clone._fetch_bytes", fake_bytes)
    monkeypatch.setattr(lc, "_draw_again", wont_draw)
    monkeypatch.setattr(lc, "_no_layers", fake_note)

    first = asyncio.run(lc.ensure_layers("act-1", "ten-1", payload))
    second = asyncio.run(lc.ensure_layers("act-1", "ten-1", payload))

    assert first is None and second is None
    assert noted == ["https://example.test/card.png"]
    assert len(tries) == 1, "the second open redrew the card again"


def test_a_redrawn_card_is_tried_afresh(monkeypatch):
    """The note is keyed by the picture it was of, so a card whose picture
    changes — a redraw, a swapped photo — gets its chance again."""
    png = BytesIO()
    Image.new("RGB", (1080, 1350), "white").save(png, format="PNG")
    payload = {"image_url": "https://example.test/one.png",
               "no_layers_for": "https://example.test/one.png"}
    tries: list = []

    async def fake_bytes(url):
        return png.getvalue()

    async def wont_draw(tenant_id, p, canvas):
        tries.append(canvas)
        return None

    monkeypatch.setattr("james_os.template_clone._fetch_bytes", fake_bytes)
    monkeypatch.setattr(lc, "_draw_again", wont_draw)
    monkeypatch.setattr(lc, "_no_layers", lambda *a, **k: _none())

    async def _none():
        return None

    assert asyncio.run(lc.ensure_layers("act-2", "ten-1", payload)) is None
    assert tries == [], "a noted card was redrawn anyway"

    payload["image_url"] = "https://example.test/two.png"
    assert asyncio.run(lc.ensure_layers("act-2", "ten-1", payload)) is None
    assert len(tries) == 1, "a card with a NEW picture was not tried"
