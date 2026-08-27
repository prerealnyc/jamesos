"""Premium text-only (photoless) carousel — the brand's "locked visual system":
a deep radial-glow ground, the brand name letter-spaced across the top, a big
bold statement with its KEY PHRASE highlighted in the brand accent, huge stat
slides (one giant number + a centred line), and an `N / total` index. Every
colour comes from the brand's own palette (`_colors`), so it never stamps one
brand's blue onto another — the typographic counterpart of `bold_statement_card`,
extended across a whole deck.

Three slide archetypes, one shared frame:
  * cover / cta  — a short accent rule + a big left-aligned headline (cover adds a
                   small eyebrow at the foot; cta adds the offer line + website),
  * text         — a headline set large and centred, key phrase in the accent,
  * stat         — one huge accent number with a centred supporting line + source.

`render_text_carousel(deck, brand_kit, handle)` returns list[bytes] — one
1080x1350 PNG per slide, in order (cover → inners → cta), capped at 10.
"""

from __future__ import annotations

import os

from PIL import ImageDraw

from .image_compose import (H, W, _colors, _emph_word_idx, _fit, _fit_left,
                            _fit_one_line, _font, _line_h, _navy_bg, _png, _spaced,
                            _spaced_fit, _spaced_w, _text_w, _wrap_idx)

# The face is Archivo Black — the SAME house font as the bold_statement poster
# (image_compose), so a text carousel and a poster read as one brand. Archivo Black is
# a single heavy weight, so the hierarchy comes from SIZE + the accent colour (exactly
# like the poster), not from separate weights: all three aliases point at it.
# (Was Montserrat, which made carousels look like a different brand than the posters.)
_FONT_DIR = os.path.join(os.path.dirname(__file__), "assets", "fonts")
_ARCHIVO = os.path.join(_FONT_DIR, "ArchivoBlack-Regular.ttf")
_MONT_XB = _ARCHIVO  # big statements + stats
_MONT_BD = _ARCHIVO  # captions / website
_MONT_SB = _ARCHIVO  # small letter-spaced labels (brand name, source, index, eyebrow)

M = 96  # side margin


# ── auto-emphasis: colour a phrase even when the model didn't name one ──
def _auto_emphasis(headline: str) -> str:
    """Pick a phrase to highlight when none was given: the closing clause after
    the last sentence break (a punchy landing), else the last few words."""
    h = (headline or "").strip()
    if not h:
        return ""
    for sep in (". ", "? ", "! ", " — ", " – "):
        if sep in h[:-1]:
            tail = h.rsplit(sep, 1)[1].strip()
            if 1 <= len(tail.split()) <= 6:
                return tail
    words = h.split()
    return " ".join(words[-min(3, len(words)):]) if len(words) > 3 else ""


# ── shared frame: brand name top, N / total index, optional source ──
def _frame(d, pal, name, index, total, source=""):
    if name:
        _spaced_fit(d, 84, name.upper(), _MONT_SB, 30, pal["accent"], 8, W - 2 * M, center=W // 2)
    itxt = f"{index}  /  {total}"
    inf = _font(_MONT_SB, 25)
    d.text((W - M - _text_w(d, itxt, inf), H - 104), itxt, font=inf, fill=pal["muted"])
    if source:
        # shrink a long citation to one line so it can't run off the right edge
        srcf = _fit_one_line(d, source, _MONT_SB, W - 2 * M, start=22, floor=12)
        d.text((M, H - 100), source, font=srcf, fill=pal["muted"])


def _headline(d, pal, headline, emphasis, top, bottom, *, center_v):
    """Big left-aligned statement, fit to the zone, KEY PHRASE inline in accent."""
    words = [w for w in (headline or "").split() if w]
    if not words:
        return
    emph = _emph_word_idx(words, emphasis or "") or _emph_word_idx(words, _auto_emphasis(headline))
    font = _fit_left(d, words, _MONT_XB, W - 2 * M, bottom - top, start=126, minimum=42)
    space = _text_w(d, " ", font)
    lh = _line_h(d, font, 1.2)
    lines = _wrap_idx(d, words, font, W - 2 * M)
    y = top + max(0, (bottom - top - lh * len(lines)) / 2) if center_v else top
    for ln in lines:
        x = M
        for w, gi in ln:
            d.text((x, y), w, font=font, fill=(pal["accent"] if gi in emph else pal["ink"]))
            x += _text_w(d, w, font) + space
        y += lh


# ── archetypes ──
def text_slide(pal, name, index, total, headline, emphasis="", source="") -> bytes:
    base = _navy_bg((0.5, 0.44), 560, glow_color=pal["glow"], base_color=pal["base"])
    d = ImageDraw.Draw(base)
    _headline(d, pal, headline, emphasis, 296, H - 250, center_v=True)
    _frame(d, pal, name, index, total, source)
    return _png(base)


def stat_slide(pal, name, index, total, stat, caption, source="") -> bytes:
    base = _navy_bg((0.5, 0.40), 520, glow_color=pal["glow"], base_color=pal["base"])
    d = ImageDraw.Draw(base)
    # the stat is one unspaceable token — fit to width on ONE line (shrink, never
    # char-break a number) so the giant figure can never slice off either edge.
    sf = _fit_one_line(d, (stat or "").upper(), _MONT_XB, W - 2 * M, start=360)
    sl = [(stat or "").upper()]
    slh = _line_h(d, sf, 1.0)
    sy = int(H * 0.33) - slh * len(sl)
    for ln in sl:
        d.text(((W - _text_w(d, ln, sf)) / 2, sy), ln, font=sf, fill=pal["accent"])
        sy += slh
    cf, cl = _fit(d, caption or "", _MONT_BD, int(W * 0.80), int(H * 0.36), start=58, minimum=32)
    clh = _line_h(d, cf, 1.3)
    cy = int(H * 0.50)
    for ln in cl:
        d.text(((W - _text_w(d, ln, cf)) / 2, cy), ln, font=cf, fill=pal["ink"])
        cy += clh
    _frame(d, pal, name, index, total, source)
    return _png(base)


def cover_slide(pal, name, index, total, headline, emphasis="", eyebrow="",
                cta=None) -> bytes:
    """Cover (eyebrow at the foot) or CTA (`cta`=(offer_line, website))."""
    base = _navy_bg((0.44, 0.40), 560, glow_color=pal["glow"], base_color=pal["base"])
    d = ImageDraw.Draw(base)
    ry = 300
    d.rectangle((M, ry, M + 150, ry + 7), fill=pal["accent"])
    _headline(d, pal, headline, emphasis, ry + 66, H - 300, center_v=False)
    if cta:
        offer, site = cta
        yb = H - 250
        if offer:
            of_, ol = _fit(d, offer, _MONT_BD, W - 2 * M, 130, start=42, minimum=24)
            for ln in ol:
                d.text((M, yb), ln, font=of_, fill=pal["ink"])
                yb += _line_h(d, of_, 1.28)
        if site:
            sitef = _fit_one_line(d, site, _MONT_BD, W - 2 * M, start=40, floor=18)
            d.text((M, yb + 8), site, font=sitef, fill=pal["accent"])
    elif eyebrow:
        _spaced_fit(d, H - 150, eyebrow.upper(), _MONT_SB, 23, pal["accent"], 5, W - 2 * M, left=M)
    _frame(d, pal, name, index, total, "")
    return _png(base)


# ── orchestrator ──
def render_text_carousel(deck: dict, brand_kit: dict | None = None, handle: str = "") -> list[bytes]:
    """Render a photoless deck to the premium ordered slide set (cover → inners →
    cta). deck = {cover:{headline,emphasis,eyebrow}, slides:[{kind:'text'|'stat',
    headline,emphasis,stat,caption,source}], cta:{headline,emphasis,ask}}."""
    pal = _colors(brand_kit)
    bk = brand_kit or {}
    name = (bk.get("display_name") or "").strip()
    website = (bk.get("website") or handle or "").strip()

    slides = list(deck.get("slides") or [])[:8]
    total = 2 + len(slides)
    out: list[bytes] = []

    cover = deck.get("cover") or {}
    out.append(cover_slide(pal, name, 1, total, cover.get("headline", ""),
                           cover.get("emphasis", ""),
                           eyebrow=(cover.get("eyebrow") or cover.get("kicker") or "")))
    for i, s in enumerate(slides, start=2):
        if str(s.get("kind")) == "stat" and (s.get("stat") or "").strip():
            out.append(stat_slide(pal, name, i, total, s.get("stat", ""),
                                  s.get("caption") or s.get("headline", ""),
                                  s.get("source", "")))
        else:
            out.append(text_slide(pal, name, i, total, s.get("headline", ""),
                                  s.get("emphasis", ""), s.get("source", "")))
    cta = deck.get("cta") or {}
    cta_headline = cta.get("headline") or cover.get("headline", "")
    out.append(cover_slide(pal, name, total, total, cta_headline, cta.get("emphasis", ""),
                           cta=(cta.get("ask", ""), website)))
    return out


__all__ = ["render_text_carousel", "cover_slide", "text_slide", "stat_slide"]
