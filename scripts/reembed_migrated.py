"""Re-embed the bm2.0-migrated events with the production embedder.

The migration's embedder selection was silently clobbered by config.py's
load_dotenv(override=True) (find_dotenv walks up from the MODULE path, so a
scratch CWD doesn't dodge it) — the 832 migrated rows landed with stub
vectors. This pass re-embeds ONLY rows whose source dedupe_key is ours
('bm2-mig-%'), instantiating VoyageEmbedder explicitly so no settings/env
machinery can redirect it. Idempotent: rows already stamped with the target
model are skipped.

Usage:
  python scripts/reembed_migrated.py --database-url "$PROD_URL" \
      --voyage-key "$VOYAGE_API_KEY" [--execute]
"""

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import asyncpg  # noqa: E402

from james_os.embedder import VoyageEmbedder  # noqa: E402

MODEL = "voyage-3-large"
BATCH = 96


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--database-url", required=True)
    ap.add_argument("--voyage-key", required=True)
    ap.add_argument("--execute", action="store_true", help="commit (default: dry-run rollback)")
    args = ap.parse_args()

    host = args.database_url.split("@")[-1].split("/")[0]
    print(f"[target database: {host} · {'EXECUTE' if args.execute else 'dry run'}]")

    embedder = VoyageEmbedder(api_key=args.voyage_key, model=MODEL)
    conn = await asyncpg.connect(
        args.database_url,
        ssl="require" if "supabase" in args.database_url else None,
        statement_cache_size=0,
    )
    # events' RLS policy casts app.current_tenant — it must be SET, and the
    # migrated rows span four tenants, so process per tenant.
    from uuid import NAMESPACE_URL, uuid5

    tenants = [
        "00000000-0000-0000-0000-000000000001",  # James (mapped)
        *(str(uuid5(NAMESPACE_URL, f"james-os:bm2-migration:{n}"))
          for n in ("Turtleback Golf Course", "Spaceport America", "biohackyourselFMEDIA")),
    ]
    tx = conn.transaction()
    await tx.start()
    try:
        total = 0
        for tid in tenants:
            await conn.execute("SELECT set_config('app.current_tenant', $1, false)", tid)
            rows = await conn.fetch(
                """SELECT id, raw_content FROM events
                   WHERE source->>'dedupe_key' LIKE 'bm2-mig-%'
                     AND coalesce(embedding_model, '') <> $1
                     AND raw_content IS NOT NULL
                     AND tenant_id = current_setting('app.current_tenant', true)::uuid""",
                MODEL,
            )
            print(f"tenant {tid[:8]}…: {len(rows)} events need re-embedding")
            for i in range(0, len(rows), BATCH):
                chunk = rows[i:i + BATCH]
                vecs = await embedder.embed([r["raw_content"] for r in chunk])
                await conn.executemany(
                    "UPDATE events SET embedding = $2, embedding_model = $3 WHERE id = $1",
                    [(r["id"], str(v), MODEL) for r, v in zip(chunk, vecs)],
                )
                print(f"  {min(i + BATCH, len(rows))}/{len(rows)}")
            total += len(rows)
        print(f"{total} re-embedded total")
        if args.execute:
            await tx.commit()
            print("COMMITTED")
        else:
            await tx.rollback()
            print("rolled back (dry run)")
    except Exception:
        await tx.rollback()
        raise
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
