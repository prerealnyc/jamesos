"""Daily Strategist — the brand-manager brain that drafts TODAY's activities.

Given the gap to the goal (Goal Agent's north star vs the audited baseline),
what's working for the fastest-growing competitors (Growth Intelligence), and
the brand's own position, it proposes today's concrete moves — posting AND
non-posting plays (a format shift, an outreach, a PR angle) — that set the
brand apart and close the gap. Each activity is persisted as a trackable
action item and surfaced in the daily digest.

Ported from bm2.0 backend/app/agents/daily_plan.py: baselines come from the
audited channels.{platform}.{metric} envelope fields (via learning), measured
results from learning.what_worked, and algorithm briefs from the
performance.algorithm_brief envelope fields the Algorithm eye writes (its
platform_playbooks copy is the versioned/diffed twin of the same data).
"""

from datetime import datetime, timezone
from uuid import UUID

from .. import db
from . import actions as action_service
from . import growth as growth_agent
from . import learning, profile, runs
from .learning import _current_baselines
from .providers import get_providers

AGENT = "daily_plan"

PROMPT_SYSTEM = (
    "You are the brand's daily manager. Each day you decide the few highest-"
    "leverage activities that move the brand toward its goals, informed by what "
    "is working for the competitors pulling ahead. You mix content and non-"
    "content moves and never propose busywork — every item has a clear reason."
)
PROMPT = (
    "Draft TODAY's activities for this brand.\n\n"
    "GOALS (north star to close the gap toward):\n{goals}\n\n"
    "OWN BASELINE:\n{baselines}\n\n"
    "WHAT'S WORKING FOR FAST-GROWING COMPETITORS:\n{growth}\n\n"
    "WHAT WORKED FOR THIS BRAND (measured results of past work — double down "
    "on over-performers, drop what flopped):\n{worked}\n\n"
    "PLATFORM ALGORITHM NOTES (how each platform currently rewards content):\n{algo}\n\n"
    "Propose 3-5 activities for today that move toward the goals and borrow "
    "what's working, mixing content (a specific post/format/topic) and non-"
    "content moves (outreach, PR, a series, a format experiment). This is the "
    "daily plan. Return JSON only: {{\"activities\": [{{\"title\": str, "
    "\"type\": \"content|outreach|pr|experiment|community|other\", \"detail\": "
    "str, \"why\": str (tie to a goal, a competitor tactic, a measured result, "
    "or an algorithm rule)}}], \"headline\": str}}."
)


async def run(tenant_id: UUID | None = None, config: dict | None = None) -> dict:
    providers = get_providers()
    handle = await runs.start_run(
        AGENT, trigger=(config or {}).get("trigger", "manual"), tenant_id=tenant_id
    )
    try:
        async with db.acquire(tenant_id) as conn:
            goals = [
                {"field": f["field_key"], "value": (f["value"].get("v") if isinstance(f["value"], dict) else f["value"])}
                for f in await profile.current_fields(conn, "goals")
            ]
            baselines = await _current_baselines(conn)
            worked = await learning.what_worked(conn)
            algo = [
                {"platform": f["item_key"], "brief": (f["value"].get("v") if isinstance(f["value"], dict) else f["value"])}
                for f in await profile.current_fields(conn, "performance")
                if f["field_key"] == "performance.algorithm_brief"
            ]
        growth_report = (config or {}).get("growth_report")
        if growth_report is None:
            growth_report = await growth_agent.run(tenant_id)
            # the nested run unbound the active-run contextvar on finish;
            # rebind so this run's remaining LLM usage keeps accruing here
            runs.bind_active_run(handle)
        growth_ctx = {
            "drivers": growth_report.get("drivers"),
            "borrow": growth_report.get("borrow"),
            "narrative": growth_report.get("narrative"),
            "top_competitors": [
                {"handle": c["handle"], "followers_per_week": c.get("followers_per_week")}
                for c in (growth_report.get("competitors") or [])[:3]
            ],
        }
        prompt = PROMPT.format(
            goals=goals or "(no goal set yet — set the north star first)",
            baselines=baselines or "(no audited baseline yet)",
            growth=growth_ctx,
            worked=worked or "(nothing measured yet — publish and measure to teach the plan)",
            algo=algo or "(no algorithm briefs yet — run the algorithm agent)",
        )
        raw = await providers.llm.complete_json("strategy", PROMPT_SYSTEM, prompt)

        activities = [a for a in (raw.get("activities") or []) if isinstance(a, dict)]
        # persist each as a trackable action item (dedupe per day+title)
        day = datetime.now(timezone.utc).date().isoformat()
        async with db.acquire(tenant_id) as conn:
            for a in activities:
                title = str(a.get("title") or "").strip()
                if not title:
                    continue
                await action_service.upsert_action(
                    conn,
                    kind="content" if a.get("type") == "content" else "general",
                    title=title,
                    detail=f"{a.get('detail', '')} — {a.get('why', '')}",
                    meta={"source": "daily_plan", "activity_type": a.get("type"), "day": day},
                    dedupe_key=f"daily:{day}:{title[:80]}",
                )
        report = {
            "brand_id": str(tenant_id or ""),
            "headline": str(raw.get("headline") or "").strip(),
            "activities": activities,
            "growth_note": growth_report.get("note"),
        }
    except Exception as exc:
        await runs.finish_run(handle, error=str(exc))
        raise
    await runs.finish_run(handle, output={"activity_count": len(activities)})
    return report
