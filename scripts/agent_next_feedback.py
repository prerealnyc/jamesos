"""Pick the next actionable queued code-change for the autonomous agent.

Run in CI (GitHub Actions). Reads the feedback_changes board and emits the
oldest, highest-confidence *concrete* code change that isn't already
proposed/done. Vague 'general' items are skipped — too ambiguous to
implement unattended; a human handles those.

Outputs (to keep $GITHUB_OUTPUT parsing bulletproof) are ONLY short, safe
scalars: has_item, change_id, area. The long task text goes to a FILE
(agent_task.md) the workflow reads into the agent's prompt — long/special
strings in $GITHUB_OUTPUT silently break the workflow.
"""

import asyncio
import os
import sys

import asyncpg

TENANT = os.environ.get("JOS_DEFAULT_TENANT_ID", "00000000-0000-0000-0000-000000000001")
# Areas the agent may touch unattended. 'general' is excluded on purpose.
ALLOWED_AREAS = ("captions", "voice", "broll", "video", "assembly", "pacing", "render")
TASK_FILE = "agent_task.md"


def _out(d: dict) -> None:
    path = os.environ.get("GITHUB_OUTPUT")
    if not path:
        print(d)
        return
    with open(path, "a") as fh:
        for k, v in d.items():
            fh.write(f"{k}={v}\n")  # only short scalars reach here


async def main() -> None:
    conn = await asyncpg.connect(os.environ["DATABASE_URL"], statement_cache_size=0)
    try:
        async with conn.transaction():
            await conn.execute("select set_config('app.current_tenant', $1, true)", TENANT)
            row = await conn.fetchrow(
                "SELECT id::text, area, plain_english, diagnosis "
                "FROM feedback_changes "
                "WHERE status = 'queued' AND kind = 'code_change' "
                "  AND area = ANY($1::text[]) "
                "ORDER BY confidence DESC NULLS LAST, created_at ASC LIMIT 1",
                list(ALLOWED_AREAS),
            )
    finally:
        await conn.close()

    if not row:
        _out({"has_item": "false"})
        print("No actionable queued code-change.", file=sys.stderr)
        return

    # Full task context → file (read by the workflow into the prompt).
    with open(TASK_FILE, "w") as fh:
        fh.write(
            f"Feedback area: {row['area']}\n"
            f"What to do: {row['plain_english']}\n"
            f"Detail / diagnosis: {row['diagnosis']}\n"
        )
    _out({"has_item": "true", "change_id": row["id"], "area": row["area"]})
    print(f"Selected change {row['id']} [{row['area']}]", file=sys.stderr)


if __name__ == "__main__":
    asyncio.run(main())
