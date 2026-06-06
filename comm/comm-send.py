#!/usr/bin/env python3
"""
comm-send — Envía un mensaje a otro bot vía DB + Comm Telegram.

Uso:
  python3 comm-send.py <destino> <mensaje> [--sender <bot>]

  <destino>: goose | lina | cline | gemma | fede | todos
  <mensaje>: texto del mensaje
  --sender:  override del bot que envía (default: auto-detect)

Ejemplo:
  python3 comm-send.py lina "Hola! Necesito tu ayuda"
  python3 comm-send.py --sender lina goose "Hola! Soy LINA"

El mensaje se guarda en comm_messages con status='sent'.
comm-svc lo detecta y lo envía al grupo Comm via Telegram.

Variables de entorno:
  LINA_DB_URL  — default: postgresql://lina:lina_dev@localhost:5432/lina
  COMM_SENDER  — quién envía (default: detecta por hostname)
"""

import asyncio
import os
import socket
import sys

import asyncpg

DB_URL = os.environ.get(
    "LINA_DB_URL",
    "postgresql://lina:lina_dev@localhost:5432/lina"
)

# Auto-detectar sender por hostname
HOSTNAME = socket.gethostname().lower()
SENDER_MAP = {
    "goose": "goose",
    "lina": "lina",
    "cline": "cline",
    "gemma": "gemma",
}
SENDER = os.environ.get("COMM_SENDER") or SENDER_MAP.get(HOSTNAME, "goose")

VALID_DEST = {"goose", "lina", "cline", "gemma", "fede", "todos"}


async def send(destination: str, message: str):
    if destination not in VALID_DEST:
        print(f"❌ Destino inválido: {destination}")
        print(f"   Válidos: {', '.join(sorted(VALID_DEST))}")
        return False

    conn = await asyncpg.connect(DB_URL, timeout=10)
    try:
        row = await conn.fetchrow(
            "INSERT INTO comm_messages (sender, destination, message) "
            "VALUES ($1, $2, $3) RETURNING id",
            SENDER, destination, message
        )
        msg_id = row["id"]
        preview = message[:120] + ("..." if len(message) > 120 else "")
        print(f"✅ Mensaje #{msg_id} enviado: {SENDER} → {destination}")
        print(f'   "{preview}"')
        print("   ⏳ comm-svc lo entregará en breve...")
        return True
    except Exception as e:
        print(f"❌ Error: {e}")
        return False
    finally:
        await conn.close()


def main():
    """Parse args: comm-send.py [--sender <bot>] <destination> <message>"""
    sender = None
    positional = []
    i = 1
    while i < len(sys.argv):
        if sys.argv[i] == "--sender" and i + 1 < len(sys.argv):
            sender = sys.argv[i + 1].lower()
            i += 2
        elif sys.argv[i].startswith("--"):
            i += 1
        else:
            positional.append(sys.argv[i])
            i += 1

    if len(positional) < 1:
        print(__doc__)
        sys.exit(1)

    destination = positional[0].lower()
    message = " ".join(positional[1:])

    if sender:
        global SENDER
        SENDER = sender

    ok = asyncio.run(send(destination, message))
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
