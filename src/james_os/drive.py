"""Google Drive auto-importer for James's real clips.

Pulls videos from a configured Drive folder, pushes the bytes through
MediaStorage (so they land in Supabase Storage with public URLs Creatomate
can fetch), and creates media_assets rows tagged with role='james_clip'.

Auth: service account JSON. The user creates one in GCP, shares the Drive
folder with the service account's email, and the importer runs headlessly
forever. No OAuth dance, no per-user tokens to refresh.

Idempotent: every imported asset is tagged with `drive:<file_id>`; a re-run
skips files that already exist.

Degrades honestly: with no service-account JSON path or no folder id,
endpoints return a clear "not configured" message — never a fake import.
"""

from __future__ import annotations

import asyncio
import io
from uuid import UUID

from .config import settings
from .media import create_media, list_media, storage as media_storage


_SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]


class DriveNotConfigured(RuntimeError):
    """Raised when service account or folder id is missing."""


def _service():
    """Build an authenticated Drive client. Imports inside so the SDK is
    optional — the rest of the app runs even if google-api isn't installed.

    GOOGLE_SERVICE_ACCOUNT_JSON can be either:
      * A filesystem path (local dev) — read as a file
      * Inline JSON starting with '{' (cloud deploys with no disk) —
        parsed directly
      * Base64-encoded JSON (when shell-escaping the JSON is painful)
    """
    import base64, json as _json
    from google.oauth2 import service_account
    from googleapiclient.discovery import build

    raw = (settings.google_service_account_json or "").strip()
    if not raw:
        raise DriveNotConfigured(
            "Set GOOGLE_SERVICE_ACCOUNT_JSON (path to the file, the "
            "inline JSON, or base64-encoded JSON) in .env or Settings."
        )

    info: dict | None = None
    if raw.startswith("{"):
        # Inline JSON.
        try:
            info = _json.loads(raw)
        except _json.JSONDecodeError as e:
            raise DriveNotConfigured(
                f"GOOGLE_SERVICE_ACCOUNT_JSON looks like JSON but didn't parse: {e}"
            ) from e
    elif not raw.startswith("/") and not raw.endswith(".json"):
        # Best-effort base64 decode (won't match a file path or JSON).
        try:
            decoded = base64.b64decode(raw, validate=True).decode("utf-8")
            if decoded.startswith("{"):
                info = _json.loads(decoded)
        except (ValueError, _json.JSONDecodeError):
            info = None

    if info is not None:
        creds = service_account.Credentials.from_service_account_info(info, scopes=_SCOPES)
    else:
        # Fall through to the file-path interpretation (local dev).
        creds = service_account.Credentials.from_service_account_file(raw, scopes=_SCOPES)
    return build("drive", "v3", credentials=creds, cache_discovery=False)


# Shared Drive support: every call needs supportsAllDrives=True; list also
# needs includeItemsFromAllDrives=True. Without these, items inside a Team
# Drive simply aren't returned (no error, silent zero) — which cost us an
# afternoon of diagnosis. Setting them is safe for My-Drive folders too.
_SHARED_DRIVE = {"supportsAllDrives": True}
_SHARED_DRIVE_LIST = {"supportsAllDrives": True, "includeItemsFromAllDrives": True}


def _list_videos_sync(folder_id: str, limit: int = 100) -> list[dict]:
    svc = _service()
    q = (
        f"'{folder_id}' in parents and trashed = false and "
        "(mimeType contains 'video/' or mimeType = 'application/octet-stream')"
    )
    res = svc.files().list(
        q=q,
        fields="files(id, name, mimeType, size, modifiedTime)",
        pageSize=min(limit, 1000),
        orderBy="modifiedTime desc",
        **_SHARED_DRIVE_LIST,
    ).execute()
    return res.get("files", [])


def _download_sync(file_id: str) -> bytes:
    from googleapiclient.http import MediaIoBaseDownload
    svc = _service()
    request = svc.files().get_media(fileId=file_id, **_SHARED_DRIVE)
    buf = io.BytesIO()
    downloader = MediaIoBaseDownload(buf, request)
    done = False
    while not done:
        _, done = downloader.next_chunk()
    return buf.getvalue()


def _file_metadata_sync(file_id: str) -> dict:
    """Pull name + size + mimeType for a single file id. Used by the
    URL-based importer so we can name the upload sensibly and reject
    non-video items before paying the download cost."""
    svc = _service()
    return svc.files().get(
        fileId=file_id,
        fields="id, name, mimeType, size",
        **_SHARED_DRIVE,
    ).execute()


# Drive sharable URL → file id. Covers every shape Google produces:
#   https://drive.google.com/file/d/<ID>/view?usp=sharing
#   https://drive.google.com/file/d/<ID>/edit
#   https://drive.google.com/open?id=<ID>
#   https://drive.google.com/uc?id=<ID>&export=download
#   https://docs.google.com/file/d/<ID>/preview
# ID is 25-44 chars of [A-Za-z0-9_-]. Reject anything shorter — a
# stray "id=42" in a tracking query string isn't a real file id.
import re as _re

_DRIVE_ID_RE = _re.compile(r"(?:/d/|[?&]id=)([A-Za-z0-9_-]{25,})")


def extract_drive_file_id(url: str) -> str | None:
    """Pull the file id out of any sharable Drive URL. Returns None
    when the URL doesn't contain a recognisable id — the caller
    surfaces a 400 to the user rather than guessing."""
    if not url:
        return None
    m = _DRIVE_ID_RE.search(url.strip())
    return m.group(1) if m else None


async def fetch_drive_file_by_url(url: str) -> tuple[bytes, str, str]:
    """Download a Drive file by sharable URL.

    Returns (bytes, filename, mime_type). Raises DriveNotConfigured
    when the service account isn't set, ValueError when the URL
    doesn't parse, or the underlying google-api exception when the
    file isn't accessible (the caller maps these to 4xx responses).

    Memory-loads the entire file. For large sources (>~200 MB) prefer
    fetch_drive_file_to_path which streams chunks to disk.
    """
    file_id = extract_drive_file_id(url)
    if not file_id:
        raise ValueError("could not parse a Drive file id from that URL")
    meta = await asyncio.to_thread(_file_metadata_sync, file_id)
    name = str(meta.get("name") or f"drive-{file_id}.mp4")
    mime = str(meta.get("mimeType") or "")
    data = await asyncio.to_thread(_download_sync, file_id)
    return data, name, mime


def _download_sync_to_path(file_id: str, out_path: str) -> None:
    """Stream a Drive file to disk in 16 MB chunks. Peak memory stays
    at one chunk regardless of source size — required for 3-4 GB
    podcast files where the bytes-into-RAM path of _download_sync
    would OOM a modest host.

    MediaIoBaseDownload accepts any io.IOBase subclass; passing the
    open file handle directly is what makes the streaming work.
    """
    from googleapiclient.http import MediaIoBaseDownload
    svc = _service()
    request = svc.files().get_media(fileId=file_id, **_SHARED_DRIVE)
    # 16 MB chunks balance overhead (fewer round-trips) against
    # progress granularity. For a 4 GB file that's ~256 chunks.
    chunksize = 16 * 1024 * 1024
    with open(out_path, "wb") as fh:
        downloader = MediaIoBaseDownload(fh, request, chunksize=chunksize)
        done = False
        while not done:
            _, done = downloader.next_chunk()


async def fetch_drive_file_to_path(
    file_id: str, out_path: str,
) -> tuple[str, str]:
    """Streaming download to a path on disk. Returns (name, mime_type).
    Caller owns the resulting file (delete after use).

    Use this for any Drive ingestion where the source could be >~200 MB
    (long-form podcast videos, multi-hour interviews). For small
    photos/clips, fetch_drive_file_by_url is fine.
    """
    if not file_id:
        raise ValueError("file_id is required")
    meta = await asyncio.to_thread(_file_metadata_sync, file_id)
    name = str(meta.get("name") or f"drive-{file_id}.mp4")
    mime = str(meta.get("mimeType") or "")
    await asyncio.to_thread(_download_sync_to_path, file_id, out_path)
    return name, mime


async def list_drive_videos(folder_id: str | None = None) -> list[dict]:
    fid = (folder_id or settings.google_drive_folder_id or "").strip()
    if not fid:
        raise DriveNotConfigured(
            "Set GOOGLE_DRIVE_FOLDER_ID in .env (or pass folder_id)."
        )
    return await asyncio.to_thread(_list_videos_sync, fid)


async def import_drive_folder(
    folder_id: str | None = None,
    role: str = "james_clip",
    limit: int = 25,
    tenant_id: UUID | None = None,
) -> dict:
    """Pull every video in the Drive folder we don't already have, save it
    through MediaStorage, and create a media_asset row for each. Returns a
    summary the UI can show."""
    files = await list_drive_videos(folder_id)
    existing = await list_media(role=role, tenant_id=tenant_id)
    have_ids = {
        t[len("drive:"):]
        for asset in existing
        for t in (asset.get("tags") or [])
        if t.startswith("drive:")
    }

    imported: list[dict] = []
    skipped: list[str] = []
    errors: list[dict] = []
    new_files = [f for f in files if f["id"] not in have_ids][:limit]
    for f in new_files:
        try:
            data = await asyncio.to_thread(_download_sync, f["id"])
            uri, file_path = media_storage().save(
                str(settings.default_tenant_id), data, f.get("name") or "clip.mp4"
            )
            created = await create_media(
                role=role,
                source_type="upload",
                uri=uri,
                file_path=file_path,
                title=f.get("name") or "",
                mime=f.get("mimeType") or "",
                tags=[f"drive:{f['id']}", "source:google_drive"],
                tenant_id=tenant_id,
            )
            imported.append({"drive_id": f["id"], "title": f.get("name"),
                             "id": created["id"], "uri": uri})
        except Exception as e:  # noqa: BLE001 — one bad file shouldn't kill the batch
            errors.append({"drive_id": f["id"], "title": f.get("name"),
                           "error": f"{type(e).__name__}: {str(e)[:120]}"})

    for f in files:
        if f["id"] in have_ids:
            skipped.append(f.get("name") or f["id"])

    return {
        "drive_total": len(files),
        "already_imported": len(have_ids),
        "imported_now": len(imported),
        "skipped": len(skipped),
        "errors": errors,
        "items": imported,
        "role": role,
        "storage_backend": media_storage().__class__.__name__,
    }


__all__ = ["list_drive_videos", "import_drive_folder", "DriveNotConfigured"]
