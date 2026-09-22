"""The design template library — every still layout learned from a reference
post, kept forever, and drawn on by autopilot.

The owner's rules for this:

  * autopilot uses the reference -> new-template engine, not only the nine
    hand-built formats;
  * the competitor images we already collect are read into templates;
  * "don't throw away any layouts — keep all data in-house";
  * templates work at every social platform's recommended size.

design_cloner already turned a reference post into a structured layout spec, and
spec_render already drew one. What was missing is the middle: the spec was kept
only on the single sample it produced (actions.payload.clone_spec) and never
reused, so every layout the system learned was used once and then lost, and
autopilot never saw any of them.

This module is that middle. It is the only writer of design_templates, and it
never deletes — the table does not even grant the app DELETE. A layout can be
retired (hidden from autopilot); it cannot be removed.

IN-HOUSE. A template is learned from OUR durable copy of the reference image
(competitor_posts.stored_media_url), never from the source URL, which for
Instagram and TikTok expires within days. The template keeps a pointer to that
copy, so the layout and the picture it came from both survive the platform.

ONE ROW, EVERY SIZE. A spec's boxes are fractions of the canvas, so the same row
renders at 4:5, 16:9, 9:16 and 2:3 (see spec_render + image_compose.canvas).
There is no per-platform copy of a template to drift out of sync.
"""

from __future__ import annotations

import copy
import hashlib
import json
import logging
from uuid import UUID

from .db import acquire

logger = logging.getLogger("design_templates")

# Minimum learned layouts before autopilot will draw on them at all. Below this
# the library is a couple of lucky reads, and rotating between them would make
# the brand's feed look MORE repetitive than the nine formats, not less.
MIN_LIBRARY = 3
# How many active layouts pick() weighs at once, in rotation order.
PICK_WINDOW = 200

# A layout that keeps failing design QA stops being offered. It is not deleted —
# retired, with its record intact — because the owner asked that nothing be
# thrown away, and a later renderer may draw it fine.
RETIRE_AFTER_QA_FAILS = 3


def fingerprint(spec: dict) -> str:
    """A hash of a layout's STRUCTURE — what makes it this layout.

    Colours are left out on purpose: every template is rebranded to the brand's
    palette before it is drawn, so two posts with the same arrangement in
    different colours are the same template. Boxes are rounded to the nearest 5%
    so a layout read twice with a pixel of jitter is still one row.
    """
    def _box(b):
        if not isinstance(b, dict):
            return None
        return tuple(round(float(b.get(k, 0)) * 20) for k in ("x", "y", "w", "h"))

    bg = spec.get("background") or {}
    shape = {
        "kind": spec.get("kind"),
        "treatment": bg.get("treatment"),
        "scrim": bg.get("scrim"),
        "photo_box": _box(bg.get("photo_box")),
        "elements": sorted(
            (e.get("role"), _box(e.get("box")), e.get("align"), e.get("size"),
             e.get("weight"), e.get("case"))
            for e in (spec.get("elements") or []) if isinstance(e, dict)
        ),
        "decorations": sorted(
            (d.get("type"), _box(d.get("box")))
            for d in (spec.get("decorations") or []) if isinstance(d, dict)
        ),
    }
    raw = json.dumps(shape, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


def usable(spec: dict | None) -> bool:
    """A spec worth keeping: it read something, and has text to put somewhere.

    A 'no_key' or 'failed' read from design_cloner is honest about being empty
    and must not become a template that renders a blank card."""
    if not isinstance(spec, dict) or spec.get("status") in ("no_key", "failed"):
        return False
    return bool(spec.get("elements"))


# ── making a read layout drawable ──────────────────────────────────────────
# A vision read is a faithful description of someone else's post, not a clean
# template. Rendering the real library at every platform size showed what that
# means in practice: three "byline" slots printing the brand name three times,
# two subhead boxes overlapping so the lines garble, an outlined frame that held
# an inset photo in the original and is now an empty rectangle, and a white card
# whose photo the read missed — a headline in the top fifth and nothing else.
# prepare() fixes what is fixable; drawable() says whether what is left is worth
# putting in front of the owner. Every read is still KEPT (save() is unchanged) —
# these only decide what autopilot draws.

# When two text boxes collide, the more important one stays.
_ROLE_RANK = {"headline": 0, "stat": 1, "subhead": 2, "kicker": 3, "stat_label": 4,
              "cta": 5, "byline": 6}
_SIZE_RANK = {"xxl": 0, "xl": 1, "lg": 2, "md": 3, "sm": 4}
# Share of the smaller box two text boxes may share before one is dropped.
MAX_TEXT_OVERLAP = 0.25
# A layout with no photo whose text spans less than this share of the height is
# a card with a hole in it — almost always a photo the read did not see.
MIN_TEXT_SPAN_NO_PHOTO = 0.35


def _area(b: dict) -> float:
    return max(0.0, float(b.get("w", 0))) * max(0.0, float(b.get("h", 0)))


def _overlap(a: dict, b: dict) -> float:
    """Shared area as a share of the SMALLER box (0..1)."""
    ix = min(a.get("x", 0) + a.get("w", 0), b.get("x", 0) + b.get("w", 0)) - max(a.get("x", 0), b.get("x", 0))
    iy = min(a.get("y", 0) + a.get("h", 0), b.get("y", 0) + b.get("h", 0)) - max(a.get("y", 0), b.get("y", 0))
    if ix <= 0 or iy <= 0:
        return 0.0
    small = min(_area(a), _area(b))
    return (ix * iy) / small if small > 0 else 0.0


def base_role(role: str) -> str:
    """"byline#2" -> "byline"."""
    return str(role or "").split("#", 1)[0]


def prepare(spec: dict) -> dict:
    """A copy of `spec` that can be filled and drawn cleanly.

      * Colliding text boxes: the more important element stays (headline over
        stat over subhead ... over byline; larger size breaks a tie), the other
        is dropped. Draw order is otherwise unchanged.
      * Repeated roles are numbered — byline, byline#2, byline#3 — so each slot
        is written as its own line instead of all of them printing the same one.
      * A frame with no text inside it is dropped: it framed an inset photo in
        the original, and on its own it is an empty rectangle.
    """
    out = copy.deepcopy(spec) if isinstance(spec, dict) else {}
    els = [e for e in (out.get("elements") or []) if isinstance(e, dict) and isinstance(e.get("box"), dict)]

    order = sorted(range(len(els)), key=lambda i: (
        _ROLE_RANK.get(base_role(els[i].get("role")), 9),
        _SIZE_RANK.get(els[i].get("size"), 5), i))
    kept: list[int] = []
    for i in order:
        if all(_overlap(els[i]["box"], els[j]["box"]) <= MAX_TEXT_OVERLAP for j in kept):
            kept.append(i)
    els = [els[i] for i in sorted(kept)]

    seen: dict[str, int] = {}
    for e in els:
        r = base_role(e.get("role")) or "line"
        seen[r] = seen.get(r, 0) + 1
        e["role"] = r if seen[r] == 1 else f"{r}#{seen[r]}"
    out["elements"] = els

    def _has_text_inside(box: dict) -> bool:
        for e in els:
            b = e["box"]
            cx, cy = b.get("x", 0) + b.get("w", 0) / 2, b.get("y", 0) + b.get("h", 0) / 2
            if box.get("x", 0) <= cx <= box.get("x", 0) + box.get("w", 0) and \
               box.get("y", 0) <= cy <= box.get("y", 0) + box.get("h", 0):
                return True
        return False

    out["decorations"] = [
        d for d in (out.get("decorations") or [])
        if isinstance(d, dict) and not (d.get("type") == "frame"
                                        and not _has_text_inside(d.get("box") or {}))
    ]
    return out


def drawable(spec: dict | None) -> bool:
    """Is this layout — once prepared — worth drawing a post from?"""
    if not usable(spec):
        return False
    prepped = prepare(spec)
    els = prepped.get("elements") or []
    if not els:
        return False
    bg = prepped.get("background") or {}
    has_photo = str(bg.get("treatment") or "solid") != "solid" or bool(bg.get("photo_box"))
    if not has_photo:
        top = min(float(e["box"].get("y", 0)) for e in els)
        bottom = max(float(e["box"].get("y", 0)) + float(e["box"].get("h", 0)) for e in els)
        if bottom - top < MIN_TEXT_SPAN_NO_PHOTO:
            return False
    return True


async def save(
    tenant_id: UUID | str | None,
    spec: dict,
    *,
    source_kind: str = "competitor",
    source_post_id: str | None = None,
    source_url: str = "",
    source_image_uri: str = "",
    source_handle: str = "",
    source_platform: str = "",
    source_engagement: float = 0.0,
) -> str | None:
    """Keep a learned layout. Returns its id, or None if it was not usable.

    Idempotent twice over: one row per source post, and one row per distinct
    structure. Seeing the same layout again returns the existing row rather than
    adding a duplicate — the library grows by NEW layouts, not by repeats.
    """
    if not usable(spec):
        return None
    fp = fingerprint(spec)
    async with acquire(tenant_id) as conn:
        existing = await conn.fetchval(
            "SELECT id FROM design_templates WHERE fingerprint = $1 "
            "OR ($2::uuid IS NOT NULL AND source_post_id = $2::uuid) LIMIT 1",
            fp, source_post_id,
        )
        if existing:
            return str(existing)
        row = await conn.fetchval(
            """INSERT INTO design_templates
                   (spec, kind, source_kind, source_post_id, source_url,
                    source_image_uri, source_handle, source_platform,
                    source_engagement, rubric_version, fingerprint)
               VALUES ($1::jsonb, $2, $3, $4::uuid, $5, $6, $7, $8, $9, $10, $11)
               ON CONFLICT DO NOTHING
               RETURNING id""",
            json.dumps(spec), str(spec.get("kind") or "photo_forward"), source_kind,
            source_post_id, source_url, source_image_uri, source_handle,
            source_platform, float(source_engagement or 0), str(spec.get("rubric_version") or ""),
            fp,
        )
        if row is None:  # lost a race to an identical insert — return the winner
            row = await conn.fetchval(
                "SELECT id FROM design_templates WHERE fingerprint = $1 LIMIT 1", fp
            )
    return str(row) if row else None


async def count(tenant_id: UUID | str | None, *, active_only: bool = True) -> int:
    async with acquire(tenant_id) as conn:
        return int(await conn.fetchval(
            "SELECT count(*) FROM design_templates"
            + (" WHERE status = 'active'" if active_only else "")
        ) or 0)


async def pick(tenant_id: UUID | str | None) -> dict | None:
    """The layout autopilot should use next, or None to use the nine formats.

    Least recently used first, so the feed rotates through the library instead of
    leaning on one layout. Ties go to the layouts the owner has approved more and
    design QA has failed less. A library below MIN_LIBRARY returns None: a couple
    of learned layouts on rotation looks more repetitive than the nine, not less.
    """
    async with acquire(tenant_id) as conn:
        rows = await conn.fetch(
            """SELECT id, spec, kind, source_handle, source_url, source_platform,
                      source_kind
                 FROM design_templates
                WHERE status = 'active'
             ORDER BY last_used_at NULLS FIRST,
                      (approvals - rejections) DESC,
                      qa_fails ASC,
                      source_engagement DESC
                LIMIT $1""",
            PICK_WINDOW,
        )
    # Only layouts that draw cleanly count — toward the minimum, and as picks.
    # The rest stay in the library (nothing read is thrown away); they are just
    # not put in front of the owner.
    good = []
    for r in rows:
        spec = r["spec"]
        if isinstance(spec, str):
            spec = json.loads(spec)
        if drawable(spec):
            good.append((r, spec))
    if len(good) < MIN_LIBRARY:
        return None
    row, spec = good[0]
    return {
        "id": str(row["id"]), "spec": spec, "kind": row["kind"],
        "source_handle": row["source_handle"], "source_url": row["source_url"],
        "source_platform": row["source_platform"], "source_kind": row["source_kind"],
    }


async def mark_used(tenant_id: UUID | str | None, template_id: str) -> None:
    async with acquire(tenant_id) as conn:
        await conn.execute(
            "UPDATE design_templates SET times_used = times_used + 1, "
            "last_used_at = now(), updated_at = now() WHERE id = $1::uuid",
            template_id,
        )


async def mark_qa(tenant_id: UUID | str | None, template_id: str, passed: bool) -> None:
    """Record a design-QA result. A layout that keeps failing is RETIRED —
    hidden from autopilot, never deleted."""
    async with acquire(tenant_id) as conn:
        if passed:
            await conn.execute(
                "UPDATE design_templates SET qa_passes = qa_passes + 1, updated_at = now() "
                "WHERE id = $1::uuid", template_id)
        else:
            await conn.execute(
                "UPDATE design_templates SET qa_fails = qa_fails + 1, updated_at = now(), "
                "status = CASE WHEN qa_fails + 1 >= $2 THEN 'retired' ELSE status END "
                "WHERE id = $1::uuid", template_id, RETIRE_AFTER_QA_FAILS)


async def mark_verdict(tenant_id: UUID | str | None, template_id: str, approved: bool) -> None:
    """The owner's approve / reject of a post built on this layout."""
    col = "approvals" if approved else "rejections"
    async with acquire(tenant_id) as conn:
        await conn.execute(
            f"UPDATE design_templates SET {col} = {col} + 1, updated_at = now() "
            "WHERE id = $1::uuid", template_id)


# ----------------------------------------------------------- learning the library


async def _unlearned_competitor_stills(tenant_id, limit: int) -> list[dict]:
    """Competitor stills we hold an in-house copy of and have not yet read.

    WHAT THE OWNER PICKED comes first — a still they marked "Layout only" is
    them saying, in as many words, *mint me a template from this*, and reading
    the shelf by engagement alone ignored that. "Layout + writing" and "Idea
    only" follow, then everything else by engagement, so a brand that picked
    nothing behaves exactly as it did before.

    A post marked "Not for me" is never read at all. Each read is a paid vision
    call, and spending one on a layout the owner has already rejected is the
    one case where the money buys something worse than nothing.

    A post with no stored copy is skipped rather than fetched from its source
    URL — the owner's rule is in-house, and a layout we cannot keep the picture
    for is a layout we cannot show the provenance of."""
    async with acquire(tenant_id) as conn:
        rows = await conn.fetch(
            r"""SELECT p.id, p.stored_media_url, p.url, p.platform, p.engagement_rate,
                      c.handle, c.status AS shelf_status, c.discovered_via
                 FROM competitor_posts p
                 JOIN competitors c ON c.id = p.competitor_id
                WHERE p.stored_media_url <> ''
                  -- A video post whose stored copy is a PICTURE counts: a
                  -- YouTube thumbnail is a designed card (big headline, face,
                  -- brand colours) and in some niches it is the ONLY designed
                  -- still anyone posts. What we hold decides, not the label.
                  AND (p.media_type IN ('image', 'carousel')
                       OR p.stored_media_url ~* '\.(jpe?g|png|webp)$')
                  AND p.template_read_at IS NULL
                  -- "Not for me" is a verdict about the LAYOUT too.
                  AND coalesce(p.replicate_status, '') <> 'skipped'
             -- likes breaks the tie: a niche reference has no follower base to
             -- divide by, so its engagement_rate is 0 and its real signal is
             -- the interaction count itself.
             ORDER BY (p.replicate_status = 'template') DESC,
                      (p.replicate_status IN ('saved','idea')) DESC,
                      p.engagement_rate DESC NULLS LAST, p.likes DESC
                LIMIT $1""",
            max(1, min(int(limit), 50)),
        )
    return [dict(r) for r in rows]


async def _mark_read(tenant_id, post_id) -> None:
    async with acquire(tenant_id) as conn:
        await conn.execute(
            "UPDATE competitor_posts SET template_read_at = now() WHERE id = $1", post_id)


# A read that FAILED (the vision call errored, timed out, was rate-limited) is
# not a verdict on the post — it is an outage. It is retried on later runs and
# only given up on after this many failures, so a post that genuinely breaks the
# reader cannot hold a slot in every run forever.
MAX_READ_ATTEMPTS = 3


async def _note_failed_read(tenant_id, post_id) -> None:
    async with acquire(tenant_id) as conn:
        await conn.execute(
            "UPDATE competitor_posts SET template_read_attempts = template_read_attempts + 1, "
            "template_read_at = CASE WHEN template_read_attempts + 1 >= $2 THEN now() "
            "ELSE template_read_at END WHERE id = $1", post_id, MAX_READ_ATTEMPTS)


async def learn_from_competitors(tenant_id, *, limit: int = 12) -> dict:
    """Read the competitor images we already collect into new templates.

    Each post is read ONCE, whatever the read produced. It is marked
    (competitor_posts.template_read_at) after a new template, after a duplicate
    of one we already hold, and after an unusable read alike — because each read
    is a paid vision-model call, and "excluded once it has a template" alone
    would re-read every duplicate and every blank on every run, forever.

    What is NOT a verdict does not mark it, because "don't throw away any
    layouts" includes the ones we failed to read:
      * the in-house copy could not be fetched — a storage miss; tried next time;
      * no vision key is configured — nothing can be read, so the run stops
        before touching a single post, and they are all read once a key exists;
      * the read errored — retried, and given up on only after
        MAX_READ_ATTEMPTS failures.
    """
    from .design_cloner import extract_template_spec
    from .template_clone import _fetch_bytes

    posts = await _unlearned_competitor_stills(tenant_id, limit)
    learned, duplicate, unreadable, failed = 0, 0, 0, 0
    for p in posts:
        img = await _fetch_bytes(p["stored_media_url"])
        if not img:
            unreadable += 1
            continue
        try:
            spec = await extract_template_spec(img)
        except Exception:  # noqa: BLE001 — one unreadable post must not end the run
            logger.warning("could not read a layout from %s", p["id"], exc_info=True)
            spec = {"status": "failed"}
        status = spec.get("status") if isinstance(spec, dict) else "failed"
        if status == "no_key":
            # Every post would come back the same way. Stop, mark nothing.
            logger.warning("design templates: no vision key — %d competitor posts wait unread",
                           len(posts))
            return {"read": 0, "learned": learned, "duplicate": duplicate,
                    "unreadable": unreadable, "reason": "no_key",
                    "library": await count(tenant_id)}
        if status == "failed":
            failed += 1
            await _note_failed_read(tenant_id, p["id"])
            continue
        if not usable(spec):
            # Read fine, and there is no text layout in it — that IS a verdict.
            unreadable += 1
            await _mark_read(tenant_id, p["id"])
            continue
        before = await count(tenant_id, active_only=False)
        # A picture off the niche-reference shelf is not a competitor's post —
        # nobody tracks the account, and often the platform never named one. It
        # is kept apart so provenance can say what it really is: a layout from
        # the niche, not "what @handle does".
        kind = "niche" if p.get("shelf_status") == "reference" else "competitor"
        tid = await save(
            tenant_id, spec, source_kind=kind, source_post_id=str(p["id"]),
            source_url=p.get("url") or "", source_image_uri=p["stored_media_url"],
            source_handle=p.get("handle") or "", source_platform=p.get("platform") or "",
            source_engagement=float(p.get("engagement_rate") or 0),
        )
        after = await count(tenant_id, active_only=False)
        if tid and after > before:
            learned += 1
        elif tid:
            duplicate += 1
        await _mark_read(tenant_id, p["id"])
    logger.info("design templates: read %d competitor posts -> %d new, %d duplicate, "
                "%d without a layout, %d failed (will retry)",
                len(posts), learned, duplicate, unreadable, failed)
    return {"read": len(posts), "learned": learned, "duplicate": duplicate,
            "unreadable": unreadable, "failed": failed, "library": await count(tenant_id)}


async def learn_from_reference(
    tenant_id, image: bytes, *, source_url: str = "", image_uri: str = "",
    source_kind: str = "reference",
) -> str | None:
    """Read ANY post the owner points at into a template — not only competitors.

    `image_uri` should be our stored copy of the image; the caller saves the
    bytes to media storage first so the reference is kept in-house."""
    from .design_cloner import extract_template_spec

    spec = await extract_template_spec(image)
    return await save(tenant_id, spec, source_kind=source_kind, source_url=source_url,
                      source_image_uri=image_uri)


__all__ = [
    "MIN_LIBRARY", "RETIRE_AFTER_QA_FAILS", "MAX_READ_ATTEMPTS", "fingerprint", "usable",
    "prepare", "drawable", "base_role", "save", "count",
    "pick", "mark_used", "mark_qa", "mark_verdict", "learn_from_competitors",
    "learn_from_reference",
]
