#!/usr/bin/env python3
"""
test-goose-thinking.py — Test de mensaje fede→goose con detección de respuesta.

Flujo:
  1. Envía "Hola, deci OK nada mas" como fede → goose vía comm_messages
  2. Monitorea logs de goosed (PID 1745) buscando "Session completed"
  3. Lee el grupo Comm via Telethon para ver la respuesta
  4. Notifica resultado

Uso:
  python3 test-goose-thinking.py
"""

import asyncio
import os
import re
import sys
import time
from datetime import datetime, timezone

os.chdir(os.path.dirname(os.path.abspath(__file__)) or "/home/fede/lina/comm")

DB_URL = "postgresql://lina:lina_dev@localhost:5432/lina"
GOOSED_PID = 1745


async def send_message(text: str) -> int | None:
    """Envía mensaje fede→goose y devuelve el msg_id."""
    import asyncpg
    conn = await asyncpg.connect(DB_URL, timeout=10)
    try:
        row = await conn.fetchrow(
            "INSERT INTO comm_messages (sender, destination, message) "
            "VALUES ('fede', 'goose', $1) RETURNING id",
            text
        )
        print(f"  ✅ Mensaje #{row['id']} enviado: fede → goose")
        return row["id"]
    finally:
        await conn.close()


async def wait_for_goose_session(timeout: int = 30) -> dict | None:
    """Monitorea journalctl de goosed buscando Session completed."""
    start = time.time()
    pattern = re.compile(
        r"Session completed.*duration_ms: (\d+).*total_tokens: (\d+).*message_count: (\d+).*exit_type: \"?(\w+)\"?",
        re.IGNORECASE
    )
    
    print(f"  ⏳ Esperando respuesta de goose (timeout={timeout}s)...")
    
    since = datetime.now(timezone.utc).strftime("%H:%M:%S")
    cmd = ["journalctl", "_PID=" + str(GOOSED_PID), "--since", since, "-f"]
    
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    
    result = None
    while time.time() - start < timeout:
        try:
            line = await asyncio.wait_for(proc.stdout.readline(), timeout=1.0)
        except asyncio.TimeoutError:
            continue
        if not line:
            continue
        text = line.decode().strip()
        
        if "Session started" in text:
            print(f"  🟢 Goose empezó a procesar...")
        if "Session completed" in text:
            m = pattern.search(text)
            if m:
                result = {
                    "duration_ms": int(m.group(1)),
                    "total_tokens": int(m.group(2)),
                    "message_count": int(m.group(3)),
                    "exit_type": m.group(4),
                }
                print(f"  ✅ Goose completó: {result['duration_ms']}ms, {result['total_tokens']} tokens")
                break
    
    try:
        proc.terminate()
        await proc.wait()
    except:
        pass
    return result


async def read_group(messages: int = 5) -> list[dict]:
    """Lee últimos N mensajes del grupo Comm via Telethon."""
    from telethon import TelegramClient
    
    client = TelegramClient("comm_session", 35434942, "9f2a614fbf2e8cbfaf844561b7f43294")
    await client.start()
    
    group = None
    async for d in client.iter_dialogs():
        if "Comm" in (d.name or ""):
            group = d
            break
    
    if not group:
        print("  ❌ Grupo Comm no encontrado")
        await client.disconnect()
        return []
    
    entries = []
    async for msg in client.iter_messages(group, limit=messages):
        sender = "???"
        if msg.sender:
            sender = f"@{msg.sender.username}" if msg.sender.username else (msg.sender.first_name or "???")
        entries.append({
            "time": msg.date.strftime("%H:%M:%S"),
            "sender": sender,
            "text": (msg.text or "")[:300],
        })
    
    await client.disconnect()
    return entries


async def main():
    print("🧪 TEST: fede → goose thinking/response\n")
    
    # 1. Enviar mensaje
    msg_id = await send_message("Hola, deci OK nada mas")
    if not msg_id:
        print("❌ Error enviando mensaje")
        sys.exit(1)
    
    # 2. Esperar a que goose procese
    result = await wait_for_goose_session()
    if not result:
        print("❌ Timeout: goose no respondió")
        sys.exit(1)
    
    # 3. Leer grupo
    print("\n  📱 Leyendo últimos mensajes del grupo Comm...")
    entries = await read_group(6)
    
    print(f"\n{'Hora':<8} {'De':<20} Mensaje")
    print("-" * 80)
    for e in entries:
        txt = e["text"][:100].replace(chr(10), " ")
        print(f"{e['time']:<8} {e['sender']:<20} {txt}")
    
    # 4. Detectar si hubo thinking y/o respuesta
    has_thinking = any(
        "razonando" in e["text"].lower() 
        or "thinking" in e["text"].lower() 
        or "Razonando" in e["text"] 
        for e in entries
    )
    has_ok = any(e["text"].strip().lower() in ("ok", "ok.") for e in entries)
    
    print(f"\n📊 RESULTADOS:")
    print(f"  Session: {result['exit_type']} en {result['duration_ms']}ms")
    print(f"  Tokens: {result['total_tokens']} | Mensajes: {result['message_count']}")
    print(f"  Thinking mostrado: {'✅ Sí' if has_thinking else '❌ No'}")
    print(f"  Respuesta 'OK': {'✅ Sí' if has_ok else '❌ No'}")
    
    if has_thinking and not has_ok:
        print("\n⚠️ Goose muestra el razonamiento pero NO envía la respuesta final.")
    elif has_thinking and has_ok:
        print("\n✅ Goose pensó y respondió correctamente.")
    elif not has_thinking and has_ok:
        print("\n✅ Goose respondió directo sin thinking.")
    else:
        print("\n❌ No se detectó ni thinking ni respuesta.")


if __name__ == "__main__":
    asyncio.run(main())
