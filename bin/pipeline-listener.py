#!/usr/bin/env python3
"""
pipeline-listener.py — Escucha comandos /pipeline en el grupo "Comms".

Arquitectura:
  - Listener usa la sesión Telethon normal (comm_session)
  - Para /pipeline run: copia la sesión a comm_session_pipeline y lanza
    la pipeline como proceso separado con esa copia. El listener no se
    desconecta y sigue escuchando comandos.
"""

import asyncio, os, re, shutil, subprocess, sys, time
from pathlib import Path

from telethon import TelegramClient, events
from telethon.tl.functions.messages import GetDialogsRequest
from telethon.tl.types import InputPeerEmpty

BASE = Path(__file__).resolve().parent.parent
BIN_PIPELINE = BASE / "bin" / "pipeline"
COMM_DIR = BASE / "comm"
SESSION = str(COMM_DIR / "comm_session")
SESSION_PIPELINE = SESSION + "_pipeline"
API_ID = 12663248
API_HASH = "57a7b9ec3cd607e64b73dbae1240af24"

GROUP_ID = None


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


async def get_group_id(client):
    dialogs = await client(GetDialogsRequest(
        offset_date=None, offset_id=0, offset_peer=InputPeerEmpty(),
        limit=200, hash=0,
    ))
    for d in dialogs.chats:
        title = getattr(d, "title", "") or ""
        if "Comm" in title:
            return d.id
    return None


def copy_session_for_pipeline():
    """Copia la sesión activa para que la pipeline la use."""
    session_file = SESSION + ".session"
    pipeline_file = SESSION_PIPELINE + ".session"
    if os.path.exists(session_file):
        shutil.copy2(session_file, pipeline_file)
        log(f"📋 Sesión copiada para pipeline: {pipeline_file}")
    else:
        log("⚠️ No se encontró archivo de sesión para copiar")


async def main():
    global GROUP_ID

    client = TelegramClient(SESSION, API_ID, API_HASH)
    client.parse_mode = None
    await client.start()
    log("✅ pipeline-listener conectado")

    GROUP_ID = await get_group_id(client)
    if not GROUP_ID:
        log("❌ No se encontró grupo Comm")
        return
    log(f"👂 Escuchando /pipeline en grupo #{GROUP_ID}")

    @client.on(events.NewMessage(chats=GROUP_ID))
    async def handler(event):
        text = (event.message.text or "").strip()
        if not text.startswith("/pipeline"):
            return

        sender = await event.get_sender()
        name = getattr(sender, "first_name", "") or getattr(sender, "username", str(sender.id))

        m = re.match(r"^/pipeline\s+(\w+)?\s*(\S+)?\s*(-p|--parallel)?", text)
        if not m:
            await client.send_message(GROUP_ID, "❌ Usa: /pipeline list | run <nombre> [-p] | info <nombre>")
            return

        subcmd = m.group(1) or "help"
        name_arg = m.group(2) or ""
        parallel = bool(m.group(3))

        log(f"📥 /pipeline {subcmd} {name_arg} (from @{name})")

        # ── help ──────────────────────────────────────────────────
        if subcmd in ("help", ""):
            await client.send_message(GROUP_ID, "📋 Pipeline Manager\nUso:\n  /pipeline list\n  /pipeline run <nombre> [-p]\n  /pipeline info <nombre>\nEj: /pipeline run duo")
            return

        # ── list / info ────────────────────────────────────────────
        if subcmd in ("list", "info"):
            try:
                r = subprocess.run(
                    [sys.executable, str(BIN_PIPELINE), subcmd, name_arg],
                    capture_output=True, text=True, timeout=15,
                )
                out = (r.stdout + r.stderr).strip()[:1500]
            except Exception as e:
                out = str(e)
            prefix = "📂 Pipelines:" if subcmd == "list" else f"📋 Info '{name_arg}':"
            await client.send_message(GROUP_ID, f"{prefix}\n<code>{out}</code>")
            return

        # ── run ────────────────────────────────────────────────────
        if subcmd == "run":
            if not name_arg:
                await client.send_message(GROUP_ID, "❌ Usa: /pipeline run <nombre>")
                return

            await client.send_message(GROUP_ID, f"🚀 Ejecutando pipeline '{name_arg}'...")

            # Copiar sesión para la pipeline
            copy_session_for_pipeline()

            # Lanzar pipeline como proceso separado con su propia sesión
            cmd = [sys.executable, str(BIN_PIPELINE), "run", name_arg]
            if parallel:
                cmd.append("--parallel")

            env = os.environ.copy()
            env["COMM_SESSION"] = SESSION_PIPELINE  # send_bot_lib.py lee esta variable

            proc = subprocess.Popen(
                cmd, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            log(f"🚀 Pipeline lanzada (PID={proc.pid})")

            # No esperamos — el listener sigue escuchando
            return

    await client.run_until_disconnected()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log("⏹️ pipeline-listener detenido")
