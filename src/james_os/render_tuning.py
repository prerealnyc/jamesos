"""Live-tunable render knobs, per tenant, stored in tenants.config['render_tuning'].

Read at render time so a feedback-driven tweak (e.g. "B-roll inserts too
short" → longer) takes effect on the NEXT production with no deploy. Mirrors
the autopilot config-slot pattern. Values are clamped on write to safe ranges
so a bad/auto value can never break rendering.
"""

import json
from uuid import UUID

from .db import acquire

# Knobs that can be changed live. Defaults match the existing code literals,
# so an empty slot = exactly today's behavior.
DEFAULT_RENDER_TUNING = {
    "broll_insert_min_dur": 1.5,   # seconds — engaging-avatar B-roll insert floor
    "broll_insert_max_dur": 2.0,   # seconds — engaging-avatar B-roll insert ceiling
}

# Conservative bounds (Runway makes ~5s clips that Creatomate trims; insert
# fade is 0.15s — staying in [1.0, 4.0] avoids dead frames).
_DUR_FLOOR, _DUR_CEIL = 1.0, 4.0


async def get_render_tuning(tenant_id: UUID | None = None) -> dict:
    async with acquire(tenant_id) as conn:
        cfg = await conn.fetchval(
            "SELECT config FROM tenants WHERE id = "
            "current_setting('app.current_tenant', true)::uuid"
        )
    if isinstance(cfg, str):
        cfg = json.loads(cfg)
    rt = (cfg or {}).get("render_tuning", {}) or {}
    return {**DEFAULT_RENDER_TUNING, **rt}


def _clamp(updates: dict) -> dict:
    out: dict = {}
    for k, v in updates.items():
        if k not in DEFAULT_RENDER_TUNING:
            continue  # allow-list: never store an unknown knob
        try:
            out[k] = max(_DUR_FLOOR, min(float(v), _DUR_CEIL))
        except (TypeError, ValueError):
            continue
    return out


async def set_render_tuning(updates: dict, tenant_id: UUID | None = None) -> dict:
    merged = {**(await get_render_tuning(tenant_id)), **_clamp(updates)}
    # keep min <= max
    if merged["broll_insert_min_dur"] > merged["broll_insert_max_dur"]:
        merged["broll_insert_min_dur"] = merged["broll_insert_max_dur"]
    async with acquire(tenant_id) as conn:
        await conn.execute(
            "UPDATE tenants SET config = jsonb_set("
            "coalesce(config,'{}'::jsonb), '{render_tuning}', $1::jsonb) "
            "WHERE id = current_setting('app.current_tenant', true)::uuid",
            json.dumps(merged),
        )
    return merged


__all__ = ["DEFAULT_RENDER_TUNING", "get_render_tuning", "set_render_tuning"]
