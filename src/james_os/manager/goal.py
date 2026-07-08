"""Goal Agent — sets the brand's north star (spec §3.4 / D4).

Negotiates concrete growth targets from where the brand stands (audited
baselines) and where the tier it aims at sits (tracked-peer benchmarks): 'to
reach the aspirational tier, hit X followers at Y cadence by <date>'. Writes
each target to the profile 'goals' section as source='negotiated' (D2: ranks
with user_stated — the human accepted it), so the Strategist and the daily
planner have a concrete objective to plan toward. Nothing can 'reach the goal'
until the goal exists.

Ported from bm2.0 backend/app/agents/goal.py: own baselines come from the
audited channels.{platform}.{metric} envelope fields (the substrate's
stand-in for ConnectedAccount.baseline — same mapping learning uses), and
the tier benchmark from the competitors.benchmarks field the peer tracker
(peers.py) derives over tracked leader+aspirational snapshots.
"""

from datetime import datetime, timezone
from uuid import UUID

from .. import db
from . import profile, runs
from .contracts import Citation, FieldWrite, Source
from .learning import _current_baselines
from .profile import write_field
from .providers import get_providers

AGENT = "goal"

PROMPT_SYSTEM = (
    "You are a brand manager setting realistic, motivating growth targets. You "
    "ground every target in the brand's current baseline and the benchmark of "
    "the tier it aims at — ambitious but reachable, never fantasy numbers."
)
PROMPT = (
    "Negotiate growth targets (the north star) for this brand.\n\n"
    "OWN BASELINE (audited):\n{baselines}\n\n"
    "TIER BENCHMARK (tracked aspirational/leader peers):\n{benchmark}\n\n"
    "BRAND GOALS CONTEXT (what the owner said matters):\n{goals_ctx}\n\n"
    "Propose 2-4 growth targets. For each pick a platform + metric (followers, "
    "avg_engagement, or cadence_per_week), a realistic target given the "
    "baseline and the peer benchmark, a timeframe in days (30-180), a priority "
    "0-1, and a one-sentence rationale tying it to the baseline and benchmark. "
    "These are growth targets. Return JSON only: {{\"goals\": [{{\"platform\": "
    "str, \"metric\": str, \"baseline\": number|null, \"target\": number, "
    "\"timeframe_days\": int, \"priority\": number, \"rationale\": str}}], "
    "\"narrative\": str}}."
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


# donor _tier_benchmark's empty shape, when no benchmark has been derived yet
_EMPTY_BENCHMARK = {
    "peer_count": 0,
    "median_followers": None,
    "median_avg_engagement": None,
    "median_cadence_per_week": None,
}


async def _tier_benchmark(conn) -> dict:
    """The tier the brand aims at — the competitors.benchmarks envelope field
    peers.snapshot_all derives (median follower/engagement/cadence across
    tracked leader+aspirational peers' latest snapshots, the donor's math)."""
    for f in await profile.current_fields(conn, "competitors"):
        if f["field_key"] == "competitors.benchmarks" and f["item_key"] is None:
            v = f["value"].get("v") if isinstance(f["value"], dict) else f["value"]
            if isinstance(v, dict):
                return v
    return dict(_EMPTY_BENCHMARK)


async def run(tenant_id: UUID | None = None, config: dict | None = None) -> dict:
    providers = get_providers()
    handle = await runs.start_run(
        AGENT, trigger=(config or {}).get("trigger", "manual"), tenant_id=tenant_id
    )
    try:
        async with db.acquire(tenant_id) as conn:
            baselines = await _current_baselines(conn)
            benchmark = await _tier_benchmark(conn)
            goals_ctx = [
                {"field": f["field_key"], "value": (f["value"].get("v") if isinstance(f["value"], dict) else f["value"])}
                for f in await profile.current_fields(conn, "goals")
            ]
        prompt = PROMPT.format(
            baselines=baselines or "(no audited accounts yet)",
            benchmark=benchmark,
            goals_ctx=goals_ctx or "(none stated)",
        )
        raw = await providers.llm.complete_json("strategy", PROMPT_SYSTEM, prompt)

        written = []
        async with db.acquire(tenant_id) as conn:
            for g in (raw.get("goals") or []):
                if not isinstance(g, dict):
                    continue
                platform = str(g.get("platform") or "overall").lower()
                metric = str(g.get("metric") or "followers")
                try:
                    target = float(g.get("target"))
                except (TypeError, ValueError):
                    continue
                value = {
                    "baseline": g.get("baseline"),
                    "target": target,
                    "timeframe_days": int(g.get("timeframe_days") or 90),
                    "priority": float(g.get("priority") or 0.5),
                    "rationale": str(g.get("rationale") or ""),
                    "set_at": _now().isoformat(),
                }
                await write_field(
                    conn,
                    FieldWrite(
                        section="goals",
                        field_key=f"goals.{platform}.{metric}",
                        value=value,
                        source=Source.NEGOTIATED,
                        citations=[Citation(ref="goal_agent:baseline+peer_benchmark")],
                        updated_by=AGENT,
                    ),
                )
                written.append({"platform": platform, "metric": metric, **value})

        report = {
            "brand_id": str(tenant_id or ""),
            "goals": written,
            "narrative": str(raw.get("narrative") or "").strip(),
            "benchmark": benchmark,
        }
    except Exception as exc:
        await runs.finish_run(handle, error=str(exc))
        raise
    await runs.finish_run(handle, output={"goal_count": len(written)})
    return report
