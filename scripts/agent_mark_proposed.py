"""Mark a feedback_changes row as 'proposed' with its PR link.

Called by CI after the agent opens a PR for a change, so the item leaves
the Queued board (won't be re-attempted) and the What's Next page shows
'PR proposed → review'. A human merging the PR is what flips it to 'done'.

Usage: python scripts/agent_mark_proposed.py <change_id> <pr_url>
"""

import asyncio
import os
import sys

import asyncpg

TENANT = os.environ.get("JOS_DEFAULT_TENANT_ID", "00000000-0000-0000-0000-000000000001")


async def main() -> None:
    change_id, pr_url = sys.argv[1], sys.argv[2]
    conn = await asyncpg.connect(os.environ["DATABASE_URL"], statement_cache_size=0)
    try:
        async with conn.transaction():
            await conn.execute("select set_config('app.current_tenant', $1, true)", TENANT)
            tag = await conn.execute(
                "UPDATE feedback_changes SET status='proposed', pr_url=$2, updated_at=now() "
                "WHERE id=$1::uuid AND status='queued'",
                change_id, pr_url,
            )
        print(f"marked {change_id} proposed → {pr_url} ({tag})")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
