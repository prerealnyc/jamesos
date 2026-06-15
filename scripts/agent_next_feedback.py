"""Pick the next actionable queued code-change for the autonomous agent.

Run in CI (GitHub Actions). Reads the feedback_changes board and emits the
oldest, highest-confidence *concrete* code change that isn't already
proposed/done. Vague 'general' items are skipped — they're too ambiguous to
implement unattended; a human handles those.

Writes results to $GITHUB_OUTPUT (has_item, change_id, area, title, detail)
when present, else prints JSON to stdout. Exits 0 always; the workflow keys
off has_item.
"""

import asyncio
import json
import os
import sys

import asyncpg

TENANT = os.environ.get("JOS_DEFAULT_TENANT_ID", "00000000-0000-0000-0000-000000000001")
# Areas the agent is allowed to touch unattended. 'general' is excluded on
# purpose (too vague to implement safely).
ALLOWED_AREAS = ("captions", "voice", "broll", "video", "assembly", "pacing", "render")


async def main() -> None:
    dsn = os.environ["DATABASE_URL"]
    conn = await asyncpg.connect(dsn, statement_cache_size=0)
    try:
        async with conn.transaction():
            await conn.execute("select set_config('app.current_tenant', $1, true)", TENANT)
            row = await conn.fetchrow(
                "SELECT id::text, area, plain_english, diagnosis, confidence "
                "FROM feedback_changes "
                "WHERE status = 'queued' AND kind = 'code_change' "
                "  AND area = ANY($1::text[]) "
                "ORDER BY confidence DESC NULLS LAST, created_at ASC LIMIT 1",
                list(ALLOWED_AREAS),
            )
    finally:
        await conn.close()

    out = os.environ.get("GITHUB_OUTPUT")

    def emit(d: dict) -> None:
        if out:
            with open(out, "a") as fh:
                for k, v in d.items():
                    val = str(v).replace("\n", " ")
                    fh.write(f"{k}={val}\n")
        else:
            print(json.dumps(d, indent=2))

    if not row:
        emit({"has_item": "false"})
        print("No actionable queued code-change.", file=sys.stderr)
        return

    emit({
        "has_item": "true",
        "change_id": row["id"],
        "area": row["area"],
        "title": (row["plain_english"] or "")[:160],
        "detail": (row["diagnosis"] or "")[:400],
    })
    print(f"Selected change {row['id']} [{row['area']}]: {row['plain_english'][:80]}", file=sys.stderr)


if __name__ == "__main__":
    asyncio.run(main())
