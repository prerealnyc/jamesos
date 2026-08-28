"""Stage 3 of competitor intelligence — actually LOOK at what they post.

Two capable analysers already existed in this codebase and neither had ever
been pointed at a competitor:

  design_eye.inspect_image  grades ONE still on 7 axes and returns its design
                            DNA, why it works, and the transferable pattern.
                            It had zero call sites anywhere in the repo.
  perception.analyze_file   watches a video — ffmpeg frames + Whisper — and
                            returns hook / structure / pacing / captions /
                            visual_style. Only ever aimed at our own clips.

This module routes competitor posts into them and persists the result, plus a
third, cheap pass that reads the words:

  stills  → design_eye        (what the design is doing)
  reels   → perception        (how the video is built)
  every post → _classify      (format, hook pattern, topic, CTA, who it talks
                               to, and the growth play it is running)

The text pass runs on everything because it is ~1/50th the cost of a vision
call and answers most of "what type of content are they posting". Vision is
spent deliberately: highest-engagement posts first, videos capped separately
because a reel costs an order of magnitude more than a still.

Honesty contract, inherited from both analysers: a missing key or a dead
media URL is recorded as status='no_key'/'failed' with the reason. An
analysis row never contains a score that was not actually computed.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from uuid import UUID

from .db import acquire

# Vision is the expensive part; these are the spend dials.
_DEFAULT_POST_CAP = 8      # posts analysed per competitor per run
_DEFAULT_VIDEO_CAP = 3     # of those, how many may be videos
_MAX_IMAGE_BYTES = 12 * 1024 * 1024

_CLASSIFY_SYSTEM = """You read ONE social post by a competitor and describe
what it is doing, for a brand studying how that account grows.

You get the caption and, for video, the transcript. Describe only what is
present. If a field is not determinable from what you were given, return an
empty string for it — never guess a hook you cannot see.

Fields:
  format        the concrete content format, e.g. "talking-head reel",
                "listing carousel", "before/after", "text-on-image quote",
                "b-roll montage with voiceover", "photo dump"
  hook          the opening line, VERBATIM, as it appears
  hook_pattern  the reusable shape of that hook, e.g. "question", "bold
                claim", "numbered list", "contrarian take", "story open",
                "result reveal", "none"
  topic         the subject in 2-5 words
  cta           what it asks the reader to do; "" when it asks nothing
  audience      who this is speaking to, in a short phrase
  growth_play   the audience-growth or engagement tactic visible in the post,
                e.g. "comment bait", "save bait", "authority proof",
                "collab tag", "trend audio", "series hook", "none"
  value_type    one of: education, proof, entertainment, promotion, personal

Return JSON with exactly those keys."""


def _analysis_row(r) -> dict:
    d = dict(r)
    for k in ("id", "tenant_id", "post_id"):
        if d.get(k) is not None:
            d[k] = str(d[k])
    if d.get("analyzed_at") is not None:
        d["analyzed_at"] = d["analyzed_at"].isoformat()
    for k in ("axes", "design_dna", "fingerprint", "classification"):
        if isinstance(d.get(k), str):
            d[k] = json.loads(d[k])
    return d


# ── resolving media we can actually feed to a model ───────────────────

def _local_path_from_served(served: str) -> Path | None:
    """Local-disk storage returns '/media-files/<tenant>/<name>', which no
    external model can fetch. Map it back to the file on disk."""
    prefix = "/media-files/"
    if not served.startswith(prefix):
        return None
    from .media import media_root
    p = (media_root() / served[len(prefix):]).resolve()
    # Never escape the media root, whatever the stored string says.
    if media_root().resolve() not in p.parents:
        return None
    return p if p.is_file() else None


async def _image_ref(post: dict) -> tuple[str, object | None, str]:
    """Resolve a still to something design_eye can grade.

    Returns (kind, value, error) where kind is 'url' (an https URL the model
    can fetch itself — cheapest) or 'bytes'. Prefers our durable copy over
    the source URL, which has usually expired by the time we analyse.
    """
    stored = (post.get("stored_media_url") or "").strip()
    if stored.startswith("http"):
        return "url", stored, ""
    local = _local_path_from_served(stored) if stored else None
    if local:
        try:
            data = local.read_bytes()
        except OSError as e:
            return "", None, f"local read failed: {type(e).__name__}"
        if len(data) > _MAX_IMAGE_BYTES:
            return "", None, "image over size cap"
        return "bytes", data, ""

    # No durable copy — fall back to the source, which may well be dead.
    for key in ("media_url", "thumbnail_url"):
        url = (post.get(key) or "").strip()
        if not url.startswith("http"):
            continue
        from .netguard import url_is_public
        if not await url_is_public(url, allow_http=True):
            continue
        import httpx
        try:
            async with httpx.AsyncClient(timeout=60, follow_redirects=True) as c:
                r = await c.get(url)
                r.raise_for_status()
                if len(r.content) <= _MAX_IMAGE_BYTES and r.content:
                    return "bytes", r.content, ""
        except Exception:  # noqa: BLE001 — try the next candidate
            continue
    return "", None, "no reachable image (source URL likely expired)"


async def _video_file(post: dict) -> tuple[str | None, str | None, str]:
    """Resolve a reel to a local file for perception. Returns
    (path, tempdir_to_clean, error)."""
    stored = (post.get("stored_media_url") or "").strip()
    local = _local_path_from_served(stored) if stored else None
    if local:
        return str(local), None, ""
    url = stored if stored.startswith("http") else (post.get("media_url") or "")
    if not url.startswith("http"):
        return None, None, "no reachable video"
    from .media import fetch_media_local
    path, tmpdir = await fetch_media_local(None, url)
    if not path:
        return None, None, "could not download the video (URL likely expired)"
    return path, tmpdir, ""


# ── the three passes ──────────────────────────────────────────────────

async def _classify(post: dict, transcript: str = "",
                    visual: dict | None = None) -> dict:
    """Read the words — and, when a vision pass already ran, what it saw.

    Captions cannot tell you a post is a text-on-image quote or a carousel;
    the first version of this returned format="image" for every still, which
    is just the media type restated. Feeding the design DNA in makes `format`
    describe the actual artefact.
    """
    from .llm import get_llm
    llm = get_llm()
    if getattr(llm, "model_name", "") == "stub":
        return {}
    caption = (post.get("caption") or "").strip()
    if not caption and not transcript and not visual:
        return {}
    body = f"PLATFORM: {post.get('platform','')}\nMEDIA: {post.get('media_type','')}\n\n"
    body += f"CAPTION:\n{caption[:2500]}"
    if transcript:
        body += f"\n\nTRANSCRIPT:\n{transcript[:3500]}"
    if visual:
        body += ("\n\nWHAT THE IMAGE/VIDEO ACTUALLY LOOKS LIKE "
                 "(from a vision pass — use it to name the format precisely):\n"
                 + json.dumps(visual)[:1800])
    try:
        out = await llm.complete_json(
            system=_CLASSIFY_SYSTEM,
            messages=[{"role": "user", "content": body}],
            max_tokens=600, temperature=0.0)
    except Exception as e:  # noqa: BLE001
        return {"error": f"{type(e).__name__}: {e}"}
    return out if isinstance(out, dict) else {}


async def analyze_post(
    post: dict, with_vision: bool = True, tenant_id: UUID | None = None
) -> dict:
    """Analyse ONE competitor post and persist the result.

    Never raises. Every failure mode — no key, dead media URL, unreadable
    file — is recorded on the row with its reason, so a shelf of analyses is
    always honest about what was actually looked at.
    """
    media_type = (post.get("media_type") or "").lower()
    is_video = media_type == "video"
    kind = "video" if is_video else "image"

    status, error = "ok", ""
    eye_score = None
    axes: dict = {}
    design_dna: dict = {}
    fingerprint: dict = {}
    why_it_works = transferable = ""
    rubric_version = model = ""
    transcript = ""

    if with_vision and is_video:
        path, tmpdir, err = await _video_file(post)
        if not path:
            status, error = "failed", err
        else:
            try:
                from .perception import analyze_file
                res = await analyze_file(path)
                if res.get("status") == "done":
                    fingerprint = res.get("fingerprint") or {}
                    transcript = res.get("transcript") or ""
                    model = "perception/gpt-4o"
                elif res.get("status") == "unsupported":
                    status, error = "no_key", res.get("note", "")
                else:
                    status, error = "failed", res.get("note", "analysis failed")
            except Exception as e:  # noqa: BLE001
                status, error = "failed", f"{type(e).__name__}: {e}"
            finally:
                if tmpdir:
                    shutil.rmtree(tmpdir, ignore_errors=True)

    elif with_vision:
        ref_kind, value, err = await _image_ref(post)
        if not ref_kind:
            status, error = "failed", err
        else:
            from .design_eye import RUBRIC_VERSION, inspect_image
            res = await inspect_image(value)
            rubric_version = res.get("rubric_version") or RUBRIC_VERSION
            if res.get("status") == "ok":
                axes = res.get("axes") or {}
                eye_score = res.get("eye_score")
                design_dna = res.get("design_dna") or {}
                why_it_works = res.get("why_it_works") or ""
                transferable = res.get("transferable_pattern") or ""
                model = "design_eye/gpt-4o"
            else:
                status = res.get("status") or "failed"
                error = str(res.get("error") or "")[:200]

    if not with_vision:
        status = "skipped"

    # Hand the vision result to the text pass so `format` names the real
    # artefact ("text-on-image announcement") instead of echoing media_type.
    visual_hint = design_dna or fingerprint or None
    classification = await _classify(post, transcript, visual_hint)

    async with acquire(tenant_id) as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO competitor_post_analysis (
                post_id, kind, status, eye_score, axes, design_dna, fingerprint,
                why_it_works, transferable_pattern, format, hook, hook_pattern,
                topic, cta, classification, rubric_version, model, error)
            VALUES ($1::uuid,$2,$3,$4,$5::jsonb,$6::jsonb,$7::jsonb,$8,$9,$10,
                    $11,$12,$13,$14,$15::jsonb,$16,$17,$18)
            ON CONFLICT (post_id) DO UPDATE SET
                kind = EXCLUDED.kind, status = EXCLUDED.status,
                eye_score = EXCLUDED.eye_score, axes = EXCLUDED.axes,
                design_dna = EXCLUDED.design_dna,
                fingerprint = EXCLUDED.fingerprint,
                why_it_works = EXCLUDED.why_it_works,
                transferable_pattern = EXCLUDED.transferable_pattern,
                format = EXCLUDED.format, hook = EXCLUDED.hook,
                hook_pattern = EXCLUDED.hook_pattern, topic = EXCLUDED.topic,
                cta = EXCLUDED.cta, classification = EXCLUDED.classification,
                rubric_version = EXCLUDED.rubric_version,
                model = EXCLUDED.model, error = EXCLUDED.error,
                analyzed_at = now()
            RETURNING *
            """,
            str(post["id"]), kind, status, eye_score, json.dumps(axes),
            json.dumps(design_dna), json.dumps(fingerprint), why_it_works[:2000],
            transferable[:2000],
            str(classification.get("format") or "")[:120],
            str(classification.get("hook") or "")[:500],
            str(classification.get("hook_pattern") or "")[:80],
            str(classification.get("topic") or "")[:160],
            str(classification.get("cta") or "")[:200],
            json.dumps(classification), rubric_version, model, error[:300],
        )
    return _analysis_row(row)


# ── batching: spend the vision budget where it matters ────────────────

async def analyze_competitor(
    competitor_id: str, post_cap: int = _DEFAULT_POST_CAP,
    video_cap: int = _DEFAULT_VIDEO_CAP, redo: bool = False,
    tenant_id: UUID | None = None,
) -> dict:
    """Analyse a competitor's best un-analysed posts.

    Ordered by engagement rate, because the question is "what works for
    them" — grading their flops teaches nothing and costs the same.
    """
    where_new = "" if redo else "AND a.id IS NULL"
    async with acquire(tenant_id) as conn:
        rows = await conn.fetch(
            f"""SELECT p.* FROM competitor_posts p
                  LEFT JOIN competitor_post_analysis a ON a.post_id = p.id
                 WHERE p.competitor_id = $1::uuid {where_new}
              ORDER BY p.engagement_rate DESC NULLS LAST, p.likes DESC
                 LIMIT $2""",
            competitor_id, max(1, post_cap) * 3)
        posts = [dict(r) for r in rows]

    picked, videos = [], 0
    for p in posts:
        if len(picked) >= post_cap:
            break
        if (p.get("media_type") or "") == "video":
            if videos >= video_cap:
                continue
            videos += 1
        picked.append(p)

    results = []
    for p in picked:
        p["id"] = str(p["id"])
        results.append(await analyze_post(p, tenant_id=tenant_id))

    ok = sum(1 for r in results if r["status"] == "ok")
    return {
        "competitor_id": competitor_id,
        "analyzed": len(results), "ok": ok,
        "failed": [{"post": str(r["post_id"]), "reason": r["error"]}
                   for r in results if r["status"] != "ok"][:10],
        "candidates_available": len(posts),
    }


async def analyze_all(
    post_cap: int = _DEFAULT_POST_CAP, video_cap: int = _DEFAULT_VIDEO_CAP,
    tenant_id: UUID | None = None,
) -> dict:
    """Run the eyes across every tracked competitor.

    Sequential per competitor on purpose: vision calls are the expensive
    resource here, and running ten competitors' worth concurrently buys
    little while making a runaway bill much easier.
    """
    from .competitors import list_competitors
    tracked = await list_competitors(status="tracked", tenant_id=tenant_id)
    if not tracked:
        return {"analyzed": 0, "results": [],
                "note": "No tracked competitors yet."}
    results = []
    for c in tracked:
        try:
            results.append({**await analyze_competitor(
                c["id"], post_cap=post_cap, video_cap=video_cap,
                tenant_id=tenant_id), "handle": c["handle"]})
        except Exception as e:  # noqa: BLE001 — one competitor ≠ the batch
            results.append({"handle": c["handle"], "analyzed": 0, "ok": 0,
                            "error": str(e)[:200]})
    return {
        "analyzed": sum(r.get("analyzed", 0) for r in results),
        "ok": sum(r.get("ok", 0) for r in results),
        "results": results,
    }


async def run_competitor_vision(tenant_id: UUID, config: dict | None = None) -> None:
    """Scheduler entry point — keep the analysis shelf caught up with the
    post shelf. Tenant-bound and explicit, per the scheduler contract."""
    cfg = config or {}
    await analyze_all(
        post_cap=int(cfg.get("post_cap") or _DEFAULT_POST_CAP),
        video_cap=int(cfg.get("video_cap") or _DEFAULT_VIDEO_CAP),
        tenant_id=tenant_id)


# ── reads ─────────────────────────────────────────────────────────────

async def list_analyses(
    competitor_id: str = "", limit: int = 60, tenant_id: UUID | None = None
) -> list[dict]:
    """Analyses joined to the post they describe — the readable shelf."""
    clauses, args = ["1=1"], []
    if competitor_id:
        args.append(competitor_id)
        clauses.append(f"p.competitor_id = ${len(args)}::uuid")
    args.append(max(1, min(limit, 200)))
    async with acquire(tenant_id) as conn:
        rows = await conn.fetch(
            f"""SELECT a.*, p.url, p.caption, p.media_type, p.likes, p.comments,
                       p.engagement_rate, p.posted_at, p.thumbnail_url,
                       c.handle, c.platform
                  FROM competitor_post_analysis a
                  JOIN competitor_posts p ON p.id = a.post_id
                  JOIN competitors c ON c.id = p.competitor_id
                 WHERE {' AND '.join(clauses)}
              ORDER BY p.engagement_rate DESC NULLS LAST
                 LIMIT ${len(args)}""", *args)
    out = []
    for r in rows:
        d = _analysis_row(r)
        if d.get("posted_at") is not None and not isinstance(d["posted_at"], str):
            d["posted_at"] = d["posted_at"].isoformat()
        out.append(d)
    return out


async def analysis_stats(tenant_id: UUID | None = None) -> dict:
    """How much of the shelf has actually been looked at."""
    async with acquire(tenant_id) as conn:
        row = await conn.fetchrow(
            """SELECT (SELECT count(*) FROM competitor_posts) AS posts,
                      count(*) AS analyzed,
                      count(*) FILTER (WHERE status = 'ok') AS ok,
                      count(*) FILTER (WHERE kind = 'video' AND status='ok') AS reels,
                      count(*) FILTER (WHERE status NOT IN ('ok','skipped')) AS failed,
                      max(analyzed_at) AS last_run
                 FROM competitor_post_analysis""")
    d = dict(row)
    if d.get("last_run") is not None:
        d["last_run"] = d["last_run"].isoformat()
    return d


__all__ = [
    "analyze_post", "analyze_competitor", "analyze_all",
    "run_competitor_vision", "list_analyses", "analysis_stats",
]
