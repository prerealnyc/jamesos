"""Audio library — music beds and SFX (whooshes etc.) that make videos pop.

Music: assets with role='music' tagged with a mood (upbeat / calm / dramatic /
tension). A render that wants an 'upbeat' bed picks one from the library —
randomly among the matches so repeated renders don't all share one track —
falling back to the static settings URL (music_url_upbeat …) when the library
has none. So the existing 4-mood pipeline keeps working with zero uploads, and
gets richer the moment tracks are added.

SFX: assets with role='sfx' tagged with a kind (whoosh / hit / riser / pop).
The insert-based builders drop a short whoosh at each B-roll cutaway start —
the classic transition pop — only when the library actually has one. No
library sound → no SFX layer, never a broken element.

Free sources that work well here (download → upload on the /audio page):
  * Mixkit (mixkit.co)            — music + SFX, free license, no attribution
  * Pixabay (pixabay.com/music,
    /sound-effects)               — free for commercial use, no attribution
  * YouTube Audio Library         — free music + SFX (check per-track terms)
  * Freesound (freesound.org)     — huge SFX archive, CC0/CC-BY (check each)
  * Incompetech (incompetech.com) — Kevin MacLeod music, CC-BY (credit him)

Lookups are best-effort: any failure returns the fallback (settings URL or
''), never an exception into the render path.
"""

import random

from .config import settings
from .db import acquire

MUSIC_MOODS = ("upbeat", "calm", "dramatic", "tension")
SFX_KINDS = ("whoosh", "hit", "riser", "pop")

# How many newest matching tracks to choose between (variety without
# resurfacing something ancient the user forgot to delete).
_PICK_POOL = 8


def _settings_music_url(mood: str) -> str:
    return {
        "upbeat": settings.music_url_upbeat,
        "calm": settings.music_url_calm,
        "dramatic": settings.music_url_dramatic,
        "tension": settings.music_url_tension,
    }.get((mood or "").strip().lower(), "")


async def _pick(role: str, tag: str) -> str:
    """Newest-first pool of http assets with `tag`, random pick. '' on none."""
    try:
        async with acquire() as conn:
            rows = await conn.fetch(
                "SELECT uri FROM media_assets "
                "WHERE role = $1 AND $2 = ANY(tags) AND uri LIKE 'http%' "
                "ORDER BY created_at DESC LIMIT $3",
                role, tag, _PICK_POOL,
            )
    except Exception:  # noqa: BLE001 — library lookup must never break a render
        return ""
    if not rows:
        return ""
    return random.choice(rows)["uri"]


async def resolve_music_url(mood: str) -> str:
    """Library track for the mood, else the static settings URL, else ''."""
    m = (mood or "").strip().lower()
    if not m or m == "none":
        return ""
    return await _pick("music", m) or _settings_music_url(m)


async def resolve_sfx_url(kind: str = "whoosh") -> str:
    """Library SFX of `kind`, or '' (callers skip the SFX layer on '')."""
    return await _pick("sfx", (kind or "whoosh").strip().lower())


__all__ = ["MUSIC_MOODS", "SFX_KINDS", "resolve_music_url", "resolve_sfx_url"]
