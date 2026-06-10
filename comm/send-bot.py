#!/usr/bin/env python3
"""
send-bot — Envía un mensaje a un bot en el grupo desde la cuenta Comm.

Uso:
    python3 send-bot.py <target> <mensaje>

Targets:
    lina   → @s_lina_bot
    cline  → @s_cline_bot
    goose  → @s_goose_bot

La cuenta Comm envía el mensaje al grupo y todos los bots lo reciben.
"""

import asyncio
import sys
from pathlib import Path
from telethon import TelegramClient
from telethon.tl.functions.messages import GetDialogsRequest
from telethon.tl.types import InputPeerEmpty, MessageEntityMention

API_ID = 12663248
API_HASH = "57a7b9ec3cd607e64b73dbae1240af24"
SESSION = str(Path(__file__).parent / "comm_session_pipeline")

BOTS = {
    "lina": "s_lina_bot",
    "cline": "s_cline_bot",
    "goose": "s_goose_bot",
}


async def find_group(client):
    dialogs = await client(GetDialogsRequest(
        offset_date=None, offset_id=0, offset_peer=InputPeerEmpty(), limit=200, hash=0,
    ))
    for dialog in dialogs.chats:
        title = getattr(dialog, "title", "") or ""
        if "Comm" in title or "comm" in title:
            return dialog.id, title
    return None, None


async def send(target: str, message: str):
    client = TelegramClient(SESSION, API_ID, API_HASH)
    client.parse_mode = None
    await client.start()

    gid, gtitle = await find_group(client)
    if not gid:
        print("ERROR: No se encontró el grupo Comm.")
        await client.disconnect()
        return

    username = BOTS.get(target)
    if not username:
        print(f"ERROR: Target inválido: {target}")
        await client.disconnect()
        return

    text = f"@{username} {message}"
    entities = [MessageEntityMention(offset=0, length=len(username) + 1)]
    await client.send_message(gid, text, formatting_entities=entities)
    print(f"OK: @{username} en '{gtitle}'")
    await client.disconnect()


async def setup():
    client = TelegramClient(SESSION, API_ID, API_HASH)
    client.parse_mode = None
    await client.start()
    me = await client.get_me()
    print(f"✅ Comm: {me.first_name} (ID={me.id})")
    gid, gtitle = await find_group(client)
    if gid:
        print(f"✅ Grupo: '{gtitle}' (ID={gid})")
    else:
        print("⚠️ No hay grupo Comm")
    await client.disconnect()


if __name__ == "__main__":
    if len(sys.argv) >= 2 and sys.argv[1] == "setup":
        asyncio.run(setup())
    elif len(sys.argv) < 3:
        print("Uso: python3 send-bot.py <target> <mensaje>")
        sys.exit(1)
    else:
        asyncio.run(send(sys.argv[1], " ".join(sys.argv[2:])))
