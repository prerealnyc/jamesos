"""The strategy engine — "it has to think."

James: "It should say: to grow your Instagram you should be making five
static image posts a day, ten videos… based on what it knows it needs to do
to grow the audience. That's what a brand manager would know."

Three grounded inputs → one weekly Prescription:

  PLAYBOOKS   per-platform algorithm briefs, research-refreshed on a
              cadence, VERSIONED with change detection ("I'll let you know
              when the algorithm changes").
  PEER BENCH  what accounts at the brand's target tier actually do
              (cadence, formats, topics) — from the profile's peer set.
  INVENTORY   what this brand actually produced/queued recently (so the
              prescription reacts to reality, not theory). Performance
              attribution joins in at M4.

The Prescription is a quantified plan — volumes per format/platform, topic
mix vs the brand's pillars, growth actions (podcast ladder, promote
candidates) — and EVERY line carries evidence[]. No oracle claims: weak
research is visible, never silently wrong. Accepting lines hands them to
the same production machinery as everything else → approval queue.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from uuid import UUID

from .db import acquire
from .llm import get_llm

_PLAYBOOK_SYSTEM = """You are a platform-algorithm analyst. From the
research briefing, produce the CURRENT operating playbook for {platform}:
what the algorithm favors right now, winning formats and lengths, cadence
norms, hook patterns, and penalties/pitfalls.

Return STRICT JSON:
{{"brief_md": str,        // <= 500 words, markdown, practitioner tone
  "key_points": [str]}}   // 5-10 atomic, diffable claims
"""

_PRESCRIPTION_SYSTEM = """You are the brand manager composing THE WEEKLY
PRESCRIPTION for the brand in <brand_profile> — the plan a $200k/yr human
brand manager would put on the table Monday morning.

You are given: per-platform algorithm playbooks, peer benchmarks (what
accounts at the target tier actually do), a measured CONTENT GAP (what the
peer group posts that this brand does not), and the brand's recent output
inventory. Compose:

* plan — one line per (platform × format) worth doing: how many per week,
  which topics (drawn from the brand's pillars; call out UNDERWEIGHT
  pillars), and WHY. EVERY line must carry evidence[] — each item names
  its source: "playbook: …", "peer: …", "gap: …", "inventory: …", or
  "canon: …". If you cannot ground a line, do not write it.
  Where <content_gap> names something the peer group does well and this brand
  does not do at all, that is the highest-value line on the page — say so, and
  say what has to be shot or made before it can happen.
* growth_actions — 2-5 moves beyond posting (next-tier podcast/guest
  targets, collab, promote a proven performer, platform to add), each with
  why + evidence.

CANON — cross-brand growth laws [measured]; apply as DEFAULTS, cite as
"canon: …", and let peer/inventory evidence OVERRIDE them when it conflicts:
- Budget the mix ~60/40 brand-building to activation; cap overt promo at
  ~10-15% of posts. Rough split: ~35% educational, ~20% community, ~20%
  entertaining/story, ~15% proof, promo last.
- Consistency beats volume — a cadence the brand can actually sustain every
  week compounds far more than bursts. Do not prescribe more than they can keep.
- Below ~10k followers, engagement GIVEN (commenting on others, collabs,
  search/SEO) is the main follower-independent distribution — weight
  growth_actions there, not just posting more.
- Replying to comments lifts ranking on every platform; build a first-hour
  reply habit into the plan, not only publishing.

Be decisive and quantified. Respect the brand's constraints and taboos.

Return STRICT JSON:
{{"plan": [{{"platform": str, "format": "post"|"reel"|"carousel"|"story",
            "per_week": int, "topics": [str], "why": str,
            "evidence": [str]}}, ...],
  "growth_actions": [{{"action": str, "why": str, "evidence": [str]}}, ...]}}
"""


# ── playbooks: versioned algorithm briefs with change detection ───────

async def refresh_playbook(
    platform: str, tenant_id: UUID | None = None,
) -> dict:
    platform = (platform or "instagram").strip().lower()
    from .research import get_research_provider
    res = await get_research_provider().research(
        subject=f"{platform} algorithm for creators",
        focus="what the algorithm favors RIGHT NOW: formats, video length, "
              "posting cadence, hooks, ranking signals, recent changes, "
              "penalties. Current-year practitioner guidance only.",
    )
    briefing = (res.summary + "\n" + "\n".join(
        f"- {f}" for f in res.findings[:15]))[:7000]
    sources = [s.url for s in res.sources][:10]
    out = await get_llm().complete_json(
        system=_PLAYBOOK_SYSTEM.format(platform=platform),
        messages=[{"role": "user", "content": briefing or "(no research)"}],
        max_tokens=1400, temperature=0.3,
    )
    brief_md = str((out or {}).get("brief_md") or "")[:6000]
    key_points = [str(k)[:300] for k in (out or {}).get("key_points") or []][:12]
    if not brief_md:
        raise RuntimeError(f"no playbook produced for {platform}")

    async with acquire(tenant_id) as conn:
        prev = await conn.fetchrow(
            "SELECT version, key_points FROM platform_playbooks "
            "WHERE platform=$1 ORDER BY version DESC LIMIT 1", platform)
        prev_points: set[str] = set()
        version = 1
        if prev:
            version = int(prev["version"]) + 1
            pp = prev["key_points"]
            if isinstance(pp, str):
                pp = json.loads(pp)
            prev_points = {str(p).strip().lower() for p in (pp or [])}
        new_points = {p.strip().lower() for p in key_points}
        # Changed = meaningful drift in the atomic claims (not cosmetic).
        overlap = len(prev_points & new_points)
        changed = bool(prev_points) and overlap < 0.6 * max(
            1, min(len(prev_points), len(new_points)))
        await conn.execute(
            """INSERT INTO platform_playbooks
                 (platform, version, brief_md, key_points, sources, changed)
               VALUES ($1, $2, $3, $4::jsonb, $5::jsonb, $6)""",
            platform, version, brief_md, json.dumps(key_points),
            json.dumps(sources), changed)
    return {"platform": platform, "version": version, "changed": changed}


async def latest_playbooks(tenant_id: UUID | None = None) -> list[dict]:
    async with acquire(tenant_id) as conn:
        rows = await conn.fetch(
            """SELECT DISTINCT ON (platform)
                      platform, version, brief_md, key_points, sources,
                      changed, refreshed_at
                 FROM platform_playbooks
                ORDER BY platform, version DESC""")
    out = []
    for r in rows:
        kp, src = r["key_points"], r["sources"]
        if isinstance(kp, str):
            kp = json.loads(kp)
        if isinstance(src, str):
            src = json.loads(src)
        out.append({
            "platform": r["platform"], "version": r["version"],
            "brief_md": r["brief_md"], "key_points": kp or [],
            "sources": src or [], "changed": r["changed"],
            "refreshed_at": r["refreshed_at"].isoformat(),
        })
    return out


# ── peer benchmarking ─────────────────────────────────────────────────

async def snapshot_peers(tenant_id: UUID | None = None, cap: int = 5) -> int:
    from .brands import get_brand_profile
    profile = await get_brand_profile(tenant_id)
    peers = [str(p) for p in (profile or {}).get("peers") or []][:cap]
    if not peers:
        return 0
    from .research import get_research_provider
    made = 0
    for peer in peers:
        try:
            res = await get_research_provider().research(
                subject=peer[:120],
                focus="their social content operation: posting cadence "
                      "(per week), dominant formats, main topics, what "
                      "performs for them. Numbers where findable.",
            )
            if res.is_empty():
                continue
            stats = {
                "summary": res.summary[:1500],
                "findings": [f[:300] for f in res.findings[:8]],
            }
            async with acquire(tenant_id) as conn:
                await conn.execute(
                    """INSERT INTO peer_snapshots (peer, stats, sources)
                       VALUES ($1, $2::jsonb, $3::jsonb)""",
                    peer[:120], json.dumps(stats),
                    json.dumps([s.url for s in res.sources][:6]))
            made += 1
        except Exception as e:  # noqa: BLE001 — one peer failing ≠ no bench
            print(f"[strategy] peer snapshot '{peer}': {e}")
    return made


async def _latest_peer_block(tenant_id: UUID | None) -> str:
    async with acquire(tenant_id) as conn:
        rows = await conn.fetch(
            """SELECT DISTINCT ON (peer) peer, stats, captured_at
                 FROM peer_snapshots ORDER BY peer, captured_at DESC LIMIT 8""")
    parts = []
    for r in rows:
        st = r["stats"]
        if isinstance(st, str):
            st = json.loads(st)
        parts.append(f"### {r['peer']}\n{st.get('summary', '')[:800]}")
    return "\n\n".join(parts)


# ── output inventory (what the brand actually shipped/queued) ────────

async def _inventory_block(tenant_id: UUID | None) -> str:
    async with acquire(tenant_id) as conn:
        rows = await conn.fetch(
            """SELECT action_type,
                      coalesce(payload->>'format', action_type) AS fmt,
                      status, count(*) AS n
                 FROM actions
                WHERE created_at > now() - interval '14 days'
                GROUP BY 1, 2, 3 ORDER BY n DESC LIMIT 20""")
    if not rows:
        return "(no output in the last 14 days)"
    return "\n".join(
        f"- {r['fmt']} [{r['status']}]: {r['n']} in last 14 days" for r in rows)


# ── the Prescription ─────────────────────────────────────────────────

async def compose_prescription(tenant_id: UUID | None = None) -> dict:
    from .brands import brand_profile_block, get_brand_profile
    profile = await get_brand_profile(tenant_id)
    if not profile or not profile.get("intake_done"):
        raise ValueError("complete the Brand Setup first — a prescription "
                         "needs to know who the brand is")
    block = await brand_profile_block(tenant_id)
    playbooks = await latest_playbooks(tenant_id)
    pb_block = "\n\n".join(
        f"### {p['platform']} playbook v{p['version']}"
        f"{' (CHANGED since last refresh)' if p['changed'] else ''}\n"
        + "\n".join(f"- {k}" for k in p["key_points"])
        for p in playbooks) or "(no playbooks yet — ground in peers/inventory)"
    peers_block = await _latest_peer_block(tenant_id) or "(no peer snapshots yet)"
    inventory = await _inventory_block(tenant_id)
    # What the peer group posts that we do not — measured from real competitor
    # posts we hold and analysed, against our own output. Best-effort and
    # additive: a brand with no competitor shelf yet gets the plan it got before.
    try:
        from .competitor_gap import gap_block
        gap = await gap_block(tenant_id)
    except Exception:  # noqa: BLE001
        gap = ""
    # Shared marketing canon (growth laws, content mix, benchmarks, platform
    # specs) retrieved for this brand's niche — the strategist may cite it as
    # "canon: …". Additive and best-effort.
    try:
        from . import house_knowledge as _hk
        niche = str(profile.get("focus_area") or profile.get("industry")
                    or profile.get("niche") or profile.get("brand") or "").strip()
        canon_block = await _hk.grounding_block(
            f"social media growth strategy, content mix, posting cadence, "
            f"benchmarks and platform playbook for {niche or 'this brand'}",
            k=5, layers=("playbook", "spec", "rule"))
    except Exception:  # noqa: BLE001
        canon_block = ""
    canon_part = f"<canon>\n{canon_block}\n</canon>\n\n" if canon_block else ""

    out = await get_llm().complete_json(
        system=_PRESCRIPTION_SYSTEM,
        messages=[{"role": "user", "content":
                   f"{block}\n\n{canon_part}<playbooks>\n{pb_block}\n</playbooks>\n\n"
                   f"<peer_benchmarks>\n{peers_block}\n</peer_benchmarks>\n\n"
                   f"<content_gap>\n{gap or '(not computed)'}\n</content_gap>\n\n"
                   f"<recent_output>\n{inventory}\n</recent_output>"}],
        max_tokens=2200, temperature=0.4,
    )
    plan = []
    for ln in (out or {}).get("plan") or []:
        if not isinstance(ln, dict):
            continue
        evidence = [str(e)[:220] for e in (ln.get("evidence") or [])][:5]
        if not evidence:
            continue   # the hard rule: no evidence, no line
        try:
            per_week = max(1, min(21, int(ln.get("per_week") or 1)))
        except (TypeError, ValueError):
            continue
        plan.append({
            "platform": str(ln.get("platform") or "instagram")[:30],
            "format": str(ln.get("format") or "post")[:20],
            "per_week": per_week,
            "topics": [str(t)[:120] for t in (ln.get("topics") or [])][:6],
            "why": str(ln.get("why") or "")[:300],
            "evidence": evidence,
        })
    growth = []
    for g in (out or {}).get("growth_actions") or []:
        if not isinstance(g, dict) or not (g.get("action") or "").strip():
            continue
        growth.append({
            "action": str(g["action"])[:240],
            "why": str(g.get("why") or "")[:300],
            "evidence": [str(e)[:220] for e in (g.get("evidence") or [])][:4],
        })
    if not plan:
        raise RuntimeError("no groundable plan lines — refresh playbooks/"
                           "peers first")

    week_of = date.today() - timedelta(days=date.today().weekday())
    async with acquire(tenant_id) as conn:
        # A new proposal supersedes older unaccepted ones.
        await conn.execute(
            "UPDATE prescriptions SET status='expired' "
            "WHERE status='proposed'")
        pid = await conn.fetchval(
            """INSERT INTO prescriptions (week_of, plan, growth_actions)
               VALUES ($1, $2::jsonb, $3::jsonb) RETURNING id""",
            week_of, json.dumps(plan), json.dumps(growth))
    return {"id": str(pid), "week_of": week_of.isoformat(),
            "plan": plan, "growth_actions": growth, "status": "proposed"}


async def latest_prescription(tenant_id: UUID | None = None) -> dict | None:
    async with acquire(tenant_id) as conn:
        row = await conn.fetchrow(
            "SELECT * FROM prescriptions ORDER BY created_at DESC LIMIT 1")
    if not row:
        return None
    plan, growth, acc = row["plan"], row["growth_actions"], row["accepted_items"]
    if isinstance(plan, str):
        plan = json.loads(plan)
    if isinstance(growth, str):
        growth = json.loads(growth)
    if isinstance(acc, str):
        acc = json.loads(acc)
    return {
        "id": str(row["id"]), "week_of": row["week_of"].isoformat(),
        "status": row["status"], "plan": plan or [],
        "growth_actions": growth or [], "accepted_items": acc or [],
        "created_at": row["created_at"].isoformat(),
    }


_ACCEPT_CAP = 12   # max items produced per accept — cost sanity


async def accept_prescription(
    prescription_id: UUID, items: list[int] | None = None,
    tenant_id: UUID | None = None,
) -> dict:
    """Accept the whole plan (items=None) or specific line indexes. Each
    accepted line produces per_week pieces NOW through the standard
    machinery (voice+QA+gates → approval queue), capped for cost sanity."""
    async with acquire(tenant_id) as conn:
        row = await conn.fetchrow(
            "SELECT id, plan, accepted_items, status FROM prescriptions "
            "WHERE id=$1", prescription_id)
    if not row:
        raise ValueError("prescription not found")
    plan = row["plan"]
    if isinstance(plan, str):
        plan = json.loads(plan)
    plan = plan or []
    chosen = list(range(len(plan))) if items is None else [
        i for i in items if 0 <= i < len(plan)]
    if not chosen:
        raise ValueError("nothing to accept")

    from .autopilot_bulk import _make_text_post, _make_video
    produced: list[dict] = []
    errors: list[str] = []
    budget = _ACCEPT_CAP
    for i in chosen:
        line = plan[i]
        topics = line.get("topics") or ["the brand's core topic"]
        count = min(int(line.get("per_week") or 1), budget)
        for k in range(count):
            if budget <= 0:
                break
            topic = topics[k % len(topics)]
            idea = {"title": f"{line['format']}: {topic[:70]}", "topic": topic,
                    "pillar": ""}
            try:
                if line.get("format") == "reel":
                    made = await _make_video(
                        idea, line.get("platform") or "instagram", tenant_id,
                        video_template="full" if k % 2 == 0 else "split")
                    produced.append({"line": i, "kind": "reel",
                                     "ref": made.get("production_id")})
                else:
                    made = await _make_text_post(
                        idea, line.get("platform") or "instagram", tenant_id,
                        image_kind="designed" if k % 2 == 0 else "james")
                    produced.append({"line": i, "kind": "post",
                                     "ref": made.get("action_id")})
                budget -= 1
            except Exception as e:  # noqa: BLE001 — partial fills still count
                errors.append(f"line {i} #{k}: {e}")
        if budget <= 0:
            break

    status = "accepted" if items is None else "partial"
    async with acquire(tenant_id) as conn:
        await conn.execute(
            """UPDATE prescriptions
                  SET status=$2, accepted_items=$3::jsonb WHERE id=$1""",
            prescription_id, status, json.dumps(produced))
    return {"id": str(prescription_id), "status": status,
            "produced": len(produced), "errors": errors,
            "capped": budget <= 0}


# ── scheduled jobs ────────────────────────────────────────────────────

async def run_playbook_refresh(tenant_id: UUID, config: dict | None = None) -> None:
    from .brands import get_brand_profile
    profile = await get_brand_profile(tenant_id)
    platforms = [str(p).lower() for p in (profile or {}).get("platforms") or []]
    for platform in (platforms or ["instagram"])[:4]:
        try:
            r = await refresh_playbook(platform, tenant_id)
            if r["changed"]:
                print(f"[strategy] {platform} playbook CHANGED "
                      f"(v{r['version']}) for tenant {tenant_id}")
        except Exception as e:  # noqa: BLE001
            print(f"[strategy] playbook {platform}: {e}")


async def run_peer_snapshot(tenant_id: UUID, config: dict | None = None) -> None:
    await snapshot_peers(tenant_id)


async def run_weekly_prescription(tenant_id: UUID, config: dict | None = None) -> None:
    try:
        p = await compose_prescription(tenant_id)
        print(f"[strategy] prescription {p['id']} composed for {tenant_id} "
              f"({len(p['plan'])} lines)")
    except ValueError:
        pass   # intake not done — silently skip
    except Exception as e:  # noqa: BLE001
        print(f"[strategy] prescription failed for {tenant_id}: {e}")


__all__ = [
    "refresh_playbook", "latest_playbooks", "snapshot_peers",
    "compose_prescription", "latest_prescription", "accept_prescription",
    "run_playbook_refresh", "run_peer_snapshot", "run_weekly_prescription",
]
