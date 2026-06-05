"""
send_bot_lib — Hybrid communication: HTTP bridge → DB → Comm relay.

Multi-canal delivery:
1. HTTP POST direct to target gateway /api/comm (fast, <10ms)
2. DB write to agent_messages (persistent backup)
3. Comm relay via Telegram (fallback if HTTP fails)
"""

import asyncio
import json
import time
import httpx
from pathlib import Path
from telethon import TelegramClient
from telethon.tl.functions.messages import GetDialogsRequest
from telethon.tl.types import InputPeerEmpty, MessageEntityMention

API_ID = 35434942
API_HASH = "9f2a614fbf2e8cbfaf844561b7f43294"
SESSION = str(Path(__file__).parent / "comm_session")

BOTS = {
    "lina": {"username": "s_lina_bot", "gateway": "localhost:9093"},
    "cline": {"username": "s_cline_bot", "gateway": "localhost:9092"},
    "goose": {"username": "s_goose_bot", "gateway": "localhost:9091"},
    "gemma": {"username": "s_gemma_bot", "gateway": "localhost:9094"},
}
GROUP_SUBSTR = "Comm"


async def _get_group(client):
    dialogs = await client(GetDialogsRequest(
        offset_date=None, offset_id=0, offset_peer=InputPeerEmpty(), limit=200, hash=0,
    ))
    for d in dialogs.chats:
        title = getattr(d, "title", "") or ""
        if GROUP_SUBSTR in title:
            return d.id, title
    return None, None


async def _send_http(target: str, text: str, msg_id: str) -> bool:
    """Try HTTP POST to target gateway /api/comm."""
    info = BOTS.get(target)
    if not info:
        return False
    url = f"http://{info['gateway']}/api/comm"
    payload = {"from": "comm_bridge", "to": target, "text": text, "id": msg_id}
    try:
        async with httpx.AsyncClient(timeout=5.0, verify=False) as c:
            r = await c.post(url, json=payload)
        if r.status_code == 200:
            print(f"OK: HTTP → {target} ({info['gateway']})")
            return True
        print(f"WARN: HTTP {r.status_code} from {url}")
    except (httpx.ConnectError, httpx.TimeoutException, OSError) as e:
        print(f"WARN: HTTP fail → {url}: {e}")
    return False


async def _send_comm(target: str, text: str) -> bool:
    """Fallback: send via Comm Telegram relay."""
    info = BOTS.get(target)
    if not info:
        print(f"ERROR: Target desconocido: {target}")
        return False
    try:
        client = TelegramClient(SESSION, API_ID, API_HASH)
        client.parse_mode = None
        await client.start()
        gid, gtitle = await _get_group(client)
        if not gid:
            print("ERROR: Grupo Comm no encontrado")
            await client.disconnect()
            return False
        text = f"@{info['username']} {text}"
        entities = [MessageEntityMention(offset=0, length=len(info['username']) + 1)]
        await client.send_message(gid, text, formatting_entities=entities)
        print(f"OK: Comm → @{info['username']} en '{gtitle}'")
        await client.disconnect()
        return True
    except Exception as e:
        print(f"ERROR: Comm relay: {e}")
        try:
            await client.disconnect()
        except Exception:
            pass
        return False


async def send_to(target: str, text: str) -> bool:
    """Send message to target bot via hybrid approach.
    
    1. Try HTTP direct to target gateway
    2. Fallback to Comm relay via Telegram
    """
    msg_id = f"comm_{int(time.time())}_{target}"
    
    # Try HTTP (fast path)
    http_ok = await _send_http(target, text, msg_id)
    if http_ok:
        return True
    
    # Fallback to Comm relay (slow path)
    print(f"HTTP failed, falling back to Comm relay...")
    return await _send_comm(target, text)
