"""Video production orchestration — script → finished clip → approval queue.

A durable state machine over the scene plan, the clip providers (HeyGen
avatar / James's real clips / B-roll), and the assembler:

    queued → planning → rendering_clips → assembling → succeeded → queue
                                                     ↘ failed (recorded)

Stub-first: with no provider keys the whole thing runs to completion using
honest stub markers (never a fake mp4), proving the pipeline. Real providers
activate from their keys. State is persisted at every stage so a failure is
explained, not silent.

Honest limits (flagged, not hidden):
  * B-roll: Runway's dev API is image-conditioned, so text-only B-roll is
    stubbed until a seed-image step is added. Marked per scene.
  * Real assembly (Creatomate) needs PUBLICLY reachable clip URLs. James's
    uploaded clips live on local disk today; assembling them for real needs
    the object-storage upgrade. Stub assembly works regardless.
"""

import asyncio
import json
from uuid import UUID

from .assembly import get_assembly_provider
from .config import settings
from .db import acquire
from .heygen import get_avatar_provider
from .imagegen import generate_seed_image
from .video import get_video_provider
from .video_plan import generate_scene_plan

_POLL_EVERY = 5.0
_MAX_POLLS = 60  # ~5 min ceiling per async stage

# Runway gen4_turbo accepts a fixed set of ratios; map our aspect to one.
_RUNWAY_RATIO = {"9:16": "720:1280", "16:9": "1280:720", "1:1": "960:960"}


def _row(r) -> dict:
    d = dict(r)
    for k in ("id", "tenant_id", "queued_action_id"):
        if d.get(k) is not None:
            d[k] = str(d[k])
    for k in ("plan", "scenes"):
        if isinstance(d.get(k), str):
            d[k] = json.loads(d[k])
    for k in ("created_at", "updated_at", "completed_at"):
        if d.get(k) is not None:
            d[k] = d[k].isoformat()
    return d


async def start_production(
    script: str, platform: str, aspect: str, title: str = "", tenant_id: UUID | None = None
) -> dict:
    async with acquire(tenant_id) as conn:
        row = await conn.fetchrow(
            """INSERT INTO video_productions
                 (status, title, platform, aspect, script,
                  avatar_provider, broll_provider, assembly_provider)
               VALUES ('queued',$1,$2,$3,$4,$5,$6,$7) RETURNING *""",
            title, platform, aspect, script,
            get_avatar_provider().name, settings.video_provider,
            get_assembly_provider().name,
        )
    return _row(row)


async def _set(conn, pid, **cols):
    sets = ", ".join(f"{k} = ${i+2}" for i, k in enumerate(cols))
    await conn.execute(
        f"UPDATE video_productions SET {sets}, updated_at=now() WHERE id=$1",
        pid, *cols.values(),
    )


async def _fail(pid, msg, tenant_id):
    async with acquire(tenant_id) as conn:
        await conn.execute(
            "UPDATE video_productions SET status='failed', error=$2, "
            "updated_at=now(), completed_at=now() WHERE id=$1",
            pid, msg[:500],
        )


async def _james_clip_uris(tenant_id: UUID | None) -> list[str]:
    async with acquire(tenant_id) as conn:
        rows = await conn.fetch(
            "SELECT uri FROM media_assets WHERE role='james_clip' "
            "ORDER BY created_at LIMIT 20"
        )
    return [r["uri"] for r in rows if r["uri"]]


def _public(uri: str) -> str:
    if uri.startswith("http"):
        return uri
    # Local served path — not publicly reachable by external assemblers.
    return uri


async def _render_avatar(text: str, aspect: str) -> tuple[str | None, str]:
    prov = get_avatar_provider()
    sub = await prov.submit(text, aspect)
    if sub.status == "failed":
        return None, sub.error or "avatar submit failed"
    for _ in range(_MAX_POLLS):
        p = await prov.poll(sub.job_id)
        if p.status == "succeeded":
            return p.url, ""
        if p.status == "failed":
            return None, p.error or "avatar render failed"
        await asyncio.sleep(_POLL_EVERY)
    return None, "avatar render timed out"


async def _render_broll(visual_prompt: str, aspect: str) -> tuple[str | None, str]:
    """B-roll = text → AI still (OpenAI) → animate (Runway). Both keys needed
    for a real clip; otherwise an honest stub with the reason."""
    if not visual_prompt:
        return None, "no visual prompt for B-roll"
    vid = get_video_provider()
    if vid.name == "stub":
        return None, "Runway not configured (VIDEO_PROVIDER=stub)"
    # 1) seed image from the idea
    image_uri, img_err = await generate_seed_image(visual_prompt, aspect)
    if not image_uri:
        return None, f"seed image: {img_err}"
    # 2) animate it (Runway needs >=5s; the assembler trims to the scene length)
    sub = await vid.submit(
        visual_prompt, image_uri,
        model=settings.runway_model,
        ratio=_RUNWAY_RATIO.get(aspect, settings.runway_video_ratio),
        duration=5,
    )
    if sub.status == "failed":
        return None, sub.error or "Runway submit failed"
    for _ in range(_MAX_POLLS):
        p = await vid.poll(sub.provider_job_id)
        if p.status == "succeeded":
            return p.result_url, ""
        if p.status == "failed":
            return None, p.error or "Runway render failed"
        await asyncio.sleep(_POLL_EVERY)
    return None, "Runway render timed out"


async def run_production(production_id: UUID, tenant_id: UUID | None = None) -> None:
    """The worker. Advances the production through every stage."""
    pid = production_id
    try:
        # ── plan ──
        async with acquire(tenant_id) as conn:
            row = await conn.fetchrow("SELECT * FROM video_productions WHERE id=$1", pid)
            if row is None:
                return
            await _set(conn, pid, status="planning")
        plan = await generate_scene_plan(row["script"], row["platform"], row["aspect"], tenant_id)
        scenes = plan.get("scenes") or []
        if not scenes:
            return await _fail(pid, plan.get("error") or "no scenes planned", tenant_id)
        async with acquire(tenant_id) as conn:
            await _set(conn, pid, status="rendering_clips",
                       plan=json.dumps(plan), scenes=json.dumps(scenes),
                       title=plan.get("title") or row["title"])

        # ── render each scene's clip ──
        # IMPORTANT: do NOT hold a DB connection across the renders below —
        # each can poll a provider for minutes. We render with no connection
        # held, then persist progress in short-lived connections so the pool
        # is never starved and no transaction stays open during a render.
        james_uris = await _james_clip_uris(tenant_id)
        used_clips: list[str] = []
        for s in scenes:
            kind, source = s["kind"], s.get("source")
            if kind == "talking_head" and source == "avatar":
                url, err = await _render_avatar(s["voiceover"], row["aspect"])
                s["url"] = url or f"stub://avatar/{s['index']}"
                s["clip_status"] = "ok" if url else "stub"
                if err:
                    s["note"] = err
            elif kind == "talking_head" and source == "james_clip":
                clip = next((u for u in james_uris if u not in used_clips),
                            james_uris[0] if james_uris else None)
                if clip:
                    used_clips.append(clip)
                    s["url"] = _public(clip)
                    s["clip_status"] = "ok"
                else:
                    s["url"] = f"stub://james_clip/{s['index']}"
                    s["clip_status"] = "stub"
                    s["note"] = "no James clips in the library"
            else:  # broll → seed image → Runway
                url, err = await _render_broll(s.get("visual_prompt", ""), row["aspect"])
                s["url"] = url or f"stub://broll/{s['index']}"
                s["clip_status"] = "ok" if url else "stub"
                if err:
                    s["note"] = err
            # persist progress per scene in a short-lived connection
            async with acquire(tenant_id) as conn:
                await _set(conn, pid, scenes=json.dumps(scenes))

        async with acquire(tenant_id) as conn:
            await _set(conn, pid, status="assembling", scenes=json.dumps(scenes))

        # ── assemble ──
        asm = get_assembly_provider()
        res = await asm.render(scenes, row["aspect"])
        if res.status == "processing":
            for _ in range(_MAX_POLLS):
                await asyncio.sleep(_POLL_EVERY)
                res = await asm.poll(res.render_id)
                if res.status in ("succeeded", "failed"):
                    break
        if res.status != "succeeded" or not res.url:
            return await _fail(pid, res.error or "assembly failed", tenant_id)

        # ── land in approval queue ──
        async with acquire(tenant_id) as conn:
            action_id = await conn.fetchval(
                """INSERT INTO actions (proposed_by, action_type, payload, status)
                   VALUES ('video_producer','video',$1::jsonb,'pending') RETURNING id""",
                json.dumps({
                    "platform": row["platform"], "format": "video",
                    "content": plan.get("title") or row["script"][:120],
                    "caption": plan.get("title") or "",
                    "media_url": res.url,
                    "stub": res.url.startswith("stub://"),
                    "scenes": len(scenes),
                }),
            )
            await conn.execute(
                """UPDATE video_productions SET status='succeeded', final_url=$2,
                   queued_action_id=$3, scenes=$4, updated_at=now(), completed_at=now()
                   WHERE id=$1""",
                pid, res.url, action_id, json.dumps(scenes),
            )
    except Exception as e:  # noqa: BLE001
        await _fail(pid, f"production crashed: {e}", tenant_id)


async def list_productions(tenant_id: UUID | None = None) -> list[dict]:
    async with acquire(tenant_id) as conn:
        rows = await conn.fetch(
            "SELECT * FROM video_productions ORDER BY created_at DESC LIMIT 50"
        )
    return [_row(r) for r in rows]


async def get_production(production_id: UUID, tenant_id: UUID | None = None) -> dict | None:
    async with acquire(tenant_id) as conn:
        row = await conn.fetchrow("SELECT * FROM video_productions WHERE id=$1", production_id)
    return _row(row) if row else None


__all__ = ["start_production", "run_production", "list_productions", "get_production"]
