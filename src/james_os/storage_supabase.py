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

import mimetypes
import re
from uuid import uuid4

import httpx

from .config import settings

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
        if self._bucket_ready:
            return
        with httpx.Client(timeout=_TIMEOUT) as c:
            # Try to read the bucket; create it if missing.
            r = c.get(f"{self.base}/storage/v1/bucket/{self.bucket}", headers=self._h())
            if r.status_code == 200:
                self._bucket_ready = True
                return
            if r.status_code == 404:
                cr = c.post(
                    f"{self.base}/storage/v1/bucket", headers=self._h(),
                    json={"id": self.bucket, "name": self.bucket, "public": True},
                )
                if cr.status_code in (200, 201, 409):
                    self._bucket_ready = True
                    return
                raise SupabaseStorageError(
                    f"Could not create bucket '{self.bucket}': "
                    f"HTTP {cr.status_code} {cr.text[:200]}"
                )
            raise SupabaseStorageError(
                f"Bucket check failed: HTTP {r.status_code} {r.text[:200]}"
            )

    def save(self, tenant: str, data: bytes, filename: str) -> tuple[str, str]:
        """Upload bytes; return (public_url, internal_path).

        The internal_path is what we store in media_assets.file_path so we
        can delete the object later. The public URL is what we render and
        what Creatomate fetches.
        """
        self._ensure_bucket()
        ext = _safe_ext(filename)
        path = f"{tenant}/{uuid4().hex}{ext}"
        mime = mimetypes.guess_type(filename)[0] or "application/octet-stream"
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
