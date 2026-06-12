"""B-roll library — every generated cutaway clip becomes a reusable asset.

Runway and Higgsfield clips cost real credits per render. This module makes
each rendered insert clip a media_assets row (role='broll'), alongside manual
uploads, so the library grows for free as videos are produced — and future
renders REUSE a matching clip instead of paying to regenerate it.

Reuse matching is deliberately conservative and transparent:
  * candidates: role='broll' assets with an http uri (uploads + generated);
  * the insert's scene prompt is compared with each asset's stored prompt
    (notes) / title by content-word overlap — no embeddings, no magic;
  * a clip is reused only when the overlap is strong (>= _REUSE_OVERLAP of
    the smaller word set AND >= _REUSE_MIN_SHARED shared words), and the
    aspect tag matches when both sides declare one;
  * reuse provenance lands in the asset's tags so the UI can show how often
    a clip earns its keep.

Failure anywhere here NEVER breaks a render — registration is best-effort,
and the worst case of a failed lookup is paying to generate, same as today.
"""

import re
from uuid import UUID

from .db import acquire
from .media import create_media

# Words that carry no scene meaning — kept tiny on purpose.
_STOP = frozenset(
    "the a an and or but with from into over under for of to in on at as is are was "
    "were be been being this that these those it its his her their our your my we "
    "you they he she them him us not no very then than so such while when where "
    "after before during through about against between each few more most other "
    "some only own same too can will just should now also".split()
)

_WORD_RE = re.compile(r"[a-zA-Z][a-zA-Z'-]{2,}")

_REUSE_OVERLAP = 0.6     # shared / min(len(a), len(b))
_REUSE_MIN_SHARED = 4    # at least this many shared content words
_MAX_CANDIDATES = 400    # newest-first cap on the comparison set


def _content_words(text: str) -> frozenset[str]:
    return frozenset(
        w.lower() for w in _WORD_RE.findall(text or "") if w.lower() not in _STOP
    )


def _short_title(prompt: str, limit: int = 90) -> str:
    p = " ".join((prompt or "").split())
    return p[:limit] + ("…" if len(p) > limit else "")


def _tenant_uuid(tenant_id) -> UUID | None:
    if tenant_id is None or isinstance(tenant_id, UUID):
        return tenant_id
    try:
        return UUID(str(tenant_id))
    except (ValueError, TypeError):
        return None


async def register_generated_clip(
    *,
    url: str,
    prompt: str,
    engine: str,
    aspect: str,
    tenant_id=None,
) -> None:
    """File a freshly rendered insert clip into the library. Best-effort —
    a failure here must never fail the render that produced the clip."""
    if not (url or "").startswith("http"):
        return
    try:
        await create_media(
            role="broll",
            source_type="generated",
            uri=url,
            title=_short_title(prompt) or f"{engine or 'video'} B-roll clip",
            mime="video/mp4",
            tags=[t for t in ("generated", engine or "", aspect or "") if t],
            notes=(prompt or "")[:2000],
            tenant_id=_tenant_uuid(tenant_id),
        )
    except Exception:  # noqa: BLE001 — provenance only, never break a render
        pass


async def find_reusable_clip(
    prompt: str, aspect: str = "", tenant_id=None,
) -> dict | None:
    """Return {'url', 'media_id', 'title'} for the best matching library clip,
    or None when nothing matches confidently. Conservative by design: a wrong
    reuse is worse than a paid regeneration."""
    want = _content_words(prompt)
    if len(want) < _REUSE_MIN_SHARED:
        return None
    try:
        async with acquire(_tenant_uuid(tenant_id)) as conn:
            rows = await conn.fetch(
                "SELECT id, uri, title, notes, tags FROM media_assets "
                "WHERE role = 'broll' AND uri LIKE 'http%' "
                "ORDER BY created_at DESC LIMIT $1",
                _MAX_CANDIDATES,
            )
    except Exception:  # noqa: BLE001 — lookup failure → just generate
        return None

    best, best_score = None, 0.0
    for r in rows:
        tags = list(r["tags"] or [])
        # Aspect gate only when BOTH sides declare one (uploads usually don't).
        clip_aspects = [t for t in tags if ":" in t or t in ("9:16", "1:1", "16:9")]
        if aspect and clip_aspects and aspect not in clip_aspects:
            continue
        have = _content_words(f"{r['notes'] or ''} {r['title'] or ''}")
        if not have:
            continue
        shared = len(want & have)
        if shared < _REUSE_MIN_SHARED:
            continue
        score = shared / min(len(want), len(have))
        if score >= _REUSE_OVERLAP and score > best_score:
            best, best_score = r, score

    if best is None:
        return None
    return {"url": best["uri"], "media_id": str(best["id"]), "title": best["title"] or ""}


async def mark_reused(media_id: str, tenant_id=None) -> None:
    """Append a 'reused' tag occurrence so the library shows which clips earn
    their keep. Best-effort."""
    try:
        async with acquire(_tenant_uuid(tenant_id)) as conn:
            await conn.execute(
                "UPDATE media_assets SET tags = array_append(tags, 'reused'), "
                "updated_at = now() WHERE id = $1",
                UUID(media_id),
            )
    except Exception:  # noqa: BLE001
        pass


__all__ = ["register_generated_clip", "find_reusable_clip", "mark_reused"]
