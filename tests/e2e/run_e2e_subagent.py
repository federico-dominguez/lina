#!/usr/bin/env python3
"""
Full e2e test: Federico → LINA → spawn sub-agent → sub-agent works → notification back.

Usage:
    python3 tests/e2e/run_e2e_subagent.py

The script:
1. Sends a Telegram message to LINA as Federico (via Telethon user client)
2. Waits for LINA to respond and a sub-agent to appear in lina-db
3. Optionally sends /instruct mid-run
4. Checks /agents command
5. Waits for AgentNotifier to send completion notification
6. Prints a summary
"""

from __future__ import annotations

import asyncio
import os
import sys
import time
import psycopg2
from pathlib import Path

from telethon import TelegramClient, events

# ── Credentials ──────────────────────────────────────────────────────────────
SECRETS_DIR = Path("/home/fede/lina/deploy/docker/secrets")
API_ID = 35434942
API_HASH = (SECRETS_DIR / "telegram__api_hash").read_text().strip()
BOT_USERNAME = (SECRETS_DIR / "telegram__bot_username").read_text().strip()
SESSION_FILE = str(Path(__file__).parent / "telegram/.sessions/lina_e2e")
DB_URL = "postgresql://lina:lina_dev@127.0.0.1:5432/lina"

# ── Timeouts ──────────────────────────────────────────────────────────────────
LINA_REPLY_TIMEOUT = 180       # seconds to wait for LINA's first reply
AGENT_SPAWN_TIMEOUT = 90       # seconds to wait for a new agent_session row
AGENT_COMPLETE_TIMEOUT = 600   # seconds to wait for sub-agent completion
STABLE_WINDOW = 5.0            # inactivity to consider reply stream done


# ── DB helpers ────────────────────────────────────────────────────────────────
def db_query(sql: str, params=()) -> list[dict]:
    with psycopg2.connect(DB_URL) as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, row)) for row in cur.fetchall()]


def wait_for_new_agent(since_ts: float, timeout: int) -> dict | None:
    """Poll agent_sessions for a new row created after since_ts."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        rows = db_query(
            "SELECT id, role, goal, status, created_at FROM agent_sessions "
            "WHERE extract(epoch FROM created_at) > %s ORDER BY created_at DESC LIMIT 1",
            (since_ts,),
        )
        if rows:
            return rows[0]
        time.sleep(3)
    return None


def wait_for_agent_done(agent_id: str, timeout: int) -> dict | None:
    """Poll until agent_sessions.status is not running/pending."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        rows = db_query(
            "SELECT id, role, goal, status, result_summary, ended_at FROM agent_sessions WHERE id = %s",
            (agent_id,),
        )
        if rows and rows[0]["status"] not in ("running", "pending"):
            return rows[0]
        time.sleep(5)
    return None


# ── Main ──────────────────────────────────────────────────────────────────────
async def main() -> None:
    print(f"[e2e] Connecting to Telegram as Federico (session: {SESSION_FILE})")
    client = TelegramClient(SESSION_FILE, API_ID, API_HASH)
    await client.connect()
    if not await client.is_user_authorized():
        print("[e2e] ERROR: Telethon session is not authorized!")
        sys.exit(1)

    me = await client.get_me()
    print(f"[e2e] Connected as {me.first_name} (id={me.id})")

    # Collect all messages from the bot during the test
    received: list[str] = []
    spawn_ts = time.time()

    @client.on(events.NewMessage(from_users=BOT_USERNAME))
    async def on_bot_message(event: events.NewMessage.Event) -> None:
        text = event.message.message or ""
        received.append(text)
        short = text[:120].replace("\n", " ")
        print(f"  [BOT→] {short}")

    @client.on(events.MessageEdited(from_users=BOT_USERNAME))
    async def on_bot_edit(event: events.MessageEdited.Event) -> None:
        text = event.message.message or ""
        short = text[:120].replace("\n", " ")
        print(f"  [BOT↻] {short}")

    # ── Step 1: Send the test message ──────────────────────────────────────
    import datetime
    ts_label = datetime.datetime.now().strftime("%m%d-%H%M")
    test_msg = (
        f"Creá un issue en el repo lina con título "
        f"'[E2E Test {ts_label}] Sub-agente completando tarea automatizada' "
        "y luego encargáselo a un sub-agente dev (usando lina-orchestrator__spawn_agent) "
        "para que agregue un comentario diciendo 'completado por sub-agente' y cierre el issue."
    )
    print(f"\n[e2e] Step 1: Sending message to {BOT_USERNAME}")
    print(f"  → {test_msg}")
    await client.send_message(BOT_USERNAME, test_msg)
    t0 = time.time()

    # ── Step 2: Wait for LINA's first reply ──────────────────────────────
    print(f"\n[e2e] Step 2: Waiting for LINA reply (timeout={LINA_REPLY_TIMEOUT}s)")
    deadline = t0 + LINA_REPLY_TIMEOUT
    while time.time() < deadline:
        await asyncio.sleep(2)
        if received:
            print(f"  LINA replied after {time.time()-t0:.1f}s")
            break
    else:
        print(f"[e2e] TIMEOUT: no reply from LINA after {LINA_REPLY_TIMEOUT}s")
        sys.exit(2)

    # Wait for LINA to finish its stream (stable window)
    print(f"  Waiting {STABLE_WINDOW}s stable window for LINA to finish...")
    last_count = len(received)
    last_change = time.time()
    while time.time() - last_change < STABLE_WINDOW:
        await asyncio.sleep(1)
        if len(received) != last_count:
            last_count = len(received)
            last_change = time.time()

    # ── Step 3: Check for sub-agent in DB ────────────────────────────────
    print(f"\n[e2e] Step 3: Waiting for sub-agent row in DB (timeout={AGENT_SPAWN_TIMEOUT}s)")
    agent = wait_for_new_agent(spawn_ts, AGENT_SPAWN_TIMEOUT)
    if agent:
        agent_id = agent["id"]
        print(f"  Found agent: id={agent_id[:8]}… role={agent['role']} status={agent['status']}")
        print(f"  Goal: {agent['goal'][:80]}")
    else:
        print("[e2e] WARNING: No sub-agent spawned yet — LINA may not have called spawn_agent")
        print("  Continuing to watch for notifications anyway...")
        agent_id = None

    # ── Step 4: Send /agents command ─────────────────────────────────────
    await asyncio.sleep(3)
    print(f"\n[e2e] Step 4: Sending /agents command")
    await client.send_message(BOT_USERNAME, "/agents")
    await asyncio.sleep(8)
    agents_replies = [r for r in received if "gente" in r.lower() or "running" in r.lower() or "🔄" in r]
    if agents_replies:
        print(f"  /agents response received: {agents_replies[-1][:120]}")
    else:
        print("  (no /agents response captured yet)")

    # ── Step 5: Send /instruct mid-run (if agent found) ──────────────────
    if agent_id:
        await asyncio.sleep(5)
        short_id = agent_id[:8]
        instruct_msg = f"/instruct {short_id} también agregá el label 'automated' al issue"
        print(f"\n[e2e] Step 5: Sending /instruct: {instruct_msg}")
        await client.send_message(BOT_USERNAME, instruct_msg)
        await asyncio.sleep(5)

    # ── Step 6: Wait for completion notification ──────────────────────────
    print(f"\n[e2e] Step 6: Waiting for completion notification (timeout={AGENT_COMPLETE_TIMEOUT}s)")
    if agent_id:
        final = wait_for_agent_done(agent_id, AGENT_COMPLETE_TIMEOUT)
        if final:
            print(f"  Agent finished: status={final['status']} summary={str(final['result_summary'])[:120]}")
        else:
            print(f"  TIMEOUT: agent still running after {AGENT_COMPLETE_TIMEOUT}s")
    else:
        # No agent_id — just wait for a notification message
        notif_deadline = time.time() + 120
        while time.time() < notif_deadline:
            await asyncio.sleep(3)
            notif = [r for r in received if "✅" in r or "❌" in r or "completó" in r.lower()]
            if notif:
                print(f"  Notification received: {notif[-1][:120]}")
                break
        else:
            print("  No completion notification received within 120s")

    # ── Step 7: Summary ───────────────────────────────────────────────────
    print(f"\n[e2e] ─── SUMMARY ───────────────────────────────────────────")
    print(f"  Messages received from LINA: {len(received)}")
    for i, msg in enumerate(received[:5]):
        print(f"  [{i}] {msg[:100]}")
    print(f"[e2e] Test complete in {time.time()-t0:.1f}s")

    await client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
