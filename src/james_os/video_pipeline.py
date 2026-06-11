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
from .video import get_video_provider, provider_for
from .video_feedback import video_avoid_block
from .video_plan import generate_scene_plan

_POLL_EVERY = 5.0
_MAX_POLLS = 60  # ~5 min ceiling per async stage

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
    caption_style: str = "",
    image_style: str = "",
    music_mood: str = "",
    logo_position: str = "",
    structure: list[dict] | None = None,
    template_id: UUID | None = None,
    video_engine: str = "",
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
                  avatar_provider, broll_provider, assembly_provider)
               VALUES ('queued',$1,$2,$3,$4,$5::jsonb,$6,$7,$8,$9,$10,$11::jsonb,$12,$13,
                       $14,$15,$16) RETURNING *""",
            title, platform, aspect, script, json.dumps(scenes or []), mode,
            caption_style or "", image_style or "",
            music_mood or "", logo_position or "", json.dumps(structure or []), template_id,
            video_engine or "",
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


async def _render_hero_talking_photo(
    script: str, aspect: str, tenant_id: UUID | None, *, captions: bool = True
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
    for _ in range(_MAX_POLLS):
        p = await prov.poll(sub.job_id)
        if p.status == "succeeded":
            return p.url, ""
        if p.status == "failed":
            return None, p.error or "talking-photo render failed"
        await asyncio.sleep(_POLL_EVERY)
    return None, "talking-photo render timed out"


async def _seed_to_public_url(data_uri: str, tenant_id: UUID | None) -> tuple[str | None, str]:
    """Some engines (Higgsfield) fetch the seed image by URL, not a data: URI.
    Upload the still to storage and return its public URL. Requires Supabase
    storage in prod (a local /media-files path isn't fetchable by the engine)."""
    try:
        import base64
        from .media import storage as _media_storage
        raw = base64.b64decode(data_uri.split(",", 1)[1])
        tenant = str(tenant_id or settings.default_tenant_id)
        served_uri, _path = _media_storage().save(tenant, raw, "broll-seed.png")
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
    for _ in range(_MAX_POLLS):
        p = await vid.poll(sub.provider_job_id)
        if p.status == "succeeded":
            return p.result_url, ""
        if p.status == "failed":
            return None, p.error or f"{vid.name} render failed"
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
        script, row["aspect"], tenant_id, captions=True
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
               WHERE id=$1""",
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
    avatar_url, err = await _render_avatar(script, row["aspect"], captions=False)
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
               WHERE id=$1""",
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
    avatar_url, err = await _render_avatar(script, row["aspect"], captions=False)
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
               WHERE id=$1""",
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
    # split_horizontal pins the speaker into a SQUARE-ish top panel. A 9:16
    # portrait avatar cover-cropped into that panel loses the top of the head
    # (the face-cut the user flagged) — only the middle vertical band survives.
    # A 16:9 landscape avatar keeps the FULL face height and only trims the empty
    # sides, so render the split speaker landscape.
    avatar_aspect = "16:9" if composition == "split_horizontal" else row["aspect"]
    avatar_url, err = await _render_avatar(script, avatar_aspect, captions=False)
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
    res = await getattr(asm, render_method)(
        avatar_video_url=assets.avatar_video_url,
        audio_duration=assets.audio_duration,
        inserts=inserts_to_dict(assets.inserts),
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
               WHERE id=$1""",
            pid, res.url, action_id,
        )


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
    meta = plan[0]
    source_url = (meta.get("source_url") or "").strip()
    drive_file_id = (meta.get("drive_file_id") or "").strip()
    try:
        start_s = float(meta["start_s"])
        end_s = float(meta["end_s"])
    except (KeyError, TypeError, ValueError):
        return await _fail(pid, "candidate window malformed", tenant_id)
    if end_s <= start_s:
        return await _fail(pid, "candidate window malformed", tenant_id)
    # Drive source-of-truth path needs only drive_file_id; legacy
    # Supabase-backed sources still need a usable source_url.
    if not drive_file_id and not source_url.startswith("http"):
        return await _fail(pid, "candidate window malformed", tenant_id)

    # ── 1) Cut the source ────────────────────────────────────────
    async with acquire(tenant_id) as conn:
        await _set(conn, pid, status="planning")

    with tempfile.TemporaryDirectory() as td:
        src_path = f"{td}/source.mp4"
        out_path = f"{td}/cut.mp4"
        # Prefer Drive when drive_file_id is set on the row — re-fetch
        # the original from the service account every time (fast,
        # free, no Supabase size cap). Falls back to source_url for
        # the legacy upload path.
        if drive_file_id:
            from .drive import fetch_drive_file_to_path, DriveNotConfigured
            try:
                await fetch_drive_file_to_path(drive_file_id, src_path)
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
                    async with c.stream("GET", source_url) as r:
                        r.raise_for_status()
                        with open(src_path, "wb") as fh:
                            async for chunk in r.aiter_bytes(chunk_size=1 << 20):
                                fh.write(chunk)
            except Exception as e:  # noqa: BLE001
                return await _fail(pid, f"could not fetch source: {e}", tenant_id)

        # Compact slicer (CRF 26 + 96k mono + height-capped) keeps the
        # working artifact under Supabase Storage's service-tier size
        # cap (HTTP 413 kicks in around 50-100 MB). Talking-head
        # footage compresses well so the quality drop is minor — and
        # Creatomate re-encodes for the final reel anyway.
        if not await slice_video_compact(src_path, out_path, start_s, end_s):
            return await _fail(pid, "ffmpeg cut failed", tenant_id)

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
    brand_context = (
        f"Source: long-form podcast / interview. "
        f"Window: [{start_s:.1f}s – {end_s:.1f}s]. "
        f"Hook: {(meta.get('hook_quote') or '')[:200]}. "
        f"Platform: {row['platform']}. Aspect: {row['aspect']}."
    )
    try:
        istyle = (row["image_style"] or "").strip()
    except (KeyError, TypeError):
        istyle = ""
    if not istyle:
        istyle = "cinematic"

    assets = await build_engaging_avatar_assets(
        avatar_video_url=cut_url,
        aspect=row["aspect"],
        style=istyle,
        brand_context=brand_context,
        platform=row["platform"],
        tenant_id=str(tenant_id) if tenant_id else None,
        broll_avoid=broll_avoid,
        engine=(row["video_engine"] or ""),   # Runway / Higgsfield for B-roll
    )
    if assets.error:
        return await _fail(pid, assets.error, tenant_id)

    async with acquire(tenant_id) as conn:
        # Preserve the candidate meta on element 0 and append the
        # rendered insert metadata after, so the UI can show both.
        merged_scenes = [meta, *inserts_to_dict(assets.inserts)]
        await _set(
            conn, pid, status="assembling",
            scenes=json.dumps(merged_scenes),
        )

    try:
        cstyle = (row["caption_style"] or "").strip()
    except (KeyError, TypeError):
        cstyle = ""
    if not cstyle:
        cstyle, _why = await pick_caption_style(
            assets.audio_url, row["platform"], brand_context, avoid=cap_avoid,
        )

    asm = get_assembly_provider()
    if not hasattr(asm, "render_engaging_avatar"):
        return await _fail(
            pid, "assembly provider does not support long_form_reel",
            tenant_id,
        )
    res = await asm.render_engaging_avatar(
        avatar_video_url=assets.avatar_video_url,
        audio_duration=assets.audio_duration,
        inserts=inserts_to_dict(assets.inserts),
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
        return await _fail(pid, res.error or "long_form_reel assembly failed", tenant_id)

    async with acquire(tenant_id) as conn:
        action_id = await conn.fetchval(
            """INSERT INTO actions (proposed_by, action_type, payload, status)
               VALUES ('video_producer','video',$1::jsonb,'pending') RETURNING id""",
            json.dumps({
                "platform": row["platform"], "format": "video",
                "content": row["title"] or meta.get("hook_quote", "")[:120],
                "caption": meta.get("hook_quote", "")[:160],
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
               WHERE id=$1""",
            pid, res.url, action_id,
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
