"""Reference / media library.

Holds the video assets the pipeline draws on:

  * style_reference — examples to replicate (the "build me videos like this")
  * james_clip      — James's real talking-head footage, insertable per scene
  * broll           — supplemental footage

Files are either uploaded (stored by `MediaStorage`) or linked by URL. The
storage layer is abstracted so today's local-disk store can be swapped for
Supabase Storage / S3 without touching callers — the only thing that changes
is where bytes live and what public URI is returned.

Honest scope: local-disk storage is a dev-grade default (the API process
owns the files). For a multi-machine / sellable deployment this becomes
object storage; that swap is isolated to `MediaStorage`.
"""

import json
import os
import re
from pathlib import Path
from uuid import UUID, uuid4

from .config import settings
from .db import acquire

ROLES = (
    "style_reference",
    "james_clip",
    "broll",
    "post_image",
    # Hero references — used by the story modes so AI image generation
    # can reference the brand's hero as a recurring visual character
    # across beats. Photos describe appearance; videos can be sampled
    # for hero clips. See get_hero_context() in story_video.py.
    "hero_photo",
    "hero_video",
)

# Local store lives beside the repo; served read-only at /media-files.
_MEDIA_ROOT = Path(__file__).resolve().parent.parent.parent / "media_store"
_SERVE_PREFIX = "/media-files"
_EXT_RE = re.compile(r"[^a-zA-Z0-9.]+")


class MediaStorage:
    """Local-disk storage. Returns a served path under _SERVE_PREFIX.

    Swap target: a SupabaseStorage / S3 implementation with the same
    save()/delete() shape — callers and the DB schema stay identical.
    """

    def __init__(self, root: Path = _MEDIA_ROOT):
        self.root = root

    def save(self, tenant: str, data: bytes, filename: str) -> tuple[str, str]:
        """Persist bytes; return (served_uri, absolute_file_path)."""
        ext = ""
        if "." in filename:
            ext = "." + _EXT_RE.sub("", filename.rsplit(".", 1)[-1])[:8]
        name = f"{uuid4().hex}{ext}"
        tenant_dir = self.root / tenant
        tenant_dir.mkdir(parents=True, exist_ok=True)
        path = tenant_dir / name
        path.write_bytes(data)
        return f"{_SERVE_PREFIX}/{tenant}/{name}", str(path)

    def save_from_path(
        self, tenant: str, src_path: str, filename: str | None = None,
    ) -> tuple[str, str]:
        """Streaming variant — moves/copies a file on disk into the
        store without loading it into memory. Mirrors SupabaseStorage's
        save_from_path so the long_form ingest can call the same method
        regardless of backend.
        """
        import shutil
        name = filename or os.path.basename(src_path) or "upload.bin"
        ext = ""
        if "." in name:
            ext = "." + _EXT_RE.sub("", name.rsplit(".", 1)[-1])[:8]
        out_name = f"{uuid4().hex}{ext}"
        tenant_dir = self.root / tenant
        tenant_dir.mkdir(parents=True, exist_ok=True)
        out_path = tenant_dir / out_name
        # copyfile streams in 16 KB chunks under the hood — never loads
        # the whole file into RAM regardless of source size.
        shutil.copyfile(src_path, out_path)
        return f"{_SERVE_PREFIX}/{tenant}/{out_name}", str(out_path)

    def delete(self, file_path: str | None) -> None:
        if not file_path:
            return
        try:
            p = Path(file_path)
            if p.is_file() and self.root in p.resolve().parents:
                p.unlink()
        except OSError:
            pass


_storage: object | None = None


def _make_storage():
    """Pick the storage backend at runtime. Supabase Storage wins when its
    key is set (returns public URLs Creatomate can fetch); local-disk is
    the honest fallback for dev / no-key environments."""
    backend = (settings.media_storage or "local").lower()
    has_key = bool((settings.supabase_service_key or "").strip())
    if backend == "supabase" or (backend == "local" and has_key):
        from .storage_supabase import (
            SupabaseMediaStorage,
            SupabaseStorageError,
            derive_supabase_url_from_db,
        )
        url = (settings.supabase_url or "").strip() or \
              derive_supabase_url_from_db(settings.database_url)
        if url and has_key:
            try:
                return SupabaseMediaStorage(url=url, key=settings.supabase_service_key,
                                            bucket=settings.supabase_media_bucket)
            except SupabaseStorageError:
                pass  # fall through to local — honest, never fake
    return MediaStorage()


def storage():
    global _storage
    if _storage is None:
        _storage = _make_storage()
    return _storage


def media_root() -> Path:
    _MEDIA_ROOT.mkdir(parents=True, exist_ok=True)
    return _MEDIA_ROOT


def _row(r) -> dict:
    d = dict(r)
    d["id"] = str(d["id"])
    d.pop("tenant_id", None)
    d.pop("file_path", None)  # internal; never exposed
    if isinstance(d.get("analysis"), str):
        d["analysis"] = json.loads(d["analysis"])
    for k in ("created_at", "updated_at"):
        if d.get(k) is not None:
            d[k] = d[k].isoformat()
    return d


async def create_media(
    *,
    role: str,
    source_type: str,
    uri: str,
    file_path: str | None = None,
    title: str = "",
    platform: str = "",
    mime: str = "",
    tags: list[str] | None = None,
    notes: str = "",
    tenant_id: UUID | None = None,
) -> dict:
    if role not in ROLES:
        raise ValueError(f"role must be one of {ROLES}")
    async with acquire(tenant_id) as conn:
        r = await conn.fetchrow(
            """
            INSERT INTO media_assets
              (role, source_type, uri, file_path, title, platform, mime, tags, notes)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8::text[],$9)
            RETURNING *
            """,
            role, source_type, uri, file_path, title, platform, mime,
            tags or [], notes,
        )
    return _row(r)


async def list_media(role: str = "", tenant_id: UUID | None = None) -> list[dict]:
    if role:
        sql = "SELECT * FROM media_assets WHERE role = $1 ORDER BY created_at DESC"
        args = [role]
    else:
        sql = "SELECT * FROM media_assets ORDER BY created_at DESC"
        args = []
    async with acquire(tenant_id) as conn:
        rows = await conn.fetch(sql, *args)
    return [_row(r) for r in rows]


async def update_media(
    media_id: UUID,
    *,
    title: str | None = None,
    notes: str | None = None,
    tags: list[str] | None = None,
    platform: str | None = None,
    mute_audio: bool | None = None,
    tenant_id: UUID | None = None,
) -> dict | None:
    sets, args = [], []
    for col, val in (("title", title), ("notes", notes), ("platform", platform)):
        if val is not None:
            args.append(val)
            sets.append(f"{col} = ${len(args)}")
    if tags is not None:
        args.append(tags)
        sets.append(f"tags = ${len(args)}::text[]")
    if mute_audio is not None:
        args.append(mute_audio)
        sets.append(f"mute_audio = ${len(args)}")
    if not sets:
        return None
    sets.append("updated_at = now()")
    args.append(media_id)
    sql = f"UPDATE media_assets SET {', '.join(sets)} WHERE id = ${len(args)} RETURNING *"
    async with acquire(tenant_id) as conn:
        r = await conn.fetchrow(sql, *args)
    return _row(r) if r else None


async def fetch_media_local(
    file_path: str | None, uri: str = ""
) -> tuple[str | None, str | None]:
    """Resolve a media asset to a LOCAL file path for ffmpeg-based analysis.

    Local-disk uploads already have a readable file_path. Supabase-backed
    uploads store file_path='supabase://…' (not on disk) — so we stream the
    public `uri` down to a temp file. Returns (local_path, tempdir_to_clean):
    the caller must rmtree tempdir when not None. Returns (None, None) when
    the asset can't be resolved (e.g. a non-downloadable external URL)."""
    if (
        file_path
        and not file_path.startswith("supabase://")
        and Path(file_path).is_file()
    ):
        return file_path, None
    url = (uri or "").strip()
    if not url.startswith("http"):
        return None, None  # external page URL (YouTube etc.) — not a media file
    import shutil
    import tempfile

    import httpx

    tmpdir = tempfile.mkdtemp(prefix="media_an_")
    base = url.split("?", 1)[0]
    ext = os.path.splitext(base)[1]
    if not ext or len(ext) > 6:
        ext = ".mp4"
    dst = os.path.join(tmpdir, f"ref{ext}")
    try:
        async with httpx.AsyncClient(timeout=180, follow_redirects=True) as client:
            async with client.stream("GET", url) as r:
                r.raise_for_status()
                with open(dst, "wb") as fh:
                    async for chunk in r.aiter_bytes(1 << 16):
                        fh.write(chunk)
    except Exception:  # noqa: BLE001 — surface as unsupported, never fake
        shutil.rmtree(tmpdir, ignore_errors=True)
        return None, None
    if not os.path.isfile(dst) or os.path.getsize(dst) == 0:
        shutil.rmtree(tmpdir, ignore_errors=True)
        return None, None
    return dst, tmpdir


async def get_media_for_analysis(
    media_id: UUID, tenant_id: UUID | None = None
) -> dict | None:
    """Returns {role, source_type, file_path, uri} for the analyzer, or None."""
    async with acquire(tenant_id) as conn:
        r = await conn.fetchrow(
            "SELECT role, source_type, file_path, uri FROM media_assets WHERE id = $1",
            media_id,
        )
    return dict(r) if r else None


async def james_clips_with_mute(tenant_id: UUID | None = None) -> list[dict]:
    """Returns [{uri, mute_audio}] for every james_clip — used at assembly
    time so we know which clips' native voice to mute."""
    async with acquire(tenant_id) as conn:
        rows = await conn.fetch(
            "SELECT uri, mute_audio FROM media_assets "
            "WHERE role = 'james_clip' ORDER BY created_at"
        )
    return [{"uri": r["uri"], "mute_audio": bool(r["mute_audio"])} for r in rows]


async def set_analysis_status(
    media_id: UUID, status: str, tenant_id: UUID | None = None
) -> None:
    async with acquire(tenant_id) as conn:
        await conn.execute(
            "UPDATE media_assets SET analysis_status = $2, updated_at = now() WHERE id = $1",
            media_id, status,
        )


async def save_analysis(
    media_id: UUID,
    *,
    status: str,
    transcript: str = "",
    analysis: dict | None = None,
    notes: str = "",
    duration: int = 0,
    tenant_id: UUID | None = None,
) -> dict | None:
    """Persist a perception result. Only overwrites notes when the user
    hasn't written their own (don't clobber human style notes)."""
    async with acquire(tenant_id) as conn:
        r = await conn.fetchrow(
            """
            UPDATE media_assets SET
              analysis = $2::jsonb,
              analyzed = $3,
              analysis_status = $4,
              transcript = CASE WHEN $5 <> '' THEN $5 ELSE transcript END,
              notes = CASE WHEN notes = '' AND $6 <> '' THEN $6 ELSE notes END,
              duration = CASE WHEN duration = 0 AND $7 > 0 THEN $7 ELSE duration END,
              updated_at = now()
            WHERE id = $1
            RETURNING *
            """,
            media_id, json.dumps(analysis or {}), status == "done", status,
            transcript, notes, duration,
        )
    return _row(r) if r else None


async def delete_media(media_id: UUID, tenant_id: UUID | None = None) -> bool:
    async with acquire(tenant_id) as conn:
        r = await conn.fetchrow(
            "DELETE FROM media_assets WHERE id = $1 RETURNING file_path", media_id
        )
    if r is None:
        return False
    _storage.delete(r["file_path"])
    return True


__all__ = [
    "ROLES", "storage", "media_root", "create_media", "list_media",
    "update_media", "delete_media", "get_media_for_analysis",
    "save_analysis", "set_analysis_status", "fetch_media_local",
]
