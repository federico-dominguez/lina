import asyncio, os

os.environ['TELEGRAM_TEST_API_ID'] = '35434942'
os.environ['TELEGRAM_TEST_API_HASH'] = '9f2a614fbf2e8cbfaf844561b7f43294'
os.environ['TELEGRAM_TEST_PHONE'] = '+59891675877'

from e2e.telegram.client import TelegramTestClient

async def test_bot(name, username):
    print()
    print(f'[TEST] {name} ({username})...', flush=True)
    os.environ['LINA_BOT_USERNAME'] = username
    try:
        async with TelegramTestClient(collect_timeout=45, stable_window=2.0) as tg:
            capture = await tg.send_prompt('responde solo: OK')
            msgs = [m.final_text[:200] for m in capture.messages]
            print(f'   OK {name}: {len(msgs)} msg(s)', flush=True)
            for m in msgs:
                print(f'   -> {m}', flush=True)
            return True
    except Exception as e:
        print(f'   FAIL {name}: {e}', flush=True)
        return False

async def main():
    r = {}
    r['LINA'] = await test_bot('LINA', '@s_lina_bot')
    r['Cline'] = await test_bot('Cline', '@s_cline_bot')
    print()
    print('=== RESUMEN ===', flush=True)
    for n, ok in r.items():
        print(f'  {n}: {"OK" if ok else "FALLO"}', flush=True)

asyncio.run(main())
