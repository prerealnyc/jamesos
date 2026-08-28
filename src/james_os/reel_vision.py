"""What an uploaded asset SHOWS, written down so it can be matched.

The placer's first rung — cut away to the user's own B-roll when it fits what
is being said — is dead without this. Assets arrive as `IMG_4821.mov` with an
empty `notes` field, and a filename carries no meaning: `_placement_assets`
reads `notes` as an asset's description, so an undescribed asset never matches
anything and every moment falls through to words.

This writes that description. One vision call per asset, for images and for
video (sampled frames), producing text aimed at ONE job: being semantically
close to a spoken sentence about the same thing. That is a different job from
`design_eye`, which grades a still's craft, and from `design_inspector`, which
reverse-engineers a style. Here the only question is *what is in the picture* —
concrete subject, setting, action, objects — because that is what a transcript
phrase can be similar to.

Two rules make it safe to run over a whole library:

  * a failure NEVER writes a description. A guessed description is worse than
    none: none means the asset is skipped, a wrong one means a cutaway that
    contradicts the sentence under it.
  * a HUMAN's note is never overwritten. We tag what we wrote (`auto-described`)
    so a re-run refreshes our own text and leaves theirs alone.
"""

import asyncio
import base64
import json
import shutil
import tempfile
from pathlib import Path
from uuid import UUID

from .config import settings

# Marks a description this module wrote, so a re-run can refresh its own work
# without clobbering something a person typed.
DESCRIBE_TAG = "auto-described"
# Roles worth describing: the ones the placer draws from.
DESCRIBABLE_ROLES = ("broll", "hero_photo", "hero_video")

_MAX_FRAMES = 3          # enough to tell what a clip is; more is just tokens
_MAX_DESC_CHARS = 320
_MAX_TAGS = 6

_SYSTEM = (
    "You describe stock footage and photographs so they can be matched to what "
    "a speaker is talking about.\n\n"
    "Write what is literally IN the picture: the subject, the setting, the "
    "action, and the concrete objects. Name things a person would say out loud "
    "— 'waterfront commercial lot', 'construction crew pouring a foundation', "
    "'spreadsheet of pricing on a laptop'.\n\n"
    "Do NOT describe the craft (lighting, grade, lens, composition, mood) — "
    "that is not what anyone says in a sentence, so it cannot help a match. "
    "Do not speculate about who someone is or where it was shot. If the frames "
    "are unclear, say so plainly instead of inventing detail.\n\n"
    'Return STRICT JSON: {"description": "<one or two sentences of concrete '
    'subject matter>", "tags": ["<3-6 short noun keywords>"], '
    '"unclear": <true if the frames do not show anything identifiable>}'
)


class VisionError(RuntimeError):
    """Raised with a message meant for a person, not a stack trace."""


async def _run(cmd: list[str], timeout: int = 120) -> int:
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
    try:
        await asyncio.wait_for(proc.wait(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        return 1
    return proc.returncode or 0


async def _frames_from_video(src: Path, outdir: Path) -> list[Path]:
    """A few evenly-spread frames. Sampling across the clip rather than taking
    the first frame matters: a lot of footage opens on a blank or a slate."""
    rc = await _run([
        "ffmpeg", "-y", "-i", str(src),
        "-vf", f"thumbnail,fps=1/2,scale=768:-1",
        "-frames:v", str(_MAX_FRAMES), "-q:v", "4",
        str(outdir / "f_%02d.jpg"),
    ], timeout=180)
    if rc != 0:
        return []
    return sorted(outdir.glob("f_*.jpg"))[:_MAX_FRAMES]


def _as_data_url(p: Path) -> str:
    return "data:image/jpeg;base64," + base64.b64encode(p.read_bytes()).decode()


async def describe_file(path: Path, kind: str = "video") -> dict:
    """One local file → {description, tags}. Raises VisionError on every
    failure path so a caller can never mistake a failure for an empty result."""
    if not settings.openai_api_key:
        raise VisionError("OPENAI_API_KEY is not set — asset description unavailable")
    if not path.is_file():
        raise VisionError("the asset file could not be read")

    tmpdir: str | None = None
    try:
        if kind == "image":
            images = [path]
        else:
            tmpdir = tempfile.mkdtemp(prefix="jos-vis-")
            images = await _frames_from_video(path, Path(tmpdir))
            if not images:
                raise VisionError("could not read any frames from that clip")

        content: list[dict] = [{
            "type": "text",
            "text": ("Describe what these frames show, for matching against "
                     "spoken sentences." if kind != "image"
                     else "Describe what this image shows, for matching against "
                          "spoken sentences."),
        }]
        for img in images:
            content.append({
                "type": "image_url",
                # 'low' detail is right here: we need the subject, not the
                # craft, and this runs over a whole library.
                "image_url": {"url": _as_data_url(img), "detail": "low"},
            })

        from openai import AsyncOpenAI
        client = AsyncOpenAI(api_key=settings.openai_api_key)
        try:
            res = await client.chat.completions.create(
                model=settings.llm_model if "gpt-4o" in settings.llm_model else "gpt-4o",
                messages=[{"role": "system", "content": _SYSTEM},
                          {"role": "user", "content": content}],
                response_format={"type": "json_object"},
                max_tokens=300, temperature=0.0,
            )
        except Exception as e:  # noqa: BLE001
            raise VisionError(f"vision call failed: {type(e).__name__}: {e}") from e

        raw = (res.choices[0].message.content or "").strip()
        try:
            out = json.loads(raw)
        except ValueError as e:
            raise VisionError("vision returned unreadable JSON") from e
    finally:
        if tmpdir:
            shutil.rmtree(tmpdir, ignore_errors=True)

    if out.get("unclear"):
        # Honest refusal. An asset with no description is skipped by the placer;
        # an invented one produces a cutaway that contradicts the voiceover.
        raise VisionError("the frames don't show anything identifiable")

    desc = str(out.get("description") or "").strip()[:_MAX_DESC_CHARS]
    if not desc:
        raise VisionError("vision returned no description")
    tags = [
        str(t).strip().lower()[:40]
        for t in (out.get("tags") or []) if str(t).strip()
    ][:_MAX_TAGS]
    return {"description": desc, "tags": tags}


async def _load_asset(media_id: UUID, tenant_id: UUID | None) -> dict | None:
    """The columns this module actually needs.

    NOT `media.get_media_for_analysis` — that returns only
    {role, source_type, file_path, uri}. Reading `notes`/`tags`/`mime` off it
    silently yields empty values, which would mean: a human's description looks
    absent and gets clobbered, the sha256 dedup tag looks absent and gets
    dropped, and every image looks like a video and goes through frame
    extraction. All three were live bugs until the tests caught them.
    """
    from .db import acquire
    async with acquire(tenant_id) as conn:
        r = await conn.fetchrow(
            "SELECT role, source_type, file_path, uri, mime, notes, tags "
            "FROM media_assets WHERE id = $1",
            media_id,
        )
    return dict(r) if r else None


def _may_overwrite(asset: dict) -> bool:
    """Ours to refresh, or a person's to leave alone."""
    notes = (asset.get("notes") or "").strip()
    if not notes:
        return True
    return DESCRIBE_TAG in (asset.get("tags") or [])


async def describe_media_asset(
    media_id: UUID, tenant_id: UUID | None = None, *, force: bool = False
) -> dict:
    """Describe one library asset and write it back to `notes` + `tags`.

    Returns {"status": "described"|"skipped"|"failed", ...} — never raises, so a
    batch over a library can't be killed by one bad file."""
    from .media import fetch_media_local, update_media

    asset = await _load_asset(media_id, tenant_id)
    if asset is None:
        return {"status": "failed", "error": "asset not found"}
    if not force and not _may_overwrite(asset):
        return {"status": "skipped", "reason": "a human wrote this description",
                "id": str(media_id)}

    local, tmpdir = await fetch_media_local(asset.get("file_path"), asset.get("uri", ""))
    if not local:
        return {"status": "failed", "error": "could not fetch the asset's file",
                "id": str(media_id)}
    mime = (asset.get("mime") or "").lower()
    kind = "image" if mime.startswith("image/") else "video"
    try:
        got = await describe_file(Path(local), kind)
    except VisionError as e:
        return {"status": "failed", "error": str(e), "id": str(media_id)}
    except Exception as e:  # noqa: BLE001
        return {"status": "failed", "error": f"{type(e).__name__}: {e}", "id": str(media_id)}
    finally:
        if tmpdir:
            shutil.rmtree(tmpdir, ignore_errors=True)

    # Keep the content hash and any human tags; add ours.
    tags = [t for t in (asset.get("tags") or []) if t != DESCRIBE_TAG]
    for t in got["tags"]:
        if t not in tags:
            tags.append(t)
    tags.append(DESCRIBE_TAG)

    await update_media(media_id, notes=got["description"], tags=tags, tenant_id=tenant_id)
    return {"status": "described", "id": str(media_id),
            "description": got["description"], "tags": got["tags"]}


async def describe_pending(
    role: str = "", tenant_id: UUID | None = None, limit: int = 25
) -> dict:
    """Describe every asset in the placer's roles that hasn't got a description.

    Bounded by `limit` because this is one vision call per asset — and the cap
    is REPORTED, so a partial pass never reads as a complete one."""
    from .media import list_media

    roles = [role] if role else list(DESCRIBABLE_ROLES)
    pending: list[dict] = []
    for r in roles:
        try:
            for a in await list_media(r, tenant_id):
                if not (a.get("notes") or "").strip():
                    pending.append(a)
        except Exception:  # noqa: BLE001 — an unreadable role isn't fatal
            continue

    total = len(pending)
    batch = pending[:max(0, limit)]
    described, failed, skipped = [], [], []
    for a in batch:
        r = await describe_media_asset(UUID(str(a["id"])), tenant_id)
        if r["status"] == "described":
            described.append({"id": r["id"], "description": r["description"]})
        elif r["status"] == "skipped":
            skipped.append(r["id"])
        else:
            failed.append({"id": r.get("id", ""), "error": r.get("error", "")})

    return {
        "described": described, "failed": failed, "skipped": skipped,
        "pending_total": total,
        "not_attempted": max(0, total - len(batch)),
        "limit": limit,
    }


__all__ = [
    "describe_file", "describe_media_asset", "describe_pending",
    "VisionError", "DESCRIBE_TAG", "DESCRIBABLE_ROLES",
]
