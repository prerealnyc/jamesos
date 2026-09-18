"""Stage 1 of competitor intelligence — niche → the brands that own it.

Answers "who is actually winning in this niche?" with data instead of a
hand-curated list. Xpoz's `get_users_by_keywords` is the right tool and was
never called: it returns creators ranked by relevance to a keyword, each
carrying follower counts AND the engagement their *niche-relevant* posts
earned. That last part matters — it separates "big account that mentioned
real estate once" from "account whose real-estate posts consistently land."

    discover(niche)   → ranked candidates, persisted as status='candidate'
    set_status(id, …) → promote to 'tracked' (syncing) or 'rejected'
    import_watchlist() → adopt the existing curated watchlist as tracked

Platform scope is honest: Instagram, TikTok and X only. Those are the three
where Xpoz exposes both keyword user-discovery AND per-author post pulls, so
a candidate found here can actually be synced in Stage 2. Reddit is
subreddit-shaped rather than brand-shaped and has no per-author post
endpoint, so it is deliberately out.

Degrade-safe like every other provider module here: no key returns a
structured {error: …}, never a raise and never invented competitors.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from .config import settings
from .db import acquire

# Xpoz namespace name → how we label it in the UI.
PLATFORMS = ("instagram", "tiktok", "twitter")
PLATFORM_LABEL = {"instagram": "Instagram", "tiktok": "TikTok", "twitter": "X",
                  "youtube": "YouTube", "linkedin": "LinkedIn"}

# What we can SCRAPE is wider than what we can DISCOVER. Xpoz searches (and
# verifies handles on) its own three; YouTube and LinkedIn come in by name —
# from the brand's approved peers — and are fetched through Apify
# (competitor_apify). Keeping the two lists apart is the point: a B2B agency's
# real presence is LinkedIn and a creator's is YouTube, and dropping those
# peers at the door left their content, their pictures and their layouts out
# of the shelf entirely.
SCRAPE_PLATFORMS = PLATFORMS + ("youtube", "linkedin")

# 'reference' is not a competitor: it is the shelf niche_reference.py keeps for
# high-engagement posts in the niche whose account nobody tracks. It is a status
# of its own precisely because every scraping loop asks for 'tracked' — so a
# shelf with no account to scrape is skipped by all of them without a single
# `if` added to sync, media, analysis or profiles.
STATUSES = ("candidate", "tracked", "rejected", "reference")

# Per-platform: the discovery fields we ask for, and the field names to read
# them back from. Xpoz's shapes differ per platform, so normalisation is a
# table rather than a pile of if/elses.
_DISCOVER_FIELDS: dict[str, list[str]] = {
    "instagram": [
        "username", "full_name", "biography", "follower_count", "following_count",
        "media_count", "is_verified", "profile_url", "profile_pic_url",
        "agg_relevance", "relevant_posts_count", "relevant_posts_likes_sum",
        "relevant_posts_comments_sum", "relevant_posts_video_plays_sum",
    ],
    "tiktok": [
        "username", "nickname", "signature", "follower_count", "following_count",
        "post_count", "is_verified", "avatar",
        "agg_relevance", "relevant_posts_count", "relevant_posts_likes_sum",
        "relevant_posts_comments_sum", "relevant_posts_plays_sum",
    ],
    "twitter": [
        "username", "name", "description", "followers_count", "following_count",
        "tweet_count", "is_verified", "profile_image_url",
        "agg_relevance", "relevant_tweets_count", "relevant_tweets_likes_sum",
        "relevant_tweets_replies_sum", "relevant_tweets_impressions_sum",
    ],
}

_MAP: dict[str, dict[str, str]] = {
    "instagram": {
        "handle": "username", "name": "full_name", "bio": "biography",
        "followers": "follower_count", "following": "following_count",
        "posts_total": "media_count", "verified": "is_verified",
        "avatar": "profile_pic_url", "profile": "profile_url",
        "rel_n": "relevant_posts_count", "rel_likes": "relevant_posts_likes_sum",
        "rel_comments": "relevant_posts_comments_sum",
    },
    "tiktok": {
        "handle": "username", "name": "nickname", "bio": "signature",
        "followers": "follower_count", "following": "following_count",
        "posts_total": "post_count", "verified": "is_verified",
        "avatar": "avatar", "profile": "",
        "rel_n": "relevant_posts_count", "rel_likes": "relevant_posts_likes_sum",
        "rel_comments": "relevant_posts_comments_sum",
    },
    "twitter": {
        "handle": "username", "name": "name", "bio": "description",
        "followers": "followers_count", "following": "following_count",
        "posts_total": "tweet_count", "verified": "is_verified",
        "avatar": "profile_image_url", "profile": "",
        "rel_n": "relevant_tweets_count", "rel_likes": "relevant_tweets_likes_sum",
        "rel_comments": "relevant_tweets_replies_sum",
    },
}

_PROFILE_URL = {
    "instagram": "https://www.instagram.com/{h}/",
    "tiktok": "https://www.tiktok.com/@{h}",
    "twitter": "https://x.com/{h}",
    # Scrape-only platforms (no Xpoz search). A LinkedIn handle may be a
    # company or a person; the company page is the commoner case for a peer,
    # and competitor_apify tries both when it fetches.
    "youtube": "https://www.youtube.com/@{h}",
    "linkedin": "https://www.linkedin.com/company/{h}/",
}


def configured() -> bool:
    return bool((settings.xpoz_api_key or "").strip())


def _g(obj: Any, name: str, default: Any = None) -> Any:
    if not name:
        return default
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _int(v: Any) -> int:
    try:
        if isinstance(v, str):
            v = v.replace(",", "").strip()
        return int(float(v))
    except (TypeError, ValueError):
        return 0


def _rank(followers: int, rel_n: int, rel_eng: int, relevance: float = 0.0) -> float:
    """Rank a candidate by how hard their NICHE posts land PER FOLLOWER.

    Engagement *rate*, not raw engagement. The first cut of this used absolute
    average engagement and immediately surfaced a 43M-follower entertainment
    account over a 77k-follower account that actually covers the niche — raw
    likes measure audience size, which we already know, not whether this
    account's take on the topic resonates.

        engagement rate  avg niche engagement ÷ followers
        focus            ramps to full weight at 10 niche posts, so one lucky
                         viral hit cannot outrank an account that owns the topic
        relevance        Xpoz's own keyword-match confidence, when it supplies one

    Scaled ×1000 purely so the numbers read as small integers rather than
    four leading zeros.
    """
    if followers <= 0 or rel_n <= 0:
        return 0.0
    rate = (rel_eng / rel_n) / followers
    focus = min(1.0, rel_n / 10.0)
    # Relevance is optional and its scale is provider-defined; treat anything
    # outside a sane 0–1 band as "not supplied" rather than trusting it.
    rel_w = relevance if 0.0 < relevance <= 1.0 else 1.0
    return round(rate * focus * rel_w * 1000, 3)


async def _discover_platform(
    client: Any, platform: str, niche: str, limit: int
) -> tuple[str, list[dict], str | None]:
    """One platform's keyword user-search, normalised. Never raises."""
    try:
        ns = getattr(client, platform)
        res = await asyncio.wait_for(
            ns.get_users_by_keywords(
                niche, fields=_DISCOVER_FIELDS[platform], limit=limit
            ),
            timeout=40,
        )
        users = await _page_items(res, limit)
    except TimeoutError:
        return platform, [], "timed out"
    except Exception as e:  # noqa: BLE001 — one platform can't sink the rest
        return platform, [], f"{type(e).__name__}: {e}"

    return platform, [_user_to_candidate(platform, u) for u in users
                      if str(_g(u, _MAP[platform]["handle"], "") or "").strip()], None


def _user_to_candidate(platform: str, u: Any) -> dict:
    """One Xpoz user object → our candidate shape. Shared by keyword discovery
    and by handle verification, so both paths produce identical rows."""
    m = _MAP[platform]
    handle = str(_g(u, m["handle"], "") or "").strip().lstrip("@")
    followers = _int(_g(u, m["followers"]))
    rel_n = _int(_g(u, m["rel_n"]))
    rel_eng = _int(_g(u, m["rel_likes"])) + _int(_g(u, m["rel_comments"]))
    try:
        relevance = float(_g(u, "agg_relevance", 0) or 0)
    except (TypeError, ValueError):
        relevance = 0.0
    profile = str(_g(u, m["profile"], "") or "") or _PROFILE_URL[platform].format(h=handle)
    return {
        "platform": platform,
        "handle": handle,
        "name": str(_g(u, m["name"], "") or ""),
        "bio": str(_g(u, m["bio"], "") or "")[:500],
        "followers": followers,
        "following": _int(_g(u, m["following"])),
        "posts_total": _int(_g(u, m["posts_total"])),
        "verified": bool(_g(u, m["verified"], False)),
        "avatar_url": str(_g(u, m["avatar"], "") or ""),
        "profile_url": profile,
        "niche_posts": rel_n,
        "niche_engagement": rel_eng,
        "relevance": relevance,
        "niche_engagement_rate": round((rel_eng / rel_n) / followers, 6)
        if rel_n > 0 and followers > 0 else 0.0,
        "rank_score": _rank(followers, rel_n, rel_eng, relevance),
    }


async def _page_items(result: Any, limit: int) -> list[Any]:
    """First page of an AsyncPaginatedResult as a plain list. Mirrors
    xpoz_intel._page_items — the SDK returns either a page object or a list."""
    try:
        page = result.get_page()
        if asyncio.iscoroutine(page):
            page = await page
    except Exception:  # noqa: BLE001
        page = result
    if isinstance(page, list):
        items = page
    else:
        items = (_g(page, "items") or _g(page, "data") or _g(page, "results") or [])
    return list(items)[:limit]


def _why(c: dict, niche: str) -> str:
    """A one-line, checkable reason this account made the shortlist. Leads with
    engagement RATE because that is what the ranking is actually built on —
    the number a reader needs to audit the position."""
    if c["niche_posts"] > 0 and c["followers"] > 0:
        avg = int(c["niche_engagement"] / c["niche_posts"])
        pct = c["niche_engagement_rate"] * 100
        return (f"{pct:.2f}% engagement rate on {c['niche_posts']} “{niche}” "
                f"posts ({avg:,} avg on {c['followers']:,} followers)")
    return f"{c['followers']:,} followers · no “{niche}” posts measured yet"


async def discover(
    niche: str = "",
    platforms: list[str] | None = None,
    limit: int = 12,
    min_followers: int = 1000,
    screen: bool = True,
    use_research: bool = True,
    tenant_id: UUID | None = None,
) -> dict:
    """Find the accounts that own `niche`, rank them, and PERSIST every one as
    a candidate. Persisting is the point — the old Xpoz paths threw their
    results away, so nothing ever accumulated.

    Re-running is safe: an existing row keeps its status (a rejected
    competitor stays rejected) and just gets fresh follower numbers.
    """
    # THE NICHE IS THE BRAND'S ANSWER, not a guess made here.
    #
    # It decides which accounts we go and study, so getting it wrong sends
    # every downstream stage — posts, media, vision, profiles — at the wrong
    # industry. A Turtleback Golf Course tenant run against a hand-typed
    # "New York commercial real estate" comes back with Ryan Serhant and
    # CPEX, and nothing later in the pipeline can notice.
    #
    # So when no niche is passed we use the one the brand CONFIRMED during
    # onboarding, and if there isn't one we refuse and say so. Falling back
    # to a proposal, a brand name, or a plausible default would reintroduce
    # exactly the failure this guard exists to prevent.
    niche = (niche or "").strip()
    confirmed_terms: list[str] = []
    if not niche:
        from .brands import get_niche
        n = await get_niche(tenant_id)
        if not n["confirmed"]:
            return {
                "error": "This brand has not confirmed its niche yet. "
                         "Confirm it during onboarding (Brand → niche) and "
                         "run discovery again — competitor research is only "
                         "as good as the niche it starts from.",
                "needs_niche": True,
                "proposed_niche": n.get("proposed") or "",
                "candidates": [],
            }
        niche = n["niche"]
        confirmed_terms = n["terms"]
    if not configured():
        return {"error": "No Xpoz API key configured. Add it under "
                         "Settings → API connections.", "candidates": []}
    plats = [p for p in (platforms or list(PLATFORMS)) if p in PLATFORMS]
    if not plats:
        return {"error": f"No valid platforms. Pick from {', '.join(PLATFORMS)}.",
                "candidates": []}
    try:
        import xpoz
    except ImportError:
        return {"error": "The `xpoz` package isn't installed on the server.",
                "candidates": []}

    # Pull deeper than we keep — the follower floor and dedupe both cull.
    fetch_n = min(50, max(limit * 3, 20))
    found: list[dict] = []
    errors: dict[str, str] = {}
    try:
        async with xpoz.AsyncXpozClient(
            settings.xpoz_api_key.strip(), check_update=False, timeout=26
        ) as c:
            # Search the brand's confirmed terms when it gave us any:
            # they were written to surface PEERS, which a niche sentence
            # ("public golf course in Kohler, Wisconsin") often is not.
            queries = confirmed_terms[:2] or [niche]
            results_nested = await asyncio.gather(
                *[_discover_platform(c, p, q, fetch_n)
                  for p in plats for q in queries]
            )
            results = list(results_nested)
    except Exception as e:  # noqa: BLE001 — connect/auth failure
        return {"error": f"{type(e).__name__}: {e}", "candidates": []}

    for plat, users, err in results:
        if err:
            errors[plat] = err
        found.extend(users)

    below_floor = sum(1 for c in found if c["followers"] < min_followers)
    found = [c for c in found if c["followers"] >= min_followers]
    # Searching several terms per platform means the same account can come
    # back more than once; without this, duplicates eat the `limit` and the
    # candidate list shows one handle twice.
    best: dict[tuple[str, str], dict] = {}
    for c in found:
        k = (c["platform"], c["handle"].lower())
        if k not in best or c["rank_score"] > best[k]["rank_score"]:
            best[k] = c
    found = list(best.values())
    found.sort(key=lambda c: c["rank_score"], reverse=True)
    found = found[:limit]
    for c in found:
        c["why"] = _why(c, niche)
        c["discovered_via"] = "niche_search"

    # Second, INDEPENDENT path. Keyword search matches on words a post
    # happens to contain, which is why a Delhi listings account and two
    # entertainment accounts came back for "Staten Island real estate".
    # Research knows *reputation* — who the leaders are — and reaches names
    # no keyword match surfaces. Every handle it produces is verified against
    # the platform before it is allowed to become a candidate.
    research_note: dict | None = None
    if use_research:
        accounts, r_err = await _research_handles(niche, plats)
        verified, unresolved = await verify_handles(accounts)
        seen = {(c["platform"], c["handle"].lower()) for c in found}
        added = 0
        for c in verified:
            key = (c["platform"], c["handle"].lower())
            if key in seen:
                continue
            # No follower floor here on purpose: the floor exists to filter
            # keyword noise, and these were named as leaders and then verified
            # to exist. In "Staten Island commercial real estate" the correct
            # answer has ~1.2k followers.
            seen.add(key)
            c["discovered_via"] = "research"
            c["why"] = (c.get("why") or "named as a leader in research")[:300]
            found.append(c)
            added += 1
        research_note = {
            "named": len(accounts), "verified": len(verified),
            "unresolved": [a.get("handle") for a in unresolved][:10],
            "added": added, "error": r_err,
        }

    stored = await upsert_many(found, niche=niche,
                               discovered_via="niche_search", tenant_id=tenant_id)

    # Keyword recall is loose; a bio-level screen culls the false positives
    # that follower counts and engagement rates cannot detect.
    screening = None
    if screen and stored:
        screening = await screen_candidates(niche, stored, tenant_id=tenant_id)
        rejected = {v["handle"].lower() for v in screening.get("verdicts", [])
                    if not v["on_niche"]}
        for row in stored:
            if row["handle"].lower() in rejected:
                row["status"] = "rejected"

    return {
        "niche": niche,
        "platforms": plats,
        "candidates": [c for c in stored if c["status"] != "rejected"],
        "screened_out": [c for c in stored if c["status"] == "rejected"],
        "count": sum(1 for c in stored if c["status"] != "rejected"),
        "below_follower_floor": below_floor,
        "min_followers": min_followers,
        "screening": screening,
        "research": research_note,
        "errors": errors,
    }


# ── persistence ───────────────────────────────────────────────────────

def _row(r) -> dict:
    d = dict(r)
    for k in ("id", "tenant_id"):
        if d.get(k) is not None:
            d[k] = str(d[k])
    for k in ("created_at", "last_synced_at"):
        if d.get(k) is not None:
            d[k] = d[k].isoformat()
    fh = d.get("follower_history")
    if isinstance(fh, str):
        d["follower_history"] = json.loads(fh)
    return d


async def upsert_many(
    candidates: list[dict],
    niche: str = "",
    discovered_via: str = "manual",
    status: str = "candidate",
    tenant_id: UUID | None = None,
) -> list[dict]:
    """Insert or refresh competitors. An existing row NEVER has its status
    overwritten — a human's tracked/rejected decision outranks a rediscovery.
    Follower counts append to follower_history, which is what makes growth
    rate computable later.
    """
    if not candidates:
        return []
    now = datetime.now(UTC).isoformat()
    out: list[dict] = []
    async with acquire(tenant_id) as conn:
        for c in candidates:
            handle = (c.get("handle") or "").strip().lstrip("@")
            platform = (c.get("platform") or "").strip().lower()
            if not handle or not platform:
                continue
            followers = _int(c.get("followers"))
            point = json.dumps([{"at": now, "followers": followers}])
            row = await conn.fetchrow(
                """
                INSERT INTO competitors (
                    platform, handle, name, niche, bio, profile_url, avatar_url,
                    followers, following, posts_total, verified, status,
                    discovered_via, rank_score, why, follower_history)
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16::jsonb)
                ON CONFLICT (tenant_id, platform, lower(handle)) DO UPDATE SET
                    name          = COALESCE(NULLIF(EXCLUDED.name, ''), competitors.name),
                    bio           = COALESCE(NULLIF(EXCLUDED.bio, ''), competitors.bio),
                    avatar_url    = COALESCE(NULLIF(EXCLUDED.avatar_url, ''), competitors.avatar_url),
                    profile_url   = COALESCE(NULLIF(EXCLUDED.profile_url, ''), competitors.profile_url),
                    niche         = COALESCE(NULLIF(EXCLUDED.niche, ''), competitors.niche),
                    followers     = EXCLUDED.followers,
                    following     = EXCLUDED.following,
                    posts_total   = EXCLUDED.posts_total,
                    verified      = EXCLUDED.verified,
                    -- Latest measurement WINS. An earlier version kept a
                    -- GREATEST() high-water mark, which meant a re-score could
                    -- never take effect and a declining account kept its rank.
                    rank_score    = EXCLUDED.rank_score,
                    why           = COALESCE(NULLIF(EXCLUDED.why, ''), competitors.why),
                    -- status is intentionally NOT updated: a human decision wins.
                    follower_history = CASE
                        WHEN EXCLUDED.followers > 0
                         AND EXCLUDED.followers IS DISTINCT FROM competitors.followers
                        THEN (competitors.follower_history || EXCLUDED.follower_history)
                        ELSE competitors.follower_history END
                RETURNING *
                """,
                platform, handle, c.get("name") or "", niche, (c.get("bio") or "")[:500],
                c.get("profile_url") or "", c.get("avatar_url") or "",
                followers, _int(c.get("following")), _int(c.get("posts_total")),
                bool(c.get("verified")), status,
                c.get("discovered_via") or discovered_via,
                float(c.get("rank_score") or 0.0), (c.get("why") or "")[:300], point,
            )
            out.append(_row(row))
    return out


async def list_competitors(
    status: str = "", platform: str = "", tenant_id: UUID | None = None
) -> list[dict]:
    """The roster. `status` takes one status or several, comma-separated
    ("tracked,reference") — the studio wants the accounts it scrapes AND the
    reference shelf in one read, while every scraping loop asks for 'tracked'
    alone and must keep getting exactly that."""
    clauses, args = [], []
    wanted = [s.strip() for s in status.split(",") if s.strip()]
    if wanted:
        args.append(wanted)
        clauses.append(f"status = ANY(${len(args)}::text[])")
    if platform:
        args.append(platform)
        clauses.append(f"platform = ${len(args)}")
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    async with acquire(tenant_id) as conn:
        rows = await conn.fetch(
            f"SELECT * FROM competitors {where} "
            "ORDER BY status = 'tracked' DESC, rank_score DESC, followers DESC", *args)
    return [_row(r) for r in rows]


async def set_status(
    competitor_id: UUID | str, status: str, tenant_id: UUID | None = None
) -> dict | None:
    if status not in STATUSES:
        raise ValueError(f"status must be one of {STATUSES}")
    async with acquire(tenant_id) as conn:
        row = await conn.fetchrow(
            "UPDATE competitors SET status = $2 WHERE id = $1::uuid RETURNING *",
            str(competitor_id), status)
    return _row(row) if row else None


async def add_competitor(
    platform: str, handle: str, name: str = "", niche: str = "",
    status: str = "tracked", tenant_id: UUID | None = None,
) -> dict | None:
    """Manually add a competitor by handle — the escape hatch for when you
    already know who to watch and don't need discovery."""
    platform = (platform or "").strip().lower()
    handle = (handle or "").strip().lstrip("@")
    if platform not in SCRAPE_PLATFORMS or not handle:
        raise ValueError(
            f"platform must be one of {SCRAPE_PLATFORMS} and handle non-empty")

    # Resolve the handle before storing it. A typo'd handle would otherwise
    # sit in the table forever failing to sync, and an unresolved row has
    # followers=0 — which silently turns every engagement rate into 0.00%.
    # Only Xpoz platforms can be verified; YouTube and LinkedIn are fetched
    # through Apify and have no search to resolve against, so "unresolved"
    # there is a fact about our tooling, not about the account.
    found: list[dict] = []
    if platform in PLATFORMS:
        found, _unresolved = await verify_handles([{"platform": platform, "handle": handle}])
    if found:
        cand = found[0]
        cand["why"] = "added by hand"
    else:
        cand = {"platform": platform, "handle": handle, "name": name,
                "profile_url": _PROFILE_URL[platform].format(h=handle),
                "why": ("added by hand" if platform not in PLATFORMS else
                        "added by hand — handle did not resolve on "
                        f"{PLATFORM_LABEL[platform]}")}
    if name:
        cand["name"] = name

    rows = await upsert_many([cand], niche=niche, discovered_via="manual",
                             status=status, tenant_id=tenant_id)
    if not rows:
        return None
    # upsert_many never changes an existing status; an explicit add should.
    out = await set_status(rows[0]["id"], status, tenant_id)
    if out is not None:
        out["resolved"] = bool(found)
    return out


async def import_watchlist(tenant_id: UUID | None = None) -> dict:
    """Adopt the existing curated watchlist as tracked competitors.

    The watchlist (tenants.config.watchlist) is a hand-built cohort — 71
    creators on the main tenant. Bringing it across means competitor
    intelligence starts with real names instead of an empty table. Only
    platforms we can actually sync are imported; the rest are reported.
    """
    from .trends import get_watchlist
    creators = await get_watchlist(tenant_id)
    usable, skipped = [], []
    for c in creators:
        plat = (c.get("platform") or "").strip().lower()
        handle = (c.get("handle") or "").strip().lstrip("@")
        if not handle:
            continue
        if plat not in PLATFORMS:
            skipped.append({"platform": plat, "handle": handle})
            continue
        usable.append({
            "platform": plat, "handle": handle, "name": c.get("name") or "",
            "profile_url": _PROFILE_URL[plat].format(h=handle),
            "why": "imported from the research watchlist",
        })
    stored = await upsert_many(
        usable, niche="", discovered_via="watchlist_import",
        status="tracked", tenant_id=tenant_id)
    return {"imported": len(stored), "skipped": skipped,
            "competitors": stored}


async def get_competitor(
    competitor_id: str, tenant_id: UUID | None = None
) -> dict | None:
    """One competitor by id, or None. RLS scopes it to the tenant."""
    async with acquire(tenant_id) as conn:
        row = await conn.fetchrow(
            "SELECT * FROM competitors WHERE id = $1::uuid", competitor_id)
    return _row(row) if row else None


# ── ranking from MEASURED posts ───────────────────────────────────────

async def recompute_ranks(tenant_id: UUID | None = None) -> list[dict]:
    """Re-rank every competitor from the posts we actually hold.

    Discovery-time rank is provisional: keyword candidates carry Xpoz's
    niche-engagement sums, and research-named candidates carry nothing at all
    (score 0), so the two are not on the same scale and the best candidates
    can sort last. Once posts are synced, everyone is measurable the same way.

        rank_score = median engagement rate × log10(followers) × 1000

    MEDIAN, not mean. @tristatecommercial has one post at 1355% (45k likes on
    3.4k followers) sitting among posts at 0.7% — a mean would let a single
    viral hit define the account. The median says what a typical post does.

    log10(followers) balances the two things you asked to rank on: reach and
    engagement. A flat multiply by followers would just re-sort by audience
    size (the mistake the first ranking made); ignoring followers entirely
    would put a 200-follower account with three engaged friends on top.
    Diminishing returns is the honest middle — 10× the audience is worth
    real weight, but not 10× the weight.

    Competitors with no synced posts keep their provisional score and are
    marked measured_posts=0 so the UI can say so rather than implying a
    measurement that never happened.
    """
    import math
    import statistics

    async with acquire(tenant_id) as conn:
        rows = await conn.fetch(
            """SELECT c.id, c.handle, c.platform, c.followers,
                      count(p.id)                       AS n,
                      array_remove(array_agg(p.engagement_rate), NULL) AS rates,
                      min(p.posted_at)                  AS first_post,
                      max(p.posted_at)                  AS last_post
                 FROM competitors c
            LEFT JOIN competitor_posts p ON p.competitor_id = c.id
                WHERE c.status <> 'rejected'
             GROUP BY c.id, c.handle, c.platform, c.followers""")

        out: list[dict] = []
        for r in rows:
            rates = [x for x in (r["rates"] or []) if x and x > 0]
            n = len(rates)
            if n == 0:
                await conn.execute(
                    "UPDATE competitors SET measured_posts = 0, ranked_at = now() "
                    "WHERE id = $1", r["id"])
                out.append({"handle": r["handle"], "platform": r["platform"],
                            "followers": r["followers"], "measured_posts": 0,
                            "rank_score": None})
                continue

            median = float(statistics.median(rates))
            mean = float(sum(rates) / n)
            followers = max(int(r["followers"] or 0), 10)
            score = round(median * math.log10(followers) * 1000, 3)

            # Posting cadence, when the window is long enough to mean anything.
            per_week = 0.0
            if r["first_post"] and r["last_post"]:
                days = (r["last_post"] - r["first_post"]).days
                if days >= 7:
                    per_week = round(n / (days / 7.0), 2)

            await conn.execute(
                """UPDATE competitors SET
                     rank_score             = $2,
                     median_engagement_rate = $3,
                     avg_engagement_rate    = $4,
                     posts_per_week         = $5,
                     measured_posts         = $6,
                     ranked_at              = now()
                   WHERE id = $1""",
                r["id"], score, median, mean, per_week, n)
            out.append({
                "handle": r["handle"], "platform": r["platform"],
                "followers": int(r["followers"] or 0), "measured_posts": n,
                "median_engagement_rate": median, "avg_engagement_rate": mean,
                "posts_per_week": per_week, "rank_score": score,
            })

    out.sort(key=lambda c: (c["rank_score"] is not None, c["rank_score"] or 0),
             reverse=True)
    return out


async def top_competitors(
    limit: int = 10, measured_only: bool = True, tenant_id: UUID | None = None
) -> list[dict]:
    """The highest-ranked pages in the niche — the shortlist worth studying.

    `measured_only` keeps out competitors we have not synced yet, whose score
    is provisional; turn it off to see the full field.
    """
    where = "WHERE status <> 'rejected'"
    if measured_only:
        where += " AND measured_posts > 0"
    async with acquire(tenant_id) as conn:
        rows = await conn.fetch(
            f"SELECT * FROM competitors {where} "
            "ORDER BY rank_score DESC, followers DESC LIMIT $1",
            max(1, min(limit, 100)))
    return [_row(r) for r in rows]


# ── second discovery path: research the niche, then VERIFY the handles ─

# get_user exposes no relevance fields — those only exist on the keyword
# endpoints — so verification asks for the profile facts alone.
_VERIFY_FIELDS: dict[str, list[str]] = {
    "instagram": ["username", "full_name", "biography", "follower_count",
                  "following_count", "media_count", "is_verified",
                  "profile_url", "profile_pic_url"],
    "tiktok": ["username", "nickname", "signature", "follower_count",
               "following_count", "post_count", "is_verified", "avatar"],
    "twitter": ["username", "name", "description", "followers_count",
                "following_count", "tweet_count", "is_verified",
                "profile_image_url"],
}

_EXTRACT_SYSTEM = """You extract social handles from research prose.

Given research about the leading accounts in a niche, list every specific
social account it names. Only include accounts the text actually names —
never infer a handle from a company name, and never invent one. If the text
names a brand without giving its handle, skip it.

Return JSON:
{"accounts": [{"platform": "instagram"|"tiktok"|"twitter", "handle": str
(no @), "name": str, "why": str (one short clause on why they lead)}]}"""


async def _research_handles(niche: str, platforms: list[str]) -> tuple[list[dict], str | None]:
    """Ask the live research provider who leads this niche, then pull the
    handles out of the prose. Returns (accounts, error)."""
    from .research import get_research_provider
    provider = get_research_provider()
    if provider.name == "stub":
        return [], "No research provider connected (add a Perplexity key)."
    names = " / ".join(PLATFORM_LABEL[p] for p in platforms)
    try:
        res = await provider.research(
            subject=f"the leading {names} accounts in {niche}",
            focus="Name the specific accounts and give their exact handles. "
                  "Prioritise accounts whose ongoing subject IS this niche "
                  "and who post consistently. Give handles verbatim.",
        )
    except Exception as e:  # noqa: BLE001
        return [], f"{type(e).__name__}: {e}"
    if res.is_empty():
        return [], "research returned nothing"

    from .llm import get_llm
    llm = get_llm()
    if getattr(llm, "model_name", "") == "stub":
        return [], "No LLM configured to read the research."
    body = (res.summary or "")[:6000]
    if res.findings:
        body += "\n\n" + "\n".join(f"- {f}" for f in res.findings[:12])
    try:
        out = await llm.complete_json(
            system=_EXTRACT_SYSTEM,
            messages=[{"role": "user", "content": f"NICHE: {niche}\n\nRESEARCH:\n{body}"}],
            max_tokens=1500, temperature=0.0,
        )
    except Exception as e:  # noqa: BLE001
        return [], f"handle extraction failed: {type(e).__name__}"
    accounts = out.get("accounts") if isinstance(out.get("accounts"), list) else []
    clean = []
    for a in accounts:
        if not isinstance(a, dict):
            continue
        plat = str(a.get("platform") or "").strip().lower()
        handle = str(a.get("handle") or "").strip().lstrip("@")
        if plat in platforms and handle:
            clean.append({"platform": plat, "handle": handle,
                          "name": str(a.get("name") or ""),
                          "why": str(a.get("why") or "")[:200]})
    return clean, None


async def verify_handles(accounts: list[dict]) -> tuple[list[dict], list[dict]]:
    """Resolve each handle against Xpoz. Returns (found, unresolved).

    This is the hallucination guard and it is not optional: a language model
    reading research prose will confidently produce handles that do not
    exist. Only an account the platform actually returns becomes a candidate,
    and resolving it also yields the real follower count instead of a
    remembered one.
    """
    if not accounts or not configured():
        return [], list(accounts)
    try:
        import xpoz
    except ImportError:
        return [], list(accounts)

    async def _one(client: Any, a: dict) -> tuple[dict, dict | None]:
        try:
            ns = getattr(client, a["platform"])
            u = await asyncio.wait_for(
                ns.get_user(a["handle"], identifier_type="username",
                            fields=_VERIFY_FIELDS[a["platform"]])
                if a["platform"] != "twitter"
                else ns.get_user(a["handle"], fields=_VERIFY_FIELDS["twitter"]),
                timeout=20)
        except Exception:  # noqa: BLE001 — unresolved is a normal outcome here
            return a, None
        if not u:
            return a, None
        c = _user_to_candidate(a["platform"], u)
        if not c["handle"]:
            return a, None
        c["why"] = a.get("why") or ""
        return a, c

    found, unresolved = [], []
    try:
        async with xpoz.AsyncXpozClient(
            settings.xpoz_api_key.strip(), check_update=False, timeout=26
        ) as client:
            results = await asyncio.gather(*[_one(client, a) for a in accounts],
                                           return_exceptions=True)
    except Exception:  # noqa: BLE001
        return [], list(accounts)
    for r in results:
        if isinstance(r, tuple):
            a, c = r
            (found if c else unresolved).append(c or a)
    return found, unresolved


# ── screening: is this actually a brand in the niche? ─────────────────

_SCREEN_SYSTEM = """You screen social accounts for a competitor-research shortlist.

You are given a NICHE and a list of accounts that a keyword search surfaced.
Keyword search is loose — it matches any account whose posts mention the
words, so entertainment, news, and unrelated-geography accounts leak in.

For each account decide whether it is genuinely a COMPETITOR worth studying
for that niche: an account whose ongoing subject matter IS the niche, in a
market the niche implies. Judge from the handle, display name and bio.

Reject: general news/gossip/entertainment accounts, accounts whose subject
is a different industry, and accounts clearly serving a different geography
than the niche names. Keep: operators, brands, agencies and creators who
work in the niche, even at modest follower counts.

When the bio is empty or uninformative, say so and set on_niche=false with
reason "not enough signal" — do NOT guess.

Return JSON:
{"verdicts": [{"handle": str, "on_niche": bool, "kind": "brand"|"creator"|
"media"|"other", "reason": str (one short clause)}]}"""


async def screen_candidates(
    niche: str, candidates: list[dict], tenant_id: UUID | None = None,
) -> dict:
    """Cull the keyword search's false positives with one LLM pass.

    Xpoz keyword discovery has loose recall — searching "Staten Island real
    estate" returned a Delhi listings account and two Bollywood gossip
    accounts, because they had all mentioned those words. Follower counts and
    engagement rates cannot tell you an account is in the wrong industry or
    the wrong country; reading its bio can.

    Off-niche accounts are set to 'rejected' with the reason recorded on the
    row — reversible in one click, never deleted, always auditable.
    """
    if not candidates:
        return {"screened": 0, "kept": 0, "rejected": 0, "verdicts": []}
    from .llm import get_llm
    llm = get_llm()
    if getattr(llm, "model_name", "") == "stub":
        return {"screened": 0, "kept": len(candidates), "rejected": 0,
                "verdicts": [], "note": "No LLM configured — nothing screened."}

    listing = "\n".join(
        f"- @{c['handle']} ({c.get('platform','')}) · {c.get('followers',0):,} followers"
        f" · name: {c.get('name') or '(none)'} · bio: {(c.get('bio') or '(empty)')[:200]}"
        for c in candidates
    )
    try:
        out = await llm.complete_json(
            system=_SCREEN_SYSTEM,
            messages=[{"role": "user",
                       "content": f"NICHE: {niche}\n\nACCOUNTS:\n{listing}"}],
            max_tokens=min(4000, 300 + len(candidates) * 90),
            temperature=0.0,
        )
    except Exception as e:  # noqa: BLE001 — a screening failure keeps everyone
        return {"screened": 0, "kept": len(candidates), "rejected": 0,
                "verdicts": [], "error": f"{type(e).__name__}: {e}"}

    verdicts = out.get("verdicts") if isinstance(out.get("verdicts"), list) else []
    by_handle = {str(v.get("handle", "")).lstrip("@").lower(): v
                 for v in verdicts if isinstance(v, dict)}

    kept, rejected, applied = 0, 0, []
    async with acquire(tenant_id) as conn:
        for c in candidates:
            v = by_handle.get(c["handle"].lower())
            if not v:
                kept += 1          # unjudged accounts are kept, never dropped
                continue
            on = bool(v.get("on_niche"))
            reason = str(v.get("reason") or "").strip()[:200]
            kind = str(v.get("kind") or "").strip()[:20]
            if on:
                kept += 1
                # Only annotate a candidate; never promote it to tracked. The
                # decision to spend sync credits on an account stays human.
                await conn.execute(
                    "UPDATE competitors SET why = $2 WHERE id = $1::uuid "
                    "AND status = 'candidate'",
                    c["id"], f"{c.get('why','')} · {kind}: {reason}"[:300])
            else:
                rejected += 1
                await conn.execute(
                    "UPDATE competitors SET status = 'rejected', why = $2 "
                    "WHERE id = $1::uuid AND status = 'candidate'",
                    c["id"], f"off-niche — {reason}"[:300])
            applied.append({"handle": c["handle"], "on_niche": on,
                            "kind": kind, "reason": reason})
    return {"screened": len(applied), "kept": kept, "rejected": rejected,
            "verdicts": applied}


__all__ = [
    "PLATFORMS", "PLATFORM_LABEL", "STATUSES", "configured", "discover",
    "upsert_many", "list_competitors", "set_status", "add_competitor",
    "screen_candidates", "verify_handles",
    "recompute_ranks", "top_competitors", "get_competitor",
    "import_watchlist",
]
