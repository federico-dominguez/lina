#!/usr/bin/env python3
import asyncio, asyncpg

async def check():
    pool = await asyncpg.create_pool("postgresql://lina:lina_dev@localhost:5432/lina", min_size=1, max_size=1)
    async with pool.acquire() as conn:
        # Check existing status values
        rows = await conn.fetch("SELECT DISTINCT status FROM comm_messages ORDER BY status")
        print("Status values:", [r['status'] for r in rows])
        
        # Check table columns
        cols = await conn.fetch("""
            SELECT column_name, data_type, is_nullable 
            FROM information_schema.columns 
            WHERE table_name = 'comm_messages' 
            ORDER BY ordinal_position
        """)
        for c in cols:
            print(f"  {c['column_name']}: {c['data_type']} nullable={c['is_nullable']}")
        
        # Check index
        indexes = await conn.fetch("""
            SELECT indexname, indexdef 
            FROM pg_indexes 
            WHERE tablename = 'comm_messages'
        """)
        for idx in indexes:
            print(f"  Index: {idx['indexname']} → {idx['indexdef']}")
        
    await pool.close()
    return "ok"

asyncio.run(check())
