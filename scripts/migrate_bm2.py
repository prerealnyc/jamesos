"""P6 of the unification: migrate bm2.0's brands into the merged Brand Manager.

Reads bm2.0's SQLite (backend/dev.db in the donor repo) and lands every
brand's data on the james-os substrate — STRICTLY ADDITIVE (the merge
contract: James's live tenant is ENRICHED, never overwritten; nothing here
supersedes, updates, or deletes an existing row).

What moves where:
  profile_fields        -> profile_fields (envelope preserved: versions,
                           supersession chains, confidence, citations)
  memory_chunks         -> events (exemplar -> voice_corpus w/ origin tag;
                           post -> category 'post'; insight -> 'insight';
                           embedded with the configured embedder)
  peer_entities         -> tenants.config['watchlist'] entries
  peer_snapshots        -> peer_snapshots
  action_items          -> action_items (dedupe_key preserved)
  work_orders(+artifacts,
   approval_events)     -> actions rows (action_type='work_order', D5 status
                           vocabulary is shared post-P1; artifacts/approvals
                           embedded in payload)
  plans                 -> prescriptions (items reconstructed from the
                           plan's work orders)
  question_instances    -> brand_questions (text/field_key joined from the
                           donor's own template table)
  connected_accounts    -> connections rows + tenants.config
                           postproxy_profile_key/brand handles

Skipped on purpose: agent_runs (dead bookkeeping), daily_digests
(regenerated daily), question_templates (already code in
manager/question_bank.py).

DRY-RUN BY DEFAULT: everything runs inside one transaction that is rolled
back unless --execute is passed, so the printed report is exactly what an
execute would do.

Usage:
  .venv/bin/python scripts/migrate_bm2.py --sqlite "/path/to/dev.db" \
      [--map "James Prendamano=<tenant-uuid>"] [--brands "A,B"] [--execute]

Unmapped brands get a fresh tenant (config.manager_v2=true). DATABASE_URL
env selects the target Postgres (local rehearsal vs production Supabase).

PRODUCTION-RUN CHECKLIST (adversarial-verification findings, 2026-07-09):
  1. Run --dry-run first and read the whole report.
  2. Map James explicitly: --map "James Prendamano=<his live tenant uuid>".
  3. The watchlist merge locks the tenants row (FOR UPDATE) — still prefer a
     quiet window; the lock briefly blocks the live app's config writes.
  4. RLS reality check: the migration user may be a superuser (fine — tenant
     stamping is explicit), but the APP must connect as a non-superuser,
     non-BYPASSRLS role on any multi-tenant cluster or RLS is bypassed and
     tenants bleed together. Supabase's standard roles are safe; a local
     docker/homebrew superuser role is NOT.
"""

import argparse
import asyncio
import json
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from uuid import NAMESPACE_URL, UUID, uuid5

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import asyncpg  # noqa: E402

from james_os.config import settings  # noqa: E402
from james_os.embedder import make_embedder  # noqa: E402

DEFAULT_TENANT = UUID("00000000-0000-0000-0000-000000000001")

_ORIGIN_MAP = {"auditor_promotion": "audited", "approval": "approved", "queue": "approved"}
_QI_STATE = {"pending": "open", "asked": "open", "auto_answered": "answered",
             "answered": "answered", "confirmed": "confirmed", "dismissed": "dismissed"}
_PLAN_STATUS = {"draft": "proposed", "proposed": "proposed", "active": "accepted",
                "superseded": "expired", "expired": "expired"}


def _j(v, default):
    if v is None:
        return default
    if isinstance(v, (dict, list)):
        return v
    try:
        return json.loads(v)
    except (TypeError, ValueError):
        return default


def _dt(v) -> datetime | None:
    if not v:
        return None
    dt = datetime.fromisoformat(str(v))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


class Report:
    def __init__(self) -> None:
        self.rows: dict[str, dict[str, dict[str, int]]] = defaultdict(dict)
        self.notes: list[str] = []

    def add(self, brand: str, table: str, found: int, migrated: int) -> None:
        self.rows[brand][table] = {"found": found, "migrated": migrated,
                                   "skipped": found - migrated}

    def print(self, executed: bool) -> None:
        mode = "EXECUTED" if executed else "DRY RUN (rolled back — nothing written)"
        print(f"\n{'=' * 72}\nbm2.0 -> Brand Manager migration · {mode}\n{'=' * 72}")
        for brand, tables in self.rows.items():
            print(f"\n{brand}")
            for table, c in tables.items():
                skip = f" · {c['skipped']} already present" if c["skipped"] else ""
                print(f"  {table:<22} {c['migrated']:>5} migrated / {c['found']} found{skip}")
        if self.notes:
            print("\nNotes:")
            for n in self.notes:
                print(f"  - {n}")
        print()


async def _tenant_for(conn: asyncpg.Connection, brand: dict, mapping: dict[str, str],
                      report: Report) -> UUID:
    """Resolve the target tenant UNDER RLS. Production's tenants policy only
    exposes the row matching app.current_tenant, so: (a) existence checks
    happen AFTER set_config; (b) unmapped brands get a DETERMINISTIC uuid5
    (idempotent re-runs re-derive the same id — name enumeration is
    impossible under the policy); (c) inserts carry the pre-set id so the
    new row passes the policy's implicit WITH CHECK."""
    mapped = mapping.get(brand["name"])
    if mapped:
        tid = UUID(mapped)
        await _set_tenant(conn, tid)
        if await conn.fetchval("SELECT 1 FROM tenants WHERE id=$1", tid) is None:
            raise SystemExit(f"mapped tenant {tid} for {brand['name']!r} does not exist "
                             "(or is not visible under RLS)")
        report.notes.append(f"{brand['name']!r} -> existing tenant {tid} (ENRICHED, additive only)")
        return tid

    tid = uuid5(NAMESPACE_URL, f"james-os:bm2-migration:{brand['name']}")
    await _set_tenant(conn, tid)
    if await conn.fetchval("SELECT 1 FROM tenants WHERE id=$1", tid):
        report.notes.append(f"{brand['name']!r} -> existing tenant {tid} (deterministic id, re-run)")
        return tid
    # legacy fallback: a rehearsal cluster whose role CAN enumerate may hold a
    # name-matched tenant from an earlier run; a no-op under production RLS
    legacy = await conn.fetchval("SELECT id FROM tenants WHERE name=$1", brand["name"])
    if legacy:
        await _set_tenant(conn, legacy)
        report.notes.append(f"{brand['name']!r} -> existing tenant {legacy} (matched by name)")
        return legacy
    await conn.execute(
        """INSERT INTO tenants (id, name, config) VALUES ($1, $2,
             jsonb_build_object('manager_v2', true, 'brand_name', $2::text,
                                'entity_type', $3::text))""",
        tid, brand["name"], brand["entity_type"],
    )
    report.notes.append(f"{brand['name']!r} -> NEW tenant {tid}")
    return tid


async def _set_tenant(conn: asyncpg.Connection, tid: UUID) -> None:
    await conn.execute("SELECT set_config('app.current_tenant', $1, false)", str(tid))


async def mig_profile_fields(conn, db, brand, report) -> None:
    rows = db.execute(
        "SELECT * FROM profile_fields WHERE brand_id=? ORDER BY created_at, version",
        (brand["id"],),
    ).fetchall()
    have = {
        (r["field_key"], r["item_key"], r["version"])
        for r in await conn.fetch("SELECT field_key, item_key, version FROM profile_fields WHERE tenant_id = current_setting('app.current_tenant', true)::uuid")
    }
    todo = [r for r in rows if (r["field_key"], r["item_key"], r["version"]) not in have]
    # deterministic per-row ids: supersession chains resolve client-side, so
    # the whole table lands in ONE batched executemany (WAN-friendly)
    idmap = {r["id"]: uuid5(NAMESPACE_URL, f"bm2-mig-pf:{r['id']}") for r in todo}
    await conn.executemany(
        """INSERT INTO profile_fields
             (id, section, field_key, item_key, value, source, confidence, citations,
              status, version, superseded_by, updated_by, created_at)
           VALUES ($1,$2,$3,$4,$5::jsonb,$6,$7,$8::jsonb,$9,$10,$11,$12,$13)""",
        [(idmap[r["id"]], r["section"], r["field_key"], r["item_key"],
          json.dumps(_j(r["value"], {})), r["source"], r["confidence"],
          json.dumps(_j(r["citations"], [])), r["status"], r["version"],
          idmap.get(r["superseded_by"]) if r["superseded_by"] else None,
          r["updated_by"] or "bm2.0-migration", _dt(r["created_at"])) for r in todo],
    )
    report.add(brand["name"], "profile_fields", len(rows), len(todo))


async def mig_memory(conn, db, brand, report, embedder) -> None:
    rows = db.execute("SELECT * FROM memory_chunks WHERE brand_id=?", (brand["id"],)).fetchall()
    have = {
        r["k"] for r in await conn.fetch(
            "SELECT source->>'dedupe_key' AS k FROM events "
            "WHERE source->>'dedupe_key' LIKE 'bm2-mig-%' "
            "AND tenant_id = current_setting('app.current_tenant', true)::uuid"
        )
    }
    todo = []
    for r in rows:
        dedupe = f"bm2-mig-{r['id']}"
        if dedupe in have:
            continue
        meta = _j(r["meta"], {})
        if r["kind"] == "exemplar":
            category, etype = "voice_corpus", "note"
            origin = _ORIGIN_MAP.get(str(meta.get("origin", "")), "harvested")
        elif r["kind"] == "post":
            category, etype, origin = "post", "document", None
        else:
            category, etype, origin = "insight", "note", None
        payload = {"text": r["text"], "category": category, **meta,
                   "channel": r["channel"], "migrated_from": "bm2.0"}
        if origin:
            payload["origin"] = origin
        todo.append((r, etype, payload, dedupe))
    # Voyage caps ~128 inputs per request; their embedder sends one request
    # per call, so batch here (96 = comfortable headroom; stub unaffected)
    vecs: list[list[float]] = []
    for i in range(0, len(todo), 96):
        vecs += await embedder.embed([t[0]["text"] for t in todo[i:i + 96]])
    await conn.executemany(
        """INSERT INTO events (event_type, payload, raw_content, embedding,
                               embedding_model, source, entities, created_at)
           VALUES ($1,$2::jsonb,$3,$4,$5,$6::jsonb,$7,$8)""",
        [(etype, json.dumps(payload), r["text"], str(vec), embedder.model_name,
          json.dumps({"adapter": "bm2_migration", "dedupe_key": dedupe,
                      "uri": r["source_ref"] or None}),
          [f"category:{payload['category']}"], _dt(r["created_at"]))
         for (r, etype, payload, dedupe), vec in zip(todo, vecs)],
    )
    report.add(brand["name"], "memory->events", len(rows), len(todo))


async def mig_peers(conn, db, brand, report) -> None:
    rows = db.execute("SELECT * FROM peer_entities WHERE brand_id=?", (brand["id"],)).fetchall()
    # FOR UPDATE: the watchlist merge is a read-modify-write on a PRE-EXISTING
    # tenants row — lock it so a concurrent write by the live app during the
    # production run can't be silently overwritten (lost update).
    cfg = _j(await conn.fetchval(
        "SELECT config FROM tenants WHERE id=current_setting('app.current_tenant', true)::uuid "
        "FOR UPDATE"
    ), {})
    watchlist = cfg.get("watchlist", [])
    # dedupe on BOTH keys (handle and display name), like manager/peers.py —
    # a name-only peer must not dodge the check and re-add on every run
    have = set()
    for w in watchlist:
        for k in (str(w.get("handle", "")), str(w.get("display_name", ""))):
            k = k.lstrip("@").strip().lower()
            if k:
                have.add(k)
    added = 0
    for r in rows:
        raw_handle = (r["handle"] or "").lstrip("@").strip()
        name = (r["display_name"] or "").strip()
        keys = {k.lower() for k in (raw_handle, name) if k}
        if not keys or keys & have:
            continue
        # name-only peers get the peers.py slug convention as their handle
        handle = raw_handle or "".join(ch if ch.isalnum() else "-" for ch in name.lower()).strip("-")
        watchlist.append({
            "handle": handle, "platform": r["platform"] or "unknown",
            "display_name": name, "status": r["status"] or "tracked",
            "kind": r["kind"], "reason": r["discovery_reason"] or "migrated from bm2.0",
        })
        have |= keys | {handle.lower()}
        added += 1
    if added:
        await conn.execute(
            "UPDATE tenants SET config = jsonb_set(coalesce(config,'{}'::jsonb), "
            "'{watchlist}', $1::jsonb) WHERE id=current_setting('app.current_tenant', true)::uuid",
            json.dumps(watchlist),
        )
    report.add(brand["name"], "peers->watchlist", len(rows), added)

    snaps = db.execute(
        "SELECT s.* FROM peer_snapshots s JOIN peer_entities p ON p.id=s.peer_id "
        "WHERE p.brand_id=?", (brand["id"],),
    ).fetchall() if _has_col(db, "peer_snapshots", "peer_id") else []
    added_s = 0
    for s in snaps:
        peer = db.execute("SELECT handle FROM peer_entities WHERE id=?", (s["peer_id"],)).fetchone()
        handle = (peer["handle"] if peer else "").lstrip("@")
        cap = _dt(s["captured_at"] if "captured_at" in s.keys() else None)
        if await conn.fetchval(
            "SELECT 1 FROM peer_snapshots WHERE peer=$1 AND captured_at=$2 AND tenant_id = current_setting('app.current_tenant', true)::uuid LIMIT 1", handle, cap
        ):
            continue
        stats = _j(s["metrics"] if "metrics" in s.keys() else None, {})
        await conn.execute(
            "INSERT INTO peer_snapshots (peer, platform, stats, sources, captured_at) "
            "VALUES ($1,$2,$3::jsonb,'[]'::jsonb,$4)",
            handle, stats.get("platform", ""), json.dumps(stats), cap,
        )
        added_s += 1
    report.add(brand["name"], "peer_snapshots", len(snaps), added_s)


def _has_col(db, table: str, col: str) -> bool:
    return any(r["name"] == col for r in db.execute(f"PRAGMA table_info({table})").fetchall())


async def mig_action_items(conn, db, brand, report) -> None:
    rows = db.execute("SELECT * FROM action_items WHERE brand_id=?", (brand["id"],)).fetchall()
    have = {
        r["dedupe_key"] for r in await conn.fetch(
            "SELECT dedupe_key FROM action_items "
            "WHERE tenant_id = current_setting('app.current_tenant', true)::uuid"
        )
    }
    batch = []
    for r in rows:
        dedupe = r["dedupe_key"] or f"bm2-mig:{r['id']}"
        if dedupe in have:
            continue
        peer = None
        if r["related_peer_id"]:
            p = db.execute("SELECT handle FROM peer_entities WHERE id=?", (r["related_peer_id"],)).fetchone()
            peer = p["handle"] if p else None
        batch.append((r["kind"], r["title"], r["detail"], r["status"], peer,
                      json.dumps({**_j(r["meta"], {}), "migrated_from": "bm2.0"}),
                      json.dumps(_j(r["updates"], [])), dedupe,
                      _dt(r["snooze_until"]), _dt(r["last_activity_at"]), _dt(r["created_at"])))
    await conn.executemany(
        """INSERT INTO action_items (kind, title, detail, status, related_peer, meta,
             updates, dedupe_key, snooze_until, last_activity_at, created_at)
           VALUES ($1,$2,$3,$4,$5,$6::jsonb,$7::jsonb,$8,$9,$10,$11)""",
        batch,
    )
    added = len(batch)
    report.add(brand["name"], "action_items", len(rows), added)


async def mig_work_orders(conn, db, brand, report) -> dict[str, str]:
    rows = db.execute("SELECT * FROM work_orders WHERE brand_id=?", (brand["id"],)).fetchall()
    prior = {
        r["mf"]: str(r["id"]) for r in await conn.fetch(
            "SELECT payload->>'migrated_from' AS mf, id FROM actions "
            "WHERE action_type='work_order' AND payload->>'migrated_from' IS NOT NULL "
            "AND tenant_id = current_setting('app.current_tenant', true)::uuid"
        )
    }
    idmap: dict[str, str] = {}
    added = 0
    for r in rows:
        if existing := prior.get(r["id"]):
            idmap[r["id"]] = existing
            continue
        artifacts = [
            {"version": a["version"], "kind": a["kind"], "content": a["content"],
             "media": _j(a["media"], {}), "review": _j(a["review"], {})}
            for a in db.execute(
                "SELECT * FROM artifacts WHERE work_order_id=? ORDER BY version", (r["id"],)
            ).fetchall()
        ]
        approvals = [
            {"action": e["decision"] if "decision" in e.keys() else "approve",
             "reason": (e["reason"] if "reason" in e.keys() else "") or "",
             "actor": (e["actor"] if "actor" in e.keys() else "") or "human",
             "at": str(e["created_at"] if "created_at" in e.keys() else "")}
            for e in db.execute(
                "SELECT * FROM approval_events WHERE artifact_id IN "
                "(SELECT id FROM artifacts WHERE work_order_id=?)", (r["id"],),
            ).fetchall()
        ]
        payload = {
            "migrated_from": r["id"], "source": r["source"],
            "source_action_id": r["source_action_id"], "plan_id": r["plan_id"],
            "topic": r["topic"], "platform": r["platform"], "content_type": r["content_type"],
            "format_spec": _j(r["format_spec"], {}), "rationale": r["rationale"],
            "evidence": _j(r["evidence"], []), "predicted_metrics": _j(r["predicted_metrics"], {}),
            "published_ref": _j(r["published_ref"], {}), "actual_metrics": _j(r["actual_metrics"], {}),
            "measured_at": str(r["measured_at"] or "") or None,
            "due_at": str(r["due_at"] or "") or None,
            "artifacts": artifacts, "approvals": approvals,
        }
        new_id = await conn.fetchval(
            "INSERT INTO actions (proposed_by, action_type, payload, status, created_at) "
            "VALUES ('bm2.0-migration', 'work_order', $1::jsonb, $2, $3) RETURNING id",
            json.dumps(payload), r["status"], _dt(r["created_at"]),
        )
        idmap[r["id"]] = str(new_id)
        added += 1
    report.add(brand["name"], "work_orders", len(rows), added)
    return idmap


async def mig_plans(conn, db, brand, report) -> None:
    rows = db.execute("SELECT * FROM plans WHERE brand_id=?", (brand["id"],)).fetchall()
    added = 0
    for r in rows:
        week_of = _dt(r["period_start"]).date()
        if await conn.fetchval(
            "SELECT 1 FROM prescriptions WHERE week_of=$1 AND plan @> $2::jsonb AND tenant_id = current_setting('app.current_tenant', true)::uuid LIMIT 1",
            week_of, json.dumps([{"migrated_from": r["id"]}]),
        ):
            continue
        items = [
            {"migrated_from": r["id"], "platform": w["platform"], "format": w["content_type"],
             "per_week": 1, "topics": [w["topic"]], "why": w["rationale"],
             "evidence": _j(w["evidence"], []), "topic": w["topic"],
             "content_type": w["content_type"], "predicted_metrics": _j(w["predicted_metrics"], {})}
            for w in db.execute("SELECT * FROM work_orders WHERE plan_id=?", (r["id"],)).fetchall()
        ] or [{"migrated_from": r["id"], "platform": "", "format": "", "per_week": 0,
               "topics": [], "why": r["rationale"], "evidence": []}]
        await conn.execute(
            "INSERT INTO prescriptions (week_of, status, plan, created_at) "
            "VALUES ($1,$2,$3::jsonb,$4)",
            week_of, _PLAN_STATUS.get(r["status"], "expired"), json.dumps(items),
            _dt(r["created_at"]),
        )
        added += 1
    report.add(brand["name"], "plans->prescriptions", len(rows), added)


async def mig_questions(conn, db, brand, report) -> None:
    rows = db.execute(
        "SELECT qi.*, qt.text AS q_text, qt.field_key AS q_field, qt.section AS q_section "
        "FROM question_instances qi JOIN question_templates qt ON qt.id = qi.template_id "
        "WHERE qi.brand_id=?", (brand["id"],),
    ).fetchall()
    have = {
        r["question"] for r in await conn.fetch(
            "SELECT question FROM brand_questions "
            "WHERE tenant_id = current_setting('app.current_tenant', true)::uuid"
        )
    }
    added = 0
    batch = []
    for r in rows:
        if r["q_text"] in have:
            continue
        ans = _j(r["answer"], None)
        # donor answers are JSON: a list of items, a {"raw": text} wrapper, or
        # a bare string — unwrap to the human text, never a Python repr
        if isinstance(ans, list):
            ans_text = ", ".join(str(a) for a in ans)
        elif isinstance(ans, dict):
            ans_text = str(ans.get("raw") or ans.get("text") or json.dumps(ans))
        else:
            ans_text = str(ans) if ans not in (None, "") else None
        batch.append(("identity", r["q_text"], ans_text,
                      "user" if r["answered_by"] == "user" else "research",
                      _QI_STATE.get(r["state"], "open"), r["q_field"] or "",
                      _dt(r["answered_at"])))
        added += 1
    await conn.executemany(
        """INSERT INTO brand_questions (dimension, question, answer, source, status,
             field_key, answered_at)
           VALUES ($1,$2,$3,$4,$5,$6,$7)""",
        batch,
    )
    report.add(brand["name"], "questions", len(rows), added)


async def mig_accounts(conn, db, brand, report) -> None:
    rows = db.execute("SELECT * FROM connected_accounts WHERE brand_id=?", (brand["id"],)).fetchall()
    added = 0
    key = ""
    for r in rows:
        key = key or (r["aggregator_profile_key"] or "")
        if await conn.fetchval(
            "SELECT 1 FROM connections WHERE platform=$1 AND tenant_id = current_setting('app.current_tenant', true)::uuid LIMIT 1", r["platform"]
        ):
            continue
        await conn.execute(
            """INSERT INTO connections (platform, status, config)
               VALUES ($1, $2, $3::jsonb)""",
            r["platform"], "connected" if r["auth_status"] == "ok" else r["auth_status"],
            json.dumps({"handle": r["handle"], "baseline": _j(r["baseline"], {}),
                        "migrated_from": "bm2.0"}),
        )
        added += 1
    if key:
        await conn.execute(
            "UPDATE tenants SET config = jsonb_set(coalesce(config,'{}'::jsonb), "
            "'{postproxy_profile_key}', to_jsonb($1::text), true) "
            "WHERE id=current_setting('app.current_tenant', true)::uuid "
            "AND coalesce(config->>'postproxy_profile_key','') = ''",
            key,
        )
    report.add(brand["name"], "connections", len(rows), added)


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sqlite", required=True)
    ap.add_argument("--map", action="append", default=[],
                    help='"Brand Name=tenant-uuid" (repeatable)')
    ap.add_argument("--brands", default="", help="comma-separated subset of brand names")
    ap.add_argument("--execute", action="store_true", help="commit (default: dry-run rollback)")
    ap.add_argument("--database-url", default="",
                    help="explicit target DB (beats .env — config.py's load_dotenv(override=True) "
                         "silently clobbers a DATABASE_URL shell var; REQUIRED practice for the "
                         "production run)")
    args = ap.parse_args()

    mapping = dict(m.split("=", 1) for m in args.map)
    only = {b.strip() for b in args.brands.split(",") if b.strip()}

    db = sqlite3.connect(args.sqlite)
    db.row_factory = sqlite3.Row
    brands = [dict(b) for b in db.execute("SELECT * FROM brands").fetchall()]
    if only:
        brands = [b for b in brands if b["name"] in only]

    db_url = args.database_url or settings.database_url
    host = db_url.split("@")[-1].split("/")[0]
    print(f"[target database: {host} · {'EXECUTE' if args.execute else 'dry run'}]")

    embedder = make_embedder()
    report = Report()
    conn = await asyncpg.connect(
        db_url,
        ssl="require" if "supabase" in db_url else (
            None if settings.db_ssl == "disable" else settings.db_ssl),
        statement_cache_size=0,
    )
    tx = conn.transaction()
    await tx.start()
    try:
        for brand in brands:
            tid = await _tenant_for(conn, brand, mapping, report)
            await _set_tenant(conn, tid)
            await mig_profile_fields(conn, db, brand, report)
            await mig_memory(conn, db, brand, report, embedder)
            await mig_peers(conn, db, brand, report)
            await mig_action_items(conn, db, brand, report)
            await mig_work_orders(conn, db, brand, report)
            await mig_plans(conn, db, brand, report)
            await mig_questions(conn, db, brand, report)
            await mig_accounts(conn, db, brand, report)
        if args.execute:
            await tx.commit()
        else:
            await tx.rollback()
    except Exception:
        await tx.rollback()
        raise
    finally:
        await conn.close()
    report.notes.append("skipped by design: agent_runs (bookkeeping), daily_digests "
                        "(regenerated), question_templates (now code)")
    report.print(args.execute)


if __name__ == "__main__":
    asyncio.run(main())
