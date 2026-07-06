"""Speaker directory — reusable on-screen name-tags (@handle + subtitle/title).

A long-form render places each speaker's tag as a lower-third for ~2.5s when
they first appear, so the audience knows who is who. This module is the CRUD
for the saved directory; the render timing + element live in the video pipeline
and caption_styles.speaker_nametag_elements.
"""

from uuid import UUID

from .db import acquire


def _row(r) -> dict:
    d = dict(r)
    d["id"] = str(d["id"])
    d.pop("tenant_id", None)
    if d.get("created_at") is not None:
        d["created_at"] = d["created_at"].isoformat()
    return d


def _norm_handle(handle: str) -> str:
    h = (handle or "").strip()
    if h and not h.startswith("@"):
        h = "@" + h
    return h


async def list_speakers(tenant_id: UUID | None = None) -> list[dict]:
    async with acquire(tenant_id) as conn:
        rows = await conn.fetch("SELECT * FROM speakers ORDER BY created_at DESC")
    return [_row(r) for r in rows]


async def create_speaker(
    handle: str, subtitle: str = "", face_ref: str = "",
    tenant_id: UUID | None = None,
) -> dict:
    h = _norm_handle(handle)
    if not h:
        raise ValueError("handle is required")
    async with acquire(tenant_id) as conn:
        # tenant_id from the RLS session setting so the WITH CHECK always passes.
        row = await conn.fetchrow(
            "INSERT INTO speakers (tenant_id, handle, subtitle, face_ref) "
            "VALUES (current_setting('app.current_tenant', true)::uuid, $1, $2, $3) "
            "RETURNING *",
            h, (subtitle or "").strip(), (face_ref or "").strip(),
        )
    return _row(row)


async def update_speaker(
    speaker_id: UUID, *, handle=None, subtitle=None, face_ref=None,
    tenant_id: UUID | None = None,
) -> dict | None:
    sets: list[str] = []
    vals: list = []
    if handle is not None:
        sets.append("handle"); vals.append(_norm_handle(handle))
    if subtitle is not None:
        sets.append("subtitle"); vals.append((subtitle or "").strip())
    if face_ref is not None:
        sets.append("face_ref"); vals.append((face_ref or "").strip())
    if not sets:
        return None
    set_sql = ", ".join(f"{c} = ${i + 2}" for i, c in enumerate(sets))
    async with acquire(tenant_id) as conn:
        row = await conn.fetchrow(
            f"UPDATE speakers SET {set_sql} WHERE id = $1 RETURNING *",
            speaker_id, *vals,
        )
    return _row(row) if row else None


async def delete_speaker(speaker_id: UUID, tenant_id: UUID | None = None) -> bool:
    async with acquire(tenant_id) as conn:
        status = await conn.execute("DELETE FROM speakers WHERE id = $1", speaker_id)
    return status.rsplit(" ", 1)[-1] != "0"


__all__ = ["list_speakers", "create_speaker", "update_speaker", "delete_speaker"]
