#!/usr/bin/env python3
"""
test-pipeline-sse.py — Pipeline con detección de Finish vía session_events (DB).

Flujo:
  1. Envía mensaje a @s_lina_bot en el grupo "Comms"
  2. Espera evento "finish" en session_events (DB)
  3. Notifica al grupo (texto plano)
  4. Repite para Cline

Uso:
  python3 bin/test-pipeline-sse.py
"""

import asyncio
import json
import os
import subprocess
import sys
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
SEND_BOT = str(BASE / "comm" / "send-bot.py")
COMM_DIR = str(BASE / "comm")
DB_DSN = os.environ.get("LINA_DB_URL", "postgresql://lina:lina_dev@localhost:5432/lina")
POLL_INTERVAL = 0.5
POLL_TIMEOUT = 120

BOTS = [
    {"name": "lina", "display": "Lina",  "agent": "lina",  "group_msg": "decime el uso de CPU y memoria del servidor"},
    {"name": "cline","display": "Cline", "agent": "cline", "group_msg": "decime el uso de CPU y memoria del servidor"},
]


def log(msg: str):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def tg_send(target: str, message: str) -> bool:
    try:
        r = subprocess.run([sys.executable, SEND_BOT, target, message],
                           capture_output=True, text=True, timeout=30)
        out = (r.stdout + r.stderr).strip()
        if r.returncode == 0:
            log(f"📤 {out}")
            return True
        log(f"⚠️ tg_send: {out[:200]}")
        return False
    except Exception as e:
        log(f"⚠️ tg_send error: {e}")
        return False


async def tg_notify_plain(message: str) -> bool:
    try:
        from telethon import TelegramClient
        from telethon.tl.functions.messages import GetDialogsRequest
        from telethon.tl.types import InputPeerEmpty
        client = TelegramClient(str(Path(COMM_DIR) / "comm_session"), 12663248, "57a7b9ec3cd607e64b73dbae1240af24")
        client.parse_mode = None
        await client.start()
        dialogs = await client(GetDialogsRequest(offset_date=None, offset_id=0, offset_peer=InputPeerEmpty(), limit=200, hash=0))
        gid = None
        for d in dialogs.chats:
            t = (getattr(d, "title", "") or "")
            if "Comm" in t:
                gid = d.id
                break
        if not gid:
            log("❌ tg_notify: no group")
            await client.disconnect()
            return False
        await client.send_message(gid, message)
        log(f"📤 (plain) → '{message}'")
        await client.disconnect()
        return True
    except Exception as e:
        log(f"⚠️ tg_notify error: {e}")
        return False


async def wait_for_finish(agent: str) -> dict:
    import asyncpg
    conn = await asyncpg.connect(DB_DSN, timeout=5)
    last_id = await conn.fetchval("SELECT COALESCE(MAX(id),0) FROM session_events WHERE event_type='finish' AND agent=$1", agent)
    await conn.close()
    log(f"⏳ Esperando Finish (last_id=#{last_id})...")
    deadline = time.time() + POLL_TIMEOUT
    while time.time() < deadline:
        try:
            conn2 = await asyncpg.connect(DB_DSN, timeout=5)
            try:
                row = await conn2.fetchrow(
                    "SELECT id,session_id,payload FROM session_events WHERE event_type='finish' AND agent=$1 AND id>$2 ORDER BY id ASC LIMIT 1",
                    agent, last_id)
                if row:
                    p = json.loads(row["payload"]) if isinstance(row["payload"], str) else (row["payload"] or {})
                    log(f"🏁 [{agent}] Finish #{row['id']} session={row['session_id']} reason={p.get('reason','?')} tokens={p.get('tokens',0)}")
                    return {"session_id": row["session_id"], "reason": p.get("reason","stop"), "tokens": p.get("tokens",0), "cost": 0.0}
            finally:
                await conn2.close()
        except Exception as e:
            log(f"⚠️ [{agent}] DB error: {e}")
            last_id += 1
        await asyncio.sleep(POLL_INTERVAL)
    log(f"⚠️ [{agent}] Timeout")
    return {"session_id": "", "reason": "timeout", "tokens": 0, "cost": 0.0}


async def process_bot(cfg: dict) -> bool:
    name, display, agent = cfg["name"], cfg["display"], cfg["agent"]
    log(f"🎯 {display}...")
    log(f"📤 @s_{name}_bot: {cfg['group_msg']}")
    tg_send(name, cfg["group_msg"])
    fin = await wait_for_finish(agent)
    if fin.get("reason") == "timeout":
        return False
    await tg_notify_plain(f"✅ {display} completo. (pipeline)")
    return True


async def main():
    log(f"🚀 Pipeline: {[b['name'] for b in BOTS]}")
    for b in BOTS:
        ok = await process_bot(b)
        log(f"{'✅' if ok else '❌'} {b['display']}: {'OK' if ok else 'FAIL'}")
    log("═══ FIN ═══")


if __name__ == "__main__":
    asyncio.run(main())
