"""Route an art-director spec to the right compositor — the generation core.

`direct_designed_image` (imagegen) decides a FORMAT + copy; this renders it.
It spans the shipped navy cards (brand_quote/hero_quote/statement) AND the v2
palette-aware layouts (full_bleed/editorial_split/big_stat/minimal_over/
framed_print), picks a fallback when a photo layout has no photo (so a render
never fails), and threads the brand PALETTE through every layout so a post comes
out in the brand's colours, not James's navy.

Pure and DB-free on purpose: everything it needs is passed in, so the routing
can be unit-tested against fixtures with no tenant, storage, or LLM.
"""

from __future__ import annotations

from . import compositors_v2 as cv
from .image_compose import bold_statement_card, brand_quote_card, hero_quote_card, statement_card

# Formats that need the brand's real photo. big_stat + brand_quote + bold_statement
# are text-only.
PHOTO_FORMATS = frozenset({
    "hero_quote", "statement", "full_bleed", "editorial_split", "minimal_over", "framed_print",
})
ALL_FORMATS = frozenset({
    "brand_quote", "hero_quote", "statement", "bold_statement",
    "full_bleed", "editorial_split", "big_stat", "minimal_over", "framed_print",
})

# Absolute last-resort palette when a render reaches this pure module with NO
# palette on the kit at all (the tenant-aware layer normally resolves a
# brand-specific palette upstream via ensure_brand_palette). Deliberately a
# NEUTRAL charcoal system — never James's navy — so a fallback can never stamp
# one brand's colours onto another. A real brand palette always overrides this.
_NEUTRAL_ROLES = [
    {"role": "background", "hex": "#14161A"},
    {"role": "ink", "hex": "#F4F6F8"},
    {"role": "accent", "hex": "#C8A46B"},
    {"role": "surface", "hex": "#242832"},
]


def needs_photo(fmt: str) -> bool:
    return fmt in PHOTO_FORMATS


def _v2_palette(kit: dict | None, palette) -> dict:
    """The palette dict the v2 compositors expect. A role-list (brand_identity's
    shape) or a flat {bg,ink,accent} both work; falls back to the brand kit's
    palette, then to a NEUTRAL floor (never James's navy) so a missing palette
    can never leak one brand's colours onto another."""
    if isinstance(palette, dict) and (palette.get("palette") or palette.get("bg")):
        return palette
    if isinstance(palette, list) and palette:
        return {"palette": palette}
    kit = kit or {}
    if isinstance(kit.get("palette"), list) and kit["palette"]:
        return {"palette": kit["palette"]}
    if isinstance(kit.get("colors"), dict) and kit["colors"]:
        return {"palette": [{"role": r, "hex": kit["colors"].get(k)}
                            for k, r in (("bg", "background"), ("ink", "ink"),
                                         ("accent", "accent"), ("surface", "surface"))
                            if kit["colors"].get(k)]}
    return {"palette": _NEUTRAL_ROLES}


def render_designed(
    fmt: str, spec: dict, *, kit: dict | None = None, hero_bytes: bytes | None = None,
    profile_bytes: bytes | None = None, profile_is_logo: bool = False,
    handle: str = "", tuning: dict | None = None, palette=None,
) -> tuple[bytes, str]:
    """Render `spec` in `fmt`. Returns (png_bytes, fmt_actually_used) — the used
    format can differ from the request when a photo layout falls back to a text
    card because no photo was available."""
    kit = kit or {}
    q = (spec.get("quote") or "").strip()
    emph = (spec.get("emphasis") or "").strip()
    headline = (spec.get("headline") or spec.get("quote") or "").strip()
    kicker = (spec.get("kicker") or "").strip()
    pal = _v2_palette(kit, palette)

    # shipped navy cards (already palette-aware via kit)
    if fmt == "brand_quote":
        return brand_quote_card(q, kit, emph), "brand_quote"
    if fmt == "bold_statement":
        # text-only statement poster (no photo): brand name up top, big bold
        # statement with the emphasis phrase highlighted inline, byline at the foot.
        return bold_statement_card(spec.get("statement") or q, kit, emph,
                                   byline_name=str(spec.get("byline_name") or "")), "bold_statement"
    if fmt == "hero_quote":
        if hero_bytes:
            return hero_quote_card(q, hero_bytes, kit, emphasis=emph, tuning=tuning), "hero_quote"
        return brand_quote_card(q, kit, emph), "brand_quote"
    if fmt == "statement":
        if hero_bytes:
            return statement_card(hero_bytes, spec.get("statement") or q, handle,
                                  profile_bytes, profile_is_logo), "statement"
        return brand_quote_card((spec.get("statement") or q), kit, emph), "brand_quote"

    # text-only v2
    if fmt == "big_stat":
        return cv.big_stat(spec.get("stat") or "", spec.get("stat_label") or headline or q,
                           spec.get("stat_sub") or "", handle, pal), "big_stat"

    # photo-required v2 — fall back to a text card if no photo
    if fmt in ("full_bleed", "editorial_split", "minimal_over", "framed_print"):
        if not hero_bytes:
            return brand_quote_card(headline or q, kit, emph), "brand_quote"
        if fmt == "full_bleed":
            return cv.full_bleed(hero_bytes, headline or q, kicker, handle, pal), "full_bleed"
        if fmt == "editorial_split":
            return cv.editorial_split(hero_bytes, headline or q, kicker, handle, pal), "editorial_split"
        if fmt == "minimal_over":
            return cv.minimal_over(hero_bytes, headline or q, kicker, handle, pal), "minimal_over"
        if fmt == "framed_print":
            return cv.framed_print(hero_bytes, spec.get("caption") or headline or q,
                                   kicker, handle, pal), "framed_print"

    # unknown → safe text card
    return brand_quote_card(q or headline, kit, emph), "brand_quote"


__all__ = ["render_designed", "needs_photo", "PHOTO_FORMATS", "ALL_FORMATS"]
