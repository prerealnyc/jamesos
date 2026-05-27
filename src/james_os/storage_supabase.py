"""Supabase Storage backend for the Reference Library.

Why this exists: Creatomate (and any other external assembler) needs to
*fetch* your uploaded clips. Local-disk paths like /media-files/... are only
reachable on the API host, so real assembly with your james_clip footage
fails when the storage is local. Supabase Storage returns a CDN-backed
public URL that Creatomate can reach from anywhere.

This mirrors the interface of MediaStorage (save / delete) so it's a drop-in
swap. Auto-bootstraps the bucket on first save — never silently fails.
"""

from __future__ import annotations

import base64
import mimetypes
import re
from uuid import uuid4

import httpx

from .config import settings

# Supabase Storage's single-POST upload caps at ~50 MB. Anything larger
# needs the TUS resumable protocol — and TUS is also recommended above ~6 MB.
_RESUMABLE_THRESHOLD = 6 * 1024 * 1024
_TUS_CHUNK = 6 * 1024 * 1024

_EXT_RE = re.compile(r"[^a-zA-Z0-9.]+")
_TIMEOUT = httpx.Timeout(60.0, connect=10.0)


def _safe_ext(filename: str) -> str:
    if "." not in filename:
        return ""
    return "." + _EXT_RE.sub("", filename.rsplit(".", 1)[-1])[:8]


class SupabaseStorageError(RuntimeError):
    """Raised when Supabase Storage rejects an operation."""


class SupabaseMediaStorage:
    """Save uploaded bytes to Supabase Storage; return its public URL."""

    def __init__(self, url: str = "", key: str = "", bucket: str = ""):
        self.base = (url or settings.supabase_url or "").rstrip("/")
        self.key = key or settings.supabase_service_key
        self.bucket = bucket or settings.supabase_media_bucket or "media"
        self._bucket_ready = False
        if not self.base or not self.key:
            raise SupabaseStorageError(
                "Supabase Storage needs SUPABASE_URL + SUPABASE_SERVICE_KEY"
            )

    # ── auth headers (server-side service_role; never expose to browser) ──
    def _h(self, extra: dict | None = None) -> dict:
        h = {"Authorization": f"Bearer {self.key}", "apikey": self.key}
        if extra:
            h.update(extra)
        return h

    def _ensure_bucket(self) -> None:
        """Create the bucket if it doesn't exist. Idempotent: 409 (already
        exists) is the success case on a repeat call. We don't bother
        GETting first — Supabase's bucket GET returns HTTP 400 (not 404)
        when the bucket is missing, which is confusing to branch on."""
        if self._bucket_ready:
            return
        with httpx.Client(timeout=_TIMEOUT) as c:
            r = c.post(
                f"{self.base}/storage/v1/bucket", headers=self._h(),
                json={"id": self.bucket, "name": self.bucket, "public": True},
            )
        # Success path: created (200/201), already exists (409), OR
        # Supabase's quirky 400 envelope around a 409/already-exists body.
        already_exists = (
            r.status_code == 409
            or '"statusCode":"409"' in r.text
            or "already exists" in r.text.lower()
        )
        if r.status_code in (200, 201) or already_exists:
            self._bucket_ready = True
            return
        raise SupabaseStorageError(
            f"Could not create bucket '{self.bucket}': "
            f"HTTP {r.status_code} {r.text[:200]}"
        )

    # ── TUS resumable upload (required for files > 50 MB; recommended > 6 MB) ──
    def _b64(self, s: str) -> str:
        return base64.b64encode(s.encode("utf-8")).decode("ascii")

    def _upload_resumable(self, path: str, data: bytes, mime: str) -> None:
        size = len(data)
        meta = (
            f"bucketName {self._b64(self.bucket)},"
            f"objectName {self._b64(path)},"
            f"contentType {self._b64(mime)},"
            f"cacheControl {self._b64('3600')}"
        )
        with httpx.Client(timeout=httpx.Timeout(300.0, connect=10.0)) as c:
            r = c.post(
                f"{self.base}/storage/v1/upload/resumable",
                headers=self._h({
                    "Tus-Resumable": "1.0.0",
                    "Upload-Length": str(size),
                    "Upload-Metadata": meta,
                    "Content-Type": "application/octet-stream",
                    "x-upsert": "true",
                }),
            )
            if r.status_code not in (201, 200):
                raise SupabaseStorageError(
                    f"TUS create failed: HTTP {r.status_code} {r.text[:200]}"
                )
            location = r.headers.get("Location") or r.headers.get("location")
            if not location:
                raise SupabaseStorageError("TUS create returned no Location header")
            # PATCH chunks
            offset = 0
            while offset < size:
                chunk = data[offset:offset + _TUS_CHUNK]
                pr = c.patch(
                    location,
                    headers=self._h({
                        "Tus-Resumable": "1.0.0",
                        "Upload-Offset": str(offset),
                        "Content-Type": "application/offset+octet-stream",
                    }),
                    content=chunk,
                )
                if pr.status_code != 204:
                    raise SupabaseStorageError(
                        f"TUS PATCH failed at {offset}: "
                        f"HTTP {pr.status_code} {pr.text[:200]}"
                    )
                new_offset = int(pr.headers.get("Upload-Offset") or
                                 pr.headers.get("upload-offset") or
                                 (offset + len(chunk)))
                if new_offset <= offset:
                    raise SupabaseStorageError("TUS server reported no progress")
                offset = new_offset

    def save(self, tenant: str, data: bytes, filename: str) -> tuple[str, str]:
        """Upload bytes; return (public_url, internal_path).

        Small files take the standard POST path. Large files (> ~6 MB) use
        the TUS resumable protocol, which bypasses the single-POST 50 MB
        cap and survives flaky connections via chunked PATCHes.
        """
        self._ensure_bucket()
        ext = _safe_ext(filename)
        path = f"{tenant}/{uuid4().hex}{ext}"
        mime = mimetypes.guess_type(filename)[0] or "application/octet-stream"

        if len(data) > _RESUMABLE_THRESHOLD:
            self._upload_resumable(path, data, mime)
        else:
            with httpx.Client(timeout=_TIMEOUT) as c:
                r = c.post(
                    f"{self.base}/storage/v1/object/{self.bucket}/{path}",
                    headers=self._h({"Content-Type": mime, "x-upsert": "false"}),
                    content=data,
                )
            if r.status_code not in (200, 201):
                raise SupabaseStorageError(
                    f"Upload failed: HTTP {r.status_code} {r.text[:200]}"
                )

        public_url = f"{self.base}/storage/v1/object/public/{self.bucket}/{path}"
        return public_url, f"supabase://{self.bucket}/{path}"

    def delete(self, file_path: str | None) -> None:
        """Delete by the internal path returned from save()."""
        if not file_path or not file_path.startswith("supabase://"):
            return
        rel = file_path[len("supabase://"):]
        try:
            bucket, _, path = rel.partition("/")
            with httpx.Client(timeout=_TIMEOUT) as c:
                c.request(
                    "DELETE",
                    f"{self.base}/storage/v1/object/{bucket}/{path}",
                    headers=self._h(),
                )
        except Exception:  # noqa: BLE001 — best-effort cleanup
            pass


def derive_supabase_url_from_db(database_url: str) -> str:
    """If SUPABASE_URL isn't set explicitly, derive it from the project ref
    in the database connection (postgres.<ref>@... or db.<ref>.supabase.co).
    Returns '' if we can't infer one — caller then refuses to enable the
    backend (no silent guessing)."""
    m = re.search(r"postgres\.([a-z0-9]{8,})", database_url or "") or \
        re.search(r"db\.([a-z0-9]{8,})\.supabase\.co", database_url or "")
    return f"https://{m.group(1)}.supabase.co" if m else ""


__all__ = ["SupabaseMediaStorage", "SupabaseStorageError",
           "derive_supabase_url_from_db"]
