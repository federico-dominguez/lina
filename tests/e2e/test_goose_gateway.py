"""Standalone E2E test for @s_goose_bot."""
import asyncio, os
from telethon import TelegramClient

API_ID = 12663248
API_HASH = "57a7b9ec3cd607e64b73dbae1240af24"
SESSION_FILE = os.path.expanduser("~/lina/tests/e2e/telegram/.sessions/lina_e2e.session")
BOT = "@s_goose_bot"

async def main():
    client = TelegramClient(SESSION_FILE, API_ID, API_HASH)
    await client.start()
    me = await client.get_me()
    print(f"Conectado: {me.first_name} (ID: {me.id})")

    last = await client.get_messages(BOT, limit=1)
    last_id = last[0].id if last else 0
    print(f"Ultimo msg ID: {last_id}")

    msg = await client.send_message(BOT, "decime tu nombre")
    print(f"Enviado (id={msg.id})")

    await asyncio.sleep(8)

    msgs = await client.get_messages(BOT, limit=25)
    new_msgs = sorted([m for m in msgs if m.id > last_id], key=lambda x: x.id)

    spam = 0
    ok = False
    for m in new_msgs:
        t = m.text or ""
        s = "BOT" if not m.out else "USER"
        print(f"ID={m.id} {s}: {t[:120]}")
        if "LINA reiniciada" in t or "reiniciada inesperadamente" in t:
            spam += 1
        if not m.out and len(t) > 15 and "Razonando" not in t and "💭" not in t:
            ok = True

    print()
    print(f"Responde: {'✅' if ok else '❌'}")
    print(f"Spam LINA: {'0 ✅' if spam == 0 else str(spam) + ' ❌'}")

    await client.disconnect()
    return ok and spam == 0

if __name__ == "__main__":
    success = asyncio.run(main())
    exit(0 if success else 1)
