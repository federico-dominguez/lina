#!/usr/bin/env python3
"""
pipeline-listener.py — Escucha comandos /pipeline en el grupo "Comms" via Comm.

El listener se detiene a sí mismo durante la ejecución de pipelines para
liberar la sesión de Telethon. Systemd lo reinicia automáticamente.

Uso:
  python3 bin/pipeline-listener.py

Daemon:
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

GROUP_ID = None


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


async def get_group_id(client):
    """Obtiene el ID numérico del grupo 'Comms'."""
    dialogs = await client(GetDialogsRequest(
        offset_date=None, offset_id=0, offset_peer=InputPeerEmpty(),
        limit=200, hash=0,
    ))
    for d in dialogs.chats:
        title = getattr(d, "title", "") or ""
        if "Comm" in title:
            return d.id
    return None


def run_sync(subcmd, name="", parallel=False):
    """Ejecuta el CLI pipeline como subproceso (síncrono)."""
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


async def send_msg(client, text):
    """Envía un mensaje al grupo."""
    if GROUP_ID and client:
        try:
            entity = await client.get_entity(GROUP_ID)
            await client.send_message(entity, text)
        except Exception as e:
            log(f"⚠️ Error send_msg: {e}")


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
            await send_msg(client, "❌ Usa: /pipeline list | run <nombre> [-p] | info <nombre>")
            return

        subcmd = m.group(1) or "help"
        name_arg = m.group(2) or ""
        parallel = bool(m.group(3))

        log(f"📥 /pipeline {subcmd} {name_arg} (from @{name})")

        # ── help ────────────────────────────────────────
        if subcmd in ("help", ""):
            await send_msg(client, "📋 Pipeline Manager\nUso:\n  /pipeline list\n  /pipeline run <nombre> [-p]\n  /pipeline info <nombre>\nEj: /pipeline run duo")
            return

        # ── list / info (rápidos, no necesitan Telethon en subproceso) ──
        if subcmd in ("list", "info"):
            out, _ = run_sync(subcmd, name_arg)
            prefix = "📂 Pipelines:" if subcmd == "list" else f"📋 Info '{name_arg}':"
            await send_msg(client, f"{prefix}\n<code>{out}</code>")
            return

        # ── run ─────────────────────────────────────────
        if subcmd == "run":
            if not name_arg:
                await send_msg(client, "❌ Usa: /pipeline run <nombre>")
                return

            await send_msg(client, f"🚀 Ejecutando pipeline '{name_arg}'...")
            await asyncio.sleep(1)

            # ── Liberar Telethon para el subproceso ──────
            await client.disconnect()
            log("🔌 Telethon desconectado, ejecutando subproceso...")

            # Ejecutar pipeline (usa la sesión liberada)
            out, code = run_sync("run", name_arg, parallel)

            # El subproceso ya envió sus propias notificaciones al grupo
            # (Lina y Cline respondieron + Comm notificó por el pipeline).
            # No reconectamos — systemd reinicia el listener.
            log(f"🏁 Subproceso completado (exit={code})")
            log("🔄 Saliendo para que systemd reinicie el listener...")
            sys.exit(0)

        await send_msg(client, f"❌ Comando /pipeline {subcmd} desconocido")

    await client.run_until_disconnected()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log("⏹️ pipeline-listener detenido")
