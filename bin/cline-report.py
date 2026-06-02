#!/usr/bin/env python3
"""
CLINE → LINA: reporta resultado de una tarea via Telegram.
Reusa la sesión E2E persistida.
"""

import asyncio
import os
import sys
from pathlib import Path

from telethon import TelegramClient

API_ID = int(os.environ["TELEGRAM_TEST_API_ID"])
API_HASH = os.environ["TELEGRAM_TEST_API_HASH"]
PHONE = os.environ["TELEGRAM_TEST_PHONE"]
BOT_USERNAME = os.environ.get("LINA_BOT_USERNAME", "@s_lina_bot")

SESSION_DIR = Path(__file__).parent.parent / "tests" / "e2e" / "telegram" / ".sessions"
SESSION_PATH = str(SESSION_DIR / "lina_e2e")


async def main():
    message = sys.argv[1] if len(sys.argv) > 1 else "(sin mensaje)"

    client = TelegramClient(SESSION_PATH, API_ID, API_HASH)
    client.parse_mode = None

    await client.start(phone=PHONE)
    bot = await client.get_entity(BOT_USERNAME)
    await client.send_message(bot, message)

    # Esperar respuesta hasta 60 segundos
    print("⏳ Esperando respuesta...")
    deadline = asyncio.get_event_loop().time() + 60
    while asyncio.get_event_loop().time() < deadline:
        remaining = deadline - asyncio.get_event_loop().time()
        msgs = await client.get_messages(bot, limit=3)
        for msg in msgs:
            if msg.out is False:
                print(f"\n📥 Respuesta de LINA:\n{'─'*60}")
                print(msg.text or "(sin texto)")
                print(f"{'─'*60}")
                await client.disconnect()
                return 0
        await asyncio.sleep(2)

    print("\n⚠️ No se recibió respuesta en 60s.")
    await client.disconnect()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))