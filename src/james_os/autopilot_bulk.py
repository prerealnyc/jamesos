"""Bulk content generation — one-click "make N pieces" for autopilot.

The daily `run_batch` (autopilot.py) produces a small fixed batch of
text drafts. This does the bigger, on-demand job the operator actually
asks for: "generate me N days of content, right now." It splits the
request 50/50 between two formats:

    N pieces  →  ceil(N/2) text+image posts  +  floor(N/2) video reels

(The odd one goes to text — a text+image post is cheaper and faster than
a render, so when the split is uneven we bias toward the cheap side.)

Every piece reuses the SAME engines the rest of the app already gates on,
so nothing here invents a new content path or a new approval surface:

  * text  → content.generate_content(brief)  — on-voice draft + voice-QA,
            queued as a pending `actions` row. We then generate a post
            hero image (imagegen.generate_post_image), persist it through
            the media storage layer, and patch the image URL onto that
            same queued action's payload so the human reviews text+image
            together.
  * video → video_pipeline.start_production(mode='engaging_avatar') — the
            durable render state machine; it lands its own pending
            `actions` row when it finishes. Renders take minutes, so we
            fire run_production as a background asyncio task and return
            immediately (the production rows / queue are the source of
            truth, not this call).

Topics come from the SAME virality-first intel + ideation path autopilot
uses (_gather_intel → generate_ideas), so bulk content stays trend-aligned
and on-voice rather than generic. If no live research provider is
configured the intel step returns None; we record that as an error per
the autopilot philosophy (no off-trend content) and queue nothing for the
affected half — honest, not a silent fake batch.

Resilient by construction: each item runs inside its own try/except and
appends to `errors` on failure, so one bad draft or one failed render
never kills the rest of the batch.

Nothing publishes. Like everywhere else in JAMES OS, the pending row in
`actions` is the gate a human approves before anything ships.
"""

import asyncio
import json
from uuid import UUID

from .autopilot import _gather_intel, generate_ideas, get_config, set_config, trend_steer
from .autopilot_templates import pick_distinct_templates
from .config import settings
from .content import generate_content
from .db import acquire
from .models import ContentBrief

# engaging_avatar is the simplest video mode that needs only a script
# (HeyGen avatar plays continuously, B-roll cut in at LLM-picked beats).
# It runs to completion on stubs and activates real providers from their
# keys — same honesty contract as the rest of the pipeline.
_VIDEO_MODE = "engaging_avatar"

# Caption-style rotation for batch reels — the finalise-candidates showcase.
# When cfg['rotate_captions'] is on, video j in a batch gets
# rotation[(offset + j) % len], and the offset persists across batches so
# every style gets seen on real renders, not just the first daily_count.
# Bold WHITE captions are the chosen default look (2026-06-28). Single-element
# rotation = no rotation; every batch reel uses the clean white reels caption.
# Other styles remain available for manual selection.
_CAPTION_ROTATION = ("bold_pop",)
_VIDEO_ASPECT = "9:16"

# Short, punchy steer for the video script writer. Reels want a spoken
# single-arc story, not an essay; the content engine still owns voice/QA.
_VIDEO_STEER = (
    "Write a SHORT spoken reel script (about 90-150 words) as a single-arc "
    "first-person story with a strong hook in the first line. No headings, "
    "no list — just the words James would say to camera."
)
_TEXT_STEER = (
    "Write as a single-arc first-person story, not a list. Decisive "
    "ownership, concrete specifics."
)


def _split(count: int, mix: str = "mixed") -> tuple[int, int]:
    """N → (n_text, n_video) for the requested mix.

    mix="video" → all video, mix="text" → all text+image, anything else
    → half and half with text getting the odd one (cheaper to produce).
    """
    n = max(0, count)
    if mix == "video":
        return 0, n
    if mix == "text":
        return n, 0
    n_video = n // 2
    n_text = n - n_video  # == n//2 + n%2
    return n_text, n_video


async def _attach_image_to_action(
    action_id: UUID,
    idea: dict,
    platform: str,
    draft_text: str,
    tenant_id: UUID | None,
) -> str | None:
    """Attach a REAL hero photo to a queued text action and patch its URL onto
    the action's payload (so the human reviews text+image as one item).

    NO AI-generated scenes. Designed posts use the branded templates; photo
    posts use the hero's OWN uploaded photos. Previously this directed a
    cinematic AI scene which, with no hero face to anchor, invented a generic
    stranger (e.g. "a young investor at a laptop") — exactly what the owner
    rejected. A real photo is picked from the hero library and rotated by the
    action id so a batch varies. Returns the URL, or None when no real hero
    photo exists (the text stands on its own — the image is additive).
    """
    import hashlib

    from .media import list_media

    photos = await list_media(role="hero_photo", tenant_id=tenant_id)
    # Real uploads ONLY — never AI-generated (source_type='generated'), matching
    # the single hero chokepoint in hero_context.get_hero_context.
    urls = [
        (m.get("uri") or "").strip()
        for m in photos
        if (m.get("uri") or "").startswith("http")
        and (m.get("source_type") or "") != "generated"
    ]
    if not urls:
        # No real hero photo → leave the post image-less rather than invent one.
        return None
    # Reuse-gated pick (James: "photo has been used before"): prefer the
    # least-recently-used photo across ALL recent posts, not an id-hash that
    # is blind to what other posts already used.
    from .photo_pick import pick_hero_url
    served_uri = await pick_hero_url(urls, tenant_id)
    if not served_uri:
        return None

    # Patch the real photo onto the pending action. jsonb `||` merges the keys
    # without disturbing the rest. No image_prompt — there is no AI prompt.
    async with acquire(tenant_id) as conn:
        await conn.execute(
            "UPDATE actions SET payload = payload || $2::jsonb WHERE id = $1",
            action_id,
            json.dumps(
                {
                    "image_url": served_uri,
                    "media_url": served_uri,
                    "has_image": True,
                    "hero_photo_key": served_uri,
                }
            ),
        )
    return served_uri


# Image-type rotation across a text+image batch: 'james' = a REAL hero photo
# (rotated from the library); 'designed' = the branded card machine (which
# itself rotates brand_quote / hero_quote / statement). Both use real photos or
# clean type — never an AI-generated scene. ~3-in-5 designed.
_IMAGE_MIX = ["designed", "james", "designed", "designed", "james"]


def _avoid_steer(feedback: str) -> str:
    """The owner's standing rejection notes, as a steer suffix.

    Was inlined in the text-post path only, so VIDEO drafts never saw it: a look
    or phrasing the owner had rejected could come back on every reel forever."""
    fb = (feedback or "").strip()
    if not fb:
        return ""
    return ("\n\nAVOID — the owner has rejected content for these reasons before; "
            "do NOT repeat them in wording, tone, claims, or style:\n" + fb[:1200])


async def _make_text_post(
    idea: dict, platform: str, tenant_id: UUID | None,
    image_kind: str = "james", avoid_fmt: str = "", force_format: str = "",
    feedback: str = "", canvas: tuple[int, int] | None = None,
) -> dict:
    """One text+image post: on-voice draft → queue → attach an image.

    image_kind 'james' attaches a REAL hero photo (rotated from the library);
    'designed' runs the branded card machine and, on any failure, falls back to
    a real hero photo so a post is never left with an AI scene or imageless.
    Returns the chosen designed format (or None) so the batch can vary them.

    `canvas` is (width, height) for the designed image — the destination
    platform's best shape, e.g. 1600x900 for X or 1080x1920 for a Reel. None
    keeps the 4:5 default, which is what every caller got before this existed.

    `feedback` carries the owner's standing rejection notes (what they keep saying
    is off-brand). It steers BOTH the text draft and the designed image away from
    what's been rejected — so a fresh post learns from past rejects, not just a
    same-post regeneration."""
    from .content import strip_internal_labels
    steer = _TEXT_STEER + trend_steer(idea) + _avoid_steer(feedback)
    draft = await generate_content(
        ContentBrief(
            platform=platform,
            format="post",
            pillar=idea.get("pillar", ""),
            topic=strip_internal_labels(idea["topic"]),
            extra_instructions=steer,
        ),
        tenant_id,
    )
    if not draft.action_id:
        # Engine refused (e.g. no voice corpus) — surface its reason.
        raise RuntimeError(draft.note or "content engine queued nothing")

    image_url = None
    fmt: str | None = None
    try:
        if image_kind == "designed":
            try:
                from . import image_compose as _ic
                from .main import _generate_designed_post_image
                # Render at the destination's shape. A cropped picture loses
                # whatever sat at the top and bottom — which on a quote card is
                # the quote — so the canvas is set BEFORE the compositor runs
                # rather than the output being cut to fit afterwards.
                with _ic.canvas(*(canvas or (0, 0))):
                    image_url, fmt = await _generate_designed_post_image(
                        draft.action_id, idea.get("topic", ""),
                        draft.draft or idea.get("topic", ""), tenant_id, avoid=avoid_fmt,
                        feedback=feedback, force_format=force_format,
                    )
            except Exception as _exc:  # noqa: BLE001 — designed failed → hero photo below
                # Loudly, not silently: a swallowed failure here is exactly why a
                # "Build carousel" could quietly come back as a plain photo post.
                import logging as _logging
                _logging.getLogger(__name__).warning(
                    "designed image failed (force_format=%r) — falling back to a "
                    "hero photo: %s", force_format, _exc,
                )
                image_url, fmt = None, None
        if not image_url:
            image_url = await _attach_image_to_action(
                draft.action_id, idea, platform, draft.draft, tenant_id
            )
    except Exception:  # noqa: BLE001 — image is additive; keep the queued text
        image_url = None

    return {
        "kind": "text",
        "title": idea.get("title", ""),
        "platform": draft.platform,
        "action_id": str(draft.action_id),
        "voice_score": draft.voice_score,
        "status": draft.status,
        "image": bool(image_url),
        "format": fmt if (image_url and fmt) else None,
    }


async def _make_reel_script(
    idea: dict, platform: str, tenant_id: UUID | None, feedback: str = ""
) -> dict:
    """A reel SCRIPT draft (text) — lands in the Approval Queue. HeyGen-avatar
    videos are off and autopilot can't cut the upload+B-roll+caption reel
    itself, so we hand off a ready script to render from real footage via the
    Long-form cutter."""
    draft = await generate_content(
        ContentBrief(
            platform=platform,
            format="reel_script",
            pillar=idea.get("pillar", ""),
            topic=idea["topic"],
            extra_instructions=_TEXT_STEER + trend_steer(idea) + _avoid_steer(feedback),
        ),
        tenant_id,
    )
    if not draft.action_id:
        raise RuntimeError(draft.note or "content engine queued nothing")
    return {
        "kind": "reel_script",
        "title": idea.get("title", ""),
        "platform": draft.platform,
        "action_id": str(draft.action_id),
        "voice_score": draft.voice_score,
        "status": draft.status,
    }


async def _make_video(
    idea: dict, platform: str, tenant_id: UUID | None,
    template: dict | None = None, broll_engine: str = "",
    caption_style: str = "", smart_captions: bool = False,
    video_template: str = "", feedback: str = "",
) -> dict:
    """One video reel: write a short on-voice script, then kick a durable
    production (rendered fire-and-forget — it queues its own pending action
    when it finishes).

    The CONTENT (script) is identical regardless of style: it comes from the
    same generate_content call. When a style `template` is assigned, only the
    STYLE changes — mode, caption preset, music mood, logo, structure, aspect
    are mapped from the template and the production is tagged with template_id.
    No template (empty library / flag off) → the unchanged engaging_avatar
    path, so default behavior is byte-for-byte the same.
    """
    from .video_pipeline import run_production, start_production

    draft = await generate_content(
        ContentBrief(
            platform=platform,
            format="reel_script",
            pillar=idea.get("pillar", ""),
            topic=idea["topic"],
            extra_instructions=_VIDEO_STEER + trend_steer(idea) + _avoid_steer(feedback),
        ),
        tenant_id,
    )
    script = (draft.draft or "").strip()
    if not script:
        raise RuntimeError(draft.note or "no script produced for the reel")

    # Reel FORMAT template (the two saved looks). When set it drives the
    # layout + caption style, overriding any style-template/rotation defaults:
    #   "split" → avatar-top / B-roll-bottom 50/50, white captions
    #   "full"  → full-frame avatar + B-roll cutaways, white captions
    # A style template (Design Inspector) still supplies image_style/music/etc.
    forced_mode = forced_caption = ""
    if video_template == "split":
        forced_mode, forced_caption = "split_horizontal", "bold_pop"
    elif video_template == "full":
        forced_mode, forced_caption = "engaging_avatar", "bold_pop"

    title = (idea.get("title") or "")[:120]
    if template and template.get("template"):
        from .template_apply import map_template_to_render
        m = map_template_to_render(template.get("template") or {})
        prod = await start_production(
            script=script,
            platform=platform,
            # Match the template's measured aspect (the inspector now probes
            # real dimensions); fall back to the vertical social default.
            aspect=m["aspect"] or _VIDEO_ASPECT,
            title=title,
            mode=forced_mode or m["mode"],
            # Caption precedence: format template > explicit rotation style >
            # smart-mode blank (pipeline LLM picks) > the style template preset.
            caption_style=forced_caption or caption_style or ("" if smart_captions else m["caption_style"]),
            image_style=m["image_style"],
            music_mood=m["music_mood"],
            logo_position=m["logo_position"] if m["logo_on"] else "",
            structure=m["structure"],
            template_id=UUID(template["id"]),
            video_engine=broll_engine,
            tenant_id=tenant_id,
        )
        applied_mode = forced_mode or m["mode"]
    else:
        prod = await start_production(
            script=script,
            platform=platform,
            aspect=_VIDEO_ASPECT,
            title=title,
            mode=forced_mode or _VIDEO_MODE,
            caption_style=forced_caption or caption_style,
            video_engine=broll_engine,
            tenant_id=tenant_id,
        )
        applied_mode = forced_mode or _VIDEO_MODE

    # Fire-and-forget: renders take minutes. The production row + the
    # pending action it lands are the durable record; we don't await it.
    # Bind tenant explicitly so the detached task isn't subject to the
    # request contextvar going away when this call returns.
    task = asyncio.create_task(
        run_production(UUID(prod["id"]), tenant_id)
    )
    # Hold a reference so the task isn't garbage-collected mid-flight, and
    # swallow its result/exception (the pipeline records failures on the row).
    _BACKGROUND_RENDERS.add(task)
    task.add_done_callback(_BACKGROUND_RENDERS.discard)

    return {
        "kind": "video",
        "title": idea.get("title", ""),
        "platform": platform,
        "production_id": prod["id"],
        "mode": applied_mode,
        "status": prod.get("status", "queued"),
        "template_id": template["id"] if template else None,
        "template_name": template.get("name") if template else None,
        "caption_style": caption_style or "(auto)",
    }


# Strong refs to detached render tasks so the event loop doesn't GC them.
_BACKGROUND_RENDERS: set[asyncio.Task] = set()


async def generate_bulk(
    count: int, days: int = 0, tenant_id: UUID | None = None,
    mix: str = "mixed",
) -> dict:
    """Generate `count` pieces of content in one shot.

    `mix` controls the content type: "video" → all video reels, "text" →
    all text+image posts, "mixed" (default) → 50/50 with text getting the
    odd one.

    `days` is accepted for API symmetry (the caller may think in "N days of
    content") but the unit of work is pieces — N pieces total. When `days`
    is given and `count` is 0, we treat days as the count so the endpoint
    can be driven either way.

    Returns:
        {requested, text_queued, video_queued, errors: [...]}

    Everything lands as pending rows in `actions` (text immediately; video
    when its background render finishes). Resilient: a single item failure
    is recorded in `errors` and never aborts the batch.
    """
    requested = int(count) if count else int(days)
    mix = (mix or "mixed").strip().lower()
    n_text, n_video = _split(requested, mix)
    errors: list[str] = []

    if requested <= 0:
        return {"requested": 0, "text_queued": 0, "video_queued": 0, "errors": []}

    cfg = await get_config(tenant_id)
    platforms = cfg.get("platforms") or ["instagram"]
    platform = platforms[0]

    # ── Virality-first ideation, exactly like run_batch ──
    # We ideate enough topics for the whole batch in one shot, then hand
    # them out to the two producers.
    intel = await _gather_intel(cfg, tenant_id)
    ideas: list[dict] = []
    if intel is None:
        errors.append(
            "No live market research/trends available — bulk generation "
            "ideates from research to stay trend-aligned. Add a Perplexity "
            "key (and Apify for scraped trends), then retry."
        )
    else:
        try:
            ideas = await generate_ideas(requested, intel, tenant_id)
        except Exception as e:  # noqa: BLE001
            errors.append(f"ideation failed: {e}")

    if not ideas:
        # No topics → nothing to produce. Errors already explain why.
        return {
            "requested": requested,
            "text_queued": 0,
            "video_queued": 0,
            "errors": errors,
        }

    # Hand out ideas round-robin: first n_text to text, next n_video to
    # video. If ideation returned fewer than requested, cycle through what
    # we got so both halves still get topics.
    def _idea_at(i: int) -> dict:
        return ideas[i % len(ideas)]

    text_queued = 0
    video_queued = 0
    script_queued = 0

    # ── Text+image posts — rotate the image TYPE across the batch (James photo
    # vs designed quote/meme/statement card) so it's a real mix of looks, not
    # five James photos. The designed machine rotates its three formats too. ──
    _fmt_counts: dict[str, int] = {}

    def _avoid_fmt() -> str:
        if not _fmt_counts:
            return ""
        top = max(_fmt_counts, key=lambda k: _fmt_counts[k])
        return top if _fmt_counts[top] >= 2 else ""

    for i in range(n_text):
        try:
            kind = _IMAGE_MIX[i % len(_IMAGE_MIX)] if _IMAGE_MIX else "james"
            r = await _make_text_post(
                _idea_at(i), platform, tenant_id,
                image_kind=kind, avoid_fmt=_avoid_fmt() if kind == "designed" else "",
            )
            f = r.get("format")
            if f:
                _fmt_counts[f] = _fmt_counts.get(f, 0) + 1
            text_queued += 1
        except Exception as e:  # noqa: BLE001 — one bad post can't kill the batch
            errors.append(f"text {i + 1}/{n_text}: {e}")

    # ── Video slots ──
    # HeyGen-avatar videos are OFF by default (unapproved). Autopilot can't cut
    # the upload+B-roll+caption reel type itself, so each video slot becomes a
    # reel SCRIPT draft to render from real footage. Flip cfg.avatar_videos on
    # to restore avatar renders.
    avatar_videos = bool(cfg.get("avatar_videos", False))
    video_results: list[dict] = []
    if not avatar_videos:
        for j in range(n_video):
            try:
                await _make_reel_script(_idea_at(n_text + j), platform, tenant_id)
                script_queued += 1
            except Exception as e:  # noqa: BLE001
                errors.append(f"script {j + 1}/{n_video}: {e}")
    else:
        # Each reel in the batch gets a DISTINCT style from the template library
        # (cycling when the batch has more videos than styles). Empty library or
        # flag off → chosen is [] and every reel uses the standard look.
        use_tpls = bool(cfg.get("use_style_templates", True))
        broll_engine = str(cfg.get("broll_engine", "") or "").strip().lower()
        caption_mode = str(cfg.get("caption_mode", "rotate") or "rotate").strip().lower()
        rotate_captions = caption_mode == "rotate"
        smart_captions = caption_mode == "smart"
        rot_offset = int(cfg.get("caption_rotation_offset", 0) or 0)
        chosen = await pick_distinct_templates(n_video, tenant_id) if use_tpls else []
        for j in range(n_video):
            try:
                tpl = chosen[j] if j < len(chosen) else None
                cap_style = (
                    _CAPTION_ROTATION[(rot_offset + j) % len(_CAPTION_ROTATION)]
                    if rotate_captions else ""
                )
                # Alternate the two saved reel templates per video: even = full-frame
                # avatar (magenta-on-black), odd = split 50/50 (magenta-on-white).
                video_template = "split" if (j % 2) else "full"
                video_results.append(
                    await _make_video(
                        _idea_at(n_text + j), platform, tenant_id,
                        template=tpl, broll_engine=broll_engine,
                        caption_style=cap_style, smart_captions=smart_captions,
                        video_template=video_template,
                    )
                )
                video_queued += 1
            except Exception as e:  # noqa: BLE001
                errors.append(f"video {j + 1}/{n_video}: {e}")

        # Advance the rotation so the NEXT batch continues where this one stopped
        # (otherwise every batch would re-show the same first daily_count styles).
        if rotate_captions and n_video:
            try:
                await set_config(
                    {"caption_rotation_offset": (rot_offset + n_video) % len(_CAPTION_ROTATION)},
                    tenant_id, internal=True,
                )
            except Exception:  # noqa: BLE001 — provenance only, never fail the batch
                pass

    # Backstop: re-interpret any feedback given since the last batch so the
    # "What's changing next" board is at-least-daily current even if no
    # reject hook fired (e.g. feedback left in an older client).
    from .feedback_interpreter import kick_interpret_background
    kick_interpret_background(tenant_id)

    return {
        "requested": requested,
        "text_queued": text_queued,
        "video_queued": video_queued,
        # Reel SCRIPT drafts emitted when avatar videos are off (the default).
        "script_queued": script_queued,
        # Per-video style provenance — which template each reel was produced in.
        "videos": [
            {
                "production_id": v.get("production_id"),
                "template_id": v.get("template_id"),
                "template_name": v.get("template_name"),
                "mode": v.get("mode"),
                "caption_style": v.get("caption_style"),
            }
            for v in video_results
        ],
        "errors": errors,
    }


__all__ = ["generate_bulk"]
