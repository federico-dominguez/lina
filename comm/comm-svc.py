#!/usr/bin/env python3
"""
comm-svc — Servicio de mensajería entre bots vía DB + Telegram (cuenta Comm).

Flujo:
  1. Bot escribe en comm_messages (sender, destination, message)
  2. comm-svc detecta (poll + LISTEN/NOTIFY)
  3. Envía al grupo Comm via Telethon:
     "s_goose_bot says: @s_lina_bot Hola!"
  4. Marca como 'delivered' en DB
  5. Bot destino recibe, procesa, escribe su respuesta en DB
  6. Ciclo se repite

Uso:
  python3 comm-svc.py                    # Inicia el servicio
  python3 comm-svc.py --once             # Una sola iteración (útil para tests)
"""

import asyncio
import sys
import os
from datetime import datetime, timezone
from pathlib import Path

import asyncpg
from telethon import TelegramClient
from telethon.errors import FloodWaitError
from telethon.tl.functions.messages import GetDialogsRequest
from telethon.tl.types import InputPeerEmpty, MessageEntityMention

# ─── Config ───────────────────────────────────────────────────────────────────
DB_URL = os.environ.get(
    "LINA_DB_URL",
    "postgresql://lina:lina_dev@localhost:5432/lina"
)

API_ID = int(os.environ.get("COMM_API_ID", "35434942"))
API_HASH = os.environ.get("COMM_API_HASH", "9f2a614fbf2e8cbfaf844561b7f43294")

# La sesión Telethon de la cuenta Comm (+59891992356)
SERVICE_DIR = Path(__file__).parent
SESSION_PATH = str(SERVICE_DIR / "comm_session")

# Mapeo de nombres cortos a @usernames
BOTS = {
    "goose": "s_goose_bot",
    "lina":  "s_lina_bot",
    "cline": "s_cline_bot",
    "gemma": "s_gemma_bot",
    "fede":  "fededominguez",
}

# Substring para identificar el grupo Comm
GROUP_SUBSTR = "Comm"

POLL_INTERVAL = 2.0  # segundos entre polls (cuando no hay NOTIFY)


# ─── Logging ──────────────────────────────────────────────────────────────────
def log(msg: str):
    ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
    print(f"[{ts}] 📬 {msg}", flush=True)


# ─── Grupo Comm (auto-detect) ────────────────────────────────────────────────
async def find_group(client: TelegramClient) -> tuple[int | None, str | None]:
    dialogs = await client(GetDialogsRequest(
        offset_date=None, offset_id=0, offset_peer=InputPeerEmpty(),
        limit=200, hash=0,
    ))
    for d in dialogs.chats:
        title = getattr(d, "title", "") or ""
        if GROUP_SUBSTR in title:
            return d.id, title
    return None, None


# ─── Procesar mensajes pendientes ─────────────────────────────────────────────
async def process_pending(conn, tg: TelegramClient, gid: int) -> int:
    rows = await conn.fetch(
        "SELECT id, sender, destination, message "
        "FROM comm_messages WHERE status = 'sent' "
        "AND destination NOT IN ('lina', 'cline', 'gemma', 'goose', 'todos') "
        "AND sender NOT IN ('goose-health', 'goose-monitor', 'monitor', 'comm') "
        "ORDER BY created_at ASC LIMIT 5"
    )
    if not rows:
        return 0

    count = 0
    for row in rows:
        sender_username = BOTS.get(row["sender"], row["sender"])
        dest_username = BOTS.get(row["destination"], row["destination"])
        msg_text = row["message"]

        if row["destination"] == "todos":
            # Mencionar a todos
            mentions = " ".join(f"@{u}" for u in BOTS.values())
            text = f"{sender_username} → {mentions}: {msg_text}"
            entities = [
                MessageEntityMention(
                    offset=len(sender_username) + 7 + sum(len(u)+2 for u in list(BOTS.keys())[:i]),
                    length=len(u)+1
                )
                for i, u in enumerate(BOTS.values())
            ]
        else:
            text = f"{sender_username} → @{dest_username}: {msg_text}"
            offset = len(sender_username) + 4  # " → @" = 4
            entities = [MessageEntityMention(offset=offset, length=len(dest_username) + 1)]

        try:
            tg_msg = await tg.send_message(gid, text, formatting_entities=entities)
            await conn.execute(
                "UPDATE comm_messages SET status='delivered', delivered_at=NOW(), "
                "telegram_msg_id=$1 WHERE id=$2",
                tg_msg.id, row["id"]
            )
            log(f"#{row['id']} {row['sender']}→{row['destination']} ✅ ({len(msg_text)} chars)")
            count += 1
            
        except FloodWaitError as fwe:
            wait = fwe.seconds
            log(f"#{row['id']} {row['sender']}→{row['destination']} ⏳ FloodWait {wait}s — esperando...")
            await conn.execute(
                "UPDATE comm_messages SET status='retrying', error=$1 WHERE id=$2",
                f"FloodWait {wait}s", row["id"]
            )
            # Esperar el tiempo que pide Telegram + 1s de margen
            await asyncio.sleep(wait + 1)
            # Reintentar una vez
            try:
                tg_msg = await tg.send_message(gid, text, formatting_entities=entities)
                await conn.execute(
                    "UPDATE comm_messages SET status='delivered', delivered_at=NOW(), "
                    "telegram_msg_id=$1 WHERE id=$2",
                    tg_msg.id, row["id"]
                )
                log(f"#{row['id']} {row['sender']}→{row['destination']} ✅ retry exitoso ({len(msg_text)} chars)")
                count += 1
            except Exception as re:
                err2 = str(re)[:200]
                await conn.execute(
                    "UPDATE comm_messages SET status='failed', error=$1 WHERE id=$2",
                    err2, row["id"]
                )
                log(f"#{row['id']} {row['sender']}→{row['destination']} ❌ retry falló: {err2}")
            
            # ── NOTA: No re-enviar mensajes comm→goose/todos ──
            # Esto creaba un loop infinito (comm-svc leía su propio reenvío y lo reenviaba de nuevo)
            # El bridge ya se encarga de entregar los mensajes comm a los bots vía SSE.
        except Exception as e:
            err = str(e)[:200]
            await conn.execute(
                "UPDATE comm_messages SET status='failed', error=$1 WHERE id=$2",
                err, row["id"]
            )
            log(f"#{row['id']} {row['sender']}→{row['destination']} ❌ {err}")

        await asyncio.sleep(1)  # pausa entre mensajes

    return count


# ─── Main loop ────────────────────────────────────────────────────────────────
async def main_loop(once: bool = False):
    log("Iniciando comm-svc...")

    conn = await asyncpg.connect(DB_URL, timeout=10)
    log("✅ Conectado a PostgreSQL")

    tg = TelegramClient(SESSION_PATH, API_ID, API_HASH)
    tg.parse_mode = None
    await tg.start()
    me = await tg.get_me()
    log(f"✅ Telegram conectado como {me.first_name} (ID={me.id})")

    gid, gtitle = await find_group(tg)
    if not gid:
        log("❌ Grupo Comm no encontrado. Ejecutá setup primero.")
        await tg.disconnect()
        await conn.close()
        return
    log(f"✅ Grupo: '{gtitle}' (ID={gid})")

    # Procesar pendientes al arrancar
    await process_pending(conn, tg, gid)

    if once:
        log("🏁 Modo --once: terminando")
        await tg.disconnect()
        await conn.close()
        return

    # LISTEN para despertar con NOTIFY en vez de solo poll
    def on_notify(connection, pid, channel, payload):
        log(f"🔔 NOTIFY: comm_new_message #{payload}")
        asyncio.create_task(process_pending(conn, tg, gid))

    await conn.add_listener("comm_new_message", on_notify)
    log("📡 Escuchando NOTIFY comm_new_message + poll cada 2s")

    # Loop principal: poll + esperar NOTIFY
    while True:
        await process_pending(conn, tg, gid)
        await asyncio.sleep(POLL_INTERVAL)


# ─── Entrypoint ───────────────────────────────────────────────────────────────
def main():
    once = "--once" in sys.argv
    asyncio.run(main_loop(once))


if __name__ == "__main__":
    main()
