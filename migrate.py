"""Apply SQL migrations to whatever DATABASE_URL points at.

Local Docker auto-runs migrations/ on first boot. Supabase (and any other
managed Postgres) does not — run this script against it instead:

    .venv/bin/python migrate.py

It applies every migrations/*.sql in filename order. Each file should be
idempotent (CREATE TABLE IF NOT EXISTS, DROP POLICY IF EXISTS, etc.) so
re-running is safe.
"""

import asyncio
import sys
from pathlib import Path

import asyncpg

from james_os.config import settings

MIGRATIONS = Path(__file__).parent / "migrations"


async def main() -> None:
    files = sorted(MIGRATIONS.glob("*.sql"))
    if not files:
        print("No migration files found.")
        return

    ssl = None if settings.db_ssl == "disable" else settings.db_ssl
    print(f"Connecting to {_redact(settings.database_url)} (ssl={ssl}) ...")
    conn = await asyncpg.connect(settings.database_url, ssl=ssl)
    try:
        for f in files:
            print(f"Applying {f.name} ...", end=" ", flush=True)
            sql = f.read_text()
            await conn.execute(sql)
            print("ok")
    finally:
        await conn.close()
    print("All migrations applied.")


def _redact(url: str) -> str:
    # hide the password in the printed connection string
    if "@" in url and "://" in url:
        scheme, rest = url.split("://", 1)
        creds, host = rest.split("@", 1)
        user = creds.split(":", 1)[0]
        return f"{scheme}://{user}:***@{host}"
    return url


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as e:  # noqa: BLE001
        print(f"\nMigration failed: {type(e).__name__}: {e}", file=sys.stderr)
        sys.exit(1)
