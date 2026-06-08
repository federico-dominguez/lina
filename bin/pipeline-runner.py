#!/usr/bin/env python3
"""
pipeline-runner.py — Pipeline multi-bot configurable.

Uso:
  python3 bin/pipeline-runner.py [pasos.yaml]

  Si no se pasa archivo, usa los pasos por defecto abajo.

Formato del YAML:
    steps:
      - bot: lina
        msg: "hola"
        notify: "✅ Lina saludo completo."
      - bot: cline
        msg: "decime el uso de CPU y memoria del servidor"
        notify: "✅ Cline sysinfo completo."

Cada paso:
  - Envía msg al bot en el grupo "Comms" (con @mention)
  - Espera el evento "finish" en session_events (DB del Observer)
  - Envía notify al grupo (texto plano, sin @mention, sin loop)
"""

import asyncio
import json
import os
import subprocess
import sys
import time
from pathlib import Path

# ═════════════════════════════════════════════════════════════════════════════
# CONFIGURACIÓN
# ═════════════════════════════════════════════════════════════════════════════

BASE = Path(__file__).resolve().parent.parent
SEND_BOT = str(BASE / "comm" / "send-bot.py")
COMM_DIR = str(BASE / "comm")
DB_DSN = os.environ.get("LINA_DB_URL", "postgresql://lina:lina_dev@localhost:5432/lina")

POLL_INTERVAL = 0.5  # segundos entre polls a la DB
POLL_TIMEOUT = 180   # tiempo máximo esperando Finish por paso

# ═════════════════════════════════════════════════════════════════════════════
# PASOS POR DEFECTO (usado si no se pasa archivo YAML)
# ═════════════════════════════════════════════════════════════════════════════

DEFAULT_STEPS = [
    {
        "bot": "lina",
        "msg": "hola",
        "notify": "✅ Lina saludo completo.",
    },
    {
        "bot": "cline",
        "msg": "decime el uso de CPU y memoria del servidor",
        "notify": "✅ Cline sysinfo completo.",
    },
]

# Mapa bot_name → agent en session_events
BOT_AGENT = {
    "lina": "lina",
    "cline": "cline",
    "goose": "goose",
    "gemma": "gemma",
}


# ═════════════════════════════════════════════════════════════════════════════
# HELPERS
# ═════════════════════════════════════════════════════════════════════════════

def log(msg: str):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def tg_send(target: str, message: str) -> bool:
    """Envía @s_{target}_bot {message} al grupo via Comm."""
    try:
        r = subprocess.run(
            [sys.executable, SEND_BOT, target, message],
            capture_output=True, text=True, timeout=30,
        )
        if r.returncode == 0:
            log(f"📤 @{target}: {message}")
            return True
        log(f"⚠️ tg_send: {(r.stdout+r.stderr)[:200]}")
        return False
    except Exception as e:
        log(f"⚠️ tg_send error: {e}")
        return False


async def tg_notify_plain(message: str) -> bool:
    """Envía texto plano al grupo (sin @mention)."""
    try:
        from telethon import TelegramClient
        from telethon.tl.functions.messages import GetDialogsRequest
        from telethon.tl.types import InputPeerEmpty
        client = TelegramClient(
            str(Path(COMM_DIR) / "comm_session"),
            12663248, "57a7b9ec3cd607e64b73dbae1240af24",
        )
        client.parse_mode = None
        await client.start()
        dialogs = await client(GetDialogsRequest(
            offset_date=None, offset_id=0, offset_peer=InputPeerEmpty(),
            limit=200, hash=0,
        ))
        gid = None
        for d in dialogs.chats:
            title = (getattr(d, "title", "") or "")
            if "Comm" in title:
                gid = d.id
                break
        if not gid:
            log("❌ tg_notify: no group")
            await client.disconnect()
            return False
        await client.send_message(gid, message)
        log(f"📤 (plain) {message}")
        await client.disconnect()
        return True
    except Exception as e:
        log(f"⚠️ tg_notify error: {e}")
        return False


async def wait_for_finish(agent: str) -> dict:
    """Espera nuevo evento 'finish' en session_events (DB) para un agente."""
    import asyncpg
    conn = await asyncpg.connect(DB_DSN, timeout=5)
    last_id = await conn.fetchval(
        "SELECT COALESCE(MAX(id),0) FROM session_events WHERE event_type='finish' AND agent=$1",
        agent,
    )
    await conn.close()
    log(f"⏳ Finish (last #{last_id})...")

    deadline = time.time() + POLL_TIMEOUT
    while time.time() < deadline:
        try:
            conn2 = await asyncpg.connect(DB_DSN, timeout=5)
            try:
                row = await conn2.fetchrow(
                    "SELECT id,session_id,payload FROM session_events "
                    "WHERE event_type='finish' AND agent=$1 AND id>$2 ORDER BY id ASC LIMIT 1",
                    agent, last_id,
                )
                if row:
                    p = json.loads(row["payload"]) if isinstance(row["payload"], str) else (row["payload"] or {})
                    log(f"🏁 #{row['id']} session={row['session_id']} "
                        f"reason={p.get('reason','?')} tokens={p.get('tokens',0)}")
                    return {
                        "session_id": row["session_id"],
                        "reason": p.get("reason", "stop"),
                        "tokens": p.get("tokens", 0),
                    }
            finally:
                await conn2.close()
        except Exception as e:
            log(f"⚠️ DB error: {e}")
            last_id += 1
        await asyncio.sleep(POLL_INTERVAL)

    log(f"⚠️ Timeout: no Finish en {POLL_TIMEOUT}s")
    return {"session_id": "", "reason": "timeout", "tokens": 0}


# ═════════════════════════════════════════════════════════════════════════════
# EJECUTOR DE PASOS
# ═════════════════════════════════════════════════════════════════════════════

async def run_step(step: dict) -> bool:
    """Ejecuta un paso del pipeline.

    Args:
        step: dict con claves:
            bot:    "lina" | "cline" | "goose" | "gemma"
            msg:    mensaje a enviar (ej: "hola", "decime el CPU")
            notify: texto de notificación al grupo cuando termine

    Returns:
        True si el paso se completó exitosamente.
    """
    bot = step["bot"]
    msg = step["msg"]
    notify = step.get("notify", f"✅ {bot.capitalize()} completo.")
    agent = BOT_AGENT.get(bot, bot)

    log("═" * 50)
    log(f"🎯 Paso: @s_{bot}_bot ← \"{msg}\"")

    # 1. Enviar al grupo con @mention
    ok = tg_send(bot, msg)
    if not ok:
        log(f"❌ {bot}: fallo envío al grupo")
        return False

    # 2. Esperar Finish en session_events
    fin = await wait_for_finish(agent)
    if fin.get("reason") == "timeout":
        log(f"❌ {bot}: timeout esperando Finish")
        return False

    # 3. Notificar al grupo (texto plano, sin loop)
    await tg_notify_plain(notify)
    log(f"💰 {bot}: {fin.get('tokens', 0)} tokens")
    return True


# ═════════════════════════════════════════════════════════════════════════════
# MAIN
# ═════════════════════════════════════════════════════════════════════════════

async def main():
    # Cargar pasos: desde YAML file o usar defaults
    steps = DEFAULT_STEPS
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    flags = [a for a in sys.argv[1:] if a.startswith("--")]
    parallel = "--parallel" in flags or os.environ.get("PIPELINE_PARALLEL", "") == "1"
    if args:
        yaml_path = args[0]
        try:
            import yaml
            with open(yaml_path) as f:
                data = yaml.safe_load(f)
            steps = data.get("steps", data) if isinstance(data, dict) else data
            log(f"📂 Pasos cargados desde {yaml_path}")
        except Exception as e:
            log(f"⚠️ Error cargando {yaml_path}: {e}")
            log("Usando pasos por defecto.")

    log(f"🚀 Pipeline: {len(steps)} paso(s)")
    descs = [f"@{s['bot']}: {s['msg'][:30]}" for s in steps]
    log(f"   {'  →  '.join(descs)}")

    results = {}
    if parallel:
        log(f"⚡ Modo paralelo: ejecutando {len(steps)} paso(s) simultáneamente")
        async def run_all():
            tasks = [run_step(s) for s in steps]
            return await asyncio.gather(*tasks)
        ok_list = await run_all()
        for i, ok in enumerate(ok_list):
            s = steps[i]
            results[f"@{s['bot']}: {s['msg'][:30]}"] = ok
    else:
        for i, step in enumerate(steps):
            log(f"\n─── Paso {i+1}/{len(steps)} ───")
            ok = await run_step(step)
            results[f"@{step['bot']}: {step['msg'][:30]}"] = ok

    log("\n" + "═" * 50)
    log("📊 RESUMEN:")
    for desc, ok in results.items():
        log(f"   {'✅' if ok else '❌'} {desc}")

    if all(results.values()):
        log("✅ Pipeline completado exitosamente")
        sys.exit(0)
    else:
        log("⚠️ Pipeline completado con errores")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
