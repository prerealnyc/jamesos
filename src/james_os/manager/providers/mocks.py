"""Deterministic mock providers (D8): APP_ENV=mock runs the platform with
zero API keys. Also the demo safety net.

Constraints:
- no network, no randomness, no clocks — same inputs always yield same outputs
- unknown queries/handles fall back to generic-but-coherent results, never KeyError
- all payloads are deep-copied out of fixtures so callers can mutate safely
- LLM routing is the JSON_ROUTES / TEXT_ROUTES tables below; extend by
  appending a row, first match wins
"""

import copy
import re
import zlib
from collections.abc import Callable
from datetime import datetime, timedelta

from . import fixtures
from .base import (
    ChannelOverview,
    DeepResearchResult,
    NewsItem,
    PageContent,
    PlaceInfo,
    Providers,
    PublishResult,
    SearchResult,
    SocialPost,
    SocialProfile,
    TranscriptResult,
    VideoRef,
    WikiPage,
)
from .. import runs

# ------------------------------------------------------------------ matching


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "unknown"


def _crc(text: str) -> int:
    return zlib.crc32(text.encode("utf-8"))


def match_brand(*texts: str) -> dict | None:
    """Fuzzy fixture lookup: alias substring match over normalized inputs."""
    blob = " ".join(_norm(t) for t in texts if t)
    for brand in fixtures.BRANDS.values():
        for alias in brand["aliases"]:
            if _norm(alias) in blob:
                return brand
    return None


def _rank_results(results: list[dict], query: str) -> list[dict]:
    """Angle-aware ordering: results whose 'boost' terms intersect the query's
    tokens come first (stable otherwise), so each researcher angle query
    ("<name> competitors|products services|reviews|about") surfaces its themed
    fixtures within num."""
    tokens = set(_norm(query).split())
    order = sorted(
        range(len(results)),
        key=lambda i: (-len(tokens & set(results[i].get("boost", ()))), i),
    )
    return [results[i] for i in order]


def _find_account(platform: str, handle: str) -> dict | None:
    """Own accounts and tracked peers across all brand fixtures."""
    h = _norm(handle).replace(" ", "")
    for brand in fixtures.BRANDS.values():
        for acc in brand["accounts"].values():
            if _norm(acc["handle"]).replace(" ", "") == h and acc["platform"] == platform:
                return acc
        peer = brand["peers"].get(h)
        if peer is not None:
            return peer
    return None


def _find_posts(platform: str, handle: str) -> list[dict] | None:
    h = _norm(handle).replace(" ", "")
    for brand in fixtures.BRANDS.values():
        for acc_platform, acc in brand["accounts"].items():
            if _norm(acc["handle"]).replace(" ", "") == h and acc_platform == platform:
                return brand["posts"].get(acc_platform)
        if h in brand["peer_posts"]:
            return brand["peer_posts"][h]
    return None


def _post(d: dict) -> SocialPost:
    return SocialPost(
        platform=d["platform"],
        post_id=d["post_id"],
        url=d["url"],
        text=d["text"],
        published_at=d["published_at"],
        metrics=copy.deepcopy(d["metrics"]),
    )


def _profile(d: dict) -> SocialProfile:
    return SocialProfile(
        platform=d["platform"],
        handle=d["handle"],
        display_name=d.get("display_name", ""),
        followers=d.get("followers"),
        bio=d.get("bio", ""),
        meta=copy.deepcopy(d.get("meta", {})),
    )


def _infer_platform(platform: str) -> str:
    """Unknown-peer platform inference: onboarding seeds PeerEntity rows with
    platform='unknown' (D4); default them to instagram, the primary channel."""
    p = (platform or "").strip().lower()
    return p if p and p not in ("unknown", "web") else "instagram"


def _generic_numbers(platform: str, handle: str) -> tuple[int, int, int]:
    """(followers, posts_per_month, base_likes) for a non-fixture account:
    deterministic but PLAUSIBLE — an unknown aspirational peer lands in the
    20k-500k follower band with a like rate near 2%, not CRC noise."""
    seed = _crc(f"{platform}:{_slug(handle)}")
    followers = 20_000 + seed % 480_000
    per_month = 3 + seed % 4  # 3-6 posts/month
    base_likes = max(followers // 50, 100)
    return followers, per_month, base_likes


def _generic_posts(platform: str, handle: str) -> list[dict]:
    followers, per_month, base_likes = _generic_numbers(platform, handle)
    return fixtures.build_posts(
        platform,
        _slug(handle),
        fixtures.GENERIC_CAPTIONS,
        followers=followers,
        base_likes=base_likes,
        like_spread=max(base_likes // 2, 40),
        per_month=per_month,
    )


# ------------------------------------------------------------------ providers


_SITE_RE = re.compile(r"site:([a-z0-9.-]+)", re.IGNORECASE)


class MockSearchProvider:
    """site:<domain> queries (the D11 reddit lane runs site:reddit.com per
    D9) are honored: results are filtered to urls containing the domain's
    first label, so a site-restricted search never returns off-site urls."""

    async def search(self, query: str, num: int = 10) -> list[SearchResult]:
        site = _SITE_RE.search(query)
        label = site.group(1).split(".")[0].lower() if site else ""
        brand = match_brand(query)
        if brand is not None:
            ranked = _rank_results(brand["search_results"], query)
            if label:
                ranked = [r for r in ranked if label in r["url"]]
            return [
                SearchResult(title=r["title"], url=r["url"], snippet=r["snippet"], source="mock")
                for r in ranked[:num]
            ]
        if label:
            s = _slug(_SITE_RE.sub("", query).strip() or query)
            return [
                SearchResult(
                    title=f"Community mention: {_SITE_RE.sub('', query).strip() or query}",
                    url=f"https://{label}.example/search/{s}",
                    snippet=f"Community-level mention matching the site-restricted query. "
                    "[Synthetic mock result.]",
                    source="mock",
                )
            ][:num]
        s = _slug(query)
        results = [
            SearchResult(
                title=f"{query} — overview",
                url=f"https://www.example.com/{s}",
                snippet=f"General information about {query}. [Synthetic mock result — no fixture matched.]",
                source="mock",
            ),
            SearchResult(
                title=f"{query} | directory listing",
                url=f"https://directory.example/{s}",
                snippet=f"Listing page for {query} with basic contact and category details. [Synthetic mock result.]",
                source="mock",
            ),
            SearchResult(
                title=f"News and mentions: {query}",
                url=f"https://news.example/search/{s}",
                snippet=f"Recent coverage mentioning {query}. [Synthetic mock result.]",
                source="mock",
            ),
        ]
        return results[:num]


class MockScrapeProvider:
    async def scrape(self, url: str) -> PageContent:
        for brand in fixtures.BRANDS.values():
            page = brand["pages"].get(url) or brand["pages"].get(url.rstrip("/") + "/")
            if page is not None:
                return PageContent(url=url, title=page["title"], text=page["text"], meta=copy.deepcopy(page["meta"]))
        brand = match_brand(url)
        if brand is not None:  # unknown path on a known brand domain -> homepage content
            home = brand["pages"][brand["website"]]
            return PageContent(url=url, title=home["title"], text=home["text"], meta=copy.deepcopy(home["meta"]))
        s = _slug(url.split("//")[-1])
        return PageContent(
            url=url,
            title=f"Mock page: {s}",
            text=f"Synthetic placeholder page content for {url}. No fixture matched this URL; "
            "the mock scrape provider returns coherent filler so pipelines keep moving.",
            meta={"synthetic": True, "kind": "generic"},
        )


class MockNewsProvider:
    async def search(self, query: str, days: int = 30) -> list[NewsItem]:
        cutoff = fixtures.ANCHOR - timedelta(days=days)
        brand = match_brand(query)
        if brand is not None:
            return [
                NewsItem(
                    title=n["title"],
                    url=n["url"],
                    published_at=n["published_at"],
                    source=n["source"],
                    snippet=n["snippet"],
                )
                for n in brand["news"]
                if datetime.fromisoformat(n["published_at"]) >= cutoff
            ]
        s = _slug(query)
        return [
            NewsItem(
                title=f"Local roundup mentions {query}",
                url=f"https://news.example/roundup/{s}",
                published_at=(fixtures.ANCHOR - timedelta(days=min(4, days))).isoformat(),
                source="Mock Wire",
                snippet=f"A routine mention of {query} in a regional roundup. [Synthetic mock item.]",
            )
        ]


class MockPeerDataProvider:
    async def profile(self, platform: str, handle: str) -> SocialProfile:
        acc = _find_account(platform, handle)
        if acc is not None:
            return _profile(acc)
        resolved = _infer_platform(platform)
        h = _slug(handle)  # handles typed with spaces slug cleanly
        followers, _, _ = _generic_numbers(resolved, h)
        return SocialProfile(
            platform=resolved,
            handle=h,
            display_name=handle.replace("@", "").strip().title() or h.replace("-", " ").title(),
            followers=followers,
            bio=f"@{h} on {resolved}.",
            meta={"synthetic": True, "kind": "unknown"},
        )

    async def recent_posts(self, platform: str, handle: str, limit: int = 20) -> list[SocialPost]:
        raw = _find_posts(platform, handle) or _generic_posts(_infer_platform(platform), handle)
        return [_post(p) for p in raw[:limit]]


class MockSocialConnector:
    """Ayrshare-shaped mock. post_history/analytics honor the 12-month cap (D9)."""

    @staticmethod
    def _brand_for_key(profile_key: str) -> dict | None:
        return match_brand(profile_key)

    async def create_profile(self, brand_name: str) -> str:
        return f"mock-profile-{_slug(brand_name)}"

    async def list_groups(self):
        from .base import AggregatorGroup

        # one group per known fixture brand, prefilled with its accounts
        groups = []
        for slug in ("turtleback", "james-prendamano", "spaceport-america"):
            key = f"mock-profile-{slug}"
            groups.append(
                AggregatorGroup(group_id=key, name=slug, accounts=await self.list_accounts(key))
            )
        return groups

    async def connect_url(self, profile_key: str, platform: str = "instagram") -> str:
        return f"https://connect.mock-social.example/{profile_key}?platform={platform}&redirect=app"

    async def list_accounts(self, profile_key: str) -> list[SocialProfile]:
        brand = self._brand_for_key(profile_key)
        if brand is not None:
            return [_profile(acc) for acc in brand["accounts"].values()]
        h = profile_key.removeprefix("mock-profile-") or "unknown"
        seed = _crc(profile_key)
        return [
            SocialProfile(
                platform="instagram",
                handle=h,
                display_name=h.replace("-", " ").title(),
                followers=800 + seed % 4000,
                bio=f"Synthetic mock account for {h} (no fixture matched).",
                meta={"synthetic": True, "kind": "own"},
            )
        ]

    async def account_analytics(self, profile_key: str, platform: str) -> dict:
        brand = self._brand_for_key(profile_key)
        if brand is not None and platform in brand["analytics"]:
            return copy.deepcopy(brand["analytics"][platform])
        seed = _crc(f"{profile_key}:{platform}")
        return {
            "followers": 800 + seed % 4000,
            "posts_last_30d": 2 + seed % 6,
            "avg_engagement_rate": round(0.02 + (seed % 30) / 1000, 4),
            "lookback_months": 12,
            "synthetic": True,
            "note": "generic mock analytics (no fixture matched)",
        }

    async def post_history(self, profile_key: str, platform: str, months: int = 12) -> list[SocialPost]:
        months = max(1, min(months, 12))  # D9: aggregator lookback cap
        cutoff = fixtures.ANCHOR - timedelta(days=round(months * 30.44))
        brand = self._brand_for_key(profile_key)
        raw = (brand or {}).get("posts", {}).get(platform)
        if raw is None:
            raw = _generic_posts(platform, profile_key.removeprefix("mock-profile-"))
        return [_post(p) for p in raw if datetime.fromisoformat(p["published_at"]) >= cutoff]

    async def publish(self, profile_key: str, platforms: list[str], text: str, media_urls: list[str]) -> dict:
        ref = _crc(f"{profile_key}:{text}:{','.join(platforms)}")
        return {
            "status": "success",
            "id": f"mock-post-{ref:08x}",
            "post_ids": [{"platform": p, "id": f"mock-{p}-{ref:08x}"} for p in platforms],
            "profile_key": profile_key,
            "media_count": len(media_urls),
            "scheduled": False,
            "synthetic": True,
        }


class MockTranscriptionProvider:
    async def transcribe(self, media_url: str) -> str:
        brand = match_brand(media_url)
        if brand is not None:
            return brand["transcript"]
        return fixtures.GENERIC["transcript"]

    async def transcribe_source(self, url: str, *, title: str = "") -> TranscriptResult:
        brand = match_brand(url, title)
        text = (brand or fixtures.GENERIC)["transcript"]
        return TranscriptResult(url=url, text=text, method="mock", title=title, note="mock transcript")


# ------------------------------------------- parallel research lanes (D11)


class MockKnowledgeProvider:
    """KnowledgeProvider — Wikipedia-shaped lookups. None is a FINDING, not a
    failure (D11): Turtleback and James are page-less BY DESIGN so the
    missing-page finding drives the 'create a Wikipedia page' recommendation.
    Unknown names also return None (most brands have no page)."""

    async def lookup(self, name: str) -> WikiPage | None:
        brand = match_brand(name)
        page = (brand or {}).get("wiki")
        if page is None:
            return None
        return WikiPage(
            title=page["title"],
            url=page["url"],
            summary=page["summary"],
            facts=copy.deepcopy(page["facts"]),
        )


class MockVideoProvider:
    """VideoProvider — channel-level overview (stats-only, mirroring the D9
    1-unit endpoints). The researcher passes the channel URL it LOCATED via
    web search (D11), so a fixture whose channel url appears in the query
    wins outright — mirroring live, where only a URL resolves; fuzzy name
    match stays as a fallback for other callers. None = no channel found:
    Turtleback's None is an honest gap, not a fixture hole; unknown queries
    are None too."""

    @staticmethod
    def _brand_for(query: str) -> dict | None:
        q = query.strip().rstrip("/").lower()
        for brand in fixtures.BRANDS.values():
            ch = brand.get("channel")
            if ch is not None and ch["url"].rstrip("/").lower() in q:
                return brand
        return match_brand(query)

    async def channel_overview(self, query: str) -> ChannelOverview | None:
        ch = (self._brand_for(query) or {}).get("channel")
        if ch is None:
            return None
        return ChannelOverview(
            platform=ch["platform"],
            channel_id=ch["channel_id"],
            title=ch["title"],
            url=ch["url"],
            subscribers=ch["subscribers"],
            video_count=ch["video_count"],
            recent_titles=list(ch["recent_titles"]),
        )

    async def recent_uploads(self, channel_query: str, limit: int = 6) -> list[VideoRef]:
        ch = (self._brand_for(channel_query) or {}).get("channel")
        if ch is None:
            return []
        base = ch["url"].rstrip("/")
        titles = list(ch.get("recent_titles") or [])
        refs: list[VideoRef] = []
        for i in range(max(0, limit)):
            title = titles[i] if i < len(titles) else f"{ch['title']} — clip {i + 1}"
            vid = f"mock{_crc(base + str(i)) & 0xFFFFFF:06x}"
            refs.append(VideoRef(video_id=vid, url=f"{base}/watch?v={vid}", title=str(title)))
        return refs


# Tokens that make an unknown query "sound like a place". Deliberately
# conservative: a miss returns None (the lane treats that as no listing),
# which is the honest default for non-physical queries.
_PLACE_TOKENS = frozenset({
    "arena", "bakery", "bar", "brewery", "cafe", "campus", "center", "centre",
    "church", "clinic", "club", "coffee", "course", "farm", "gallery", "golf",
    "grill", "gym", "hotel", "inn", "library", "lodge", "market", "museum",
    "park", "pavilion", "ranch", "resort", "restaurant", "salon", "school",
    "shop", "spa", "stadium", "store", "studio", "theater", "theatre", "venue",
    "winery",
})


class MockPlacesProvider:
    """PlacesProvider — physical_asset/institution lane. Known brands use
    their fixture (James, a person, is None BY DESIGN). Unknown queries get a
    plausible generic listing only when the name sounds like a place;
    clearly non-place queries return None."""

    async def place(self, query: str) -> PlaceInfo | None:
        brand = match_brand(query)
        if brand is not None:
            info = brand.get("place")
            if info is None:
                return None
            return PlaceInfo(
                name=info["name"],
                address=info["address"],
                rating=info["rating"],
                reviews_count=info["reviews_count"],
                categories=list(info["categories"]),
                website=info["website"],
                url=info["url"],
            )
        tokens = set(_norm(query).split())
        matched = sorted(tokens & _PLACE_TOKENS)
        if not matched:
            return None
        s = _slug(query)
        seed = _crc(f"place:{s}")  # deterministic-but-plausible, like _generic_numbers
        return PlaceInfo(
            name=" ".join(w.capitalize() for w in _norm(query).split()) or s,
            address=f"{100 + seed % 9800} Example Ave, Springfield, USA",
            rating=round(3.8 + (seed % 10) / 10, 1),
            reviews_count=40 + seed % 420,
            categories=[m.capitalize() for m in matched[:2]],
            website=f"https://www.example.com/{s}",
            url=f"https://maps.example/place/{s}",
        )


class MockDeepResearchProvider:
    """DeepResearchProvider — Perplexity-shaped synthesis assembled from the
    brand fixture's own themes and existing source urls (D11: supplementary
    lane, written at researched-secondary confidence — never primary)."""

    async def research(self, question: str) -> DeepResearchResult:
        brand = match_brand(question)
        if brand is not None:
            deep = brand["deep"]
            return DeepResearchResult(
                question=question,
                synthesis=deep["synthesis"],
                citations=list(deep["citations"]),
            )
        s = _slug(question)
        return DeepResearchResult(
            question=question,
            synthesis=fixtures.GENERIC["deep"]["synthesis"],
            citations=[f"https://www.example.com/{s}", f"https://news.example/search/{s}"],
        )


# ------------------------------------------------------------------ LLM router


def _payload(brand: dict | None, key: str) -> dict:
    src = (brand or {}).get("llm", {})
    return copy.deepcopy(src.get(key, fixtures.GENERIC["llm"][key]))


def _text_payload(brand: dict | None, key: str) -> str:
    src = (brand or {}).get("llm", {})
    return src.get(key, fixtures.GENERIC["llm"][key])


def _brand_id_from(text: str, brand: dict | None) -> str:
    m = re.search(r"brand[_ ]?id\W{0,4}([0-9a-f]{32})", text, re.IGNORECASE)
    if m:
        return m.group(1)
    return (brand or fixtures.GENERIC)["slug"]


def _stamped(text: str, brand: dict | None, key: str) -> dict:
    payload = _payload(brand, key)
    if "brand_id" in payload:
        payload["brand_id"] = _brand_id_from(text, brand)
    return payload


_URL_RE = re.compile(r"https?://[^\s\"'|]+")
_SEED_BRAND_RE = re.compile(r"^brand:\s*(.+?)\s*$", re.IGNORECASE | re.MULTILINE)
_SEED_NAME_RE = re.compile(r'"name":\s*"([^"]+)"')
_TOPIC_RE = re.compile(r"^topic:\s*(.+?)\s*$", re.IGNORECASE | re.MULTILINE)


_CONFIRMED_RE = re.compile(r"^confirmed sources?:\s*(.+)$", re.IGNORECASE | re.MULTILINE)


def _candidate_overlap(brand: dict, text: str) -> bool:
    """The disambiguation contract: fixture fields flow only when the USER'S
    confirmed urls overlap the fixture's primary entity candidate. The
    researcher writes them on a 'Confirmed sources:' prompt line; when that
    line is present the check runs against it alone (material from angle
    searches/news always name-matches the fixture and must not count). No
    line -> fall back to whole-prompt overlap."""
    candidates = brand.get("llm", {}).get("entity_candidates", {}).get("candidates") or []
    urls = (candidates[0].get("urls") if candidates else None) or []
    m = _CONFIRMED_RE.search(text)
    haystack = m.group(1) if m else text
    return any(u in haystack for u in urls)


def _json_entity_candidates(text: str, brand: dict | None) -> dict:
    """{"candidates": [EntityCandidate...]}. Unknown brands: the generic
    candidate is re-stamped with the seed name and the material URLs so the
    'Is this your brand?' step shows a plausible entity, never placeholder
    plumbing text."""
    payload = _payload(brand, "entity_candidates")
    if brand is None:
        name = _SEED_NAME_RE.search(text)
        urls = [u.rstrip(".,;)") for u in _URL_RE.findall(text)]
        deduped = list(dict.fromkeys(urls))[:3]
        for cand in payload.get("candidates", []):
            if name:
                cand["name"] = name.group(1)
                cand["description"] = (
                    f"Closest public-web match for {name.group(1)}, assembled from the search "
                    "results; confirm the sources to continue."
                )
            if deduped:
                cand["urls"] = deduped
    return payload


def _json_field_extraction(text: str, brand: dict | None) -> dict:
    """{"fields": [{section, field_key, item_key, value, citations:[url,...]}]}
    per researcher.PROMPT_EXTRACT — citations are URL STRINGS drawn from the
    batch material (the agent drops any field citing a URL absent from its
    inputs).

    Known brands: the fixture payload is returned ONLY when the batch
    material overlaps the primary candidate's urls (i.e. the user actually
    confirmed sources belonging to the fixture entity), and even then the
    fields are FILTERED to those whose citations appear in the material.
    Confirming bogus URLs therefore runs the generic path instead of
    conjuring the full fixture profile — the confirmed_urls choice visibly
    shapes the output, like grounded extraction would. Unknown brands: stamp
    the first material URL + 'Brand:' line name so generic fields survive
    validation."""
    if brand is not None and _candidate_overlap(brand, text):
        payload = _payload(brand, "field_extraction")
        kept = []
        for field in payload.get("fields", []):
            cited = [u for u in field.get("citations", []) if u in text]
            if cited:
                field["citations"] = cited
                kept.append(field)
        if kept:
            payload["fields"] = kept
            return payload

    payload = copy.deepcopy(fixtures.GENERIC["llm"]["field_extraction"])
    url = _URL_RE.search(text)
    name = _SEED_BRAND_RE.search(text)
    for field in payload.get("fields", []):
        if url:
            field["citations"] = [url.group(0).rstrip(".,;)")]
        if name:
            if field.get("field_key") == "identity.display_name":
                field["value"] = name.group(1)
            elif field.get("field_key") == "identity.positioning":
                field["value"] = (
                    f"{name.group(1)} — an established name in its field with a growing public "
                    "presence; a sharper positioning line is pending source confirmation."
                )
    return payload


_URL_CONT = frozenset("abcdefghijklmnopqrstuvwxyz0123456789/-_%?#=&~")


def _cited_in(url: str, text: str) -> bool:
    """URL-boundary-aware substring check: a citation counts only where it is
    NOT the prefix of a longer url in the material (a homepage citation like
    'https://site.example/' must not match inside 'https://site.example/about',
    or every lane would surface homepage-cited fields)."""
    start = text.find(url)
    while start != -1:
        end = start + len(url)
        if end >= len(text) or text[end].lower() not in _URL_CONT:
            return True
        start = text.find(url, start + 1)
    return False


def _json_lane_findings(text: str, brand: dict | None) -> dict:
    """{"fields": [...]} for the D11 lane-extraction prompts ('lane findings').
    Lane detection is implicit: the brand's lane_findings fixture is filtered
    to fields whose citations appear in the prompt material, so the wikipedia
    lane's material surfaces only the wiki-cited fields, the youtube lane's
    only the channel field, and so on — grounded extraction, same contract as
    _json_field_extraction. Nothing cited -> empty fields (an honest lane,
    e.g. the wiki lane on a page-less brand). Unknown brands: the generic
    synthesis field stamped with the first material URL."""
    if brand is not None:
        payload = _payload(brand, "lane_findings")
        kept = []
        for field in payload.get("fields", []):
            cited = [u for u in field.get("citations", []) if _cited_in(u, text)]
            if cited:
                field["citations"] = cited
                kept.append(field)
        payload["fields"] = kept
        return payload
    payload = copy.deepcopy(fixtures.GENERIC["llm"]["lane_findings"])
    url = _URL_RE.search(text)
    for field in payload.get("fields", []):
        if url:
            field["citations"] = [url.group(0).rstrip(".,;)")]
    return payload


def _json_weekly_plan(text: str, brand: dict | None) -> dict:
    return _stamped(text, brand, "weekly_plan")  # WeeklyPlan (items: PlanItem, predicted_metrics set)


def _json_morning_brief(text: str, brand: dict | None) -> dict:
    return _stamped(text, brand, "morning_brief")  # MorningBrief


def _json_peer_digest(text: str, brand: dict | None) -> dict:
    return _stamped(text, brand, "peer_digest")  # PeerDigest


def _json_review_judge(text: str, brand: dict | None) -> dict:
    return _payload(brand, "review_judge")  # {"score": float 0-1, "notes": str} — reviewer judge contract


def _json_goals(text: str, brand: dict | None) -> dict:
    return _payload(brand, "goals")  # {"goals": [...], "narrative": str}


def _json_press_events(text: str, brand: dict | None) -> dict:
    return _payload(brand, "press_events")  # {"events": [...]}


def _json_growth(text: str, brand: dict | None) -> dict:
    """Growth Intelligence ('driving the growth' prompts)."""
    return {
        "drivers": ["Higher posting cadence on short-form video", "Consistent hook/format templates"],
        "borrow": ["Post short-form daily", "Reuse the top-performing format on your strongest channel"],
        "narrative": "The fastest-growing tracked accounts lean on frequent short-form video with a repeatable format.",
    }


def _json_trends(text: str, brand: dict | None) -> dict:
    """Trend Agent ('trend-watcher' prompts) — cites news urls from the prompt."""
    links = re.findall(r"https?://[^\s)]+", text)[:4]
    m = _SEED_BRAND_RE.search(text)
    name = m.group(1).strip() if m else "the industry"
    return {
        "trends": [{
            "title": f"Ride the current {name} news moment",
            "trend": "A relevant story is gaining attention in the industry right now.",
            "angle": "A timely take connecting the story to the brand's positioning.",
            "content_type": "post", "why": "Timely relevance = reach while the topic is hot.",
            "links": links[:2],
        }],
        "note": "Trending industry news turned into a timely post angle.",
    }


def _json_rejection_rule(text: str, brand: dict | None) -> dict:
    """Queue learning ('never-again rules' prompts): distill the rejection
    reason line into a short avoid-term."""
    m = re.search(r"Reason:\s*([^\n]+)", text, re.IGNORECASE)
    reason = (m.group(1).strip() if m else "").rstrip(".")
    if not reason:
        return {"terms": []}
    return {"terms": [" ".join(reason.split()[:6])]}


def _json_algorithm(text: str, brand: dict | None) -> dict:
    """Algorithm Agent ('platform algorithm analyst' prompts) — one brief per
    platform named in the prompt, citing urls from the supplied results."""
    m = re.search(r"PLATFORMS:\s*([^\n]+)", text, re.IGNORECASE)
    platforms = [p.strip() for p in (m.group(1).split(",") if m else ["instagram"]) if p.strip()][:4]
    links = re.findall(r"https?://[^\s)]+", text)[:6]
    briefs = [{
        "platform": p,
        "summary": f"{p.title()} currently rewards early engagement velocity and native formats; reach follows watch/dwell time.",
        "rules": ["Native formats outrank links", "First-hour engagement decides distribution"],
        "recent_changes": ["Original content weighted above reposts this quarter"],
        "do_now": [f"Post {p} native format at the audience's peak hour and reply to early comments"],
        "links": links[:2],
    } for p in platforms]
    return {"briefs": briefs, "note": "Current platform ranking behavior distilled from fresh coverage."}


def _json_press(text: str, brand: dict | None) -> dict:
    """Press Agent ('press-watcher' / content-worthy prompts)."""
    links = re.findall(r"https?://[^\s)]+", text)[:4]
    return {
        "items": [{
            "title": "Amplify recent coverage",
            "signal": "feature",
            "post": "A short authority post celebrating the coverage and its takeaway.",
            "content_type": "post", "links": links[:2],
        }] if links else [],
        "note": "Content-worthy coverage flagged for amplification." if links else "No content-worthy press found this scan.",
    }


def _json_questions(text: str, brand: dict | None) -> dict:
    """Search-Questions / AEO agent ('answer-engine strategist' prompts)."""
    links = re.findall(r"https?://[^\s)]+", text)[:4]
    m = _SEED_BRAND_RE.search(text)
    name = m.group(1).strip() if m else "the niche"
    return {
        "questions": [{
            "question": f"How do you actually get started with {name}?",
            "content_type": "blog",
            "answer_angle": "A definitive, brand-credible getting-started answer.",
            "why": "High-intent recurring search — owns the answer box + AI citations.",
            "links": links[:2],
        }],
        "note": "High-intent questions turned into authoritative answer content.",
    }


def _json_appearances(text: str, brand: dict | None) -> dict:
    """Appearances agent ('booking strategist' prompts)."""
    links = re.findall(r"https?://[^\s)]+", text)[:4]
    return {
        "appearances": [{
            "title": "Relevant niche podcast with an audience one tier up",
            "kind": "podcast",
            "fit": "Its audience overlaps the brand's target and sits a level above in reach.",
            "approach": "Pitch via the show's public booking/guest page — verify the current contact first.",
            "links": links[:2],
        }] if links else [],
        "note": "Appearance opportunities to reach a bigger relevant audience." if links else "No clear appearance opportunities this scan.",
    }


def _json_content_radar(text: str, brand: dict | None) -> dict:
    """Content Opportunity Radar ('recurring problems' prompts). Pulls reddit
    urls out of the prompt so mock opportunities cite real threads from the
    (mock) search results."""
    links = re.findall(r"https?://[^\s)]*reddit\.com[^\s)]*", text)[:4]
    m = _SEED_BRAND_RE.search(text)
    name = m.group(1).strip() if m else "the niche"
    opps = [
        {"title": f"Address the recurring '{name}' onboarding confusion",
         "problem": "People repeatedly ask how to get started and hit the same confusion.",
         "content_type": "blog", "angle": "A step-by-step getting-started guide answering the top thread questions.",
         "why": "The same question recurs across multiple threads — high-intent search demand.",
         "recurring_count": max(len(links), 2), "reddit_links": links[:3]},
        {"title": "Myth-bust the most-repeated complaint",
         "problem": "A common frustration keeps surfacing unaddressed.",
         "content_type": "post", "angle": "A short, direct post naming the frustration and the real fix.",
         "why": "Recurring frustration = an audience actively looking for an answer.",
         "recurring_count": max(len(links), 2), "reddit_links": links[1:4]},
    ]
    return {"opportunities": opps, "competitor_themes": ["short-form explainers", "behind-the-scenes"],
            "note": "Recurring problems mined from Reddit; each cites its source threads."}


def _json_daily_plan(text: str, brand: dict | None) -> dict:
    """Daily Strategist ('daily plan' prompts)."""
    m = _SEED_BRAND_RE.search(text)
    name = m.group(1).strip() if m else "the brand"
    return {
        "headline": f"Today: move {name} toward its goals on its strongest channel.",
        "activities": [
            {"title": "Post one short-form video on the top channel", "type": "content",
             "detail": "Reuse the best-performing format on a core topic.", "why": "Closes the cadence + engagement gap to goal."},
            {"title": "Send 3 collaboration/outreach notes", "type": "outreach",
             "detail": "Contact approved peers via their public paths.", "why": "Borrow audience from the aspirational tier."},
            {"title": "Draft one authority piece for the dormant channel", "type": "experiment",
             "detail": "Test a weekly series on the underused platform.", "why": "Activates the channel the goal targets."},
        ],
    }


_DISCOVERY_BUCKETS = (
    ("leader", "instagram", "the established top-of-industry benchmark"),
    ("aspirational", "youtube", "a realistically reachable next tier to grow into"),
    ("collaborator", "instagram", "a same-level peer worth partnering with"),
    ("competitor", "tiktok", "a direct competitor chasing the same audience"),
)


def _json_peer_discovery(text: str, brand: dict | None) -> dict:
    """{"candidates": [{name, handle, platform, kind, reason}]} for the Peer
    Discovery agent ('competitor discovery' prompts). Known brands with a
    'peer_discovery' fixture roster use it; otherwise a deterministic, plausible
    candidate set is synthesized across all four relationship buckets from the
    seed 'Brand:' line so mock discovery always returns an approvable, grouped
    list (the discover→approve→track flow is exercisable with no API keys)."""
    roster = (brand or {}).get("llm", {}).get("peer_discovery")
    if isinstance(roster, dict) and isinstance(roster.get("candidates"), list):
        return copy.deepcopy(roster)
    m = _SEED_BRAND_RE.search(text)
    name = m.group(1).strip() if m else "the brand"
    base = re.sub(r"[^a-z0-9]+", "", name.lower())[:12] or "brand"
    candidates = []
    for kind, platform, why in _DISCOVERY_BUCKETS:
        for j in (1, 2):
            candidates.append({
                "name": f"{name} {kind.capitalize()} {j}",
                "handle": f"{base}{kind[:4]}{j}",
                "platform": platform,
                "kind": kind,
                "reason": f"Surfaced as {why} for {name}.",
            })
    return {"candidates": candidates}


def _json_collaboration(text: str, brand: dict | None) -> dict:
    """{"plays":[...], "visibility_plays":[...], "note":str} for the
    Collaboration agent ('collaboration plays' prompts). One play per tracked
    peer parsed from the prompt's peer lines, plus a visibility floor — so the
    discover→approve→collaborate flow is exercisable with no API keys."""
    plays = []
    for line in text.splitlines():
        mt = re.search(r"@(\S+?),\s*\S+,\s*kind=(\w+)", line)
        if not mt:
            continue
        handle, kind = mt.group(1), mt.group(2)
        play_by_kind = {
            "leader": ("endorsement", f"Earn a mention or feature from @{handle} to borrow their credibility."),
            "aspirational": ("guest_appearance", f"Pitch a guest slot on @{handle}'s channel to borrow their audience upward."),
            "collaborator": ("co_creation", f"Co-create a joint piece (podcast swap or shared series) with @{handle}."),
            "competitor": ("monitor", f"Monitor @{handle}; only co-market if a genuinely mutual moment appears."),
        }
        ptype, play = play_by_kind.get(kind, play_by_kind["collaborator"])
        plays.append({
            "peer_handle": handle,
            "kind": kind,
            "mutual_interest": "Overlapping target audience and complementary content strengths.",
            "play_type": ptype,
            "play": play,
            "outreach_path": "Business inquiry via their site + a warm DM follow-up",
            "outreach_confidence": "medium",
        })
    m = _SEED_BRAND_RE.search(text)
    name = m.group(1).strip() if m else "the brand"
    visibility = [
        {"title": "Signature content series", "action": f"Ship a weekly series on {name}'s core topics for its exact audience.", "why": "A steady, ownable cadence is the baseline visibility lever."},
        {"title": "Search & answer presence", "action": "Publish transcript/answer pages so the brand surfaces in search and AI answers.", "why": "Compounding discovery that doesn't depend on any partner."},
        {"title": "Community seeding", "action": "Engage where the target audience already gathers (relevant subs, groups, events).", "why": "Direct reach to the exact audience without gatekeepers."},
    ]
    note = (
        "No approved collaboration targets yet — focus on the visibility plays."
        if not plays else "Collaboration plays are leads to verify before outreach."
    )
    return {"plays": plays, "visibility_plays": visibility, "note": note}


def _json_voice_profile(text: str, brand: dict | None) -> dict:
    """Deterministic voice profile distilled from the (mock) samples — mirrors
    the shape the live content-tier LLM returns for the harvester."""
    b = brand or fixtures.GENERIC
    name = b.get("name") or b.get("slug", "the brand")
    return {
        "register": "conversational expert",
        "tone": "direct, candid, numbers-first",
        "sentence_style": "short, punchy sentences anchored to concrete specifics",
        "signature_phrases": ["here's the truth", "let's underwrite it", "the numbers don't lie"],
        "vocabulary": ["underwrite", "on the ground", "real deal"],
        "dos": ["lead with a specific number or example", "tell a real story from the work"],
        "donts": ["hype and superlatives", "vague platitudes"],
        "summary": (
            f"{name} sounds like a seasoned operator on the mic: plain-spoken, specific, and unafraid "
            "to challenge the conventional take. Lead with a real number or story, keep it grounded."
        ),
    }


# ---------------------------------------------------------- text handlers


def _clean_topic(text: str) -> str | None:
    """'Topic: ...' line from a drafting/revision prompt, de-dashed so the
    produced copy stays clean under the reviewer's em-dash density lint."""
    m = _TOPIC_RE.search(text)
    if not m:
        return None
    topic = m.group(1).strip().rstrip(".")
    return topic.replace("—", "-").replace("--", "-")


def _fill_template(brand: dict | None, key: str, topic: str) -> str:
    templates = (brand or {}).get("llm", {}).get(key) or fixtures.GENERIC["llm"][key]
    tpl = templates[_crc(topic) % len(templates)]
    return tpl.format(topic=topic[0].upper() + topic[1:] if topic else topic)


def _text_brief(text: str, brand: dict | None) -> str:
    return _text_payload(brand, "brief_text")


def _text_plan_narrative(text: str, brand: dict | None) -> str:
    return _text_payload(brand, "plan_narrative")


def _text_draft_post(text: str, brand: dict | None) -> str:
    """Publish-ready, TOPIC-SPECIFIC copy for the Creator ('draft the post'
    prompts) — real post copy in the brand fixture's register, never
    meta-filler that describes a post instead of being one."""
    topic = _clean_topic(text)
    if topic:
        return _fill_template(brand, "draft_templates", topic)
    return _text_payload(brand, "draft_post")


def _text_revise_draft(text: str, brand: dict | None) -> str:
    """Reviewer D7 revise loop ('revise this draft' prompts): a cleaned,
    still-topic-specific rewrite guaranteed free of AI-ism lint."""
    topic = _clean_topic(text)
    if topic:
        return _fill_template(brand, "revise_templates", topic)
    return _text_payload(brand, "draft_post")


def _text_draft_blog(text: str, brand: dict | None) -> str:
    """A blog 'hand' draft — a clean, topic-specific article body (no em-dashes
    or AI-isms so it clears the D7 lint on first pass, matching the real
    content-tier LLM output shape)."""
    topic = _clean_topic(text) or "the topic"
    t = topic[0].upper() + topic[1:]
    return (
        f"{t}: a straight answer\n\n"
        f"Most advice on {topic} skips the part that matters: the numbers. This is the practical "
        f"version, written for people who want to act, not just read.\n\n"
        f"Start with the fundamentals. Know your inputs before you make a single move on {topic}. "
        f"Write them down and check them against a real example you can point to.\n\n"
        f"Then pressure-test the plan. Run the case where things go wrong and see whether {topic} still "
        f"holds up. If it does, you have something you can actually use.\n\n"
        f"The takeaway is simple. Treat {topic} as a repeatable process, not a slogan. Do the work "
        f"once and it keeps paying off every time after."
    )


def _text_draft_email(text: str, brand: dict | None) -> str:
    """An email/newsletter 'hand' draft — a short, clean body (subject is set
    separately by the hand from the topic)."""
    topic = _clean_topic(text) or "this week"
    return (
        f"Hi there,\n\n"
        f"A quick note on {topic}. People ask about it constantly, so here is the straight answer.\n\n"
        f"The key point: {topic} is simpler than it looks once you focus on the few numbers that "
        f"actually matter. Skip the hype and run your own case.\n\n"
        f"Want the full breakdown? Reply to this email and I will send the worked example.\n\n"
        f"Talk soon."
    )


# Routing tables. Matched against lowercased system+prompt; FIRST match wins.
# To add a route: write a handler (text, brand)->payload and append a row.
JSON_ROUTES: list[tuple[str, tuple[str, ...], Callable[[str, dict | None], dict]]] = [
    # lane_findings MUST precede field_extraction: the D11 lane prompts reuse
    # PROMPT_EXTRACT wording, so both marker sets can appear in one prompt.
    ("lane_findings", ("lane findings",), _json_lane_findings),
    ("entity_candidates", ("entity candidates", "entity-resolution", "is this your brand", "disambiguat"), _json_entity_candidates),
    ("field_extraction", ("extract profile fields", "field extraction", "extract fields", "researchable fields"), _json_field_extraction),
    ("weekly_plan", ("weekly plan",), _json_weekly_plan),
    ("morning_brief", ("morning brief",), _json_morning_brief),
    ("peer_discovery", ("competitor discovery",), _json_peer_discovery),
    ("collaboration", ("collaboration plays",), _json_collaboration),
    # the loop routes precede peer_digest: goal/daily prompts carry goal
    # rationale text that can mention "peer benchmark(s)" (peer_digest's marker),
    # so the more specific loop markers must win first.
    ("goals", ("goal negotiation", "negotiate goals", "growth targets"), _json_goals),
    ("growth", ("driving the growth",), _json_growth),
    ("fact_check", ("unsupported claims",), lambda text, brand: {"unsupported_claims": []}),
    ("rejection_rule", ("never-again rules", "never-again rule"), _json_rejection_rule),
    ("algorithm", ("platform algorithm analyst", "algorithm brief per platform"), _json_algorithm),
    ("trends", ("trend-watcher", "trends this brand should post"), _json_trends),
    ("press", ("press-watcher", "content-worthy press"), _json_press),
    ("questions", ("answer-engine strategist", "authoritative answer content"), _json_questions),
    ("appearances", ("booking strategist", "appearance opportunities"), _json_appearances),
    ("content_radar", ("recurring problems", "content strategist who finds"), _json_content_radar),
    ("daily_plan", ("today's activities", "daily plan"), _json_daily_plan),
    ("voice_profile", ("brand voice analyst", "describe the brand voice"), _json_voice_profile),
    ("peer_digest", ("peer digest", "peer benchmark"), _json_peer_digest),
    ("review_judge", ("voice fidelity", "voice score", "judge the draft", "score this draft"), _json_review_judge),
    ("press_events", ("press event", "content-worthy", "classify mention"), _json_press_events),
]

TEXT_ROUTES: list[tuple[str, tuple[str, ...], Callable[[str, dict | None], str]]] = [
    ("revise_draft", ("revise this draft",), _text_revise_draft),
    # the hands' markers precede draft_post: a blog/email prompt also names its
    # topic, so the more specific hand marker must win first.
    ("draft_blog", ("draft the blog",), _text_draft_blog),
    ("draft_email", ("draft the email",), _text_draft_email),
    ("draft_post", ("draft the post", "caption", "draft a post", "write a post", "social post", "post copy"), _text_draft_post),
    ("brief_text", ("morning brief",), _text_brief),
    ("plan_narrative", ("weekly plan",), _text_plan_narrative),
]


class MockLLMRouter:
    """Canned, structurally valid completions routed on prompt substrings.
    Deterministic token accounting (len/4) accumulates on the instance AND on
    the active AgentRun (bound by services.runs.start_run), mirroring the
    live router's usage accrual."""

    def __init__(self) -> None:
        self.tokens_in = 0
        self.tokens_out = 0
        self.calls: list[dict] = []

    def _account(self, tier: str, route: str, system: str, prompt: str, out: str) -> None:
        tin, tout = (len(system) + len(prompt)) // 4, len(out) // 4
        self.tokens_in += tin
        self.tokens_out += tout
        self.calls.append({"tier": tier, "route": route, "tokens_in": tin, "tokens_out": tout})
        runs.add_usage(tokens_in=tin, tokens_out=tout)

    async def complete(self, tier: str, system: str, prompt: str, max_tokens: int = 2000) -> str:
        text = f"{system}\n{prompt}".lower()
        brand = match_brand(text)
        for key, markers, handler in TEXT_ROUTES:
            if any(m in text for m in markers):
                out = handler(f"{system}\n{prompt}", brand)
                self._account(tier, key, system, prompt, out)
                return out
        out = (
            f"[mock:{tier}] Synthetic completion (no text route matched; extend TEXT_ROUTES in "
            f"james_os/manager/providers/mocks.py). Prompt intent: {prompt.strip()[:120]}"
        )
        self._account(tier, "default", system, prompt, out)
        return out

    async def complete_json(self, tier: str, system: str, prompt: str, max_tokens: int = 4000) -> dict:
        text = f"{system}\n{prompt}".lower()
        brand = match_brand(text)
        for name, markers, handler in JSON_ROUTES:
            if any(m in text for m in markers):
                out = handler(f"{system}\n{prompt}", brand)
                self._account(tier, name, system, prompt, str(out))
                return out
        out = {
            "note": "mock default route: no marker matched; extend JSON_ROUTES in james_os/manager/providers/mocks.py",
            "matched_brand": (brand or fixtures.GENERIC)["slug"],
            "items": [],
        }
        self._account(tier, "default", system, prompt, str(out))
        return out


# ------------------------------------------------- execution 'hands' (mock)


def _slug(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return (s or "post")[:60]


class MockEmailProvider:
    """Deterministic email 'hand': records the send and returns a fake message
    id. No real email ever leaves in mock/dev — the owner sees an honest note."""

    async def send(
        self, *, to: list[str], subject: str, html: str, from_name: str = "", preheader: str = ""
    ) -> PublishResult:
        return PublishResult(
            ok=True,
            ref=f"mock-email-{_crc(subject + '|' + ''.join(to)):08x}",
            url="",
            detail={
                "to": to,
                "subject": subject,
                "chars": len(html),
                "note": "mock send — no real email dispatched (add RESEND_API_KEY to go live)",
            },
            provider="mock",
        )


class MockBlogProvider:
    """Deterministic blog 'hand': returns a fake hosted URL so the loop is
    demonstrable end-to-end without a configured publishing surface."""

    async def publish(
        self, *, title: str, body_markdown: str, slug: str = "", meta: dict | None = None
    ) -> PublishResult:
        s = slug or _slug(title)
        return PublishResult(
            ok=True,
            ref=f"mock-blog-{_crc(s):08x}",
            url=f"https://blog.local/mock/{s}",
            detail={
                "title": title,
                "slug": s,
                "words": len(body_markdown.split()),
                "note": "mock publish — hosted blog surface not configured (set BLOG_PUBLISH_URL to go live)",
            },
            provider="mock",
        )


# ------------------------------------------------------------------ wiring


def mock_providers() -> Providers:
    return Providers(
        search=MockSearchProvider(),
        scrape=MockScrapeProvider(),
        news=MockNewsProvider(),
        peers=MockPeerDataProvider(),
        social=MockSocialConnector(),
        transcription=MockTranscriptionProvider(),
        llm=MockLLMRouter(),
        wiki=MockKnowledgeProvider(),
        video=MockVideoProvider(),
        places=MockPlacesProvider(),
        deep=MockDeepResearchProvider(),
        email=MockEmailProvider(),
        blog=MockBlogProvider(),
    )
