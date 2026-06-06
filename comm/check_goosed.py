#!/usr/bin/env python3
import asyncio, httpx

async def check():
    bots = [
        ("LINA", "https://localhost:3000", "4bea469dd75fb2de8cf3f797c832813575bc5408fab3ae3a2b6734037971e564"),
        ("Cline", "https://localhost:3001", "4bea469dd75fb2de8cf3f797c832813575bc5408fab3ae3a2b6734037971e564"),
        ("Gemma", "https://localhost:3002", "cf5787c8fe1f97d14c2bec888013d2f46f22b0004ccfc50c2fcd224b2723e910"),
    ]
    for name, url, secret in bots:
        headers = {"x-secret-key": secret} if secret else {}
        try:
            async with httpx.AsyncClient(timeout=5.0, verify=False) as c:
                r = await c.get(f"{url}/status", headers=headers)
            print(f"{name} ({url}): status={r.status_code} {r.text[:100]}")
        except Exception as e:
            print(f"{name} ({url}): ❌ {e}")

    # Also check sessions
    for name, url, secret in bots:
        headers = {"x-secret-key": secret} if secret else {}
        try:
            async with httpx.AsyncClient(timeout=5.0, verify=False) as c:
                r = await c.get(f"{url}/sessions", headers=headers)
            print(f"{name} sessions: status={r.status_code} {r.text[:200]}")
        except Exception as e:
            print(f"{name} sessions: ❌ {e}")

asyncio.run(check())
