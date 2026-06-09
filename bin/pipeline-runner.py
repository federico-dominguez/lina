import asyncio, json, os, subprocess, sys, time
from pathlib import Path

import yaml

BASE = Path(__file__).resolve().parent.parent
SEND_BOT = BASE / "comm" / "send-bot.py"
BIN_PIPELINE = BASE / "bin" / "pipeline"
YAML_DIR = BASE / "config" / "pipelines"

DB_DSN = os.environ.get("LINA_DB_URL", "postgresql://lina:lina_dev@localhost:5432/lina")
POLL_INTERVAL = 0.5
POLL_TIMEOUT = 600  # 10 min — tareas Moodle pueden ser largas

DEFAULT_STEPS = [
    {"bot": "lina", "msg": "hola", "notify": "✅ Lina saludo completo."},
    {"bot": "cline", "msg": "decime el uso de CPU y memoria del servidor", "notify": "✅ Cline sysinfo completo."},
]

BOT_AGENT = {"lina": "lina", "cline": "cline", "goose": "goose", "gemma": "gemma"}

# ─── Pipeline run tracking ──────────────────────────────────────
PIPELINE_RUNS_FILE = "/tmp/pipeline-runs.jsonl"


def track_run(name, status, steps_data, started_at=None):
    """Guarda un registro de ejecución de pipeline."""
    import json
    record = {
        "name": name,
        "status": status,
        "started_at": started_at or (time.time() - 10),
        "completed_at": time.time(),
        "steps": steps_data,
    }
    try:
        with open(PIPELINE_RUNS_FILE, "a") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception as e:
        log(f"⚠️ track: {e}")


def log(msg: str):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def tg_send(target: str, message: str) -> bool:
    try:
        r = subprocess.run(
            [sys.executable, SEND_BOT, target, message],
            capture_output=True, text=True, timeout=30,
        )
        if r.returncode == 0:
            log(f"📤 @{target}: {message}")
            return True
        log(f"❌ send-bot exit={r.returncode}: {r.stderr[:200]}")
        return False
    except Exception as e:
        log(f"❌ send-bot: {e}")
        return False


async def tg_notify_plain(text: str):
    try:
        subprocess.run([sys.executable, SEND_BOT, "--plain", text], capture_output=True, text=True, timeout=30)
        log(f"📤 (plain): {text}")
    except Exception as e:
        log(f"⚠️ notify: {e}")


async def wait_for_finish(agent: str) -> dict:
    """Espera el ULTIMO evento 'finish' en session_events (DB) para un agente.
    Usa cooldown de 3s para evitar Finishes de sub-agentes.
    """
    last_id = 0
    try:
        import asyncpg
        conn = await asyncpg.connect(DB_DSN, timeout=5)
        try:
            last_id = await conn.fetchval(
                "SELECT COALESCE(MAX(id),0) FROM session_events WHERE event_type='finish' AND agent=$1", agent)
        finally:
            await conn.close()
    except:
        pass

    log(f"⏳ Finish (last #{last_id})...")
    found_finish = None
    deadline = time.time() + POLL_TIMEOUT
    while time.time() < deadline:
        try:
            import asyncpg
            conn2 = await asyncpg.connect(DB_DSN, timeout=5)
            try:
                row = await conn2.fetchrow(
                    "SELECT id,session_id,payload FROM session_events "
                    "WHERE event_type='finish' AND agent=$1 AND id>$2 ORDER BY id ASC LIMIT 1",
                    agent, last_id,
                )
                if row:
                    p = json.loads(row["payload"]) if isinstance(row["payload"], str) else (row["payload"] or {})
                    reason = p.get("reason", "stop")
                    tokens = p.get("tokens", 0)
                    log(f"🏁 #{row['id']} session={row['session_id']} reason={reason} tokens={tokens}")
                    found_finish = {"session_id": row["session_id"], "reason": reason, "tokens": tokens, "id": row["id"]}
                    last_id = row["id"]
                    continue  # cooldown
            finally:
                await conn2.close()
        except Exception as e:
            log(f"⚠️ DB error: {e}")
            last_id += 1

        if found_finish:
            await asyncio.sleep(3)
            try:
                import asyncpg
                conn3 = await asyncpg.connect(DB_DSN, timeout=5)
                try:
                    newer = await conn3.fetchval(
                        "SELECT COUNT(*) FROM session_events "
                        "WHERE event_type='finish' AND agent=$1 AND id>$2",
                        agent, found_finish["id"],
                    )
                finally:
                    await conn3.close()
                if newer and newer > 0:
                    log(f"⚠️ Apareció otro Finish, esperando el último...")
                    last_id = found_finish["id"]
                    found_finish = None
                    continue
            except:
                pass
            log(f"🏁 Fin CONFIRMADO: reason={found_finish['reason']}")
            return found_finish

        await asyncio.sleep(POLL_INTERVAL)

    log(f"⚠️ Timeout ({POLL_TIMEOUT}s): no Finish para '{agent}'")
    return {"session_id": "", "reason": "timeout", "tokens": 0}


async def run_step(step: dict) -> bool:
    bot = step["bot"]
    msg = step["msg"]
    notify = step.get("notify", f"✅ {bot.capitalize()} completo.")
    agent = BOT_AGENT.get(bot, bot)

    log("═" * 50)
    log(f"🎯 Paso: @s_{bot}_bot ← \"{msg}\"")

    ok = tg_send(bot, msg)
    if not ok:
        log(f"❌ {bot}: fallo envío al grupo")
        return False

    fin = await wait_for_finish(agent)
    if fin.get("reason") == "timeout":
        log(f"❌ {bot}: timeout esperando Finish")
        return False

    await tg_notify_plain(notify)
    log(f"💰 {bot}: {fin.get('tokens', 0)} tokens")
    return True


async def main():
    started_at = time.time()
    steps = DEFAULT_STEPS
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    flags = [a for a in sys.argv[1:] if a.startswith("--")]
    parallel = "--parallel" in flags or os.environ.get("PIPELINE_PARALLEL", "") == "1"
    if args:
        yaml_path = args[0]
        try:
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
    step_records = []

    if parallel:
        log(f"⚡ Modo paralelo: ejecutando {len(steps)} paso(s) simultáneamente")
        async def run_all():
            tasks = [run_step(s) for s in steps]
            return await asyncio.gather(*tasks)
        ok_list = await run_all()
        for i, ok in enumerate(ok_list):
            s = steps[i]
            results[f"@{s['bot']}: {s['msg'][:30]}"] = ok
            step_records.append({"bot": s["bot"], "msg": s["msg"], "status": "done" if ok else "error", "tokens": 0, "cost": 0})
    else:
        for i, step in enumerate(steps):
            log(f"\n─── Paso {i+1}/{len(steps)} ───")
            ok = await run_step(step)
            results[f"@{step['bot']}: {step['msg'][:30]}"] = ok
            step_records.append({"bot": step["bot"], "msg": step["msg"], "status": "done" if ok else "error", "tokens": 0, "cost": 0})

    log("\n" + "═" * 50)
    log("📊 RESUMEN:")
    for desc, ok in results.items():
        log(f"   {'✅' if ok else '❌'} {desc}")

    # Tracking
    pipeline_name = Path(args[0]).stem if args else "default"
    all_ok = all(results.values())
    track_run(pipeline_name, "done" if all_ok else "error", step_records, started_at)

    if all_ok:
        log("✅ Pipeline completado exitosamente")
        sys.exit(0)
    else:
        log("⚠️ Pipeline completado con errores")
        sys.exit(1)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log("⏹️ Pipeline detenido")
