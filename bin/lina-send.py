#!/usr/bin/env python3
"""
lina-send — Envía un mensaje a LINA (@s_lina_bot) via Telegram desde la cuenta de Fede.

Uso desde Cline (shell):
    python3 /home/user/lina/bin/lina-send.py "mensaje"

Uso desde pipeline:
    echo "mensaje" | python3 /home/user/lina/bin/lina-send.py

Esto se conecta con la sesión Telethon de Fede y manda un mensaje
a @s_lina_bot COMO SI FUERA FEDE (no como bot).
LINA lo recibe como un mensaje directo y puede procesarlo.
"""

import asyncio
import sys
from pathlib import Path
from telethon import TelegramClient

API_ID = 35434942
API_HASH = "9f2a614fbf2e8cbfaf844561b7f43294"
SESSION_PATH = "/home/fede/lina/tests/e2e/telegram/.sessions/lina_e2e"
LINA_BOT = "@s_lina_bot"


async def send_message(text: str):
    client = TelegramClient(SESSION_PATH, API_ID, API_HASH)
    client.parse_mode = None
    await client.start()
    me = await client.get_me()
    bot = await client.get_entity(LINA_BOT)
    await client.send_message(bot, text)
    print(f"✅ Mensaje enviado a {LINA_BOT} como {me.first_name}")
    await client.disconnect()


def main():
    if len(sys.argv) > 1:
        message = " ".join(sys.argv[1:])
    else:
        message = sys.stdin.read().strip()

    if not message:
        print("❌ Uso: lina-send.py 'mensaje' | echo 'mensaje' | lina-send.py")
        sys.exit(1)

    asyncio.run(send_message(message))


if __name__ == "__main__":
    main()
