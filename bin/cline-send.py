#!/usr/bin/env python3
"""
cline-send — Envía un mensaje a Cline via Telegram desde la cuenta de Fede.

Uso desde LINA (shell):
    python3 /home/user/lina/bin/cline-send.py "mensaje"

Uso desde pipeline:
    echo "mensaje" | python3 /home/user/lina/bin/cline-send.py

Esto se conecta con la sesión Telethon de Fede y manda un mensaje
a @s_cline_bot COMO SI FUERA FEDE (no como bot).
Cline lo recibe como un mensaje directo en el chat del bot.
"""

import asyncio
import sys
from pathlib import Path
from telethon import TelegramClient

API_ID = 35434942
API_HASH = "9f2a614fbf2e8cbfaf844561b7f43294"
SESSION_PATH = str(Path(__file__).parent.parent / "tests" / "e2e" / "telegram" / ".sessions" / "lina_e2e")
CLINE_BOT = "@s_cline_bot"


async def send_message(text: str):
    client = TelegramClient(SESSION_PATH, API_ID, API_HASH)
    client.parse_mode = None
    await client.start()
    me = await client.get_me()
    bot = await client.get_entity(CLINE_BOT)
    await client.send_message(bot, text)
    print(f"✅ Mensaje enviado a {CLINE_BOT} como {me.first_name}")
    await client.disconnect()


def main():
    if len(sys.argv) > 1:
        message = " ".join(sys.argv[1:])
    else:
        message = sys.stdin.read().strip()

    if not message:
        print("❌ Uso: cline-send.py 'mensaje' | echo 'mensaje' | cline-send.py")
        sys.exit(1)

    asyncio.run(send_message(message))


if __name__ == "__main__":
    main()
