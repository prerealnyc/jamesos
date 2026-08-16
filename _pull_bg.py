import asyncio
from james_os.db import init_pool, close_pool
from james_os.pri_plug_ingest import pull_silo_into_memory

async def main():
    await init_pool()
    for silo in ("spaceport", "turtleback"):
        r = await pull_silo_into_memory(silo)
        print(f"pull {silo}: ingested={r['ingested']} updated={r['updated']} skipped={r['skipped']} failed={r['failed']}", flush=True)
    await close_pool()
    print("PULL COMPLETE", flush=True)

asyncio.run(main())
