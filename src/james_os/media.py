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
import re
from pathlib import Path
from uuid import UUID, uuid4

from .db import acquire

ROLES = ("style_reference", "james_clip", "broll")

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

    def delete(self, file_path: str | None) -> None:
        if not file_path:
            return
        try:
            p = Path(file_path)
            if p.is_file() and self.root in p.resolve().parents:
                p.unlink()
        except OSError:
            pass


_storage = MediaStorage()


def storage() -> MediaStorage:
    return _storage


def media_root() -> Path:
    _MEDIA_ROOT.mkdir(parents=True, exist_ok=True)
    return _MEDIA_ROOT


def _row(r) -> dict:
    d = dict(r)
    d["id"] = str(d["id"])
    d.pop("tenant_id", None)
    d.pop("file_path", None)  # internal; never exposed
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
    if not sets:
        return None
    sets.append("updated_at = now()")
    args.append(media_id)
    sql = f"UPDATE media_assets SET {', '.join(sets)} WHERE id = ${len(args)} RETURNING *"
    async with acquire(tenant_id) as conn:
        r = await conn.fetchrow(sql, *args)
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
    "update_media", "delete_media",
]
