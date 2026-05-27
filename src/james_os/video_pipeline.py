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

import httpx

from .assembly import get_assembly_provider
from .config import settings
from .db import acquire
from .heygen import get_avatar_provider
from .imagegen import generate_seed_image
from .media import storage as media_storage
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
    script: str, platform: str, aspect: str, title: str = "",
    scenes: list[dict] | None = None, mode: str = "mixed",
    tenant_id: UUID | None = None,
) -> dict:
    """Create a production. mode='avatar_only' renders the whole script as
    one HeyGen avatar — no per-scene assembly. mode='mixed' (default) uses
    the full plan/render/assemble pipeline. If `scenes` is supplied, the
    planner is skipped in mixed mode."""
    if mode not in ("mixed", "avatar_only"):
        mode = "mixed"
    async with acquire(tenant_id) as conn:
        row = await conn.fetchrow(
            """INSERT INTO video_productions
                 (status, title, platform, aspect, script, scenes, mode,
                  avatar_provider, broll_provider, assembly_provider)
               VALUES ('queued',$1,$2,$3,$4,$5::jsonb,$6,$7,$8,$9) RETURNING *""",
            title, platform, aspect, script, json.dumps(scenes or []), mode,
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


async def _james_clip_entries(tenant_id: UUID | None) -> list[dict]:
    """Returns the james_clip pool with mute_audio metadata, so the renderer
    can mark a scene's native audio as muted when the user has flagged the
    clip that way."""
    from .media import james_clips_with_mute
    return await james_clips_with_mute(tenant_id)


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


async def _render_avatar(
    text: str, aspect: str, *, captions: bool = False
) -> tuple[str | None, str]:
    prov = get_avatar_provider()
    sub = await prov.submit(text, aspect, captions=captions)
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


async def _persist_clip_to_storage(
    provider_url: str, label: str
) -> tuple[str | None, float]:
    """Download a freshly-rendered clip, optionally trim trailing silence,
    and re-upload to our storage. Returns (durable_public_url, actual_seconds).

    actual_seconds is the post-trim duration (or 0.0 if we couldn't measure),
    so the caller can snap the scene's duration to the real spoken length and
    eliminate dead air at scene boundaries.
    """
    if not provider_url or not provider_url.startswith("http"):
        return None, 0.0
    if "supabase.co/storage/v1/object/public" in provider_url:
        return None, 0.0  # already durable; caller keeps existing duration
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(180.0, connect=10.0)) as c:
            r = await c.get(provider_url, follow_redirects=True)
            r.raise_for_status()
            data = r.content

        actual_duration = 0.0
        if settings.auto_trim_silence:
            import tempfile
            from pathlib import Path
            from .audio_trim import detect_speech_end, trim_to
            with tempfile.TemporaryDirectory() as td:
                in_path = f"{td}/in.mp4"
                out_path = f"{td}/out.mp4"
                Path(in_path).write_bytes(data)
                speech_end, total = await detect_speech_end(in_path)
                if speech_end and total and speech_end < total - 0.3:
                    # Trailing silence detected — cut it off.
                    if await trim_to(in_path, out_path, speech_end):
                        data = Path(out_path).read_bytes()
                        actual_duration = round(speech_end, 2)
                if not actual_duration and total > 0:
                    actual_duration = round(total, 2)

        url, _ = await asyncio.to_thread(
            media_storage().save,
            str(settings.default_tenant_id), data, f"{label}.mp4",
        )
        return url, actual_duration
    except Exception:  # noqa: BLE001 — keep the provider URL on any failure
        return None, 0.0


async def _render_scene_inplace(
    s: dict, aspect: str, james_uris: list[str], used_clips: list[str]
) -> dict:
    """Render one scene's clip, mutating s with url/clip_status/note."""
    kind, source = s.get("kind"), s.get("source")
    s.pop("note", None)
    s.pop("provider_url", None)
    idx = s.get("index", 0)
    label = s.get("label") or kind or "scene"

    if kind == "talking_head" and source == "avatar":
        url, err = await _render_avatar(s.get("voiceover", ""), aspect)
        if url:
            durable, actual = await _persist_clip_to_storage(url, f"scene-{idx}-{label}")
            if durable:
                s["provider_url"] = url  # transient — what HeyGen gave us
                s["url"] = durable        # durable — our Supabase URL
                s["persisted"] = True
                if actual >= 0.5:
                    s["planned_duration"] = s.get("duration")
                    s["duration"] = actual  # snap to real spoken length
            else:
                s["url"] = url
                s["persisted"] = False
                s["note"] = "kept provider URL (re-host to our storage failed)"
            s["clip_status"] = "ok"
        else:
            s["url"] = f"stub://avatar/{idx}"
            s["clip_status"] = "stub"
            if err:
                s["note"] = err
    elif kind == "talking_head" and source == "james_clip":
        # james_uris is a list of dicts {uri, mute_audio} when supplied by
        # _james_clip_entries; fall back to plain str list for backwards-compat.
        entries = james_uris
        if entries and isinstance(entries[0], str):
            entries = [{"uri": u, "mute_audio": False} for u in entries]
        chosen = next((e for e in entries if e["uri"] not in used_clips),
                      entries[0] if entries else None)
        if chosen:
            used_clips.append(chosen["uri"])
            s["url"] = _public(chosen["uri"])
            s["clip_status"] = "ok"
            s["persisted"] = True  # already on our storage
            if chosen.get("mute_audio"):
                s["mute_native_audio"] = True
        else:
            s["url"] = f"stub://james_clip/{idx}"
            s["clip_status"] = "stub"
            s["note"] = "no James clips in the library — upload some in the Reference Library"
    else:  # broll → seed image → Runway
        url, err = await _render_broll(s.get("visual_prompt", ""), aspect)
        if url:
            durable, actual = await _persist_clip_to_storage(url, f"scene-{idx}-{label}")
            if durable:
                s["provider_url"] = url
                s["url"] = durable
                s["persisted"] = True
                if actual >= 0.5:
                    s["planned_duration"] = s.get("duration")
                    s["duration"] = actual
            else:
                s["url"] = url
                s["persisted"] = False
                s["note"] = "kept provider URL (re-host to our storage failed)"
            s["clip_status"] = "ok"
        else:
            s["url"] = f"stub://broll/{idx}"
            s["clip_status"] = "stub"
            if err:
                s["note"] = err
    return s


async def render_one_scene(
    scene: dict, aspect: str = "9:16", tenant_id: UUID | None = None
) -> dict:
    """Render a single scene for the editor's per-scene preview. Returns the
    scene with url/clip_status/note filled. Reuses the same providers as the
    full pipeline, so a stub stays a stub and a real key produces a real clip."""
    james_uris = await _james_clip_uris(tenant_id)
    s = dict(scene)
    return await _render_scene_inplace(s, aspect, james_uris, [])


async def _run_avatar_only(row, tenant_id: UUID | None) -> None:
    """Avatar-only mode: one HeyGen render of the full script — same voice
    end-to-end, no per-scene assembly, no Creatomate. Lands in queue."""
    pid = row["id"]
    script = (row["script"] or "").strip()
    if not script:
        return await _fail(pid, "avatar-only mode needs a script", tenant_id)
    async with acquire(tenant_id) as conn:
        await _set(conn, pid, status="rendering_clips")
    # In avatar-only mode we want HeyGen to burn in spoken-word subtitles
    # (no scene titles, no B-roll — only the avatar + captions of what it says).
    url, err = await _render_avatar(script, row["aspect"], captions=True)
    if not url:
        return await _fail(pid, err or "HeyGen render failed", tenant_id)
    durable, _actual = await _persist_clip_to_storage(url, f"avatar-only-{pid}")
    final_url = durable or url
    async with acquire(tenant_id) as conn:
        action_id = await conn.fetchval(
            """INSERT INTO actions (proposed_by, action_type, payload, status)
               VALUES ('video_producer','video',$1::jsonb,'pending') RETURNING id""",
            json.dumps({
                "platform": row["platform"], "format": "video",
                "content": row["title"] or script[:120],
                "caption": row["title"] or "",
                "media_url": final_url,
                "stub": final_url.startswith("stub://"),
                "mode": "avatar_only",
            }),
        )
        await conn.execute(
            """UPDATE video_productions SET status='succeeded', final_url=$2,
               queued_action_id=$3, updated_at=now(), completed_at=now()
               WHERE id=$1""",
            pid, final_url, action_id,
        )


async def run_production(production_id: UUID, tenant_id: UUID | None = None) -> None:
    """The worker. Advances the production through every stage."""
    pid = production_id
    try:
        # ── plan (skip if the editor supplied an edited plan) ──
        async with acquire(tenant_id) as conn:
            row = await conn.fetchrow("SELECT * FROM video_productions WHERE id=$1", pid)
            if row is None:
                return

        # Avatar-only mode forks here — one HeyGen render of the entire
        # script, no per-scene plan, no Creatomate assembly.
        if row["mode"] == "avatar_only":
            return await _run_avatar_only(row, tenant_id)
        existing = row["scenes"]
        if isinstance(existing, str):
            existing = json.loads(existing)
        if existing:  # pre-edited plan from the visual editor — use as-is
            scenes = existing
            final_title = row["title"]
            async with acquire(tenant_id) as conn:
                await _set(conn, pid, status="rendering_clips")
        else:
            async with acquire(tenant_id) as conn:
                await _set(conn, pid, status="planning")
            plan = await generate_scene_plan(
                row["script"], row["platform"], row["aspect"], tenant_id=tenant_id
            )
            scenes = plan.get("scenes") or []
            if not scenes:
                return await _fail(pid, plan.get("error") or "no scenes planned", tenant_id)
            final_title = plan.get("title") or row["title"]
            async with acquire(tenant_id) as conn:
                await _set(conn, pid, status="rendering_clips",
                           plan=json.dumps(plan), scenes=json.dumps(scenes),
                           title=final_title)

        # ── render each scene's clip ──
        # IMPORTANT: do NOT hold a DB connection across the renders below —
        # each can poll a provider for minutes. We render with no connection
        # held, then persist progress in short-lived connections so the pool
        # is never starved and no transaction stays open during a render.
        james_uris = await _james_clip_entries(tenant_id)
        used_clips: list[str] = []
        for s in scenes:
            # Reuse a clip already rendered in the editor's per-scene preview.
            if (s.get("url") or "").startswith("http"):
                s["clip_status"] = "ok"
                if s.get("source") == "james_clip":
                    used_clips.append(s["url"])
            else:
                await _render_scene_inplace(s, row["aspect"], james_uris, used_clips)
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
                    "content": final_title or row["script"][:120],
                    "caption": final_title or "",
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
