#!/usr/bin/env python3
import asyncio, asyncpg, os
async def check():
    pool = await asyncpg.create_pool(os.environ["LINA_DB_URL"], min_size=1, max_size=1)
    async with pool.acquire() as conn:
        # Get check constraint
        rows = await conn.fetch("""
            SELECT conname, pg_get_constraintdef(oid) 
            FROM pg_constraint 
            WHERE conrelid = 'comm_messages'::regclass 
              AND contype = 'c'
        """)
        for r in rows:
            print(f"Constraint: {r['conname']} = {r['pg_get_constraintdef']}")
        
        # Get distinct status values
        vals = await conn.fetch("SELECT DISTINCT status FROM comm_messages")
        print(f"Existing statuses: {[r['status'] for r in vals]}")
        
        # Test insert with monitor status
        try:
            await conn.execute(
                "INSERT INTO comm_messages (sender, destination, message, status) VALUES ($1, $2, $3, 'sent')",
                "test-monitor", "goose", "test summary",
            )
            print("✅ INSERT test OK (will delete)")
            # Clean up
            await conn.execute("DELETE FROM comm_messages WHERE sender = 'test-monitor'")
        except Exception as e:
            print(f"❌ INSERT failed: {e}")
    await pool.close()
asyncio.run(check())
