#!/usr/bin/env python3
"""
pipeline-listener.py — Escucha comandos de Pipeline Manager en el grupo "Comms".

Soporta comandos de BotFather:
  /pipelines     → /pipeline list
  /run <nombre>  → /pipeline run <nombre>
  /new ...       → /pipeline new ...
  /status        → /pipeline list
  /cancel        → /pipeline cancel
  /help          → ayuda

Arquitectura:
  - Listener usa sesión Telethon (comm_session)
  - Para /pipeline run: copia sesión a comm_session_pipeline y lanza subproceso
  - El listener nunca se desconecta
"""

import asyncio, os, re, shutil, subprocess, sys, time
from pathlib import Path

import yaml
from telethon import TelegramClient, events
from telethon.tl.functions.messages import GetDialogsRequest
from telethon.tl.types import InputPeerEmpty
from telethon.tl.custom import Button

BASE = Path(__file__).resolve().parent.parent
BIN_PIPELINE = BASE / "bin" / "pipeline"
COMM_DIR = BASE / "comm"
SESSION = str(COMM_DIR / "comm_session")
SESSION_PIPELINE = SESSION + "_pipeline"
YAML_DIR = BASE / "config" / "pipelines"
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
    sf = SESSION + ".session"
    pf = SESSION_PIPELINE + ".session"
    if os.path.exists(sf):
        shutil.copy2(sf, pf)
        return True
    return False


# ─── Inline keyboards ─────────────────────────────────────────────
async def send_pipeline_menu(client, chat, edit_msg=None):
    """Muestra menu con pipelines como botones."""
    yamls = sorted([f.stem for f in YAML_DIR.glob("*.yaml") if f.stem != "template"])
    if not yamls:
        txt = "📂 No hay pipelines. Crea uno con /new"
        if edit_msg: await client.edit_message(chat, edit_msg, txt)
        else: await client.send_message(chat, txt)
        return
    rows = [[Button.inline("📂 " + y, data="menu_" + y) for y in yamls[i:i+2]] for i in range(0, len(yamls), 2)]
    rows.append([Button.inline("➕ Crear nuevo", data="action_new"), Button.inline("🔄 Refrescar", data="action_refresh")])
    txt = f"📋 Pipeline Manager — {len(yamls)} pipeline(s)"
    if edit_msg: await client.edit_message(chat, edit_msg, txt, buttons=rows)
    else: await client.send_message(chat, txt, buttons=rows)

async def send_pipeline_actions(client, chat, edit_msg, pname):
    """Muestra acciones para un pipeline."""
    rows = [
        [Button.inline("▶️ Ejecutar", data="run_" + pname), Button.inline("⚡ Paralelo", data="runp_" + pname)],
        [Button.inline("ℹ️ Info", data="info_" + pname), Button.inline("🗑️ Eliminar", data="del_" + pname)],
        [Button.inline("🔙 Volver", data="action_menu")],
    ]
    await client.edit_message(chat, edit_msg, f"📋 Pipeline: {pname}", buttons=rows)

async def send_confirm_delete(client, chat, edit_msg, pname):
    rows = [[Button.inline("✅ Sí", data="confirm_del_" + pname)], [Button.inline("❌ No", data="menu_" + pname)]]
    await client.edit_message(chat, edit_msg, f"🗑️ Eliminar '{pname}'?", buttons=rows)


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
        if not text.startswith("/"):
            return

        # ── Normalizar comandos raíz (BotFather) ──────────────────
        normalized = text
        if text == "/pipelines" or text.startswith("/pipelines "):
            normalized = "/pipeline list"
        elif text == "/run" or text.startswith("/run "):
            rest = text[4:].strip()
            normalized = f"/pipeline run {rest}" if rest else "/pipeline run"
        elif text == "/new" or text.startswith("/new "):
            rest = text[4:].strip()
            normalized = f"/pipeline new {rest}" if rest else "/pipeline new"
        elif text == "/status" or text.startswith("/status"):
            normalized = "/pipeline list"
        elif text == "/cancel" or text.startswith("/cancel"):
            normalized = "/pipeline cancel"
        elif text in ("/help", "/start") or text.startswith("/help "):
            normalized = "/pipeline help"
        elif not text.startswith("/pipeline"):
            return
        text = normalized

        sender = await event.get_sender()
        name = getattr(sender, "first_name", "") or getattr(sender, "username", str(sender.id))

        m = re.match(r"^/pipeline\s+(\w+)?\s*(\S+)?\s*(-p|--parallel)?", text)
        if not m:
            await client.send_message(GROUP_ID, "❌ Usa: /pipeline list | run <nombre> [-p] | info <nombre> | new <nombre> pasos...")
            return

        subcmd = m.group(1) or "help"
        name_arg = m.group(2) or ""
        parallel = bool(m.group(3))

        log(f"📥 /pipeline {subcmd} {name_arg} (from @{name})")

        # ── help ──────────────────────────────────────────────────
        if subcmd in ("help", ""):
            msg = "🤖 Pipeline Manager\n\nComandos:\n"
            msg += "  /pipelines   — Listar pipelines\n"
            msg += "  /run <n>     — Ejecutar pipeline\n"
            msg += "  /run <n> -p  — En paralelo\n"
            msg += "  /new <n> ... — Crear pipeline\n"
            msg += "  /status      — Estado de pipelines\n"
            msg += "  /cancel      — Cancelar\n\n"
            msg += "Ej: /run duo -p\n"
            msg += "   /new moodle step1:lina -> revisa, step2:cline -> check\n"
            msg += "   pipeline:name -> miPipe"
            await client.send_message(GROUP_ID, msg)
            return

        # ── list (menu con botones) / info ─────────────────────────
        if subcmd == "list":
            await send_pipeline_menu(client, GROUP_ID)
            return
        if subcmd == "info":
            try:
                r = subprocess.run([sys.executable, str(BIN_PIPELINE), "info", name_arg],
                    capture_output=True, text=True, timeout=15)
                out = (r.stdout + r.stderr).strip()[:1500]
            except Exception as e:
                out = str(e)
            await client.send_message(GROUP_ID, f"📋 Info '{name_arg}':\n<code>{out}</code>")
            return

        # ── new ────────────────────────────────────────────────────
        if subcmd == "new":
            parts = text.split(None, 2)
            if len(parts) < 3:
                await client.send_message(GROUP_ID, "❌ Usa: /new <nombre> step1:lina -> mensaje, step2:cline -> msg, pipeline:name -> Nombre")
                return

            pipe_name = parts[1].strip()
            steps = []

            step_matches = re.findall(
                r'step\d+\s*:\s*(\w+)\s*-\s*>\s*(.+?)(?=,\s*step\d+\s*:|,\s*pipeline\s*:|$)',
                text, re.IGNORECASE | re.DOTALL
            )

            for bot, msg in step_matches:
                bot = bot.strip().lower()
                msg = msg.strip()
                if bot and msg:
                    steps.append({"bot": bot, "msg": msg})

            name_match = re.search(r'pipeline\s*:\s*name\s*-\s*>\s*(.+?)(?:,|$)', text, re.IGNORECASE)
            if name_match:
                custom_name = name_match.group(1).strip()
                if custom_name:
                    pipe_name = custom_name

            if not steps:
                await client.send_message(GROUP_ID, "❌ No se parsearon pasos.\nFormato: /new moodle step1:lina -> revisa, step2:cline -> verifica")
                return

            yaml_path = YAML_DIR / f"{pipe_name}.yaml"
            data = {
                "_name": pipe_name,
                "description": f"Creado via Telegram: {len(steps)} paso(s)",
                "steps": [
                    {"bot": s["bot"], "msg": s["msg"], "notify": f"✅ {s['bot'].title()} completo."}
                    for s in steps
                ]
            }
            yaml_path.parent.mkdir(parents=True, exist_ok=True)
            with open(yaml_path, "w") as f:
                yaml.dump(data, f, allow_unicode=True, default_flow_style=False, sort_keys=False)

            summary = f"✅ Pipeline '{pipe_name}' creado ({len(steps)} pasos):\n"
            for i, s in enumerate(steps, 1):
                summary += f"  {i}. @{s['bot']}: {s['msg'][:60]}...\n"
            summary += f"\nEjecutar: /run {pipe_name}"
            await client.send_message(GROUP_ID, summary)
            log(f"✅ Pipeline '{pipe_name}' creado via Telegram ({len(steps)} pasos)")
            return

        # ── run ────────────────────────────────────────────────────
        if subcmd == "run":
            if not name_arg:
                await client.send_message(GROUP_ID, "❌ Usa: /run <nombre> [-p]\nPipelines:\n" +
                    "\n".join(f"  - {f.stem}" for f in sorted(YAML_DIR.glob("*.yaml"))))
                return

            await client.send_message(GROUP_ID, f"🚀 Ejecutando pipeline '{name_arg}'...")
            copy_session_for_pipeline()

            cmd = [sys.executable, str(BIN_PIPELINE), "run", name_arg]
            if parallel:
                cmd.append("--parallel")

            env = os.environ.copy()
            env["COMM_SESSION"] = SESSION_PIPELINE

            subprocess.Popen(cmd, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
            log(f"🚀 Pipeline '{name_arg}' lanzada")
            return

        await client.send_message(GROUP_ID, f"❌ Comando /pipeline {subcmd} desconocido")

    # ── CallbackQuery handler (botones inline) ──────────────────────
    @client.on(events.CallbackQuery)
    async def callback(event):
        data = (event.data or b"").decode()
        chat = event.chat_id
        msg_id = event.message_id
        sender = await event.get_sender()
        sname = getattr(sender, "first_name", "") or getattr(sender, "username", str(sender.id))
        log(f"🔘 {data} (from @{sname})")

        # Menu principal
        if data in ("action_menu", "action_refresh"):
            if data == "action_refresh": await event.answer("🔄")
            await send_pipeline_menu(client, chat, msg_id)
            return

        if data == "action_new":
            await event.answer("📝 Usa /new")
            await client.edit_message(chat, msg_id, "✨ Usa: /new <nombre> step1:lina -> msg, step2:cline -> msg2")
            return

        # Menu de pipeline
        if data.startswith("menu_"):
            await send_pipeline_actions(client, chat, msg_id, data[5:])
            return

        # Run
        if data.startswith("run_") or data.startswith("runp_"):
            parallel = data.startswith("runp_")
            pname = data[5:] if parallel else data[4:]
            await event.answer("🚀")
            await client.edit_message(chat, msg_id, f"🚀 {pname}...")
            copy_session_for_pipeline()
            cmd = [sys.executable, str(BIN_PIPELINE), "run", pname]
            if parallel: cmd.append("--parallel")
            env = os.environ.copy()
            env["COMM_SESSION"] = SESSION_PIPELINE
            subprocess.Popen(cmd, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
            log(f"🚀 '{pname}' desde boton")
            return

        # Info
        if data.startswith("info_"):
            pname = data[5:]
            try:
                r = subprocess.run([sys.executable, str(BIN_PIPELINE), "info", pname], capture_output=True, text=True, timeout=15)
                out = (r.stdout + r.stderr).strip()[:1000]
            except: out = "Error"
            await client.edit_message(chat, msg_id, f"📋 {pname}:\n<code>{out}</code>")
            return

        # Delete
        if data.startswith("del_"):
            await send_confirm_delete(client, chat, msg_id, data[4:])
            return

        if data.startswith("confirm_del_"):
            pname = data[12:]
            yaml_path = YAML_DIR / f"{pname}.yaml"
            if yaml_path.exists():
                yaml_path.unlink()
                await client.edit_message(chat, msg_id, f"🗑️ '{pname}' eliminado.")
                log(f"🗑️ '{pname}' eliminado")
            else:
                await event.answer("⚠️ No encontrado")
                return
            await send_pipeline_menu(client, chat, msg_id)
            return

        await event.answer("❌")

    await client.run_until_disconnected()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log("⏹️ pipeline-listener detenido")
