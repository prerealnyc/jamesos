"""Brand identity engine — read, judge, and (if needed) design a brand's look.

The design eye (design_eye.py) grades ONE post. This works at the identity
level across the brand's OWN posts:

  1. extract_palette()  — the real, pixel-level colors a brand actually uses
     (deterministic, no LLM): "the branding used so far".
  2. assess_and_propose() — one vision+LLM pass that reads several of the
     brand's posts, judges whether there IS a coherent identity and whether it
     is any good (consistency + distinctiveness), and returns a verdict:
        codify  → the look is strong; lock it in as the brand kit
        tune    → the look is okay but weak; propose a sharper palette/theme
        rebuild → no real identity; design one from scratch (from niche/voice)
     When tune/rebuild, it returns a concrete proposed_theme (palette with color
     ROLES, a type direction, and 3–4 principles).
  3. render_palette_card() — renders a palette + type + principles into an
     actual visual THEME card (the "color designs") the owner can look at.

Honesty: the numeric scores are computed HERE from the model's sub-judgments
(D2), never model-reported. No OpenAI key → status='no_key', extraction still
works (it is pure Pillow). The proposed theme is a brand-agnostic design, never
a copy of any competitor's assets.
"""

from __future__ import annotations

import base64
from io import BytesIO

from PIL import Image, ImageDraw

from .config import settings

# Reuse the shipped typography so a rendered theme card looks native.
from .image_compose import _ARCHIVO, _font  # noqa: E402


def extract_palette(image_bytes: bytes, k: int = 6) -> list[dict]:
    """The brand's real colors, most-used first: [{hex, proportion}]. Pure
    Pillow adaptive-quantize on a downscaled copy — cheap, deterministic, no
    network. This is how we 'catch the branding used so far'."""
    try:
        im = Image.open(BytesIO(image_bytes)).convert("RGB")
    except Exception:  # noqa: BLE001 — an undecodable image yields no palette, never a crash
        return []
    im.thumbnail((220, 220))
    q = im.quantize(colors=max(2, min(12, k)), method=Image.Quantize.MEDIANCUT)
    pal = q.getpalette() or []
    counts = q.getcolors() or []
    total = sum(c for c, _ in counts) or 1
    out = []
    for count, idx in sorted(counts, key=lambda t: t[0], reverse=True):
        r, g, b = pal[idx * 3: idx * 3 + 3]
        out.append({"hex": "#%02x%02x%02x" % (r, g, b), "proportion": round(count / total, 3)})
    return out


def _client():
    from openai import AsyncOpenAI
    key = (settings.openai_api_key or "").strip()
    return AsyncOpenAI(api_key=key) if key else None


def _score(*vals) -> float:
    nums = []
    for v in vals:
        try:
            nums.append(max(0.0, min(100.0, float(v))))
        except (TypeError, ValueError):
            continue
    return round(sum(nums) / len(nums), 1) if nums else 0.0


_SYSTEM = (
    "You are a brand identity director. You are shown SEVERAL of ONE brand's own "
    "posts (plus the dominant colors auto-extracted from them and the brand's "
    "context) and must judge the brand's VISUAL IDENTITY as it stands, then, if "
    "it is weak or absent, DESIGN a better one. Be candid — most small brands "
    "have an inconsistent, undistinctive look.\n\n"
    "Judge two things 0–100 with a one-line reason each:\n"
    "  consistency — do the posts share a deliberate, repeatable system (palette, "
    "type, layout), or is every post a different look?\n"
    "  distinctiveness — is the look ownable and memorable, or generic / "
    "template-ish / like everyone else in the niche?\n\n"
    "Then set verdict:\n"
    "  'codify'  — identity is strong AND consistent (both >=70): keep it, lock "
    "it in.\n"
    "  'tune'    — there is a recognisable look but it is weak (drop-outs in "
    "contrast, muddy palette, ok-not-great): propose a SHARPER version that keeps "
    "what is ownable.\n"
    "  'rebuild' — there is no coherent identity (or no posts): design one from "
    "scratch that fits the brand's niche and voice.\n\n"
    "ALWAYS return a proposed_theme (for 'codify' it is the cleaned-up current "
    "look). proposed_theme.palette must give 4–5 colors each with a ROLE so it "
    "can drive a layout engine: roles from [background, ink, accent, secondary, "
    "surface]; hex values; a short human label. Ensure the background/ink pair "
    "is high-contrast and accessible. Also give type_direction (a concrete type "
    "pairing/treatment, e.g. 'heavy condensed sans display + humanist sans body, "
    "tight all-caps headlines') and 3–4 short principles (imperative rules, e.g. "
    "'one accent color, never two', 'faces bleed to the edge').\n\n"
    "Return STRICT JSON: {\"has_branding\": bool, \"identity_summary\": str, "
    "\"consistency\": {\"score\": int, \"reason\": str}, \"distinctiveness\": "
    "{\"score\": int, \"reason\": str}, \"verdict\": \"codify\"|\"tune\"|"
    "\"rebuild\", \"critique\": str, \"proposed_theme\": {\"name\": str, "
    "\"palette\": [{\"role\": str, \"hex\": str, \"label\": str}], "
    "\"type_direction\": str, \"principles\": [str]}}. Never invent posts you "
    "were not shown; if given zero images, has_branding=false and rebuild."
)


async def assess_and_propose(images: list[bytes], brand_context: dict | None = None) -> dict:
    """Read the brand's own posts and return an identity verdict + a proposed
    theme. `brand_context` may carry name/niche/positioning/voice to ground a
    rebuild. Returns status='no_key' (still includes the extracted palettes) if
    no vision is configured — extraction is pure and always runs."""
    ctx = brand_context or {}
    observed = [extract_palette(b) for b in images]
    client = _client()
    if client is None:
        return {"status": "no_key", "observed_palettes": observed}

    import json as _json
    content = [{"type": "text", "text":
        "Brand context: " + _json.dumps({
            "name": ctx.get("name", ""), "niche": ctx.get("niche", ""),
            "positioning": ctx.get("positioning", ""), "voice": ctx.get("voice", ""),
        }) + "\nAuto-extracted dominant colors per post (most-used first): "
        + _json.dumps(observed)
        + "\nThe brand's posts follow. Judge the identity and propose a theme."}]
    for b in images[:5]:
        content.append({"type": "image_url", "image_url": {
            "url": f"data:image/png;base64,{base64.b64encode(b).decode()}", "detail": "high"}})

    try:
        resp = await client.chat.completions.create(
            model="gpt-4o",
            messages=[{"role": "system", "content": _SYSTEM},
                      {"role": "user", "content": content}],
            max_tokens=1100, temperature=0.2, response_format={"type": "json_object"},
        )
        out = _json.loads(resp.choices[0].message.content or "{}")
    except Exception as exc:  # noqa: BLE001
        return {"status": "failed", "observed_palettes": observed, "error": str(exc)[:200]}

    cons = (out.get("consistency") or {}).get("score")
    dist = (out.get("distinctiveness") or {}).get("score")
    return {
        "status": "ok",
        "observed_palettes": observed,
        "has_branding": bool(out.get("has_branding")),
        "identity_summary": str(out.get("identity_summary") or "").strip(),
        "consistency": out.get("consistency") or {},
        "distinctiveness": out.get("distinctiveness") or {},
        "identity_score": _score(cons, dist),  # computed here (D2)
        "verdict": out.get("verdict") or "rebuild",
        "critique": str(out.get("critique") or "").strip(),
        "proposed_theme": out.get("proposed_theme") or {},
    }


# ── rendering the theme so the owner can SEE it ─────────────────────────

def _hex(s: str, fallback=(20, 20, 24)) -> tuple:
    s = (s or "").strip().lstrip("#")
    try:
        return (int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16))
    except (ValueError, IndexError):
        return fallback


def _readable_on(bg: tuple) -> tuple:
    # WCAG-ish luminance pick for label text over a swatch.
    lum = 0.299 * bg[0] + 0.587 * bg[1] + 0.114 * bg[2]
    return (17, 17, 20) if lum > 150 else (245, 245, 248)


def render_palette_card(theme: dict, subtitle: str = "") -> bytes:
    """Render a proposed/observed theme (palette with roles + type direction +
    principles) into a 1080x1350 visual board the owner can look at."""
    W, H = 1080, 1350
    palette = theme.get("palette") or []
    bg = next((p["hex"] for p in palette if p.get("role") == "background"), None)
    ink = next((p["hex"] for p in palette if p.get("role") == "ink"), None)
    BG = _hex(bg, (18, 20, 30))
    INK = _hex(ink, _readable_on(BG))

    card = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(card)
    M = 84
    d.text((M, 70), (theme.get("name") or "Brand theme").upper(),
           font=_font(_ARCHIVO, 58), fill=INK)
    if subtitle:
        d.text((M, 150), subtitle, font=_font(_ARCHIVO, 26), fill=INK)

    # swatch row
    y = 230
    sw = (W - 2 * M)
    n = max(1, len(palette))
    bw = sw // n
    for i, p in enumerate(palette):
        c = _hex(p.get("hex", ""))
        x0 = M + i * bw
        d.rounded_rectangle((x0, y, x0 + bw - 16, y + 300), radius=18, fill=c)
        tc = _readable_on(c)
        d.text((x0 + 18, y + 300 - 78), (p.get("role") or "").upper(),
               font=_font(_ARCHIVO, 22), fill=tc)
        d.text((x0 + 18, y + 300 - 48), (p.get("hex") or "").upper(),
               font=_font(_ARCHIVO, 26), fill=tc)

    # type direction
    ty = y + 360
    d.text((M, ty), "TYPE", font=_font(_ARCHIVO, 24), fill=INK)
    _wrap(d, (M, ty + 40), theme.get("type_direction") or "", _font(_ARCHIVO, 34), INK, W - 2 * M)

    # principles
    py = ty + 200
    d.text((M, py), "PRINCIPLES", font=_font(_ARCHIVO, 24), fill=INK)
    yy = py + 46
    for pr in (theme.get("principles") or [])[:5]:
        d.text((M, yy), "— " + str(pr), font=_font(_ARCHIVO, 30), fill=INK)
        yy += 56

    buf = BytesIO()
    card.save(buf, "PNG")
    return buf.getvalue()


def _wrap(draw, xy, text, font, fill, max_w):
    x, y = xy
    words = str(text).split()
    line = ""
    for w in words:
        trial = (line + " " + w).strip()
        if draw.textlength(trial, font=font) > max_w and line:
            draw.text((x, y), line, font=font, fill=fill)
            y += font.size + 10
            line = w
        else:
            line = trial
    if line:
        draw.text((x, y), line, font=font, fill=fill)


# ── persist the accepted theme so generation renders in the brand's colours ──
# (DB imports kept inside so this module stays importable without a database —
# the extract/render helpers and the pure demo path need no connection.)

async def get_brand_palette(tenant_id=None):
    """The brand's stored theme palette (a role-list [{role,hex}]) or None. Read
    by the image generator so every render uses the brand's own colours."""
    import json as _json

    from .db import acquire
    async with acquire(tenant_id) as conn:
        cfg = await conn.fetchval(
            "SELECT config FROM tenants WHERE id = "
            "current_setting('app.current_tenant', true)::uuid")
    if isinstance(cfg, str):
        cfg = _json.loads(cfg)
    return (cfg or {}).get("brand_palette") or None


async def set_brand_palette(palette, tenant_id=None):
    """Store the brand's theme palette (a role-list [{role,hex}], exactly the
    shape assess_and_propose emits) in tenant config, so generation renders in
    these colours from now on. Call this when an owner accepts a proposed theme."""
    import json as _json

    from .db import acquire
    async with acquire(tenant_id) as conn:
        await conn.execute(
            "UPDATE tenants SET config = coalesce(config, '{}'::jsonb) || $1::jsonb "
            "WHERE id = current_setting('app.current_tenant', true)::uuid",
            _json.dumps({"brand_palette": palette}))
    return True


# ── automatic per-brand palette ───────────────────────────────────────────
# Every brand renders in ITS OWN colours from the very first post — never
# James's. When no theme has been accepted yet, derive one cheaply (pure Pillow)
# from the brand's own assets, persist it, and use it. This is the hard-coded
# floor that makes "its own branded content" true for EVERY new brand.

# Absolute last resort: a neutral dark editorial system with a warm neutral
# accent — deliberately NOT James's navy/blue, so a brand with zero derivable
# assets still reads as its own clean identity rather than inheriting his.
_NEUTRAL_PALETTE = [
    {"role": "background", "hex": "#14161A", "label": "Charcoal"},
    {"role": "ink", "hex": "#F4F6F8", "label": "Off-white"},
    {"role": "accent", "hex": "#C8A46B", "label": "Warm sand"},
    {"role": "surface", "hex": "#242832", "label": "Slate"},
]
# James Prendamano's signature navy — applied ONLY to his own tenant.
_JAMES_PALETTE = [
    {"role": "background", "hex": "#070B14", "label": "Deep space"},
    {"role": "ink", "hex": "#F5F8FC", "label": "White"},
    {"role": "accent", "hex": "#2E80E4", "label": "Brand blue"},
    {"role": "surface", "hex": "#1C3E74", "label": "Navy"},
]


def _lum(c) -> float:
    return (0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]) / 255.0


def _sat(c) -> float:
    mx, mn = max(c), min(c)
    return 0.0 if mx == 0 else (mx - mn) / mx


def _mixc(a, b, t):
    return tuple(int(round(a[i] * (1 - t) + b[i] * t)) for i in range(3))


def _hx(c) -> str:
    return "#%02x%02x%02x" % (int(c[0]), int(c[1]), int(c[2]))


def derive_palette_from_images(images: list[bytes]) -> list[dict] | None:
    """A brand's OWN theme, derived cheaply from its OWN pixels (logo/photos):
    the most ownable accent (saturated), a dark ground, a high-contrast ink, and
    a surface between them. Deterministic, no LLM, no network. Returns a
    role-list [{role,hex,label}] or None when nothing decodes."""
    cols: list[tuple] = []
    for img in images:
        if not img:
            continue
        for c in extract_palette(img, k=6):
            rgb = _hex(c.get("hex"), None)
            if rgb:
                cols.append((rgb, float(c.get("proportion") or 0)))
    if not cols:
        return None

    # accent: the most ownable colour — saturated, not too dark, weighted a
    # little by how much of the brand it covers.
    def _accent_score(t):
        rgb, prop = t
        return _sat(rgb) * (0.35 + 0.65 * min(1.0, _lum(rgb) * 1.6)) * (0.6 + prop)

    accent = max(cols, key=_accent_score)[0]
    if _sat(accent) < 0.12:                      # brand is essentially greyscale
        accent = _hex("#C8A46B", None)           # a restrained neutral accent
    # background: the brand's darkest real colour if dark enough, else a
    # near-black tinted slightly toward the dominant colour.
    darks = [rgb for rgb, _ in cols if _lum(rgb) < 0.26]
    bg = min(darks, key=_lum) if darks else _mixc((10, 12, 16), cols[0][0], 0.18)
    ink = (245, 247, 250) if _lum(bg) < 0.5 else (16, 18, 22)
    surface = _mixc(bg, accent, 0.24)
    # guarantee the accent stands off the ground; lift or deepen it if too close.
    if abs(_lum(accent) - _lum(bg)) < 0.22:
        accent = _mixc(accent, (255, 255, 255), 0.45) if _lum(bg) < 0.5 \
            else _mixc(accent, (0, 0, 0), 0.35)
    return [
        {"role": "background", "hex": _hx(bg), "label": "Background"},
        {"role": "ink", "hex": _hx(ink), "label": "Ink"},
        {"role": "accent", "hex": _hx(accent), "label": "Accent"},
        {"role": "surface", "hex": _hx(surface), "label": "Surface"},
    ]


async def _brand_source_images(tenant_id, kit, hint_image) -> list[bytes]:
    """The brand's OWN pixels to derive a palette from: an uploaded logo first
    (the truest brand colours), a caller-supplied image already in hand, then a
    hero photo. Cheap and best-effort — any failure just yields fewer."""
    import httpx
    imgs: list[bytes] = []
    logo_url = ((kit or {}).get("logo_url") or "").strip()
    if logo_url.startswith("http"):
        try:
            async with httpx.AsyncClient(timeout=20, follow_redirects=True) as c:
                r = await c.get(logo_url)
                r.raise_for_status()
                imgs.append(r.content)
        except Exception:  # noqa: BLE001
            pass
    if hint_image:
        imgs.append(hint_image)
    if not imgs:
        try:
            from .hero_context import get_hero_photo_files
            from .photo_pick import pick_hero_bytes
            refs = await get_hero_photo_files(tenant_id=tenant_id)
            picked = await pick_hero_bytes(refs, tenant_id) if refs else None
            if picked:
                imgs.append(picked[1])
        except Exception:  # noqa: BLE001
            pass
    return imgs


async def ensure_brand_palette(tenant_id=None, *, kit=None, hint_image=None) -> list[dict]:
    """The palette generation renders THIS brand in — guaranteed brand-specific.
    Order: the brand's stored theme → (James's tenant) his signature navy → a
    palette derived from the brand's OWN logo/photos → a neutral floor. Derived
    palettes are persisted so they are stable across posts and can be refined
    later via the brand-look panel; the neutral floor is NOT persisted, so a
    later logo/photo upload still gets a real palette derived from it."""
    stored = await get_brand_palette(tenant_id)
    if stored:
        return stored
    # James's own tenant keeps his signature navy without any derivation.
    try:
        from .db import acquire
        async with acquire(tenant_id) as conn:
            tid = await conn.fetchval(
                "SELECT id FROM tenants WHERE id = "
                "current_setting('app.current_tenant', true)::uuid")
        if tid is not None and tid == settings.default_tenant_id:
            await set_brand_palette(_JAMES_PALETTE, tenant_id)
            return _JAMES_PALETTE
    except Exception:  # noqa: BLE001
        pass
    imgs = await _brand_source_images(tenant_id, kit, hint_image)
    palette = derive_palette_from_images(imgs)
    if palette:
        try:
            await set_brand_palette(palette, tenant_id)
        except Exception:  # noqa: BLE001
            pass
        return palette
    return _NEUTRAL_PALETTE


async def get_design_intel_enabled(tenant_id=None) -> bool:
    """Per-tenant design-intelligence switch. Falls back to the global default
    (settings.design_intel_enabled) when the tenant hasn't set one — so the
    8-format brain can be turned on for ONE brand without touching the rest."""
    import json as _json

    from .config import settings
    from .db import acquire
    async with acquire(tenant_id) as conn:
        cfg = await conn.fetchval(
            "SELECT config FROM tenants WHERE id = "
            "current_setting('app.current_tenant', true)::uuid")
    if isinstance(cfg, str):
        cfg = _json.loads(cfg)
    v = (cfg or {}).get("design_intel_enabled")
    return bool(v) if v is not None else bool(settings.design_intel_enabled)


async def set_design_intel_enabled(enabled, tenant_id=None):
    """Turn the design brain on/off for THIS brand (tenant config override)."""
    import json as _json

    from .db import acquire
    async with acquire(tenant_id) as conn:
        await conn.execute(
            "UPDATE tenants SET config = coalesce(config, '{}'::jsonb) || $1::jsonb "
            "WHERE id = current_setting('app.current_tenant', true)::uuid",
            _json.dumps({"design_intel_enabled": bool(enabled)}))
    return True


async def get_enabled_formats(tenant_id=None) -> set[str] | None:
    """The designed-image formats this brand is ALLOWED to produce, synced from
    the Brand Manager admin's per-brand template control. Returns None when the
    tenant has no restriction set — meaning every format is allowed (the default,
    so a brand nobody curated behaves exactly as before). An explicit (possibly
    empty) list becomes a set the format selector must stay within."""
    import json as _json

    from .db import acquire
    async with acquire(tenant_id) as conn:
        cfg = await conn.fetchval(
            "SELECT config FROM tenants WHERE id = "
            "current_setting('app.current_tenant', true)::uuid")
    if isinstance(cfg, str):
        cfg = _json.loads(cfg)
    v = (cfg or {}).get("enabled_formats")
    if v is None or not isinstance(v, list):
        return None
    return {str(x).strip().lower() for x in v}


async def set_enabled_formats(formats, tenant_id=None):
    """Store the allowed designed-image formats for THIS brand (tenant config).
    Called by the Brand Manager sync when the admin saves a brand's templates."""
    import json as _json

    from .db import acquire
    clean = [str(f).strip().lower() for f in (formats or []) if str(f).strip()]
    async with acquire(tenant_id) as conn:
        await conn.execute(
            "UPDATE tenants SET config = coalesce(config, '{}'::jsonb) || $1::jsonb "
            "WHERE id = current_setting('app.current_tenant', true)::uuid",
            _json.dumps({"enabled_formats": clean}))
    return clean


async def get_brand_font(tenant_id=None) -> str:
    """The typography theme key this brand's static posts render in (synced from the
    Brand Manager owner's font picker). Empty/unset => '' meaning the default house
    look (Anton + Archivo Black), so an un-styled brand renders exactly as before."""
    import json as _json

    from .db import acquire
    async with acquire(tenant_id) as conn:
        cfg = await conn.fetchval(
            "SELECT config FROM tenants WHERE id = "
            "current_setting('app.current_tenant', true)::uuid")
    if isinstance(cfg, str):
        cfg = _json.loads(cfg)
    return str((cfg or {}).get("brand_font") or "").strip().lower()


async def set_brand_font(font_key, tenant_id=None) -> str:
    """Store this brand's typography theme key (tenant config). Called by the Brand
    Manager sync when the owner picks a font."""
    import json as _json

    from .db import acquire
    clean = str(font_key or "").strip().lower()
    async with acquire(tenant_id) as conn:
        await conn.execute(
            "UPDATE tenants SET config = coalesce(config, '{}'::jsonb) || $1::jsonb "
            "WHERE id = current_setting('app.current_tenant', true)::uuid",
            _json.dumps({"brand_font": clean}))
    return clean


__all__ = ["extract_palette", "assess_and_propose", "render_palette_card",
           "get_brand_palette", "set_brand_palette",
           "get_design_intel_enabled", "set_design_intel_enabled",
           "get_enabled_formats", "set_enabled_formats",
           "get_brand_font", "set_brand_font"]
