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
import logging
from uuid import UUID

import asyncpg

from .db import acquire

logger = logging.getLogger("james_os.brand_kit")

# NEUTRAL default — carries NO brand identity. Every identity field is empty so
# a brand that hasn't been configured inherits NOTHING from another brand; the
# nameplate falls back to the tenant's OWN name (get_brand_kit), and empty
# handle/website/tagline/signoff simply degrade (renders omit them). This is the
# D10 guarantee: content sticks to the specific brand, never leaks James's.
DEFAULT_BRAND_KIT = {
    "display_name": "",
    "tagline": "",
    "handle": "",
    "logo_url": "",
    # Fixed closing line appended to every generated caption (posts + videos).
    # Empty by default → no forced sign-off on brands that didn't choose one.
    "caption_signoff": "",
    # Branded quote cards (brand_quote / hero_quote): footer + accent.
    "website": "",
    "footer_tagline": "",
}

# James Prendamano's OWN identity. Applied ONLY to his tenant
# (settings.default_tenant_id) as a fallback base, so his signature name plate,
# handle, footer and sign-off survive without a stored kit — and never touch any
# other brand. Any brand can override every field via set_brand_kit.
_JAMES_BRAND_KIT = {
    "display_name": "James Prendamano",
    "tagline": "PreReal",
    "handle": "@j_prendamano",
    "logo_url": "",
    "caption_signoff": "We are ALL one",
    "website": "prendamanoacademy.com",
    "footer_tagline": "free forever",
}

_KEYS = set(DEFAULT_BRAND_KIT)


def _tenant(tenant_id) -> UUID | None:
    """Normalize to a UUID, or None so acquire() resolves the tenant itself
    (request contextvar → settings.default_tenant_id)."""
    if tenant_id is None or isinstance(tenant_id, UUID):
        return tenant_id
    try:
        return UUID(str(tenant_id))
    except (ValueError, TypeError):
        return None


def _own_name(name, config) -> str:
    """The brand's OWN display name, resolved from its tenant row:
    config->profile.brand first (editable), then tenants.name — sentinel labels
    excluded. Never another brand's name. '' if the tenant has none yet."""
    cfg = json.loads(config) if isinstance(config, str) else (config or {})
    brand = ((cfg or {}).get("profile") or {}).get("brand") or ""
    brand = brand.strip()
    if brand and brand != "JP Brand Manager":
        return brand
    nm = (name or "").strip()
    if nm and nm.lower() not in ("tenant zero", "tenant-zero"):
        return nm
    return ""


async def get_brand_kit(tenant_id=None) -> dict:
    from .config import settings
    try:
        async with acquire(_tenant(tenant_id)) as conn:
            row = await conn.fetchrow(
                "SELECT id, name, config, config->'brand_kit' AS kit FROM tenants "
                "WHERE id = current_setting('app.current_tenant', true)::uuid"
            )
    except (asyncpg.PostgresError, OSError) as exc:
        # Renders must never break on brand kit — but the failure must be loud.
        # Neutral (not James) on failure: a broken read must not forge identity.
        logger.warning("brand kit read failed, falling back to defaults: %s", exc)
        return dict(DEFAULT_BRAND_KIT)
    if not row:
        return dict(DEFAULT_BRAND_KIT)
    # James's OWN tenant keeps his signature identity as its base; every other
    # brand starts from the neutral kit so nothing of James's can leak in.
    is_james = row["id"] == settings.default_tenant_id
    base = dict(_JAMES_BRAND_KIT if is_james else DEFAULT_BRAND_KIT)
    raw = row["kit"]
    stored = raw if isinstance(raw, dict) else (json.loads(raw) if raw else {})
    kit = {**base, **{k: v for k, v in (stored or {}).items() if k in _KEYS}}
    # Name plate falls back to the brand's OWN name, never a hardcoded literal.
    if not (kit.get("display_name") or "").strip():
        kit["display_name"] = _own_name(row["name"], row["config"])
    return kit


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
