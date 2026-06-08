#!/usr/bin/env python3
"""
template-pipeline-sse.py — Template de pipeline multi-bot con SSE Finish.

Copia este archivo, editá STEPS y ejecutá:
    python3 pipeline/template-pipeline-sse.py

Dependencias: httpx, telethon, asyncpg
"""

import asyncio, json, os, subprocess, sys, time
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
SEND_BOT = str(BASE / "comm" / "send-bot.py")
COMM_DIR = str(BASE / "comm")
DB_DSN = os.environ.get("LINA_DB_URL", "postgresql://lina:lina_dev@localhost:5432/lina")

STEPS = [
    {"bot": "lina",  "msg": "hola",                                    "notify": "✅ Lina saludo."},
    {"bot": "cline", "msg": "decime el uso de CPU y memoria",          "notify": "✅ Cline sysinfo."},
    # Agregá más pasos acá:
    # {"bot": "gemma","msg": "qué opinás?",                             "notify": "✅ Gemma opinó."},
    # {"bot": "goose","msg": "resumí las últimas novedades",            "notify": "✅ Goose resumen."},
]

BOT_AGENT = {"lina":"lina","cline":"cline","goose":"goose","gemma":"gemma"}
POLL_TIMEOUT = 180

def log(msg): print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)

def tg_send(target, msg):
    r = subprocess.run([sys.executable,SEND_BOT,target,msg], capture_output=True,text=True,timeout=30)
    ok = r.returncode == 0
    if ok: log(f"📤 @{target}: {msg}")
    else: log(f"⚠️ {target}: {(r.stdout+r.stderr)[:100]}")
    return ok

async def tg_notify(text):
    from telethon import TelegramClient; from telethon.tl.functions.messages import GetDialogsRequest; from telethon.tl.types import InputPeerEmpty
    c = TelegramClient(str(Path(COMM_DIR)/"comm_session"),12663248,"57a7b9ec3cd607e64b73dbae1240af24"); c.parse_mode=None; await c.start()
    ds = await c(GetDialogsRequest(offset_date=None,offset_id=0,offset_peer=InputPeerEmpty(),limit=200,hash=0))
    g = next((d for d in ds.chats if "Comm" in (getattr(d,"title","") or "")), None)
    if g: await c.send_message(g.id, text); log(f"📤 (plain) {text}")
    else: log("❌ group not found")
    await c.disconnect()

async def wait_finish(agent):
    import asyncpg
    conn = await asyncpg.connect(DB_DSN,timeout=5)
    last = await conn.fetchval("SELECT COALESCE(MAX(id),0) FROM session_events WHERE event_type='finish' AND agent=$1",agent)
    await conn.close()
    log(f"⏳ Finish (last #{last})...")
    deadline = time.time()+POLL_TIMEOUT
    while time.time()<deadline:
        try:
            conn2 = await asyncpg.connect(DB_DSN,timeout=5)
            row = await conn2.fetchrow("SELECT id,session_id,payload FROM session_events WHERE event_type='finish' AND agent=$1 AND id>$2 ORDER BY id ASC LIMIT 1",agent,last)
            await conn2.close()
            if row:
                p = json.loads(row["payload"]) if isinstance(row["payload"],str) else (row["payload"] or {})
                log(f"🏁 #{row['id']} session={row['session_id']} tokens={p.get('tokens',0)}")
                return True
        except Exception as e: log(f"⚠️ DB: {e}"); last+=1
        await asyncio.sleep(0.5)
    log(f"⚠️ Timeout"); return False

async def main():
    log(f"🚀 Pipeline: {len(STEPS)} pasos")
    for i,s in enumerate(STEPS,1):
        log(f"\n─── {i}/{len(STEPS)}: @{s['bot']}: {s['msg'][:40]} ───")
        if not tg_send(s['bot'],s['msg']): continue
        if not await wait_finish(BOT_AGENT.get(s['bot'],s['bot'])): continue
        await tg_notify(s.get('notify',f"✅ {s['bot']} completo."))
    log("\n✅ Pipeline completado")

if __name__=="__main__": asyncio.run(main())
