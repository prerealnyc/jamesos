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
import os
from uuid import UUID

import httpx

from .assembly import get_assembly_provider
from .config import settings
from .db import acquire
from .heygen import get_avatar_provider
from .imagegen import generate_seed_image
from .media import storage as media_storage
from .video import get_video_provider, is_transient_poll_error, provider_for
from .video_feedback import video_avoid_block
from .video_plan import generate_scene_plan

_POLL_EVERY = 5.0
# ~15 min ceiling: a heavy multi-track assembly under provider load runs well over
# 5 min, and Creatomate bills + finishes it anyway — the old 5-min cap threw away a
# paid, near-complete render (render_id isn't persisted, so it couldn't re-poll).
# Matches the B-roll ceiling.
_MAX_POLLS = 180  # ~15 min ceiling for Creatomate assembly polls
# B-roll engines vary a lot: Runway finishes a 5s clip in <3 min, Higgsfield
# (dop/standard) takes 5-7 min (measured). The loop exits on success, so the
# generous ceiling only binds when the provider is genuinely slow.
_BROLL_MAX_POLLS = 180  # ~15 min ceiling per B-roll clip
# HeyGen routinely takes longer than 5 minutes on 1-2 minute avatar scripts;
# giving up early throws away a render HeyGen finishes (and bills for) anyway.
# The avatar/talking-photo stages get their own generous ceiling.
_AVATAR_MAX_POLLS = 240  # ~20 min ceiling for HeyGen avatar renders
# Poll loops make dozens of HTTP calls over minutes — tolerate a few
# consecutive transient failures (network blip, provider 5xx/429) before
# declaring the render dead.
_MAX_TRANSIENT_POLL_ERRORS = 3

# A batch (a Drive import fanning out N clips, a bulk autopilot run) can spawn many
# run_production tasks at once; each downloads a multi-GB source to the instance's
# ephemeral disk and runs ffmpeg + minutes of provider polls. N-at-once exhausts
# disk/RAM on the single Railway container, and an OOM restart fails ALL of them.
# Cap how many render concurrently — the rest queue on this semaphore. Env-tunable
# (raise on a bigger instance). Lazily created so it binds to the running loop.
_RENDER_CONCURRENCY = max(1, int(os.getenv("RENDER_CONCURRENCY", "2") or 2))
_RENDER_SEM: "asyncio.Semaphore | None" = None


def _render_semaphore() -> "asyncio.Semaphore":
    global _RENDER_SEM
    if _RENDER_SEM is None:
        _RENDER_SEM = asyncio.Semaphore(_RENDER_CONCURRENCY)
    return _RENDER_SEM

# Runway gen4_turbo accepts a fixed set of ratios; map our aspect to one.
_RUNWAY_RATIO = {"9:16": "720:1280", "16:9": "1280:720", "1:1": "960:960"}


async def _avoid_block(tags: list[str] | None, tenant_id: UUID | None) -> str:
    """Fetch the <avoid> steering block for a render, degrade-safe.

    video_avoid_block already swallows its own errors, but we double-wrap
    here so a render NEVER breaks over feedback retrieval — on any failure
    we simply forgo the steering for this one run and return "".
    """
    try:
        return await video_avoid_block(tags=tags, tenant_id=tenant_id)
    except Exception:  # noqa: BLE001 — feedback must never break a render
        return ""


# ── render progress / ETA ─────────────────────────────────────────────
# Working stages in order (terminal 'succeeded'/'failed' handled separately).
_PROGRESS_STAGES = ["queued", "planning", "rendering_clips", "assembling"]
_STAGE_LABEL = {
    "queued": "Queued — waiting to start",
    "planning": "Planning the scenes & beats",
    "rendering_clips": "Rendering clips — avatar + animated B-roll (the long part)",
    "assembling": "Assembling, captioning & mixing audio",
    "succeeded": "Done",
    "failed": "Failed",
}
# Rough typical seconds per stage — rendering_clips dominates (HeyGen avatar +
# image-to-video B-roll). The ETA is APPROXIMATE by design.
_STAGE_TYPICAL_S = {"queued": 4, "planning": 18, "rendering_clips": 220, "assembling": 80}


def _progress(r) -> dict:
    """Approximate render progress + ETA for the tracker. Stage-weighted by
    typical durations, blended with time elapsed in the current stage."""
    from datetime import datetime, timezone

    status = (r.get("status") or "queued")
    n = len(_PROGRESS_STAGES)
    if status == "failed":
        return {"stage": "failed", "stage_index": 0, "total_stages": n,
                "label": _STAGE_LABEL["failed"], "pct": 100, "elapsed_s": 0, "eta_s": 0}
    if status == "succeeded":
        return {"stage": "succeeded", "stage_index": n, "total_stages": n,
                "label": _STAGE_LABEL["succeeded"], "pct": 100, "elapsed_s": 0, "eta_s": 0}
    try:
        now = datetime.now(timezone.utc)
        created = r.get("created_at")
        updated = r.get("updated_at") or created
        elapsed_total = max(0, int((now - created).total_seconds())) if created else 0
        elapsed_stage = max(0, int((now - updated).total_seconds())) if updated else 0
    except Exception:  # noqa: BLE001
        elapsed_total = elapsed_stage = 0
    idx = _PROGRESS_STAGES.index(status) if status in _PROGRESS_STAGES else 0
    total_typical = sum(_STAGE_TYPICAL_S[s] for s in _PROGRESS_STAGES)
    before = sum(_STAGE_TYPICAL_S[s] for s in _PROGRESS_STAGES[:idx])
    cur = _STAGE_TYPICAL_S.get(status, 60)
    done = before + min(elapsed_stage, cur)
    pct = max(1, min(99, round(done / total_typical * 100)))
    remaining = (cur - min(elapsed_stage, cur)) + sum(
        _STAGE_TYPICAL_S[s] for s in _PROGRESS_STAGES[idx + 1:]
    )
    return {"stage": status, "stage_index": idx, "total_stages": n,
            "label": _STAGE_LABEL.get(status, status), "pct": pct,
            "elapsed_s": elapsed_total, "eta_s": max(5, int(remaining))}


def _row(r) -> dict:
    d = dict(r)
    d["progress"] = _progress(r)
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
    caption_style: str = "",
    image_style: str = "",
    music_mood: str = "",
    logo_position: str = "",
    structure: list[dict] | None = None,
    template_id: UUID | None = None,
    video_engine: str = "",
    broll_pacing: str = "",
    broll_style: str = "",
    tenant_id: UUID | None = None,
) -> dict:
    """Create a production.

    Modes:
      * 'avatar_only' — render the whole script as one HeyGen avatar.
        No per-scene plan, no Creatomate.
      * 'mixed' (default) — full plan → per-scene clips → Creatomate assembly.
        If `scenes` is supplied, the planner is skipped.
      * 'timeline' — freeform clip stitching from the /editor page. Every
        block already carries a real URL, so the planner and per-scene
        renderer are no-ops; we go straight to Creatomate.
      * 'story_audio' — render HeyGen once for the voice, transcribe with
        Whisper word-timestamps, segment into 8-18 visual beats, generate
        one photoreal still per beat with gpt-image-1, Creatomate stitches
        audio + stills + burned word-pinned captions. The "Agent Opus"
        story-style format.
      * 'avatar_story_mix' — same single HeyGen render, but reused 100%:
        audio drives the timeline AND the avatar video is sliced per
        "James on camera" beat. The LLM classifies each beat as avatar
        vs broll; B-roll beats still get gpt-image-1 stills, avatar
        beats show James talking. One HeyGen spend, no audio drift.
      * 'engaging_avatar' — HeyGen avatar plays continuously (full
        video + audio); 2-5 cinematic B-roll stills cut in for
        1.5-2.5s each at moments the LLM picks as visually
        amplifiable. Most-time-on-James format with B-roll as
        punctuation, vs avatar_story_mix which alternates per beat.
      * 'long_form_reel' — same engaging-avatar treatment but the
        "avatar" track is a CUT from a real long-form source (podcast,
        interview) instead of a HeyGen render. Source + candidate
        window are stored on the production's `scenes` jsonb as
        {source_id, candidate_id, source_url, start_s, end_s} so the
        worker can ffmpeg-cut [start, end] and proceed identically.
    """
    if mode not in (
        "mixed", "avatar_only", "timeline", "story_audio",
        "avatar_story_mix", "engaging_avatar", "long_form_reel", "hero_clone",
        "split_horizontal", "split_screen", "split_vertical",
    ):
        mode = "mixed"
    if mode == "timeline":
        # Cheap structural guard: a timeline render is meaningless without
        # real clip URLs. Catch this here so the error is honest, not a
        # mid-pipeline assembler failure 30 seconds later.
        if not scenes or not any((s.get("url") or "").startswith("http") for s in scenes):
            raise ValueError("timeline mode needs at least one block with a real clip URL")
    if mode == "story_audio" and not script.strip():
        raise ValueError("story_audio mode requires a script (the voiceover text)")
    if mode == "avatar_story_mix" and not script.strip():
        raise ValueError("avatar_story_mix mode requires a script (the voiceover text)")
    if mode == "engaging_avatar" and not script.strip():
        raise ValueError("engaging_avatar mode requires a script")
    if mode in ("split_horizontal", "split_screen", "split_vertical") and not script.strip():
        raise ValueError("split-screen modes require a script")
    async with acquire(tenant_id) as conn:
        row = await conn.fetchrow(
            """INSERT INTO video_productions
                 (status, title, platform, aspect, script, scenes, mode,
                  caption_style, image_style,
                  music_mood, logo_position, structure, template_id, video_engine,
                  broll_pacing,
                  avatar_provider, broll_provider, assembly_provider, broll_style)
               VALUES ('queued',$1,$2,$3,$4,$5::jsonb,$6,$7,$8,$9,$10,$11::jsonb,$12,$13,
                       $14,$15,$16,$17,$18) RETURNING *""",
            title, platform, aspect, script, json.dumps(scenes or []), mode,
            caption_style or "", image_style or "",
            music_mood or "", logo_position or "", json.dumps(structure or []), template_id,
            video_engine or "",
            broll_pacing or "",
            get_avatar_provider().name, settings.video_provider,
            get_assembly_provider().name, broll_style or "",
        )
    return _row(row)


class RenderCanceled(Exception):
    """Raised inside the worker when the user has canceled the production, so
    the render stops cleanly at the next stage boundary WITHOUT being recorded
    as a 'failed' render (the status stays 'canceled')."""


async def _abort_if_canceled(conn, pid) -> None:
    """Stage-boundary checkpoint. If the row was canceled out-of-band (the
    /cancel endpoint set status='canceled'), stop the worker before it starts
    the next — paid — stage. Cheap: one indexed SELECT per transition."""
    st = await conn.fetchval("SELECT status FROM video_productions WHERE id=$1", pid)
    if st == "canceled":
        raise RenderCanceled()


async def _set(conn, pid, **cols):
    # Every stage transition doubles as a cancellation checkpoint — the cheapest
    # moment to stop a canceled render before the next provider call is made.
    await _abort_if_canceled(conn, pid)
    sets = ", ".join(f"{k} = ${i+2}" for i, k in enumerate(cols))
    await conn.execute(
        f"UPDATE video_productions SET {sets}, updated_at=now() WHERE id=$1",
        pid, *cols.values(),
    )


async def _fail(pid, msg, tenant_id):
    async with acquire(tenant_id) as conn:
        # Never clobber a user cancellation into a 'failed' render — 'canceled'
        # is a terminal state of its own, so guard the write.
        await conn.execute(
            "UPDATE video_productions SET status='failed', error=$2, "
            "updated_at=now(), completed_at=now() WHERE id=$1 AND status <> 'canceled'",
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


async def _persist_provider_job(
    pid: UUID | None, key: str, job_id: str, provider: str,
    tenant_id: UUID | None,
) -> None:
    """Record the provider's job id on the production row (plan jsonb) at
    submit time, so a timed-out or restart-interrupted render is recoverable
    (the provider finishes — and bills — regardless). Best-effort:
    bookkeeping must never break a render."""
    if pid is None or not job_id:
        return
    try:
        async with acquire(tenant_id) as conn:
            await conn.execute(
                """UPDATE video_productions
                   SET plan = COALESCE(plan, '{}'::jsonb) || $2::jsonb,
                       updated_at=now()
                   WHERE id=$1""",
                pid, json.dumps({key: job_id, f"{key}_provider": provider}),
            )
    except Exception:  # noqa: BLE001 — never fail a render over bookkeeping
        pass


async def _render_avatar(
    text: str, aspect: str, *, captions: bool = False,
    pid: UUID | None = None, tenant_id: UUID | None = None,
) -> tuple[str | None, str]:
    prov = get_avatar_provider()
    sub = await prov.submit(text, aspect, captions=captions)
    if sub.status == "failed":
        return None, sub.error or "avatar submit failed"
    await _persist_provider_job(pid, "avatar_job_id", sub.job_id, prov.name, tenant_id)
    transient = 0
    for _ in range(_AVATAR_MAX_POLLS):
        p = await prov.poll(sub.job_id)
        if p.status == "succeeded":
            return p.url, ""
        if p.status == "failed":
            # Tolerate a few consecutive blips — one timeout over a multi-
            # minute poll loop must not crash a render HeyGen finishes anyway.
            if is_transient_poll_error(p.error) and transient < _MAX_TRANSIENT_POLL_ERRORS:
                transient += 1
            else:
                return None, p.error or "avatar render failed"
        else:
            transient = 0
        await asyncio.sleep(_POLL_EVERY)
    return None, (
        f"avatar render timed out after ~{int(_AVATAR_MAX_POLLS * _POLL_EVERY / 60)} min "
        f"({prov.name} video id {sub.job_id} saved on the production — it may "
        "still finish on the provider side)"
    )


async def _render_hero_talking_photo(
    script: str, aspect: str, tenant_id: UUID | None, *,
    captions: bool = True, pid: UUID | None = None,
) -> tuple[str | None, str]:
    """Clone the hero into a talking video: hero photos → a hyper-real
    front-facing still (image-to-image) → HeyGen Talking Photo (lip-synced in
    the brand voice). Honest: a strong likeness, not a forensic face-clone."""
    from .hero_context import get_hero_photo_files
    from .imagegen import generate_post_image_with_refs

    refs = await get_hero_photo_files(tenant_id)
    if not refs:
        return None, "no hero photos — upload photos of the hero on the Hero page first"
    png, _meta, err = await generate_post_image_with_refs(
        topic=(
            "studio portrait of this exact person, front-facing, looking "
            "straight at camera, head and shoulders, even soft lighting, "
            "clean neutral background, photorealistic, sharp focus"
        ),
        references=refs,
        style="photoreal",
        aspect=aspect,
    )
    if not png:
        return None, f"hero portrait still: {err or 'generation failed'}"

    prov = get_avatar_provider()
    tp_id, up_err = await prov.upload_talking_photo(png, mime="image/png")
    if not tp_id:
        return None, up_err or "HeyGen talking-photo upload failed"
    sub = await prov.submit_talking_photo(tp_id, script, aspect, captions=captions)
    if sub.status == "failed":
        return None, sub.error or "HeyGen talking-photo submit failed"
    await _persist_provider_job(pid, "avatar_job_id", sub.job_id, prov.name, tenant_id)
    transient = 0
    for _ in range(_AVATAR_MAX_POLLS):
        p = await prov.poll(sub.job_id)
        if p.status == "succeeded":
            return p.url, ""
        if p.status == "failed":
            if is_transient_poll_error(p.error) and transient < _MAX_TRANSIENT_POLL_ERRORS:
                transient += 1
            else:
                return None, p.error or "talking-photo render failed"
        else:
            transient = 0
        await asyncio.sleep(_POLL_EVERY)
    return None, (
        f"talking-photo render timed out after ~{int(_AVATAR_MAX_POLLS * _POLL_EVERY / 60)} min "
        f"({prov.name} video id {sub.job_id} saved on the production — it may "
        "still finish on the provider side)"
    )


async def _seed_to_public_url(data_uri: str, tenant_id: UUID | None) -> tuple[str | None, str]:
    """Some engines (Higgsfield) fetch the seed image by URL, not a data: URI.
    Upload the still to storage and return its public URL. Requires Supabase
    storage in prod (a local /media-files path isn't fetchable by the engine)."""
    try:
        import base64
        from .media import storage as _media_storage
        raw = base64.b64decode(data_uri.split(",", 1)[1])
        tenant = str(tenant_id or settings.default_tenant_id)
        served_uri, _path = await asyncio.to_thread(
            _media_storage().save, tenant, raw, "broll-seed.png",
        )
    except Exception as e:  # noqa: BLE001
        return None, f"seed image upload failed: {e}"
    if not served_uri.startswith("http"):
        return None, ("seed image isn't publicly fetchable (local storage) — "
                      "Higgsfield needs Supabase storage configured")
    return served_uri, ""


async def _render_broll(
    visual_prompt: str, aspect: str, *, engine: str = "", tenant_id: UUID | None = None,
) -> tuple[str | None, str]:
    """B-roll = text → AI still (OpenAI) → animate (Runway or, per-render,
    Higgsfield). Honest stub with a reason when an engine isn't configured."""
    if not visual_prompt:
        return None, "no visual prompt for B-roll"
    try:
        vid = provider_for(engine) if engine else get_video_provider()
    except Exception as e:  # noqa: BLE001 — missing keys → honest reason
        return None, f"{engine or 'video'} engine not configured: {e}"
    if vid.name == "stub":
        return None, f"{vid.name} video engine not configured (add the key in Settings)"
    # 1) seed image from the idea
    image_uri, img_err = await generate_seed_image(visual_prompt, aspect)
    if not image_uri:
        return None, f"seed image: {img_err}"
    # Higgsfield fetches the seed by URL — host the data-URI still first.
    seed = image_uri
    if vid.name == "higgsfield" and isinstance(seed, str) and seed.startswith("data:"):
        seed, up_err = await _seed_to_public_url(image_uri, tenant_id)
        if not seed:
            return None, up_err
    # 2) animate it (Runway needs >=5s; the assembler trims to the scene length)
    sub = await vid.submit(
        visual_prompt, seed,
        model=settings.runway_model,
        ratio=_RUNWAY_RATIO.get(aspect, settings.runway_video_ratio),
        duration=5,
    )
    if sub.status == "failed":
        return None, sub.error or f"{vid.name} submit failed"
    transient = 0
    for _ in range(_BROLL_MAX_POLLS):
        p = await vid.poll(sub.provider_job_id)
        if p.status == "succeeded":
            return p.result_url, ""
        if p.status == "failed":
            if is_transient_poll_error(p.error) and transient < _MAX_TRANSIENT_POLL_ERRORS:
                transient += 1
            else:
                return None, p.error or f"{vid.name} render failed"
        else:
            transient = 0
        await asyncio.sleep(_POLL_EVERY)
    return None, f"{vid.name} render timed out"


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
    s: dict, aspect: str, james_uris: list[str], used_clips: list[str],
    *, engine: str = "", tenant_id: UUID | None = None,
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
        url, err = await _render_broll(
            s.get("visual_prompt", ""), aspect, engine=engine, tenant_id=tenant_id
        )
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


async def _cards_and_bed(row, assets, pid, tenant_id):
    """The production's designed cards + its pinned music bed, both read off
    the style template.

    Cards stay OFF unless the template turns them on, so every production that
    predates this renders exactly as it did. Both are ADDITIVE: a director
    outage or a missing track costs the cards or the pinned bed, never the
    render — the reel falls back to plain captions and the mood-picked track.
    """
    card_els: list[dict] = []
    pinned_track = ""
    try:
        tpl_id = row["template_id"]
    except (KeyError, TypeError):
        tpl_id = None
    if not tpl_id:
        return card_els, pinned_track
    try:
        from .template_apply import map_template_to_render
        from .templates import get_template
        tpl = await get_template(tpl_id, tenant_id)
        m = map_template_to_render((tpl or {}).get("template") or {})
        if m.get("music_track_id"):
            from .audio_library import resolve_music_url_by_id
            pinned_track = await resolve_music_url_by_id(m["music_track_id"])
        if (m.get("cards") or {}).get("enabled"):
            from .brand_kit import get_brand_kit
            from .reel_cards import cards_to_elements
            from .reel_director import plan_cards, words_from_captions
            cards = await plan_cards(
                words_from_captions(assets.captions),
                duration=assets.audio_duration,
                styles=(m["cards"].get("styles") or None),
            )
            card_els = cards_to_elements(cards, await get_brand_kit())
            print(f"[cards] production {pid}: {len(cards)} card(s) placed")
    except Exception as e:  # noqa: BLE001 — additive, never fatal
        print(f"[cards] production {pid}: skipped ({type(e).__name__}: {e})")
    return card_els, pinned_track


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
    url, err = await _render_avatar(
        script, row["aspect"], captions=True, pid=pid, tenant_id=tenant_id,
    )
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
               WHERE id=$1 AND status <> 'canceled'""",
            pid, final_url, action_id,
        )


async def _run_hero_clone(row, tenant_id: UUID | None) -> None:
    """Hero-clone mode: a hyper-real still of the hero (from hero photos) →
    HeyGen Talking Photo, lip-synced in the brand voice. One render, no
    Creatomate. Lands in the approval queue like every other piece."""
    pid = row["id"]
    script = (row["script"] or "").strip()
    if not script:
        return await _fail(pid, "hero clone needs a script", tenant_id)
    async with acquire(tenant_id) as conn:
        await _set(conn, pid, status="rendering_clips")
    url, err = await _render_hero_talking_photo(
        script, row["aspect"], tenant_id, captions=True, pid=pid,
    )
    if not url:
        return await _fail(pid, err or "hero clone render failed", tenant_id)
    durable, _actual = await _persist_clip_to_storage(url, f"hero-clone-{pid}")
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
                "mode": "hero_clone",
            }),
        )
        await conn.execute(
            """UPDATE video_productions SET status='succeeded', final_url=$2,
               queued_action_id=$3, updated_at=now(), completed_at=now()
               WHERE id=$1 AND status <> 'canceled'""",
            pid, final_url, action_id,
        )


async def _run_story_audio(row, tenant_id: UUID | None) -> None:
    """Story-audio mode: HeyGen voice → Whisper word-stamps → 8-18 visual
    beats → gpt-image-1 still per beat → Creatomate stitches audio +
    stills + word-pinned captions.

    State machine (the externally-visible status field):
      queued → planning  (we render HeyGen to get the voice)
             → rendering_clips  (Whisper + image generation per beat)
             → assembling  (Creatomate)
             → succeeded / failed
    """
    from .story_video import (
        build_story_audio_assets, beats_to_dict, pick_caption_style,
    )

    pid = row["id"]
    script = (row["script"] or "").strip()
    if not script:
        return await _fail(pid, "story_audio mode needs a script", tenant_id)

    # 1) HeyGen → voice (the avatar video is a means to an end here).
    async with acquire(tenant_id) as conn:
        await _set(conn, pid, status="planning")
    avatar_url, err = await _render_avatar(
        script, row["aspect"], captions=False, pid=pid, tenant_id=tenant_id,
    )
    if not avatar_url:
        return await _fail(pid, err or "HeyGen render failed", tenant_id)

    # 2) Strip + Whisper + segment + image-gen (the heavy lift).
    async with acquire(tenant_id) as conn:
        await _set(conn, pid, status="rendering_clips")
    # Past human rejections steer this render. story_audio's only LLM
    # visual call is the per-beat image prompt (via brand_context), so we
    # fold the B-roll avoid block into brand_context; captions get their
    # own avoid block in the picker below.
    broll_avoid = await _avoid_block(["broll"], tenant_id)
    cap_avoid = await _avoid_block(["captions"], tenant_id)
    brand_context = (
        f"Brand: {row['title'] or 'James Prendamano'}. "
        "Real-estate broker and brand voice, Staten Island / NYC focus. "
        f"Platform: {row['platform']}. Aspect: {row['aspect']}."
        f"{chr(10) + broll_avoid if broll_avoid else ''}"
    )
    # Image style: user-pinned wins, else default to 'cinematic' for
    # story-mode (matches the reference video aesthetic — film stills,
    # symbolic objects, dramatic light).
    try:
        istyle = (row["image_style"] or "").strip()
    except (KeyError, TypeError):
        istyle = ""
    if not istyle:
        istyle = "cinematic"
    assets = await build_story_audio_assets(
        avatar_video_url=avatar_url,
        aspect=row["aspect"],
        style=istyle,
        brand_context=brand_context,
        platform=row["platform"],
        tenant_id=str(tenant_id) if tenant_id else None,
    )
    if assets.error or not assets.audio_url or not assets.beats:
        return await _fail(pid, assets.error or "story assets failed", tenant_id)

    # Persist intermediate state — every beat with its prompt + image
    # URL goes into video_productions.scenes so the UI can inspect it.
    async with acquire(tenant_id) as conn:
        await _set(
            conn, pid, status="assembling",
            scenes=json.dumps(beats_to_dict(assets.beats)),
        )

    # 3) Creatomate — story-shaped source builder.
    asm = get_assembly_provider()
    if not hasattr(asm, "render_story"):
        return await _fail(
            pid, "assembly provider does not support story_audio mode", tenant_id,
        )
    # Resolve caption preset — user-picked wins, else LLM picks based
    # on script energy + platform. Honest fallback inside picker.
    try:
        cstyle = (row["caption_style"] or "").strip()
    except (KeyError, TypeError):
        cstyle = ""
    if not cstyle:
        cstyle, _why = await pick_caption_style(
            script, row["platform"], brand_context, avoid=cap_avoid,
        )

    res = await asm.render_story(
        audio_url=assets.audio_url,
        audio_duration=assets.audio_duration,
        beats=beats_to_dict(assets.beats),
        captions=assets.captions,
        aspect=row["aspect"],
        music_mood=(row["music_mood"] or "calm"),
        caption_style=cstyle,
    )
    if res.status == "processing":
        for _ in range(_MAX_POLLS):
            await asyncio.sleep(_POLL_EVERY)
            res = await asm.poll(res.render_id)
            if res.status in ("succeeded", "failed"):
                break
    if res.status != "succeeded" or not res.url:
        return await _fail(pid, res.error or "story assembly failed", tenant_id)

    # 4) Queue + close out.
    async with acquire(tenant_id) as conn:
        action_id = await conn.fetchval(
            """INSERT INTO actions (proposed_by, action_type, payload, status)
               VALUES ('video_producer','video',$1::jsonb,'pending') RETURNING id""",
            json.dumps({
                "platform": row["platform"], "format": "video",
                "content": row["title"] or script[:120],
                "caption": row["title"] or "",
                "media_url": res.url,
                "stub": res.url.startswith("stub://"),
                "mode": "story_audio",
                "beats": len(assets.beats),
            }),
        )
        await conn.execute(
            """UPDATE video_productions SET status='succeeded', final_url=$2,
               queued_action_id=$3, updated_at=now(), completed_at=now()
               WHERE id=$1 AND status <> 'canceled'""",
            pid, res.url, action_id,
        )


async def _run_avatar_story_mix(row, tenant_id: UUID | None) -> None:
    """Mixed-mode pipeline: ONE HeyGen render, reused twice.

    Same five opening steps as story_audio (HeyGen → audio strip →
    Whisper word-stamps → segment → persist), then forks:

      a) LLM classifies each beat as 'avatar' (James on camera) or
         'broll' (AI photoreal still that visualizes the moment)
      b) broll beats get a visual prompt and a gpt-image-1 still
      c) avatar beats get a silent video slice from the cached HeyGen
         mp4 covering that beat's [start, end]
      d) Creatomate stitches all of it: voice (whole), mixed visual
         track per beat, word-pinned captions, optional music

    The avatar slices are silent so the master voice on track 1 isn't
    duplicated — playing two copies of the same HeyGen audio creates
    a perfect echo. Verified empirically.
    """
    from .story_video import (
        build_avatar_story_mix_assets, beats_to_dict, pick_caption_style,
    )

    pid = row["id"]
    script = (row["script"] or "").strip()
    if not script:
        return await _fail(pid, "avatar_story_mix mode needs a script", tenant_id)

    async with acquire(tenant_id) as conn:
        await _set(conn, pid, status="planning")
    avatar_url, err = await _render_avatar(
        script, row["aspect"], captions=False, pid=pid, tenant_id=tenant_id,
    )
    if not avatar_url:
        return await _fail(pid, err or "HeyGen render failed", tenant_id)

    async with acquire(tenant_id) as conn:
        await _set(conn, pid, status="rendering_clips")
    # Steer the B-roll beat prompts away from past rejections via
    # brand_context (feeds both the beat classifier and the image-prompt
    # LLM); captions get their own avoid block in the picker below.
    broll_avoid = await _avoid_block(["broll"], tenant_id)
    cap_avoid = await _avoid_block(["captions"], tenant_id)
    brand_context = (
        f"Brand: {row['title'] or 'James Prendamano'}. "
        "Real-estate broker and brand voice, Staten Island / NYC focus. "
        f"Platform: {row['platform']}. Aspect: {row['aspect']}."
        f"{chr(10) + broll_avoid if broll_avoid else ''}"
    )
    # Mix mode: image_style applies only to B-roll beats (avatar beats
    # use the actual HeyGen video slice, no AI image). User-pinned wins,
    # else default to 'cinematic' for the dramatic-cutaway look.
    try:
        istyle = (row["image_style"] or "").strip()
    except (KeyError, TypeError):
        istyle = ""
    if not istyle:
        istyle = "cinematic"
    assets = await build_avatar_story_mix_assets(
        avatar_video_url=avatar_url,
        aspect=row["aspect"],
        style=istyle,
        brand_context=brand_context,
        platform=row["platform"],
        tenant_id=str(tenant_id) if tenant_id else None,
    )
    if assets.error or not assets.audio_url or not assets.beats:
        return await _fail(pid, assets.error or "mix assets failed", tenant_id)

    async with acquire(tenant_id) as conn:
        await _set(
            conn, pid, status="assembling",
            scenes=json.dumps(beats_to_dict(assets.beats)),
        )

    asm = get_assembly_provider()
    if not hasattr(asm, "render_avatar_story_mix"):
        return await _fail(
            pid, "assembly provider does not support avatar_story_mix mode",
            tenant_id,
        )
    try:
        cstyle = (row["caption_style"] or "").strip()
    except (KeyError, TypeError):
        cstyle = ""
    if not cstyle:
        cstyle, _why = await pick_caption_style(
            script, row["platform"], brand_context, avoid=cap_avoid,
        )

    res = await asm.render_avatar_story_mix(
        audio_url=assets.audio_url,
        audio_duration=assets.audio_duration,
        beats=beats_to_dict(assets.beats),
        captions=assets.captions,
        aspect=row["aspect"],
        music_mood=(row["music_mood"] or "calm"),
        caption_style=cstyle,
    )
    if res.status == "processing":
        for _ in range(_MAX_POLLS):
            await asyncio.sleep(_POLL_EVERY)
            res = await asm.poll(res.render_id)
            if res.status in ("succeeded", "failed"):
                break
    if res.status != "succeeded" or not res.url:
        return await _fail(pid, res.error or "mix assembly failed", tenant_id)

    async with acquire(tenant_id) as conn:
        action_id = await conn.fetchval(
            """INSERT INTO actions (proposed_by, action_type, payload, status)
               VALUES ('video_producer','video',$1::jsonb,'pending') RETURNING id""",
            json.dumps({
                "platform": row["platform"], "format": "video",
                "content": row["title"] or script[:120],
                "caption": row["title"] or "",
                "media_url": res.url,
                "stub": res.url.startswith("stub://"),
                "mode": "avatar_story_mix",
                "beats": len(assets.beats),
                "avatar_beats": sum(1 for b in assets.beats if b.role == "avatar"),
                "broll_beats": sum(1 for b in assets.beats if b.role == "broll"),
            }),
        )
        await conn.execute(
            """UPDATE video_productions SET status='succeeded', final_url=$2,
               queued_action_id=$3, updated_at=now(), completed_at=now()
               WHERE id=$1 AND status <> 'canceled'""",
            pid, res.url, action_id,
        )


async def _run_engaging_avatar(
    row, tenant_id: UUID | None, composition: str = "engaging_avatar",
) -> None:
    """engaging_avatar mode — HeyGen avatar plays continuously with
    2-5 cinematic B-roll cutaways overlaid at LLM-picked moments.

    `composition` selects the FINAL Creatomate layout from the SAME assets
    (avatar render + Whisper word-stamps + LLM-picked B-roll inserts +
    captions):
      * 'engaging_avatar'  — full-frame speaker, B-roll as transient cutaways
      * 'split_horizontal' — speaker pinned top, B-roll+text pinned bottom
                             (the captured split-screen reel composition)
    Everything upstream is identical; only the render method differs.

    State machine:
      queued → planning  (HeyGen render)
             → rendering_clips  (Whisper + insert image gen)
             → assembling  (Creatomate)
             → succeeded
    """
    from .story_video import (
        build_engaging_avatar_assets, inserts_to_dict, pick_caption_style,
    )

    pid = row["id"]
    script = (row["script"] or "").strip()
    if not script:
        return await _fail(pid, "engaging_avatar mode needs a script", tenant_id)

    # 1) HeyGen render (captions OFF — we burn our own with safe-zone
    # placement that respects the insert overlays)
    async with acquire(tenant_id) as conn:
        await _set(conn, pid, status="planning")
    # Output is ALWAYS 9:16 vertical. For the split, the speaker goes in the
    # TOP HALF — a near-square box. A 9:16 portrait cover-cropped into a square
    # box loses the head (the "only part of his face" bug); a LANDSCAPE
    # head-and-shoulders source cover-fits that box with the FULL face. So the
    # split speaker is shot landscape and cropped into the top half — it never
    # appears as a 16:9 video; the final reel is 9:16.
    avatar_aspect = "16:9" if composition == "split_horizontal" else row["aspect"]
    avatar_url, err = await _render_avatar(
        script, avatar_aspect, captions=False, pid=pid, tenant_id=tenant_id,
    )
    if not avatar_url:
        return await _fail(pid, err or "HeyGen render failed", tenant_id)

    # Persist the avatar video to our storage so Creatomate has a
    # durable reachable URL (HeyGen URLs expire).
    durable_avatar, _ = await _persist_clip_to_storage(
        avatar_url, f"engaging-avatar-{pid}"
    )
    avatar_url = durable_avatar or avatar_url

    # 2) Extract audio + Whisper + LLM picks inserts + image gen
    async with acquire(tenant_id) as conn:
        await _set(conn, pid, status="rendering_clips")
    # B-roll inserts are this mode's only generated visual; steer the
    # insert-picker via the builder's dedicated broll_avoid param.
    # Captions get their own avoid block in the picker below.
    broll_avoid = await _avoid_block(["broll"], tenant_id)
    cap_avoid = await _avoid_block(["captions"], tenant_id)
    brand_context = (
        f"Brand: {row['title'] or 'James Prendamano'}. "
        "Real-estate broker and brand voice, Staten Island / NYC focus. "
        f"Platform: {row['platform']}. Aspect: {row['aspect']}."
    )
    try:
        istyle = (row["image_style"] or "").strip()
    except (KeyError, TypeError):
        istyle = ""
    if not istyle:
        istyle = "cinematic"
    assets = await build_engaging_avatar_assets(
        avatar_video_url=avatar_url,
        aspect=row["aspect"],
        style=istyle,
        brand_context=brand_context,
        platform=row["platform"],
        tenant_id=str(tenant_id) if tenant_id else None,
        broll_avoid=broll_avoid,
        engine=(row["video_engine"] or ""),   # Runway / Higgsfield for B-roll
        broll_pacing=(row.get("broll_pacing") or ""),
    )
    if assets.error:
        return await _fail(pid, assets.error, tenant_id)

    # Persist insert metadata for the UI.
    async with acquire(tenant_id) as conn:
        await _set(
            conn, pid, status="assembling",
            scenes=json.dumps(inserts_to_dict(assets.inserts)),
        )

    # 3) Caption preset (auto if blank)
    try:
        cstyle = (row["caption_style"] or "").strip()
    except (KeyError, TypeError):
        cstyle = ""
    if not cstyle:
        cstyle, _why = await pick_caption_style(
            script, row["platform"], brand_context, avoid=cap_avoid,
        )

    # 4) Creatomate compose. If no inserts (LLM picked zero), the
    # assembler still renders the avatar with captions only — degraded
    # but ships.
    asm = get_assembly_provider()
    render_method = {
        "split_horizontal": "render_split_horizontal",
        "split_vertical": "render_split_vertical",
    }.get(composition, "render_engaging_avatar")
    if not hasattr(asm, render_method):
        return await _fail(
            pid, f"assembly provider does not support {composition} mode",
            tenant_id,
        )
    _cards, _bed = await _cards_and_bed(row, assets, pid, tenant_id)
    _extra = {}
    if render_method == "render_engaging_avatar":
        # Only the full-frame builder draws cards today; the split compositions
        # have no card layer yet, so don't pretend otherwise.
        _extra["card_elements"] = _cards
    res = await getattr(asm, render_method)(
        avatar_video_url=assets.avatar_video_url,
        audio_duration=assets.audio_duration,
        inserts=inserts_to_dict(assets.inserts),
        captions=assets.captions,
        aspect=row["aspect"],
        music_mood=(row["music_mood"] or "calm"),
        caption_style=cstyle,
        music_track_url=_bed,
        **_extra,
    )
    if res.status == "processing":
        for _ in range(_MAX_POLLS):
            await asyncio.sleep(_POLL_EVERY)
            res = await asm.poll(res.render_id)
            if res.status in ("succeeded", "failed"):
                break
    if res.status != "succeeded" or not res.url:
        return await _fail(pid, res.error or "engaging_avatar assembly failed", tenant_id)

    async with acquire(tenant_id) as conn:
        action_id = await conn.fetchval(
            """INSERT INTO actions (proposed_by, action_type, payload, status)
               VALUES ('video_producer','video',$1::jsonb,'pending') RETURNING id""",
            json.dumps({
                "platform": row["platform"], "format": "video",
                "content": row["title"] or script[:120],
                "caption": row["title"] or "",
                "media_url": res.url,
                "stub": res.url.startswith("stub://"),
                "mode": composition,
                "inserts": len(assets.inserts),
                "hero_inserts": sum(1 for i in assets.inserts if i.uses_hero),
            }),
        )
        await conn.execute(
            """UPDATE video_productions SET status='succeeded', final_url=$2,
               queued_action_id=$3, updated_at=now(), completed_at=now()
               WHERE id=$1 AND status <> 'canceled'""",
            pid, res.url, action_id,
        )


def _aspect_ratio_of(aspect: str, default: float = 9.0 / 16.0) -> float:
    """Parse a 'W:H' aspect string → W/H float (e.g. '9:16' → 0.5625).
    Falls back to 9:16 on anything unparseable."""
    try:
        w, h = (aspect or "").split(":")
        r = float(w) / float(h)
        return r if r > 0 else default
    except (ValueError, ZeroDivisionError, AttributeError):
        return default


async def _run_long_form_reel(row, tenant_id: UUID | None) -> None:
    """long_form_reel mode — cut a window out of a long-form source,
    then run the engaging-avatar treatment on the cut.

    The candidate metadata lives in row['scenes'] (jsonb) shaped as:
      {source_id, candidate_id, source_url, start_s, end_s, hook_quote}

    Stages:
      planning         — ffmpeg-cut [start_s, end_s] from source
                         mp4; persist the cut to Supabase Storage so
                         Creatomate can fetch it
      rendering_clips  — extract audio, Whisper word-stamps, LLM
                         picks B-roll inserts every 5s, gen + Runway
                         animate (same path as engaging_avatar)
      assembling       — Creatomate stitches: source cut on track 1
                         + B-roll overlays on track 2 + word-pinned
                         captions on track 3 + royalty-free music
                         underbed on track 4
    """
    import tempfile
    from pathlib import Path as _P

    from .audio_trim import slice_video_compact
    from .story_video import (
        build_engaging_avatar_assets,
        inserts_to_dict,
        pick_caption_style,
    )

    pid = row["id"]
    plan = row["scenes"]
    if isinstance(plan, str):
        plan = json.loads(plan)
    if not plan or not isinstance(plan, list) or not plan[0]:
        return await _fail(pid, "long_form_reel needs candidate metadata", tenant_id)
    # Every plan entry carrying a time window is a segment to cut. Topic
    # builds pass several (possibly from different sources) — they get
    # stitched into ONE cut before the engaging treatment. Rendered-insert
    # dicts appended on a previous run use start/end (no start_s), so a
    # re-run naturally ignores them.
    windows: list[dict] = []
    for entry in plan:
        if not isinstance(entry, dict) or "start_s" not in entry:
            continue
        e_url = (entry.get("source_url") or "").strip()
        e_drive = (entry.get("drive_file_id") or "").strip()
        try:
            e_start = float(entry["start_s"])
            e_end = float(entry["end_s"])
        except (KeyError, TypeError, ValueError):
            return await _fail(pid, "candidate window malformed", tenant_id)
        if e_end <= e_start:
            return await _fail(pid, "candidate window malformed", tenant_id)
        # Drive source-of-truth path needs only drive_file_id; legacy
        # Supabase-backed sources still need a usable source_url.
        if not e_drive and not e_url.startswith("http"):
            return await _fail(pid, "candidate window malformed", tenant_id)
        windows.append({**entry, "source_url": e_url, "drive_file_id": e_drive,
                        "start_s": e_start, "end_s": e_end})
    if not windows:
        return await _fail(pid, "candidate window malformed", tenant_id)
    meta = windows[0]
    start_s, end_s = meta["start_s"], meta["end_s"]

    # ── 1) Cut the source ────────────────────────────────────────
    async with acquire(tenant_id) as conn:
        await _set(conn, pid, status="planning")

    # Speaker re-centering: detected inside the temp block (while the local cut
    # exists) so the vertical crop can pan to keep James centered. None → the
    # assembler keeps today's centered cover crop.
    speaker_face_x: float | None = None
    source_overflow_pct: float | None = None

    # Re-fetched source can be multi-GB — keep it on the mounted volume
    # (BIG_FILE_TMP) so it doesn't fill the container's ephemeral disk.
    from .drive import big_file_tmp_dir
    with tempfile.TemporaryDirectory(dir=big_file_tmp_dir()) as td:
        out_path = f"{td}/cut.mp4"
        # Group windows by source so only ONE (possibly multi-GB) original
        # sits on disk at a time: download → cut its windows → delete it,
        # then move to the next source. Cut order still follows the
        # storytelling order the windows arrived in.
        by_source: dict[str, list[int]] = {}
        for wi, win in enumerate(windows):
            by_source.setdefault(
                win["drive_file_id"] or win["source_url"], []).append(wi)
        cut_by_window: dict[int, str] = {}
        for si, indices in enumerate(by_source.values()):
            first = windows[indices[0]]
            src_path = f"{td}/source{si}.mp4"
            # Prefer Drive when drive_file_id is set — re-fetch the
            # original from the service account every time (fast, free,
            # no Supabase size cap). Falls back to source_url for the
            # legacy upload path.
            if first["drive_file_id"]:
                from .drive import fetch_drive_file_to_path, DriveNotConfigured
                try:
                    await fetch_drive_file_to_path(first["drive_file_id"], src_path)
                except DriveNotConfigured as e:
                    return await _fail(pid, f"drive not configured: {e}", tenant_id)
                except Exception as e:  # noqa: BLE001
                    return await _fail(
                        pid, f"could not re-fetch from Drive: {e}", tenant_id,
                    )
            else:
                try:
                    async with httpx.AsyncClient(
                        timeout=httpx.Timeout(900.0, connect=15.0),
                    ) as c:
                        async with c.stream("GET", first["source_url"]) as r:
                            r.raise_for_status()
                            with open(src_path, "wb") as fh:
                                async for chunk in r.aiter_bytes(chunk_size=1 << 20):
                                    fh.write(chunk)
                except Exception as e:  # noqa: BLE001
                    return await _fail(pid, f"could not fetch source: {e}", tenant_id)

            # Compact slicer (CRF 20 + mono + height-capped) keeps the
            # working artifact under Supabase Storage's service-tier size
            # cap (HTTP 413 kicks in around 50-100 MB). Talking-head
            # footage compresses well so the quality drop is minor — and
            # Creatomate re-encodes for the final reel anyway.
            for wi in indices:
                cpath = f"{td}/seg{wi}.mp4"
                if not await slice_video_compact(
                    src_path, cpath, windows[wi]["start_s"], windows[wi]["end_s"],
                ):
                    return await _fail(pid, "ffmpeg cut failed", tenant_id)
                cut_by_window[wi] = cpath
            # Free the original before fetching the next source.
            _P(src_path).unlink(missing_ok=True)
        cut_paths = [cut_by_window[wi] for wi in range(len(windows))]

        if len(cut_paths) == 1:
            out_path = cut_paths[0]
        else:
            from .audio_trim import concat_videos_normalized
            if not await concat_videos_normalized(cut_paths, out_path):
                return await _fail(pid, "ffmpeg concat failed", tenant_id)
            print(f"[long_form] stitched {len(cut_paths)} segments into one cut")

        # Speaker re-centering (best-effort, never breaks the render). If the cut
        # is WIDER than the target reel aspect (16:9 podcast → 9:16), the default
        # center-crop can slice the speaker to the edge — so detect the face once
        # and pan the crop to center it at assembly time. For a TWO-person shot
        # this detector now returns the most prominent person (not "no single
        # face" → blind center crop that lost BOTH), so at least one is framed;
        # the active-speaker keyframes below still handle who's-talking when
        # diarization is available. A stitched multi-segment cut can put the
        # speaker in a DIFFERENT spot per segment — one static pan would mis-crop
        # the others, so stitched cuts keep the safe center crop.
        try:
            from .audio_trim import probe_video_dims, probe_duration
            _dims = await probe_video_dims(out_path)
            if _dims and _dims[1] > 0:
                _cw, _ch = _dims
                _src_ar = _cw / _ch
                _out_ar = _aspect_ratio_of(row["aspect"])
                if len(windows) > 1:
                    print(f"[long_form] centering: skipped — {len(windows)}-segment "
                          f"stitch keeps the default center crop")
                elif _src_ar > _out_ar * 1.05:          # source is meaningfully wider
                    source_overflow_pct = round(_src_ar / _out_ar * 100.0, 1)
                    from .perception import detect_speaker_center_x
                    _dur = await probe_duration(out_path)
                    speaker_face_x = await detect_speaker_center_x(out_path, _dur)
                    # No speaking face (scenery / object footage): pan to the main
                    # visual SUBJECT the shot is about, instead of a blind center crop.
                    if speaker_face_x is None and settings.subject_focus_enabled:
                        from .perception import detect_subject_center_x
                        _subj = await detect_subject_center_x(out_path, _dur)
                        if _subj is not None:
                            speaker_face_x = _subj[0]
                            print(f"[long_form] centering: no face → SUBJECT "
                                  f"'{_subj[1]}' at x={_subj[0]} → pan to it")
                    print(
                        f"[long_form] centering: cut {_cw}x{_ch} (ar {_src_ar:.2f}) "
                        f"→ overflow {source_overflow_pct}%, face_x={speaker_face_x} "
                        f"({'PAN to subject/center' if speaker_face_x is not None else 'no subject → center crop'})"
                    )
                else:
                    print(f"[long_form] centering: cut ar {_src_ar:.2f} ≤ target "
                          f"{_out_ar:.2f} — already vertical, no pan")
        except Exception as e:  # noqa: BLE001 — centering is best-effort
            print(f"[long_form] speaker-centering detection skipped: {e}")
            speaker_face_x, source_overflow_pct = None, None

        try:
            cut_bytes = _P(out_path).read_bytes()
        except OSError as e:
            return await _fail(pid, f"could not read cut: {e}", tenant_id)

    tid_str = str(tenant_id or settings.default_tenant_id)
    try:
        cut_url, _ = await asyncio.to_thread(
            media_storage().save, tid_str, cut_bytes,
            f"reel-cut-{pid}.mp4",
        )
    except Exception as e:  # noqa: BLE001
        return await _fail(pid, f"could not persist cut: {e}", tenant_id)

    # ── 2) Reuse the engaging-avatar pipeline on the cut ─────────
    # build_engaging_avatar_assets expects a video URL with audio
    # baked in — our cut has it — and produces inserts + captions
    # the same way. Hero refs flow automatically.
    async with acquire(tenant_id) as conn:
        await _set(conn, pid, status="rendering_clips")
    # Same engaging-avatar treatment, so the same avoid blocks apply:
    # B-roll inserts via the builder's broll_avoid param, captions via
    # the picker below.
    broll_avoid = await _avoid_block(["broll"], tenant_id)
    cap_avoid = await _avoid_block(["captions"], tenant_id)
    _win_desc = (
        f"[{start_s:.1f}s – {end_s:.1f}s]" if len(windows) == 1
        else f"{len(windows)} stitched segments"
    )
    brand_context = (
        f"Source: long-form podcast / interview. "
        f"Window: {_win_desc}. "
        f"Hook: {(meta.get('hook_quote') or '')[:200]}. "
        f"Platform: {row['platform']}. Aspect: {row['aspect']}."
    )
    try:
        istyle = (row["image_style"] or "").strip()
    except (KeyError, TypeError):
        istyle = ""
    if not istyle:
        istyle = "cinematic"

    try:
        bstyle = (row["broll_style"] or "").strip().lower() or "literal"
    except (KeyError, TypeError):
        bstyle = "literal"

    # Speaker name-tag assignment lives on the source (assigned once via the
    # "who is this?" step); it applies to every reel cut from that source.
    speaker_assignment: list[dict] = []
    _src_id = meta.get("source_id")
    if _src_id:
        try:
            async with acquire(tenant_id) as conn:
                _st = await conn.fetchval(
                    "SELECT speaker_tags FROM long_sources WHERE id=$1", UUID(str(_src_id))
                )
            if isinstance(_st, str):
                _st = json.loads(_st)
            if isinstance(_st, list):
                speaker_assignment = [a for a in _st if isinstance(a, dict) and a.get("handle")]
        except Exception:  # noqa: BLE001 — no tags → just no name-tags
            speaker_assignment = []

    assets = await build_engaging_avatar_assets(
        avatar_video_url=cut_url,
        aspect=row["aspect"],
        style=istyle,
        brand_context=brand_context,
        platform=row["platform"],
        tenant_id=str(tenant_id) if tenant_id else None,
        broll_avoid=broll_avoid,
        engine=(row["video_engine"] or ""),   # Runway / Higgsfield for B-roll
        broll_pacing=(row.get("broll_pacing") or ""),
        broll_style=bstyle,                   # 'literal' | 'cinematic'
        speaker_assignment=speaker_assignment,
    )
    if assets.error:
        return await _fail(pid, assets.error, tenant_id)

    async with acquire(tenant_id) as conn:
        # Preserve EVERY candidate window up front (a topic build has
        # several) and append the rendered insert metadata after, so the
        # UI can show both and a re-run can re-derive the windows.
        merged_scenes = [*windows, *inserts_to_dict(assets.inserts)]
        await _set(
            conn, pid, status="assembling",
            scenes=json.dumps(merged_scenes),
        )

    try:
        cstyle = (row["caption_style"] or "").strip()
    except (KeyError, TypeError):
        cstyle = ""
    if not cstyle:
        # Clean WHITE captions are the default reel look (configurable via the
        # Autopilot "Caption style" setting). An explicit caption_style on the
        # row still overrides.
        try:
            from .autopilot import get_config
            cstyle = (await get_config(tenant_id)).get("default_caption_style") or "clean_white"
        except Exception:  # noqa: BLE001
            cstyle = "clean_white"

    # Short, punchy on-screen HOOK (big bold white) generated from the spoken
    # words — not the long run-on opening line.
    from .content import gen_video_hook
    _hook_src = " ".join((c.get("text") or "") for c in (assets.captions or [])).strip()
    short_hook = await gen_video_hook(_hook_src or meta.get("hook_quote", ""), tenant_id)

    card_els, pinned_track = await _cards_and_bed(row, assets, pid, tenant_id)

    asm = get_assembly_provider()
    if not hasattr(asm, "render_engaging_avatar"):
        return await _fail(
            pid, "assembly provider does not support long_form_reel",
            tenant_id,
        )
    res = await asm.render_engaging_avatar(
        card_elements=card_els,
        music_track_url=pinned_track,
        avatar_video_url=assets.avatar_video_url,
        audio_duration=assets.audio_duration,
        inserts=inserts_to_dict(assets.inserts),
        captions=assets.captions,
        aspect=row["aspect"],
        music_mood=(row["music_mood"] or "calm"),
        caption_style=cstyle,
        # Short bold-white hook for the first ~3s (what the reel is about).
        hook_title=short_hook or (meta.get("hook_quote") or row["title"] or "")[:80],
        # Reframe the wide source to 9:16: prefer speaker-FOLLOWING keyframes
        # (2-person interview, panning to the active speaker); else the static
        # single-face pan. When keyframes exist, use the overflow width they were
        # built for so the media box matches.
        speaker_face_x=speaker_face_x,
        source_overflow_pct=assets.speaker_overflow_pct or source_overflow_pct,
        speaker_keyframes=assets.speaker_keyframes,
        speaker_tags=assets.speaker_tags,     # lower-third name-tags
    )
    if res.status == "processing":
        for _ in range(_MAX_POLLS):
            await asyncio.sleep(_POLL_EVERY)
            res = await asm.poll(res.render_id)
            if res.status in ("succeeded", "failed"):
                break
    if res.status != "succeeded" or not res.url:
        return await _fail(pid, res.error or "long_form_reel assembly failed", tenant_id)

    # A relevant social caption from the reel's actual spoken words (+ brand
    # sign-off) — shown beside the video in the Approval Queue.
    from .content import gen_video_caption
    _spoken = " ".join((c.get("text") or "") for c in (assets.captions or [])).strip()
    social_caption = await gen_video_caption(
        _spoken or meta.get("hook_quote", ""), row["platform"], tenant_id,
    )

    async with acquire(tenant_id) as conn:
        action_id = await conn.fetchval(
            """INSERT INTO actions (proposed_by, action_type, payload, status)
               VALUES ('video_producer','video',$1::jsonb,'pending') RETURNING id""",
            json.dumps({
                "platform": row["platform"], "format": "video",
                "content": row["title"] or meta.get("hook_quote", "")[:120],
                "caption": social_caption or meta.get("hook_quote", "")[:160],
                "media_url": res.url,
                "stub": res.url.startswith("stub://"),
                "mode": "long_form_reel",
                "source_id": meta.get("source_id"),
                "candidate_id": meta.get("candidate_id"),
                "window": f"{start_s:.1f}-{end_s:.1f}s",
                "inserts": len(assets.inserts),
            }),
        )
        await conn.execute(
            """UPDATE video_productions SET status='succeeded', final_url=$2,
               queued_action_id=$3, updated_at=now(), completed_at=now()
               WHERE id=$1 AND status <> 'canceled'""",
            pid, res.url, action_id,
        )


async def run_production(production_id: UUID, tenant_id: UUID | None = None) -> None:
    """Public entry: throttle concurrent renders behind a process-wide semaphore.
    A batch (a Drive import fanning out N clips, a bulk autopilot run) spawns many
    run_production tasks at once; each downloads a multi-GB source and runs ffmpeg
    + minutes of provider polls, so N-at-once exhausts disk/RAM on the single
    instance. Queued renders wait here for a slot; the render itself is unchanged."""
    async with _render_semaphore():
        await _run_production(production_id, tenant_id)


async def _run_production(production_id: UUID, tenant_id: UUID | None = None) -> None:
    """The worker. Advances the production through every stage."""
    pid = production_id
    try:
        # ── plan (skip if the editor supplied an edited plan) ──
        async with acquire(tenant_id) as conn:
            row = await conn.fetchrow("SELECT * FROM video_productions WHERE id=$1", pid)
            if row is None:
                return
            if row["status"] == "canceled":
                return  # canceled before the worker even picked it up

        # Avatar-only mode forks here — one HeyGen render of the entire
        # script, no per-scene plan, no Creatomate assembly.
        if row["mode"] == "long_form_reel":
            return await _run_long_form_reel(row, tenant_id)
        if row["mode"] == "hero_clone":
            return await _run_hero_clone(row, tenant_id)
        if row["mode"] == "engaging_avatar":
            return await _run_engaging_avatar(row, tenant_id)
        if row["mode"] in ("split_horizontal", "split_screen"):
            # Same asset pipeline as engaging_avatar, different final layout:
            # speaker pinned top, B-roll+text pinned bottom.
            return await _run_engaging_avatar(
                row, tenant_id, composition="split_horizontal"
            )
        if row["mode"] == "split_vertical":
            # Same asset pipeline, speaker pinned LEFT, B-roll+text pinned RIGHT.
            return await _run_engaging_avatar(
                row, tenant_id, composition="split_vertical"
            )
        if row["mode"] == "avatar_story_mix":
            return await _run_avatar_story_mix(row, tenant_id)
        if row["mode"] == "story_audio":
            return await _run_story_audio(row, tenant_id)
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
            # Replication: a style template can supply a clamped scene
            # structure and template-level music/logo to stamp onto the plan.
            _struct = row["structure"]
            if isinstance(_struct, str):
                _struct = json.loads(_struct)
            _struct = _struct if (isinstance(_struct, list) and _struct) else None
            plan = await generate_scene_plan(
                row["script"], row["platform"], row["aspect"],
                structure=_struct, tenant_id=tenant_id,
            )
            scenes = plan.get("scenes") or []
            if not scenes:
                return await _fail(pid, plan.get("error") or "no scenes planned", tenant_id)
            _logo_pos = (row["logo_position"] or "").strip()
            if (row["music_mood"] or "") or _logo_pos:
                from .template_apply import apply_overrides_to_scenes
                apply_overrides_to_scenes(
                    scenes,
                    music_mood=(row["music_mood"] or ""),
                    logo_on=bool(_logo_pos),
                    logo_position=_logo_pos or "bottom-right",
                )
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
                await _render_scene_inplace(
                    s, row["aspect"], james_uris, used_clips,
                    engine=(row["video_engine"] or ""), tenant_id=tenant_id,
                )
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
                    "content": final_title or (row["script"] or "")[:120],
                    "caption": final_title or "",
                    "media_url": res.url,
                    "stub": res.url.startswith("stub://"),
                    "scenes": len(scenes),
                    "mode": row["mode"],
                }),
            )
            await conn.execute(
                """UPDATE video_productions SET status='succeeded', final_url=$2,
                   queued_action_id=$3, scenes=$4, updated_at=now(), completed_at=now()
                   WHERE id=$1 AND status <> 'canceled'""",
                pid, res.url, action_id, json.dumps(scenes),
            )
    except RenderCanceled:
        return  # user canceled mid-render — status is already 'canceled'
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


async def cancel_production(production_id: UUID, tenant_id: UUID | None = None) -> dict:
    """Cancel an in-flight render. Flips status to 'canceled' ONLY while the
    production is still in a non-terminal stage; the worker's per-stage
    checkpoint (_abort_if_canceled in _set) then stops it before the next paid
    provider call. Returns {ok, id, status}: ok=False (with the current status)
    when the render already finished, or status=None when it doesn't exist."""
    async with acquire(tenant_id) as conn:
        row = await conn.fetchrow(
            "UPDATE video_productions SET status='canceled', "
            "error='canceled by user', updated_at=now(), completed_at=now() "
            "WHERE id=$1 AND status IN "
            "('queued','planning','rendering_clips','assembling') "
            "RETURNING id",
            production_id,
        )
        if row is not None:
            return {"ok": True, "id": str(production_id), "status": "canceled"}
        cur = await conn.fetchval(
            "SELECT status FROM video_productions WHERE id=$1", production_id
        )
    return {
        "ok": False, "id": str(production_id), "status": cur,
        "reason": "not found" if cur is None else "already finished",
    }


async def trim_production(
    production_id: UUID, start_s: float, end_s: float,
    tenant_id: UUID | None = None,
) -> dict:
    """Trim a FINISHED render to the window [start_s, end_s], re-host the result,
    and point both the production (final_url) and its queued action (media_url)
    at the trimmed video. Returns {ok, url, duration} or {ok:False, reason}.
    Non-destructive to the original bytes — it writes a new file; the row simply
    references the trimmed one now."""
    import tempfile
    from pathlib import Path

    from .audio_trim import probe_duration, trim_video

    prod = await get_production(production_id, tenant_id)
    if prod is None:
        return {"ok": False, "reason": "production not found"}
    url = (prod.get("final_url") or "").strip()
    if not url.startswith("http"):
        return {"ok": False, "reason": "no rendered video to trim"}
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(240.0, connect=10.0)) as c:
            r = await c.get(url, follow_redirects=True)
            r.raise_for_status()
            data = r.content
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "reason": f"download failed: {e}"}

    with tempfile.TemporaryDirectory() as td:
        ip, op = f"{td}/in.mp4", f"{td}/out.mp4"
        Path(ip).write_bytes(data)
        total = await probe_duration(ip)
        s = max(0.0, float(start_s or 0.0))
        e = float(end_s) if end_s and float(end_s) > 0 else (total or 0.0)
        if total and total > 0:
            e = min(e, total)
        if e - s < 0.5:
            return {"ok": False, "reason": "trim window too short (min 0.5s)"}
        if not await trim_video(ip, op, s, e):
            return {"ok": False, "reason": "ffmpeg trim failed"}
        out_bytes = Path(op).read_bytes()
    new_dur = round(e - s, 2)
    tenant = str(tenant_id or settings.default_tenant_id)
    new_url, _ = await asyncio.to_thread(
        media_storage().save, tenant, out_bytes,
        f"trim-{production_id}-{int(s * 10)}-{int(e * 10)}.mp4",
    )
    async with acquire(tenant_id) as conn:
        await conn.execute(
            "UPDATE video_productions SET final_url=$2, updated_at=now() WHERE id=$1",
            production_id, new_url,
        )
        aid = prod.get("queued_action_id")
        if aid:
            # Point the queued/approved item at the trimmed video too.
            await conn.execute(
                "UPDATE actions SET payload = payload || $2::jsonb WHERE id=$1",
                UUID(str(aid)),
                json.dumps({"media_url": new_url, "video_url": new_url}),
            )
    return {"ok": True, "url": new_url, "duration": new_dur}


async def delete_production(production_id: UUID, tenant_id: UUID | None = None) -> bool:
    """Hard-delete a production row. Returns True if a row was removed (RLS via
    acquire scopes it to the caller's tenant)."""
    async with acquire(tenant_id) as conn:
        status = await conn.execute(
            "DELETE FROM video_productions WHERE id=$1", production_id
        )
    return status.rsplit(" ", 1)[-1] != "0"


__all__ = [
    "start_production", "run_production", "list_productions", "get_production",
    "delete_production",
]
