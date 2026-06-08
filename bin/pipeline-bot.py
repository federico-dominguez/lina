#!/usr/bin/env python3
"""
pipeline-bot.py — Bot Telegram dedicado a Pipeline Manager (@s_pipelines_bot).

Incluye wizard interactivo para /new y /pipelines con botones inline.
"""

import asyncio, json, os, re, subprocess, sys, time
from pathlib import Path

import httpx
import yaml

BASE = Path(__file__).resolve().parent.parent
BIN_PIPELINE = BASE / "bin" / "pipeline"
YAML_DIR = BASE / "config" / "pipelines"

TOKEN = "8993390190:AAFAE8uTDbavC4lhrXdoORxaEdnJcgXKGHg"
API = f"https://api.telegram.org/bot{TOKEN}"
LAST_UPDATE = 0

COMMANDS = [
    {"command": "pipelines", "description": "📋 Listar y gestionar pipelines"},
    {"command": "run", "description": "🚀 Ejecutar pipeline guardado"},
    {"command": "new", "description": "✨ Crear pipeline paso a paso"},
    {"command": "status", "description": "📊 Estado de pipelines activos"},
    {"command": "cancel", "description": "🛑 Cancelar pipeline en ejecución"},
    {"command": "help", "description": "❓ Ayuda de Pipeline Manager"},
]

# ─── Wizard state ────────────────────────────────────────────────
WIZARD = {}
WIZARD_BOTS = ["lina", "cline", "gemma", "goose"]


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def api(method, json_data=None):
    url = f"{API}/{method}"
    try:
        r = httpx.post(url, json=json_data, timeout=10) if json_data else httpx.post(url, timeout=10)
        return r.json() if r.text else {"ok": False}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def send(chat_id, text, buttons=None):
    data = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
    if buttons:
        data["reply_markup"] = json.dumps({"inline_keyboard": buttons})
    return api("sendMessage", data)


def edit(chat_id, msg_id, text, buttons=None):
    data = {"chat_id": chat_id, "message_id": msg_id, "text": text, "parse_mode": "HTML"}
    if buttons:
        data["reply_markup"] = json.dumps({"inline_keyboard": buttons})
    return api("editMessageText", data)


def answer_cb(cb_id, text="", alert=False):
    return api("answerCallbackQuery", {"callback_query_id": cb_id, "text": text, "show_alert": alert})


# ─── Keyboards ───────────────────────────────────────────────────

def menu_keyboard():
    yamls = sorted([f.stem for f in YAML_DIR.glob("*.yaml") if f.stem != "template"])
    kb = [[{"text": f"📂 {y}", "callback_data": f"menu_{y}"} for y in yamls[i:i+2]] for i in range(0, len(yamls), 2)]
    kb.append([{"text": "➕ Crear nuevo", "callback_data": "action_new"}, {"text": "🔄 Refrescar", "callback_data": "action_refresh"}])
    return kb


def pipeline_keyboard(name):
    return [
        [{"text": "▶️ Ejecutar", "callback_data": f"run_{name}"},
         {"text": "⚡ Paralelo", "callback_data": f"runp_{name}"}],
        [{"text": "ℹ️ Info", "callback_data": f"info_{name}"},
         {"text": "🗑️ Eliminar", "callback_data": f"del_{name}"}],
        [{"text": "🔙 Volver", "callback_data": "action_menu"}],
    ]


def confirm_delete_keyboard(name):
    return [
        [{"text": "✅ Sí, eliminar", "callback_data": f"confirm_del_{name}"}],
        [{"text": "❌ No, volver", "callback_data": f"menu_{name}"}],
    ]


# ─── Wizard ──────────────────────────────────────────────────────

def wizard_send_next(chat_id):
    s = WIZARD.get(chat_id)
    if not s:
        return
    step = s["step"]
    if step == "name":
        send(chat_id, "🧰 <b>¿Nombre del pipeline?</b>\n(Ej: moodle-status, healthcheck, duo)")
    elif step == "bot":
        btns = [[{"text": b, "callback_data": f"wiz_{b}"}] for b in WIZARD_BOTS]
        send(chat_id, f"🤖 <b>Paso {len(s['steps'])+1} — ¿Qué bot?</b>", btns)
    elif step == "message":
        send(chat_id, f"📝 <b>Mensaje para @{s['current_bot']}:</b>\n(Ej: revisa los cuestionarios de Moodle)")
    elif step == "confirm":
        steps_txt = "\n".join([f"  {i+1}. @{x['bot']}: {x['msg'][:50]}..." for i, x in enumerate(s["steps"])])
        btns = [[{"text": "➕ Sí, agregar", "callback_data": "wiz_add"}],
                [{"text": "✅ No, guardar", "callback_data": "wiz_save"}]]
        send(chat_id, f"✅ <b>Pipeline en construcción</b>\n\nNombre: <b>{s['name']}</b>\nPasos ({len(s['steps'])}):\n{steps_txt}\n\n¿Agregar otro paso?", btns)


def wizard_start(chat_id):
    WIZARD[chat_id] = {"step": "name", "name": None, "steps": [], "current_bot": None}
    send(chat_id, "✨ <b>Asistente de pipelines</b>\n\nTe guío paso a paso.")
    wizard_send_next(chat_id)


def wizard_text(chat_id, text):
    s = WIZARD.get(chat_id)
    if not s:
        return False
    step = s["step"]
    if step == "name":
        s["name"] = text.strip().lower().replace(" ", "-")
        s["step"] = "bot"
        wizard_send_next(chat_id)
        return True
    elif step == "bot":
        bot = text.strip().lower()
        if bot not in WIZARD_BOTS:
            send(chat_id, f"❌ Bot inválido. Opciones: {', '.join(WIZARD_BOTS)}")
            return True
        s["current_bot"] = bot
        s["step"] = "message"
        wizard_send_next(chat_id)
        return True
    elif step == "message":
        s["steps"].append({"bot": s["current_bot"], "msg": text.strip()})
        s["current_bot"] = None
        s["step"] = "confirm"
        wizard_send_next(chat_id)
        return True
    return False


def wizard_callback(chat_id, cb_id, data):
    s = WIZARD.get(chat_id)
    if not s:
        return False
    if data == "wiz_add":
        s["step"] = "bot"
        wizard_send_next(chat_id)
        answer_cb(cb_id)
        return True
    if data == "wiz_save":
        return wizard_save(chat_id, cb_id)
    for b in WIZARD_BOTS:
        if data == f"wiz_{b}":
            s["current_bot"] = b
            s["step"] = "message"
            wizard_send_next(chat_id)
            answer_cb(cb_id)
            return True
    return False


def wizard_save(chat_id, cb_id=None):
    s = WIZARD.get(chat_id)
    if not s or not s["steps"]:
        return False
    name = s["name"]
    steps = s["steps"]
    yaml_path = YAML_DIR / f"{name}.yaml"
    data = {
        "_name": name,
        "description": f"Creado via wizard: {len(steps)} paso(s)",
        "steps": [{"bot": x["bot"], "msg": x["msg"], "notify": f"✅ {x['bot'].title()} completo."} for x in steps]
    }
    yaml_path.parent.mkdir(parents=True, exist_ok=True)
    with open(yaml_path, "w") as f:
        yaml.dump(data, f, allow_unicode=True, default_flow_style=False, sort_keys=False)
    summary = f"✅ <b>Pipeline '{name}' creado</b> ({len(steps)} pasos)\n"
    for i, x in enumerate(steps, 1):
        summary += f"  {i}. @{x['bot']}: {x['msg'][:60]}...\n"
    summary += f"\n➡️ Ejecutar: /run {name}"
    btns = [[{"text": f"▶️ Ejecutar {name}", "callback_data": f"run_{name}"}],
            [{"text": "📋 Volver", "callback_data": "action_menu"}]]
    send(chat_id, summary, btns)
    if cb_id:
        answer_cb(cb_id, "✅")
    log(f"✅ Pipeline '{name}' creado via wizard ({len(steps)} pasos)")
    del WIZARD[chat_id]
    return True


# ─── Handlers ───────────────────────────────────────────────────

def handle_help(chat, msg_id=None):
    txt = (
        "🤖 <b>Pipeline Manager</b>\n\n"
        "<b>Comandos:</b>\n"
        "  /pipelines  — 📋 Listar pipelines (con botones)\n"
        "  /run &lt;nombre&gt;  — 🚀 Ejecutar pipeline\n"
        "  /run &lt;nombre&gt; -p  — ⚡ En paralelo\n"
        "  /new  — ✨ Crear pipeline (wizard interactivo)\n"
        "  /status  — 📊 Estado\n"
        "  /cancel  — 🛑 Cancelar\n\n"
        "O usa /pipelines para ver todo con botones."
    )
    send(chat, txt) if not msg_id else edit(chat, msg_id, txt)


def handle_pipelines(chat, msg_id=None):
    yamls = sorted([f.stem for f in YAML_DIR.glob("*.yaml") if f.stem != "template"])
    if not yamls:
        send(chat, "📂 No hay pipelines aún. Creá uno con /new") if not msg_id else edit(chat, msg_id, "📂 No hay pipelines.")
        return
    txt = f"📋 Pipeline Manager — {len(yamls)} pipeline(s)\nElegí uno:"
    if msg_id:
        edit(chat, msg_id, txt, menu_keyboard())
    else:
        send(chat, txt, menu_keyboard())


def handle_info(chat, msg_id, name):
    try:
        r = subprocess.run([sys.executable, str(BIN_PIPELINE), "info", name], capture_output=True, text=True, timeout=15)
        out = (r.stdout + r.stderr).strip()[:1000]
    except Exception as e:
        out = str(e)
    edit(chat, msg_id, f"📋 Pipeline '{name}':\n<code>{out}</code>")


def handle_run(chat, msg_id, name, parallel=False):
    yaml_path = YAML_DIR / f"{name}.yaml"
    if not yaml_path.exists():
        yamls = sorted([f.stem for f in YAML_DIR.glob("*.yaml") if f.stem != "template"])
        edit(chat, msg_id, f"❌ Pipeline '{name}' no encontrado.\n{', '.join(yamls)}")
        return
    edit(chat, msg_id, f"🚀 Ejecutando '{name}'{' en paralelo ⚡' if parallel else ''}...")
    cmd = [sys.executable, str(BIN_PIPELINE), "run", name]
    if parallel: cmd.append("--parallel")
    subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    log(f"🚀 Pipeline '{name}' lanzada")


def handle_new_one_liner(chat, msg_id, text):
    parts = text.split(None, 2)
    if len(parts) < 3:
        edit(chat, msg_id, "❌ Usa: /new &lt;nombre&gt; step1:lina -> msg, step2:cline -> msg2")
        return
    pipe_name = parts[1].strip()
    steps = []
    matches = re.findall(r'step\d+\s*:\s*(\w+)\s*-\s*>\s*(.+?)(?=,\s*step\d+\s*:|,\s*pipeline\s*:|$)', text, re.IGNORECASE | re.DOTALL)
    for bot, msg in matches:
        bot = bot.strip().lower()
        msg = msg.strip()
        if bot and msg:
            steps.append({"bot": bot, "msg": msg})
    name_match = re.search(r'pipeline\s*:\s*name\s*-\s*>\s*(.+?)(?:,|$)', text, re.IGNORECASE)
    if name_match:
        cn = name_match.group(1).strip()
        if cn: pipe_name = cn
    if not steps:
        edit(chat, msg_id, "❌ No se parsearon pasos. Usá el wizard: /new")
        return
    yaml_path = YAML_DIR / f"{pipe_name}.yaml"
    data = {
        "_name": pipe_name,
        "description": f"Creado via Telegram: {len(steps)} paso(s)",
        "steps": [{"bot": s["bot"], "msg": s["msg"], "notify": f"✅ {s['bot'].title()} completo."} for s in steps]
    }
    yaml_path.parent.mkdir(parents=True, exist_ok=True)
    with open(yaml_path, "w") as f:
        yaml.dump(data, f, allow_unicode=True, default_flow_style=False, sort_keys=False)
    summary = f"✅ Pipeline '{pipe_name}' creado ({len(steps)} pasos):\n"
    for i, s in enumerate(steps, 1):
        summary += f"  {i}. @{s['bot']}: {s['msg'][:60]}...\n"
    summary += f"\nEjecutar: /run {pipe_name}"
    edit(chat, msg_id, summary)


def handle_callback(cb):
    data = cb.get("data", "")
    cid = cb["message"]["chat"]["id"]
    mid = cb["message"]["message_id"]
    cb_id = cb["id"]
    log(f"🔘 {data}")
    
    # ── Wizard callbacks ──────────────────────────────────────
    if cid in WIZARD:
        if wizard_callback(cid, cb_id, data):
            return
    
    # ── Menu ─────────────────────────────────────────────────
    if data == "action_menu":
        handle_pipelines(cid, mid)
        answer_cb(cb_id)
        return
    if data == "action_refresh":
        answer_cb(cb_id, "🔄")
        handle_pipelines(cid, mid)
        return
    if data == "action_new":
        answer_cb(cb_id, "✨")
        wizard_start(cid)
        try:
            api("deleteMessage", {"chat_id": cid, "message_id": mid})
        except Exception:
            pass
        return
    
    # ── Pipeline menu ────────────────────────────────────────
    if data.startswith("menu_"):
        name = data[5:]
        edit(cid, mid, f"📋 Pipeline: {name}", pipeline_keyboard(name))
        answer_cb(cb_id)
        return
    
    # ── Run ──────────────────────────────────────────────────
    if data.startswith("run_") or data.startswith("runp_"):
        parallel = data.startswith("runp_")
        name = data[5:] if parallel else data[4:]
        answer_cb(cb_id, f"🚀 {name}")
        handle_run(cid, mid, name, parallel)
        return
    
    # ── Info ─────────────────────────────────────────────────
    if data.startswith("info_"):
        handle_info(cid, mid, data[5:])
        answer_cb(cb_id)
        return
    
    # ── Delete ───────────────────────────────────────────────
    if data.startswith("del_"):
        edit(cid, mid, f"🗑️ Eliminar '{data[4:]}'?", confirm_delete_keyboard(data[4:]))
        answer_cb(cb_id)
        return
    if data.startswith("confirm_del_"):
        name = data[12:]
        yaml_path = YAML_DIR / f"{name}.yaml"
        if yaml_path.exists():
            yaml_path.unlink()
            edit(cid, mid, f"🗑️ '{name}' eliminado.")
            log(f"🗑️ Pipeline '{name}' eliminado")
        else:
            answer_cb(cb_id, "⚠️ No encontrado")
            return
        handle_pipelines(cid, mid)
        return
    
    answer_cb(cb_id, "❌")


def handle_message(msg):
    cid = msg.get("chat", {}).get("id")
    text = msg.get("text", "").strip()
    entities = msg.get("entities", []) or []
    if not cid or not text:
        return
    
    # ── Wizard ────────────────────────────────────────────────
    if cid in WIZARD:
        if wizard_text(cid, text):
            return
    
    # ── Commands ─────────────────────────────────────────────
    is_cmd = any(e.get("type") == "bot_command" for e in entities)
    if not is_cmd:
        return
    text = re.sub(r'@\w+', '', text).strip()
    log(f"📥 {text[:80]}")
    
    if text in ("/start", "/help"):
        handle_help(cid)
        return
    if text in ("/pipelines", "/status"):
        handle_pipelines(cid)
        return
    if text == "/new":
        wizard_start(cid)
        return
    if text.startswith("/new "):
        msg = send(cid, "⏳ Creando pipeline...")
        if msg.get("ok"):
            handle_new_one_liner(cid, msg["result"]["message_id"], text)
        return
    if text.startswith("/run "):
        rest = text[5:].strip()
        parallel = rest.endswith(" -p")
        name = rest[:-3].strip() if parallel else rest
        msg = send(cid, f"🚀 Ejecutando '{name}'...")
        if msg.get("ok"):
            handle_run(cid, msg["result"]["message_id"], name, parallel)
        return
    if text == "/cancel":
        send(cid, "🛑 Cancelación no implementada aún.")
        return
    handle_help(cid)


# ─── Main loop ──────────────────────────────────────────────────

async def main():
    global LAST_UPDATE
    log("⏳ Configurando comandos...")
    r = api("setMyCommands", {"commands": COMMANDS})
    log(f"   Commands: {'✅' if r.get('ok') else '❌'}")
    r = api("setMyDescription", {"description": "🤖 Pipeline Manager — Gestioná pipelines multi-bot desde Telegram."})
    log(f"   Description: {'✅' if r.get('ok') else '❌'}")
    log(f"✅ @s_pipelines_bot conectado. Escuchando...")
    while True:
        try:
            r = api("getUpdates", {"offset": LAST_UPDATE + 1, "timeout": 30, "allowed_updates": ["message", "callback_query"]})
            if not r.get("ok"):
                await asyncio.sleep(3)
                continue
            for update in r.get("result", []):
                LAST_UPDATE = update["update_id"]
                if "message" in update and update["message"].get("text"):
                    handle_message(update["message"])
                if "callback_query" in update:
                    handle_callback(update["callback_query"])
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log(f"⚠️ Error: {e}")
            await asyncio.sleep(3)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log("⏹️ pipeline-bot detenido")
