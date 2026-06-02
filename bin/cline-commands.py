#!/usr/bin/env python3
"""
cline-commands — Lee órdenes de LINA desde PostgreSQL y las ejecuta.

Uso:
    python3 /home/user/lina/bin/cline-commands.py check
        → Lista órdenes pendientes de LINA

    python3 /home/user/lina/bin/cline-commands.py run [--id N]
        → Ejecuta la próxima orden pendiente (o la específica por --id)

    python3 /home/user/lina/bin/cline-commands.py respond --id N --status completed --response "hecho"
        → Marca una orden como completada/failed/rejected con respuesta

    python3 /home/user/lina/bin/cline-commands.py ask --id N --question "pregunta"
        → Marca una orden como needs_approval con una pregunta para LINA

Flujo completo:
  1. LINA escribe una orden en cline_commands (via shell/DB)
  2. Yo corro: cline-commands.py check  → veo órdenes pendientes
  3. Yo corro: cline-commands.py run    → ejecuto y marco como running
  4. Si necesito aprobación: cline-commands.py ask --id N
  5. Cuando termino: cline-commands.py respond --id N --status completed
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


def log(msg: str):
    ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
    print(f"[{ts}] 📋 {msg}", flush=True)


async def get_conn():
    return await asyncpg.connect(DB_URL, timeout=5)


async def cmd_check(status: str = "pending", limit: int = 10):
    """Lista órdenes con cierto estado."""
    conn = await get_conn()
    try:
        rows = await conn.fetch(
            f"""SELECT id, command, args_json, status, response, notification, created_at
                FROM cline_commands
                WHERE status = $1
                ORDER BY created_at ASC
                LIMIT $2""",
            status, limit
        )
        if not rows:
            print(f"📭 No hay órdenes con estado '{status}'.")
            return

        print(f"📋 Órdenes [{status}] ({len(rows)}):")
        print("─" * 80)
        for r in rows:
            args_str = json.dumps(r["args_json"], ensure_ascii=False)[:100] if r["args_json"] else "{}"
            notif = f" [{r['notification']}]" if r["notification"] else ""
            print(f"  #{r['id']:4d} | {r['command']:25s} | args={args_str}{notif}")
            print(f"         creado: {str(r['created_at'])[:19]}")
            if r["response"]:
                print(f"         respuesta: {r['response'][:100]}")
            print()
    finally:
        await conn.close()


async def cmd_run(command_id: int | None = None):
    """Toma la próxima orden pendiente y la marca como running.
    Devuelve el dict de la orden para que el caller la ejecute."""
    conn = await get_conn()
    try:
        if command_id:
            row = await conn.fetchrow(
                "SELECT id, command, args_json, notification FROM cline_commands "
                "WHERE id = $1 AND status = 'pending'",
                command_id
            )
        else:
            row = await conn.fetchrow(
                "SELECT id, command, args_json, notification FROM cline_commands "
                "WHERE status = 'pending' ORDER BY created_at ASC LIMIT 1"
            )

        if not row:
            print("📭 No hay órdenes pendientes para ejecutar.")
            return None

        # Marcar como running
        await conn.execute(
            "UPDATE cline_commands SET status = 'running', started_at = NOW() WHERE id = $1",
            row["id"]
        )
        
        cmd = dict(row)
        log(f"▶️  Ejecutando orden #{cmd['id']}: {cmd['command']}")
        print(f"  args: {json.dumps(cmd['args_json'], ensure_ascii=False)[:200]}")
        if cmd["notification"]:
            print(f"  notification type: {cmd['notification']}")
        print(f"\n  Para marcar como completada:")
        print(f"    cline-commands.py respond --id {cmd['id']} --status completed --response 'lo hice'")
        print(f"  Para marcar como fallida:")
        print(f"    cline-commands.py respond --id {cmd['id']} --status failed --response 'error: ...'")
        print(f"  Para pedir aprobación a LINA:")
        print(f"    cline-commands.py ask --id {cmd['id']} --question '¿qué hago con X?'")
        
        return cmd
    finally:
        await conn.close()


async def cmd_respond(command_id: int, status: str, response: str):
    """Marca una orden como completada/failed/rejected con respuesta."""
    valid = {"completed", "failed", "rejected"}
    if status not in valid:
        print(f"❌ Estado inválido: {status}. Válidos: {valid}")
        return False

    conn = await get_conn()
    try:
        result = await conn.execute(
            """UPDATE cline_commands 
               SET status = $1, response = $2, completed_at = NOW()
               WHERE id = $3 AND (status = 'running' OR status = 'needs_approval')""",
            status, response, command_id
        )
        if result == "UPDATE 0":
            print(f"❌ Orden #{command_id} no encontrada o no está en estado running/needs_approval")
            return False
        
        log(f"✅ Orden #{command_id} marcada como {status}")
        return True
    finally:
        await conn.close()


async def cmd_ask(command_id: int, question: str):
    """Pide aprobación a LINA para una orden en ejecución.
    Marca la orden como needs_approval y deja la pregunta como response."""
    conn = await get_conn()
    try:
        result = await conn.execute(
            "UPDATE cline_commands SET status = 'needs_approval', response = $1 WHERE id = $2 AND status = 'running'",
            question, command_id
        )
        if result == "UPDATE 0":
            print(f"❌ Orden #{command_id} no encontrada o no está running")
            return False
        
        log(f"❓ Orden #{command_id} marcada como needs_approval")
        print(f"   Pregunta: {question}")
        print(f"   LINA debe responder cambiando status a 'pending' (revisado) o 'rejected'")
        return True
    finally:
        await conn.close()


async def cmd_approve(command_id: int, answer: str):
    """LINA responde una aprobación: marca la orden como pending de nuevo
    y guarda la respuesta de LINA en response."""
    conn = await get_conn()
    try:
        result = await conn.execute(
            "UPDATE cline_commands SET status = 'pending', response = $1 WHERE id = $2 AND status = 'needs_approval'",
            answer, command_id
        )
        if result == "UPDATE 0":
            print(f"❌ Orden #{command_id} no encontrada o no está needs_approval")
            return False
        
        log(f"✅ Orden #{command_id} aprobada por LINA, vuelta a pending")
        return True
    finally:
        await conn.close()


def main():
    parser = argparse.ArgumentParser(description="Gestión de órdenes de LINA")
    sub = parser.add_subparsers(dest="action", required=True)

    p_check = sub.add_parser("check", help="Ver órdenes pendientes")
    p_check.add_argument("--status", default="pending", help="Filtrar por estado")
    p_check.add_argument("--limit", type=int, default=10)

    p_run = sub.add_parser("run", help="Ejecutar próxima orden pendiente")
    p_run.add_argument("--id", type=int, dest="command_id", help="ID específico")

    p_resp = sub.add_parser("respond", help="Responder una orden")
    p_resp.add_argument("--id", type=int, required=True, dest="command_id")
    p_resp.add_argument("--status", required=True, choices=["completed", "failed", "rejected"])
    p_resp.add_argument("--response", required=True)

    p_ask = sub.add_parser("ask", help="Pedir aprobación a LINA")
    p_ask.add_argument("--id", type=int, required=True, dest="command_id")
    p_ask.add_argument("--question", required=True)

    p_app = sub.add_parser("approve", help="[LINA] Aprobar una orden needs_approval")
    p_app.add_argument("--id", type=int, required=True, dest="command_id")
    p_app.add_argument("--answer", required=True)

    args = parser.parse_args()

    if args.action == "check":
        asyncio.run(cmd_check(args.status, args.limit))
    elif args.action == "run":
        asyncio.run(cmd_run(args.command_id))
    elif args.action == "respond":
        asyncio.run(cmd_respond(args.command_id, args.status, args.response))
    elif args.action == "ask":
        asyncio.run(cmd_ask(args.command_id, args.question))
    elif args.action == "approve":
        asyncio.run(cmd_approve(args.command_id, args.answer))


if __name__ == "__main__":
    main()
