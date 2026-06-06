#!/usr/bin/env python3
"""
test-goose.py — Test de mensaje fede→goose con detección final.

Flujo:
  1. Verifica que comm-svc esté activo
  2. Envía mensaje como fede → goose vía comm_messages
  3. Monitorea journalctl de goosed (polling, no -f) buscando "Session completed"
  4. Lee comm_messages para detectar respuesta de goose (LinaDb.commSend)
  5. Envía notificación final vía comm → fede SIEMPRE

Uso:
  python3 test-goose.py <mensaje>
  python3 test-goose.py "Revisá el CPU y memoria y publicá los resultados"

Variables de entorno:
  LINA_DB_URL — default: postgresql://lina:lina_dev@localhost:5432/lina
  GOOSED_PID — default: 1745
  TIMEOUT — default: 120 (segundos)
"""

import asyncio
import os
import re
import sys
import time
from datetime import datetime, timezone

DB_URL = os.environ.get("LINA_DB_URL", "postgresql://lina:lina_dev@localhost:5432/lina")
GOOSED_PID = int(os.environ.get("GOOSED_PID", "1745"))
TIMEOUT = int(os.environ.get("TIMEOUT", "120"))


async def check_db() -> bool:
    """Verifica conexión a la DB."""
    import asyncpg
    try:
        conn = await asyncpg.connect(DB_URL, timeout=5)
        await conn.close()
        return True
    except:
        return False


async def check_comm_svc() -> bool:
    """Verifica que comm-svc esté corriendo."""
    proc = await asyncio.create_subprocess_exec(
        "pgrep", "-f", "comm-svc.py",
        stdout=asyncio.subprocess.PIPE,
    )
    stdout, _ = await proc.communicate()
    return bool(stdout.decode().strip())


async def send_to_db(sender: str, destination: str, text: str) -> int | None:
    """Inserta un mensaje en comm_messages y devuelve el id."""
    import asyncpg
    conn = await asyncpg.connect(DB_URL, timeout=10)
    try:
        row = await conn.fetchrow(
            "INSERT INTO comm_messages (sender, destination, message) "
            "VALUES ($1, $2, $3) RETURNING id",
            sender, destination, text
        )
        return row["id"]
    finally:
        await conn.close()


async def read_db_messages(since_id: int = 0) -> list[dict]:
    """Lee mensajes de comm_messages desde un id."""
    import asyncpg
    conn = await asyncpg.connect(DB_URL, timeout=10)
    try:
        rows = await conn.fetch(
            "SELECT id, sender, destination, message, status, created_at "
            "FROM comm_messages "
            "WHERE id >= $1 "
            "ORDER BY id",
            since_id
        )
        return [
            {
                "id": r["id"],
                "sender": r["sender"],
                "destination": r["destination"],
                "message": r["message"],
                "status": r["status"],
                "created_at": str(r["created_at"])[11:19],
            }
            for r in rows
        ]
    finally:
        await conn.close()


async def wait_for_goose_sessions(timeout: int = 120) -> list[dict]:
    """Monitorea journalctl de goosed por polling, buscando Session completed.
    
    No usa -f (follow) para no perder líneas con pipes.
    Retorna todas las sesiones completadas detectadas.
    """
    start = time.time()
    since = datetime.now().strftime("%H:%M:%S")
    
    pattern = re.compile(
        r"Session completed.*"
        r"exit_type: \"?(\w+)\"?.*"
        r"duration_ms: (\d+).*"
        r"total_tokens: (\d+).*"
        r"message_count: (\d+)",
        re.IGNORECASE
    )
    
    sessions = []
    seen_lines = set()
    
    print(f"  ⏳ Esperando respuesta de goose (timeout={timeout}s)...", flush=True)
    
    while time.time() - start < timeout:
        proc = await asyncio.create_subprocess_exec(
            "journalctl", f"_PID={GOOSED_PID}", "--since", since, "--no-pager",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=10.0)
        except asyncio.TimeoutError:
            proc.kill()
            await asyncio.sleep(1)
            continue
        
        output = stdout.decode(errors="replace")
        found_new = False
        
        for line in output.split("\n"):
            line = line.strip()
            if not line or line in seen_lines:
                continue
            seen_lines.add(line)
            
            if "Session started" in line:
                if not found_new:
                    print(f"  🟢 Goose empezó a procesar...", flush=True)
                    found_new = True
            
            if "Session completed" in line:
                m = pattern.search(line)
                if m:
                    s = {
                        "exit_type": m.group(1),
                        "duration_ms": int(m.group(2)),
                        "total_tokens": int(m.group(3)),
                        "message_count": int(m.group(4)),
                    }
                    sessions.append(s)
                    print(f"  ✅ Goose completó: {s['duration_ms']}ms | {s['total_tokens']} tok | {s['message_count']} msgs | exit={s['exit_type']}", flush=True)
        
        if sessions:
            # Si encontramos sesiones, esperamos un poco más por si hay encadenadas
            if len(sessions) == 1:
                await asyncio.sleep(3)
                # Re-verificar
                proc2 = await asyncio.create_subprocess_exec(
                    "journalctl", f"_PID={GOOSED_PID}", "--since", since, "--no-pager",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                stdout2, _ = await asyncio.wait_for(proc2.communicate(), timeout=5.0)
                for line2 in stdout2.decode(errors="replace").split("\n"):
                    m = pattern.search(line2)
                    if m:
                        s2 = {
                            "exit_type": m.group(1),
                            "duration_ms": int(m.group(2)),
                            "total_tokens": int(m.group(3)),
                            "message_count": int(m.group(4)),
                        }
                        if s2 not in sessions:
                            sessions.append(s2)
                            print(f"  ✅ Goose completó (encadenada): {s2['duration_ms']}ms | {s2['total_tokens']} tok | {s2['message_count']} msgs", flush=True)
            break
        
        await asyncio.sleep(1)
    
    return sessions


def build_report(
    msg_id: int,
    text: str,
    sessions: list[dict],
    responses: list[dict],
) -> str:
    """Construye el mensaje de reporte final."""
    lines = []
    lines.append(f"🧪 *TEST GOOSE COMPLETADO*")
    lines.append(f"")
    lines.append(f"📤 Mensaje enviado: `fede → goose` (#{msg_id})")
    lines.append(f'   "{text[:80]}{"..." if len(text) > 80 else ""}"')
    lines.append(f"")
    
    if sessions:
        total_dur = sum(s["duration_ms"] for s in sessions)
        total_tok = sum(s["total_tokens"] for s in sessions)
        total_msg = sum(s["message_count"] for s in sessions)
        lines.append(f"🦆 Goose procesó ({len(sessions)} sesion{'es' if len(sessions) > 1 else ''}):")
        lines.append(f"   • Duración total: {total_dur}ms ({total_dur/1000:.1f}s)")
        lines.append(f"   • Tokens totales: {total_tok}")
        lines.append(f"   • Mensajes totales: {total_msg}")
        for i, s in enumerate(sessions):
            lines.append(f"     #{i+1}: {s['duration_ms']}ms | {s['total_tokens']}tok | {s['message_count']}msgs | {s['exit_type']}")
    else:
        lines.append(f"⚠️ Goose no completó ninguna sesión (timeout {TIMEOUT}s)")
    
    # Buscar respuestas de goose en la DB (mensajes nuevos desde msg_id)
    goose_msgs = [r for r in responses if r["sender"] == "goose" and r["id"] > msg_id]
    if goose_msgs:
        lines.append(f"")
        lines.append(f"📬 goose publicó en comm_messages:")
        for r in goose_msgs:
            preview = r["message"][:130].replace("\n", " ")
            lines.append(f"   • #{r['id']} → {r['destination']}: {preview}")
    
    lines.append(f"")
    if goose_msgs:
        lines.append(f"✅ *Test exitoso* — goose respondió correctamente")
    elif sessions:
        lines.append(f"⚠️ Goose procesó pero no publicó nada vía comm_messages")
    else:
        lines.append(f"❌ *Test falló* — goose no respondió")
    
    return "\n".join(lines)


async def main():
    text = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else "Revisá el CPU y memoria del sistema y publicá los resultados en el grupo Comm"
    
    print(f"🧪 TEST: fede → goose\n", flush=True)
    
    # ── Pre-flight checks ──
    print(f"🔍 Pre-flight checks...", flush=True)
    
    db_ok = await check_db()
    print(f"  DB: {'✅' if db_ok else '❌'}", flush=True)
    if not db_ok:
        sys.exit(1)
    
    svc_ok = await check_comm_svc()
    print(f"  comm-svc: {'✅' if svc_ok else '❌'}", flush=True)
    if not svc_ok:
        print(f"  ⚠️  comm-svc no está corriendo. Inicialo con:", flush=True)
        print(f"     cd ~/lina/comm && python3 comm-svc.py &", flush=True)
        sys.exit(1)
    
    print(f"", flush=True)
    print(f"📤 Mensaje: \"{text}\"\n", flush=True)
    
    ts_start = time.time()
    
    # 1. Enviar mensaje
    msg_id = await send_to_db("fede", "goose", text)
    if not msg_id:
        print("❌ Error enviando mensaje a la DB", flush=True)
        sys.exit(1)
    print(f"  ✅ Mensaje #{msg_id} insertado: fede → goose", flush=True)
    
    # 2. Esperar a que goose procese
    sessions = await wait_for_goose_sessions(timeout=TIMEOUT)
    
    # 3. Leer DB para ver si goose respondió
    await asyncio.sleep(1)
    responses = await read_db_messages(since_id=msg_id)
    
    ts_end = time.time()
    total_time = ts_end - ts_start
    
    # 4. Construir y enviar reporte
    report = build_report(msg_id, text, sessions, responses)
    
    print(f"\n📋 Reporte:\n{report}\n", flush=True)
    
    # 5. Enviar notificación final — SIEMPRE via comm → fede
    notify_id = await send_to_db("comm", "fede", report)
    if notify_id:
        print(f"  ✅ Notificación #{notify_id} enviada: comm → fede", flush=True)
    
    print(f"\n⏱️  Tiempo total: {total_time:.1f}s", flush=True)
    
    # Exit code según resultado
    if any(r["id"] > msg_id and r["sender"] == "goose" for r in responses):
        sys.exit(0)
    elif sessions:
        sys.exit(2)  # goose procesó pero no publicó
    else:
        sys.exit(1)  # timeout


if __name__ == "__main__":
    asyncio.run(main())
