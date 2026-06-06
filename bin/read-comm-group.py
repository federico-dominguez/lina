#!/usr/bin/env python3
"""Lee los últimos N mensajes del grupo Comm en Telegram.

Uso:
  python3 read-comm-group.py [N]

  N: cantidad de mensajes a mostrar (default: 10)
"""
import asyncio, sys, os
from telethon import TelegramClient

async def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 10
    os.chdir('/home/fede/lina/comm')
    
    client = TelegramClient('comm_session', 35434942, '9f2a614fbf2e8cbfaf844561b7f43294')
    await client.start()
    
    async for d in client.iter_dialogs():
        if 'Comm' in (d.name or ''):
            group = d
            break
    else:
        print("❌ Grupo Comm no encontrado")
        await client.disconnect()
        return
    
    print(f"📱 Grupo: {group.name}  |  Últimos {n} mensajes\n")
    async for msg in client.iter_messages(group, limit=n):
        sender = f"@{msg.sender.username}" if msg.sender and msg.sender.username else (msg.sender.first_name or str(msg.sender_id))
        text = (msg.text or "")[:500]
        print(f"[{msg.date.strftime('%H:%M:%S')}] {sender}")
        print(f"  {text}\n")
    
    await client.disconnect()

asyncio.run(main())
