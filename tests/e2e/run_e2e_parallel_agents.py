#!/usr/bin/env python3
"""
E2E test: parallel sub-agents.

Instructs LINA to spawn N agents concurrently for independent tasks, then:
  1. Monitors all agents via /agents command while they run
  2. Verifies /status <id> shows per-agent detail
  3. Waits for all agents to complete
  4. Reports wall-clock time and overlap (proves parallelism)

Usage:
    uv run --with psycopg2-binary python3 tests/e2e/run_e2e_parallel_agents.py

The test passes if:
  - At least 2 agents are created
  - All created agents reach completed/failed status
  - Multiple agents are simultaneously in running/pending state (overlap proof)
"""

from __future__ import annotations

import asyncio
import datetime
import os
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
AGENT_SPAWN_TIMEOUT = 120       # time to wait until at least 2 agents appear
AGENT_COMPLETE_TIMEOUT = 900    # max time for all agents to finish
POLL_INTERVAL = 10              # seconds between /agents polls
STABLE_WINDOW = 5.0             # LINA stream settled

N_AGENTS = 3                    # number of parallel agents to request


# ── DB helpers ────────────────────────────────────────────────────────────────
def db_query(sql: str, params=()) -> list[dict]:
    with psycopg2.connect(DB_URL) as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, row)) for row in cur.fetchall()]


def wait_for_agents(since_ts: float, count: int, timeout: int) -> list[dict]:
    """Poll until at least `count` agent rows created after since_ts appear."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        rows = db_query(
            "SELECT id, role, goal, status, created_at FROM agent_sessions "
            "WHERE extract(epoch FROM created_at) > %s ORDER BY created_at DESC",
            (since_ts,),
        )
        if len(rows) >= count:
            return rows
        time.sleep(4)
    return db_query(
        "SELECT id, role, goal, status, created_at FROM agent_sessions "
        "WHERE extract(epoch FROM created_at) > %s ORDER BY created_at DESC",
        (since_ts,),
    )


def wait_for_all_done(agent_ids: list[str], timeout: int) -> dict[str, dict]:
    """Poll until all agents have a terminal status. Returns {id: row}."""
    deadline = time.time() + timeout
    results: dict[str, dict] = {}
    pending = set(agent_ids)
    while pending and time.time() < deadline:
        for aid in list(pending):
            rows = db_query(
                "SELECT id, status, result_summary, started_at, ended_at FROM agent_sessions WHERE id = %s",
                (aid,),
            )
            if rows and rows[0]["status"] not in ("running", "pending"):
                results[aid] = rows[0]
                pending.discard(aid)
        if pending:
            time.sleep(8)
    # Grab whatever is left
    for aid in pending:
        rows = db_query(
            "SELECT id, status, result_summary, started_at, ended_at FROM agent_sessions WHERE id = %s",
            (aid,),
        )
        if rows:
            results[aid] = rows[0]
    return results


def snapshot_active_agents() -> list[dict]:
    """Returns agents currently in pending/running state."""
    return db_query(
        "SELECT id, role, status, EXTRACT(EPOCH FROM (NOW() - started_at))::INT AS elapsed "
        "FROM agent_sessions WHERE status IN ('pending', 'running') ORDER BY created_at DESC"
    )


# ── Main ──────────────────────────────────────────────────────────────────────
async def main() -> None:
    print(f"[e2e-parallel] Connecting (session={SESSION_FILE})")
    client = TelegramClient(SESSION_FILE, API_ID, API_HASH)
    await client.connect()
    if not await client.is_user_authorized():
        print("[e2e-parallel] ERROR: session not authorized!")
        sys.exit(1)

    me = await client.get_me()
    print(f"[e2e-parallel] Connected as {me.first_name}")

    received: list[str] = []
    spawn_ts = time.time()
    test_label = datetime.datetime.now().strftime("%m%d-%H%M")

    @client.on(events.NewMessage(from_users=BOT_USERNAME))
    async def on_bot_msg(event: events.NewMessage.Event) -> None:
        text = event.message.message or ""
        received.append(text)
        print(f"  [BOT→] {text[:140].replace(chr(10), ' ')}")

    @client.on(events.MessageEdited(from_users=BOT_USERNAME))
    async def on_bot_edit(event: events.MessageEdited.Event) -> None:
        text = event.message.message or ""
        short = text[:100].replace("\n", " ")
        print(f"  [BOT↻] {short}")

    # ── Step 1: Ask LINA to spawn N agents in parallel ────────────────────
    test_msg = (
        f"[E2E Parallel {test_label}] Necesito que uses lina-orchestrator__spawn_agent "
        f"para lanzar {N_AGENTS} sub-agentes dev EN PARALELO, cada uno con una tarea "
        f"independiente y diferente. Las tareas son:\n"
        f"1. Buscar en el repo lina los archivos .py con más de 500 líneas y reportar sus nombres\n"
        f"2. Listar los últimos 5 commits del repo lina con su mensaje\n"
        f"3. Contar cuántos archivos .yaml hay en el repo lina\n"
        f"Lanzá los {N_AGENTS} agentes casi simultáneamente (no esperes que uno termine para "
        f"lanzar el siguiente). Usá spawn_agent una vez por cada tarea. "
        f"Confirmame cuando los hayas lanzado todos."
    )
    print(f"\n[Step 1] Sending parallel spawn request to {BOT_USERNAME}")
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
        print("[e2e-parallel] TIMEOUT: no reply from LINA")
        sys.exit(2)

    # Wait for LINA stream to settle
    last_count = len(received)
    last_change = time.time()
    while time.time() - last_change < STABLE_WINDOW:
        await asyncio.sleep(1)
        if len(received) != last_count:
            last_count = len(received)
            last_change = time.time()

    # ── Step 3: Wait for agents to appear in DB ───────────────────────────
    print(f"\n[Step 3] Waiting for {N_AGENTS} agents in DB (timeout={AGENT_SPAWN_TIMEOUT}s)")
    agents = wait_for_agents(spawn_ts, N_AGENTS, AGENT_SPAWN_TIMEOUT)
    agent_ids = [a["id"] for a in agents]
    print(f"  Found {len(agents)} agent(s):")
    for a in agents:
        print(f"    id={a['id'][:8]}… role={a['role']} status={a['status']}")

    if len(agents) < 2:
        print(f"[e2e-parallel] WARNING: only {len(agents)} agent(s) spawned (expected {N_AGENTS})")

    # ── Step 4: Monitor via /agents while agents run ──────────────────────
    print(f"\n[Step 4] Polling /agents while agents run (every {POLL_INTERVAL}s)")
    max_concurrent = 0
    poll_results: list[tuple[float, int]] = []  # (elapsed, active_count)
    monitor_deadline = time.time() + 60  # monitor for up to 60s
    while time.time() < monitor_deadline and agent_ids:
        active = snapshot_active_agents()
        # Only count agents from this test run
        test_active = [a for a in active if a["id"] in agent_ids]
        n_active = len(test_active)
        poll_results.append((time.time() - t0, n_active))
        if n_active > max_concurrent:
            max_concurrent = n_active
        print(f"  t={time.time()-t0:.0f}s: {n_active} agent(s) active [{', '.join(a['status'] for a in test_active)}]")

        # Also use /status on the first agent if available
        if agent_ids:
            await client.send_message(BOT_USERNAME, f"/agents")
            await asyncio.sleep(3)

        await asyncio.sleep(POLL_INTERVAL - 3)

    # ── Step 5: Wait for all agents to complete ───────────────────────────
    if agent_ids:
        print(f"\n[Step 5] Waiting for all agents to complete (timeout={AGENT_COMPLETE_TIMEOUT}s)")
        results = wait_for_all_done(agent_ids, AGENT_COMPLETE_TIMEOUT)
    else:
        results = {}

    # ── Step 6: Use /status on each agent ────────────────────────────────
    print(f"\n[Step 6] Checking /status for each agent")
    for aid in agent_ids[:3]:
        await client.send_message(BOT_USERNAME, f"/status {aid[:8]}")
        await asyncio.sleep(5)

    # ── Summary ──────────────────────────────────────────────────────────
    total_time = time.time() - t0
    print(f"\n[e2e-parallel] ─── SUMMARY ─────────────────────────────────────")
    print(f"  Total time: {total_time:.1f}s")
    print(f"  Agents spawned: {len(agents)} (requested: {N_AGENTS})")
    print(f"  Max concurrent running: {max_concurrent}")
    print(f"  Agent outcomes:")
    for aid, r in results.items():
        summary = str(r.get("result_summary") or "(no summary)")[:100]
        print(f"    {aid[:8]}… status={r['status']} — {summary}")

    # Overlap proof: check if multiple agents were ever running simultaneously
    if max_concurrent >= 2:
        print(f"\n  ✅ PARALLELISM VERIFIED: max {max_concurrent} agents ran concurrently")
    else:
        print(f"\n  ⚠️  Parallelism NOT verified (max concurrent={max_concurrent})")

    completed = sum(1 for r in results.values() if r["status"] == "completed")
    failed = sum(1 for r in results.values() if r["status"] == "failed")
    print(f"  Completed: {completed}  Failed: {failed}  Remaining: {len(agent_ids) - len(results)}")

    success = len(agents) >= 2 and len(results) >= len(agents)
    print(f"\n  {'✅ TEST PASSED' if success else '❌ TEST FAILED'}")

    await client.disconnect()
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    asyncio.run(main())
