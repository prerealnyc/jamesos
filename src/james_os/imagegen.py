"""Image generation — two flavours, one OpenAI client.

1. `generate_seed_image` — B-roll seed stills, returned as a data URI so
   Runway can consume them directly (no public hosting required).
2. `generate_post_image` — hero images for LinkedIn/Twitter/IG posts.
   Returns the decoded PNG bytes + the prompt, so the caller can persist
   to storage and create a media_asset row. Tuned for an editorial,
   uncrowded, single-focal-point aesthetic — the kind of image you'd
   actually post alongside a text post, not a busy collage.

Stub-honest: with no OpenAI key both return (None, reason) — never a
fake image.
"""

import base64
import json as _json
import re

from openai import AsyncOpenAI

from .config import settings

# gpt-image-1 sizes: 1024x1024 | 1024x1536 (portrait) | 1536x1024 (landscape)
_SIZE_FOR_ASPECT = {
    "9:16": "1024x1536",
    "16:9": "1536x1024",
    "1:1": "1024x1024",
}

# Per-platform default aspect for post hero images. Twitter and LinkedIn
# render best at 16:9 (1.91:1 cropping aside); IG feed at 1:1; vertical
# 9:16 only for IG/TT/YT Shorts (not a "post image" surface).
_POST_ASPECT = {
    "twitter":  "16:9",
    "x":        "16:9",
    "linkedin": "16:9",
    "facebook": "16:9",
    "instagram": "1:1",
    "ig":       "1:1",
}

# Style library for post hero images. Each entry is a system-style
# prefix appended to the user's topic. All share the same uncluttered/
# no-text-overlays rules so a post image is never a busy collage. The
# style picker on /images selects between them per-render.
#
#   * editorial — clean flat-vector illustration. Best for metaphors and
#     concept hooks ("market shifting", "underpriced asset"). Cheapest
#     to reuse; can't go uncanny on faces.
#   * photoreal — modern documentary-photography aesthetic. Best for
#     real-world subjects (buildings, neighborhoods, offices, objects).
#     Faces still risk uncanny; the prompt asks for environments not
#     portraits unless the topic requires them.
#   * minimal — high-contrast geometric / abstract. Best for newsletters
#     and Twitter, where a strong shape reads at thumbnail scale.
#   * bw_photo — black-and-white documentary photograph. Quiet, serious;
#     good for institutional or legacy-brand posts.
_BASE_RULES = (
    "Single clear focal point, simple uncluttered composition with plenty "
    "of negative space. No text overlays, no logos, no busy background "
    "detail, no faces unless integral to the topic. Professional but "
    "inviting; suitable as a LinkedIn/Twitter/Instagram post hero image."
)

POST_STYLES: dict[str, str] = {
    "editorial": (
        "Clean editorial illustration, modern flat-vector aesthetic. "
        "Restrained color palette (2–4 tones). " + _BASE_RULES
    ),
    "photoreal": (
        "Photorealistic editorial photograph in the style of a modern "
        "documentary or high-quality stock image. Natural lighting, "
        "shallow depth of field, real-world setting. Prefer environments, "
        "buildings, objects and wide scenes over close-up faces. "
        + _BASE_RULES
    ),
    "minimal": (
        "Bold, high-contrast minimal composition. Strong geometric "
        "shapes, large fields of color, abstracted or symbolic. Reads "
        "clearly at thumbnail scale. " + _BASE_RULES
    ),
    "bw_photo": (
        "Black-and-white documentary photograph. Quiet, serious, "
        "classic editorial framing. Natural lighting, real-world setting. "
        + _BASE_RULES
    ),
    "cinematic": (
        # The "political-ad / Netflix-documentary cutaway" aesthetic.
        # Matches the reference videos the user provided — strong
        # symbolic single-subject framing with hard directional light.
        # Used by story_audio + avatar_story_mix when image_style=
        # 'cinematic'; the prompt-generation system in story_video.py
        # also switches to a metaphor-finding system prompt to pair
        # with this look. The two together (prefix + prompt system)
        # are what makes the output read like a film still, not stock.
        "Cinematic single-subject photograph in the style of a "
        "political ad or Netflix documentary cutaway. Dramatic "
        "directional lighting — spotlight, hard side light, window "
        "beam, or rim light — with deep shadows and high contrast. "
        "Desaturated cool palette: deep blues, muted browns, "
        "occasional warm rim. Shallow depth of field. Atmospheric "
        "details (dust particles, paper in motion, light beams, light "
        "haze) where appropriate. Hero/close-up framing. Heavy mood, "
        "gravitas. Feels like a film still, never like stock photography. "
        + _BASE_RULES
    ),
    "cinematic_real": (
        # DEFAULT for post hero images. Premium, photorealistic, emotionally
        # grounded — a film still, never flat-vector illustration or stock.
        # Allows a real human subject with genuine emotion (the stories are
        # first-person), so it deliberately does NOT inherit the no-faces
        # _BASE_RULES.
        "Cinematic, photorealistic film still in the style of premium editorial "
        "photography or a prestige brand campaign. Emotional realism with a real "
        "human subject when the story is personal. Golden-hour or hard "
        "directional natural light, warm tones, shallow depth of field, "
        "ultra-detailed, upscale real-world setting, subtle ambition and tension. "
        "Looks like a frame from a prestige film — never stock photography, never "
        "illustration. Single strong focal point. Absolutely no text, numbers, "
        "words, logos, charts, or watermarks."
    ),
}
_DEFAULT_STYLE = "cinematic_real"


def _client() -> AsyncOpenAI | None:
    key = (settings.openai_api_key or "").strip()
    return AsyncOpenAI(api_key=key) if key else None


async def generate_seed_image(prompt: str, aspect: str = "9:16") -> tuple[str | None, str]:
    """Text → still image. Returns (data_uri, error). data_uri is
    'data:image/png;base64,...' suitable as a Runway promptImage."""
    client = _client()
    if client is None:
        return None, "No OpenAI key — add it in Settings to generate B-roll seed images."
    size = _SIZE_FOR_ASPECT.get(aspect, "1024x1536")
    style = (settings.image_style or "").strip()
    full_prompt = f"{style} {prompt}".strip() if style else prompt
    try:
        res = await client.images.generate(
            model=settings.image_model,
            prompt=full_prompt[:1000],
            size=size,
            n=1,
        )
    except Exception as e:  # noqa: BLE001
        return None, f"image generation failed: {e}"
    item = res.data[0] if res.data else None
    b64 = getattr(item, "b64_json", None) if item else None
    if not b64:
        url = getattr(item, "url", None) if item else None
        if url:
            return url, ""  # some models return a URL instead of b64
        return None, "image model returned no image"
    return f"data:image/png;base64,{b64}", ""


def _build_post_prompt(
    topic: str, brief: str, style: str, brand_guidelines: str = ""
) -> str:
    """Compose the final text prompt for a post hero image. The style
    prefix is the lever — same topic, different prefix = different
    aesthetic. Unknown style falls back to editorial silently rather
    than crashing the render. When brand_guidelines are supplied they are
    woven in so the image follows the brand's visual rules."""
    prefix = POST_STYLES.get(style.lower(), POST_STYLES[_DEFAULT_STYLE])
    parts = [prefix, f"Subject: {topic.strip()}."]
    if brief.strip():
        parts.append(f"Context: {brief.strip()[:240]}")
    if brand_guidelines.strip():
        parts.append(
            "Follow the brand's visual guidelines exactly: "
            f"{brand_guidelines.strip()[:500]}"
        )
    return " ".join(parts)[:1300]


_IMG_DIRECTOR_SYSTEM = (
    "You are a cinematic photo director for a premium personal brand. Given a "
    "first-person story, write ONE image-generation prompt for a single "
    "photorealistic still that captures the story's EMOTIONAL TENSION — never a "
    "literal, generic, or stock scene.\n"
    "Requirements:\n"
    "- Cinematic film-still realism: name the lighting (e.g. golden hour, hard "
    "window light, rim light), shallow depth of field, ultra-realistic detail, "
    "warm tones, emotional realism, an upscale/premium setting.\n"
    "- Ground it in the story's specific scene, stakes, and subtext (e.g. "
    "intuition vs algorithms, confidence under skepticism, momentum and demand, "
    "premium positioning, testing the ceiling).\n"
    "- A real human subject showing genuine, specific emotion is encouraged for "
    "personal stories.\n"
    "- Absolutely NO text, numbers, words, logos, charts, or watermarks in the image.\n"
    "- FORBIDDEN clichés that kill the tension: 'realtor with house', 'happy "
    "couple buying a home', 'businessman holding paperwork', generic smiling stock.\n"
    'Return STRICT JSON: {"prompt": str} — the prompt 40-80 words, no quotes inside.'
)


async def direct_image_scene(story: str, fallback_topic: str = "") -> str:
    """LLM image director: turn a post's story into ONE cinematic, realistic
    scene prompt that carries its emotional tension (the post equivalent of the
    video pipeline's write_image_prompts). Best-effort — returns fallback_topic
    if the LLM is unavailable or errors, so image generation never depends on it."""
    story = (story or "").strip()
    if not story:
        return fallback_topic
    try:
        from .llm import get_llm

        out = await get_llm().complete_json(
            system=_IMG_DIRECTOR_SYSTEM,
            messages=[{"role": "user", "content": story[:2000]}],
            max_tokens=300, temperature=0.7,
        )
        scene = str((out or {}).get("prompt") or "").strip()
        return scene or fallback_topic
    except Exception:  # noqa: BLE001
        return fallback_topic


# raw / legacy format name → canonical compositor format. Drives both the
# force_format map and the coercion of whatever the model returns, so an unknown
# name can never reach the compositors.
_FORMAT_MAP = {
    "quote": "brand_quote", "meme": "brand_quote", "brand_quote": "brand_quote",
    "hero_quote": "hero_quote", "statement": "statement", "big_stat": "big_stat",
    "bold_statement": "bold_statement", "statement_poster": "bold_statement",
    "poster": "bold_statement",
    "full_bleed": "full_bleed", "full_bleed_hero": "full_bleed",
    "editorial_split": "editorial_split", "editorial": "editorial_split",
    "minimal_over": "minimal_over", "minimal": "minimal_over",
    "framed_print": "framed_print", "framed": "framed_print",
    "carousel": "carousel", "photo_carousel": "carousel",
    "text_carousel": "text_carousel", "type_carousel": "text_carousel",
    "typographic_carousel": "text_carousel", "no_photo_carousel": "text_carousel",
}
# Formats that place the brand's REAL photo (everything except the text-only
# cards). Used to decide bg_kind and whether to fetch a hero photo.
_TEXT_ONLY_FORMATS = {"brand_quote", "big_stat", "bold_statement"}

# When the design-intelligence switch is OFF, coerce a v2 layout the model may
# still name back to the nearest shipped format, so prod output stays unchanged.
_V2_TO_LEGACY = {
    "full_bleed": "statement", "editorial_split": "statement",
    "framed_print": "statement", "minimal_over": "hero_quote",
    "big_stat": "brand_quote", "carousel": "statement",
    "text_carousel": "bold_statement",
}

_DESIGN_DIRECTOR_SYSTEM = (
    "You are the art director for a scroll-stopping Instagram IMAGE that "
    "accompanies a brand's post. Choose the single best visual FORMAT for THIS "
    "post and write the short on-image text. Text is overlaid later in perfect "
    "type — write it, never describe it, and keep on-image copy to ONE short "
    "idea (a stranger should grasp it in under 1.5 seconds).\n\n"
    "The formats — ALL use clean brand type over the brand's REAL photo or a "
    "solid brand-colour card, NEVER an AI-generated scene:\n"
    "  * brand_quote — text-only card for a short punchy mantra / identity line "
    "(no photo). Best for a crisp quotable line.\n"
    "  * bold_statement — text-only POSTER for a longer declarative statement or "
    "observation (no photo): the brand name across the top, the statement set "
    "large and left-aligned with its key phrase highlighted, a byline at the "
    "foot. Best for a punchy multi-line claim/contrast that stands on its own "
    "words (e.g. a hard truth, a 'we do X but not Y'). Put the line in "
    "\"statement\" and the phrase to highlight in \"emphasis\".\n"
    "  * big_stat — ONE huge number or claim: a first, a ranking, a count, a span "
    "of years, a superlative (no photo). Use when the post has a strong number "
    "or 'first/only/most'.\n"
    "  * full_bleed — the brand's striking photo edge-to-edge with a short bold "
    "headline. Use when the PHOTO is dramatic and carries the post.\n"
    "  * editorial_split — the photo in a clean band above a brand-colour band "
    "with a headline. A confident, magazine-style statement over a good photo.\n"
    "  * minimal_over — a beautiful photo with small, airy type. For an "
    "aspirational / atmospheric moment where less is more.\n"
    "  * framed_print — the photo matted inside a brand frame with a short "
    "caption. To showcase ONE great image.\n"
    "  * hero_quote — the person's photo beside a quote. For a personal "
    "motivational line where a face adds authority.\n"
    "  * statement — a bold declarative statement with the photo framed below.\n"
    "  * carousel — a multi-slide SET (up to 10) ON THE BRAND'S PHOTOS for a list "
    "('N ways/reasons'), a step-by-step, or several proof points/stats: pick this "
    "when the post has SEVERAL distinct points that each deserve their own slide "
    "AND photos help carry them. Its slides are written separately — just choose "
    "this format.\n"
    "  * text_carousel — the SAME multi-slide SET but TEXT-ONLY (no photos): each "
    "point set as clean type on a brand-colour slide. Pick this for a list / "
    "steps / points that stand on their words, or when the brand has no strong "
    "photos for the topic.\n\n"
    "Return STRICT JSON with ALL keys (fill only what the chosen format needs, "
    "leave the rest \"\"):\n"
    "{\n"
    '  "format": "brand_quote"|"bold_statement"|"big_stat"|"full_bleed"|'
    '"editorial_split"|"minimal_over"|"framed_print"|"hero_quote"|"statement"|"carousel"|"text_carousel",\n'
    '  "quote": "<brand_quote/hero_quote line, <=12 words>",\n'
    '  "emphasis": "<1-3 KEY words from quote to highlight, verbatim>",\n'
    '  "statement": "<statement line, <=16 words>",\n'
    '  "headline": "<full_bleed/editorial_split/minimal_over bold line, <=8 words>",\n'
    '  "kicker": "<tiny label above a headline, <=4 words, optional>",\n'
    '  "stat": "<big_stat huge number/word, e.g. 1ST, 20 YRS, 18K FT, <=3 words>",\n'
    '  "stat_label": "<big_stat: what it means, <=6 words>",\n'
    '  "stat_sub": "<big_stat: a short supporting line, optional>",\n'
    '  "caption": "<framed_print caption, <=8 words>"\n'
    "}\n"
    "VOICE IS PRIMARY. The on-image line MUST sound like THIS brand: reuse the "
    "brand's own words, phrases and cadence from the <brand_voice> block and the "
    "<draft>. LIFT and tighten a real line the brand already wrote in the draft "
    "rather than composing a fresh generic one; never introduce vocabulary the "
    "brand would not use.\n"
    "The hook guidance is SECONDARY craft — it governs STRUCTURE only (what to "
    "lead with, one idea, format choice), never the brand's wording. When "
    "structure and voice conflict, VOICE WINS.\n"
    "Within the brand's own words, prefer a SPECIFIC claim — a number, a "
    "contrarian line, or a concrete result ALREADY PRESENT in the draft — over a "
    "question or a vague adjective. No clichés, no hype words.\n"
    "FORMAT PRIORITY: when two or more formats would fit this post well, PREFER "
    "the newest template, `bold_statement` (the text + brand-name poster) — it is "
    "the brand's current house style, so make it your default choice for any "
    "declarative claim, contrast, hard truth or mantra. Only pass it over when the "
    "post is CLEARLY better as another format: a dominant number/superlative → "
    "big_stat, several distinct points → carousel, or a genuinely photo-led moment "
    "where the image carries the post → full_bleed/minimal_over/framed_print. "
    "Across a batch, still vary — but let bold_statement lead the rotation."
)


# Format families for the per-brand allowed-set clamp. When a brand's templates
# are restricted (synced from the Brand Manager admin) and the art director — or a
# force_format — picks a disallowed one, we swap to an allowed format in the SAME
# family (a text card stays a text card, a photo card a photo card, a carousel a
# carousel) and rotate among the allowed members for variety; only a fully-disabled
# family crosses over. `allowed=None` means no restriction (behaves as before).
_TEXT_CARD_FMTS = frozenset({"brand_quote", "big_stat", "bold_statement"})
_PHOTO_CARD_FMTS = frozenset({"hero_quote", "statement", "full_bleed",
                              "editorial_split", "minimal_over", "framed_print"})
_CAROUSEL_FMTS = frozenset({"carousel", "text_carousel"})
_FMT_FAMILIES = (_TEXT_CARD_FMTS, _PHOTO_CARD_FMTS, _CAROUSEL_FMTS)


def _clamp_format(fmt: str, allowed: set[str] | None) -> str:
    """Keep fmt if the brand allows it (or has no restriction); else pick an
    allowed replacement — same family first, rotating for variety — so a disabled
    template is never produced. `allowed is None` = no restriction; an EMPTY set =
    nothing allowed (the caller short-circuits image production before this — here
    it degrades to keeping fmt since there is nothing to pick)."""
    if allowed is None or fmt in allowed:
        return fmt
    import random
    for fam in _FMT_FAMILIES:
        if fmt in fam:
            pool = [f for f in fam if f in allowed]
            if pool:
                return random.choice(pool)
            break
    pool = list(allowed)
    return random.choice(pool) if pool else fmt


# The keys a designed spec carries. An edit returns the SAME shape — the editor
# is not allowed to invent a key or drop one, because the renderer reads exactly
# these and a missing one renders blank.
_SPEC_KEYS = (
    "format", "quote", "emphasis", "top_text", "bottom_text", "statement",
    "headline", "kicker", "stat", "stat_label", "stat_sub", "caption",
    "bg_prompt", "bg_kind",
)

_EDIT_SYSTEM = (
    "You are editing ONE finished social card, not designing a new one.\n\n"
    "You are given the exact spec that produced the card the owner is looking at, "
    "and the owner's words about what they want changed. Apply THAT change and "
    "nothing else.\n\n"
    "RULES — these are the whole job:\n"
    "- Return every key you were given, with the same meaning.\n"
    "- A key the owner did not mention comes back BYTE-IDENTICAL. Do not reword "
    "it, tighten it, fix its punctuation, or improve it. Unchanged means unchanged.\n"
    "- Never change 'format'. The layout is fixed.\n"
    "- If the request is about something a spec cannot express (spacing, colour, "
    "position, adding a logo or handle), change NOTHING and return the spec as "
    "given. Returning it unchanged is the correct answer — someone else applies "
    "those.\n"
    "- Keep the brand's voice and any real numbers exactly as they are.\n\n"
    "Return STRICT JSON with exactly the keys you were given."
)


async def edit_designed_spec(base_spec: dict, feedback: str) -> dict:
    """Re-issue a card's spec with ONE change applied — the owner's change.

    Every redo used to re-run the art director from scratch at temperature 0.7,
    so "keep everything the same, just change X" came back with every line of
    on-image copy rewritten. The owner could keep the layout and the photo and
    still not recognise the card. This is the missing path: the spec that made
    the card, plus their words, minus a fresh act of authorship.

    Degrades to the input on any failure — an editor that cannot run must return
    the card unchanged rather than hand the job back to a fresh composition,
    which is the outcome this exists to prevent."""
    base = {k: base_spec.get(k, "") for k in _SPEC_KEYS if k in base_spec}
    words = (feedback or "").strip()
    if not base or not words:
        return dict(base_spec)
    from .llm import get_llm

    try:
        out = await get_llm().complete_json(
            system=_EDIT_SYSTEM,
            messages=[{"role": "user", "content":
                       "THE CARD AS IT IS:\n" + _json.dumps(base, indent=2)
                       + "\n\nWHAT THE OWNER WANTS CHANGED:\n" + words[:400]}],
            max_tokens=700,
            # Deterministic: an edit is not a creative act, and temperature is
            # exactly what rewrote the untouched lines last time.
            temperature=0.0,
        )
    except Exception:  # noqa: BLE001
        return dict(base_spec)
    if not isinstance(out, dict) or not out:
        return dict(base_spec)
    # Merge onto the original: a key the editor omitted keeps its old value, and
    # a key it invented is dropped. The format is never editable.
    edited = dict(base_spec)
    for k in _SPEC_KEYS:
        v = out.get(k)
        if k != "format" and isinstance(v, str) and v.strip():
            edited[k] = v.strip()
    edited["format"] = base_spec.get("format") or edited.get("format")
    return edited


async def direct_designed_image(
    draft_text: str, topic: str = "", avoid: str = "",
    feedback: str = "", force_format: str = "", allow_v2: bool | None = None,
    voice: str = "", brand_profile: str = "", allowed: set[str] | None = None,
) -> dict:
    """LLM art director → {format, quote, top_text, bottom_text, bg_prompt,
    bg_kind} for the multi-format image machine. Best-effort: falls back to a
    quote built from the draft's first line so the machine never hard-depends
    on the LLM.

    `avoid` is a soft variety hint (a format name like "quote") used when
    creating many posts in one batch: it nudges the art director toward a
    DIFFERENT format so a batch doesn't emit the same card type over and over,
    while still letting content win when a post clearly fits the avoided one.

    `feedback` is the owner's own words about what was wrong with the image
    they just rejected ("he's half hidden behind the text"). Unlike `avoid`
    it is a CORRECTION, not a preference, so it goes in as a requirement the
    art director has to satisfy.

    `force_format` pins the layout outright. The two hints above are things a
    model may talk itself out of — the docstring above admits `avoid` loses to
    content — so when a regeneration must come back visibly different, the
    caller names the format and the model does not get a vote."""
    text = (draft_text or topic or "").strip()
    # Per-call override (from the tenant switch) wins; else the global default.
    enabled = settings.design_intel_enabled if allow_v2 is None else bool(allow_v2)
    _fb_fmt = _FORMAT_MAP.get(str(force_format).lower(), "brand_quote") if force_format else "brand_quote"
    # An explicit force_format is a user opt-in — honor it even when the design
    # switch is off (the switch gates AUTONOMOUS picks, not explicit builds).
    if not enabled and not force_format:
        _fb_fmt = _V2_TO_LEGACY.get(_fb_fmt, _fb_fmt)
    # A disabled template must not slip through the no-LLM fallback either.
    _fb_fmt = _clamp_format(_fb_fmt, allowed)
    _fb_quote = (text.split(". ")[0] if text else (topic or "")).strip()[:140]
    fallback = {
        # A pinned format has to survive the fallback too — otherwise an LLM
        # hiccup silently returns the same brand_quote layout the owner just
        # rejected, and the regeneration looks like it did nothing.
        "format": _fb_fmt,
        "quote": _fb_quote,
        "emphasis": "",
        "top_text": "", "bottom_text": "",
        "statement": _fb_quote if _fb_fmt in ("statement", "bold_statement") else "",
        # New v2 formats: a pinned format keeps its own layout in the fallback by
        # borrowing the first line, instead of collapsing to a plain card.
        "headline": _fb_quote if _fb_fmt in ("full_bleed", "editorial_split", "minimal_over") else "",
        "kicker": "",
        "stat": _fb_quote[:16] if _fb_fmt == "big_stat" else "",
        "stat_label": "", "stat_sub": "",
        "caption": _fb_quote if _fb_fmt == "framed_print" else "",
        "bg_prompt": (topic or "").strip(),
        "bg_kind": "none" if _fb_fmt in _TEXT_ONLY_FORMATS else "hero",
    }
    if not text:
        return fallback
    try:
        from .llm import get_llm

        # Voice first: the art director sees the brand's real cadence/vocabulary
        # BEFORE the draft, and the prompt tells it to LIFT the card line from the
        # brand's own words — so the headline sounds like the brand, not a hook
        # template. Empty voice/profile → just the <draft>, safe as before.
        vb = ""
        if voice:
            vb += f"<brand_voice>\n{voice[:1500]}\n</brand_voice>\n\n"
        if brand_profile:
            vb += f"{brand_profile}\n\n"
        user_content = f"{vb}<draft>\n{text[:2000]}\n</draft>"
        if allowed:
            user_content += (
                f"\n\n[ALLOWED FORMATS — choose `format` from ONLY these: "
                f"{', '.join(sorted(allowed))}. Do not use any other format. Vary "
                f"your choice across posts so the feed is a designed mix, not one "
                f"card type repeated.]"
            )
        if avoid:
            user_content += (
                f"\n\n[Variety note: recent posts in this batch already used the "
                f"'{avoid}' format. Prefer a DIFFERENT format for THIS post "
                f"UNLESS its content clearly fits '{avoid}' best.]"
            )
        if feedback:
            # Stated as a requirement, not a preference: this is a redo of an
            # image a human already turned down for this reason.
            user_content += (
                f"\n\n[REQUIRED CORRECTION — the previous image for this post was "
                f"REJECTED by the brand owner for this reason: \"{feedback[:400]}\". "
                f"Your layout and text choices MUST fix it. If the complaint is "
                f"that the person is hidden, obscured or hard to see, choose a "
                f"format that gives the photo room and keep the on-image text "
                f"short so it cannot cover him.]"
            )
        out = await get_llm().complete_json(
            system=_DESIGN_DIRECTOR_SYSTEM,
            messages=[{"role": "user", "content": user_content}],
            max_tokens=400, temperature=0.7,
        )
        out = out or {}
        raw_fmt = str(out.get("format", "")).lower()
        # Coerce whatever the model returns into a KNOWN compositor format, so an
        # unknown/legacy name can never reach the compositors.
        fmt = _FORMAT_MAP.get(raw_fmt, "brand_quote")
        # A pinned format wins over whatever the model picked.
        if force_format:
            fmt = _FORMAT_MAP.get(str(force_format).lower(), fmt)
        # Gate AUTONOMOUS v2 picks behind the design switch; an explicit
        # force_format is a user opt-in and is honored regardless.
        elif not enabled:
            fmt = _V2_TO_LEGACY.get(fmt, fmt)
        # Per-brand template control has the FINAL say (even over force_format):
        # a template the admin disabled is swapped for an allowed one in-family.
        fmt = _clamp_format(fmt, allowed)
        # bg_kind: text-only cards → none; every other format places the brand's
        # REAL uploaded photo (never AI-generated).
        bg_kind = "none" if fmt in _TEXT_ONLY_FORMATS else "hero"
        spec = {
            "format": fmt,
            "quote": str(out.get("quote") or "").strip(),
            "emphasis": str(out.get("emphasis") or "").strip(),
            "top_text": str(out.get("top_text") or "").strip(),
            "bottom_text": str(out.get("bottom_text") or "").strip(),
            "statement": str(out.get("statement") or "").strip(),
            "headline": str(out.get("headline") or "").strip(),
            "kicker": str(out.get("kicker") or "").strip(),
            "stat": str(out.get("stat") or "").strip(),
            "stat_label": str(out.get("stat_label") or "").strip(),
            "stat_sub": str(out.get("stat_sub") or "").strip(),
            "caption": str(out.get("caption") or "").strip(),
            "bg_prompt": str(out.get("bg_prompt") or "").strip() or fallback["bg_prompt"],
            "bg_kind": bg_kind,
        }
        # Guards: each format needs its own text, or borrows the quote/first line
        # so a render never comes back blank.
        line = spec["quote"] or fallback["quote"]
        if fmt in ("brand_quote", "hero_quote") and not spec["quote"]:
            spec["quote"] = line
        if fmt in ("statement", "bold_statement") and not spec["statement"]:
            spec["statement"] = line
        if fmt in ("full_bleed", "editorial_split", "minimal_over") and not spec["headline"]:
            spec["headline"] = line
        if fmt == "framed_print" and not spec["caption"]:
            spec["caption"] = line
        if fmt == "big_stat" and not spec["stat"]:
            # No clear number → don't fake a stat card; render another text card.
            # Re-clamp so this escape hatch can't emit a template the admin disabled
            # (big_stat itself is excluded — it's the format we're escaping).
            repl = _clamp_format("brand_quote", (allowed - {"big_stat"}) if allowed else allowed)
            if allowed and repl not in allowed:
                # nothing else is allowed → keep big_stat, use the line as its number
                spec["stat"] = (spec["stat"] or line)[:16]
            else:
                _t = spec["quote"] or line
                spec.update(format=repl, bg_kind="none" if repl in _TEXT_ONLY_FORMATS else "hero")
                if repl in ("brand_quote", "hero_quote"):
                    spec["quote"] = spec["quote"] or _t
                elif repl in ("statement", "bold_statement"):
                    spec["statement"] = spec["statement"] or _t
                elif repl in ("full_bleed", "editorial_split", "minimal_over"):
                    spec["headline"] = spec["headline"] or _t
                elif repl == "framed_print":
                    spec["caption"] = spec["caption"] or _t
        return spec
    except Exception:  # noqa: BLE001
        return fallback


_CAROUSEL_SYSTEM = (
    "You are the art director for an Instagram CAROUSEL (a swipeable multi-slide "
    "set). From the post, choose a narrative arc and write a COVER that EARNS THE "
    "SWIPE, 4–7 inner SLIDES (ONE atomic idea each), and a CTA. 7–10 total slides "
    "is the proven sweet spot. Photos come from the brand's own library — you only "
    "choose whether each inner slide is a 'photo' slide or a 'stat' slide (one big "
    "number/word), and write its short text.\n\n"
    "COVER is the whole distribution bet — ~2 in 3 viewers decide from the cover "
    "alone whether to swipe. Make a SPECIFIC promise: a number, a contrarian claim, "
    "or a how-to. Proven cover shapes: '{N} {things} that {result}', '{contrarian "
    "claim}', 'How to {outcome} without {pain}'. Never a vague title.\n"
    "SLIDES: exactly one self-contained idea + one visual each; put ONE surprising "
    "or contrarian slide in the MIDDLE to re-hook the swipe.\n"
    "CTA: a SINGLE ask on the last slide — prefer 'save', 'share', or 'comment "
    "{keyword}' over 'link in bio' (they convert far better). Never stack asks.\n\n"
    "Return STRICT JSON:\n"
    "{\n"
    '  "arc": "listicle"|"steps"|"proof"|"before_after"|"myth_fact",\n'
    '  "cover": {"headline": "<=8 words", "emphasis": "1–4 KEY words FROM the '
    'headline to highlight, verbatim", "count_promise": "e.g. 6 REASONS / 5 STEPS '
    '— <=3 words; MUST equal the number of inner slides", "kicker": "<=3 words"},\n'
    '  "slides": [{"kind": "photo"|"stat", "section_label": "<=3 words", '
    '"headline": "<=14 words (the slide\'s full line)", "emphasis": "1–4 KEY words '
    'FROM this headline to highlight, verbatim", "stat": "<stat kind only: 1–3 '
    'words, e.g. 1ST, 20 YRS, 90%>", "source": "<stat kind only: the data source, '
    '<=6 words, e.g. NAEP 2024>"}],\n'
    '  "cta": {"headline": "<a strong closing statement, <=14 words>", "emphasis": '
    '"1–4 KEY words FROM the cta headline, verbatim", "action": "1–2 words, e.g. '
    'VISIT, BOOK, FOLLOW", "ask": "<the offer line, <=10 words>"}\n'
    "}\n"
    "`emphasis` is the phrase set in the brand ACCENT colour — pick the words that "
    "carry the punch (a contrast, a landing, the payoff), always a verbatim slice "
    "of that same line. On a TEXT (photoless) carousel each headline stands on its "
    "own words, so emphasis matters most there; a stat slide's `source` is the data "
    "attribution shown small at the foot.\n"
    "4–7 inner slides. The cover count_promise MUST match the number of inner "
    "slides. One atomic idea per slide. VOICE IS PRIMARY: every cover line, slide "
    "headline and CTA MUST use the brand's own words and cadence from the "
    "<brand_voice> block and the <draft> — lift real phrases, never invent generic "
    "ones. The swipe/cover/CTA rules above are SECONDARY structure only; when they "
    "conflict with the brand's wording, VOICE WINS. No fluff."
)


# A carousel titled "5 Signs …" promises 5 inner slides. Nothing used to enforce
# that — the LLM picked a slide count freely and the title's number was decorative,
# so "5 Signs" routinely shipped 4 or 6 slides. We now (a) parse the promised count
# and ask for exactly that, then (b) reconcile in code so the number ON the cover
# always equals the number of inner slides rendered — the title can never lie.
_LIST_NOUNS = (
    r"signs?|reasons?|steps?|ways?|tips?|lessons?|myths?|facts?|things?|rules?|"
    r"mistakes?|secrets?|questions?|stats?|truths?|principles?|habits?|traits?|"
    r"examples?|keys?|strategies|strategy|hacks?|takeaways?|lies|benefits?"
)
_LIST_COUNT_RE = re.compile(rf"\b([2-9]|1[0-2])\s+(?:\w+\s+){{0,2}}(?:{_LIST_NOUNS})\b", re.I)


def _list_count(text: str) -> int | None:
    """The integer a listicle title promises (e.g. 5 from '5 Signs …'), or None."""
    if not text:
        return None
    m = _LIST_COUNT_RE.search(text)
    return int(m.group(1)) if m else None


def _reconcile_cover_count(cover: dict, n: int) -> None:
    """Force the cover's promised number to equal n (the real inner-slide count),
    so title/count_promise and the rendered slides can never disagree. Rewrites the
    first number token in the headline and rebuilds count_promise from n."""
    head = str(cover.get("headline") or "")
    promise = str(cover.get("count_promise") or "")
    noun = ""
    mn = re.search(r"\b\d+\s+([A-Za-z]+)", promise) or re.search(r"\b\d+\s+([A-Za-z]+)", head)
    if mn:
        noun = mn.group(1)
    if re.search(r"\b\d+\b", head):
        cover["headline"] = re.sub(r"\b\d+\b", str(n), head, count=1)
    if promise and re.search(r"\b\d+\b", promise):
        cover["count_promise"] = re.sub(r"\b\d+\b", str(n), promise, count=1)
    elif noun:
        cover["count_promise"] = f"{n} {noun}"


def _finalize_deck(deck: dict, want: int | None) -> dict:
    """Trim to the promised count (never pad — better fewer real slides than filler)
    and reconcile the cover number to the actual inner-slide count."""
    slides = deck.get("slides") or []
    if want and len(slides) > want >= 2:
        deck["slides"] = slides = slides[:want]
    if deck.get("cover") and len(slides) >= 2:
        _reconcile_cover_count(deck["cover"], len(slides))
    return deck


async def direct_carousel_deck(draft_text: str, topic: str = "", brand_name: str = "",
                               voice: str = "", brand_profile: str = "",
                               text_only: bool = False) -> dict:
    """LLM → a structured carousel deck {arc, cover, slides[], cta}. Photos are
    assigned later from the brand's library; the model only writes text and picks
    photo-vs-stat per slide. Best-effort with a deck built from the draft.

    `text_only` forces a PHOTOLESS deck: every inner slide is coerced to kind
    "text" (pure typography on the brand ground), and no photo is assigned later —
    the typographic carousel template."""
    text = (draft_text or topic or "").strip()
    want = _list_count(text) or _list_count(topic)  # e.g. 5 from "5 Signs …"
    _slide_kind = "text" if text_only else "photo"
    parts = [p.strip() for p in text.split(". ") if p.strip()]
    fb_slides = [{"kind": _slide_kind, "section_label": "", "headline": p[:80], "stat": ""}
                 for p in parts[1:(1 + (want or 4))]]
    fallback = {
        "arc": "listicle",
        "cover": {"headline": (parts[0][:80] if parts else (topic or "")),
                  "count_promise": "", "kicker": brand_name[:24]},
        "slides": fb_slides if len(fb_slides) >= 2 else [
            {"kind": _slide_kind, "section_label": "", "headline": (parts[0][:80] if parts else topic), "stat": ""},
            {"kind": _slide_kind, "section_label": "", "headline": (topic or "")[:80], "stat": ""},
        ],
        "cta": {"action": "LEARN MORE", "ask": ""},
    }
    if not text:
        return _finalize_deck(fallback, want)
    try:
        from .llm import get_llm
        out = await get_llm().complete_json(
            system=_CAROUSEL_SYSTEM,
            messages=[{"role": "user", "content": (
                (f"<brand_voice>\n{voice[:1500]}\n</brand_voice>\n\n" if voice else "")
                + (f"{brand_profile}\n\n" if brand_profile else "")
                + f"<draft>\n{text[:2500]}\n</draft>"
                + (f"\n\nThe title promises {want} items — produce EXACTLY {want} inner "
                   f"slides (one per item), no more, no fewer." if want else ""))}],
            max_tokens=800, temperature=0.6,
        )
        out = out or {}
        slides = []
        for s in (out.get("slides") or [])[:8]:
            if not isinstance(s, dict):
                continue
            is_stat = str(s.get("kind")).lower() == "stat" and str(s.get("stat") or "").strip()
            # A text-only (photoless) deck keeps STAT slides (they're numbers, no
            # photo) and turns every other slide into a pure-type slide; a photo
            # deck picks stat-vs-photo per slide.
            kind = "stat" if is_stat else ("text" if text_only else "photo")
            row = {
                "kind": kind,
                "section_label": str(s.get("section_label") or "").strip(),
                "headline": str(s.get("headline") or "").strip(),
                "stat": str(s.get("stat") or "").strip(),
                "emphasis": str(s.get("emphasis") or "").strip(),
                "source": str(s.get("source") or "").strip(),
                # a stat slide's line IS its caption in the premium text carousel
                "caption": str(s.get("headline") or "").strip(),
            }
            if row["headline"] or row["stat"]:
                slides.append(row)
        if len(slides) < 2:
            return _finalize_deck(fallback, want)
        cover = out.get("cover") or {}
        cta = out.get("cta") or {}
        return _finalize_deck({
            "arc": str(out.get("arc") or "listicle"),
            "cover": {
                "headline": str(cover.get("headline") or "").strip() or fallback["cover"]["headline"],
                "emphasis": str(cover.get("emphasis") or "").strip(),
                "count_promise": str(cover.get("count_promise") or "").strip(),
                "kicker": (str(cover.get("kicker") or "").strip() or brand_name)[:24],
                "eyebrow": str(cover.get("kicker") or "").strip(),
            },
            "slides": slides,
            "cta": {
                "headline": str(cta.get("headline") or "").strip(),
                "emphasis": str(cta.get("emphasis") or "").strip(),
                "action": str(cta.get("action") or "LEARN MORE").strip(),
                "ask": str(cta.get("ask") or "").strip(),
            },
        }, want)
    except Exception:  # noqa: BLE001
        return _finalize_deck(fallback, want)


async def _brand_visual_directive(tenant_id) -> str:
    """Pull the brand's visual/style guidelines from memory so generated
    POST images follow them (colours, imagery, layout, look). Returns ""
    when no tenant is given (e.g. video B-roll callers, which stay
    independent of post guidelines) or no guideline material exists.
    Best-effort — never blocks a render."""
    if not tenant_id:
        return ""
    try:
        from .retrieval import search

        hits = await search(
            "brand visual style: colours, typography, logo usage, imagery and "
            "photography style, layout, what posts should look like",
            tenant_id=tenant_id,
        )
        gl = [h for h in hits if (h.payload or {}).get("category") == "guideline"]
        return " ".join((h.raw_content or "") for h in gl[:4]).strip()[:700]
    except Exception:  # noqa: BLE001
        return ""


async def generate_post_image(
    topic: str,
    platform: str = "linkedin",
    brief: str = "",
    aspect: str = "",
    style: str = _DEFAULT_STYLE,
    tenant_id=None,
) -> tuple[bytes | None, dict, str]:
    """Topic → PNG bytes for a shareable post hero image.

    Returns (png_bytes, meta, error). meta carries the final prompt,
    chosen size, model and platform so the caller can persist it for
    later reproducibility.

    aspect override beats per-platform default; falls back to 16:9 when
    neither is recognised — the most universal social ratio.
    """
    client = _client()
    if client is None:
        return None, {}, (
            "No OpenAI key — add OPENAI_API_KEY in Settings to enable "
            "post-image generation."
        )
    topic = (topic or "").strip()
    if not topic:
        return None, {}, "topic is required"
    chosen_aspect = (aspect or "").strip() or _POST_ASPECT.get(
        platform.lower(), "16:9"
    )
    size = _SIZE_FOR_ASPECT.get(chosen_aspect, "1536x1024")
    chosen_style = (style or _DEFAULT_STYLE).lower()
    if chosen_style not in POST_STYLES:
        chosen_style = _DEFAULT_STYLE
    guidelines = await _brand_visual_directive(tenant_id)
    full_prompt = _build_post_prompt(topic, brief, chosen_style, guidelines)
    try:
        res = await client.images.generate(
            model=settings.image_model,
            prompt=full_prompt,
            size=size,
            n=1,
        )
    except Exception as e:  # noqa: BLE001
        return None, {}, f"image generation failed: {e}"
    item = res.data[0] if res.data else None
    b64 = getattr(item, "b64_json", None) if item else None
    if not b64:
        # gpt-image-1 always returns b64_json. A URL path would mean the
        # caller has to fetch separately — we refuse to fake a "success"
        # without the actual bytes in hand.
        return None, {}, "image model returned no PNG bytes"
    try:
        png = base64.b64decode(b64)
    except Exception as e:  # noqa: BLE001
        return None, {}, f"could not decode image bytes: {e}"
    meta = {
        "prompt": full_prompt,
        "size": size,
        "aspect": chosen_aspect,
        "platform": platform,
        "model": settings.image_model,
        "topic": topic,
        "style": chosen_style,
    }
    return png, meta, ""


async def generate_post_image_with_refs(
    topic: str,
    *,
    references: list[tuple[str, bytes]],   # [(filename, bytes), ...]
    platform: str = "linkedin",
    brief: str = "",
    aspect: str = "",
    style: str = _DEFAULT_STYLE,
    tenant_id=None,
) -> tuple[bytes | None, dict, str]:
    """Topic + reference image bytes → PNG bytes.

    Calls gpt-image-1's edit endpoint instead of generate, passing 1-3
    reference photos as visual conditioning. Used for hero-tagged
    beats so the recurring character stays visually consistent across
    a slideshow, rather than the LLM-text-description path which
    produces a different generic person every beat.

    References should already be resized to <4 MB each (see
    hero_context.get_hero_photo_files). The returned PNG is the same
    shape/aspect as generate_post_image — the worker treats both paths
    identically downstream.

    Honest fallback: if `references` is empty, calls through to
    generate_post_image so the caller can pass refs unconditionally
    without an `if` ladder. Single source of truth for the prompt build.
    """
    if not references:
        return await generate_post_image(
            topic, platform=platform, brief=brief, aspect=aspect, style=style,
            tenant_id=tenant_id,
        )
    client = _client()
    if client is None:
        return None, {}, (
            "No OpenAI key — add OPENAI_API_KEY in Settings to enable "
            "post-image generation."
        )
    topic = (topic or "").strip()
    if not topic:
        return None, {}, "topic is required"
    chosen_aspect = (aspect or "").strip() or _POST_ASPECT.get(
        platform.lower(), "16:9"
    )
    size = _SIZE_FOR_ASPECT.get(chosen_aspect, "1536x1024")
    chosen_style = (style or _DEFAULT_STYLE).lower()
    if chosen_style not in POST_STYLES:
        chosen_style = _DEFAULT_STYLE
    # The prompt explicitly tells the model to preserve the recurring
    # subject from the references — without this nudge, gpt-image-1 will
    # sometimes ignore the reference identity for stylistic reasons.
    guidelines = await _brand_visual_directive(tenant_id)
    edit_prompt = (
        _build_post_prompt(topic, brief, chosen_style, guidelines)
        + " The recurring person from the reference photos must be "
        "rendered as the subject of this scene with the same face, "
        "build, hair, beard, and signature dress. Do not substitute "
        "a different person."
    )[:1000]
    # OpenAI's SDK accepts file-like inputs for images.edit. BytesIO
    # works directly; gpt-image-1 reads bytes regardless of extension.
    # Ref names are source URLs (reuse-ledger identity) — synthesize a
    # clean multipart filename here.
    from io import BytesIO
    image_files = [
        (f"hero-{i + 1}.png", BytesIO(data), "image/png")
        for i, (_name, data) in enumerate(references)
    ]
    try:
        res = await client.images.edit(
            model=settings.image_model,
            image=image_files,
            prompt=edit_prompt,
            size=size,
            n=1,
        )
    except Exception as e:  # noqa: BLE001
        return None, {}, f"image edit failed: {e}"
    item = res.data[0] if res.data else None
    b64 = getattr(item, "b64_json", None) if item else None
    if not b64:
        return None, {}, "image model returned no PNG bytes"
    try:
        png = base64.b64decode(b64)
    except Exception as e:  # noqa: BLE001
        return None, {}, f"could not decode image bytes: {e}"
    meta = {
        "prompt": edit_prompt,
        "size": size,
        "aspect": chosen_aspect,
        "platform": platform,
        "model": settings.image_model,
        "topic": topic,
        "style": chosen_style,
        "used_refs": len(references),
    }
    return png, meta, ""


async def edit_hero_photo(
    photo_bytes: bytes,
    instruction: str,
    *,
    size: str = "1024x1024",
    strong: bool = False,
    tenant_id=None,
) -> tuple[bytes | None, dict, str]:
    """Image-to-image edit of ONE existing photo — the ChatGPT/Gemini "here is the
    image, apply this change" behaviour behind the copilot's "Change the image" box.

    Two modes:
      • DEFAULT (strong=False): a faithful tweak — feeds the photo into gpt-image-1's
        edit endpoint with input_fidelity="high", so the shot is PRESERVED and only the
        owner's change is applied ("brighter", "warmer light", "make the sky orange").
      • strong=True: a VARIATION — reimagine the photo as a visibly different image on
        the same theme (no input_fidelity pin), for "change/swap the photo" on a brand
        with no other library photo to swap in, so the owner still gets a genuinely
        different picture instead of a look-alike.

    Either way the caller re-composites the brand's SAME text/layout on top with Pillow
    — the on-card text is NEVER sent to the model, so it can't be mangled.

    Returns (png, meta, err); err is a human line on any miss so the caller can fall
    back honestly (e.g. a text-only card has no photo to relight)."""
    client = _client()
    if client is None:
        return None, {}, "No OpenAI key — add OPENAI_API_KEY in Settings to edit images."
    instr = (instruction or "").strip()
    if not instr:
        return None, {}, "instruction is required"
    if not photo_bytes:
        return None, {}, "no photo to edit"
    from io import BytesIO

    # Match the model's output size to the photo's aspect so the edit doesn't crop
    # the shot (gpt-image only takes fixed sizes). Best-effort — keep `size` on miss.
    try:
        from PIL import Image as _Img
        with _Img.open(BytesIO(photo_bytes)) as _im:
            _w, _h = _im.size
        _ar = (_w / _h) if _h else 1.0
        size = "1536x1024" if _ar >= 1.2 else "1024x1536" if _ar <= 0.83 else "1024x1024"
    except Exception:  # noqa: BLE001 — a readable size default already stands
        pass

    image_files = [("current.png", BytesIO(photo_bytes), "image/png")]
    if strong:
        # VARIATION: produce a visibly DIFFERENT image on the same theme (used when a
        # swap has no other real photo to offer). Change composition/angle/setting so it
        # reads as a new photo — no input_fidelity pin, so it actually changes.
        edit_prompt = (
            "Reimagine this photograph as a visibly DIFFERENT, fresh image on the same "
            f"theme: {instr}. Change the composition, angle and setting so it clearly "
            "reads as a new photo, keeping it realistic and on-brand. Do not add any "
            "text, captions, logos or watermarks."
        )[:1000]
    else:
        # Change only what they asked; keep the rest of the photo as-is. No brand
        # directive and no text — the compositor re-applies the brand text/layout after.
        edit_prompt = (
            "Edit this photograph. Apply ONLY this change, keeping the same subject, "
            "composition and framing and everything the change does not mention: "
            f"{instr}. Do not add any text, captions, logos or watermarks."
        )[:1000]
    kwargs = dict(model=settings.image_model, image=image_files, prompt=edit_prompt, size=size, n=1)
    try:
        if strong:
            # No input_fidelity pin — a variation SHOULD depart from the input.
            res = await client.images.edit(**kwargs)
        else:
            try:
                # input_fidelity="high" is what makes this an EDIT (preserve the input)
                # rather than a fresh generation. It postdates older SDKs, so fall back
                # cleanly if the installed openai doesn't accept the kwarg.
                res = await client.images.edit(**kwargs, input_fidelity="high")
            except TypeError:
                res = await client.images.edit(**kwargs)
    except Exception as e:  # noqa: BLE001
        return None, {}, f"image edit failed: {e}"
    item = res.data[0] if res.data else None
    b64 = getattr(item, "b64_json", None) if item else None
    if not b64:
        return None, {}, "image model returned no edited bytes"
    try:
        png = base64.b64decode(b64)
    except Exception as e:  # noqa: BLE001
        return None, {}, f"could not decode edited image: {e}"
    return png, {"size": size, "model": settings.image_model, "instruction": instr}, ""


__all__ = [
    "generate_seed_image", "generate_post_image",
    "generate_post_image_with_refs", "edit_hero_photo", "POST_STYLES",
]
