#!/usr/bin/env python3
import asyncio, asyncpg, os
async def check():
    pool = await asyncpg.create_pool(os.environ.get("LINA_DB_URL", "postgresql://lina:lina_dev@localhost:5432/lina"), min_size=1, max_size=1)
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT id, sender, destination, status, length(message) as len FROM comm_messages WHERE id >= 108 ORDER BY id DESC LIMIT 5")
        for r in rows:
            print(f"#{r['id']}: {r['sender']}->{r['destination']} [{r['status']}] {r['len']} chars")
    await pool.close()
asyncio.run(check())
