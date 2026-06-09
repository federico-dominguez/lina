import asyncio, asyncpg, os
async def c():
    pool = await asyncpg.create_pool(os.environ["LINA_DB_URL"], min_size=1)
    async with pool.acquire() as conn:
        # Look for cline response after msg 108
        row = await conn.fetchrow("SELECT id, length(message) FROM comm_messages WHERE sender='cline' AND destination='goose' AND id > 108 ORDER BY id DESC LIMIT 1")
        if row: print(f"cline response: #{row['id']} ({row['length']} chars)")
        else: print("no cline response yet")
        # Check if msg 108 was processed (status should be delivered)
        r = await conn.fetchrow("SELECT status FROM comm_messages WHERE id = 108")
        print(f"msg 108 status: {r['status']}")
        # Also check pending
        p = await conn.fetchval("SELECT count(*) FROM comm_messages WHERE status='sent'")
        print(f"pending sent: {p}")
    await pool.close()
asyncio.run(c())