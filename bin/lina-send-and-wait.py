#!/usr/bin/env python3
"""
lina-send-and-wait — Envía un mensaje a LINA y espera su respuesta.

Uso:
    python3 /home/user/lina/bin/lina-send-and-wait.py "mensaje"
    python3 /home/user/lina/bin/lina-send-and-wait.py "mensaje" --timeout 120

Flujo:
  1. Envía el mensaje a @s_lina_bot como si fuera Fede
  2. Espera hasta N segundos la respuesta de LINA
  3. Muestra la respuesta y termina
"""

import asyncio
import sys
import time
from pathlib import Path

from telethon import TelegramClient

API_ID = 35434942
API_HASH = "9f2a614fbf2e8cbfaf844561b7f43294"
SESSION_PATH = str(Path(__file__).parent.parent / "tests" / "e2e" / "telegram" / ".sessions" / "lina_e2e")
LINA_BOT = "@s_lina_bot"


async def send_and_wait(text: str, timeout: int = 120):
    client = TelegramClient(SESSION_PATH, API_ID, API_HASH)
    client.parse_mode = None
    await client.start()
    me = await client.get_me()
    bot = await client.get_entity(LINA_BOT)

    print(f"✅ Conectado como {me.first_name}")
    print(f"📤 Enviando a {LINA_BOT}...")
    await client.send_message(bot, text)
    print("✅ Mensaje enviado.")
    print(f"⏳ Esperando respuesta (timeout {timeout}s)...", flush=True)

    deadline = time.monotonic() + timeout
    last_id = 0
    while time.monotonic() < deadline:
        remaining = int(deadline - time.monotonic())
        msgs = await client.get_messages(bot, limit=5)
        for msg in msgs:
            if msg.out is False and msg.id > last_id:
                print(f"\n📥 Respuesta de LINA:\n{'─'*60}")
                print(msg.text or "(sin texto)")
                print(f"{'─'*60}")
                await client.disconnect()
                return msg.text

        sys.stdout.write(".")
        sys.stdout.flush()
        await asyncio.sleep(2)

    print(f"\n⚠️ No se recibió respuesta en {timeout}s.")
    await client.disconnect()
    return None


def main():
    args = sys.argv[1:]
    timeout = 120
    msg_parts = []

    i = 0
    while i < len(args):
        if args[i] == "--timeout" and i + 1 < len(args):
            timeout = int(args[i + 1])
            i += 2
        elif args[i].startswith("--timeout="):
            timeout = int(args[i].split("=", 1)[1])
            i += 1
        else:
            msg_parts.append(args[i])
            i += 1

    message = " ".join(msg_parts) if msg_parts else sys.stdin.read().strip()
    if not message:
        print("❌ Uso: lina-send-and-wait.py 'mensaje' [--timeout 120]")
        sys.exit(1)

    result = asyncio.run(send_and_wait(message, timeout))
    sys.exit(0 if result else 1)


if __name__ == "__main__":
    main()
