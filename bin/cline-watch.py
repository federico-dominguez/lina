#!/usr/bin/env python3
"""
cline-watch — Muestra el estado actual de Cline para que LINA monitoree.

Uso:
    python3 /home/user/lina/bin/cline-watch.py
        → Resumen completo de actividad reciente

    python3 /home/user/lina/bin/cline-watch.py --thinking
        → Solo últimos pensamientos

    python3 /home/user/lina/bin/cline-watch.py --commands
        → Solo últimos comandos ejecutados

    python3 /home/user/lina/bin/cline-watch.py --follow
        → Modo seguir, actualiza cada 5s

    python3 /home/user/lina/bin/cline-watch.py --session ID
        → Ver logs de una sesión específica
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
    print(f"[{ts}] 👁️ {msg}", flush=True)


def format_session(session_id: str) -> str:
    """Da un nombre legible a la sesión."""
    names = {
        "20260601_3": "Sesión actual (Cline ↔ LINA)",
        "20260601_1": "Sesión anterior (LINA+Fede)",
    }
    return names.get(session_id, f"Sesión {session_id}")


async def show_status(follow: bool = False, thinking_only: bool = False,
                      commands_only: bool = False, session_filter: str = None):
    """Muestra el estado actual de Cline."""
    
    conn = await asyncpg.connect(DB_URL, timeout=5)
    try:
        while True:
            output = []
            
            # ── 1. Estado de órdenes ────────────────────────────────────
            orders = await conn.fetch("""
                SELECT status, COUNT(*) as cnt FROM cline_commands
                WHERE created_at > NOW() - INTERVAL '1 hour'
                GROUP BY status ORDER BY status
            """)
            if orders:
                parts = [f"{r['status']}: {r['cnt']}" for r in orders]
                output.append(f"📋 Órdenes (última hora): {' | '.join(parts)}")
            
            # Órdenes pendientes/running
            active = await conn.fetch("""
                SELECT id, command, status, created_at FROM cline_commands
                WHERE status IN ('pending', 'running', 'needs_approval')
                ORDER BY created_at DESC LIMIT 3
            """)
            if active:
                output.append("  Activas:")
                for r in active:
                    icon = {"pending": "⏳", "running": "▶️", "needs_approval": "❓"}.get(r["status"], "📌")
                    output.append(f"    {icon} #{r['id']} {r['command']} [{r['status']}]")
            
            # ── 2. Últimos pensamientos ────────────────────────────────
            if not commands_only:
                thoughts = await conn.fetch("""
                    SELECT session_id, substr(content, 1, 300) as snippet, created_at
                    FROM cline_logs
                    WHERE content_type = 'thinking'
                    ORDER BY id DESC LIMIT 3
                """)
                if thoughts:
                    output.append(f"\n🧠 Últimos pensamientos:")
                    for r in thoughts:
                        ts = str(r["created_at"])[11:19]
                        output.append(f"  [{ts}] {r['snippet']}")
                        output.append("  ─")
            
            # ── 3. Últimos comandos ejecutados ─────────────────────────
            if not thinking_only:
                cmds = await conn.fetch("""
                    SELECT tool_name, substr(tool_args, 1, 200) as args,
                           substr(content, 1, 200) as result, created_at
                    FROM cline_logs
                    WHERE content_type = 'tool_call'
                    ORDER BY id DESC LIMIT 5
                """)
                if cmds:
                    output.append(f"\n⚙️ Últimos comandos ({len(cmds)}):")
                    for r in cmds:
                        ts = str(r["created_at"])[11:19]
                        output.append(f"  [{ts}] {r['tool_name']}")
                        if r["args"]:
                            output.append(f"         args: {r['args'][:150]}")
                        output.append("  ─")
            
            # ── 4. Últimas respuestas recibidas ────────────────────────
            if not thinking_only:
                resps = await conn.fetch("""
                    SELECT substr(content, 1, 200) as snippet, created_at
                    FROM cline_logs
                    WHERE content_type = 'tool_response'
                    ORDER BY id DESC LIMIT 3
                """)
                if resps:
                    output.append(f"\n📥 Últimas respuestas:")
                    for r in resps:
                        ts = str(r["created_at"])[11:19]
                        output.append(f"  [{ts}] {r['snippet'][:150]}")
                        output.append("  ─")
            
            # ── 5. Último mensaje de texto ────────────────────────────
            texts = await conn.fetch("""
                SELECT substr(content, 1, 200) as snippet, role, created_at
                FROM cline_logs
                WHERE content_type = 'text'
                ORDER BY id DESC LIMIT 2
            """)
            if texts:
                output.append(f"\n💬 Últimos mensajes:")
                for r in texts:
                    ts = str(r["created_at"])[11:19]
                    icon = "👤" if r["role"] == "user" else "🤖"
                    output.append(f"  {icon} [{ts}] {r['snippet'][:150]}")
            
            # ── 6. Métricas ────────────────────────────────────────────
            metrics = await conn.fetchrow("""
                SELECT COUNT(*) as total,
                       COUNT(*) FILTER (WHERE content_type='thinking') as thoughts,
                       COUNT(*) FILTER (WHERE content_type='tool_call') as tools,
                       MAX(created_at) as last_activity
                FROM cline_logs
                WHERE created_at > NOW() - INTERVAL '10 minutes'
            """)
            if metrics:
                last = str(metrics["last_activity"])[11:19] if metrics["last_activity"] else "—"
                output.append(f"\n📊 Últimos 10min: {metrics['total']} eventos "
                              f"({metrics['thoughts']}🧠 {metrics['tools']}⚙️) "
                              f"| última actividad: {last}")
            
            # Print
            result = "\n".join(output)
            print(result)
            
            if not follow:
                break
            
            print(f"\n⏳ Siguiente refresh en 5s... (Ctrl+C para salir)")
            await asyncio.sleep(5)
            # Clear previous output (cursor up)
            lines = result.count("\n") + 8
            print(f"\033[{lines}A", end="")
    
    finally:
        await conn.close()


async def show_session(session_id: str):
    """Muestra el resumen de una sesión específica."""
    conn = await asyncpg.connect(DB_URL, timeout=5)
    try:
        # Resumen
        summary = await conn.fetch("""
            SELECT content_type, COUNT(*) as cnt
            FROM cline_logs
            WHERE session_id = $1
            GROUP BY content_type ORDER BY content_type
        """, session_id)
        
        if not summary:
            print(f"❌ No hay logs para sesión {session_id}")
            return
        
        print(f"📁 {format_session(session_id)}")
        for r in summary:
            print(f"  {r['content_type']:15s} {r['cnt']:5d}")
        
        # Últimos eventos
        print(f"\n📋 Últimos eventos:")
        events = await conn.fetch("""
            SELECT content_type, role, substr(content, 1, 200) as snippet,
                   tool_name, created_at
            FROM cline_logs
            WHERE session_id = $1
            ORDER BY id DESC LIMIT 10
        """, session_id)
        for r in reversed(events):
            ts = str(r["created_at"])[11:19]
            icon = {"thinking": "🧠", "text": "💬", "tool_call": "⚙️", "tool_response": "📥"}.get(r["content_type"], "📄")
            snippet = r["snippet"] or ""
            tool = f" [{r['tool_name']}]" if r["tool_name"] else ""
            print(f"  {icon} [{ts}]{tool} {snippet[:120]}")
    
    finally:
        await conn.close()


def main():
    parser = argparse.ArgumentParser(description="Monitorear estado de Cline")
    parser.add_argument("--thinking", action="store_true", help="Solo pensamientos")
    parser.add_argument("--commands", action="store_true", help="Solo comandos")
    parser.add_argument("--follow", action="store_true", help="Modo seguir (refresh cada 5s)")
    parser.add_argument("--session", help="Ver sesión específica")
    
    args = parser.parse_args()
    
    if args.session:
        asyncio.run(show_session(args.session))
    else:
        asyncio.run(show_status(
            follow=args.follow,
            thinking_only=args.thinking,
            commands_only=args.commands,
        ))


if __name__ == "__main__":
    main()
