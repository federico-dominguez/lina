#!/usr/bin/env python3
"""Send a message to @s_lina_bot via Comm Telegram group."""
import sys
from pathlib import Path
_PARENT = Path(__file__).resolve().parent
sys.path.insert(0, str(_PARENT))
from send_bot_lib import find_group, send_message

msg = " ".join(sys.argv[1:])
if not msg:
    print("Uso: send-lina.py <mensaje>")
    sys.exit(1)

group = find_group("Comm")
if not group:
    print("ERROR: Grupo Comm no encontrado")
    sys.exit(1)

ok, detail = send_message(group, "s_lina_bot", msg)
if ok:
    print(f"✅ Enviado: {detail}")
else:
    print(f"❌ Error: {detail}")
    sys.exit(1)
