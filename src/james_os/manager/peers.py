"""Peer intelligence — bm2.0's discover → approve → track pair as ONE module,
ported onto the james-os substrate (the ledger's research_roster FILL).

Ported from bm2.0 backend/app/agents/{peer_discovery,peer}.py. The donor's
PeerEntity table maps onto the per-tenant watchlist (tenants.config
['watchlist'], via trends.get_watchlist/set_watchlist): each entry carries
{handle, platform, display_name, status, kind, reason, discovered_at}.
Entries that pre-date the merge (no 'status' key) count as 'tracked' — they
were curated by hand, which IS the human gate.

Lifecycle (Section 6, D4 semantics preserved):
- discover() proposes relationship-bucketed candidates from the public web,
  status='candidate'. It never marks anything tracked — a human approves
  (set_status → 'tracked') or rejects (→ 'rejected'); rejected entries are
  never snapshotted and, like every existing entry, block re-discovery.
- snapshot_all() monitors ONLY tracked entries: per-peer failure isolation,
  rows into peer_snapshots (052_strategy.sql), benchmark fields into the
  profile envelope as source=derived cited to the snapshots, and a PeerDigest
  report with plain computed observations — no LLM.

Constraints kept from the donors: web access only through the provider layer
(D8); one LLM 'extract' call buckets discovery candidates; confidence is
computed by the profile service (D2), never model-reported; every run is a
job_runs row.
"""

import json
import re
from collections import Counter
from datetime import datetime, timedelta, timezone
from uuid import UUID

from .. import db
from ..trends import get_watchlist, set_watchlist
from . import profile, runs
from .contracts import Citation, FieldWrite, PeerDigest, Source
from .providers import Providers, get_providers
from .providers.base import SearchResult, SocialPost

AGENT_DISCOVERY = "peer_discovery"
AGENT_TRACK = "peer"

VALID_KINDS = ("leader", "aspirational", "collaborator", "competitor")
VALID_STATUSES = ("candidate", "tracked", "rejected")
_MAX_SEARCH_RESULTS = 24
_BATCH_CHARS = 20000

PROMPT_SYSTEM = (
    "You are a competitive-intelligence researcher performing competitor "
    "discovery. You cluster public web signals into the specific accounts, "
    "creators, and organizations that matter to a brand's competitive set."
)
PROMPT_DISCOVER = (
    "Perform competitor discovery for the brand described below. Using ONLY "
    "the search material provided, propose the accounts that populate the "
    "brand's competitive landscape, sorted into four relationship buckets:\n"
    "- leader: an established top-of-the-industry name (the benchmark others "
    "measure against)\n"
    "- aspirational: a realistically-reachable next tier the brand could "
    "become — a level up, not out of reach\n"
    "- collaborator: a peer at a similar level worth partnering with\n"
    "- competitor: a direct competitor going after the same audience\n\n"
    "Aim for roughly 2-4 entries per bucket. Prefer real, nameable accounts "
    "grounded in the material; skip a bucket rather than invent filler.\n\n"
    'Return JSON only: {"candidates": [{"name": str, "handle": str, '
    '"platform": str, "kind": str, "reason": str}]} where kind is one of '
    "leader|aspirational|collaborator|competitor, handle is the social handle "
    "(no leading @; empty string if unknown), platform is the primary "
    "platform lowercased (empty string if unknown), and reason is ONE "
    "sentence explaining why this account belongs in that bucket."
)


# ── shared helpers ───────────────────────────────────────────────────────────


def _handle_norm(text: str) -> str:
    """Alnum-only lowercase key — matches bm2.0 onboarding._handle_norm so
    'Virgin Galactic' dedupes against a 'virgingalactic' entry."""
    return re.sub(r"[^a-z0-9]+", "", str(text).lower())


def _status(entry: dict) -> str:
    """Entries that pre-date the merge (no status field) count as tracked."""
    return str(entry.get("status") or "tracked")


def _entry_keys(entry: dict) -> set[str]:
    """Normalized dedupe keys for one watchlist entry: handle AND any display
    label — candidate, tracked, or rejected all block re-adding (donor rule)."""
    keys = {_handle_norm(entry.get("handle") or "")}
    for label in (entry.get("display_name"), entry.get("name")):
        if label:
            keys.add(_handle_norm(label))
    keys.discard("")
    return keys


# ── discovery (from bm2.0 peer_discovery.py) ────────────────────────────────


def _fields_map(rows: list[dict]) -> dict[str, object]:
    """Latest current value per field_key (list items collapse to a list)."""
    out: dict[str, object] = {}
    for row in rows:
        value = row["value"].get("v") if isinstance(row["value"], dict) else row["value"]
        if value in (None, "", [], {}):
            continue
        if row["item_key"] is not None:
            bucket = out.setdefault(row["field_key"], [])
            if isinstance(bucket, list):
                bucket.append(value)
        else:
            out.setdefault(row["field_key"], value)
    return out


def _profile_context(fields: dict[str, object]) -> tuple[str, list[str]]:
    """Human-readable brand brief for the prompt + the search seed terms."""
    name = str(fields.get("identity.display_name") or "")
    entity_type = str(fields.get("identity.entity_type") or "")
    niche = fields.get("positioning.niche")
    positioning = (
        fields.get("identity.positioning")
        or fields.get("identity.positioning_one_liner")
        or fields.get("positioning.differentiator")
    )
    pillars = fields.get("positioning.pillar_topics")
    competitors = (
        fields.get("competitors.direct")
        or fields.get("competitors.roster")
        or fields.get("competitors.aspirational")
    )
    lines = [f"Brand: {name}"]
    if entity_type:
        lines.append(f"Entity type: {entity_type}")
    if niche:
        lines.append(f"Niche / industry: {niche}")
    if positioning:
        lines.append(f"Positioning: {positioning}")
    if pillars:
        lines.append(f"Content pillars: {_join(pillars)}")
    if competitors:
        lines.append(f"Competitors already named: {_join(competitors)}")

    seeds: list[str] = []
    if name:
        anchor = str(niche or entity_type or "").strip()
        seeds.append(f"{name} competitors" + (f" {anchor}" if anchor else ""))
    if niche:
        seeds.append(f"top {niche} accounts creators")
        seeds.append(f"leading {niche} brands")
    if pillars:
        first_pillar = pillars[0] if isinstance(pillars, list) and pillars else pillars
        seeds.append(f"best {first_pillar} accounts to follow")
    # dedupe, keep order, drop blanks
    seeds = list(dict.fromkeys(s.strip() for s in seeds if s and s.strip()))
    return "\n".join(lines), seeds[:4]


def _join(value: object) -> str:
    if isinstance(value, (list, tuple)):
        return ", ".join(str(v) for v in value if v not in (None, ""))
    return str(value)


async def discover(tenant_id: UUID | None = None, config: dict | None = None) -> dict:
    """Propose candidate peers onto the watchlist (status='candidate').
    Never marks anything tracked — the human-approval gate is the point."""
    providers = get_providers()
    handle = await runs.start_run(
        AGENT_DISCOVERY, trigger=(config or {}).get("trigger", "manual"), tenant_id=tenant_id
    )
    try:
        result = await _discover(providers, tenant_id)
    except Exception as exc:
        await runs.finish_run(handle, error=str(exc))
        raise
    await runs.finish_run(handle, output={"count": result["count"]})
    return result


async def _discover(providers: Providers, tenant_id: UUID | None) -> dict:
    async with db.acquire(tenant_id) as conn:
        fields = _fields_map(await profile.current_fields(conn))
    brief, seeds = _profile_context(fields)
    name = str(fields.get("identity.display_name") or "")
    if not seeds:
        seeds = [f"{name} competitors"] if name else ["industry leaders"]

    results: list[SearchResult] = []
    for query in seeds:
        try:
            results.extend(await providers.search.search(query, num=8))
        except Exception:  # noqa: BLE001 — one failed search never fails discovery
            continue
    material = "\n".join(
        f"- {r.title} | {r.url} | {r.snippet}" for r in results[:_MAX_SEARCH_RESULTS]
    )

    # Optional synthesized landscape — supplementary, never required.
    synthesis = ""
    try:
        deep = await providers.deep.research(
            f"Who are the leading, aspirational, collaborator, and direct-competitor "
            f"accounts for {name or 'this brand'}?"
        )
        synthesis = (deep.synthesis or "").strip()
    except Exception:  # noqa: BLE001 — deep lane is supplementary
        synthesis = ""

    prompt = (
        f"{PROMPT_DISCOVER}\n\n{brief}\n\nSearch material:\n{material[:_BATCH_CHARS]}"
    )
    if synthesis:
        prompt += f"\n\nSynthesized landscape:\n{synthesis[:_BATCH_CHARS // 2]}"

    data = await providers.llm.complete_json("extract", PROMPT_SYSTEM, prompt)

    # Dedupe against EVERY existing watchlist entry — candidate, tracked, or
    # rejected — by normalized handle AND display name, so a re-run never
    # re-proposes an entity the human already saw.
    watchlist = await get_watchlist(tenant_id)
    existing: set[str] = set()
    for e in watchlist:
        existing |= _entry_keys(e)

    created: list[dict] = []
    now = datetime.now(timezone.utc).isoformat()
    for c in (data.get("candidates") if isinstance(data, dict) else None) or []:
        if not isinstance(c, dict):
            continue
        display_name = str(c.get("name") or "").strip()
        raw_handle = str(c.get("handle") or "").lstrip("@").strip()
        key = _handle_norm(raw_handle) or _handle_norm(display_name)
        if not key or key in existing:
            continue
        kind = str(c.get("kind") or "").strip().lower()
        if kind not in VALID_KINDS:
            kind = "competitor"
        entry = {
            # the watchlist is handle-keyed; a name-only candidate gets its
            # normalized name as the best-guess handle, resolved (like an
            # unknown platform) from the first successful snapshot
            "handle": raw_handle or key,
            "platform": str(c.get("platform") or "").strip().lower(),
            "display_name": display_name,
            "status": "candidate",
            "kind": kind,
            "reason": str(c.get("reason") or "").strip(),
            "discovered_at": now,
        }
        watchlist.append(entry)
        existing.add(key)
        created.append(entry)
    if created:
        await set_watchlist(watchlist, tenant_id)
    return {"candidates": created, "count": len(created)}


# ── tracking (from bm2.0 peer.py) ────────────────────────────────────────────

MAX_CORE_OBSERVATIONS = 6
_ENGAGEMENT_KEYS = ("likes", "comments", "shares", "saves", "reactions")
_HASHTAG = re.compile(r"#(\w+)")
_URL_FORMATS = (("/reel", "reel"), ("/shorts/", "short"), ("watch?v=", "video"), ("/video", "video"), ("/p/", "static"))


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


def _median(values: list[float]) -> float | None:
    vals = sorted(values)
    if not vals:
        return None
    mid = len(vals) // 2
    return round(vals[mid] if len(vals) % 2 else (vals[mid - 1] + vals[mid]) / 2, 2)


def _post_format(post: SocialPost) -> str:
    fmt = post.metrics.get("media_type") or post.metrics.get("format")
    if isinstance(fmt, str) and fmt:
        return fmt.lower()
    url = post.url.lower()
    for token, name in _URL_FORMATS:
        if token in url:
            return name
    return "post"


def _peer_metrics(followers: int | None, posts: list[SocialPost]) -> dict:
    scored = sorted(((_engagement(p.metrics), p) for p in posts), key=lambda t: t[0], reverse=True)
    avg_engagement = round(sum(s for s, _ in scored) / len(scored), 2) if scored else 0.0
    cutoff = _now() - timedelta(days=30)
    dated = [d for d in (_parse_dt(p.published_at) for p in posts) if d]
    posts_last_30d = sum(1 for d in dated if d >= cutoff) if dated else len(posts)
    return {
        "followers": followers,
        "posts_last_30d": posts_last_30d,
        "cadence_per_week": round(posts_last_30d * 7 / 30, 2),
        "avg_engagement": avg_engagement,
        "top_posts": [{"url": p.url, "text": p.text, "metrics": p.metrics} for _, p in scored[:3]],
        "formats": dict(Counter(_post_format(p) for p in posts)),
        "top_topics": [
            t for t, _ in Counter(
                tag.lower() for p in posts for tag in _HASHTAG.findall(p.text)
            ).most_common(3)
        ],
    }


def _benchmarks(snapshots: list[tuple[dict, dict]]) -> dict:
    # 'who leads' benchmark pool: the tier the brand aims at — established
    # leaders and realistically-reachable aspirational peers.
    aspirational = [(e, m) for e, m in snapshots if e.get("kind") in ("leader", "aspirational")]
    peer_set, pool = ("aspirational", aspirational) if aspirational else ("all_tracked", snapshots)
    formats: Counter[str] = Counter()
    for _, m in pool:
        formats.update(m["formats"])
    return {
        "peer_set": peer_set,
        "peer_count": len(pool),
        "median_cadence_per_week": _median([m["cadence_per_week"] for _, m in pool]),
        "median_avg_engagement": _median([m["avg_engagement"] for _, m in pool]),
        "median_followers": _median([float(m["followers"]) for _, m in pool if m["followers"] is not None]),
        "dominant_formats": [f for f, _ in formats.most_common(3)],
        "posts_sampled": sum(formats.values()),
    }


def _observations(
    snapshots: list[tuple[dict, dict]], benchmarks: dict, baselines: dict[str, dict]
) -> list[str]:
    n = benchmarks["peer_count"]
    label = benchmarks["peer_set"]
    obs: list[str] = []
    if benchmarks["dominant_formats"]:
        formats = ", ".join(benchmarks["dominant_formats"])
        obs.append(
            f"Dominant formats across {n} {label} peers ({benchmarks['posts_sampled']} recent posts): {formats}."
        )
    leader_e, leader_m = max(snapshots, key=lambda t: t[1]["avg_engagement"])
    obs.append(
        f"@{leader_e['handle']} ({leader_e.get('platform', '')}) leads tracked peers on engagement: "
        f"avg {leader_m['avg_engagement']}/post at {leader_m['posts_last_30d']} posts/30d."
    )
    top_post = max(
        (p for _, m in snapshots for p in m["top_posts"]),
        key=lambda p: _engagement(p["metrics"]),
        default=None,
    )
    if top_post:
        obs.append(
            f"Top peer post this period (engagement {_engagement(top_post['metrics']):.0f}): "
            f"\"{top_post['text'][:120]}\" — {top_post['url']}"
        )
    if not baselines:
        obs.append(
            "No audited baseline on connected accounts yet — peer benchmarks reported without own-brand comparison; run the Auditor."
        )
    for platform, base in sorted(baselines.items()):
        own_cad, med_cad = base.get("cadence_per_week"), benchmarks["median_cadence_per_week"]
        if own_cad is not None and med_cad is not None:
            obs.append(
                f"Own {platform} cadence {own_cad} posts/week vs {label}-peer median {med_cad} "
                f"({n} peers): gap {round(med_cad - own_cad, 2)}/week."
            )
        own_eng, med_eng = base.get("avg_engagement"), benchmarks["median_avg_engagement"]
        if own_eng is not None and med_eng is not None:
            obs.append(
                f"Own {platform} avg engagement {own_eng}/post vs {label}-peer median {med_eng}/post."
            )
        own_f, med_f = base.get("followers"), benchmarks["median_followers"]
        if own_f and med_f:
            obs.append(
                f"{label.capitalize()}-peer median followers {med_f:.0f} vs own {platform} {own_f} "
                f"({round(med_f / own_f, 1)}x)."
            )
    return obs[:MAX_CORE_OBSERVATIONS]


def _empty_digest(brand_id: str, observations: list[str]) -> PeerDigest:
    now = _now()
    return PeerDigest(
        brand_id=brand_id,
        period=f"{(now - timedelta(days=7)).date().isoformat()}..{now.date().isoformat()}",
        peers=[],
        benchmarks={},
        observations=observations,
    )


def _own_baselines(rows: list[dict]) -> dict[str, dict]:
    """Own-brand per-platform baselines from the profile envelope
    (performance.baseline · item per platform — what the Auditor port lands
    in place of bm2.0's ConnectedAccount.baseline). Empty when no audit has
    run yet; the digest says so honestly (donor rule)."""
    out: dict[str, dict] = {}
    for f in rows:
        if f["field_key"] != "performance.baseline" or not f["item_key"]:
            continue
        value = f["value"].get("v") if isinstance(f["value"], dict) else f["value"]
        if isinstance(value, dict):
            out.setdefault(f["item_key"], value)  # rows are newest-first per key
    return out


async def snapshot_all(tenant_id: UUID | None = None, config: dict | None = None) -> dict:
    """Snapshot every TRACKED watchlist peer (the human gate): peer_snapshots
    rows, derived benchmark profile fields, and the PeerDigest report."""
    providers = get_providers()
    handle = await runs.start_run(
        AGENT_TRACK, trigger=(config or {}).get("trigger", "scheduled"), tenant_id=tenant_id
    )
    try:
        digest, snapshot_count, skipped = await _track(providers, tenant_id)
    except Exception as exc:
        await runs.finish_run(handle, error=str(exc))
        raise
    await runs.finish_run(
        handle,
        output={
            "snapshot_count": snapshot_count,
            "skipped": skipped,
            "benchmarks": digest.benchmarks,
        },
    )
    return digest.model_dump()


async def _track(
    providers: Providers, tenant_id: UUID | None
) -> tuple[PeerDigest, int, list[str]]:
    brand_id = str(tenant_id or "")
    # Only APPROVED peers get monitored (D4): status='tracked' is the
    # human-approval gate. Candidates awaiting review and rejected entries are
    # never fetched.
    watchlist = await get_watchlist(tenant_id)
    tracked = [e for e in watchlist if _status(e) == "tracked"]
    if not tracked:
        # No tracked peers yet is a normal state (nothing approved) — return an
        # empty digest so the dashboard never 500s on refresh.
        return _empty_digest(
            brand_id,
            ["No tracked competitor accounts yet — approve discovered peers or answer the aspirational-peers question."],
        ), 0, []

    snapshots: list[tuple[dict, dict, str]] = []
    skipped: list[str] = []
    backfilled = False
    for entry in tracked:
        peer_handle = str(entry.get("handle") or "")
        platform = str(entry.get("platform") or "")
        try:
            prof = await providers.peers.profile(platform, peer_handle)
            # discovery proposes candidates with platform='' when unknown; the
            # tracker resolves handles to platforms from the provider and
            # backfills the watchlist entry from the first successful snapshot
            if platform in ("", "unknown") and prof.platform not in ("", "unknown"):
                entry["platform"] = platform = prof.platform
                if not entry.get("display_name") and prof.display_name:
                    entry["display_name"] = prof.display_name
                backfilled = True
            posts = await providers.peers.recent_posts(platform, peer_handle, limit=20)
        except Exception as exc:  # noqa: BLE001 — individual peer failure never fails the run
            skipped.append(f"@{peer_handle} ({platform}): fetch failed ({exc}); skipped this cycle")
            continue
        metrics = _peer_metrics(prof.followers, posts)
        sources = [p["url"] for p in metrics["top_posts"] if p.get("url")]
        async with db.acquire(tenant_id) as conn:
            snap_id = await conn.fetchval(
                """INSERT INTO peer_snapshots (peer, platform, stats, sources)
                   VALUES ($1, $2, $3::jsonb, $4::jsonb) RETURNING id""",
                peer_handle,
                platform,
                json.dumps(metrics),
                json.dumps(sources),
            )
        snapshots.append((entry, metrics, str(snap_id)))
    if backfilled:
        await set_watchlist(watchlist, tenant_id)
    if not snapshots:
        # Every peer fetch failed (e.g. unresolved handles) — degrade to an
        # empty digest with the reasons, rather than 500ing the dashboard.
        return _empty_digest(
            brand_id,
            ["Couldn't fetch any competitor data this cycle:"] + skipped,
        ), 0, skipped

    benchmarks = _benchmarks([(e, m) for e, m, _ in snapshots])

    async with db.acquire(tenant_id) as conn:
        for entry, metrics, snap_id in snapshots:
            await profile.write_field(
                conn,
                FieldWrite(
                    section="competitors",
                    field_key="competitors.peer_metrics",
                    item_key=f"{entry.get('platform') or ''}:{entry['handle']}",
                    value={k: metrics[k] for k in ("followers", "posts_last_30d", "cadence_per_week", "avg_engagement")},
                    source=Source.DERIVED,
                    citations=[Citation(ref=f"peer_snapshot:{snap_id}", note="engagement math over public posts")],
                    updated_by=AGENT_TRACK,
                ),
            )
        await profile.write_field(
            conn,
            FieldWrite(
                section="competitors",
                field_key="competitors.benchmarks",
                value=benchmarks,
                source=Source.DERIVED,
                citations=[Citation(ref=f"peer_snapshot:{snap_id}") for _, _, snap_id in snapshots[:5]],
                updated_by=AGENT_TRACK,
            ),
        )
        baselines = _own_baselines(await profile.current_fields(conn, "performance"))

    now = _now()
    digest = PeerDigest(
        brand_id=brand_id,
        period=f"{(now - timedelta(days=7)).date().isoformat()}..{now.date().isoformat()}",
        peers=[
            {
                "handle": e["handle"],
                "platform": e.get("platform", ""),
                "kind": e.get("kind", ""),
                "followers": m["followers"],
                "cadence_per_week": m["cadence_per_week"],
                "avg_engagement": m["avg_engagement"],
                "top_topics": m["top_topics"],
            }
            for e, m, _ in snapshots
        ],
        benchmarks=benchmarks,
        observations=_observations([(e, m) for e, m, _ in snapshots], benchmarks, baselines) + skipped,
    )
    return digest, len(snapshots), skipped


# ── the human gate: candidate review for the API layer ──────────────────────


async def candidates(tenant_id: UUID | None = None) -> list[dict]:
    """Watchlist entries awaiting the human approve/reject decision."""
    return [e for e in await get_watchlist(tenant_id) if _status(e) == "candidate"]


async def set_status(tenant_id: UUID | None, handle: str, status: str) -> dict:
    """Move one watchlist entry through the lifecycle: approve a candidate
    (→ 'tracked', monitored from the next snapshot run) or reject it
    (→ 'rejected', never snapshotted, still blocks re-discovery)."""
    if status not in VALID_STATUSES:
        raise ValueError(f"invalid status '{status}' — expected one of {VALID_STATUSES}")
    key = _handle_norm(handle)
    if not key:
        raise ValueError(f"no watchlist entry for handle '{handle}'")
    watchlist = await get_watchlist(tenant_id)
    for entry in watchlist:
        if key in _entry_keys(entry):
            entry["status"] = status
            await set_watchlist(watchlist, tenant_id)
            return entry
    raise ValueError(f"no watchlist entry for handle '{handle}'")


__all__ = ["discover", "snapshot_all", "candidates", "set_status"]
