#!/usr/bin/env python3
"""
cline-notify — Envía notificaciones a LINA (Telegram + DB).

Uso:
    cline-notify.py done "Migración completada ✅"
        → Notifica a LINA que terminé una tarea

    cline-notify.py approval "¿Borro los datos temporales?"
        → Pide aprobación a LINA y espera respuesta

    cline-notify.py log "Procesando archivo X..."
        → Update de progreso para LINA

    cline-notify.py error "Fallo en la conexión"
        → Notifica un error crítico
"""

import argparse
import asyncio
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import asyncpg

DB_URL = os.environ.get("LINA_DB_URL", "postgresql://lina:lina_dev@localhost:5432/lina")
LINA_SEND = "/home/user/lina/bin/lina-send.py"


def log(msg: str):
    ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
    print(f"[{ts}] 🔔 {msg}", flush=True)


async def notify_lina_telegram(text: str):
    """Envía mensaje a LINA via Telegram."""
    import subprocess
    proc = await asyncio.create_subprocess_exec(
        sys.executable, LINA_SEND, text,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        log(f"⚠️ Telegram falló: {stderr.decode().strip()[:200]}")
        return False
    return True


async def notify_done(message: str, session_id: str = None):
    """Notificar tarea completada."""
    text = f"✅ [Cline] Tarea completada: {message}"
    
    # Insertar en cline_commands como registro histórico
    conn = await asyncpg.connect(DB_URL, timeout=5)
    try:
        await conn.execute(
            """INSERT INTO cline_commands (command, status, response, notification, session_id)
               VALUES ('notify_completion', 'completed', $1, 'notify_completion', $2)""",
            message, session_id or ""
        )
        log(f"Registrado en cline_commands")
    finally:
        await conn.close()
    
    # Enviar a Telegram
    await notify_lina_telegram(text)
    log(f"✅ Notificación enviada: {message}")


async def notify_approval(question: str, timeout: int = 300):
    """Pedir aprobación a LINA y esperar respuesta."""
    conn = await asyncpg.connect(DB_URL, timeout=5)
    try:
        row = await conn.fetchrow(
            """INSERT INTO cline_commands (command, status, notification, args_json)
               VALUES ('ask_approval', 'needs_approval', 'ask_approval', $1::jsonb)
               RETURNING id""",
            json.dumps({"question": question})
        )
        cmd_id = row["id"]
        
        text = f"❓ [Cline] Necesito tu aprobación:\n\n{question}\n\n(ID: {cmd_id})"
        await notify_lina_telegram(text)
        log(f"❓ Aprobación solicitada (#{cmd_id})")

        # Esperar respuesta
        print(f"⏳ Esperando respuesta de LINA (timeout {timeout}s)...")
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            await asyncio.sleep(3)
            row = await conn.fetchrow(
                "SELECT status, response FROM cline_commands WHERE id = $1",
                cmd_id
            )
            if row and row["status"] == "pending":
                log(f"✅ Aprobación recibida: {row['response'][:200]}")
                return {"approved": True, "answer": row["response"]}
            elif row and row["status"] == "rejected":
                log(f"❌ Rechazado: {row['response'][:200]}")
                return {"approved": False, "answer": row["response"]}
        
        log(f"⏰ Timeout esperando aprobación")
        return {"approved": False, "answer": None, "timeout": True}
    finally:
        await conn.close()


async def notify_log(message: str):
    """Update de progreso."""
    text = f"🔄 [Cline] Progreso: {message}"
    await notify_lina_telegram(text)
    log(f"📝 Progreso: {message}")


async def notify_error(message: str):
    """Notificar error crítico."""
    text = f"❌ [Cline] Error: {message}"
    
    conn = await asyncpg.connect(DB_URL, timeout=5)
    try:
        await conn.execute(
            "INSERT INTO cline_commands (command, status, response, notification) "
            "VALUES ('error', 'failed', $1, 'error')",
            message
        )
    finally:
        await conn.close()
    
    await notify_lina_telegram(text)
    log(f"❌ Error notificado: {message}")


def main():
    parser = argparse.ArgumentParser(description="Notificaciones a LINA")
    sub = parser.add_subparsers(dest="type", required=True)
    
    p_done = sub.add_parser("done", help="Notificar tarea completada")
    p_done.add_argument("message")
    p_done.add_argument("--session", help="ID de sesión")
    
    p_apr = sub.add_parser("approval", help="Pedir aprobación")
    p_apr.add_argument("question")
    p_apr.add_argument("--timeout", type=int, default=300)
    
    p_log = sub.add_parser("log", help="Update de progreso")
    p_log.add_argument("message")
    
    p_err = sub.add_parser("error", help="Notificar error")
    p_err.add_argument("message")
    
    args = parser.parse_args()
    
    if args.type == "done":
        asyncio.run(notify_done(args.message, getattr(args, "session", None)))
    elif args.type == "approval":
        result = asyncio.run(notify_approval(args.question, args.timeout))
        if result.get("timeout"):
            sys.exit(2)
        elif not result.get("approved"):
            sys.exit(1)
    elif args.type == "log":
        asyncio.run(notify_log(args.message))
    elif args.type == "error":
        asyncio.run(notify_error(args.message))


if __name__ == "__main__":
    main()
