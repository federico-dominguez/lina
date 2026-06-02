#!/usr/bin/env python3
"""
CLINE → LINA: intro via Telegram.

Se conecta como el user de test, envía un mensaje de presentación a @s_lina_bot,
y muestra la respuesta. Reusa la sesión E2E persistida.
"""

import asyncio
import os
import sys
from pathlib import Path

from telethon import TelegramClient
from telethon.tl.types import Message

API_ID = int(os.environ["TELEGRAM_TEST_API_ID"])
API_HASH = os.environ["TELEGRAM_TEST_API_HASH"]
PHONE = os.environ["TELEGRAM_TEST_PHONE"]
BOT_USERNAME = os.environ.get("LINA_BOT_USERNAME", "@s_lina_bot")

SESSION_DIR = Path(__file__).parent.parent / "tests" / "e2e" / "telegram" / ".sessions"
SESSION_DIR.mkdir(parents=True, exist_ok=True)
SESSION_PATH = str(SESSION_DIR / "lina_e2e")


async def main():
    client = TelegramClient(SESSION_PATH, API_ID, API_HASH)
    client.parse_mode = None

    await client.start(phone=PHONE)
    print("✅ Conectado como", await client.get_me())

    bot = await client.get_entity(BOT_USERNAME)
    print(f"✅ Bot encontrado: {bot.id}")

    # Mensaje de presentación
    intro = (
        "👋 Hola LINA, soy CLINE — el agente de desarrollo que estuvo trabajando "
        "en el repo lina desde VSCode remoto. Acabo de ser invocado por Fede "
        "para presentarme oficialmente.\n\n"
        "Ya revisé el código que dejó Copilot (el Multi-Agent Framework con "
        "lina-orchestrator), y tengo entendido que el plan es que podamos "
        "trabajar juntos — vos desde Telegram, yo desde VSCode, coordinando "
        "via la DB con agent_commands y el trigger NOTIFY.\n\n"
        "¿Cómo estás? ¿Hay algo urgente que deba saber?"
    )
    print(f"\n📤 Enviando mensaje a {BOT_USERNAME}...")
    await client.send_message(bot, intro)
    print("✅ Mensaje enviado.")

    # Esperar respuesta hasta 60 segundos
    print("⏳ Esperando respuesta...")
    deadline = asyncio.get_event_loop().time() + 60
    while asyncio.get_event_loop().time() < deadline:
        remaining = deadline - asyncio.get_event_loop().time()
        msgs = await client.get_messages(bot, limit=3)
        for msg in msgs:
            if msg.out is False:  # mensaje del bot
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