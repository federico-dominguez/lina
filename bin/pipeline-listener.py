#!/usr/bin/env python3
"""
pipeline-listener.py — Escucha comandos /pipeline en el grupo "Comms" via Comm.

Corre como daemon systemd. Cuando alguien escribe /pipeline en el grupo,
ejecuta el comando y responde.

Uso directo:
  python3 bin/pipeline-listener.py

Uso daemon:
  systemctl --user start lina-pipeline-listener.service
"""

import asyncio, os, re, subprocess, sys, time
from pathlib import Path

from telethon import TelegramClient, events
from telethon.tl.functions.messages import GetDialogsRequest
from telethon.tl.types import InputPeerEmpty

BASE = Path(__file__).resolve().parent.parent
BIN_PIPELINE = BASE / "bin" / "pipeline"
COMM_DIR = BASE / "comm"
SESSION = str(COMM_DIR / "comm_session")
API_ID = 12663248
API_HASH = "57a7b9ec3cd607e64b73dbae1240af24"

PIPELINE_PATTERN = re.compile(r"^/pipeline\s+(\w+)?\s*(\S+)?\s*(-p|--parallel)?")


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


async def get_group(client):
    dialogs = await client(GetDialogsRequest(
        offset_date=None, offset_id=0, offset_peer=InputPeerEmpty(),
        limit=200, hash=0,
    ))
    for d in dialogs.chats:
        title = getattr(d, "title", "") or ""
        if "Comm" in title:
            return d
    return None


async def run_pipeline(subcmd, name="", parallel=False):
    cmd = [sys.executable, str(BIN_PIPELINE), subcmd]
    if name:
        cmd.append(name)
    if parallel:
        cmd.append("--parallel")
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        out = (r.stdout + r.stderr).strip()
        if len(out) > 2000:
            out = out[:1000] + "\n... (truncated) ...\n" + out[-900:]
        return out, r.returncode
    except subprocess.TimeoutExpired:
        return "Timeout 5min", 1
    except Exception as e:
        return f"Error: {e}", 1


async def handle_command(client, group, text, sender_name):
    m = PIPELINE_PATTERN.match(text.strip())
    if not m:
        return
    subcmd = m.group(1) or "help"
    name = m.group(2) or ""
    parallel = bool(m.group(3))

    log(f"📥 /pipeline {subcmd} {name} (from @{sender_name})")

    if subcmd == "help":
        await client.send_message(group,
            "📋 Pipeline Manager\n"
            "Uso:\n"
            "  /pipeline list          — listar\n"
            "  /pipeline run <nombre>  — ejecutar\n"
            "  /pipeline run <nombre> -p — paralelo\n"
            "  /pipeline info <nombre> — info\n"
            "Ej: /pipeline run duo"
        )
        return

    if subcmd == "list":
        out, _ = await run_pipeline("list")
        await client.send_message(group, f"📂 Pipelines:\n<code>{out}</code>")
        return

    if subcmd == "run":
        if not name:
            await client.send_message(group, "❌ Usa: /pipeline run <nombre>")
            return
        await client.send_message(group, f"🚀 Ejecutando pipeline '{name}'...")
        out, code = await run_pipeline("run", name, parallel)
        status = "✅ Completado" if code == 0 else "⚠️ Con errores"
        await client.send_message(group, f"{status}:\n<code>{out}</code>")
        return

    if subcmd == "info":
        if not name:
            await client.send_message(group, "❌ Usa: /pipeline info <nombre>")
            return
        out, _ = await run_pipeline("info", name)
        await client.send_message(group, f"📋 Info:\n<code>{out}</code>")
        return

    await client.send_message(group, f"❌ Comando desconocido: /pipeline {subcmd}")


async def main():
    client = TelegramClient(SESSION, API_ID, API_HASH)
    client.parse_mode = None
    await client.start()
    log("✅ pipeline-listener conectado")

    group = await get_group(client)
    if not group:
        log("❌ No se encontró grupo Comm")
        return

    log(f"👂 Escuchando /pipeline en '{getattr(group, 'title', '?')}'")

    @client.on(events.NewMessage(chats=group.id))
    async def handler(event):
        text = (event.message.text or "").strip()
        if not text.startswith("/pipeline"):
            return
        sender = await event.get_sender()
        name = getattr(sender, "first_name", "") or getattr(sender, "username", str(sender.id))
        await handle_command(client, group, text, name)

    await client.run_until_disconnected()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log("⏹️ pipeline-listener detenido")
