#!/usr/bin/env python3
"""
cline-recv — Lee mensajes de LINA (@s_lina_bot) desde Telegram.

Se conecta con la sesión Telethon de Fede y busca mensajes recibidos
de @s_lina_bot. Mantiene un archivo de offset para no repetir mensajes.

Uso:
    python3 /home/user/lina/bin/cline-recv.py              # mensajes nuevos no leídos
    python3 /home/user/lina/bin/cline-recv.py --all         # últimos 5 mensajes
    python3 /home/user/lina/bin/cline-recv.py --limit 10    # últimos N mensajes

Devuelve JSON para consumo programático.
"""

import asyncio
import json
import sys
from pathlib import Path

from telethon import TelegramClient
from telethon.tl.types import Message, MessageService

API_ID = 35434942
API_HASH = "9f2a614fbf2e8cbfaf844561b7f43294"
SESSION_PATH = str(Path(__file__).parent.parent / "tests" / "e2e" / "telegram" / ".sessions" / "lina_e2e")
LINA_BOT = "@s_lina_bot"
OFFSET_FILE = Path(__file__).parent.parent / ".cline_recv_offset"


async def fetch_new(client, bot, limit: int, offset: int) -> list[dict]:
    """Fetch messages from LINA's bot, tracking offset."""
    messages = []
    async for msg in client.iter_messages(bot, limit=limit * 2):
        if isinstance(msg, MessageService):
            continue
        if msg.out:
            continue  # sent by us (Fede), skip
        if msg.id <= offset:
            break
        messages.append({
            "id": msg.id,
            "text": msg.text or "",
            "date": msg.date.isoformat() if msg.date else None,
        })
    return list(reversed(messages))  # oldest first


async def fetch_recent(client, bot, limit: int) -> list[dict]:
    """Fetch recent messages from LINA's bot (no offset filter)."""
    messages = []
    async for msg in client.iter_messages(bot, limit=limit * 2):
        if isinstance(msg, MessageService):
            continue
        if msg.out:
            continue
        messages.append({
            "id": msg.id,
            "text": msg.text or "",
            "date": msg.date.isoformat() if msg.date else None,
        })
    return list(reversed(messages))


def load_offset() -> int:
    try:
        return int(OFFSET_FILE.read_text().strip())
    except (FileNotFoundError, ValueError):
        return 0


def save_offset(msg_id: int) -> None:
    OFFSET_FILE.parent.mkdir(parents=True, exist_ok=True)
    OFFSET_FILE.write_text(str(msg_id))


def main():
    show_all = "--all" in sys.argv
    limit = 5
    for arg in sys.argv[1:]:
        if arg == "--all":
            show_all = True
        elif arg.startswith("--limit="):
            limit = int(arg.split("=", 1)[1])
        elif arg == "--limit" and sys.argv.index(arg) + 1 < len(sys.argv):
            idx = sys.argv.index(arg)
            limit = int(sys.argv[idx + 1])

    offset = load_offset()

    async def run():
        client = TelegramClient(SESSION_PATH, API_ID, API_HASH)
        client.parse_mode = None
        await client.start()
        bot = await client.get_entity(LINA_BOT)

        if show_all or offset == 0:
            messages = await fetch_recent(client, bot, limit)
        else:
            messages = await fetch_new(client, bot, limit, offset)

        await client.disconnect()
        return messages

    messages = asyncio.run(run())

    if not messages:
        print(json.dumps({"messages": [], "status": "no_new"}))
        return

    max_id = max(m["id"] for m in messages)
    if not show_all and max_id > offset:
        save_offset(max_id)

    print(json.dumps({
        "messages": messages,
        "status": "ok",
        "count": len(messages),
        "new_offset": max_id,
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
