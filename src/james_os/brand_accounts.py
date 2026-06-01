"""Brand accounts — the social handles the BRAND itself owns.

Distinct from `trends.get_watchlist()` which lists peer / competitor /
research creators. Analytics reads from this list ONLY, so the
dashboard never mixes "how is JP doing" with "how is Ryan Serhant
doing."

Stored on `tenants.config.brand_accounts` as a JSON array:

    [{"platform": "instagram", "handle": "jamesprendamano",
      "name": "James Prendamano (personal)"},
     {"platform": "instagram", "handle": "prerealcapital",
      "name": "PreReal Capital"}]

Scraping reuses the watchlist refresh path — same Apify actors, same
ingestion into the events table, same TREND_CATEGORY. The difference
is just WHICH handles get scraped, and which handles analytics
considers "mine." Posts are filtered at read time by checking the
post's handle against this list.
"""

from __future__ import annotations

import json
from uuid import UUID

from .db import acquire


async def get_brand_accounts(tenant_id: UUID | None = None) -> list[dict]:
    """The brand's own tracked handles. Returns [] when none have been
    configured — analytics shows an empty-state prompt in that case."""
    async with acquire(tenant_id) as conn:
        cfg = await conn.fetchval(
            "SELECT config FROM tenants WHERE id = "
            "current_setting('app.current_tenant', true)::uuid"
        )
    if isinstance(cfg, str):
        cfg = json.loads(cfg)
    return (cfg or {}).get("brand_accounts", [])


async def set_brand_accounts(
    accounts: list[dict], tenant_id: UUID | None = None,
) -> list[dict]:
    """Wholesale replace the brand's tracked handles. accounts =
    [{platform, handle, name?}].

    Coerces handles (strip @, strip whitespace, lowercase the handle)
    so '@JamesPrendamano' and 'jamesprendamano' collapse to the same
    entry — important because Apify returns handles lowercased and
    analytics matches on equality.
    """
    clean: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for a in accounts:
        platform = (a.get("platform") or "").strip().lower()
        handle = (a.get("handle") or "").strip().lstrip("@").lower()
        if not platform or not handle:
            continue
        key = (platform, handle)
        if key in seen:
            continue
        seen.add(key)
        entry = {"platform": platform, "handle": handle}
        name = (a.get("name") or "").strip()
        if name:
            entry["name"] = name
        clean.append(entry)
    async with acquire(tenant_id) as conn:
        await conn.execute(
            "UPDATE tenants SET config = jsonb_set("
            "coalesce(config,'{}'::jsonb), '{brand_accounts}', $1::jsonb) "
            "WHERE id = current_setting('app.current_tenant', true)::uuid",
            json.dumps(clean),
        )
    return clean


async def brand_handles_by_platform(
    tenant_id: UUID | None = None,
) -> dict[str, list[str]]:
    """Reshape into {platform: [handle, ...]} for the Apify scraper,
    which takes one batch per platform. Returns an empty dict when
    no accounts are configured — the caller refuses to start a scrape
    in that case rather than calling Apify with nothing to do."""
    rows = await get_brand_accounts(tenant_id)
    out: dict[str, list[str]] = {}
    for r in rows:
        platform = r.get("platform") or ""
        handle = r.get("handle") or ""
        if platform and handle:
            out.setdefault(platform, []).append(handle)
    return out


async def brand_handle_set(tenant_id: UUID | None = None) -> set[str]:
    """Just the lowercased handles, platform-agnostic — used by
    analytics to filter posts down to brand-owned content. Returns
    an empty set when no accounts are configured (so analytics
    correctly returns zero posts rather than every peer post)."""
    rows = await get_brand_accounts(tenant_id)
    return {(r.get("handle") or "").lower() for r in rows if r.get("handle")}


__all__ = [
    "get_brand_accounts", "set_brand_accounts",
    "brand_handles_by_platform", "brand_handle_set",
]
