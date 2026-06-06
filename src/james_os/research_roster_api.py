"""HTTP surface for the Social Research roster.

    GET  /research/roster          → the watched creators + scrape stats
    POST /research/roster/refresh  → kick a background scrape (returns immediately)
    GET  /research/roster/status   → last refresh time + whether a scrape is due

Refresh is fire-and-forget: a roster scrape hits Apify for every handle and
can take a while, so the POST hands it to BackgroundTasks and returns
{started: True} rather than blocking the request.
"""

from fastapi import APIRouter, BackgroundTasks

from .research_roster import get_roster, refresh_roster, roster_status

router = APIRouter()


@router.get("/research/roster")
async def research_roster_get() -> dict:
    return await get_roster()


@router.post("/research/roster/refresh")
async def research_roster_refresh(
    background_tasks: BackgroundTasks, limit: int = 15
) -> dict:
    background_tasks.add_task(refresh_roster, limit=limit)
    return {"started": True}


@router.get("/research/roster/status")
async def research_roster_status() -> dict:
    return await roster_status()
