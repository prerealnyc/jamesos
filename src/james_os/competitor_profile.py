"""Stage 4 of competitor intelligence — what is this account actually doing?

Turns a shelf of posts and per-post analyses into one readable profile per
competitor: how often they post, in what formats, on what topics, with which
hooks, how hard each lands, and what growth strategy that adds up to.

The division of labour is deliberate and load-bearing:

  EVERY NUMBER is computed in Python from stored rows — cadence, format mix,
  topic shares, hook performance, posting windows, follower growth. They are
  reproducible and auditable, and nothing can hallucinate them.

  ONLY THE NARRATIVE is written by a model, and it is handed the computed
  numbers and told to explain them. It never supplies a figure of its own.

That split is what makes this usable as evidence downstream. `strategy.py`
consumes it in place of `snapshot_peers`, which asks a web-research provider
to *describe* a competitor's content operation and has never read a post.

Engagement is compared as RATE (per follower), never raw likes, so a 3k
account and a 300k account can sit in the same table honestly.
"""

from __future__ import annotations

import json
import statistics
from collections import Counter, defaultdict
from datetime import datetime
from uuid import UUID

from .db import acquire

_SYNTH_SYSTEM = """You explain a competitor's content strategy to a brand
that is deciding what to do.

You are given COMPUTED facts about one account: posting cadence, format mix,
topic shares, how each hook pattern performs, posting windows, follower
growth, and its best posts with what a vision pass observed about them.

Write the strategy those facts add up to: how this account grows an audience
and how it connects with the people it is talking to.

Hard rules:
  * Every number you state must be one you were GIVEN. Never compute,
    estimate, round differently, or invent a figure.
  * Cite the fact behind each claim in `evidence` as a short string, e.g.
    "reels are 62% of output and carry a 2.1% median rate vs 0.4% for photos".
  * Where the data does not support a conclusion, say so plainly. "Not
    enough posts to tell" is a valid and useful finding.
  * No generic social-media advice. Only what THIS account's numbers show.

Return JSON:
{"strategy": str (3-6 sentences), "evidence": [str, ...],
 "what_they_do_well": [str, ...], "gaps": [str, ...]}"""


def _pct(part: int, whole: int) -> float:
    return round(part / whole, 4) if whole else 0.0


def _rate_stats(rates: list[float]) -> dict:
    clean = [r for r in rates if r and r > 0]
    if not clean:
        return {"n": 0, "median": 0.0, "mean": 0.0}
    return {"n": len(clean),
            "median": round(float(statistics.median(clean)), 6),
            "mean": round(float(sum(clean) / len(clean)), 6)}


async def _load(competitor_id: str, tenant_id: UUID | None) -> tuple[dict, list[dict]]:
    async with acquire(tenant_id) as conn:
        comp = await conn.fetchrow(
            "SELECT * FROM competitors WHERE id = $1::uuid", competitor_id)
        rows = await conn.fetch(
            """SELECT p.id, p.url, p.caption, p.media_type, p.likes, p.comments,
                      p.views, p.engagement_rate, p.posted_at,
                      a.format, a.hook, a.hook_pattern, a.topic, a.cta,
                      a.eye_score, a.design_dna, a.classification,
                      a.fingerprint, a.why_it_works, a.status AS a_status
                 FROM competitor_posts p
            LEFT JOIN competitor_post_analysis a ON a.post_id = p.id
                WHERE p.competitor_id = $1::uuid
             ORDER BY p.posted_at DESC NULLS LAST""",
            competitor_id)
    if not comp:
        return {}, []
    posts = []
    for r in rows:
        d = dict(r)
        for k in ("design_dna", "classification", "fingerprint"):
            if isinstance(d.get(k), str):
                d[k] = json.loads(d[k])
        posts.append(d)
    c = dict(comp)
    if isinstance(c.get("follower_history"), str):
        c["follower_history"] = json.loads(c["follower_history"])
    return c, posts


def _follower_growth(history: list[dict]) -> dict:
    """Growth between the first and last follower reading we hold.

    Honest about its own limits: two readings a day apart cannot support a
    weekly rate, so `window_days` rides along with every figure and a window
    under a day reports nothing.
    """
    pts = [h for h in (history or []) if h.get("followers")]
    if len(pts) < 2:
        return {"window_days": 0, "note": "only one follower reading so far"}
    try:
        first, last = pts[0], pts[-1]
        t0 = datetime.fromisoformat(str(first["at"]).replace("Z", "+00:00"))
        t1 = datetime.fromisoformat(str(last["at"]).replace("Z", "+00:00"))
    except (ValueError, TypeError, KeyError):
        return {"window_days": 0, "note": "unparseable follower history"}
    days = (t1 - t0).total_seconds() / 86400.0
    if days < 1:
        return {"window_days": round(days, 2),
                "note": "readings less than a day apart — no rate yet"}
    delta = int(last["followers"]) - int(first["followers"])
    base = max(int(first["followers"]), 1)
    return {
        "window_days": round(days, 1),
        "gained": delta,
        "per_week": round(delta / (days / 7.0), 1),
        "pct": round(delta / base, 4),
    }


def _design_signature(posts: list[dict]) -> dict:
    """The recurring visual choices, from design_eye's structured DNA. Only
    fields it actually returned — nothing is inferred to fill a gap."""
    buckets: dict[str, Counter] = defaultdict(Counter)
    scores: list[float] = []
    for p in posts:
        dna = p.get("design_dna") or {}
        for k, v in dna.items():
            if isinstance(v, str) and v.strip():
                buckets[k][v.strip().lower()[:60]] += 1
        if p.get("eye_score"):
            scores.append(float(p["eye_score"]))
    sig = {k: [{"value": v, "n": n} for v, n in c.most_common(3)]
           for k, c in buckets.items()}
    if scores:
        sig["eye_score"] = {"n": len(scores),
                            "median": round(float(statistics.median(scores)), 1)}
    return sig


def _grouped(posts: list[dict], key: str, cap: int = 12) -> list[dict]:
    """Group by an analysis field and report share + engagement per group."""
    groups: dict[str, list[dict]] = defaultdict(list)
    for p in posts:
        v = (p.get(key) or "").strip().lower()
        if v and v not in ("none", "n/a"):
            groups[v].append(p)
    total = sum(len(v) for v in groups.values())
    out = []
    for name, items in groups.items():
        st = _rate_stats([i.get("engagement_rate") or 0 for i in items])
        out.append({"value": name, "n": len(items), "share": _pct(len(items), total),
                    "median_engagement_rate": st["median"]})
    out.sort(key=lambda g: (g["n"], g["median_engagement_rate"]), reverse=True)
    return out[:cap]


def compute_facts(competitor: dict, posts: list[dict]) -> dict:
    """Everything measurable, computed. No model involved."""
    analysed = [p for p in posts if p.get("a_status") == "ok"]
    rates = [p.get("engagement_rate") or 0 for p in posts]
    st = _rate_stats(rates)

    # Cadence over the window we actually observed.
    dated = [p["posted_at"] for p in posts if p.get("posted_at")]
    cadence, window_days = 0.0, 0
    if len(dated) >= 2:
        window_days = max((max(dated) - min(dated)).days, 0)
        if window_days >= 7:
            cadence = round(len(dated) / (window_days / 7.0), 2)

    # Posting windows in UTC — stated as UTC because converting without
    # knowing the account's timezone would be a guess dressed as a fact.
    windows = Counter()
    for d in dated:
        windows[(d.weekday(), d.hour)] += 1

    media_mix = _grouped(posts, "media_type")
    return {
        "handle": competitor.get("handle"),
        "platform": competitor.get("platform"),
        "followers": int(competitor.get("followers") or 0),
        "posts_held": len(posts),
        "posts_analysed": len(analysed),
        "window_days": window_days,
        "cadence_per_week": cadence,
        "engagement_rate": st,
        "media_mix": media_mix,
        "format_mix": _grouped(analysed, "format"),
        "topic_clusters": _grouped(analysed, "topic"),
        "hook_patterns": _grouped(analysed, "hook_pattern"),
        "value_types": _grouped(
            [{**p, "value_type": (p.get("classification") or {}).get("value_type", ""),
              "engagement_rate": p.get("engagement_rate")} for p in analysed],
            "value_type"),
        "growth_plays": _grouped(
            [{**p, "growth_play": (p.get("classification") or {}).get("growth_play", ""),
              "engagement_rate": p.get("engagement_rate")} for p in analysed],
            "growth_play"),
        "posting_windows_utc": [
            {"weekday": d, "hour": h, "n": n}
            for (d, h), n in windows.most_common(6)],
        "follower_growth": _follower_growth(competitor.get("follower_history") or []),
        "top_posts": [
            {"url": p.get("url"), "engagement_rate": p.get("engagement_rate"),
             "likes": p.get("likes"), "media_type": p.get("media_type"),
             "format": p.get("format"), "hook": (p.get("hook") or "")[:160],
             "why_it_works": (p.get("why_it_works") or "")[:300]}
            for p in sorted(posts, key=lambda x: x.get("engagement_rate") or 0,
                            reverse=True)[:5]],
        "design_signature": _design_signature(analysed),
    }


async def build_profile(
    competitor_id: str, synthesise: bool = True, tenant_id: UUID | None = None
) -> dict:
    """Compute one competitor's profile and persist it."""
    competitor, posts = await _load(competitor_id, tenant_id)
    if not competitor:
        return {"error": "competitor not found"}
    if not posts:
        return {"error": "no posts on the shelf — sync this competitor first",
                "handle": competitor.get("handle")}

    facts = compute_facts(competitor, posts)

    strategy, evidence, well, gaps = "", [], [], []
    if synthesise and facts["posts_analysed"] > 0:
        from .llm import get_llm
        llm = get_llm()
        if getattr(llm, "model_name", "") != "stub":
            try:
                out = await llm.complete_json(
                    system=_SYNTH_SYSTEM,
                    messages=[{"role": "user",
                               "content": json.dumps(facts, default=str)[:14000]}],
                    max_tokens=1200, temperature=0.1)
                strategy = str(out.get("strategy") or "")[:4000]
                evidence = [str(e)[:300] for e in (out.get("evidence") or [])][:12]
                well = [str(e)[:200] for e in (out.get("what_they_do_well") or [])][:8]
                gaps = [str(e)[:200] for e in (out.get("gaps") or [])][:8]
            except Exception as e:  # noqa: BLE001 — facts still stand without prose
                strategy = ""
                evidence = [f"(synthesis failed: {type(e).__name__})"]

    payload_evidence = evidence + [f"what they do well: {w}" for w in well] \
                                + [f"gap: {g}" for g in gaps]

    async with acquire(tenant_id) as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO competitor_profiles (
                competitor_id, posts_analyzed, cadence_per_week, format_mix,
                topic_clusters, hook_patterns, posting_windows,
                avg_engagement_rate, follower_growth, design_signature,
                top_posts, growth_strategy, evidence)
            VALUES ($1::uuid,$2,$3,$4::jsonb,$5::jsonb,$6::jsonb,$7::jsonb,$8,
                    $9::jsonb,$10::jsonb,$11::jsonb,$12,$13::jsonb)
            ON CONFLICT (competitor_id) DO UPDATE SET
                posts_analyzed = EXCLUDED.posts_analyzed,
                cadence_per_week = EXCLUDED.cadence_per_week,
                format_mix = EXCLUDED.format_mix,
                topic_clusters = EXCLUDED.topic_clusters,
                hook_patterns = EXCLUDED.hook_patterns,
                posting_windows = EXCLUDED.posting_windows,
                avg_engagement_rate = EXCLUDED.avg_engagement_rate,
                follower_growth = EXCLUDED.follower_growth,
                design_signature = EXCLUDED.design_signature,
                top_posts = EXCLUDED.top_posts,
                growth_strategy = EXCLUDED.growth_strategy,
                evidence = EXCLUDED.evidence,
                computed_at = now()
            RETURNING *
            """,
            competitor_id, facts["posts_analysed"], facts["cadence_per_week"],
            json.dumps({"format": facts["format_mix"], "media": facts["media_mix"],
                        "value_types": facts["value_types"],
                        "growth_plays": facts["growth_plays"]}),
            json.dumps(facts["topic_clusters"]),
            json.dumps(facts["hook_patterns"]),
            json.dumps(facts["posting_windows_utc"]),
            facts["engagement_rate"]["median"],
            json.dumps(facts["follower_growth"]),
            json.dumps(facts["design_signature"]),
            json.dumps(facts["top_posts"]), strategy,
            json.dumps(payload_evidence))

    d = dict(row)
    for k in ("id", "tenant_id", "competitor_id"):
        d[k] = str(d[k])
    d["computed_at"] = d["computed_at"].isoformat()
    for k in ("format_mix", "topic_clusters", "hook_patterns", "posting_windows",
              "follower_growth", "design_signature", "top_posts", "evidence"):
        if isinstance(d.get(k), str):
            d[k] = json.loads(d[k])
    d["facts"] = facts
    return d


async def build_all_profiles(
    synthesise: bool = True, tenant_id: UUID | None = None
) -> dict:
    from .competitors import list_competitors
    tracked = await list_competitors(status="tracked", tenant_id=tenant_id)
    built, skipped = [], []
    for c in tracked:
        try:
            p = await build_profile(c["id"], synthesise=synthesise, tenant_id=tenant_id)
        except Exception as e:  # noqa: BLE001
            skipped.append({"handle": c["handle"], "reason": str(e)[:160]})
            continue
        if p.get("error"):
            skipped.append({"handle": c["handle"], "reason": p["error"]})
        else:
            built.append({"handle": c["handle"], "posts_analyzed": p["posts_analyzed"],
                          "cadence_per_week": p["cadence_per_week"]})
    return {"built": len(built), "profiles": built, "skipped": skipped}


async def run_competitor_profiles(tenant_id: UUID, config: dict | None = None) -> None:
    """Scheduler entry point. Tenant-bound and explicit."""
    cfg = config or {}
    await build_all_profiles(
        synthesise=bool(cfg.get("synthesise", True)), tenant_id=tenant_id)


async def list_profiles(tenant_id: UUID | None = None) -> list[dict]:
    async with acquire(tenant_id) as conn:
        rows = await conn.fetch(
            """SELECT pr.*, c.handle, c.platform, c.followers, c.rank_score,
                      c.median_engagement_rate
                 FROM competitor_profiles pr
                 JOIN competitors c ON c.id = pr.competitor_id
             ORDER BY c.rank_score DESC NULLS LAST""")
    out = []
    for r in rows:
        d = dict(r)
        for k in ("id", "tenant_id", "competitor_id"):
            d[k] = str(d[k])
        d["computed_at"] = d["computed_at"].isoformat()
        for k in ("format_mix", "topic_clusters", "hook_patterns", "posting_windows",
                  "follower_growth", "design_signature", "top_posts", "evidence"):
            if isinstance(d.get(k), str):
                d[k] = json.loads(d[k])
        out.append(d)
    return out


__all__ = [
    "compute_facts", "build_profile", "build_all_profiles",
    "run_competitor_profiles", "list_profiles",
]
