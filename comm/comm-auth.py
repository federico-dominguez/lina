#!/usr/bin/env python3
"""
comm-auth.py — Autoriza la cuenta Comm (59891992356) en Telethon.

Uso:
    python3 comm-auth.py                    # Envía código al teléfono
    python3 comm-auth.py <codigo>           # Completa la autenticación
"""

import asyncio
import sys
import json
import os
from pathlib import Path
from telethon import TelegramClient

API_ID = 35434942
API_HASH = "9f2a614fbf2e8cbfaf844561b7f43294"
SESSION = str(Path(__file__).parent / "comm_session")
PHONE = "+59891992356"
HASH_FILE = str(Path(__file__).parent / ".comm_hash")


async def main():
    client = TelegramClient(SESSION, API_ID, API_HASH)
    client.parse_mode = None
    await client.connect()

    if await client.is_user_authorized():
        me = await client.get_me()
        print(f"AUTORIZADO: {me.first_name} (ID={me.id})")
        await client.disconnect()
        return

    if len(sys.argv) < 2:
        # Step 1: send code
        sent = await client.send_code_request(PHONE)
        with open(HASH_FILE, "w") as f:
            f.write(sent.phone_code_hash)
        print(f"CODIGO_ENVIADO a {PHONE}")
        print("Ejecutá: python3 comm-auth.py <codigo>")
        await client.disconnect()
        return

    # Step 2: sign in with code
    code = sys.argv[1].strip()
    phone_code_hash = ""
    try:
        phone_code_hash = Path(HASH_FILE).read_text().strip()
    except FileNotFoundError:
        pass

    try:
        await client.sign_in(PHONE, code, phone_code_hash=phone_code_hash)
        me = await client.get_me()
        print(f"AUTORIZADO: {me.first_name} (ID={me.id})")
    except Exception as e:
        if "phone code" in str(e).lower() and not phone_code_hash:
            # Re-send and try again
            sent = await client.send_code_request(PHONE)
            phone_code_hash = sent.phone_code_hash
            print(f"ERROR: {e}")
            print("(El código expiró. Reenviado. Ejecutá de nuevo con el nuevo código)")
            with open(HASH_FILE, "w") as f:
                f.write(phone_code_hash)
        else:
            print(f"ERROR: {e}")

    if Path(HASH_FILE).exists():
        Path(HASH_FILE).unlink()
    await client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
