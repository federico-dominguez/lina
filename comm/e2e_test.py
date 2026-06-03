#!/usr/bin/env python3
"""E2E Test: collaborative workflow between Lina, Cline and Goose via Comm."""
import asyncio, sys, os, time, json, re
sys.path.insert(0, "/home/fede/lina/comm")
from send_bot_lib import send_to, _get_group, SESSION, API_ID, API_HASH
from telethon import TelegramClient
from telethon.tl.types import MessageEntityMention

GROUP_PREFIX = "comm-bots-"

async def test():
    client = TelegramClient(SESSION, API_ID, API_HASH)
    client.parse_mode = None
    await client.start()

    gid, gtitle = await _get_group(client)
    if not gid:
        print("FAIL: No se encontró el grupo Comm")
        await client.disconnect()
        return

    print(f"=== E2E Test: Colaboración Lina ↔ Cline ↔ Goose ===")
    print(f"Grupo: {gtitle} (ID={gid})")
    print()

    # Step 1: Comm sends task to Lina
    print("1️⃣ Comm → Lina: revisar tarjeta NVIDIA")
    await client.send_message(gid,
        "@s_lina_bot revisá la tarjeta nvidia del sistema y decime que GPU tiene",
        formatting_entities=[MessageEntityMention(offset=0, length=12)])
    print("   ✅ Mensaje enviado\n")
    await asyncio.sleep(5)

    # Wait for Lina to process and delegate
    print("⏳ Esperando que Lina delegue a Cline o Goose...")
    await asyncio.sleep(20)

    # Step 2: Check who responded
    msgs = await client.get_messages(gid, limit=30)
    new = [m for m in msgs if not m.out]
    
    # Classify by sender
    results = {"lina": 0, "cline": 0, "goose": 0, "other": 0}
    for m in new:
        uid = str(getattr(m.from_id, 'user_id', '') or '')
        txt = (m.text or '')[:150]
        if uid == "8749962054":
            results["lina"] += 1
            print(f"   🤖 LINA: {txt}")
        elif uid == "8984281604":
            results["cline"] += 1
            print(f"   👨‍💻 CLINE: {txt}")
        elif uid == "8921012511":
            results["goose"] += 1
            print(f"   🦢 GOOSE: {txt}")
        else:
            results["other"] += 1
            print(f"   👤 {uid}: {txt}")

    print(f"\n📊 Mensajes: LINA={results['lina']} Cline={results['cline']} Goose={results['goose']}")
    
    print("\n✅ Test completado")

    # Cleanup: leave group
    await asyncio.sleep(2)
    await client.disconnect()

asyncio.run(test())
