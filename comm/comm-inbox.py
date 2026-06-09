#!/usr/bin/env python3
"""
comm-inbox — Lee mensajes recibidos por un bot vía Comm.

Uso:
  python3 comm-inbox.py <bot_name> [opciones]

  <bot_name>: goose | lina | cline | gemma | fede

Opciones:
  --last N         Últimos N mensajes (default: 5)
  --pending        Solo mensajes no leídos (status='delivered')
  --since ID       Mensajes desde ID específico
  --watch          Modo watch: muestra mensajes nuevos en tiempo real

Variables de entorno:
  LINA_DB_URL — default: postgresql://lina:lina_dev@localhost:5432/lina

Ejemplo:
  python3 comm-inbox.py goose --last 10
  python3 comm-inbox.py goose --pending --watch
"""

import asyncio
import os
import sys
import time
from datetime import datetime, timezone

import asyncpg

DB_URL = os.environ.get(
    "LINA_DB_URL",
    "postgresql://lina:lina_dev@localhost:5432/lina"
)

VALID_BOTS = {"goose", "lina", "cline", "gemma", "fede"}
BOT_EMOJIS = {"lina": "🩷", "cline": "😎", "gemma": "🔹", "fede": "👤"}
BOT_NAMES = {"lina": "LINA", "cline": "Cline", "gemma": "Gemma", "fede": "Fede"}


def log(msg: str):
    ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


async def show_inbox(bot: str, last: int = 5, pending: bool = False, since: int = 0):
    conn = await asyncpg.connect(DB_URL, timeout=10)
    try:
        if pending:
            rows = await conn.fetch(
                "SELECT id, sender, message, created_at "
                "FROM comm_messages "
                "WHERE destination = $1 AND status = 'delivered' "
                "ORDER BY created_at DESC LIMIT $2",
                bot, last
            )
        elif since:
            rows = await conn.fetch(
                "SELECT id, sender, message, status, created_at, delivered_at "
                "FROM comm_messages "
                "WHERE destination = $1 AND id > $2 "
                "ORDER BY created_at ASC",
                bot, since
            )
        else:
            rows = await conn.fetch(
                "SELECT id, sender, message, status, created_at, delivered_at "
                "FROM comm_messages "
                "WHERE destination = $1 "
                "ORDER BY created_at DESC LIMIT $2",
                bot, last
            )

        if not rows:
            print(f"📭 No hay mensajes para @{BOT_NAMES.get(bot, bot)}")
            return

        emoji = BOT_EMOJIS.get(bot, "📬")
        name = BOT_NAMES.get(bot, bot)
        print(f"{emoji} Inbox de @{name} ({len(rows)} mensaje(s)):")
        print("─" * 60)

        for row in reversed(rows):
            sender_emoji = BOT_EMOJIS.get(row["sender"], "📤")
            sender_name = BOT_NAMES.get(row["sender"], row["sender"])
            ts = row["created_at"].strftime("%H:%M:%S")
            status_icon = "✅" if row["status"] == "delivered" else "⏳"
            preview = row["message"][:100].replace("\n", " ")
            if len(row["message"]) > 100:
                preview += "..."

            print(f"  #{row['id']} {sender_emoji} {sender_name} → {status_icon} ({ts})")
            print(f"     {preview}")
            print()

    finally:
        await conn.close()


async def watch_inbox(bot: str):
    conn = await asyncpg.connect(DB_URL, timeout=10)
    try:
        # Show existing pending first
        await show_inbox(bot, last=10, pending=True)

        last_id = 0
        try:
            row = await conn.fetchrow(
                "SELECT MAX(id) as max_id FROM comm_messages WHERE destination = $1",
                bot
            )
            if row and row["max_id"]:
                last_id = row["max_id"]
        except Exception:
            pass

        log(f"📡 Escuchando mensajes nuevos para @{BOT_NAMES.get(bot, bot)}...")

        while True:
            rows = await conn.fetch(
                "SELECT id, sender, message, created_at "
                "FROM comm_messages "
                "WHERE destination = $1 AND id > $2 AND status = 'delivered' "
                "ORDER BY created_at ASC",
                bot, last_id
            )

            for row in rows:
                sender_emoji = BOT_EMOJIS.get(row["sender"], "📤")
                sender_name = BOT_NAMES.get(row["sender"], row["sender"])
                ts = row["created_at"].strftime("%H:%M:%S")
                print(f"{sender_emoji} [{ts}] #{row['id']} {sender_name}:")
                print(f"  {row['message']}")
                last_id = max(last_id, row["id"])

            await asyncio.sleep(2)
    finally:
        await conn.close()


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    bot = sys.argv[1].lower()
    if bot not in VALID_BOTS:
        print(f"❌ Bot inválido: {bot}")
        print(f"   Válidos: {', '.join(sorted(VALID_BOTS))}")
        sys.exit(1)

    last = 5
    pending = False
    since = 0
    watch = False

    args = sys.argv[2:]
    i = 0
    while i < len(args):
        if args[i] == "--last" and i + 1 < len(args):
            last = int(args[i + 1])
            i += 2
        elif args[i] == "--pending":
            pending = True
            i += 1
        elif args[i] == "--since" and i + 1 < len(args):
            since = int(args[i + 1])
            i += 2
        elif args[i] == "--watch":
            watch = True
            i += 1
        else:
            i += 1

    if watch:
        asyncio.run(watch_inbox(bot))
    else:
        asyncio.run(show_inbox(bot, last, pending, since))


if __name__ == "__main__":
    main()
