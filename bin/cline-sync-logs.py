#!/usr/bin/env python3
"""
cline-sync-logs — Sincroniza los logs de Cline (SQLite → PostgreSQL).

Lee la DB de sesiones de goosed (SQLite) y vuelca los mensajes nuevos
en PostgreSQL (tabla cline_logs), separada de los logs de LINA.

Uso:
    python3 /home/user/lina/bin/cline-sync-logs.py              # sync incremental
    python3 /home/user/lina/bin/cline-sync-logs.py --full        # sync todo desde cero
    python3 /home/user/lina/bin/cline-sync-logs.py --watch       # modo continuo (c/30s)
    python3 /home/user/lina/bin/cline-sync-logs.py --session ID  # sync solo una sesión

Requiere: pip3 install asyncpg --break-system-packages
"""

import asyncio
import json
import os
import sqlite3
import sys
import time
from pathlib import Path

DB_URL = os.environ.get("LINA_DB_URL", "postgresql://lina:lina_dev@localhost:5432/lina")
SQLITE_DB = "/root/.local/share/goose/sessions/sessions.db"
OFFSET_FILE = Path("/home/user/lina/.cline_sync_offset")


def get_pg_connection():
    import asyncpg
    return asyncpg.connect(DB_URL, timeout=5)


def load_offset() -> int:
    """Last synced SQLite message ID."""
    try:
        return int(OFFSET_FILE.read_text().strip())
    except (FileNotFoundError, ValueError):
        return 0


def save_offset(msg_id: int) -> None:
    OFFSET_FILE.parent.mkdir(parents=True, exist_ok=True)
    OFFSET_FILE.write_text(str(msg_id))


def extract_messages(sqlite_path: str, min_id: int = 0, session_filter: str | None = None) -> list[dict]:
    """Extract messages from goosed SQLite sessions DB."""
    if not os.path.exists(sqlite_path):
        print(f"⚠️ SQLite DB no encontrada: {sqlite_path}")
        return []

    conn = sqlite3.connect(sqlite_path)
    conn.row_factory = sqlite3.Row
    
    # Get sessions list
    if session_filter:
        sessions = conn.execute(
            "SELECT id, name as title, updated_at FROM sessions WHERE id = ?", 
            (session_filter,)
        ).fetchall()
    else:
        sessions = conn.execute(
            "SELECT id, name as title, updated_at FROM sessions ORDER BY updated_at DESC"
        ).fetchall()
    
    messages = []
    for sess in sessions:
        sid = sess["id"]
        rows = conn.execute(
            "SELECT id, role, content_json, created_timestamp FROM messages "
            "WHERE session_id = ? AND id > ? ORDER BY id ASC",
            (sid, min_id)
        ).fetchall()
        
        turn = 0
        for row in rows:
            turn += 1
            msg_id = row["id"]
            role = row["role"]
            created = row["created_timestamp"]
            content_json_str = row["content_json"]
            
            try:
                items = json.loads(content_json_str)
            except json.JSONDecodeError:
                items = [{"type": "text", "text": content_json_str}]
            
            if not isinstance(items, list):
                items = [items]
            
            for item in items:
                content_type = item.get("type", "text")
                
                if content_type == "thinking":
                    messages.append({
                        "message_id": msg_id,
                        "session_id": sid,
                        "turn_number": turn,
                        "role": role,
                        "content_type": "thinking",
                        "content": item.get("thinking", ""),
                        "tool_name": None,
                        "tool_args": None,
                        "created_timestamp": created,
                    })
                elif content_type == "text":
                    messages.append({
                        "message_id": msg_id,
                        "session_id": sid,
                        "turn_number": turn,
                        "role": role,
                        "content_type": "text",
                        "content": item.get("text", ""),
                        "tool_name": None,
                        "tool_args": None,
                        "created_timestamp": created,
                    })
                elif content_type == "toolRequest":
                    tc = item.get("toolCall", {})
                    val = tc.get("value", {}) if isinstance(tc, dict) else {}
                    messages.append({
                        "message_id": msg_id,
                        "session_id": sid,
                        "turn_number": turn,
                        "role": role,
                        "content_type": "tool_call",
                        "content": "",
                        "tool_name": val.get("name", ""),
                        "tool_args": json.dumps(val.get("arguments", {}), ensure_ascii=False),
                        "created_timestamp": created,
                    })
                elif content_type == "toolResponse":
                    tr = item.get("toolResult", {})
                    val = tr.get("value", {}) if isinstance(tr, dict) else {}
                    content_text = ""
                    content_items = val.get("content", []) if isinstance(val, dict) else []
                    for ci in content_items:
                        if isinstance(ci, dict) and ci.get("type") == "text":
                            content_text += ci.get("text", "")
                    
                    tool_call_id = item.get("id", "")
                    messages.append({
                        "message_id": msg_id,
                        "session_id": sid,
                        "turn_number": turn,
                        "role": role,
                        "content_type": "tool_response",
                        "content": content_text[:5000],  # truncar megarespuestas
                        "tool_name": tool_call_id,
                        "tool_args": None,
                        "created_timestamp": created,
                    })
                elif content_type == "tool_use":
                    messages.append({
                        "message_id": msg_id,
                        "session_id": sid,
                        "turn_number": turn,
                        "role": role,
                        "content_type": "tool_call",
                        "content": "",
                        "tool_name": item.get("name", ""),
                        "tool_args": json.dumps(item.get("input", {}), ensure_ascii=False),
                        "created_timestamp": created,
                    })
                elif content_type == "tool_result":
                    messages.append({
                        "message_id": msg_id,
                        "session_id": sid,
                        "turn_number": turn,
                        "role": role,
                        "content_type": "tool_response",
                        "content": str(item.get("content", ""))[:5000],
                        "tool_name": None,
                        "tool_args": None,
                        "created_timestamp": created,
                    })
    
    conn.close()
    return messages


async def sync_to_pg(messages: list[dict]) -> int:
    """Insert messages into PostgreSQL cline_logs."""
    if not messages:
        return 0
    
    conn = await get_pg_connection()
    try:
        inserted = 0
        for msg in messages:
            await conn.execute(
                """INSERT INTO cline_logs 
                   (session_id, turn_number, role, content_type, content, tool_name, tool_args, created_at)
                   VALUES ($1, $2, $3, $4, $5, $6, $7, to_timestamp($8::double precision))""",
                msg["session_id"],
                msg["turn_number"],
                msg["role"],
                msg["content_type"],
                msg["content"],
                msg["tool_name"],
                msg["tool_args"],
                msg["created_timestamp"],
            )
            inserted += 1
        
        # Update offset to last message id synced
        max_id = max(m["message_id"] for m in messages)
        save_offset(max_id)
        
        return inserted
    finally:
        await conn.close()


async def main():
    args = sys.argv[1:]
    
    full = "--full" in args
    watch = "--watch" in args
    session_filter = None
    
    for arg in args:
        if arg.startswith("--session="):
            session_filter = arg.split("=", 1)[1]
        elif arg == "--session" and args.index(arg) + 1 < len(args):
            idx = args.index(arg)
            session_filter = args[idx + 1]
    
    if watch:
        print("🔍 Modo watch: sincronizando cada 30s...")
        while True:
            offset = 0 if full else load_offset()
            msgs = extract_messages(SQLITE_DB, offset, session_filter)
            if msgs:
                cnt = await sync_to_pg(msgs)
                print(f"  [{time.strftime('%H:%M:%S')}] +{cnt} mensajes sincronizados")
            else:
                print(f"  [{time.strftime('%H:%M:%S')}] sin novedades")
            await asyncio.sleep(30)
    
    else:
        offset = 0 if full else load_offset()
        msgs = extract_messages(SQLITE_DB, offset, session_filter)
        
        if not msgs:
            print("ℹ️ Sin mensajes nuevos para sincronizar.")
            return
        
        cnt = await sync_to_pg(msgs)
        print(f"✅ {cnt} mensajes sincronizados a cline_logs")
        
        # Mostrar resumen
        sessions = set(m["session_id"] for m in msgs)
        for sid in sorted(sessions):
            count = sum(1 for m in msgs if m["session_id"] == sid)
            print(f"  📁 {sid}: {count} mensajes")


if __name__ == "__main__":
    asyncio.run(main())
