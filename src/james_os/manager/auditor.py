"""Auditor agent (bm2.0 spec §3.3) — baseline snapshot from connected
accounts, ported from bm2.0 backend/app/agents/auditor.py onto the james-os
substrate.

v0 constraints kept from the donor: 12-month lookback cap (D9); degraded
modes (missing platform, IG demographics floor, missing metrics) become
report NOTES, never hard failures. The connected-account source is the
aggregator profile key in tenants.config['postproxy_profile_key']. When it
serves a platform, metrics come from providers.social (account_analytics +
post_history) and land as source=audited. When a platform cannot be served
(NotImplementedError/RuntimeError — e.g. PostProxy before its first stats
snapshot) — or when no profile key is configured at all — the platform
degrades to a PUBLIC baseline scraped from the brand's OWN handle (taken
from the profile's channels.*/identity fields) via providers.peers, written
source=researched primary=True (D2: 0.8) with data_basis='public_scrape' —
never dressed as audited.

Post history lands as events through the ingestion API (embedded, hence
Ask-retrievable): event_type='document', payload.category='post' — there is
no dedicated post bucket in content.assemble_memory, so these ground drafts
via the uncategorised->facts fallback. Clean brand-authored posts (no
banned-phrase/opener lint hits; density heuristics ignored for the human's
own historical writing) are additionally promoted to payload.category=
'voice_corpus' events tagged origin='audited' — the exact category
content._bucket_of counts as voice — so the voice corpus fills from the
brand's actual written register. Ingestion is idempotent on dedupe_key and
strictly additive: existing exemplars are never superseded or touched.

There is no connected_accounts table on this substrate; the per-platform
baseline snapshot lives in the report and the job_runs output row. No LLM
use — pure computation.
"""

import json
from datetime import datetime, timedelta, timezone
from uuid import UUID

from .. import db
from ..ingestion import ingest_many
from ..models import EventCreate, EventSource
from . import ai_isms, profile, runs
from .contracts import BaselineReport, Citation, FieldWrite, Source
from .providers import get_providers
from .providers.base import Providers, SocialPost

AGENT = "auditor"
LOOKBACK_MONTHS = 12  # D9: aggregator analytics lookback
_ENGAGEMENT_KEYS = ("likes", "comments", "shares", "saves", "reactions")
# lint findings that disqualify a historical post from the voice corpus —
# hard AI-tells only; stylistic density heuristics don't apply to the
# brand's own writing
_EXEMPLAR_DISQUALIFIERS = ("banned phrase", "AI-ism opener")
# platforms a channels.<platform> profile field can put on the fallback path
# (channels.website / channels.local etc. are not social handles)
_SOCIAL_PLATFORMS = {
    "instagram", "facebook", "x", "twitter", "tiktok", "linkedin",
    "youtube", "threads", "pinterest", "bluesky",
}
# clock-skew slack when deciding whether ingest() inserted or deduped
_NEW_EVENT_SLACK = timedelta(seconds=60)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_dt(raw: str) -> datetime | None:
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _engagement(metrics: dict) -> float:
    return float(sum(v for k in _ENGAGEMENT_KEYS if isinstance((v := metrics.get(k)), (int, float))))


def _followers(analytics: dict) -> int | None:
    for key in ("followers", "follower_count", "followersCount"):
        v = analytics.get(key)
        if isinstance(v, (int, float)):
            return int(v)
    return None


def _cadence_per_week(posts: list[SocialPost]) -> float:
    """Posts/week over the observed span, capped at the 12-month window."""
    if not posts:
        return 0.0
    dates = [d for d in (_parse_dt(p.published_at) for p in posts) if d]
    span_days = max((_now() - min(dates)).days, 7) if dates else LOOKBACK_MONTHS * 30
    weeks = min(span_days / 7, 52.0)
    return round(len(posts) / weeks, 2)


def _post_summary(platform: str, engagement: float, post: SocialPost) -> dict:
    return {
        "platform": platform,
        "post_id": post.post_id,
        "url": post.url,
        "published_at": post.published_at,
        "text": post.text[:200],
        "engagement": engagement,
        "metrics": post.metrics,
    }


def _exemplar_clean(text: str) -> bool:
    return not any(
        v.startswith(disq) for v in ai_isms.lint(text) for disq in _EXEMPLAR_DISQUALIFIERS
    )


def _profile_handles(fields: list[dict]) -> dict[str, str]:
    """The brand's OWN handles from the profile envelope (channels.* /
    identity fields — the port of the donor's ConnectedAccount.handle). The
    researcher writes channels.<platform> as {'platform','handle','url'};
    string values (a bare handle or a profile URL) are accepted too."""
    handles: dict[str, str] = {}
    for f in fields:
        key = f["field_key"]
        if not (key.startswith("channels.") or key.startswith("identity.")):
            continue
        v = f["value"].get("v") if isinstance(f.get("value"), dict) else None
        if isinstance(v, dict) and v.get("handle"):
            platform = str(v.get("platform") or key.split(".")[1]).lower()
            if platform in _SOCIAL_PLATFORMS:
                handles.setdefault(platform, str(v["handle"]).lstrip("@"))
        elif isinstance(v, str) and v.strip() and key.startswith("channels."):
            platform = key.split(".")[1].lower()
            if platform in _SOCIAL_PLATFORMS:
                handle = v.strip().rstrip("/").rsplit("/", 1)[-1].lstrip("@")
                if handle:
                    handles.setdefault(platform, handle)
    return handles


# ------------------------------------------------------------- events


def _post_events(platform: str, posts: list[SocialPost]) -> list[EventCreate]:
    """Post history -> events (embedded => Ask-retrievable). category='post'
    grounds content drafts through the facts bucket; dedupe on the stable
    (platform, post_id) identity so re-audits never duplicate."""
    events: list[EventCreate] = []
    for p in posts:
        if not p.text:
            continue
        events.append(
            EventCreate(
                event_type="document",
                payload={
                    "text": p.text,
                    "category": "post",
                    "platform": platform,
                    "post_id": p.post_id,
                    "url": p.url,
                    "published_at": p.published_at,
                    "metrics": p.metrics,
                },
                raw_content=p.text,
                source=EventSource(
                    adapter=f"auditor:{platform}",
                    uri=p.url or None,
                    dedupe_key=f"post-{platform}-{p.post_id}",
                    raw_metadata={"category": "post", "platform": platform},
                ),
                entities=["category:post", f"platform:{platform}"],
                effective_at=_parse_dt(p.published_at),
            )
        )
    return events


def _exemplar_events(platform: str, posts: list[SocialPost]) -> list[EventCreate]:
    """D6/D7: promote clean brand-authored posts into the voice corpus —
    category='voice_corpus' is the exact category content._bucket_of counts
    as voice; origin='audited' marks provenance. payload.source is left
    unset so these join the cadence-sample pool, never impersonating an
    approved_exemplar. Purely additive (own dedupe keys, no supersede)."""
    events: list[EventCreate] = []
    for p in posts:
        if not p.text or not _exemplar_clean(p.text):
            continue
        events.append(
            EventCreate(
                event_type="note",
                payload={
                    "text": p.text,
                    "category": "voice_corpus",
                    "origin": "audited",
                    "platform": platform,
                    "post_id": p.post_id,
                    "published_at": p.published_at,
                },
                raw_content=p.text,
                source=EventSource(
                    adapter="auditor_promotion",
                    uri=p.url or None,
                    dedupe_key=f"exemplar-{platform}-{p.post_id}",
                    raw_metadata={"category": "voice_corpus", "origin": "audited"},
                ),
                entities=["category:voice_corpus", f"platform:{platform}"],
                effective_at=_parse_dt(p.published_at),
            )
        )
    return events


async def _ingest_new(
    events: list[EventCreate], tenant_id: UUID | None, started: datetime
) -> int:
    """Ingest through the idempotent API; count only rows actually created
    by this run (dedupe returns pre-existing rows with old created_at)."""
    if not events:
        return 0
    inserted = await ingest_many(events, tenant_id=tenant_id)
    floor = started - _NEW_EVENT_SLACK
    return sum(1 for e in inserted if e.created_at and e.created_at >= floor)


# ------------------------------------------------------------- field writes


async def _write_channel_fields(
    tenant_id: UUID | None,
    platform: str,
    metrics: dict,
    has_posts: bool,
    cite: list[Citation],
    source: Source,
    primary: bool,
) -> None:
    """Baseline metrics -> profile_fields channels.* (donor field keys kept
    verbatim) via the profile service — the only profile_fields writer."""
    writes: list[tuple[str, object]] = [
        (f"channels.{platform}.cadence_per_week", metrics["cadence_per_week"])
    ]
    if metrics["followers"] is not None:
        writes.append((f"channels.{platform}.followers", metrics["followers"]))
    if has_posts:
        writes.append((f"channels.{platform}.avg_engagement", metrics["avg_engagement"]))
    if metrics["engagement_rate"] is not None:
        writes.append((f"channels.{platform}.engagement_rate", metrics["engagement_rate"]))
    if metrics.get("data_basis"):
        # honest provenance on the envelope itself for the public fallback
        writes.append((f"channels.{platform}.data_basis", metrics["data_basis"]))
    async with db.acquire(tenant_id) as conn:
        for field_key, value in writes:
            await profile.write_field(
                conn,
                FieldWrite(
                    section="channels",
                    field_key=field_key,
                    value=value,
                    source=source,
                    primary=primary,
                    citations=cite,
                    updated_by=AGENT,
                ),
            )


def _score(posts: list[SocialPost]) -> tuple[list[tuple[float, SocialPost]], float]:
    scored = sorted(((_engagement(p.metrics), p) for p in posts), key=lambda t: t[0], reverse=True)
    avg = round(sum(s for s, _ in scored) / len(scored), 2) if scored else 0.0
    return scored, avg


# ------------------------------------------------------------- audit paths


async def _audit_platform(
    providers: Providers,
    tenant_id: UUID | None,
    profile_key: str,
    platform: str,
    handle: str,
    report: BaselineReport,
    started: datetime,
) -> tuple[int, int]:
    """Connector-served path (source=audited). Returns (posts_imported,
    exemplars_promoted); appends degraded-mode notes."""
    analytics = await providers.social.account_analytics(profile_key, platform)
    posts = await providers.social.post_history(profile_key, platform, months=LOOKBACK_MONTHS)

    followers = _followers(analytics)
    if followers is None:
        report.notes.append(f"{platform}: follower count unavailable from aggregator analytics")
    demographics = analytics.get("demographics") or {}
    if platform == "instagram" and not demographics:
        report.notes.append(
            "instagram: audience demographics unavailable (Instagram requires >=100 followers "
            "and >=100 engagements/30d before exposing demographics — D9)"
        )
    if not posts:
        report.notes.append(f"{platform}: no posts returned in the {LOOKBACK_MONTHS}-month window")

    scored, avg_engagement = _score(posts)
    engagement_rate = round(avg_engagement / followers, 4) if followers else None
    if followers is not None and followers == 0:
        report.notes.append(f"{platform}: zero followers — engagement rate undefined")
    cadence = _cadence_per_week(posts)
    top5 = [_post_summary(platform, s, p) for s, p in scored[:5]]
    bottom5 = [_post_summary(platform, s, p) for s, p in scored[::-1][:5]]

    metrics = {
        "handle": handle,
        "followers": followers,
        "cadence_per_week": cadence,
        "avg_engagement": avg_engagement,
        "engagement_rate": engagement_rate,
        "posts_last_12m": len(posts),
    }
    report.platforms[platform] = metrics
    report.top_posts.extend(top5)
    report.bottom_posts.extend(bottom5)

    cite = [
        Citation(
            ref=f"connected_account:{platform}:@{handle}" if handle else f"connected_account:{platform}",
            note="aggregator analytics snapshot",
        )
    ]
    await _write_channel_fields(
        tenant_id, platform, metrics, bool(posts), cite, Source.AUDITED, primary=False
    )
    if demographics:
        async with db.acquire(tenant_id) as conn:
            await profile.write_field(
                conn,
                FieldWrite(
                    section="audience",
                    field_key=f"audience.current.{platform}",
                    value=demographics,
                    source=Source.AUDITED,
                    citations=cite,
                    updated_by=AGENT,
                ),
            )

    imported = await _ingest_new(_post_events(platform, posts), tenant_id, started)
    promoted = await _ingest_new(_exemplar_events(platform, posts), tenant_id, started)
    return imported, promoted


async def _public_baseline(
    providers: Providers,
    tenant_id: UUID | None,
    platform: str,
    handle: str,
    report: BaselineReport,
    started: datetime,
) -> int:
    """Public-data fallback when the aggregator can't serve a platform (or no
    profile key is configured): scrape the brand's OWN handle via the
    peer-data provider. Same metric shape as the audited path, but every
    field is written source=RESEARCHED with primary=True (own property) —
    confidence 0.8 per D2, reflecting that this is a public scrape, not
    authenticated account data. Posts still land in memory (category='post',
    brand-authored either way); exemplar promotion is skipped — the public
    copy's completeness/register is unverified vs the connector's
    authoritative text."""
    prof = await providers.peers.profile(platform, handle)
    posts = await providers.peers.recent_posts(platform, handle, limit=50)
    cutoff = _now() - timedelta(days=round(LOOKBACK_MONTHS * 30.44))
    posts = [p for p in posts if (d := _parse_dt(p.published_at)) is None or d >= cutoff]

    followers = prof.followers
    scored, avg_engagement = _score(posts)
    engagement_rate = round(avg_engagement / followers, 4) if followers else None
    cadence = _cadence_per_week(posts)
    top5 = [_post_summary(platform, s, p) for s, p in scored[:5]]
    bottom5 = [_post_summary(platform, s, p) for s, p in scored[::-1][:5]]
    if not posts:
        report.notes.append(f"{platform}: public fallback found no recent posts for @{handle}")

    metrics = {
        "handle": handle,
        "followers": followers,
        "cadence_per_week": cadence,
        "avg_engagement": avg_engagement,
        "engagement_rate": engagement_rate,
        "posts_last_12m": len(posts),
        "data_basis": "public_scrape",  # vs the audited path's aggregator data
    }
    report.platforms[platform] = metrics
    report.top_posts.extend(top5)
    report.bottom_posts.extend(bottom5)

    cite = [
        Citation(
            ref=f"public-profile:{platform}:@{handle}",
            note="public profile scrape (aggregator analytics unavailable)",
        )
    ]
    await _write_channel_fields(
        tenant_id, platform, metrics, bool(posts), cite, Source.RESEARCHED, primary=True
    )

    return await _ingest_new(_post_events(platform, posts), tenant_id, started)


# ------------------------------------------------------------- entrypoint


async def run(tenant_id: UUID | None = None, config: dict | None = None) -> dict:
    providers = get_providers()
    handle = await runs.start_run(
        AGENT, trigger=(config or {}).get("trigger", "manual"), tenant_id=tenant_id
    )
    started = _now()
    try:
        async with db.acquire(tenant_id) as conn:
            row = await conn.fetchrow(
                "SELECT current_setting('app.current_tenant', true) AS tenant, "
                "(SELECT config FROM tenants "
                " WHERE id = current_setting('app.current_tenant', true)::uuid) AS config"
            )
            cfg = row["config"] if row else None
            if isinstance(cfg, str):
                cfg = json.loads(cfg or "{}")
            profile_key = str((cfg or {}).get("postproxy_profile_key") or "")
            fields = await profile.current_fields(conn)

        report = BaselineReport(
            brand_id=str((row["tenant"] if row else "") or tenant_id or ""), captured_at=started
        )
        own_handles = _profile_handles(fields)

        # platform roster: aggregator accounts when a profile key is bound,
        # else the brand's own handles from the profile envelope
        accounts: list[tuple[str, str]] = []
        if profile_key:
            try:
                accounts = [
                    (a.platform, a.handle) for a in await providers.social.list_accounts(profile_key)
                ]
            except Exception as exc:  # noqa: BLE001 — degraded mode, not run failure (D9)
                report.notes.append(
                    f"aggregator account listing failed ({exc}); "
                    "falling back to the profile's own channel handles"
                )
        else:
            report.notes.append(
                "no postproxy_profile_key in tenant config; baseline degrades to public "
                "profile data from the brand's own handles (researched, not audited)"
            )
        connector_served = bool(accounts)
        if not accounts:
            accounts = sorted(own_handles.items())
        if not accounts:
            report.notes.append(
                "no connected accounts and no channel handles in the profile; baseline is empty"
            )

        posts_imported = 0
        exemplars_promoted = 0
        seen: set[str] = set()
        for platform, acct_handle in accounts:
            if platform in seen:
                continue
            seen.add(platform)
            acct_handle = acct_handle or own_handles.get(platform, "")
            if profile_key and connector_served:
                try:
                    imported, promoted = await _audit_platform(
                        providers, tenant_id, profile_key, platform, acct_handle, report, started
                    )
                    posts_imported += imported
                    exemplars_promoted += promoted
                    continue
                except (NotImplementedError, RuntimeError) as exc:
                    # Connector can't provide authenticated data for this
                    # platform (e.g. PostProxy pre-first-snapshot). Degrade to
                    # a PUBLIC baseline from the brand's own handle — honest
                    # about being a public scrape, never dressed as audited.
                    report.notes.append(
                        f"{platform} (@{acct_handle}): aggregator data unavailable ({exc}); "
                        "falling back to public profile data (researched, not audited)"
                    )
                except Exception as exc:  # noqa: BLE001 — degraded mode, not run failure (D9)
                    report.notes.append(
                        f"{platform} (@{acct_handle}): analytics fetch failed ({exc}); "
                        "platform missing from baseline"
                    )
                    continue
            if not acct_handle:
                report.notes.append(
                    f"{platform}: no handle available for the public fallback; platform skipped"
                )
                continue
            try:
                posts_imported += await _public_baseline(
                    providers, tenant_id, platform, acct_handle, report, started
                )
            except Exception as fb_exc:  # noqa: BLE001 — degraded mode, not run failure (D9)
                report.notes.append(
                    f"{platform} (@{acct_handle}): public fallback also failed "
                    f"({fb_exc}); platform missing from baseline"
                )
    except Exception as exc:
        await runs.finish_run(handle, error=str(exc))
        raise
    await runs.finish_run(
        handle,
        output={
            "platforms": report.platforms,
            "posts_imported": posts_imported,
            "exemplars_promoted": exemplars_promoted,
            "notes": report.notes,
        },
    )
    return {
        **report.model_dump(mode="json"),
        "posts_imported": posts_imported,
        "exemplars_promoted": exemplars_promoted,
    }
