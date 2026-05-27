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
    optional — the rest of the app runs even if google-api isn't installed."""
    from google.oauth2 import service_account
    from googleapiclient.discovery import build

    path = (settings.google_service_account_json or "").strip()
    if not path:
        raise DriveNotConfigured(
            "Set GOOGLE_SERVICE_ACCOUNT_JSON (path to the service-account "
            "key file) in .env or Settings."
        )
    creds = service_account.Credentials.from_service_account_file(path, scopes=_SCOPES)
    return build("drive", "v3", credentials=creds, cache_discovery=False)


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
    ).execute()
    return res.get("files", [])


def _download_sync(file_id: str) -> bytes:
    from googleapiclient.http import MediaIoBaseDownload
    svc = _service()
    request = svc.files().get_media(fileId=file_id)
    buf = io.BytesIO()
    downloader = MediaIoBaseDownload(buf, request)
    done = False
    while not done:
        _, done = downloader.next_chunk()
    return buf.getvalue()


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
