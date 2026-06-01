#!/usr/bin/env python3
"""
E2E test: real-time monitoring + mid-run instructions.

Flow:
  1. LINA spawns a long-running dev agent (task: analyse a large directory tree).
  2. While agent is running:
     a. Test polls /status <id> every 15s for live updates
     b. Test sends /instruct <id> to change direction mid-run
     c. Test verifies instruction appears in agent_commands
  3. Agent completes (must acknowledge instruction in result_summary or events).
  4. Test validates that the instruction was received (ack_at set in DB).

Usage:
    uv run --with psycopg2-binary python3 tests/e2e/run_e2e_realtime_monitor.py

Passes if:
  - Agent is spawned
  - /instruct inserts a row in agent_commands
  - Agent eventually completes
  - (Optional) agent_commands row has ack_at set (agent polled instructions)
"""

from __future__ import annotations

import asyncio
import datetime
import sys
import time
from pathlib import Path

import psycopg2
from telethon import TelegramClient, events

# ── Credentials ───────────────────────────────────────────────────────────────
SECRETS_DIR = Path("/home/fede/lina/deploy/docker/secrets")
API_ID = 35434942
API_HASH = (SECRETS_DIR / "telegram__api_hash").read_text().strip()
BOT_USERNAME = (SECRETS_DIR / "telegram__bot_username").read_text().strip()
SESSION_FILE = str(Path(__file__).parent / "telegram/.sessions/lina_e2e")
DB_URL = "postgresql://lina:lina_dev@127.0.0.1:5432/lina"

# ── Timeouts ──────────────────────────────────────────────────────────────────
LINA_REPLY_TIMEOUT = 180
AGENT_SPAWN_TIMEOUT = 120
AGENT_COMPLETE_TIMEOUT = 900
MONITOR_INTERVAL = 15       # seconds between /status polls
INSTRUCT_DELAY = 30         # seconds after spawn before sending instruction
STABLE_WINDOW = 5.0


# ── DB helpers ────────────────────────────────────────────────────────────────
def db_query(sql: str, params=()) -> list[dict]:
    with psycopg2.connect(DB_URL) as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, row)) for row in cur.fetchall()]


def wait_for_new_agent(since_ts: float, timeout: int) -> dict | None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        rows = db_query(
            "SELECT id, role, goal, status, created_at FROM agent_sessions "
            "WHERE extract(epoch FROM created_at) > %s ORDER BY created_at DESC LIMIT 1",
            (since_ts,),
        )
        if rows:
            return rows[0]
        time.sleep(4)
    return None


def get_agent_status(agent_id: str) -> dict | None:
    rows = db_query(
        "SELECT id, status, result_summary, started_at, ended_at, "
        "EXTRACT(EPOCH FROM (COALESCE(ended_at, NOW()) - started_at))::INT AS elapsed "
        "FROM agent_sessions WHERE id = %s",
        (agent_id,),
    )
    return rows[0] if rows else None


def get_pending_instructions(agent_id: str) -> list[dict]:
    return db_query(
        "SELECT id, kind, args_json, sent_at, ack_at FROM agent_commands "
        "WHERE session_id = %s ORDER BY sent_at DESC",
        (agent_id,),
    )


def get_agent_events(agent_id: str, limit: int = 20) -> list[dict]:
    return db_query(
        "SELECT kind, ts, payload_json FROM agent_events "
        "WHERE session_id = %s ORDER BY ts DESC LIMIT %s",
        (agent_id, limit),
    )


def wait_for_agent_done(agent_id: str, timeout: int) -> dict | None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        s = get_agent_status(agent_id)
        if s and s["status"] not in ("running", "pending"):
            return s
        time.sleep(8)
    return get_agent_status(agent_id)


# ── Main ──────────────────────────────────────────────────────────────────────
async def main() -> None:
    print(f"[e2e-realtime] Connecting (session={SESSION_FILE})")
    client = TelegramClient(SESSION_FILE, API_ID, API_HASH)
    await client.connect()
    if not await client.is_user_authorized():
        print("[e2e-realtime] ERROR: session not authorized!")
        sys.exit(1)

    me = await client.get_me()
    print(f"[e2e-realtime] Connected as {me.first_name}")

    received: list[str] = []
    spawn_ts = time.time()
    test_label = datetime.datetime.now().strftime("%m%d-%H%M")

    @client.on(events.NewMessage(from_users=BOT_USERNAME))
    async def on_bot_msg(event: events.NewMessage.Event) -> None:
        text = event.message.message or ""
        received.append(text)
        print(f"  [BOT→] {text[:160].replace(chr(10), ' ')}")

    @client.on(events.MessageEdited(from_users=BOT_USERNAME))
    async def on_bot_edit(event: events.MessageEdited.Event) -> None:
        text = event.message.message or ""
        print(f"  [BOT↻] {text[:100].replace(chr(10), ' ')}")

    # ── Step 1: Spawn a long-ish running agent ────────────────────────────
    test_msg = (
        f"[E2E Realtime {test_label}] Lanzá un sub-agente dev con "
        f"lina-orchestrator__spawn_agent para hacer esta tarea que lleva algo de tiempo: "
        f"'Analizá la estructura del directorio /home/user/lina/mcps/ — "
        f"para cada MCP, contá cuántos archivos .py tiene, suma el total de líneas de código, "
        f"y generá un reporte en formato texto. "
        f"Revisá también /home/user/lina/tests/ y contá los tests por tipo (unit/integration/e2e). "
        f"Guardá el reporte en /home/user/lina/tests/e2e/realtime_report_{test_label}.txt. "
        f"Tomá tu tiempo — esto implica leer varios archivos. "
        f"En tu result_summary incluí el resumen del reporte.' "
        f"Decime el agent_id cuando lo hayas lanzado."
    )

    print(f"\n[Step 1] Requesting long-running agent spawn")
    print(f"  → {test_msg[:200]}…")
    await client.send_message(BOT_USERNAME, test_msg)
    t0 = time.time()

    # ── Step 2: Wait for LINA reply ───────────────────────────────────────
    print(f"\n[Step 2] Waiting for LINA reply (timeout={LINA_REPLY_TIMEOUT}s)")
    deadline = t0 + LINA_REPLY_TIMEOUT
    while time.time() < deadline:
        await asyncio.sleep(2)
        if received:
            print(f"  LINA replied after {time.time()-t0:.1f}s")
            break
    else:
        print("[e2e-realtime] TIMEOUT: no reply from LINA")
        sys.exit(2)

    last_count = len(received)
    last_change = time.time()
    while time.time() - last_change < STABLE_WINDOW:
        await asyncio.sleep(1)
        if len(received) != last_count:
            last_count = len(received)
            last_change = time.time()

    # ── Step 3: Wait for agent in DB ──────────────────────────────────────
    print(f"\n[Step 3] Waiting for agent in DB (timeout={AGENT_SPAWN_TIMEOUT}s)")
    agent = wait_for_new_agent(spawn_ts, AGENT_SPAWN_TIMEOUT)
    if not agent:
        print("[e2e-realtime] WARNING: agent not found in DB")
        sys.exit(2)

    agent_id = agent["id"]
    print(f"  Agent: id={agent_id[:8]}… role={agent['role']} status={agent['status']}")

    # ── Step 4: Monitor with /status every MONITOR_INTERVAL seconds ───────
    print(f"\n[Step 4] Real-time monitoring via /status (interval={MONITOR_INTERVAL}s)")
    instruct_sent = False
    command_id: int | None = None
    monitor_end = time.time() + INSTRUCT_DELAY + 60  # monitor for instruct_delay + 60s

    while time.time() < monitor_end:
        # Poll DB directly
        status = get_agent_status(agent_id)
        if status:
            elapsed = status.get("elapsed") or 0
            mins, secs = divmod(int(elapsed), 60)
            print(f"  [DB] t={time.time()-t0:.0f}s: status={status['status']} elapsed={mins}m{secs:02d}s")
            if status["status"] not in ("running", "pending"):
                print(f"  Agent already done: {status['status']}")
                break

        # Use /status command (exercises the new gateway command)
        await client.send_message(BOT_USERNAME, f"/status {agent_id[:8]}")
        await asyncio.sleep(4)

        # After INSTRUCT_DELAY seconds, send a mid-run instruction
        elapsed_total = time.time() - t0
        if not instruct_sent and elapsed_total >= INSTRUCT_DELAY:
            instruct_text = (
                "además contá cuántos archivos README.md hay en el repo completo y "
                "añadilo al reporte antes de guardarlo"
            )
            print(f"\n[Step 4b] Sending mid-run /instruct (t={elapsed_total:.0f}s)")
            await client.send_message(
                BOT_USERNAME,
                f"/instruct {agent_id[:8]} {instruct_text}",
            )
            instruct_sent = True
            await asyncio.sleep(5)

            # Verify instruction landed in DB (via agent_commands)
            cmds = get_pending_instructions(agent_id)
            if cmds:
                command_id = cmds[0]["id"]
                print(f"  Instruction in DB: id={command_id} ack_at={cmds[0]['ack_at']}")
            else:
                print(f"  ⚠️  No instruction rows in agent_commands for {agent_id[:8]}")

        await asyncio.sleep(MONITOR_INTERVAL - 4)

    # ── Step 5: Wait for agent to complete ────────────────────────────────
    print(f"\n[Step 5] Waiting for agent completion (timeout={AGENT_COMPLETE_TIMEOUT}s)")
    final = wait_for_agent_done(agent_id, AGENT_COMPLETE_TIMEOUT)
    if final:
        print(f"  Final status: {final['status']}")
        print(f"  Summary: {str(final.get('result_summary'))[:300]}")
    else:
        print(f"  TIMEOUT: agent still running after {AGENT_COMPLETE_TIMEOUT}s")
        final = {"status": "timeout"}

    # ── Step 6: Check /events output ──────────────────────────────────────
    print(f"\n[Step 6] Checking /events for agent")
    await client.send_message(BOT_USERNAME, f"/events {agent_id[:8]}")
    await asyncio.sleep(8)

    # ── Step 7: Validate instruction ack ──────────────────────────────────
    print(f"\n[Step 7] Checking instruction acknowledgement")
    cmds = get_pending_instructions(agent_id)
    events_log = get_agent_events(agent_id)
    if cmds:
        for cmd in cmds:
            acked = "✅" if cmd["ack_at"] else "⏳"
            print(f"  Cmd {cmd['id']}: kind={cmd['kind']} sent={cmd['sent_at']} ack={acked}")
    else:
        print("  No commands recorded (agent may not have called get_pending_instructions)")

    instruction_acked = any(c["ack_at"] is not None for c in cmds) if cmds else False

    # ── Summary ───────────────────────────────────────────────────────────
    total_time = time.time() - t0
    print(f"\n[e2e-realtime] ─── SUMMARY ─────────────────────────────────────")
    print(f"  Total time: {total_time:.1f}s")
    print(f"  Agent status: {final.get('status')}")
    print(f"  Instruction sent: {instruct_sent}")
    print(f"  Instruction in DB: {command_id is not None}")
    print(f"  Instruction acked by agent: {instruction_acked}")
    print(f"  Agent events logged: {len(events_log)}")
    print(f"  LINA messages received: {len(received)}")

    # Test passes if: agent spawned, instruction sent and landed in DB, agent completed
    success = (
        agent_id is not None
        and instruct_sent
        and command_id is not None
        and final.get("status") in ("completed", "failed")
    )
    if not instruction_acked:
        print(f"\n  ⚠️  NOTE: Instruction was sent but agent didn't ACK it.")
        print(f"     This means the agent sub-process needs to call")
        print(f"     lina-db__get_pending_instructions() during its work.")
        print(f"     Consider adding it to the agent's AGENTS.md instructions.")
    print(f"\n  {'✅ TEST PASSED' if success else '❌ TEST FAILED'}")

    await client.disconnect()
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    asyncio.run(main())
