"""Connected-account endpoints — ported from bm2.0 backend/app/routers/accounts.py
(spec §2.2 Section 9 / D9) onto tenant-scoped routes.

One aggregator profile group per tenant: the group id lives in
tenants.config['postproxy_profile_key'] — the exact key the auditor,
learning, and voice modules already read — and is minted at most once
(never mint-and-forget). Sync pulls whatever the user connected through the
aggregator into the `connections` table rows (the shapes owned by
james_os.connections), which is what turns "OAuth'd through the connect
link" into "the Auditor can see it".

Gated like manager_api.py: every route sits behind the manager_v2 flag.
Also home to GET /manager/next-steps (the computed checklist) and
GET /manager/brand-profile (the internal profile projection).
"""

import json
import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from .. import db
from ..connections import (
    list_stored_connections,
    set_stored_profile_key,
    upsert_synced_connection,
)
from . import next_steps as next_steps_service
from .providers import Providers
from .sources_api import provider_dep, require_manager_v2

logger = logging.getLogger("manager.accounts")

router = APIRouter(tags=["manager-accounts"], dependencies=[Depends(require_manager_v2)])

# donor platform set; 'twitter' is accepted and normalized to the substrate's
# 'x' vocabulary (connections rows are UNIQUE per (tenant, platform))
_PLATFORMS = {"instagram", "facebook", "youtube", "linkedin", "tiktok", "x", "twitter", "threads"}

PROFILE_KEY_CONFIG = "postproxy_profile_key"


def _norm_platform(platform: str) -> str:
    p = platform.strip().lower()
    return "x" if p == "twitter" else p


async def _tenant_config(conn) -> dict:
    cfg = await conn.fetchval(
        "SELECT config FROM tenants WHERE id = current_setting('app.current_tenant', true)::uuid"
    )
    if isinstance(cfg, str):
        cfg = json.loads(cfg or "{}")
    return cfg or {}


async def _save_profile_key(conn, key: str) -> None:
    await conn.execute(
        "UPDATE tenants SET config = jsonb_set(coalesce(config, '{}'::jsonb), "
        "'{postproxy_profile_key}', to_jsonb($1::text)) "
        "WHERE id = current_setting('app.current_tenant', true)::uuid",
        key,
    )


async def _profile_key(conn, providers: Providers) -> str:
    """One aggregator profile group per tenant, persisted in tenants.config so
    it survives before any account exists (never mint-and-forget)."""
    key = str((await _tenant_config(conn)).get(PROFILE_KEY_CONFIG) or "")
    if key:
        return key
    existing = await conn.fetchval(
        "SELECT config->>'aggregator_profile_key' FROM connections "
        "WHERE config->>'aggregator_profile_key' IS NOT NULL LIMIT 1"
    )
    if existing:
        return str(existing)
    return await _mint_profile_key(conn, providers)


async def _mint_profile_key(conn, providers: Providers) -> str:
    """Create a fresh aggregator profile group and persist it — on the tenant
    config and on any stale connection rows (heals mock->live and vendor
    switches, where the stored key is unknown to the current provider)."""
    name = await conn.fetchval(
        "SELECT name FROM tenants WHERE id = current_setting('app.current_tenant', true)::uuid"
    )
    key = await providers.social.create_profile(str(name or "brand"))
    await _save_profile_key(conn, key)
    await set_stored_profile_key(conn, key)
    return key


# ── accounts ────────────────────────────────────────────────────────────────


@router.get("/manager/accounts")
async def list_accounts() -> dict:
    async with db.acquire() as conn:
        rows = await list_stored_connections(conn)
        key = str((await _tenant_config(conn)).get(PROFILE_KEY_CONFIG) or "")
    return {"profile_key": key, "accounts": rows}


class AccountBody(BaseModel):
    platform: str = Field(min_length=1, max_length=30)
    handle: str = Field(min_length=1, max_length=120)


@router.post("/manager/accounts", status_code=201)
async def add_account(
    body: AccountBody, providers: Providers = Depends(provider_dep)
) -> dict:
    """Manually register one account (donor POST /accounts). The connections
    table keys one row per platform, so re-adding the same platform+handle is
    a 409 (donor semantics) and a different handle replaces the stored one."""
    platform = _norm_platform(body.platform)
    handle = body.handle.strip().lstrip("@")
    if platform not in _PLATFORMS:
        raise HTTPException(
            status_code=422, detail=f"unknown platform {platform!r}; one of {sorted(_PLATFORMS)}"
        )
    if not handle:
        raise HTTPException(status_code=422, detail="handle must not be empty")
    async with db.acquire() as conn:
        duplicate = await conn.fetchrow(
            "SELECT 1 FROM connections WHERE platform = $1 AND handle = $2 "
            "AND status = 'connected'",
            platform,
            handle,
        )
        if duplicate is not None:
            raise HTTPException(
                status_code=409, detail=f"{platform} account @{handle} is already connected"
            )
        key = await _profile_key(conn, providers)
        row = await upsert_synced_connection(conn, platform, handle, key)
    return row


# ── white-label groups: list / bind / sync ──────────────────────────────────


@router.get("/manager/accounts/groups")
async def list_groups(providers: Providers = Depends(provider_dep)) -> dict:
    """List the aggregator's existing profile groups + the accounts already in
    each, so the user can bind this tenant to the group holding its data (needed
    when accounts were connected in the aggregator's own dashboard, or when the
    plan group-limit forced reuse)."""
    try:
        groups = await providers.social.list_groups()
    except Exception as exc:  # noqa: BLE001 — vendor error, surface cleanly
        raise HTTPException(status_code=502, detail=f"could not list groups: {str(exc)[:200]}") from exc
    async with db.acquire() as conn:
        bound = str((await _tenant_config(conn)).get(PROFILE_KEY_CONFIG) or "") or None
    return {
        "bound_group": bound,
        "groups": [
            {
                "group_id": g.group_id,
                "name": g.name,
                "account_count": len(g.accounts),
                "accounts": [f"{a.platform}:@{a.handle}" for a in g.accounts],
            }
            for g in groups
        ],
    }


class BindBody(BaseModel):
    group_id: str = Field(min_length=1, max_length=120)


@router.post("/manager/accounts/bind")
async def bind_group(
    body: BindBody, providers: Providers = Depends(provider_dep)
) -> dict:
    """Point this tenant at an existing aggregator group, then sync its
    accounts. Persists the choice in tenants.config so later syncs/audits
    (auditor.py reads the same key) use it."""
    async with db.acquire() as conn:
        await _save_profile_key(conn, body.group_id.strip())
        return await _do_sync(conn, providers)


@router.post("/manager/accounts/sync")
async def sync_accounts(providers: Providers = Depends(provider_dep)) -> dict:
    """Pull whatever the user connected in the aggregator into our connections
    rows — call it when the user returns from the connect flow. Idempotent:
    upserts by (tenant, platform); the group is the source of truth."""
    async with db.acquire() as conn:
        return await _do_sync(conn, providers)


async def _do_sync(conn, providers: Providers) -> dict:
    key = await _profile_key(conn, providers)
    try:
        remote = await providers.social.list_accounts(key)
    except Exception as exc:  # bad/empty group, vendor error — surface cleanly
        raise HTTPException(status_code=502, detail=f"sync failed: {str(exc)[:200]}") from exc

    synced: list[dict] = []
    for prof in remote:
        platform = _norm_platform(prof.platform or "")
        handle = (prof.handle or "").strip().lstrip("@")
        if not platform or not handle:
            continue
        synced.append(await upsert_synced_connection(conn, platform, handle, key))
    return {"profile_key": key, "synced": len(synced), "accounts": synced}


# ── connect URL (white-label OAuth passthrough) ─────────────────────────────


@router.get("/manager/accounts/connect-url")
async def connect_url(
    platform: str = "instagram", providers: Providers = Depends(provider_dep)
) -> dict:
    async with db.acquire() as conn:
        key = await _profile_key(conn, providers)
        try:
            url = await providers.social.connect_url(key, platform)
        except RuntimeError as exc:
            if "not found" not in str(exc).lower():
                raise HTTPException(
                    status_code=502, detail=f"connect-url failed: {str(exc)[:200]}"
                ) from exc
            # stored key predates the current provider (mock->live switch):
            # mint a real group and retry once (donor self-heal)
            key = await _mint_profile_key(conn, providers)
            try:
                url = await providers.social.connect_url(key, platform)
            except RuntimeError as retry_exc:
                raise HTTPException(
                    status_code=502, detail=f"connect-url failed: {str(retry_exc)[:200]}"
                ) from retry_exc
    return {"profile_key": key, "platform": platform, "connect_url": url}


# ── next steps + brand profile projection ───────────────────────────────────


@router.get("/manager/next-steps")
async def get_next_steps() -> list[dict]:
    """The onboarding/operating checklist, recomputed from live DB state on
    every call (donor GET /brands/{id}/next-steps)."""
    return await next_steps_service.compute()


@router.get("/manager/brand-profile")
async def get_brand_profile_projection() -> dict:
    """The bm2.0 'james-os handoff export' shape as this system's internal
    projection: the profile-fields envelope merged over the brand_profiles
    row (envelope wins; readers see a superset)."""
    from ..brands import profile_projection  # lazy: brands pulls content machinery

    return await profile_projection()
