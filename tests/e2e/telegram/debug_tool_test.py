"""Debug: show captured tool format."""
import asyncio
import logging
logging.basicConfig(level=logging.WARNING)
from client import TelegramTestClient

async def show():
    async with TelegramTestClient(collect_timeout=60, stable_window=3.0) as tg:
        capture = await tg.send_prompt('Ejecutá echo hola mundo')
        print("=== CAPTURED ===")
        for m in capture.messages:
            print(f"\n--- msg_id={m.message_id} edits={m.edit_count} ---")
            print(m.final_text[:400])
            if m.edit_count > 0:
                print("  Edits:")
                for i, (t, txt) in enumerate(m.snapshots):
                    c = txt[:100].replace('\n', ' ')
                    print(f"    [{i}] t={t:.2f}s: {c}")
        print("\n=== CHECK === ✅📥📤")
        for m in capture.messages:
            t = m.final_text
            ck = '✅' in t and t.lstrip().startswith('✅')
            inp = '📥 Input' in t
            out = '📤 Output' in t
            print(f"  msg={m.message_id}: ✅={ck} 📥={inp} 📤={out} edits={m.edit_count}")

asyncio.run(show())
