import asyncio
from telethon import TelegramClient
from pathlib import Path
async def main():
    client = TelegramClient(str(Path('/home/fede/lina/comm/comm_session')), 12663248, '57a7b9ec3cd607e64b73dbae1240af24')
    await client.start()
    dialogs = await client.get_dialogs()
    for d in dialogs:
        if 'Comm' in (getattr(d.entity, 'title', '') or ''):
            await client.send_message(d.entity, '/pipelines')
            print('✅ /pipelines sent')
            break
    await client.disconnect()
asyncio.run(main())
