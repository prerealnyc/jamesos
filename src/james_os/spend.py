"""The provider spend ledger — what each brand costs, written at the call site.

Before this module nothing recorded spend: llm.py discarded result.usage, a
dozen direct AsyncOpenAI clients never passed through llm.py, and the one
natural ceiling (Anthropic out of credit) was bypassed by FallbackLLM quietly
switching to OpenAI. The first anyone knew of a runaway loop was the invoice.

Three jobs:
  * record()   — one row in provider_spend per paid call. NEVER raises: a
                 ledger failure must not turn a successful render into an
                 error, so it logs and swallows.
  * estimate_* — a price table for the models this codebase actually calls.
                 Every number is an ESTIMATE (the providers' invoices are the
                 truth, and prices drift); every row says so in meta.
  * over_cap() — the gate the scheduler and autopilot ask before spending:
                 PAUSE_SPEND (global kill switch) or today's estimate at or
                 above the brand's daily cap.

Tenant resolution: explicit arg → job_scope tenant → request contextvar →
default tenant. A row that fell through to the default is marked
meta.tenant_fallback=true rather than silently billed to the operator's brand.
"""

from __future__ import annotations

import contextvars
import json
import logging
from contextlib import contextmanager
from typing import Any
from uuid import UUID

from . import db as _db
from .config import settings
from .db import acquire

logger = logging.getLogger("spend")

# Every figure here is an estimate. Kept on each row so a reader of the ledger
# never mistakes it for an invoice.
ESTIMATE = True

# USD per 1M tokens (input, output). Longest matching prefix wins, so a dated
# snapshot ("claude-sonnet-4-5-20250929") resolves through its family.
_TOKEN_PRICES: dict[str, tuple[float, float]] = {
    # OpenAI
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-4o": (2.50, 10.00),
    "gpt-4.1-mini": (0.40, 1.60),
    "gpt-4.1": (2.00, 8.00),
    # Anthropic (first-party API rates)
    "claude-fable-5-1": (10.00, 50.00),
    "claude-fable-5": (10.00, 50.00),
    "claude-opus-5-5": (4.00, 20.00),
    "claude-opus-5": (5.00, 25.00),
    "claude-opus-4-8": (5.00, 25.00),
    "claude-opus-4-7": (5.00, 25.00),
    "claude-opus-4-6": (5.00, 25.00),
    "claude-opus-4-5": (5.00, 25.00),
    "claude-opus": (15.00, 75.00),          # 4.1 and earlier
    "claude-sonnet-5-5": (2.00, 10.00),
    "claude-sonnet-5": (2.00, 10.00),
    "claude-sonnet-4-6": (3.00, 15.00),
    "claude-sonnet": (3.00, 15.00),         # 4.5 and earlier
    "claude-haiku-4-5": (1.00, 5.00),
    "claude-haiku": (0.80, 4.00),
    # Embeddings (input only)
    "text-embedding-3-small": (0.02, 0.0),
    "text-embedding-3-large": (0.13, 0.0),
    "voyage-3-large": (0.18, 0.0),
    "voyage-3": (0.06, 0.0),
}

# USD per image, by model and size. gpt-image-1 bills by output token and the
# quality defaults to 'auto' (high for these sizes), so the high-quality
# figure is the honest estimate when the call does not say otherwise.
_IMAGE_PRICES: dict[str, dict[str, float]] = {
    "gpt-image-1": {
        "1024x1024": 0.167,
        "1024x1536": 0.25,
        "1536x1024": 0.25,
        "auto": 0.167,
    },
    "dall-e-3": {"1024x1024": 0.04, "1024x1792": 0.08, "1792x1024": 0.08},
}
_IMAGE_DEFAULT_USD = 0.167

# USD per RESULT for the pay-per-result Apify actors (a result = one dataset
# item the actor returned, i.e. one post). These are ESTIMATES from the actor
# listings as noted in competitor_apify / competitor_media, taken at the top
# of each quoted range so the cap trips early rather than late; the Apify
# console is the truth. Keyed by actor id in the `user~name` form the code
# calls (a `user/name` spelling is normalised to it).
_RESULT_PRICES: dict[str, float] = {
    "apify~instagram-scraper": 0.0027,            # "$1.50-$2.70 per 1,000 posts"
    "streamers~youtube-scraper": 0.004,           # "~$0.003-0.004 a video"
    "apimaestro~linkedin-company-posts": 0.005,   # "$0.005 a post"
    "apimaestro~linkedin-profile-posts": 0.005,   # "$0.005 a post"
    # No figure for this actor is recorded anywhere in this codebase; this is
    # a conservative placeholder, not a quote. Correct it from the console.
    "clockworks~tiktok-scraper": 0.005,
}


def estimate_results_usd(actor: str, n: int) -> float | None:
    """USD estimate for `n` results from a pay-per-result actor, or None when
    the actor is not in the table (recorded unpriced, never guessed)."""
    per = _RESULT_PRICES.get((actor or "").strip().lower().replace("/", "~"))
    if per is None:
        return None
    return round(per * max(0, int(n or 0)), 6)


def _match(model: str) -> str | None:
    m = (model or "").lower()
    best = None
    for key in _TOKEN_PRICES:
        if m.startswith(key) and (best is None or len(key) > len(best)):
            best = key
    return best


def estimate_tokens_usd(model: str, tokens_in: int, tokens_out: int) -> float | None:
    """USD estimate for a token-billed call, or None when the model is not in
    the table. None is deliberate: an unknown model is recorded with est_usd 0
    and meta.priced=false, not priced by a guess that looks like a fact."""
    key = _match(model)
    if key is None:
        return None
    pin, pout = _TOKEN_PRICES[key]
    return round((max(0, int(tokens_in or 0)) * pin + max(0, int(tokens_out or 0)) * pout) / 1_000_000, 6)


def estimate_image_usd(model: str, size: str = "", n: int = 1) -> float:
    table = _IMAGE_PRICES.get((model or "").lower())
    if table is None:
        per = _IMAGE_DEFAULT_USD
    else:
        per = table.get((size or "auto").lower(), table.get("auto", _IMAGE_DEFAULT_USD))
    return round(per * max(1, int(n or 1)), 6)


# ───────────────────────────────────────────────────────── job scope ──

# The job or agent currently spending. Set by the scheduler, autopilot and
# agent runners so a row written deep inside llm.py (which only knows it is
# "complete_json") still names the job that caused it.
_job: contextvars.ContextVar[str | None] = contextvars.ContextVar("spend_job", default=None)

# The brand a scoped job is spending FOR. Scheduler handlers run with no
# request context, so every llm.complete_json / vision call inside a
# competitor_vision or brand_research job used to fall through to the default
# tenant: the busy brand's cap never tripped on its own runaway, and the
# operator's brand absorbed everyone's spend until ITS cap blocked its own
# jobs. Kept here, not in db._request_tenant, so binding the ledger does not
# change what a handler's platform-level bare acquire() resolves to.
_tenant: contextvars.ContextVar[UUID | None] = contextvars.ContextVar("spend_tenant", default=None)


@contextmanager
def job_scope(name: str, tenant_id: UUID | None = None):
    token = _job.set(name)
    t_token = _tenant.set(tenant_id) if tenant_id is not None else None
    try:
        yield
    finally:
        if t_token is not None:
            _tenant.reset(t_token)
        _job.reset(token)


def current_job() -> str | None:
    return _job.get()


def current_tenant() -> UUID | None:
    return _tenant.get()


# ───────────────────────────────────────────────────────── recording ──

def _resolve_tenant(tenant_id: UUID | None) -> tuple[UUID, bool]:
    """(tenant, fell_back_to_default): explicit arg → the job scope's tenant →
    the request contextvar → the default tenant, flagged so the row can say
    honestly when it could not tell whose spend this was."""
    resolved = tenant_id or _tenant.get() or _db._request_tenant.get()
    if resolved is None:
        return settings.default_tenant_id, True
    return resolved, False


async def record(
    provider: str,
    model: str,
    units: float,
    unit_kind: str,
    est_usd: float | None,
    agent_or_job: str,
    meta: dict[str, Any] | None = None,
    *,
    tenant_id: UUID | None = None,
) -> bool:
    """Write one ledger row. Returns True on success, False on any failure —
    and NEVER raises. The call that spent the money already succeeded; losing
    the receipt is a logged problem, not a reason to fail the caller."""
    try:
        tenant, fell_back = _resolve_tenant(tenant_id)
        m = dict(meta or {})
        m.setdefault("estimate", ESTIMATE)
        m["site"] = agent_or_job
        if est_usd is None:
            m["priced"] = False
            est_usd = 0.0
        if fell_back:
            m["tenant_fallback"] = True
        job = _job.get() or agent_or_job
        async with acquire(tenant) as conn:
            await conn.execute(
                "INSERT INTO provider_spend "
                "(provider, model, units, unit_kind, est_usd, agent_or_job, meta) "
                "VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb)",
                str(provider or "")[:40], str(model or "")[:120],
                float(units or 0), str(unit_kind or "")[:20],
                round(float(est_usd), 6), str(job)[:120], json.dumps(m, default=str),
            )
        return True
    except Exception:  # noqa: BLE001 — the ledger must never fail the spender
        logger.warning("spend: could not record %s/%s for %s", provider, model, agent_or_job,
                       exc_info=True)
        return False


def _usage_tokens(usage: Any) -> tuple[int, int]:
    """(input, output) from either SDK's usage object. OpenAI says
    prompt/completion, Anthropic says input/output; both are read so one
    helper serves every call site."""
    if usage is None:
        return 0, 0
    get = (lambda k: usage.get(k)) if isinstance(usage, dict) else (lambda k: getattr(usage, k, None))
    tin = get("input_tokens")
    if tin is None:
        tin = get("prompt_tokens")
    tout = get("output_tokens")
    if tout is None:
        tout = get("completion_tokens")
    try:
        return int(tin or 0), int(tout or 0)
    except (TypeError, ValueError):
        return 0, 0


async def record_tokens(
    provider: str,
    model: str,
    usage: Any,
    agent_or_job: str,
    meta: dict[str, Any] | None = None,
    *,
    tenant_id: UUID | None = None,
) -> bool:
    """Record a token-billed chat/messages call straight from the SDK's
    response.usage. Never raises (see record)."""
    try:
        tin, tout = _usage_tokens(usage)
        m = dict(meta or {})
        m.update({"tokens_in": tin, "tokens_out": tout})
        return await record(
            provider, model, tin + tout, "tokens",
            estimate_tokens_usd(model, tin, tout), agent_or_job, m, tenant_id=tenant_id,
        )
    except Exception:  # noqa: BLE001
        logger.warning("spend: could not record tokens for %s", agent_or_job, exc_info=True)
        return False


async def record_images(
    model: str,
    n: int,
    agent_or_job: str,
    *,
    size: str = "",
    usage: Any = None,
    meta: dict[str, Any] | None = None,
    tenant_id: UUID | None = None,
    provider: str = "openai",
) -> bool:
    """One row PER IMAGE drawn or edited. gpt-image-1 is the single most
    expensive call in the system, so each picture is its own line."""
    ok = True
    try:
        tin, tout = _usage_tokens(usage)
        for _ in range(max(1, int(n or 1))):
            m = dict(meta or {})
            m["size"] = size or "auto"
            if tin or tout:
                m.update({"tokens_in": tin, "tokens_out": tout})
            ok = await record(
                provider, model, 1, "image", estimate_image_usd(model, size, 1),
                agent_or_job, m, tenant_id=tenant_id,
            ) and ok
    except Exception:  # noqa: BLE001
        logger.warning("spend: could not record images for %s", agent_or_job, exc_info=True)
        return False
    return ok


async def record_fallback(
    primary_model: str, fallback_model: str, reason: str, agent_or_job: str = "llm.fallback",
    *, tenant_id: UUID | None = None,
) -> bool:
    """A provider crossing is counted, not priced: the fallback's own call
    records its tokens. The row exists so 'how often is Claude out of credit'
    is a query instead of a hunch."""
    return await record(
        "fallback", f"{primary_model}->{fallback_model}", 1, "crossing", 0.0, agent_or_job,
        {"reason": str(reason)[:200]}, tenant_id=tenant_id,
    )


# ────────────────────────────────────────────────────────── the cap ──

async def set_daily_cap(tenant_id: UUID | None, cap_usd: float | None) -> float:
    """Write (or with None, clear) the brand's own cap at
    tenants.config->'spend'->'daily_cap_usd'. Before this the cap was read
    there and nothing could write it. Returns the cap now in force."""
    async with acquire(tenant_id) as conn:
        if cap_usd is None:
            await conn.execute(
                "UPDATE tenants SET config = jsonb_set(coalesce(config,'{}'::jsonb), '{spend}', "
                "coalesce(config->'spend','{}'::jsonb) - 'daily_cap_usd') "
                "WHERE id = current_setting('app.current_tenant', true)::uuid")
        else:
            await conn.execute(
                "UPDATE tenants SET config = jsonb_set(coalesce(config,'{}'::jsonb), '{spend}', "
                "coalesce(config->'spend','{}'::jsonb) || jsonb_build_object('daily_cap_usd', $1::numeric)) "
                "WHERE id = current_setting('app.current_tenant', true)::uuid",
                round(max(0.0, float(cap_usd)), 2))
    return await daily_cap_usd(tenant_id)


async def daily_cap_usd(tenant_id: UUID | None = None) -> float:
    """The brand's cap from tenants.config->'spend'->'daily_cap_usd', else the
    configured default. A value that is not a number falls back rather than
    disabling the cap."""
    try:
        async with acquire(tenant_id) as conn:
            cfg = await conn.fetchval(
                "SELECT config FROM tenants WHERE id = "
                "current_setting('app.current_tenant', true)::uuid"
            )
        if isinstance(cfg, str):
            cfg = json.loads(cfg)
        raw = ((cfg or {}).get("spend") or {}).get("daily_cap_usd")
        if raw is not None:
            return max(0.0, float(raw))
    except Exception:  # noqa: BLE001
        logger.warning("spend: could not read the tenant cap; using default", exc_info=True)
    return float(settings.spend_daily_cap_usd)


# Reads name the tenant explicitly as well as relying on RLS. The policy is
# the boundary in production (james_app is not a superuser), but a superuser
# connection — the docker bootstrap role, a one-off script — bypasses RLS
# entirely, and a cap computed over EVERY brand's spend would block all of
# them the moment one brand got busy.
_TENANT_FILTER = "tenant_id = current_setting('app.current_tenant', true)::uuid"


async def daily_total_usd(tenant_id: UUID | None = None) -> float:
    """Today's (UTC) estimated spend for the tenant."""
    async with acquire(tenant_id) as conn:
        total = await conn.fetchval(
            f"SELECT coalesce(sum(est_usd), 0) FROM provider_spend WHERE {_TENANT_FILTER} "
            "AND created_at >= date_trunc('day', now() AT TIME ZONE 'UTC') AT TIME ZONE 'UTC'"
        )
    return float(total or 0)


async def cap_status(tenant_id: UUID | None = None) -> dict[str, Any]:
    """{paused, cap_usd, today_usd, over_cap, blocked, reason}. The entry
    points log `reason` when they skip. A ledger read failure is reported as
    not-over-cap with the error in `reason`: the job's own DB work would hit
    the same outage, and a dead ledger must not also stop a healthy system."""
    paused = bool(settings.pause_spend)
    out: dict[str, Any] = {
        "paused": paused, "cap_usd": None, "today_usd": None,
        "over_cap": False, "blocked": paused, "reason": "PAUSE_SPEND is set" if paused else "",
    }
    try:
        cap = await daily_cap_usd(tenant_id)
        today = await daily_total_usd(tenant_id)
        over = cap > 0 and today >= cap
        out.update({"cap_usd": cap, "today_usd": round(today, 6), "over_cap": over})
        if over and not paused:
            out["blocked"] = True
            out["reason"] = f"daily spend cap reached (est ${today:.2f} >= ${cap:.2f})"
    except Exception as e:  # noqa: BLE001
        logger.warning("spend: cap check failed", exc_info=True)
        if not paused:
            out["reason"] = f"cap check unavailable: {type(e).__name__}"
    return out


async def over_cap(tenant_id: UUID | None = None) -> bool:
    """True when the tenant must not spend: PAUSE_SPEND, or today's estimate
    at or above its daily cap. A cap of 0 means 'no cap'."""
    return bool((await cap_status(tenant_id))["blocked"])


# ─────────────────────────────────────────────────────────── report ──

async def report(tenant_id: UUID | None = None, days: int = 7) -> dict[str, Any]:
    """Totals by provider x model x day for the last `days` days, plus today's
    total and the cap. The shape GET /v1/spend returns."""
    d = max(1, min(90, int(days or 7)))
    async with acquire(tenant_id) as conn:
        rows = await conn.fetch(
            "SELECT (created_at AT TIME ZONE 'UTC')::date AS day, provider, model, "
            "       sum(units) AS units, count(*) AS calls, sum(est_usd) AS est_usd "
            "  FROM provider_spend "
            f" WHERE {_TENANT_FILTER} "
            "   AND created_at >= (date_trunc('day', now() AT TIME ZONE 'UTC') "
            "                      - ($1::int - 1) * interval '1 day') AT TIME ZONE 'UTC' "
            " GROUP BY 1, 2, 3 ORDER BY 1 DESC, 6 DESC",
            d,
        )
    status = await cap_status(tenant_id)
    out_rows = [
        {
            "day": r["day"].isoformat(), "provider": r["provider"], "model": r["model"],
            "units": float(r["units"] or 0), "calls": int(r["calls"] or 0),
            "est_usd": round(float(r["est_usd"] or 0), 6),
        }
        for r in rows
    ]
    return {
        "days": d,
        "estimate": ESTIMATE,
        "rows": out_rows,
        "total_usd": round(sum(r["est_usd"] for r in out_rows), 6),
        "today_usd": status["today_usd"],
        "cap_usd": status["cap_usd"],
        "paused": status["paused"],
        "over_cap": status["over_cap"],
    }


__all__ = [
    "ESTIMATE", "record", "record_tokens", "record_images", "record_fallback",
    "estimate_tokens_usd", "estimate_image_usd", "estimate_results_usd", "job_scope", "current_job", "current_tenant",
    "daily_cap_usd", "set_daily_cap", "daily_total_usd", "cap_status", "over_cap", "report",
]
