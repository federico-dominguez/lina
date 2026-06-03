"""Shared library for send-bot scripts."""
import asyncio
from pathlib import Path
from telethon import TelegramClient, errors
from telethon.tl.functions.messages import GetDialogsRequest
from telethon.tl.types import InputPeerEmpty, MessageEntityMention

API_ID = 35434942
API_HASH = "9f2a614fbf2e8cbfaf844561b7f43294"
SESSION = str(Path(__file__).parent / "comm_session")

BOTS = {
    "lina": "s_lina_bot", "cline": "s_cline_bot", "goose": "s_goose_bot",
}
GROUP_PREFIX = "comm-bots-"

async def _get_group(client):
    dialogs = await client(GetDialogsRequest(
        offset_date=None, offset_id=0, offset_peer=InputPeerEmpty(), limit=200, hash=0,
    ))
    for d in dialogs.chats:
        title = getattr(d, "title", "") or ""
        if title.startswith(GROUP_PREFIX):
            return d.id, title
    return None, None

async def send_to(target: str, message: str):
    client = TelegramClient(SESSION, API_ID, API_HASH)
    client.parse_mode = None
    await client.start()
    gid, gtitle = await _get_group(client)
    if not gid:
        print("ERROR: Grupo Comm no encontrado")
        await client.disconnect()
        return False
    username = BOTS.get(target)
    if not username:
        print(f"ERROR: Target desconocido: {target}")
        await client.disconnect()
        return False
    text = f"@{username} {message}"
    entities = [MessageEntityMention(offset=0, length=len(username) + 1)]
    try:
        await client.send_message(gid, text, formatting_entities=entities)
        print(f"OK: @{username} en '{gtitle}'")
        await client.disconnect()
        return True
    except Exception as e:
        print(f"ERROR: {e}")
        await client.disconnect()
        return False
