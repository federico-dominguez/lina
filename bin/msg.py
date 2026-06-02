#!/usr/bin/env python3
"""
msg — Mensajería simple entre Cline y LINA.

Uso:
    python3 msg.py send "texto"
        → Envía mensaje de Cline → LINA (DB + Telegram)

    python3 msg.py recv
        → Lee mensajes nuevos de LINA → Cline

    python3 msg.py watch
        → Modo monitor: cada 3s muestra mensajes nuevos

    python3 msg.py history [--limit 10]
        → Muestra historial completo del chat
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
ME = "cline"
OTHER = "lina"


def log(msg: str):
    ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
    print(f"[{ts}] 💬 {msg}", flush=True)


async def send(text: str):
    """Envía mensaje de Cline → LINA (DB + notificación Telegram)."""
    conn = await asyncpg.connect(DB_URL, timeout=5)
    try:
        row = await conn.fetchrow(
            "INSERT INTO agent_messages (sender, recipient, text) VALUES ($1, $2, $3) RETURNING id",
            ME, OTHER, text
        )
        msg_id = row["id"]
        log(f"✅ Mensaje #{msg_id} enviado a LINA vía DB")
    finally:
        await conn.close()

    # Notificar a LINA por Telegram (opcional, para que lo vea al toque)
    import subprocess
    proc = await asyncio.create_subprocess_exec(
        sys.executable, LINA_SEND, f"💬 [Cline] {text}",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    await proc.communicate()
    log(f"📨 Notificado a LINA por Telegram")
    return msg_id


async def recv(limit: int = 10, mark_read: bool = True):
    """Lee mensajes de LINA no leídos."""
    conn = await asyncpg.connect(DB_URL, timeout=5)
    try:
        rows = await conn.fetch(
            """SELECT id, sender, text, created_at
               FROM agent_messages
               WHERE recipient = $1 AND read_at IS NULL
               ORDER BY created_at ASC
               LIMIT $2""",
            ME, limit
        )
        
        if not rows:
            print("📭 No hay mensajes nuevos de LINA.")
            return []
        
        print(f"📩 {len(rows)} mensaje(s) de LINA:")
        print("─" * 60)
        for r in rows:
            ts = str(r["created_at"])[11:19]
            print(f"  [#{r['id']}] [{ts}] {r['text']}")
            print()
        
        if mark_read:
            ids = [r["id"] for r in rows]
            await conn.execute(
                "UPDATE agent_messages SET read_at = NOW() WHERE id = ANY($1)",
                ids
            )
            log(f"✅ {len(ids)} mensaje(s) marcados como leídos")
        
        return rows
    finally:
        await conn.close()


async def history(limit: int = 20):
    """Muestra historial completo del chat Cline ↔ LINA."""
    conn = await asyncpg.connect(DB_URL, timeout=5)
    try:
        rows = await conn.fetch(
            """SELECT id, sender, recipient, text, created_at, read_at
               FROM agent_messages
               ORDER BY created_at DESC
               LIMIT $1""",
            limit
        )
        
        if not rows:
            print("📭 No hay mensajes en el historial.")
            return
        
        print(f"📜 Historial (últimos {len(rows)}):")
        print("─" * 60)
        for r in reversed(rows):
            ts = str(r["created_at"])[11:19]
            icon = "🤖" if r["sender"] == "cline" else "🎀"
            read = " ✓" if r["read_at"] else ""
            print(f"  {icon} [{ts}] {r['text'][:150]}{read}")
    finally:
        await conn.close()


async def watch():
    """Modo monitor: muestra mensajes nuevos cada 3s."""
    print("👁️  Modo watch: monitoreando mensajes de LINA (Ctrl+C para salir)")
    print("─" * 40)
    
    last_count = 0
    try:
        while True:
            conn = await asyncpg.connect(DB_URL, timeout=5)
            try:
                rows = await conn.fetch(
                    """SELECT id, sender, text, created_at
                       FROM agent_messages
                       WHERE recipient = $1 AND read_at IS NULL
                       ORDER BY created_at ASC""",
                    ME
                )
                
                if len(rows) > last_count:
                    new = rows[last_count:]
                    for r in new:
                        ts = str(r["created_at"])[11:19]
                        print(f"  🎀 [{ts}] {r['text']}")
                    last_count = len(rows)
                
            finally:
                await conn.close()
            
            await asyncio.sleep(3)
    except KeyboardInterrupt:
        print("\n👋 Watch detenido")


def main():
    parser = argparse.ArgumentParser(description="Mensajería Cline ↔ LINA")
    sub = parser.add_subparsers(dest="action", required=True)
    
    p_send = sub.add_parser("send", help="Enviar mensaje a LINA")
    p_send.add_argument("text", help="Texto del mensaje")
    
    p_recv = sub.add_parser("recv", help="Leer mensajes de LINA")
    p_recv.add_argument("--limit", type=int, default=10)
    p_recv.add_argument("--no-mark-read", action="store_true", help="No marcar como leídos")
    
    p_hist = sub.add_parser("history", help="Ver historial del chat")
    p_hist.add_argument("--limit", type=int, default=20)
    
    p_watch = sub.add_parser("watch", help="Monitorear mensajes entrantes")
    
    args = parser.parse_args()
    
    if args.action == "send":
        asyncio.run(send(args.text))
    elif args.action == "recv":
        asyncio.run(recv(args.limit, not args.no_mark_read))
    elif args.action == "history":
        asyncio.run(history(args.limit))
    elif args.action == "watch":
        asyncio.run(watch())


if __name__ == "__main__":
    main()
