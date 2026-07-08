"""Researcher agent (bm2.0 spec §3.1): public-data fan-out drafting
researchable profile fields; powers the "Is this your brand?" onboarding
moment. Ported from bm2.0 backend/app/agents/researcher.py onto the james-os
substrate — this REPLACES the pre-merge single-Perplexity-pass research
(brand_research.py / research.py), whose scheduler jobs are retired per P2.

Constraints (donor rules kept verbatim):
- mode='discover' returns EntityCandidates only — never writes fields (human
  confirms first); mode='confirmed' writes via profile.write_field exclusively
- confirmed mode fans out over named parallel lanes (D11): web, news,
  wikipedia, youtube, reddit, places (physical_asset/institution only), deep.
  Lanes run concurrently with per-lane failure isolation — proceed if >=1
  lane succeeds, raise if all fail; failures surface as '<lane>: <error>'
- structured lanes (wikipedia/youtube/places) map provider payloads to fields
  deterministically — no LLM; free-text lanes (web/news/reddit/deep) each make
  extract calls whose prompts carry '<lane> lane findings' for mock routing
- every written field requires citations; uncited or unverifiable-citation
  extractions are dropped; primary=True only for the brand's own site/accounts
- a missing Wikipedia page is itself a finding: positioning.wikipedia_presence
  = false with ref='wikipedia:not_found' (the Strategist reads it)
- deep lane is supplementary synthesis: always primary=False (D11 secondary
  confidence) and dropped wholesale when the provider returns no citations
- reddit is SERP-level signal only (D9): site:reddit.com via providers.search
- youtube lane locates the channel URL first (a user-confirmed url, else
  site:youtube.com web search) and only then calls providers.video with that
  url — a bare brand name is never guessed into a handle (D9/D11)
- competitors: watchlist candidate entries only, never metrics (D4) — the
  donor's PeerEntity inserts map onto the per-tenant watchlist with
  status='candidate' (peers.py conventions: human approve/reject gate, deduped
  by normalized handle AND display name); the youtube lane writes
  channels.youtube.* only and never proposes a peer
- tenancy comes from the connection (db.acquire binds app.current_tenant;
  RLS scopes every query — the D10 brand-scoping rule on this substrate);
  LLM only via providers.llm tier='extract'
- substrate notes: run bookkeeping is a job_runs row (runs.start_run /
  finish_run commit autonomously, so the donor's commit_running dance is
  unnecessary — the 'running' row is visible to status pollers immediately);
  lanes never touch the DB — they return (fields, notes) and all writes
  happen sequentially after the gather.
"""

import asyncio
import json
from collections.abc import Coroutine
from datetime import datetime, timezone
from urllib.parse import urlparse
from uuid import UUID

from .. import db
from ..trends import get_watchlist, set_watchlist
from . import profile, runs
from .contracts import Citation, EntityCandidate, FieldWrite, ResearchReport, Source
from .peers import _entry_keys, _handle_norm
from .providers import Providers, get_providers
from .providers.base import PageContent, SearchResult

AGENT = "researcher"

RESEARCHABLE_SECTIONS = frozenset({"identity", "positioning", "products", "competitors", "channels"})
SECONDARY_SECTIONS = frozenset({"audience", "positioning"})  # reddit/deep lanes (D11)
PLACES_ENTITY_TYPES = frozenset({"physical_asset", "institution"})
SEARCH_ANGLES: tuple[str, ...] = ("competitors", "products services", "reviews", "about")
REDDIT_QUERY = 'site:reddit.com "{name}"'  # D9: SERP-level only
YOUTUBE_QUERY = 'site:youtube.com "{name}"'  # D11: locate the channel via web search
DEEP_QUESTION = "{name} brand positioning, reputation, and recent activity"
DEEP_MAX_FIELDS = 3
MAX_PAGES = 5
NEWS_LOOKBACK_DAYS = 90
_PAGE_CHARS = 6000
_BATCH_CHARS = 24000

_ENTITY_HINTS: dict[str, str] = {
    "person": "person public figure",
    "company": "company business",
    "physical_asset": "venue location",
    "institution": "institution organization",
}

PROMPT_DISCOVER_SYSTEM = (
    "You are an entity-resolution researcher. You cluster web search results "
    "into distinct real-world entities and score how likely each one matches "
    "the seed brand."
)
PROMPT_DISCOVER = (
    "Cluster the search results below into entity candidates for the seed "
    "brand. Group results referring to the same real-world entity; use only "
    "URLs present in the results. Return JSON only: "
    '{"candidates": [{"name": str, "description": str, "urls": [str], '
    '"score": float}]} where score in [0,1] is the likelihood the candidate '
    "is the seed brand."
)

PROMPT_EXTRACT_SYSTEM = (
    "You are a brand researcher. You extract profile fields strictly grounded "
    "in the provided source material, citing source URLs."
)
PROMPT_EXTRACT = (
    "From the source material below, extract profile fields for the brand. "
    'Return JSON only: {"fields": [{"section": str, "field_key": str, '
    '"item_key": str|null, "value": any, "citations": [url, ...]}]}. Rules: '
    "section must be one of identity|positioning|products|competitors|channels; "
    "field_key is '<section>.<name>' (e.g. identity.positioning_line, "
    "products.offer, channels.website); list-valued fields set item_key to a "
    "stable slug, scalar fields use null; cite ONLY urls that appear in the "
    "material and omit any field you cannot cite; competitor entries use "
    "field_key 'competitors.direct', item_key = handle-or-name slug, value = "
    '{"name": str, "platform": str, "handle": str}.'
)

PROMPT_REDDIT_SYSTEM = (
    "You are a brand researcher reading community discussion signals from "
    "search result snippets. You never invent sentiment; every finding is "
    "grounded in the snippets and cites thread URLs."
)
PROMPT_REDDIT = (
    "From the reddit search results below, produce reddit lane findings: "
    "community-sentiment and mention observations about the brand. Return "
    'JSON only: {"fields": [{"section": str, "field_key": str, "item_key": '
    'str|null, "value": any, "citations": [url, ...]}]}. Rules: section must '
    "be one of audience|positioning; field_key is '<section>.<name>' (e.g. "
    "audience.community_sentiment, positioning.reddit_mentions); cite ONLY "
    "reddit thread urls that appear in the results and omit any finding you "
    "cannot cite."
)

PROMPT_DEEP_SYSTEM = (
    "You are a brand researcher condensing a synthesized research report "
    "into a handful of profile findings."
)
PROMPT_DEEP = (
    "From the research synthesis below, produce at most 3 deep lane findings "
    'for the brand. Return JSON only: {"fields": [{"section": str, '
    '"field_key": str, "item_key": str|null, "value": any}]}. Rules: section '
    "must be one of positioning|audience; field_key is '<section>.<name>' "
    "(e.g. positioning.summary_external); do not include citations — "
    "provenance is attached from the synthesis sources by the caller."
)

# (fields, intra-lane failure notes) — notes get '<lane>: ' prefixed by the collector
_LaneOut = tuple[list[FieldWrite], list[str]]


async def run(
    tenant_id: UUID | None = None,
    *,
    seed: dict,
    mode: str = "discover",
    confirmed_urls: list[str] | None = None,
    config: dict | None = None,
) -> ResearchReport:
    """seed = {name, website?, socials?: [handles], entity_type, location?}.

    Every run is a job_runs row; start_run commits the 'running' row in its
    own transaction, so status pollers (and the double-confirm 409 guard in
    research_api) see it immediately — no commit_running flag needed here."""
    providers = get_providers()
    handle = await runs.start_run(
        AGENT,
        trigger=(config or {}).get("trigger", "onboarding"),
        input={"mode": mode, "seed": seed},
        tenant_id=tenant_id,
    )
    peers_inserted = 0
    lane_stats: dict[str, int] = {}
    try:
        if mode == "discover":
            report = await _discover(providers, str(tenant_id or ""), seed)
        elif mode == "confirmed":
            report, peers_inserted, lane_stats = await _confirmed(
                providers, tenant_id, seed, [str(u) for u in (confirmed_urls or [])]
            )
        else:
            raise ValueError(f"unknown mode: {mode!r}")
    except Exception as exc:
        await runs.finish_run(handle, error=str(exc))
        raise
    await runs.finish_run(
        handle,
        output={
            "mode": mode,
            "candidates": len(report.candidates),
            "fields_written": len(report.fields),
            "peers_inserted": peers_inserted,
            "lane_stats": lane_stats,
            "failures": report.failures,
        },
    )
    return report


# ---------------------------------------------------------------- discover


async def _discover(providers: Providers, brand_id: str, seed: dict) -> ResearchReport:
    name = str(seed.get("name") or "").strip()
    if not name:
        raise ValueError("seed.name is required")
    failures: list[str] = []
    hint = _ENTITY_HINTS.get(str(seed.get("entity_type") or ""), "")
    location = str(seed.get("location") or "").strip()
    queries = [" ".join(part for part in (name, hint, location) if part)]
    if seed.get("website"):
        queries.append(f"{name} {_host(str(seed['website']))}")

    results: list[SearchResult] = []
    for query in queries:
        try:
            results.extend(await providers.search.search(query, num=10))
        except Exception as exc:
            failures.append(f"search[{query}]: {exc}")
    if not results:
        raise RuntimeError(f"discover: all searches failed for {name!r}: {failures}")

    blob = "\n".join(f"- {r.title} | {r.url} | {r.snippet}" for r in results[:20])
    data = await providers.llm.complete_json(
        tier="extract",
        system=PROMPT_DISCOVER_SYSTEM,
        prompt=f"{PROMPT_DISCOVER}\n\nSeed: {json.dumps(seed, default=str)}\n\nSearch results:\n{blob}",
    )
    candidates: list[EntityCandidate] = []
    for c in data.get("candidates", []) if isinstance(data, dict) else []:
        if not isinstance(c, dict) or not c.get("name"):
            continue
        try:
            score = max(0.0, min(1.0, float(c.get("score") or 0.0)))
        except (TypeError, ValueError):
            score = 0.0
        candidates.append(
            EntityCandidate(
                name=str(c["name"]),
                description=str(c.get("description") or ""),
                urls=[str(u) for u in (c.get("urls") or []) if isinstance(u, str) and u.strip()],
                score=score,
            )
        )
    candidates.sort(key=lambda c: c.score, reverse=True)
    return ResearchReport(brand_id=brand_id, candidates=candidates, fields=[], failures=failures)


# ---------------------------------------------------------------- confirmed


async def _confirmed(
    providers: Providers,
    tenant_id: UUID | None,
    seed: dict,
    confirmed_urls: list[str],
) -> tuple[ResearchReport, int, dict[str, int]]:
    """D11 fan-out: named lanes run concurrently and never touch the DB —
    they return (fields, notes); merge, profile writes, and watchlist inserts
    happen here, after gather, so DB access stays sequential."""
    name = str(seed.get("name") or "").strip()
    if not name:
        raise ValueError("seed.name is required")
    hosts, handles = _primary_sets(seed)

    lanes: list[tuple[str, Coroutine]] = [
        ("web", _lane_web(providers, name, seed, confirmed_urls, hosts, handles)),
        ("news", _lane_news(providers, name, confirmed_urls, hosts, handles)),
        ("wikipedia", _lane_wikipedia(providers, name)),
        ("youtube", _lane_youtube(providers, name, confirmed_urls, hosts, handles)),
        ("reddit", _lane_reddit(providers, name, hosts, handles)),
    ]
    if str(seed.get("entity_type") or "") in PLACES_ENTITY_TYPES:
        lanes.append(("places", _lane_places(providers, name, seed)))
    lanes.append(("deep", _lane_deep(providers, name)))

    results = await asyncio.gather(*(coro for _, coro in lanes), return_exceptions=True)
    failures: list[str] = []
    extracted: list[FieldWrite] = []
    lane_stats: dict[str, int] = {}
    lanes_ok = 0
    for (lane, _), result in zip(lanes, results):
        if isinstance(result, BaseException):
            if not isinstance(result, Exception):
                raise result  # cancellation and friends must propagate
            failures.append(f"{lane}: {result}")
            lane_stats[lane] = 0
            continue
        fields, notes = result
        failures.extend(f"{lane}: {note}" for note in notes)
        extracted.extend(fields)
        lane_stats[lane] = len(fields)  # pre-merge contribution (merge dedupes across lanes)
        lanes_ok += 1
    if lanes_ok == 0:
        raise RuntimeError(f"researcher: all lanes failed: {failures}")

    final = _merge_fields(extracted)
    async with db.acquire(tenant_id) as conn:
        for fw in final:
            await profile.write_field(conn, fw)
    peers_inserted = await _insert_peer_candidates(tenant_id, final)
    report = ResearchReport(
        brand_id=str(tenant_id or ""), candidates=[], fields=final, failures=failures
    )
    return report, peers_inserted, lane_stats


# ---------------------------------------------------------------- lanes


async def _lane_web(
    providers: Providers,
    name: str,
    seed: dict,
    confirmed_urls: list[str],
    hosts: set[str],
    handles: set[str],
) -> _LaneOut:
    """The original confirmed-mode search+scrape flow, factored into a lane.
    Two extract batches on purpose (confirmed pages, angle searches): merging
    them would let page material truncate the SERP batch out of the
    _BATCH_CHARS window. The lane fails only when every sub-part fails."""
    notes: list[str] = []
    fields: list[FieldWrite] = []
    parts_attempted = 0
    parts_ok = 0

    page_urls: list[str] = []
    seen: set[str] = set()
    for u in [*confirmed_urls, str(seed.get("website") or "")]:
        if u and _canon(u) not in seen:
            seen.add(_canon(u))
            page_urls.append(u)

    if page_urls:
        parts_attempted += 1
        try:
            pages: list[PageContent] = []
            for u in page_urls[:MAX_PAGES]:
                try:
                    pages.append(await providers.scrape.scrape(_norm_url(u)))
                except Exception as exc:
                    notes.append(f"scrape[{u}]: {exc}")
            if not pages:
                raise RuntimeError("all page scrapes failed")
            material = "\n\n".join(
                f"URL: {p.url}\nTITLE: {p.title}\n{p.text[:_PAGE_CHARS]}" for p in pages
            )
            data = await _extract_batch(
                providers, name, "web lane findings (confirmed pages)", material, confirmed_urls
            )
            fields.extend(_validate_fields(data, hosts, handles, {_canon(p.url) for p in pages}))
            parts_ok += 1
        except Exception as exc:
            notes.append(f"website: {exc}")

    parts_attempted += 1
    try:
        results: list[SearchResult] = []
        angle_failures: list[str] = []
        for angle in SEARCH_ANGLES:
            try:
                results.extend(await providers.search.search(f"{name} {angle}", num=5))
            except Exception as exc:
                angle_failures.append(f"search[{name} {angle}]: {exc}")
        notes.extend(angle_failures)
        if angle_failures and not results:
            raise RuntimeError("all angle searches failed")
        if results:
            material = "\n".join(f"- {r.title} | {r.url} | {r.snippet}" for r in results[:30])
            data = await _extract_batch(
                providers, name, "web lane findings (angle searches)", material, confirmed_urls
            )
            fields.extend(_validate_fields(data, hosts, handles, {_canon(r.url) for r in results}))
        parts_ok += 1
    except Exception as exc:
        notes.append(f"search: {exc}")

    if parts_attempted and parts_ok == 0:
        raise RuntimeError("; ".join(notes) or "all web sub-sources failed")
    return fields, notes


async def _lane_news(
    providers: Providers, name: str, confirmed_urls: list[str], hosts: set[str], handles: set[str]
) -> _LaneOut:
    items = await providers.news.search(name, days=NEWS_LOOKBACK_DAYS)
    if not items:
        return [], []
    material = "\n".join(
        f"- {i.title} | {i.url} | {i.published_at} | {i.source} | {i.snippet}"
        for i in items[:25]
    )
    data = await _extract_batch(providers, name, "news lane findings", material, confirmed_urls)
    return _validate_fields(data, hosts, handles, {_canon(i.url) for i in items}), []


async def _lane_wikipedia(providers: Providers, name: str) -> _LaneOut:
    """Deterministic mapping — structured payload, no LLM. A missing page is
    itself a finding: wikipedia_presence=false is ALWAYS written (D11 — the
    Strategist turns it into the 'create a Wikipedia page' recommendation)."""
    page = await providers.wiki.lookup(name)
    if page is None:
        return [
            _fw(
                "positioning",
                "positioning.wikipedia_presence",
                False,
                [Citation(ref="wikipedia:not_found")],
            )
        ], []
    fields = [
        _fw("positioning", "positioning.wikipedia_presence", True, [Citation(url=page.url)])
    ]
    if page.summary:
        fields.append(
            _fw("identity", "identity.public_summary", page.summary, [Citation(url=page.url)])
        )
    for key, value in (page.facts or {}).items():
        if value in (None, "", [], {}):
            continue
        fields.append(
            _fw("identity", f"identity.{_snake(str(key))}", value, [Citation(url=page.url)])
        )
    return fields, []


async def _lane_youtube(
    providers: Providers, name: str, confirmed_urls: list[str], hosts: set[str], handles: set[str]
) -> _LaneOut:
    """Deterministic channels.youtube.* mapping — never a peer candidate here
    (competitor discovery belongs to the free-text lanes; metrics to the Peer
    Agent, D4). D11 contract: the channel is LOCATED here first — a
    user-confirmed channel url wins, else site:youtube.com web search — and
    only the located url goes to the provider, which stats it with 1-unit
    endpoints (D9: search.list forbidden). A bare brand name is never guessed
    into a handle: the slug could resolve to a STRANGER'S channel and pollute
    the profile. No channel url located -> the lane honestly writes nothing."""
    channel_url = next((u for u in confirmed_urls if _yt_channel_url(u)), "")
    if not channel_url:
        results = await providers.search.search(YOUTUBE_QUERY.format(name=name), num=10)
        channel_url = next((r.url for r in results if _yt_channel_url(r.url)), "")
    if not channel_url:
        return [], []  # no channel located — an honest gap, not an error
    overview = await providers.video.channel_overview(channel_url)
    if overview is None:
        return [], []
    if not overview.url:
        return [], ["no channel url on overview; findings skipped (provenance rule)"]
    primary = _is_primary(overview.url, hosts, handles)
    pairs: list[tuple[str, object]] = [
        ("channels.youtube.channel_id", overview.channel_id),
        ("channels.youtube.url", overview.url),
        ("channels.youtube.title", overview.title),
        ("channels.youtube.subscribers", overview.subscribers),
        ("channels.youtube.video_count", overview.video_count),
        ("channels.youtube.recent_titles", overview.recent_titles),
    ]
    return [
        _fw("channels", key, value, [Citation(url=overview.url)], primary=primary)
        for key, value in pairs
        if value not in (None, "", [])
    ], []


async def _lane_reddit(
    providers: Providers, name: str, hosts: set[str], handles: set[str]
) -> _LaneOut:
    """D9: SERP-level community signal only — never full-thread fetching."""
    results = await providers.search.search(REDDIT_QUERY.format(name=name), num=10)
    if not results:
        return [], []
    material = "\n".join(f"- {r.title} | {r.url} | {r.snippet}" for r in results[:20])
    data = await providers.llm.complete_json(
        tier="extract",
        system=PROMPT_REDDIT_SYSTEM,
        prompt=f"{PROMPT_REDDIT}\n\nBrand: {name}\n\nSearch results:\n{material[:_BATCH_CHARS]}",
    )
    fields = _validate_fields(
        data,
        hosts,
        handles,
        {_canon(r.url) for r in results},
        sections=SECONDARY_SECTIONS,
        force_secondary=True,
    )
    return fields, []


async def _lane_places(providers: Providers, name: str, seed: dict) -> _LaneOut:
    """physical_asset/institution only (gated by the caller): local/maps
    presence mapped deterministically under section 'channels'."""
    location = str(seed.get("location") or "").strip()
    place = await providers.places.place(" ".join(part for part in (name, location) if part))
    if place is None:
        return [], []
    url = place.url or place.website
    if not url:
        return [], ["no maps/service url on result; findings skipped (provenance rule)"]
    pairs: list[tuple[str, object]] = [
        ("channels.local.name", place.name),
        ("channels.local.address", place.address),
        ("channels.local.rating", place.rating),
        ("channels.local.reviews_count", place.reviews_count),
        ("channels.local.categories", place.categories),
        ("channels.local.website", place.website),
    ]
    return [
        _fw("channels", key, value, [Citation(url=url)])
        for key, value in pairs
        if value not in (None, "", [])
    ], []


async def _lane_deep(providers: Providers, name: str) -> _LaneOut:
    """D11: supplementary synthesis, always secondary confidence. Provenance
    rule: a synthesis without citations is skipped wholesale, never written;
    citations come from the provider result, never from the model."""
    result = await providers.deep.research(DEEP_QUESTION.format(name=name))
    citations = [str(u).strip() for u in (result.citations or []) if str(u).strip()]
    if not citations:
        return [], ["no citations returned; findings skipped (provenance rule)"]
    if not result.synthesis.strip():
        return [], []
    sources = "\n".join(f"- {u}" for u in citations)
    data = await providers.llm.complete_json(
        tier="extract",
        system=PROMPT_DEEP_SYSTEM,
        prompt=(
            f"{PROMPT_DEEP}\n\nBrand: {name}\n\nSynthesis sources:\n{sources}\n\n"
            f"Synthesis:\n{result.synthesis[:_BATCH_CHARS]}"
        ),
    )
    items = data.get("fields") if isinstance(data, dict) else None
    for item in items or []:
        if isinstance(item, dict):
            item["citations"] = citations  # provider provenance overrides the model's
    fields = _validate_fields(
        {"fields": list(items or [])},
        set(),
        set(),
        {_canon(u) for u in citations},
        sections=SECONDARY_SECTIONS,
        force_secondary=True,
    )
    return fields[:DEEP_MAX_FIELDS], []


# ---------------------------------------------------------------- extraction


async def _extract_batch(
    providers: Providers, name: str, label: str, material: str, confirmed_urls: list[str]
) -> dict:
    """confirmed_urls ride along in every batch so the extractor knows which
    sources the human vouched for (disambiguation anchor for name collisions).
    label carries '<lane> lane findings' so the mock router can route per lane."""
    confirmed = ", ".join(confirmed_urls) if confirmed_urls else "none"
    return await providers.llm.complete_json(
        tier="extract",
        system=PROMPT_EXTRACT_SYSTEM,
        prompt=(
            f"{PROMPT_EXTRACT}\n\nBrand: {name}\nConfirmed sources: {confirmed}\n"
            f"Source batch: {label}\n\n"
            f"Material:\n{material[:_BATCH_CHARS]}"
        ),
    )


def _validate_fields(
    data: object,
    hosts: set[str],
    handles: set[str],
    known_urls: set[str],
    *,
    sections: frozenset[str] = RESEARCHABLE_SECTIONS,
    force_secondary: bool = False,
) -> list[FieldWrite]:
    """Citations mandatory and restricted to batch-input URLs; drop otherwise.
    sections narrows what a lane may write; force_secondary pins primary=False
    (reddit/deep material is never the brand's own property — D11)."""
    out: list[FieldWrite] = []
    items = data.get("fields") if isinstance(data, dict) else None
    for item in items or []:
        if not isinstance(item, dict):
            continue
        section = str(item.get("section") or "")
        field_key = str(item.get("field_key") or "")
        if not field_key:
            continue
        prefix = field_key.split(".", 1)[0]
        if prefix in sections:
            section = prefix
        elif section in sections:
            field_key = f"{section}.{field_key}"
        else:
            continue
        urls = [
            str(u).strip()
            for u in (item.get("citations") or [])
            if isinstance(u, str) and str(u).strip()
        ]
        urls = [u for u in urls if _canon(u) in known_urls]
        value = item.get("value")
        if not urls or value in (None, "", [], {}):
            continue
        item_key = item.get("item_key")
        out.append(
            FieldWrite(
                section=section,
                field_key=field_key,
                item_key=_slug(str(item_key)) if item_key else None,
                value=value,
                source=Source.RESEARCHED,
                citations=[Citation(url=u) for u in urls],
                primary=not force_secondary and all(_is_primary(u, hosts, handles) for u in urls),
                updated_by=AGENT,
            )
        )
    return out


def _merge_fields(fields: list[FieldWrite]) -> list[FieldWrite]:
    """Cross-batch agreement merges citations (D2 multi-citation bonus —
    profile.write_field's rubric consumes the union automatically);
    disagreement within one run keeps the last batch's value."""
    merged: dict[tuple[str, str | None], FieldWrite] = {}
    for fw in fields:
        key = (fw.field_key, fw.item_key)
        prev = merged.get(key)
        if prev is not None and prev.value == fw.value:
            seen = {c.url for c in prev.citations}
            prev.citations.extend(c for c in fw.citations if c.url not in seen)
            prev.primary = prev.primary or fw.primary
        else:
            merged[key] = fw
    return list(merged.values())


# Legacy/loose research kinds → the locked watchlist kind taxonomy.
_PEER_KIND_MAP = {
    "direct": "competitor",
    "competitor": "competitor",
    "collab": "collaborator",
    "collaborator": "collaborator",
    "aspirational": "aspirational",
    "leader": "leader",
}


async def _insert_peer_candidates(tenant_id: UUID | None, fields: list[FieldWrite]) -> int:
    """D4: candidate entities only — metrics belong to the Peer Agent. The
    donor's PeerEntity inserts land on the per-tenant watchlist instead
    (peers.py conventions): status='candidate' awaiting the human
    approve/reject gate; deduped against EVERY existing entry — candidate,
    tracked, or rejected — by normalized handle AND display name, so research
    never re-proposes an entity the human already saw."""
    candidates = [fw for fw in fields if fw.section == "competitors"]
    if not candidates:
        return 0
    watchlist = await get_watchlist(tenant_id)
    existing: set[str] = set()
    for e in watchlist:
        existing |= _entry_keys(e)
    inserted = 0
    now = datetime.now(timezone.utc).isoformat()
    for fw in candidates:
        value = fw.value if isinstance(fw.value, dict) else {"name": str(fw.value)}
        handle = str(value.get("handle") or "").lstrip("@").strip().lower() or _slug(
            str(value.get("name") or fw.item_key or "")
        )
        if not handle or handle == "item":
            continue
        display_name = str(value.get("name") or "")
        key = _handle_norm(handle) or _handle_norm(display_name)
        if not key or key in existing:
            continue
        # Normalize to the locked watchlist kind taxonomy (leader |
        # aspirational | collaborator | competitor) so researcher-proposed
        # candidates flow through the same discover→approve→track UI/benchmarks
        # as discovery-proposed ones.
        raw_kind = str(value.get("kind") or "").strip().lower()
        entry = {
            "handle": handle,
            "platform": str(value.get("platform") or "web").strip().lower() or "web",
            "display_name": display_name,
            "status": "candidate",
            "kind": _PEER_KIND_MAP.get(raw_kind, "competitor"),
            "reason": "named alongside the brand in confirmed research sources",
            "discovered_at": now,
        }
        watchlist.append(entry)
        existing |= _entry_keys(entry)
        inserted += 1
    if inserted:
        await set_watchlist(watchlist, tenant_id)
    return inserted


# ---------------------------------------------------------------- helpers


def _fw(
    section: str,
    field_key: str,
    value: object,
    citations: list[Citation],
    *,
    primary: bool = False,
) -> FieldWrite:
    """Structured-lane write: source=researched; primary only when the cited
    property is the brand's own (the D2 rubric consumes the flag at write time)."""
    return FieldWrite(
        section=section,
        field_key=field_key,
        item_key=None,
        value=value,
        source=Source.RESEARCHED,
        citations=citations,
        primary=primary,
        updated_by=AGENT,
    )


def _norm_url(url: str) -> str:
    return url if "://" in url else f"https://{url}"


def _canon(url: str) -> str:
    return _norm_url(url).rstrip("/").lower()


def _host(url: str) -> str:
    return urlparse(_norm_url(url)).netloc.lower().removeprefix("www.")


def _yt_channel_url(url: str) -> str | None:
    """url when it carries YouTube CHANNEL identity (/@handle, /channel/id,
    /user/name, /c/name), else None — watch/shorts/search urls identify a
    video or nothing, not a channel. Host check is by first label ('youtube')
    so youtube.com, m.youtube.com and the mock youtube.example all pass."""
    parsed = urlparse(_norm_url(url))
    host = parsed.netloc.lower().removeprefix("www.").removeprefix("m.")
    if host.split(".", 1)[0] != "youtube":
        return None
    parts = [p for p in parsed.path.split("/") if p]
    if parts and parts[0].startswith("@") and len(parts[0]) > 1:
        return url
    if len(parts) >= 2 and parts[0] in ("channel", "user", "c"):
        return url
    return None


def _slug(text: str) -> str:
    parts = "".join(ch if ch.isalnum() else "-" for ch in text.lower()).split("-")
    return "-".join(p for p in parts if p)[:120] or "item"


def _snake(text: str) -> str:
    return _slug(text).replace("-", "_")


def _primary_sets(seed: dict) -> tuple[set[str], set[str]]:
    hosts = {_host(str(seed["website"]))} if seed.get("website") else set()
    handles = {
        str(h).lstrip("@").strip("/").lower()
        for h in (seed.get("socials") or [])
        if h and str(h).strip()
    }
    return hosts, handles


def _is_primary(url: str, hosts: set[str], handles: set[str]) -> bool:
    parsed = urlparse(_norm_url(url))
    if parsed.netloc.lower().removeprefix("www.") in hosts:
        return True
    # handles are stored '@'-stripped; URL path segments may carry it (/@handle)
    parts = {p.lower().lstrip("@") for p in parsed.path.split("/") if p}
    return bool(handles & parts)
