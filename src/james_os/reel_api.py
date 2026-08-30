"""Reel editing API — music-bed extraction and the card plan.

Two endpoints families, both under paths the auth middleware already allows for
a service key (`/video/...`):

  * `/video/music/*` — pull the instrumental bed out of a clip with Demucs and
    save it into the brand's Audio Library, where it becomes a reusable asset
    like any uploaded track.
  * `/video/cards/*` — transcribe a clip and return the card plan the director
    WOULD place, without rendering anything. That separation is deliberate: the
    plan is cheap and inspectable, so the decision layer can be judged before a
    single render credit is spent on it.

Both are background jobs. Separation runs for tens of seconds on CPU and
transcription is a network round-trip against the whole audio track — either
one inline would blow the gateway timeout, the same trap `_gather_intel` hit.

Job stores are in-memory and follow the lessons already paid for elsewhere in
this codebase: capture the tenant BEFORE `add_task`, hold a local reference to
the job dict rather than re-indexing it, and only ever prune FINISHED entries so
a running job can't be evicted out from under its own writer.
"""

import asyncio
import uuid
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, File, Form, HTTPException, UploadFile
from pydantic import BaseModel

from .config import settings

router = APIRouter()

_MUSIC_JOBS: dict[str, dict] = {}
_CARD_JOBS: dict[str, dict] = {}
_MAX_JOBS = 40
_FINISHED = ("succeeded", "failed")


def _tenant():
    try:
        from .db import _request_tenant
        t = _request_tenant.get()
    except (LookupError, ImportError):
        t = None
    return t or settings.default_tenant_id


def _new_job(store: dict, **fields) -> tuple[str, dict]:
    # Prune only FINISHED jobs — evicting a running one would KeyError its own
    # background writer.
    if len(store) > _MAX_JOBS:
        for k in [k for k, v in store.items() if v.get("status") in _FINISHED][:20]:
            store.pop(k, None)
    job_id = str(uuid.uuid4())
    job = {"status": "running", "error": "", **fields}
    store[job_id] = job
    return job_id, job


async def _source_file(media_id: str, upload: UploadFile | None, tenant) -> tuple[Path, object]:
    """Resolve either an uploaded file or a library asset to a local path."""
    if upload is not None:
        data = await upload.read()
        if not data:
            raise HTTPException(status_code=400, detail="empty file")
        import tempfile
        tmp = tempfile.mkdtemp(prefix="jos-reel-")
        path = Path(tmp) / (upload.filename or "clip.mp4")
        path.write_bytes(data)
        return path, tmp
    if not media_id:
        raise HTTPException(status_code=400, detail="provide a file or a media_id")
    from .media import fetch_media_local, get_media_for_analysis
    asset = await get_media_for_analysis(uuid.UUID(media_id), tenant)
    if asset is None:
        raise HTTPException(status_code=404, detail="media not found")
    local, tmpdir = await fetch_media_local(asset.get("file_path"), asset.get("uri", ""))
    if not local:
        raise HTTPException(status_code=400, detail="could not fetch that asset's file")
    return Path(local), tmpdir


# ── music extraction ─────────────────────────────────────────────────
# STATIC path first: /video/music/capability must not be read as a job id.

@router.get("/video/music/capability")
async def music_capability() -> dict:
    """Whether this deployment can separate audio at all. The UI asks first so
    it can say what's missing instead of queueing a job that cannot run."""
    from .audio_separate import MAX_SECONDS, available
    ok, why = available()
    return {"available": ok, "reason": why, "max_seconds": MAX_SECONDS}


async def _run_music_job(job: dict, src: Path, tmpdir, *, tenant,
                         mood: str, title: str) -> None:
    from .audio_separate import SeparationError, extract_instrumental
    try:
        data, meta = await extract_instrumental(src)
        from .media import create_media, storage
        served_uri, file_path = await asyncio.to_thread(
            storage().save, str(tenant), data, f"{title or 'extracted-bed'}.mp3"
        )
        asset = await create_media(
            role="music", source_type="upload", uri=served_uri,
            file_path=file_path, title=title or "Extracted bed",
            mime="audio/mpeg",
            # The mood tag is what `audio_library.resolve_music_url` matches on,
            # so tagging here is what makes the bed reusable by every render.
            tags=[mood] if mood else [],
            notes=f"instrumental separated with {meta['model']}",
            tenant_id=tenant,
        )
        job.update(status="succeeded", asset=asset, meta=meta)
    except SeparationError as e:
        job.update(status="failed", error=str(e))
    except Exception as e:  # noqa: BLE001
        job.update(status="failed", error=f"{type(e).__name__}: {e}")
    finally:
        if tmpdir:
            import shutil
            shutil.rmtree(tmpdir, ignore_errors=True)


@router.post("/video/music/extract", status_code=202)
async def music_extract(
    background: BackgroundTasks,
    file: UploadFile | None = File(default=None),
    media_id: str = Form(default=""),
    mood: str = Form(default=""),
    title: str = Form(default=""),
) -> dict:
    """Separate a clip's instrumental bed and save it to the Audio Library.

    Tag it with a mood and every render asking for that mood can use it — which
    is what makes an extracted bed a reusable brand asset rather than a one-off
    download."""
    from .audio_library import MUSIC_MOODS
    from .audio_separate import available

    ok, why = available()
    if not ok:
        raise HTTPException(status_code=503, detail=why)
    mood = (mood or "").strip().lower()
    if mood and mood not in MUSIC_MOODS:
        raise HTTPException(
            status_code=400,
            detail=f"mood must be one of {', '.join(MUSIC_MOODS)} (or empty)")

    tenant = _tenant()                       # captured BEFORE add_task
    src, tmpdir = await _source_file(media_id, file, tenant)
    job_id, job = _new_job(_MUSIC_JOBS, kind="music")
    background.add_task(_run_music_job, job, src, tmpdir,
                        tenant=tenant, mood=mood, title=(title or "").strip())
    return {"job_id": job_id, "status": "running",
            "note": "separation runs on CPU and takes tens of seconds — poll for it"}


@router.get("/video/music/extract/{job_id}")
async def music_extract_status(job_id: str) -> dict:
    job = _MUSIC_JOBS.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    return {"job_id": job_id, **job}


# ── the front door: one upload, one reel ─────────────────────────────
#
# Everything below already existed as separate steps: /long-form/upload (store
# + transcribe), then poll for "ready", then /long-form/{id}/render-whole. That
# is a fine API and a poor product — the user's action is "make me a reel", not
# five calls with a wait in the middle.
#
# This chains them behind ONE call, uploads the user's B-roll alongside, gets it
# described so the placer can actually match it, and reports a single progress
# line across the whole chain. The chain is the point: each step already worked
# on its own.

_REEL_JOBS: dict[str, dict] = {}

# One progress vocabulary for the whole chain, not just the render. The
# production's own `_progress` covers only the last stage; a user watching an
# upload transcribe needs to see that too, or the bar sits at 0 for a minute.
_REEL_STAGES = ("uploading", "transcribing", "describing", "planning",
                "rendering", "succeeded")
_REEL_LABEL = {
    "uploading": "Saving your video",
    "transcribing": "Listening to what you said",
    "describing": "Looking at your B-roll",
    "planning": "Choosing the moments to cut away",
    "rendering": "Cutting the reel",
    "succeeded": "Done",
    "failed": "Failed",
}
# Rough seconds per stage, for a bar that moves at a believable rate. Transcribe
# and render dominate; APPROX by design, same as `video_pipeline._progress`.
_REEL_TYPICAL_S = {"uploading": 6, "transcribing": 45, "describing": 20,
                   "planning": 25, "rendering": 240}


def _reel_progress(job: dict) -> dict:
    """One bar across upload → transcribe → describe → plan → render."""
    import time
    stage = job.get("stage") or "uploading"
    if job.get("status") == "failed":
        return {"stage": "failed", "label": _REEL_LABEL["failed"], "pct": 100}
    if job.get("status") == "succeeded":
        return {"stage": "succeeded", "label": _REEL_LABEL["succeeded"], "pct": 100}
    total = sum(_REEL_TYPICAL_S.values())
    order = [st for st in _REEL_STAGES if st in _REEL_TYPICAL_S]
    idx = order.index(stage) if stage in order else 0
    before = sum(_REEL_TYPICAL_S[st] for st in order[:idx])
    cur = _REEL_TYPICAL_S.get(stage, 30)
    in_stage = max(0.0, time.monotonic() - float(job.get("_stage_at") or 0))
    pct = max(1, min(99, round((before + min(in_stage, cur)) / total * 100)))
    return {"stage": stage, "label": _REEL_LABEL.get(stage, stage), "pct": pct,
            "stage_index": idx, "total_stages": len(order)}


def _stage(job: dict, stage: str) -> None:
    import time
    job["stage"] = stage
    job["_stage_at"] = time.monotonic()


async def _run_reel_job(job: dict, *, tenant, head_bytes: bytes, head_name: str,
                        broll: list[tuple[str, bytes]], title: str, platform: str,
                        aspect: str, caption_style: str, music_mood: str,
                        cards: bool) -> None:
    """Store → transcribe → describe B-roll → plan → render, as one job."""
    from .db import set_request_tenant
    set_request_tenant(str(tenant))
    try:
        # 1) the talking head
        _stage(job, "uploading")
        from .media import storage
        served_uri, _ = await asyncio.to_thread(
            storage().save, str(tenant), head_bytes, head_name or "reel.mp4")

        from .long_form import (
            create_source, create_whole_source_candidate,
            get_source_with_candidates, ingest_source, link_candidate_to_production,
        )
        src = await create_source(title=title or head_name, source_url=served_uri,
                                  tenant_id=tenant)
        job["source_id"] = src["id"]

        # 2) the user's B-roll, stored AND described. Describing is what makes it
        # matchable at all — an asset with no description can never be cut in.
        if broll:
            _stage(job, "describing")
            from .media import create_media
            from .reel_vision import describe_media_asset
            saved = []
            for name, data in broll:
                uri, path = await asyncio.to_thread(
                    storage().save, str(tenant), data, name or "broll.mp4")
                a = await create_media(
                    role="broll", source_type="upload", uri=uri, file_path=path,
                    title=name or "B-roll", mime="video/mp4", tenant_id=tenant)
                saved.append(a["id"])
            described = 0
            for aid in saved:
                r = await describe_media_asset(uuid.UUID(aid), tenant)
                if r.get("status") == "described":
                    described += 1
            job["broll"] = {"uploaded": len(saved), "described": described}

        # 3) transcribe the whole thing (the WHOLE upload is the reel — no
        #    candidate picking, per the product decision)
        _stage(job, "transcribing")
        await ingest_source(uuid.UUID(src["id"]), tenant)

        _stage(job, "planning")
        # Ingest records its own failure reason on the source row. Read it and
        # report THAT — an invented explanation ("no usable audio") sends the
        # user hunting the wrong problem when the truth was, say, that storage
        # handed back a path instead of a URL.
        full = await get_source_with_candidates(uuid.UUID(src["id"]), tenant)
        if (full or {}).get("status") == "failed":
            job.update(status="failed",
                       error=(full or {}).get("error") or "the video could not be ingested")
            return
        cand = await create_whole_source_candidate(uuid.UUID(src["id"]), tenant)
        if cand is None:
            job.update(
                status="failed",
                error=(full or {}).get("error")
                or "the video was stored but produced nothing to cut — it may "
                   "have no audio track, or ingest may not have finished")
            return
        payload = [{
            "source_id": cand["source_id"], "candidate_id": cand["id"],
            "source_url": (full or {}).get("source_url") or served_uri,
            "drive_file_id": (full or {}).get("drive_file_id") or "",
            "start_s": cand["start_s"], "end_s": cand["end_s"],
            "hook_quote": cand["hook_quote"], "summary": cand["summary"],
        }]

        # 4) render. `options` carries cards for THIS production, so the front
        #    door doesn't need a saved template first (migration 058).
        from .video_pipeline import run_production, start_production
        prod = await start_production(
            (cand["hook_quote"] or title or "Reel")[:200],
            platform, aspect,
            (title or cand["summary"] or "Reel")[:120],
            payload, "long_form_reel",
            caption_style, "",
            music_mood=music_mood,
            options={"cards": bool(cards)},
            tenant_id=tenant,
        )
        job["production_id"] = prod["id"]
        await link_candidate_to_production(
            uuid.UUID(cand["id"]), uuid.UUID(prod["id"]), tenant)

        _stage(job, "rendering")
        await run_production(uuid.UUID(prod["id"]), tenant)

        from .video_pipeline import get_production
        done = await get_production(uuid.UUID(prod["id"]), tenant)
        if (done or {}).get("status") == "succeeded":
            job.update(status="succeeded", final_url=(done or {}).get("final_url") or "")
        else:
            job.update(status="failed",
                       error=(done or {}).get("error") or "the render did not finish")
    except Exception as e:  # noqa: BLE001 — one job's failure is reported, not raised
        job.update(status="failed", error=f"{type(e).__name__}: {e}")


@router.post("/video/reel", status_code=202)
async def make_reel(
    background: BackgroundTasks,
    file: UploadFile = File(...),
    broll: list[UploadFile] = File(default=[]),
    title: str = Form(default=""),
    platform: str = Form(default="instagram"),
    aspect: str = Form(default="9:16"),
    caption_style: str = Form(default=""),
    music_mood: str = Form(default="calm"),
    cards: bool = Form(default=True),
) -> dict:
    """Upload a talking-head video (and optionally your B-roll) → get a reel.

    The WHOLE upload becomes the reel: no window picking, no trimming. B-roll is
    optional — without it the cutaways are designed cards built from your own
    words, which is the format working as intended rather than a degraded mode.
    """
    head = await file.read()
    if not head:
        raise HTTPException(status_code=400, detail="empty file")
    if not (file.content_type or "").startswith("video/"):
        raise HTTPException(
            status_code=400,
            detail="the main upload must be a video file (mp4/mov/webm)")

    extra: list[tuple[str, bytes]] = []
    for up in broll or []:
        data = await up.read()
        if data:
            extra.append((up.filename or "broll.mp4", data))

    tenant = _tenant()                       # captured BEFORE add_task
    job_id, job = _new_job(_REEL_JOBS, kind="reel", stage="uploading",
                           source_id="", production_id="", final_url="")
    _stage(job, "uploading")
    background.add_task(
        _run_reel_job, job, tenant=tenant, head_bytes=head,
        head_name=file.filename or "reel.mp4", broll=extra,
        title=title.strip(), platform=platform, aspect=aspect,
        caption_style=caption_style, music_mood=music_mood, cards=cards)
    return {"job_id": job_id, "status": "running",
            "progress": _reel_progress(job),
            "broll_received": len(extra)}


@router.get("/video/reel/{job_id}")
async def make_reel_status(job_id: str) -> dict:
    job = _REEL_JOBS.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    out = {k: v for k, v in job.items() if not k.startswith("_")}
    out["progress"] = _reel_progress(job)
    return {"job_id": job_id, **out}


# ── describing assets so they can be matched ─────────────────────────

_DESCRIBE_JOBS: dict[str, dict] = {}


async def _run_describe_job(job: dict, *, tenant, role: str, limit: int) -> None:
    from .db import set_request_tenant
    from .reel_vision import describe_pending
    try:
        set_request_tenant(str(tenant))
        job.update(status="succeeded", **await describe_pending(role, tenant, limit))
    except Exception as e:  # noqa: BLE001
        job.update(status="failed", error=f"{type(e).__name__}: {e}")


class DescribeRequest(BaseModel):
    role: str = ""          # '' = every role the placer draws from
    limit: int = 25


@router.post("/video/assets/describe", status_code=202)
async def assets_describe(req: DescribeRequest, background: BackgroundTasks) -> dict:
    """Write down what each undescribed B-roll / hero asset SHOWS.

    Nothing else makes the placer's first rung fire: assets arrive with an empty
    `notes` field, and that field is what the matcher reads. One vision call per
    asset, so it's a background job with a reported cap."""
    tenant = _tenant()                       # captured BEFORE add_task
    job_id, job = _new_job(_DESCRIBE_JOBS, kind="describe")
    background.add_task(_run_describe_job, job, tenant=tenant,
                        role=(req.role or "").strip(), limit=max(1, min(req.limit, 200)))
    return {"job_id": job_id, "status": "running"}


@router.get("/video/assets/describe/{job_id}")
async def assets_describe_status(job_id: str) -> dict:
    job = _DESCRIBE_JOBS.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    return {"job_id": job_id, **job}


@router.get("/video/assets/coverage")
async def assets_coverage() -> dict:
    """How much of the library is actually matchable. An undescribed asset is
    invisible to the placer, so this is the number that predicts whether a reel
    will use the user's own footage or fall back to words."""
    from .media import list_media
    from .reel_vision import DESCRIBABLE_ROLES

    tenant = _tenant()
    out: dict[str, dict] = {}
    for role in DESCRIBABLE_ROLES:
        try:
            rows = await list_media(role, tenant)
        except Exception:  # noqa: BLE001
            continue
        described = sum(1 for a in rows if (a.get("notes") or "").strip())
        out[role] = {"total": len(rows), "described": described,
                     "undescribed": len(rows) - described}
    return {"roles": out,
            "matchable": sum(v["described"] for v in out.values()),
            "undescribed": sum(v["undescribed"] for v in out.values())}


# ── the card plan ────────────────────────────────────────────────────

async def _run_card_job(job: dict, src: Path, tmpdir, *, tenant,
                        styles: list[str], brand_note: str) -> None:
    from .db import set_request_tenant
    try:
        set_request_tenant(str(tenant))
        from .reel_director import plan_cards
        from .reel_placer import coverage, plan_placements, plan_summary
        from .transcription import transcribe_words

        data = src.read_bytes()
        tr = await transcribe_words(src.name, data)
        cards = await plan_cards(
            tr.words, duration=tr.duration, brand_note=brand_note, styles=styles)

        # Run the full cascade, not just the moments — the review screen has to
        # show what will ACTUALLY fill each moment (the user's own clip, a hero
        # photo, or their words), because that's the part a human wants to
        # correct before spending a render.
        from .video_pipeline import _placement_assets
        user_assets, heroes = await _placement_assets(tenant)
        placements = await plan_placements(
            cards, assets=user_assets, hero_photos=heroes)

        job.update(
            status="succeeded",
            transcript=tr.text,
            duration=round(tr.duration, 2),
            words=len(tr.words),
            cards=plan_summary(placements),
            coverage=coverage(placements, tr.duration, user_assets),
            assets_available={"broll": len(user_assets), "hero_photos": len(heroes)},
        )
    except Exception as e:  # noqa: BLE001
        job.update(status="failed", error=f"{type(e).__name__}: {e}")
    finally:
        if tmpdir:
            import shutil
            shutil.rmtree(tmpdir, ignore_errors=True)


class CardPlanRequest(BaseModel):
    media_id: str = ""
    styles: list[str] = []
    brand_note: str = ""


@router.post("/video/cards/plan", status_code=202)
async def cards_plan(req: CardPlanRequest, background: BackgroundTasks) -> dict:
    """Transcribe a clip and return the cards the director WOULD place — where,
    how long, and in the speaker's own words. Renders nothing.

    This is the cheap way to judge the decision layer: read the plan, and only
    then decide whether it's worth a render."""
    tenant = _tenant()                       # captured BEFORE add_task
    src, tmpdir = await _source_file(req.media_id, None, tenant)
    job_id, job = _new_job(_CARD_JOBS, kind="cards")
    background.add_task(_run_card_job, job, src, tmpdir, tenant=tenant,
                        styles=list(req.styles or []), brand_note=req.brand_note)
    return {"job_id": job_id, "status": "running"}


@router.post("/video/cards/plan-upload", status_code=202)
async def cards_plan_upload(
    background: BackgroundTasks,
    file: UploadFile = File(...),
    brand_note: str = Form(default=""),
) -> dict:
    """Same as /video/cards/plan, for a clip that isn't in the library yet."""
    tenant = _tenant()
    src, tmpdir = await _source_file("", file, tenant)
    job_id, job = _new_job(_CARD_JOBS, kind="cards")
    background.add_task(_run_card_job, job, src, tmpdir, tenant=tenant,
                        styles=[], brand_note=brand_note)
    return {"job_id": job_id, "status": "running"}


@router.get("/video/cards/plan/{job_id}")
async def cards_plan_status(job_id: str) -> dict:
    job = _CARD_JOBS.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    return {"job_id": job_id, **job}


__all__ = ["router"]
