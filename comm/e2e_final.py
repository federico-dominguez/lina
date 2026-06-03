#!/usr/bin/env python3
"""E2E Test: collaborative workflow between Lina, Cline and Goose via Comm."""
import asyncio, sys, os, time, json, re
sys.path.insert(0, "/home/fede/lina/comm")
from pathlib import Path
_PARENT = Path(__file__).resolve().parent
if str(_PARENT) not in sys.path: sys.path.insert(0, str(_PARENT))
_GRANDPARENT = _PARENT.parent
if str(_GRANDPARENT) not in sys.path: sys.path.insert(0, str(_GRANDPARENT))
from send_bot_lib import send_to, _get_group, SESSION, API_ID, API_HASH
from telethon import TelegramClient
from telethon.tl.types import MessageEntityMention

async def test():
    client = TelegramClient(SESSION, API_ID, API_HASH)
    client.parse_mode = None
    await client.start()

    gid, gtitle = await _get_group(client)
    if not gid:
        print("FAIL: No group found")
        await client.disconnect()
        return

    print(f"Grupo: {gtitle}")
    print(f"Enviando: Lina -> pide a Goose que liste containers...")
    
    # Send ONE message to Lina
    await client.send_message(gid,
        "@s_lina_bot pedile a Goose que liste los contenedores Docker activos",
        formatting_entities=[MessageEntityMention(offset=0, length=12)])
    print("✅ Enviado, esperando 50s...")
    
    # Wait 50s for the chain: Lina→send-goose→Goose→send-lina
    await asyncio.sleep(50)
    
    # Read all messages
    msgs = await client.get_messages(gid, limit=50)
    new = [m for m in msgs if not m.out]
    
    lina_ok = goose_ok = False
    for m in reversed(new):
        uid = str(getattr(m.from_id, 'user_id', '') or '')
        txt = (m.text or '')[:200]
        tag = ""
        if any(e.__class__.__name__ == "MessageEntityMention" for e in getattr(m, 'entities', []) or []):
            tag = " [MENTION]"
        
        if uid == "8749962054":
            print(f"  LINA{tag}: {txt}")
            lina_ok = True
        elif uid == "8921012511":
            print(f"  GOOSE{tag}: {txt}")
            goose_ok = True
        elif uid == "8984281604":
            print(f"  CLINE{tag}: {txt}")
        elif uid == "8887121852":
            print(f"  COMM{tag}: {txt}")
    
    print(f"\nResultado: Lina={'✅' if lina_ok else '❌'} Goose={'✅' if goose_ok else '❌'}")
    print(f"OVERALL: {'✅ PASS' if lina_ok and goose_ok else '❌ FAIL'}")
    
    await client.disconnect()

asyncio.run(test())
