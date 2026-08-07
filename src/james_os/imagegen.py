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
    "carousel": "carousel",
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
}

_DESIGN_DIRECTOR_SYSTEM = (
    "You are the art director for a scroll-stopping Instagram IMAGE that "
    "accompanies a brand's post. Choose the single best visual FORMAT for THIS "
    "post and write the short on-image text. Text is overlaid later in perfect "
    "type — write it, never describe it, and keep on-image copy to ONE short "
    "idea (a stranger should grasp it in under 1.5 seconds).\n\n"
    "NINE formats — ALL use clean brand type over the brand's REAL photo or a "
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
    "  * carousel — a multi-slide SET (up to 10) for a list ('N ways/reasons'), "
    "a step-by-step, or several proof points/stats: pick this when the post has "
    "SEVERAL distinct points that each deserve their own slide. Its slides are "
    "written separately — just choose this format.\n\n"
    "Return STRICT JSON with ALL keys (fill only what the chosen format needs, "
    "leave the rest \"\"):\n"
    "{\n"
    '  "format": "brand_quote"|"bold_statement"|"big_stat"|"full_bleed"|'
    '"editorial_split"|"minimal_over"|"framed_print"|"hero_quote"|"statement"|"carousel",\n'
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


async def direct_designed_image(
    draft_text: str, topic: str = "", avoid: str = "",
    feedback: str = "", force_format: str = "", allow_v2: bool | None = None,
    voice: str = "", brand_profile: str = "",
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
            # No clear number → don't fake a stat card; render a quote card.
            spec.update(format="brand_quote", bg_kind="none",
                        quote=(spec["quote"] or line))
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
    '  "cover": {"headline": "<=8 words", "count_promise": "e.g. 6 REASONS / 5 '
    'STEPS — <=3 words; MUST equal the number of inner slides", "kicker": '
    '"<=3 words"},\n'
    '  "slides": [{"kind": "photo"|"stat", "section_label": "<=3 words", '
    '"headline": "<=9 words", "stat": "<stat kind only: 1–3 words, e.g. 1ST, 20 '
    'YRS, 90%>"}],\n'
    '  "cta": {"action": "1–2 words, e.g. VISIT, BOOK, FOLLOW", "ask": '
    '"<=10 words"}\n'
    "}\n"
    "4–7 inner slides. The cover count_promise MUST match the number of inner "
    "slides. One atomic idea per slide. VOICE IS PRIMARY: every cover line, slide "
    "headline and CTA MUST use the brand's own words and cadence from the "
    "<brand_voice> block and the <draft> — lift real phrases, never invent generic "
    "ones. The swipe/cover/CTA rules above are SECONDARY structure only; when they "
    "conflict with the brand's wording, VOICE WINS. No fluff."
)


async def direct_carousel_deck(draft_text: str, topic: str = "", brand_name: str = "",
                               voice: str = "", brand_profile: str = "") -> dict:
    """LLM → a structured carousel deck {arc, cover, slides[], cta}. Photos are
    assigned later from the brand's library; the model only writes text and picks
    photo-vs-stat per slide. Best-effort with a deck built from the draft."""
    text = (draft_text or topic or "").strip()
    parts = [p.strip() for p in text.split(". ") if p.strip()]
    fb_slides = [{"kind": "photo", "section_label": "", "headline": p[:80], "stat": ""}
                 for p in parts[1:5]]
    fallback = {
        "arc": "listicle",
        "cover": {"headline": (parts[0][:80] if parts else (topic or "")),
                  "count_promise": "", "kicker": brand_name[:24]},
        "slides": fb_slides if len(fb_slides) >= 2 else [
            {"kind": "photo", "section_label": "", "headline": (parts[0][:80] if parts else topic), "stat": ""},
            {"kind": "photo", "section_label": "", "headline": (topic or "")[:80], "stat": ""},
        ],
        "cta": {"action": "LEARN MORE", "ask": ""},
    }
    if not text:
        return fallback
    try:
        from .llm import get_llm
        out = await get_llm().complete_json(
            system=_CAROUSEL_SYSTEM,
            messages=[{"role": "user", "content": (
                (f"<brand_voice>\n{voice[:1500]}\n</brand_voice>\n\n" if voice else "")
                + (f"{brand_profile}\n\n" if brand_profile else "")
                + f"<draft>\n{text[:2500]}\n</draft>")}],
            max_tokens=800, temperature=0.6,
        )
        out = out or {}
        slides = []
        for s in (out.get("slides") or [])[:8]:
            if not isinstance(s, dict):
                continue
            row = {
                "kind": "stat" if str(s.get("kind")).lower() == "stat" else "photo",
                "section_label": str(s.get("section_label") or "").strip(),
                "headline": str(s.get("headline") or "").strip(),
                "stat": str(s.get("stat") or "").strip(),
            }
            if row["headline"] or row["stat"]:
                slides.append(row)
        if len(slides) < 2:
            return fallback
        cover = out.get("cover") or {}
        cta = out.get("cta") or {}
        return {
            "arc": str(out.get("arc") or "listicle"),
            "cover": {
                "headline": str(cover.get("headline") or "").strip() or fallback["cover"]["headline"],
                "count_promise": str(cover.get("count_promise") or "").strip(),
                "kicker": (str(cover.get("kicker") or "").strip() or brand_name)[:24],
            },
            "slides": slides,
            "cta": {
                "action": str(cta.get("action") or "LEARN MORE").strip(),
                "ask": str(cta.get("ask") or "").strip(),
            },
        }
    except Exception:  # noqa: BLE001
        return fallback


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


__all__ = [
    "generate_seed_image", "generate_post_image",
    "generate_post_image_with_refs", "POST_STYLES",
]
