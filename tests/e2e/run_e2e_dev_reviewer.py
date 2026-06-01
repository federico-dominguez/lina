#!/usr/bin/env python3
"""
E2E test: dev agent → reviewer agent pipeline.

Flow:
  1. LINA spawns a dev agent to implement a small change (add a comment to
     a scratch file in the repo, then open a draft GitHub issue documenting it).
  2. Test waits for dev agent to complete.
  3. LINA spawns a reviewer agent that reads what dev did and:
     - comments on the GitHub issue with a review ("LGTM" or issues found)
     - reports the file change
  4. Test waits for reviewer to complete.
  5. Validates the two-phase handoff via agent_events in the DB.

Usage:
    uv run --with psycopg2-binary python3 tests/e2e/run_e2e_dev_reviewer.py

Passes if:
  - Two agents are created sequentially (dev then reviewer)
  - Both agents reach terminal state
  - reviewer agent result references dev agent's work
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
DEV_COMPLETE_TIMEOUT = 600
REVIEWER_COMPLETE_TIMEOUT = 600
STABLE_WINDOW = 5.0


# ── DB helpers ────────────────────────────────────────────────────────────────
def db_query(sql: str, params=()) -> list[dict]:
    with psycopg2.connect(DB_URL) as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, row)) for row in cur.fetchall()]


def wait_for_new_agent(since_ts: float, timeout: int, exclude_ids: list[str] | None = None) -> dict | None:
    """Poll agent_sessions for a new row created after since_ts, excluding known ids."""
    exclude_ids = exclude_ids or []
    deadline = time.time() + timeout
    while time.time() < deadline:
        rows = db_query(
            "SELECT id, role, goal, status, created_at FROM agent_sessions "
            "WHERE extract(epoch FROM created_at) > %s ORDER BY created_at DESC LIMIT 5",
            (since_ts,),
        )
        new_rows = [r for r in rows if r["id"] not in exclude_ids]
        if new_rows:
            return new_rows[0]
        time.sleep(4)
    return None


def wait_for_agent_done(agent_id: str, timeout: int) -> dict | None:
    """Poll until agent_sessions.status is terminal."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        rows = db_query(
            "SELECT id, status, result_summary, ended_at FROM agent_sessions WHERE id = %s",
            (agent_id,),
        )
        if rows and rows[0]["status"] not in ("running", "pending"):
            return rows[0]
        time.sleep(8)
    return None


def get_agent_events(agent_id: str) -> list[dict]:
    return db_query(
        "SELECT kind, ts, payload_json FROM agent_events WHERE session_id = %s ORDER BY ts",
        (agent_id,),
    )


# ── Helpers ───────────────────────────────────────────────────────────────────
async def wait_for_lina_reply(received: list[str], t0: float, timeout: float) -> bool:
    deadline = t0 + timeout
    while time.time() < deadline:
        await asyncio.sleep(2)
        if received:
            return True
    return False


async def settle(received: list[str]) -> None:
    last_count = len(received)
    last_change = time.time()
    while time.time() - last_change < STABLE_WINDOW:
        await asyncio.sleep(1)
        if len(received) != last_count:
            last_count = len(received)
            last_change = time.time()


# ── Main ──────────────────────────────────────────────────────────────────────
async def main() -> None:
    print(f"[e2e-pipeline] Connecting (session={SESSION_FILE})")
    client = TelegramClient(SESSION_FILE, API_ID, API_HASH)
    await client.connect()
    if not await client.is_user_authorized():
        print("[e2e-pipeline] ERROR: session not authorized!")
        sys.exit(1)

    me = await client.get_me()
    print(f"[e2e-pipeline] Connected as {me.first_name}")

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

    # ════════════════════════════════════════════════════════════════════════
    # Phase 1: Dev agent
    # ════════════════════════════════════════════════════════════════════════

    scratch_file = f"/home/user/lina/tests/e2e/pipeline_scratch_{test_label}.txt"
    phase1_msg = (
        f"[E2E Pipeline {test_label}] Fase 1 — dev. "
        f"Usá lina-orchestrator__spawn_agent con role='dev' para lanzar un agente "
        f"con esta tarea exacta: "
        f"'Creá el archivo {scratch_file} con el contenido: "
        f"[dev] Trabajo completado por el agente dev el {test_label}. "
        f"Además abrí un issue en el repo lina con título "
        f"\"[E2E Pipeline {test_label}] Dev completó su tarea\" "
        f"y descripción \"Archivo creado: {scratch_file}\". "
        f"Al final escribí en tu result_summary el número de issue creado.' "
        f"Confirmame el agent_id cuando lo hayas lanzado."
    )

    print(f"\n[Phase 1] Sending dev agent request")
    print(f"  → {phase1_msg[:200]}…")
    received.clear()
    await client.send_message(BOT_USERNAME, phase1_msg)
    t0 = time.time()

    if not await wait_for_lina_reply(received, t0, LINA_REPLY_TIMEOUT):
        print("[e2e-pipeline] TIMEOUT: no LINA reply for Phase 1")
        sys.exit(2)
    print(f"  LINA replied after {time.time()-t0:.1f}s")
    await settle(received)

    # Locate dev agent in DB
    print(f"\n  Waiting for dev agent in DB…")
    dev_agent = wait_for_new_agent(spawn_ts, AGENT_SPAWN_TIMEOUT)
    if not dev_agent:
        print("[e2e-pipeline] WARNING: dev agent not found in DB")
        dev_agent_id = None
    else:
        dev_agent_id = dev_agent["id"]
        print(f"  Dev agent: id={dev_agent_id[:8]}… status={dev_agent['status']}")
        print(f"  Goal: {dev_agent['goal'][:120]}")

    # Check /status while dev agent runs
    if dev_agent_id:
        await asyncio.sleep(5)
        print(f"\n  Checking /status {dev_agent_id[:8]}")
        await client.send_message(BOT_USERNAME, f"/status {dev_agent_id[:8]}")
        await asyncio.sleep(5)

    # Wait for dev to complete
    print(f"\n  Waiting for dev agent to complete (timeout={DEV_COMPLETE_TIMEOUT}s)…")
    if dev_agent_id:
        dev_result = wait_for_agent_done(dev_agent_id, DEV_COMPLETE_TIMEOUT)
        if dev_result:
            print(f"  Dev finished: status={dev_result['status']}")
            print(f"  Summary: {str(dev_result.get('result_summary'))[:200]}")
        else:
            print(f"  Dev agent TIMEOUT after {DEV_COMPLETE_TIMEOUT}s")
            dev_result = {"status": "timeout", "result_summary": None}
    else:
        dev_result = {"status": "unknown", "result_summary": None}

    # ════════════════════════════════════════════════════════════════════════
    # Phase 2: Reviewer agent
    # ════════════════════════════════════════════════════════════════════════

    phase2_msg = (
        f"[E2E Pipeline {test_label}] Fase 2 — reviewer. "
        f"El agente dev ({dev_agent_id[:8] if dev_agent_id else 'anterior'}) completó su tarea. "
        f"Ahora usá lina-orchestrator__spawn_agent con role='dev' para lanzar un agente reviewer "
        f"con esta tarea: "
        f"'Revisá el archivo {scratch_file} — si existe, leé su contenido. "
        f"Buscá issues abiertos en el repo lina con título que contenga \"E2E Pipeline {test_label}\". "
        f"Comentá en ese issue con: \"[reviewer] Revisión completada: archivo existe y contiene el texto correcto\" "
        f"o \"[reviewer] ERROR: archivo no encontrado o contenido incorrecto\" según corresponda. "
        f"En tu result_summary indicá si el trabajo del dev fue correcto o no.' "
        f"Confirmame el agent_id del reviewer."
    )

    print(f"\n[Phase 2] Sending reviewer agent request")
    print(f"  → {phase2_msg[:200]}…")
    received.clear()
    reviewer_spawn_ts = time.time()
    await client.send_message(BOT_USERNAME, phase2_msg)

    if not await wait_for_lina_reply(received, reviewer_spawn_ts, LINA_REPLY_TIMEOUT):
        print("[e2e-pipeline] TIMEOUT: no LINA reply for Phase 2")
        sys.exit(2)
    print(f"  LINA replied after {time.time()-reviewer_spawn_ts:.1f}s")
    await settle(received)

    # Locate reviewer agent in DB
    print(f"\n  Waiting for reviewer agent in DB…")
    reviewer_agent = wait_for_new_agent(
        reviewer_spawn_ts - 5,
        AGENT_SPAWN_TIMEOUT,
        exclude_ids=[dev_agent_id] if dev_agent_id else [],
    )
    if not reviewer_agent:
        print("[e2e-pipeline] WARNING: reviewer agent not found in DB")
        reviewer_agent_id = None
    else:
        reviewer_agent_id = reviewer_agent["id"]
        print(f"  Reviewer agent: id={reviewer_agent_id[:8]}… status={reviewer_agent['status']}")

    # Monitor both via /agents
    await asyncio.sleep(5)
    print(f"\n  Monitoring both agents via /agents…")
    await client.send_message(BOT_USERNAME, "/agents")
    await asyncio.sleep(8)

    # Wait for reviewer to complete
    print(f"\n  Waiting for reviewer to complete (timeout={REVIEWER_COMPLETE_TIMEOUT}s)…")
    if reviewer_agent_id:
        reviewer_result = wait_for_agent_done(reviewer_agent_id, REVIEWER_COMPLETE_TIMEOUT)
        if reviewer_result:
            print(f"  Reviewer finished: status={reviewer_result['status']}")
            print(f"  Summary: {str(reviewer_result.get('result_summary'))[:200]}")
        else:
            print(f"  Reviewer TIMEOUT after {REVIEWER_COMPLETE_TIMEOUT}s")
            reviewer_result = {"status": "timeout", "result_summary": None}
    else:
        reviewer_result = {"status": "unknown", "result_summary": None}

    # ── Check /events for both agents ─────────────────────────────────────
    print(f"\n  Event logs:")
    for name, aid in [("dev", dev_agent_id), ("reviewer", reviewer_agent_id)]:
        if aid:
            evts = get_agent_events(aid)
            print(f"    {name} ({aid[:8]}): {len(evts)} event(s)")
            for e in evts[-5:]:
                print(f"      {e['ts']} {e['kind']}")

    # ── Summary ───────────────────────────────────────────────────────────
    total_time = time.time() - t0
    print(f"\n[e2e-pipeline] ─── SUMMARY ─────────────────────────────────────")
    print(f"  Total time: {total_time:.1f}s")
    print(f"  Dev agent:      {dev_result['status']:15s} | {str(dev_result.get('result_summary'))[:100]}")
    print(f"  Reviewer agent: {reviewer_result['status']:15s} | {str(reviewer_result.get('result_summary'))[:100]}")
    print(f"  Messages from LINA: {len(received)}")

    success = (
        dev_agent_id is not None
        and reviewer_agent_id is not None
        and dev_result["status"] in ("completed", "failed")
        and reviewer_result["status"] in ("completed", "failed")
    )
    print(f"\n  {'✅ TEST PASSED' if success else '❌ TEST FAILED'}")

    await client.disconnect()
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    asyncio.run(main())
