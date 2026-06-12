"""Brand kit — the identity elements every render carries.

One config slot (tenants.config['brand_kit']) holding:
  * display_name — the on-screen name plate ("JAMES PRENDAMANO")
  * tagline      — small line under the name ("PreReal · Staten Island")
  * handle       — social handle for the end card ("@jamesprendamano")
  * logo_url     — uploaded logo (role='brand_logo' media), watermarked on
                   every video + shown on the end card

Renders read this through get_brand_kit() and degrade gracefully: no logo →
no watermark layer; empty handle → end card shows just "FOLLOW FOR MORE".
Defaults keep the name plate working before anything is configured.
"""

import json
from uuid import UUID

from .config import settings
from .db import acquire

DEFAULT_BRAND_KIT = {
    "display_name": "James Prendamano",
    "tagline": "PreReal",
    "handle": "",
    "logo_url": "",
}

_KEYS = set(DEFAULT_BRAND_KIT)


def _tenant(tenant_id) -> UUID:
    if isinstance(tenant_id, UUID):
        return tenant_id
    try:
        return UUID(str(tenant_id)) if tenant_id else UUID(settings.default_tenant_id)
    except (ValueError, TypeError):
        return UUID(settings.default_tenant_id)


async def get_brand_kit(tenant_id=None) -> dict:
    try:
        async with acquire(_tenant(tenant_id)) as conn:
            raw = await conn.fetchval(
                "SELECT config->'brand_kit' FROM tenants WHERE id = "
                "current_setting('app.current_tenant', true)::uuid"
            )
    except Exception:  # noqa: BLE001 — renders must never break on brand kit
        return dict(DEFAULT_BRAND_KIT)
    stored = raw if isinstance(raw, dict) else (json.loads(raw) if raw else {})
    return {**DEFAULT_BRAND_KIT, **{k: v for k, v in (stored or {}).items() if k in _KEYS}}


async def set_brand_kit(updates: dict, tenant_id=None) -> dict:
    cur = await get_brand_kit(tenant_id)
    merged = {**cur, **{k: str(v or "").strip() for k, v in (updates or {}).items() if k in _KEYS}}
    async with acquire(_tenant(tenant_id)) as conn:
        await conn.execute(
            "UPDATE tenants SET config = jsonb_set(coalesce(config,'{}'::jsonb), "
            "'{brand_kit}', $1::jsonb, true) WHERE id = "
            "current_setting('app.current_tenant', true)::uuid",
            json.dumps(merged),
        )
    return merged


__all__ = ["DEFAULT_BRAND_KIT", "get_brand_kit", "set_brand_kit"]
