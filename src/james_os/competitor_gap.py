"""What the peer group posts, what we have, and what we are missing.

The competitor shelf answers "what are they doing". The brand's own actions
and hero library answer "what do we have". This module is the subtraction —
and the subtraction is the part that changes what anyone does on Monday.

    THEIRS   analysed competitor posts, grouped by media type, format, value
             type, hook pattern and visual signature, weighted by the
             engagement each group actually earns
    OURS     the brand's own shipped/queued output, plus what its hero photo
             library can actually depict
    GAP      groups that earn well for the peer group and that we do not
             produce at all, or produce far below their share

Two rules keep this honest:

  * Every number is computed from stored rows. The narrative is written by a
    model that is handed those numbers and forbidden from inventing any.
  * A gap is only claimed where BOTH sides are measurable. With no analysed
    competitor posts, or no output of our own, the answer is "not enough
    evidence yet" — never a confident recommendation resting on nothing.

The output is consumed by strategy.compose_prescription as <content_gap>,
where lines may cite it as "gap: …".
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from uuid import UUID

from .db import acquire

_GAP_SYSTEM = """You advise a brand on what content it is MISSING, from
measured facts about its competitors and itself.

You are given: what the peer group posts and how each kind performs, what the
brand has produced recently, and what its own photo library can depict.

Hard rules:
  * Every number you state must be one you were GIVEN. Never invent, compute
    or re-round a figure.
  * A gap is only real if the peer data shows it works AND the brand's own
    output shows it is absent or thin. Say which side each claim rests on.
  * Any key ending in `_pct` is already a percentage — write it with a % and
    do not rescale.
  * Where the evidence is too thin, say so. "Not enough analysed posts to
    tell" is a valid finding and better than a confident guess.
  * Be concrete about PHOTOS specifically: what the peer group shoots that
    this brand has no equivalent of.

Return JSON:
{"summary": str,                       // 2-4 sentences
 "gaps": [{"what": str,                // the missing content type, concretely
           "evidence": str,            // the numbers behind it
           "why_it_matters": str,
           "shot_list": [str]}],       // what to actually go and capture/make
 "we_already_do": [str],               // where we match them — do not "fix"
 "not_enough_evidence": [str]}"""


def _pct(part: int, whole: int) -> float:
    return round(100.0 * part / whole, 1) if whole else 0.0


async def peer_content_profile(tenant_id: UUID | None = None) -> dict:
    """What the tracked peer group posts, and how each kind performs.

    Engagement is the MEDIAN rate per group, not the mean: one 84% carousel
    would otherwise make every carousel look like a guaranteed hit.
    """
    async with acquire(tenant_id) as conn:
        rows = await conn.fetch(
            """SELECT p.media_type, p.engagement_rate,
                      a.format, a.hook_pattern, a.classification, a.design_dna,
                      c.handle
                 FROM competitor_posts p
                 JOIN competitors c ON c.id = p.competitor_id
            LEFT JOIN competitor_post_analysis a ON a.post_id = p.id
                WHERE c.status = 'tracked'""")

    posts = []
    for r in rows:
        d = dict(r)
        for k in ("classification", "design_dna"):
            if isinstance(d.get(k), str):
                d[k] = json.loads(d[k])
        posts.append(d)

    analysed = [p for p in posts if p.get("format")]

    def group(key: str, source: str = "row") -> list[dict]:
        buckets: dict[str, list[float]] = defaultdict(list)
        for p in analysed:
            v = (p.get(key) if source == "row"
                 else (p.get("classification") or {}).get(key)) or ""
            v = str(v).strip().lower().replace("_", " ")
            v = " ".join(v.split())
            if not v or v in ("none", "n/a"):
                continue
            buckets[v].append(float(p.get("engagement_rate") or 0) * 100)
        total = sum(len(v) for v in buckets.values())
        out = []
        for name, rates in buckets.items():
            rates_sorted = sorted(rates)
            mid = rates_sorted[len(rates_sorted) // 2] if rates_sorted else 0.0
            out.append({"value": name, "n": len(rates),
                        "share_pct": _pct(len(rates), total),
                        "median_engagement_pct": round(mid, 2)})
        out.sort(key=lambda g: g["n"], reverse=True)
        return out[:12]

    # Visual vocabulary — what their pictures literally look like.
    visual: dict[str, Counter] = defaultdict(Counter)
    for p in analysed:
        for k, v in (p.get("design_dna") or {}).items():
            if isinstance(v, str) and v.strip():
                visual[k][v.strip().lower()[:50]] += 1

    media_counts = Counter(str(p.get("media_type") or "").lower() for p in posts)
    return {
        "peers": sorted({p["handle"] for p in posts}),
        "posts_held": len(posts),
        "posts_analysed": len(analysed),
        "media_mix": [{"value": k, "n": v, "share_pct": _pct(v, len(posts))}
                      for k, v in media_counts.most_common()],
        "formats": group("format"),
        "hook_patterns": group("hook_pattern"),
        "value_types": group("value_type", "classification"),
        "growth_plays": group("growth_play", "classification"),
        "visual_vocabulary": {k: [{"value": v, "n": n} for v, n in c.most_common(4)]
                              for k, c in visual.items()},
    }


async def own_content_profile(tenant_id: UUID | None = None) -> dict:
    """What WE have produced, and what our photo library can depict.

    The hero library matters as much as the output here: a brand cannot post
    a format it has no imagery for, so "we lack the photos" is a different
    and more actionable finding than "we lack the posts".
    """
    async with acquire(tenant_id) as conn:
        out_rows = await conn.fetch(
            """SELECT coalesce(payload->>'format', action_type) AS fmt,
                      count(*) AS n
                 FROM actions
                WHERE created_at > now() - interval '90 days'
             GROUP BY 1 ORDER BY n DESC LIMIT 20""")
        media_rows = await conn.fetch(
            """SELECT role, count(*) AS n FROM media_assets
             GROUP BY role ORDER BY n DESC LIMIT 10""")
    total = sum(r["n"] for r in out_rows)
    return {
        "posts_90d": total,
        "formats": [{"value": (r["fmt"] or "").lower(), "n": r["n"],
                     "share_pct": _pct(r["n"], total)} for r in out_rows],
        "media_library": [{"role": r["role"], "n": r["n"]} for r in media_rows],
    }


def _shortfall(theirs: list[dict], ours: list[dict]) -> list[dict]:
    """Peer groups we produce far less of, or not at all. Pure arithmetic —
    the model is not asked to work out what is missing, only to explain it."""
    ours_by = {o["value"]: o for o in ours}
    gaps = []
    for t in theirs:
        mine = ours_by.get(t["value"])
        my_share = float(mine["share_pct"]) if mine else 0.0
        if t["share_pct"] - my_share >= 10.0:      # a real difference, not noise
            gaps.append({
                "value": t["value"],
                "their_share_pct": t["share_pct"],
                "our_share_pct": my_share,
                "their_median_engagement_pct": t["median_engagement_pct"],
                "we_have_none": mine is None,
            })
    gaps.sort(key=lambda g: (g["their_share_pct"], g["their_median_engagement_pct"]),
              reverse=True)
    return gaps


async def content_gap(tenant_id: UUID | None = None) -> dict:
    """The full picture: theirs, ours, the measured shortfall, and the read."""
    theirs = await peer_content_profile(tenant_id)
    ours = await own_content_profile(tenant_id)

    thin = []
    if theirs["posts_analysed"] < 5:
        thin.append(f"only {theirs['posts_analysed']} analysed competitor posts")
    if ours["posts_90d"] < 3:
        thin.append(f"only {ours['posts_90d']} of our own posts in 90 days")

    facts = {
        "peer_group": theirs["peers"],
        "peer_posts_analysed": theirs["posts_analysed"],
        "their_media_mix": theirs["media_mix"],
        "their_formats": theirs["formats"],
        "their_value_types": theirs["value_types"],
        "their_hook_patterns": theirs["hook_patterns"],
        "their_visual_vocabulary": theirs["visual_vocabulary"],
        "our_posts_90d": ours["posts_90d"],
        "our_formats": ours["formats"],
        "our_media_library": ours["media_library"],
        "measured_format_shortfall": _shortfall(theirs["formats"], ours["formats"]),
    }

    read: dict = {}
    if not thin:
        from .llm import get_llm
        llm = get_llm()
        if getattr(llm, "model_name", "") != "stub":
            try:
                read = await llm.complete_json(
                    system=_GAP_SYSTEM,
                    messages=[{"role": "user",
                               "content": json.dumps(facts, default=str)[:14000]}],
                    max_tokens=1400, temperature=0.2) or {}
            except Exception as e:  # noqa: BLE001 — facts stand without prose
                read = {"summary": "", "gaps": [],
                        "not_enough_evidence": [f"synthesis failed: {type(e).__name__}"]}

    result = {"facts": facts, "read": read, "insufficient_evidence": thin}

    # Keep it. Recomputing on every read cost an LLM pass and, worse, made the
    # gap unanswerable over time — you could not see whether a gap you acted on
    # had closed. Append-only, so the series records how the brand's shape
    # changed against its peers.
    async with acquire(tenant_id) as conn:
        await conn.execute(
            """INSERT INTO competitor_gaps (
                   facts, read, insufficient, peer_posts_analysed,
                   our_posts_90d, gap_count)
               VALUES ($1::jsonb, $2::jsonb, $3::jsonb, $4, $5, $6)""",
            json.dumps(facts, default=str), json.dumps(read, default=str),
            json.dumps(thin), theirs["posts_analysed"], ours["posts_90d"],
            len(facts["measured_format_shortfall"]))
    return result


async def latest_gap(tenant_id: UUID | None = None) -> dict | None:
    """The most recent stored gap — a cheap read, no model involved."""
    async with acquire(tenant_id) as conn:
        row = await conn.fetchrow(
            "SELECT * FROM competitor_gaps ORDER BY computed_at DESC LIMIT 1")
    if not row:
        return None
    d = dict(row)
    for k in ("id", "tenant_id"):
        d[k] = str(d[k])
    d["computed_at"] = d["computed_at"].isoformat()
    for k in ("facts", "read", "insufficient"):
        if isinstance(d.get(k), str):
            d[k] = json.loads(d[k])
    # Same shape as content_gap() so callers do not branch on where it came from.
    d["insufficient_evidence"] = d.pop("insufficient")
    return d


async def gap_block(tenant_id: UUID | None = None, max_age_hours: int = 168) -> str:
    """Compact <content_gap> text for the strategiser's prompt.

    Prefers the stored gap: composing a plan should not trigger a fresh LLM
    pass over the whole shelf, and a gap measured this week is the right input
    for this week's plan. Recomputes only when there is nothing stored or the
    stored one has gone stale.
    """
    from datetime import UTC, datetime, timedelta
    g = await latest_gap(tenant_id)
    if g:
        try:
            age = datetime.now(UTC) - datetime.fromisoformat(g["computed_at"])
            if age > timedelta(hours=max_age_hours):
                g = None
        except (ValueError, TypeError):
            g = None
    if not g:
        g = await content_gap(tenant_id)
    if g["insufficient_evidence"]:
        return ("(not enough evidence for a content gap: "
                + "; ".join(g["insufficient_evidence"]) + ")")
    lines: list[str] = []
    for s in g["facts"]["measured_format_shortfall"][:6]:
        lines.append(
            f"- {s['value']}: peers {s['their_share_pct']}% of posts "
            f"(median {s['their_median_engagement_pct']}% engagement), "
            f"us {s['our_share_pct']}%"
            + (" — WE HAVE NONE" if s["we_have_none"] else ""))
    read = g.get("read") or {}
    for gp in (read.get("gaps") or [])[:5]:
        lines.append(f"- {gp.get('what','')} — {gp.get('evidence','')}")
    return "\n".join(lines) or "(no material gap measured)"


__all__ = ["peer_content_profile", "own_content_profile", "content_gap",
           "latest_gap", "gap_block"]
