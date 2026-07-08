"""Brand identity — who this tenant IS, read by every engine.

The `brand_profiles` row is the product of the Intake ("who am I, what are
my goals, what do I talk about") and the seed for:

  * the content voice engine    (brand_profile_block → system prompt)
  * autopilot ideation          (goals + pillars steer topics)
  * Ask                         (identity context)
  * the daily research job      (what to research, how many suggestions)
  * the strategy engine (M2)    (goals are what prescriptions optimize for)

Also home to content_suggestions — the pieces the brand manager proposes on
its own (daily research now; press monitor + prescriptions later). Accepting
a suggestion routes through the same production machinery as everything
else, into the approval queue.
"""

from __future__ import annotations

import json
from uuid import UUID

from .db import acquire

_PROFILE_COLS = ("kind", "identity", "goals", "pillars", "taboos",
                 "platforms", "peers", "constraints", "intake_done")
_JSON_COLS = {"identity", "goals", "pillars", "taboos", "platforms",
              "peers", "constraints"}


def _parse(row: dict) -> dict:
    out = dict(row)
    for k in _JSON_COLS:
        v = out.get(k)
        if isinstance(v, str):
            try:
                out[k] = json.loads(v)
            except ValueError:
                out[k] = {} if k in ("identity", "constraints") else []
    if out.get("updated_at") is not None:
        out["updated_at"] = out["updated_at"].isoformat()
    out["tenant_id"] = str(out["tenant_id"])
    return out


async def get_brand_profile(tenant_id: UUID | None = None) -> dict | None:
    async with acquire(tenant_id) as conn:
        row = await conn.fetchrow("SELECT * FROM brand_profiles")
    return _parse(dict(row)) if row else None


async def upsert_brand_profile(
    fields: dict, tenant_id: UUID | None = None,
) -> dict:
    """Create/update the tenant's profile (partial updates fine). Completing
    the intake auto-enables the daily research job — the brand manager
    starts working the morning after it learns who the brand is."""
    clean: dict = {}
    for k in _PROFILE_COLS:
        if k not in fields:
            continue
        v = fields[k]
        clean[k] = json.dumps(v) if k in _JSON_COLS else v
    async with acquire(tenant_id) as conn:
        if clean:
            sets = ", ".join(f"{k} = ${i + 1}" for i, k in enumerate(clean))
            await conn.execute(
                f"""INSERT INTO brand_profiles (tenant_id)
                    VALUES (current_setting('app.current_tenant', true)::uuid)
                    ON CONFLICT (tenant_id) DO NOTHING""")
            await conn.execute(
                f"UPDATE brand_profiles SET {sets}, updated_at = now()",
                *clean.values(),
            )
        if fields.get("intake_done"):
            tid = await conn.fetchval(
                "SELECT current_setting('app.current_tenant', true)::uuid")
            # The brand manager starts working the moment it knows who the
            # brand is: daily research, the continuous deep interview, and
            # the strategy loop (playbooks weekly, peers weekly, a fresh
            # Prescription every Monday-ish).
            for kind, cadence in (("daily_brand_research", 24),
                                  ("brand_interview", 12),
                                  ("playbook_refresh", 168),
                                  ("peer_snapshot", 168),
                                  ("weekly_prescription", 168)):
                await conn.execute(
                    """INSERT INTO scheduled_jobs (tenant_id, kind, cadence_hours)
                       VALUES ($1, $2, $3)
                       ON CONFLICT (tenant_id, kind)
                       DO UPDATE SET enabled = true""",
                    tid, kind, cadence,
                )
    prof = await get_brand_profile(tenant_id)
    return prof or {}


async def brand_profile_block(tenant_id: UUID | None = None) -> str:
    """Compact <brand_profile> block for system prompts. Empty string when
    no intake has been done (engines behave exactly as before)."""
    p = await get_brand_profile(tenant_id)
    if not p or not (p.get("identity") or p.get("goals") or p.get("pillars")):
        return ""
    ident = p.get("identity") or {}
    lines = [f"kind: {p.get('kind') or 'person'}"]
    for key in ("name", "mission", "positioning", "audience"):
        if ident.get(key):
            lines.append(f"{key}: {str(ident[key])[:300]}")
    if p.get("goals"):
        lines.append("goals: " + "; ".join(str(g)[:120] for g in p["goals"][:6]))
    if p.get("pillars"):
        lines.append("topic pillars: " + "; ".join(str(t)[:80] for t in p["pillars"][:8]))
    if p.get("taboos"):
        lines.append("NEVER touch: " + "; ".join(str(t)[:80] for t in p["taboos"][:8]))
    if p.get("platforms"):
        lines.append("platforms: " + ", ".join(str(t)[:30] for t in p["platforms"][:8]))
    return "<brand_profile>\n" + "\n".join(lines) + "\n</brand_profile>"


# ── content suggestions (what the brand manager proposes on its own) ──

async def list_suggestions(
    status: str = "suggested", tenant_id: UUID | None = None, limit: int = 30,
) -> list[dict]:
    async with acquire(tenant_id) as conn:
        rows = await conn.fetch(
            """SELECT id, source, title, topic, format, why, status,
                      action_ref, created_at
                 FROM content_suggestions
                WHERE ($1 = '' OR status = $1)
                ORDER BY created_at DESC LIMIT $2""",
            status or "", limit,
        )
    return [{
        "id": str(r["id"]), "source": r["source"], "title": r["title"],
        "topic": r["topic"], "format": r["format"], "why": r["why"],
        "status": r["status"],
        "action_ref": str(r["action_ref"]) if r["action_ref"] else None,
        "created_at": r["created_at"].isoformat() if r["created_at"] else None,
    } for r in rows]


async def accept_suggestion(
    suggestion_id: UUID, tenant_id: UUID | None = None,
) -> dict:
    """Turn a suggestion into real queued content through the SAME machinery
    as autopilot (voice engine + QA + photo gates / video templates)."""
    async with acquire(tenant_id) as conn:
        row = await conn.fetchrow(
            "SELECT id, title, topic, format, status FROM content_suggestions "
            "WHERE id=$1", suggestion_id)
    if not row:
        raise ValueError("suggestion not found")
    if row["status"] == "accepted" and row["format"] == "post":
        return {"id": str(suggestion_id), "status": "accepted"}

    idea = {"title": row["title"], "topic": row["topic"], "pillar": ""}
    ref = None
    if row["format"] == "reel":
        from .autopilot_bulk import _make_video
        made = await _make_video(idea, "instagram", tenant_id,
                                 video_template="full")
        ref = made.get("production_id")
    else:
        from .autopilot_bulk import _make_text_post
        made = await _make_text_post(idea, "instagram", tenant_id,
                                     image_kind="james")
        ref = made.get("action_id") if isinstance(made, dict) else None

    async with acquire(tenant_id) as conn:
        await conn.execute(
            "UPDATE content_suggestions SET status='accepted', action_ref=$2 "
            "WHERE id=$1",
            suggestion_id, UUID(str(ref)) if ref else None,
        )
    return {"id": str(suggestion_id), "status": "accepted",
            "ref": str(ref) if ref else None}


async def dismiss_suggestion(
    suggestion_id: UUID, tenant_id: UUID | None = None,
) -> bool:
    async with acquire(tenant_id) as conn:
        tag = await conn.execute(
            "UPDATE content_suggestions SET status='dismissed' WHERE id=$1",
            suggestion_id)
    return tag.endswith("1")


# ── profile projection (bm2.0 export shape, internal) ─────────────────
#
# bm2.0's GET /export/brand-profile was the handoff between the two systems;
# unified, that shape becomes the internal projection over the profile_fields
# envelope. Merge order: the existing brand_profiles row is the BASE, and the
# envelope's flat {field_key: value} snapshot merges OVER it — envelope values
# win on overlapping keys, every brand_profiles key survives, so current
# readers (voice engine, autopilot, Ask, research, strategy) see a superset.


def _values(v: object) -> list:
    """A flat field value may itself be a list; normalize for list merges."""
    if isinstance(v, (list, tuple)):
        return list(v)
    return [] if v is None else [v]


def _merged_list(base: object, extra: list) -> list:
    out = list(base) if isinstance(base, list) else _values(base)
    for v in extra:
        if v not in out:
            out.append(v)
    return out


async def profile_projection(tenant_id: UUID | None = None) -> dict:
    """The flat envelope snapshot merged over brand_profiles — the bm2.0
    'james-os handoff export' shape (identity/positioning/audience/goals/
    pillars/taboos/voice/platforms/peers/constraints/intake_done), computed
    from live DB state on every call.

    - dict sections (identity, constraints): per-subkey merge, envelope wins
    - list sections (goals, pillars, taboos): base order kept, envelope
      values appended (deduped) — guardrails.* feed taboos (donor mapping),
      positioning.pillar_topics feed pillars
    - new sections the base never had (positioning, audience, voice,
      connected_accounts, tracked_peers) ride alongside — pure superset
    - the complete flat projection is under 'fields' so nothing is lost
    """
    from .manager import profile as manager_profile  # lazy: keeps import edges one-way

    async with acquire(tenant_id) as conn:
        fields = await manager_profile.current_fields(conn)
        conn_rows = await conn.fetch(
            "SELECT platform, handle, status FROM connections ORDER BY created_at"
        )
        cfg = await conn.fetchval(
            "SELECT config FROM tenants WHERE id = current_setting('app.current_tenant', true)::uuid"
        )
    if isinstance(cfg, str):
        cfg = json.loads(cfg or "{}")
    cfg = cfg or {}

    flat = manager_profile.projection(fields)
    base = await get_brand_profile(tenant_id) or {}

    # split the flat snapshot: scalar keys vs item-keyed lists ("fk[item]")
    scalars: dict = {}
    lists: dict[str, list] = {}
    for k, v in flat.items():
        if k.endswith("]") and "[" in k:
            lists.setdefault(k.split("[", 1)[0], []).append(v)
        else:
            scalars[k] = v

    def collect(prefix: str) -> dict:
        return {
            k.split(".", 1)[1]: v for k, v in scalars.items() if k.startswith(prefix + ".")
        } | {k.split(".", 1)[1]: v for k, v in lists.items() if k.startswith(prefix + ".")}

    goals_extra = [v for raw in collect("goals").values() for v in _values(raw)]
    pillars_extra = [
        v
        for raw in (lists.get("positioning.pillar_topics") or []) + _values(scalars.get("positioning.pillar_topics"))
        for v in _values(raw)
    ]
    taboos_extra = [v for raw in collect("guardrails").values() for v in _values(raw)]
    tracked_peers = [
        {
            "platform": e.get("platform") or "",
            "handle": e.get("handle") or "",
            "kind": e.get("kind") or "",
            "name": e.get("display_name") or e.get("name") or "",
            "why": e.get("reason") or "",
        }
        for e in (cfg.get("watchlist") or [])
        if str(e.get("status") or "tracked") == "tracked"
    ]

    return {
        "tenant_id": str(base.get("tenant_id") or ""),
        "kind": scalars.get("identity.kind") or base.get("kind"),
        "identity": {**(base.get("identity") or {}), **collect("identity")},
        "positioning": collect("positioning"),
        "audience": collect("audience"),
        "voice": collect("voice"),
        "goals": _merged_list(base.get("goals"), goals_extra),
        "pillars": _merged_list(base.get("pillars"), pillars_extra),
        "taboos": _merged_list(base.get("taboos"), taboos_extra),
        "platforms": base.get("platforms") or [],
        "peers": base.get("peers") or [],
        "constraints": base.get("constraints") or {},
        "intake_done": bool(base.get("intake_done")),
        # superset extensions (never existed on brand_profiles)
        "connected_accounts": [
            {"platform": r["platform"], "handle": r["handle"] or "", "auth_status": r["status"]}
            for r in conn_rows
        ],
        "tracked_peers": tracked_peers,
        "fields": flat,
    }


__all__ = [
    "get_brand_profile", "upsert_brand_profile", "brand_profile_block",
    "list_suggestions", "accept_suggestion", "dismiss_suggestion",
    "profile_projection",
]
