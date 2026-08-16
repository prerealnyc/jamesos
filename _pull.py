import asyncio
from james_os.db import acquire, init_pool, close_pool
from james_os.pri_plug_ingest import pull_silo_into_memory
from james_os.retrieval import search

async def main():
    await init_pool()
    async with acquire() as c:
        base_events = await c.fetchval("SELECT count(*) FROM events")
    for silo in ("spaceport", "turtleback"):
        r = await pull_silo_into_memory(silo)
        print(f"pull {silo:11}: ingested={r['ingested']} updated={r['updated']} skipped={r['skipped']} failed={r['failed']} withheld={r['withheld'].get('docs_hidden','?')}")
    async with acquire() as c:
        log = await c.fetchval("SELECT count(*) FROM pri_pull_log")
        sp = await c.fetchval("SELECT count(*) FROM document_metadata WHERE silo_id='spaceport'")
        tb = await c.fetchval("SELECT count(*) FROM document_metadata WHERE silo_id='turtleback'")
        delta = await c.fetchval("SELECT count(*) FROM events") - base_events
    print(f"\nVERIFY: pri_pull_log={log} | spaceport docs={sp} | turtleback docs={tb} | new event-chunks=+{delta}")
    hits = await search("Spaceport hotel")
    print(f"retrieval 'Spaceport hotel' -> {len(hits)} hits; top: {hits[0].raw_content[:80]!r}" if hits else "no hits")
    await close_pool()

asyncio.run(main())
