#!/usr/bin/env python3
"""
send-bot — Envía un mensaje a un bot en el grupo desde la cuenta Comm.

Uso:
    python3 send-bot.py <target> <mensaje>

Targets:
    lina   → @s_lina_bot
    cline  → @s_cline_bot
    goose  → @s_goose_bot
    todos  → @s_lina_bot @s_cline_bot @s_goose_bot

La cuenta Comm (59891992356) envía el mensaje al grupo.
Como viene de un usuario humano, todos los bots lo reciben.
"""

import asyncio
import sys
import os
from pathlib import Path
from telethon import TelegramClient
from telethon.tl.types import MessageEntityMention

API_ID = 12663248
API_HASH = "57a7b9ec3cd607e64b73dbae1240af24"
SESSION = str(Path(__file__).parent / "comm_session")

BOTS = {
    "lina": "s_lina_bot",
    "cline": "s_cline_bot",
    "goose": "s_goose_bot",
}

# El grupo compartido donde están todos los bots + Comm
# Se auto-detecta: el grupo con los 3 bots y Comm
GROUP_TITLE_PREFIX = "comm-bots-"


async def find_group(client):
    """Find the comm-bots group."""
    from telethon.tl.functions.messages import GetDialogsRequest
    from telethon.tl.types import InputPeerEmpty

    dialogs = await client(GetDialogsRequest(
        offset_date=None,
        offset_id=0,
        offset_peer=InputPeerEmpty(),
        limit=200,
        hash=0,
    ))
    for dialog in dialogs.chats:
        title = getattr(dialog, "title", "") or ""
        if title.startswith(GROUP_TITLE_PREFIX):
            return dialog.id, title
    return None, None


async def send(target: str, message: str):
    client = TelegramClient(SESSION, API_ID, API_HASH)
    client.parse_mode = None
    await client.start()

    group_id, group_title = await find_group(client)
    if not group_id:
        print("ERROR: No se encontró el grupo Comm. Creálo primero con setup-comm.py")
        await client.disconnect()
        return

    # Build text with @mention for target
    if target == "todos":
        text = " ".join(f"@{u}" for u in BOTS.values()) + f" {message}"
        entities = []
        offset = 0
        for u in BOTS.values():
            entities.append(MessageEntityMention(offset=offset, length=len(u) + 1))
            offset += len(u) + 2  # @ + space
    else:
        username = BOTS.get(target)
        if not username:
            print(f"ERROR: Target desconocido: {target}. Usá: lina, cline, goose, todos")
            await client.disconnect()
            return
        text = f"@{username} {message}"
        entities = [MessageEntityMention(offset=0, length=len(username) + 1)]

    await client.send_message(group_id, text, formatting_entities=entities)
    print(f"OK: mensaje enviado a @{username} en '{group_title}'")
    await client.disconnect()


async def setup():
    """Create the comm-bots group and invite all bots."""
    import random
    from telethon.tl.functions.messages import CreateChatRequest, AddChatUserRequest, GetDialogsRequest
    from telethon.tl.types import InputPeerEmpty

    client = TelegramClient(SESSION, API_ID, API_HASH)
    client.parse_mode = None
    await client.start()
    me = await client.get_me()
    print(f"Comm conectado: {me.first_name} (ID={me.id})")

    # Check if group already exists
    gid, gtitle = await find_group(client)
    if gid:
        print(f"Grupo ya existe: '{gtitle}' (ID={gid})")
        await client.disconnect()
        return

    # Create the group
    suffix = random.randint(1000, 9999)
    title = f"{GROUP_TITLE_PREFIX}{suffix}"
    result = await client(CreateChatRequest(
        users=["@s_lina_bot", "@s_cline_bot", "@s_goose_bot"],
        title=title,
    ))
    group_id = result.updates.chats[0].id
    group_title = result.chats[0].title if hasattr(result, 'chats') else title
    print(f"Grupo creado: '{group_title}' (ID={group_id})")
    await client.disconnect()


if __name__ == "__main__":
    if len(sys.argv) >= 2 and sys.argv[1] == "setup":
        asyncio.run(setup())
    elif len(sys.argv) < 3:
        print("Uso: python3 send-bot.py <target> <mensaje>")
        print("   o: python3 send-bot.py setup   (crear grupo)")
        sys.exit(1)
    else:
        target = sys.argv[1]
        message = " ".join(sys.argv[2:])
        asyncio.run(send(target, message))
