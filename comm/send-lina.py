#!/usr/bin/env python3
"""Send a message to @s_lina_bot via Comm in the group."""
import asyncio, sys
from pathlib import Path
_PARENT = Path(__file__).resolve().parent
if str(_PARENT) not in sys.path: sys.path.insert(0, str(_PARENT))
_GRANDPARENT = _PARENT.parent
if str(_GRANDPARENT) not in sys.path: sys.path.insert(0, str(_GRANDPARENT))
from send_bot_lib import send_to
async def _main():
    msg = " ".join(sys.argv[1:])
    if not msg: print("Uso: send-lina.py <mensaje>"); return
    ok = await send_to("lina", msg)
    sys.exit(0 if ok else 1)
asyncio.run(_main())
