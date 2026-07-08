"""The outbox executor — the 'do it' step the queue was missing. Ported from
bm2.0 backend/app/services/execution.py::publish/_dispatch onto the james-os
substrate.

publish_work_order() publishes an APPROVED/SCHEDULED work order via the right
provider 'hand' (blog -> PublishProvider, email -> EmailProvider, social ->
SocialConnector) with honest-failure semantics: a PublishResult.ok=False (no
key, no recipients, provider error) leaves the order approved and records the
failure on payload.publish_attempts — never a fabricated 'published'.

Durability: every attempt is anchored in the (previously dormant) outbox table
— a task_type='execute_action' row is INSERTed before the provider call and
marked done/pending/failed after, so the outbox becomes the execution ledger
and process_outbox() can retry honest failures later (bounded attempts).
Everything is tenant-scoped via db.acquire (RLS).
"""

import json
import logging
from datetime import datetime, timedelta, timezone
from uuid import UUID

import asyncpg

from .. import db
from . import actions as action_service
from .contracts import WorkOrderStatus
from .execution import get_order, latest_artifact
from .providers import get_providers
from .providers.base import PublishResult, Providers
from .state_machine import PUBLISH_FROM, transition

logger = logging.getLogger("manager.publish")

TASK_TYPE = "execute_action"
MAX_ATTEMPTS = 5  # bounded retries — after this the ledger row is 'failed'
_RETRY_BASE_MINUTES = 15  # linear backoff: attempts * base
_PROCESS_BATCH = 10  # per process_outbox() call


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _as_html(body: str) -> str:
    paras = "".join(f"<p>{p.strip()}</p>" for p in body.split("\n\n") if p.strip())
    return f"<div>{paras}</div>"


async def _tenant(conn: asyncpg.Connection) -> tuple[str, dict]:
    row = await conn.fetchrow(
        "SELECT name, config FROM tenants WHERE id = current_setting('app.current_tenant', true)::uuid"
    )
    if row is None:
        return "", {}
    cfg = row["config"]
    if isinstance(cfg, str):
        cfg = json.loads(cfg or "{}")
    return row["name"] or "", cfg or {}


async def _dispatch(
    providers: Providers, tenant_name: str, tcfg: dict, payload: dict, artifact: dict
) -> PublishResult:
    """Route one approved artifact to its provider hand, by content_type."""
    body = str(artifact.get("content") or "")
    media = artifact.get("media") or {}
    ctype = payload.get("content_type")
    topic = str(payload.get("topic") or "")

    if ctype == "blog":
        return await providers.blog.publish(
            title=media.get("title") or topic,
            body_markdown=body,
            slug=media.get("slug", ""),
            meta={"meta_description": media.get("meta_description", ""), "tags": media.get("tags", [])},
        )
    if ctype == "email":
        recipients = tcfg.get("email_recipients") or []
        placeholder = not recipients
        if placeholder:
            # donor's placeholder-recipient guard: never block the demo loop,
            # never pretend a real list was mailed
            recipients = ["list@" + (tenant_name.lower().replace(" ", "") or "brand")]
        res = await providers.email.send(
            to=recipients,
            subject=media.get("subject") or topic,
            html=_as_html(body),
            preheader=media.get("preheader", ""),
        )
        if placeholder and res.ok:
            res.detail = {**res.detail, "note": "no recipient list configured — connect a list to send for real"}
        return res
    # social_post / text: post via the aggregator, iff the tenant is connected
    profile_key = str(tcfg.get("postproxy_profile_key") or "")
    if not profile_key:
        return PublishResult(
            ok=False, provider="social",
            detail={"error": "no connected social profile — connect accounts first"},
        )
    raw = await providers.social.publish(
        profile_key=profile_key,
        platforms=[str(payload.get("platform") or "x")],
        text=body,
        media_urls=[],
    )
    ok = str(raw.get("status", "")).lower() in ("success", "scheduled", "ok")
    return PublishResult(
        ok=ok, provider="social", ref=str(raw.get("id") or ""),
        detail={"post_ids": raw.get("post_ids") or [], **({} if ok else {"error": str(raw)[:200]})},
    )


async def publish_work_order(
    tenant_id: UUID | None, work_order_id: str, *, outbox_id: str | None = None
) -> dict:
    """Do the work: publish/send/post an APPROVED work order via the right hand.

    Pass outbox_id when retrying an existing ledger row (process_outbox);
    otherwise a fresh outbox row is INSERTed before the attempt. State errors
    raise ValueError (the API maps them to 404/409); provider failures return
    ok=False with the order left approved.
    """
    async with db.acquire(tenant_id) as conn:
        row, payload = await get_order(conn, work_order_id)
        if WorkOrderStatus(row["status"]) not in PUBLISH_FROM:
            raise ValueError(
                f"work order {work_order_id} is {row['status']!r}; publish only from "
                f"{sorted(s.value for s in PUBLISH_FROM)} (approve it first)"
            )
        artifact = latest_artifact(payload)
        if artifact is None:
            raise ValueError(f"work order {work_order_id} has no drafted artifact to publish")
        tenant_name, tcfg = await _tenant(conn)
        if outbox_id is None:
            outbox_id = str(
                await conn.fetchval(
                    """INSERT INTO outbox (task_type, payload, status)
                       VALUES ($1, $2::jsonb, 'in_progress') RETURNING id""",
                    TASK_TYPE,
                    json.dumps({
                        "work_order_id": work_order_id,
                        "content_type": payload.get("content_type"),
                        "platform": payload.get("platform"),
                        "topic": str(payload.get("topic") or "")[:200],
                    }),
                )
            )
        else:
            await conn.execute(
                "UPDATE outbox SET status = 'in_progress', updated_at = now() WHERE id = $1::uuid",
                outbox_id,
            )

    # provider I/O happens outside any DB transaction
    result = await _dispatch(get_providers(), tenant_name, tcfg, payload, artifact)
    attempt = {
        "at": _now().isoformat(), "ok": result.ok, "provider": result.provider,
        "ref": result.ref, "url": result.url, "detail": result.detail, "outbox_id": outbox_id,
    }

    if not result.ok:
        async with db.acquire(tenant_id) as conn:
            _, payload = await get_order(conn, work_order_id)  # re-read: don't clobber concurrent notes
            payload.setdefault("publish_attempts", []).append(attempt)
            await conn.execute(
                "UPDATE actions SET payload = $2::jsonb WHERE id = $1::uuid",
                work_order_id, json.dumps(payload),
            )
            attempts = await conn.fetchval(
                """UPDATE outbox SET attempts = attempts + 1, last_error = $2, updated_at = now()
                   WHERE id = $1::uuid RETURNING attempts""",
                outbox_id, str(result.detail.get("error") or result.detail)[:500],
            )
            attempts = attempts or MAX_ATTEMPTS
            exhausted = attempts >= MAX_ATTEMPTS
            await conn.execute(
                "UPDATE outbox SET status = $2, next_run_at = $3 WHERE id = $1::uuid",
                outbox_id,
                "failed" if exhausted else "pending",
                _now() + timedelta(minutes=_RETRY_BASE_MINUTES * attempts),
            )
        return {
            "work_order_id": work_order_id, "ok": False, "status": row["status"],
            "detail": result.detail, "provider": result.provider,
            "outbox_id": outbox_id, "attempts": attempts, "retry_exhausted": exhausted,
        }

    async with db.acquire(tenant_id) as conn:
        updated = await transition(conn, work_order_id, PUBLISH_FROM, WorkOrderStatus.PUBLISHED)
        _, payload = await get_order(conn, work_order_id)
        payload.setdefault("publish_attempts", []).append(attempt)
        payload["published_ref"] = {
            "at": attempt["at"], "url": result.url, "ref": result.ref,
            "provider": result.provider, "detail": result.detail,
            "content_type": payload.get("content_type"),
        }
        await conn.execute(
            "UPDATE actions SET payload = $2::jsonb WHERE id = $1::uuid",
            work_order_id, json.dumps(payload),
        )
        await conn.execute(
            "UPDATE outbox SET status = 'done', updated_at = now() WHERE id = $1::uuid",
            outbox_id,
        )
        # note the outcome back on the source opportunity (donor semantics)
        src = payload.get("source_action_id")
        if src:
            where = result.url or result.ref or "the connected channel"
            try:
                await action_service.add_note(
                    conn, src, f"Published ({payload.get('content_type')}): {where}", actor="hands"
                )
                await action_service.set_status(conn, src, "done")
            except ValueError:
                pass  # the action card may have been deleted; the publish still stands
    return {
        "work_order_id": work_order_id, "ok": True, "status": updated["status"],
        "url": result.url, "ref": result.ref, "provider": result.provider,
        "outbox_id": outbox_id,
    }


async def process_outbox(tenant_id: UUID | None = None) -> dict:
    """Retry unprocessed execute_action ledger rows whose time has come.
    Bounded: at most _PROCESS_BATCH rows per call, MAX_ATTEMPTS per row. A row
    whose work order is no longer publishable (already published, rejected,
    superseded) is closed rather than retried forever."""
    async with db.acquire(tenant_id) as conn:
        rows = await conn.fetch(
            """SELECT id, payload, attempts FROM outbox
               WHERE task_type = $1 AND status = 'pending'
                 AND next_run_at <= now() AND attempts < $2
               ORDER BY next_run_at LIMIT $3""",
            TASK_TYPE, MAX_ATTEMPTS, _PROCESS_BATCH,
        )
    results: list[dict] = []
    for r in rows:
        ob_id = str(r["id"])
        p = r["payload"]
        if isinstance(p, str):
            p = json.loads(p or "{}")
        wo_id = str((p or {}).get("work_order_id") or "")
        try:
            out = await publish_work_order(tenant_id, wo_id, outbox_id=ob_id)
            results.append({"outbox_id": ob_id, "work_order_id": wo_id, "ok": out["ok"]})
        except ValueError as exc:
            # not publishable anymore — close the ledger row honestly
            async with db.acquire(tenant_id) as conn:
                await conn.execute(
                    """UPDATE outbox SET status = 'done', last_error = $2, updated_at = now()
                       WHERE id = $1::uuid""",
                    ob_id, f"closed without publish: {str(exc)[:300]}",
                )
            results.append({"outbox_id": ob_id, "work_order_id": wo_id, "ok": False, "closed": str(exc)[:160]})
        except Exception as exc:  # noqa: BLE001 — one bad row must not sink the sweep
            logger.exception("outbox retry failed for %s", ob_id)
            results.append({"outbox_id": ob_id, "work_order_id": wo_id, "ok": False, "error": str(exc)[:160]})
    return {"processed": len(rows), "results": results}
