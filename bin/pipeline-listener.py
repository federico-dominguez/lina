#!/usr/bin/env python3
"""
pipeline-listener.py — Escucha comandos /pipeline en el grupo "Comms" via Comm.

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

GROUP = None


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
            return d.id, d
    return None, None


def run_pipeline_sync(subcmd, name="", parallel=False):
    """Ejecuta pipeline CLI como subproceso (síncrono)."""
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


async def handle_command(client, gid_entity, text, sender_name):
    global GROUP

    m = re.match(r"^/pipeline\s+(\w+)?\s*(\S+)?\s*(-p|--parallel)?", text.strip())
    if not m:
        return
    subcmd = m.group(1) or "help"
    name = m.group(2) or ""
    parallel = bool(m.group(3))

    log(f"📥 /pipeline {subcmd} {name} (from @{sender_name})")

    if subcmd == "help":
        await client.send_message(gid_entity,
            "📋 Pipeline Manager\nUso:\n  /pipeline list\n  /pipeline run <nombre> [-p]\n  /pipeline info <nombre>\nEj: /pipeline run duo")
        return

    if subcmd in ("list", "info"):
        out, _ = run_pipeline_sync(subcmd, name)
        prefix = "📂 Pipelines:" if subcmd == "list" else f"📋 Info '{name}':"
        await client.send_message(gid_entity, f"{prefix}\n<code>{out}</code>")
        return

    if subcmd == "run":
        if not name:
            await client.send_message(gid_entity, "❌ Usa: /pipeline run <nombre>")
            return
        await client.send_message(gid_entity, f"🚀 Ejecutando pipeline '{name}'...")
        await asyncio.sleep(2)

        # Guardar ID del grupo y cerrar sesión
        gid = gid_entity.id if hasattr(gid_entity, "id") else int(gid_entity)
        await client.disconnect()
        # La sesión de Telethon ahora está libre para send-bot.py

        # Ejecutar pipeline (síncrono, no necesita event loop de Telethon)
        out, code = run_pipeline_sync("run", name, parallel)

        # Reconectar con cliente nuevo y enviar resultado
        new_c = TelegramClient(SESSION, API_ID, API_HASH)
        new_c.parse_mode = None
        await new_c.start()
        try:
            g_e = await new_c.get_entity(gid)
            status = "✅ Completado" if code == 0 else "⚠️ Con errores"
            await new_c.send_message(g_e, f"{status}:\n<code>{out}</code>")
        except Exception as e:
            log(f"⚠️ Error al notificar resultado: {e}")
        await new_c.disconnect()

        # Salir — systemd reinicia el listener automáticamente
        log("🔄 Reiniciando listener...")
        sys.exit(0)

    await client.send_message(gid_entity, f"❌ Comando desconocido: /pipeline {subcmd}")


async def main():
    global GROUP

    client = TelegramClient(SESSION, API_ID, API_HASH)
    client.parse_mode = None
    await client.start()
    log("✅ pipeline-listener conectado")

    gid, gid_entity = await get_group(client)
    if not gid:
        log("❌ No se encontró grupo Comm")
        return
    GROUP = gid
    log(f"👂 Escuchando /pipeline en '{getattr(gid_entity, 'title', '?')}'")

    @client.on(events.NewMessage(chats=GROUP))
    async def handler(event):
        text = (event.message.text or "").strip()
        if not text.startswith("/pipeline"):
            return
        e = await client.get_entity(GROUP)
        sender = await event.get_sender()
        name = getattr(sender, "first_name", "") or getattr(sender, "username", str(sender.id))
        await handle_command(client, e, text, name)

    await client.run_until_disconnected()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log("⏹️ pipeline-listener detenido")
