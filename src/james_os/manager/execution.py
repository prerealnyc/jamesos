"""The execution spine — turn an approved opportunity into done, published work.
Ported from bm2.0 backend/app/services/execution.py onto the james-os
substrate (the measure() half already lives in manager/learning.py).

Every intelligence 'eye' surfaces an action_items card ("here's what to make");
this service is the set of HANDS that acts on one end to end:

    opportunity (action_items row)
      -> execute_opportunity: create a work order on the actions queue and
         draft the asset through the EXISTING james-os content engine
         (grounded, voice-QA'd, honest refusal without a voice corpus)
      -> [human approves in the queue — the D5/D7 gate, execution_api]
      -> publish (manager/publish.py): actually DO it
      -> measure (manager/learning.py): capture impact, learn

Text formats (blog / email / social copy) are drafted here. Video/image are
NOT: the work order is created and left queued with payload routing
{'to': 'production'} — the james-os video/image pipelines are in-process and
pick those up; there is no cross-project HTTP hand-off anymore. Everything is
tenant-scoped via db.acquire (RLS); nothing publishes without approval.
"""

import json
import re
from datetime import datetime, timezone
from uuid import UUID

import asyncpg

from .. import db
from ..content import ContentBrief, generate_content
from . import actions as action_service
from . import profile, runs
from .contracts import WorkOrderStatus
from .state_machine import GENERATE_FROM, PASS_REVIEW_FROM, REVIEW_FROM, transition

AGENT = "hands"

# normalize an opportunity's suggested format to a canonical content_type
_ALIASES = {
    "faq": "blog", "article": "blog", "whitepaper": "blog", "guide": "blog", "blog": "blog",
    "email": "email", "newsletter": "email",
    "post": "social_post", "text_post": "social_post", "social_post": "social_post",
    "thread": "social_post", "caption": "social_post", "social": "social_post",
    "video": "video", "reel": "video", "short": "video", "image": "image", "carousel": "image",
}
# formats a hand can DO here; the rest stay queued for the production pipelines
HANDS_FORMATS = {"blog", "email", "social_post"}
EXTERNAL_FORMATS = {"video", "image"}
# default content_type when the opportunity doesn't name one, by its kind
# (donor kinds + the james-os action_items kinds: collab|radar|trend|press|
# question|appearance|goal|promote|content)
_KIND_DEFAULT = {
    "content": "blog", "research": "blog", "press": "social_post",
    "collaboration": "email", "collab": "email", "visibility": "social_post",
    "general": "social_post", "radar": "blog", "trend": "social_post",
    "question": "blog", "appearance": "email", "goal": "social_post",
    "promote": "social_post",
}

# content_type -> (ContentBrief platform hint, ContentBrief format) for the
# james-os engine; social keeps the resolved distribution platform.
_BRIEF_FORMATS = {"blog": ("website", "blog"), "email": ("newsletter", "email")}
# content_type -> stored artifact kind (donor's Artifact.kind vocabulary)
_ARTIFACT_KIND = {"blog": "blog", "email": "email", "social_post": "text"}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _slug(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return (s or "post")[:60]


def resolve_format(action: dict, fmt: str | None) -> str:
    """Donor's resolver: explicit format > the opportunity's own content_type >
    a default by the action kind; aliases collapse to a canonical type."""
    raw = (
        fmt
        or (action.get("meta") or {}).get("content_type")
        or _KIND_DEFAULT.get(str(action.get("kind") or ""), "social_post")
    )
    return _ALIASES.get(str(raw).lower(), "social_post")


def _evidence(action: dict) -> list[dict]:
    meta = action.get("meta") or {}
    links = meta.get("links") or meta.get("reddit_links") or []
    return [{"url": str(u), "note": "from the opportunity"} for u in links if u][:6]


def _crude_prediction() -> dict:
    """Honest v0: no per-piece benchmark yet, so the prediction is a labelled
    placeholder rather than a fabricated number. The measure step
    (manager/learning.py) fills actuals and the gap becomes an insight."""
    return {"basis": "opportunity_v0_no_benchmark", "note": "predicted impact unknown until analytics wired"}


async def _require_action(conn: asyncpg.Connection, action_id: str) -> dict:
    row = await conn.fetchrow(
        "SELECT id, kind, title, detail, status, meta FROM action_items WHERE id = $1::uuid",
        action_id,
    )
    if row is None:
        raise ValueError(f"action {action_id} not found")
    d = dict(row)
    if isinstance(d.get("meta"), str):
        d["meta"] = json.loads(d["meta"])
    d["id"] = str(d["id"])
    return d


async def _default_platform(conn: asyncpg.Connection, action: dict) -> str:
    """The opportunity's own platform when named, else the first audited
    channel from the profile envelope (the substrate's stand-in for the donor's
    ConnectedAccount table), else 'x'."""
    meta_platform = (action.get("meta") or {}).get("platform")
    if meta_platform:
        return str(meta_platform)
    for f in await profile.current_fields(conn, "channels"):
        parts = f["field_key"].split(".")
        if len(parts) == 3:
            return parts[1]
    return "x"


def _media(ctype: str, topic: str, platform: str, links: list[str], body: str) -> dict:
    """Format-specific fields the publish step needs (title/slug for a blog,
    subject/preheader for an email) — donor hands._media, kept with the draft."""
    if ctype == "blog":
        title = (body.splitlines()[0].strip() if body.strip() else topic) or topic
        title = title.lstrip("# ").strip()[:200]
        rest = body.split("\n", 1)[1].strip() if "\n" in body else body
        return {
            "title": title, "slug": _slug(title or topic),
            "meta_description": rest[:155], "tags": [], "links": links,
        }
    if ctype == "email":
        subject = topic.strip()
        return {"subject": subject[:150], "preheader": subject[:120], "links": links}
    return {"platform": platform, "links": links}


def payload_of(row: asyncpg.Record | dict) -> dict:
    p = row["payload"]
    return json.loads(p) if isinstance(p, str) else (p or {})


async def get_order(conn: asyncpg.Connection, work_order_id: str) -> tuple[dict, dict]:
    """(row dict, parsed payload) for one work order; ValueError when absent.
    RLS on the connection keeps this tenant-scoped."""
    row = await conn.fetchrow(
        """SELECT id, status, payload, created_at, decided_at, executed_at
           FROM actions WHERE id = $1::uuid AND action_type = 'work_order'""",
        work_order_id,
    )
    if row is None:
        raise ValueError(f"work order {work_order_id} not found")
    d = dict(row)
    d["id"] = str(d["id"])
    return d, payload_of(row)


def latest_artifact(payload: dict) -> dict | None:
    artifacts = [a for a in (payload.get("artifacts") or []) if isinstance(a, dict)]
    if not artifacts:
        return None
    return max(artifacts, key=lambda a: int(a.get("version") or 0))


async def execute_opportunity(
    tenant_id: UUID | None,
    action_id: str,
    *,
    fmt: str | None = None,
    trigger: str = "manual",
) -> dict:
    """Act on an opportunity: create its work order on the actions queue and
    draft the asset through the james-os content engine. Video/image work
    orders stay queued with routing={'to': 'production'} for the in-process
    production pipelines. The action_items card records what was done."""
    async with db.acquire(tenant_id) as conn:
        action = await _require_action(conn, action_id)
        ctype = resolve_format(action, fmt)
        platform = await _default_platform(conn, action)

        payload: dict = {
            "source": "opportunity",
            "source_action_id": action["id"],
            "topic": (action["title"] or "")[:300],
            "platform": platform,
            "content_type": ctype,
            "format": ctype,
            "rationale": action["detail"] or action["title"],
            "evidence": _evidence(action),
            "predicted_metrics": _crude_prediction(),
            "format_spec": {"route": "production"} if ctype in EXTERNAL_FORMATS else {},
            "artifacts": [],
        }
        if ctype in EXTERNAL_FORMATS:
            payload["routing"] = {"to": "production"}
        order_id = str(
            await conn.fetchval(
                """INSERT INTO actions (proposed_by, action_type, payload, status)
                   VALUES ('hands', 'work_order', $1::jsonb, 'queued') RETURNING id""",
                json.dumps(payload),
            )
        )

        if ctype in EXTERNAL_FORMATS:
            # the video/image pipelines live in this process — the queued order
            # with routing.to='production' is the hand-off, not an HTTP call
            await action_service.add_note(
                conn, action_id,
                f"Queued a {ctype} work order ({order_id}) for the production pipelines.",
                actor="hands",
            )
            return {
                "action_id": action["id"], "work_order_id": order_id, "content_type": ctype,
                "routed_to": "production", "status": WorkOrderStatus.QUEUED.value, "drafted": False,
            }

    # text formats: draft through the existing engine, wrapped in a job_run
    # (the donor wrapped hands.draft_order the same way)
    handle = await runs.start_run(
        AGENT, trigger=trigger,
        input={"action_id": action["id"], "work_order": order_id, "format": ctype},
        tenant_id=tenant_id,
    )
    try:
        async with db.acquire(tenant_id) as conn:
            await transition(conn, order_id, GENERATE_FROM, WorkOrderStatus.GENERATING)

        brief_platform, brief_format = _BRIEF_FORMATS.get(ctype, (platform, "post"))
        links = [e["url"] for e in payload["evidence"]]
        extra = f"Why it matters (the opportunity): {payload['rationale']}"
        if links:
            extra += "\nSource evidence (draw on it, never fabricate): " + " · ".join(links)
        draft = await generate_content(
            ContentBrief(
                platform=brief_platform,
                format=brief_format,
                topic=payload["topic"],
                extra_instructions=extra[:1200],
            ),
            tenant_id,
        )
        if draft.status == "not_generated" or not (draft.draft or "").strip():
            # honest refusal/failure: no fake asset; the order stays
            # 'generating' with the failed run on record (donor semantics)
            raise RuntimeError(
                f"drafting failed for work order {order_id}: "
                f"{draft.note or 'engine returned no draft'}"
            )

        qa = {
            "voice_score": round(float(draft.voice_score or 0.0), 3),
            "passed": bool(draft.qa.passed) if draft.qa else None,
            "drift": list(draft.qa.drift) if draft.qa else [],
        }
        artifact = {
            "version": len(payload["artifacts"]) + 1,
            "kind": _ARTIFACT_KIND.get(ctype, "text"),
            "content": draft.draft,
            "media": _media(ctype, payload["topic"], platform, links, draft.draft),
            "review": qa,
            "angle": draft.angle,
            "created_at": _now().isoformat(),
        }
        payload["artifacts"] = [artifact]
        payload["qa"] = qa
        if draft.action_id:
            # the engine queued its own pending 'content' row (its human gate);
            # the work order IS the approval surface here, so supersede the
            # duplicate — but keep the row: the learning hooks
            # (james_os.learning.record_approval/record_rejection) read it.
            payload["content_action_id"] = str(draft.action_id)

        async with db.acquire(tenant_id) as conn:
            if draft.action_id:
                await conn.execute(
                    "UPDATE actions SET status = 'superseded' "
                    "WHERE id = $1 AND action_type = 'content' AND status = 'pending'",
                    draft.action_id,
                )
            # the engine's voice-QA already ran — walk both D5 edges; flagged
            # drafts still land pending_approval (a human decides, D5/D7)
            await transition(conn, order_id, REVIEW_FROM, WorkOrderStatus.REVIEW)
            await transition(conn, order_id, PASS_REVIEW_FROM, WorkOrderStatus.PENDING_APPROVAL)
            await conn.execute(
                "UPDATE actions SET payload = $2::jsonb WHERE id = $1::uuid",
                order_id, json.dumps(payload),
            )
            await action_service.add_note(
                conn, action_id,
                f"Drafted a {ctype} (work order {order_id}); "
                f"{'passed' if qa['passed'] else 'flagged by'} the voice-QA gate.",
                actor="hands",
            )
        result = {
            "action_id": action["id"], "work_order_id": order_id, "content_type": ctype,
            "status": WorkOrderStatus.PENDING_APPROVAL.value, "drafted": True,
            "review_passed": bool(qa["passed"]), "voice_score": qa["voice_score"],
            "content_action_id": payload.get("content_action_id"),
        }
    except Exception as exc:
        await runs.finish_run(handle, error=str(exc))
        raise
    await runs.finish_run(handle, output=result)
    return result
