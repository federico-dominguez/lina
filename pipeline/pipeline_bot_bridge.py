"""
pipeline_bot_bridge — Envía @mention al bot via Telegram y espera respuesta COMPLETA.

Comunicación 100% por Telegram (el usuario ve todo en el grupo Comm).
La finalización se detecta por silencio: 15s sin mensajes del bot = terminó.
Los tests confirmaron que todos los bots siguen el patrón:
  thinking → 1-5s → respuesta final → SILENCIO = COMPLETADO
"""

import asyncio, time
from pathlib import Path
from telethon import TelegramClient
from telethon.tl.functions.messages import GetDialogsRequest
from telethon.tl.types import InputPeerEmpty

API_ID = 35434942
API_HASH = "9f2a614fbf2e8cbfaf844561b7f43294"
SESSION_FILE = str(Path(__file__).resolve().parent.parent / "comm" / "comm_session")

BOTS = {
    "lina":  {"username": "s_lina_bot",  "name": "LINA"},
    "cline": {"username": "s_cline_bot", "name": "Cline"},
    "gemma": {"username": "s_gemma_bot", "name": "Gemma"},
    "goose": {"username": "s_goose_bot", "name": "Goose"},
}


POLL_INTERVAL = 2
SILENCE_TIMEOUT = 90  # segundos sin mensajes del bot = terminó
# El gap más largo entre mensajes de un bot fue 40s (Cline, herramienta pesada)
# Con 45s nos aseguramos de no cortar prematuramente
MAX_WAIT = 600


async def _find_group(client):
    dialogs = await client(GetDialogsRequest(
        offset_date=None, offset_id=0, offset_peer=InputPeerEmpty(), limit=200, hash=0))
    for d in dialogs.chats:
        if "Comm" in getattr(d, "title", ""):
            return d.id
    return None


def send(bot_name: str, text: str, timeout: int = MAX_WAIT, last_n: int = 0) -> str:
    """
    Envía @mention. 
    Si last_n > 0, devuelve solo los últimos N mensajes del bot.
    last_n=3 → último mensaje de thinking + 2 de respuesta final.
    """
    """Envía @mention al bot en el grupo Comm y espera respuesta COMPLETA.
    
    Returns: texto de TODOS los mensajes del bot concatenados.
    """
    return asyncio.run(_send(bot_name, text, timeout, last_n))


async def _send(bot_name: str, text: str, timeout: int, last_n: int = 0) -> str:
    info = BOTS.get(bot_name)
    if not info:
        return f"ERROR: Bot '{bot_name}' desconocido"

    username = info["username"]
    client = TelegramClient(SESSION_FILE, API_ID, API_HASH)
    await client.start()
    
    try:
        gid = await _find_group(client)
        if not gid:
            return "ERROR: Grupo Comm no encontrado"

        # Last message before our send
        last = await client.get_messages(gid, limit=1)
        last_id = last[0].id if last else 0

        # Send @mention to the bot
        msg_text = f"@{username} {text}"
        sent = await client.send_message(gid, msg_text)
        print(f"  📤 @{username} enviado (msg #{sent.id})")

        # ── Collect all responses from this bot ──
        seen = set()
        messages = []
        idle_since = None
        deadline = time.time() + timeout

        while time.time() < deadline:
            await asyncio.sleep(POLL_INTERVAL)
            msgs = await client.get_messages(gid, limit=25)

            got_new = False
            for msg in msgs:
                if msg.id in seen or msg.id <= last_id:
                    continue
                sender = msg.sender
                if not sender or not sender.username:
                    continue
                if sender.username.lower().lstrip("@") != username.lower().lstrip("@"):
                    continue
                seen.add(msg.id)
                got_new = True
                t = (msg.text or "").strip()
                messages.append((msg.id, t))
                print(f"    📥 msg#{msg.id} ({len(t)} chars)")

            if got_new:
                idle_since = None
            else:
                if idle_since is None:
                    idle_since = time.time()
                elif time.time() - idle_since >= SILENCE_TIMEOUT:
                    messages.sort(key=lambda x: x[0])
                    filtered = messages[-last_n:] if last_n else messages
                    total = sum(len(m[1]) for m in messages)
                    print(f"  ✅ @{username} completado ({len(seen)} msgs, {total} chars, last={len(filtered)})")
                    await client.disconnect()
                    return "\n".join(m[1] for m in filtered)

        # Timeout
        filtered = messages[-last_n:] if last_n else messages
        total = sum(len(m[1]) for m in filtered)
        print(f"  ⚠️ Timeout ({timeout}s). {len(filtered)}/{len(messages)} msgs, {total} chars")
        await client.disconnect()
        return "\n".join(m[1] for m in filtered)

    except Exception as e:
        print(f"ERROR: {e}")
        try:
            await client.disconnect()
        except Exception:
            pass
        return ""


if __name__ == "__main__":
    import sys
    bot = sys.argv[1] if len(sys.argv) > 1 else "lina"
    msg = " ".join(sys.argv[2:]) or "Decime quién sos en una línea"
    print(f"\n🔬 @{BOTS[bot]['username']}: {msg}\n")
    resp = send(bot, msg, timeout=120)
    print(f"\n📝 ({len(resp)} chars): {resp[:300]}")
