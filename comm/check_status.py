import asyncio, asyncpg, os
async def c():
    pool = await asyncpg.create_pool(os.environ["LINA_DB_URL"], min_size=1)
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT id, sender, destination, status, length(message) as len FROM comm_messages ORDER BY id DESC LIMIT 5")
        for r in rows:
            print(f"#{r['id']}: {r['sender']}->{r['destination']} [{r['status']}] {r['len']} chars")
    await pool.close()
asyncio.run(c())