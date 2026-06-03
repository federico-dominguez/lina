#!/usr/bin/env python3
"""E2E Test: Lina delegates to Goose (cross-bot collaboration)."""
import asyncio, sys
sys.path.insert(0, "/home/fede/lina/comm")
from send_bot_lib import send_to, _get_group, SESSION, API_ID, API_HASH
from telethon import TelegramClient
from telethon.tl.types import MessageEntityMention

async def test():
    client = TelegramClient(SESSION, API_ID, API_HASH)
    client.parse_mode = None
    await client.start()
    gid, gtitle = await _get_group(client)
    if not gid: print("FAIL: No group"); return

    print(f"=== E2E: Lina delega en Cline, Cline delega en Goose ===\n")

    # Step 1: Comm asks Lina to check Docker containers
    print("1️⃣ Comm → Lina: pedirle a Goose que liste containers Docker")
    await client.send_message(gid, 
        "@s_lina_bot pedile a Goose que liste los contenedores Docker activos y te diga cuantos hay",
        formatting_entities=[MessageEntityMention(offset=0, length=12)])
    print("   ✅ Mensaje enviado\n")

    # Wait for chain: Lina→send-goose→Goose→send-lina→Lina
    for i in range(60):
        await asyncio.sleep(5)
        msgs = await client.get_messages(gid, limit=30)
        # Check all new msgs
        all_users = set()
        for m in msgs:
            uid = str(getattr(m.from_id, 'user_id', '') or '')
            if uid in ("8749962054", "8984281604", "8921012511"):
                all_users.add(uid)
        
        goose_active = "8921012511" in all_users
        cline_active = "8984281604" in all_users
        lina_active = "8749962054" in all_users
        
        status = f"[{i*5}s] Lina={'✅' if lina_active else '⏳'} Cline={'✅' if cline_active else '⏳'} Goose={'✅' if goose_active else '⏳'}"
        print(f"   {status}")
        
        # Show recent messages
        for m in msgs[:5]:
            uid = str(getattr(m.from_id, 'user_id', '') or '')
            if uid in ("8749962054", "8984281604", "8921012511"):
                txt = (m.text or '')[:120]
                who = {"8749962054":"LINA","8984281604":"CLINE","8921012511":"GOOSE"}.get(uid, "?")
                print(f"     {who}: {txt}")
        
        # If Goose and Lina both responded, finished
        if goose_active and lina_active and i > 3:
            print(f"\n✅ Goose responded! Checking full chain...")
            break

    # Print full chain
    msgs = await client.get_messages(gid, limit=50)
    new = [m for m in msgs if not m.out]
    print(f"\n📋 Mensajes del grupo:")
    for m in reversed(new):
        uid = str(getattr(m.from_id, 'user_id', '') or '')
        txt = (m.text or '')[:150]
        who = {"8749962054":"LINA","8984281604":"CLINE","8921012511":"GOOSE","8887121852":"COMM"}
        name = who.get(uid, uid[:8])
        is_mention = "MENTION" if any(e.__class__.__name__ == "MessageEntityMention" for e in getattr(m, 'entities', []) if e) else ""
        print(f"   [{name}] {is_mention} {txt}")

    await client.disconnect()

asyncio.run(test())
