#!/usr/bin/env python3
"""
cline-poll-daemon — Puente bidireccional LINA ↔ CLINE.

Escucha canales PostgreSQL (LISTEN/NOTIFY) y Telegram:
• cline_new_command → forwardea órdenes de LINA a CLINE vía Telegram
• Respuesta de CLINE → captura TODA la conversación en DB para auditoría,
                         NOTIFICA a LINA con el mensaje SUSTANCIAL de Cline
                         (salta mensajes de cierre tipo "✅ Orden #X cerrada")

Usa Telethon para enviar/recibir mensajes como si fuera Fede.
"""

import asyncio
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import asyncpg
from telethon import TelegramClient

DB_URL = "postgresql://lina:lina_dev@localhost:5432/lina"
LINA_DIR = Path(__file__).parent.parent

API_ID = 35434942
API_HASH = "9f2a614fbf2e8cbfaf844561b7f43294"
SESSION_PATH = str(LINA_DIR / "tests" / "e2e" / "telegram" / ".sessions" / "daemon" / "lina_e2e")
CLINE_BOT = "@s_cline_bot"
LINA_BOT = "@s_lina_bot"

_pending = False

# Patrones de mensajes de cierre que NO deben enviarse a LINA
CLOSE_PATTERNS = re.compile(
    r'^(✅\s*)?(Orden\s*#\d+\s*)?(cerrada|completada|finalizada|closed|done|finished|ok)',
    re.IGNORECASE
)


def log(msg: str):
    ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


def is_close_message(text: str) -> bool:
    """Detecta si un mensaje es solo un cierre (sin contenido útil)."""
    stripped = text.strip()
    if len(stripped) < 15:
        return 'cerrada' in stripped or 'completada' in stripped or 'closed' in stripped.lower()
    return False


THINKING_PATTERNS = re.compile(
    r'^\s*(💭\s*)?__?Razonando__?\s*:?\s*',
    re.IGNORECASE
)


def extract_final_report(messages: list[tuple[int, str]]) -> str:
    """
    Extrae el reporte final de Cline.
    Estrategia: concatenar TODOS los mensajes sustanciales (sin thinking,
    sin tool calls internos), y devolver el bloque más informativo.
    """
    # Separar en bloques: thinking vs contenido
    content_blocks = []
    for mid, text in messages:
        if not text or len(text.strip()) < 3:
            continue
        stripped = text.strip()
        # Saltar thinking puro
        if THINKING_PATTERNS.match(stripped):
            continue
        if stripped.startswith('💭') or stripped.startswith('⚙️'):
            continue
        if is_close_message(stripped):
            continue
        content_blocks.append(stripped)

    if not content_blocks:
        return messages[-1][1] if messages else ""

    # Unir todo el contenido limpio
    combined = "\n\n".join(content_blocks)
    return combined


async def send_tg(tg: TelegramClient, target: str, text: str) -> bool:
    try:
        await tg.send_message(target, text)
        return True
    except Exception as e:
        log(f"  ⚠️ Error enviando a {target}: {e}")
        return False


async def wait_for_cline_response(tg: TelegramClient, order_id: int,
                                   timeout: int = 180,
                                   stable_window: float = 8.0
                                   ) -> tuple[list[tuple[int, str]], str] | None:
    """
    Espera la respuesta de Cline.
    Devuelve (sorted_messages, final_report).
    """
    log(f"  ⏳ Esperando respuesta de Cline para orden #{order_id}...")
    start = datetime.now(timezone.utc)
    entity = await tg.get_entity(CLINE_BOT)

    before = await tg.get_messages(entity, limit=1)
    last_known_id = before[0].id if before else 0

    messages: dict[int, tuple[str, datetime]] = {}
    last_update = None
    stable_since = None

    while (datetime.now(timezone.utc) - start).total_seconds() < timeout:
        await asyncio.sleep(2)
        try:
            msgs = await tg.get_messages(entity, limit=5, min_id=last_known_id)
        except Exception:
            continue

        if not msgs:
            if messages and stable_since:
                if (datetime.now(timezone.utc) - stable_since).total_seconds() >= stable_window:
                    break
            continue

        now = datetime.now(timezone.utc)
        for msg in msgs:
            if msg.sender_id == (await tg.get_me()).id:
                continue
            text = msg.text or ""

            if msg.id in messages:
                old_text, _ = messages[msg.id]
                if text != old_text:
                    messages[msg.id] = (text, now)
                    last_update = now
                    stable_since = None
                    log(f"  📝 Edit #{msg.id} ({len(text)} chars)")
            else:
                messages[msg.id] = (text, now)
                last_update = now
                stable_since = None
                last_known_id = max(last_known_id, msg.id)
                log(f"  💬 Nuevo msg #{msg.id} ({len(text)} chars)")

        if messages and last_update:
            stable_since = last_update

    if not messages:
        log(f"  ⏰ No se recibió respuesta para #{order_id}")
        return None

    sorted_msgs = sorted(messages.items(), key=lambda x: x[0])
    msg_list = [(mid, text) for mid, (text, _) in sorted_msgs]
    report = extract_final_report(msg_list)

    log(f"  ✅ Conversación: {len(msg_list)} msg(s), reporte={len(report)} chars")
    return (msg_list, report)


async def process_pending(conn, tg: TelegramClient):
    global _pending
    if _pending:
        return
    _pending = True
    try:
        rows = await conn.fetch(
            """SELECT id, command, notification
               FROM cline_commands WHERE status = 'pending'
                 AND command NOT LIKE '✅%'
               ORDER BY created_at ASC LIMIT 3"""
        )
        if not rows:
            return
        log(f"📋 {len(rows)} orden(es) pendiente(s)")
        for row in rows:
            cmd_id = row["id"]
            msg = f"📋 Orden #{cmd_id} de LINA"
            if row["notification"]:
                msg += f"\n{row['notification']}"
            msg += f"\n\n{row['command']}"

            if await send_tg(tg, CLINE_BOT, msg):
                await conn.execute(
                    "UPDATE cline_commands SET status='running', started_at=NOW() WHERE id=$1",
                    cmd_id,
                )
                log(f"  ✅ #{cmd_id} → CLINE")

                result = await wait_for_cline_response(tg, cmd_id)

                if result:
                    msg_list, report = result

                    # Guardar toda la conversación en DB
                    full = "\n\n---\n\n".join(text for mid, text in msg_list)
                    await conn.execute(
                        "UPDATE cline_commands SET status='completed', completed_at=NOW(), response=$1 WHERE id=$2",
                        full[:2000], cmd_id,
                    )

                    # Notificar a LINA con el reporte SUSTANCIAL
                    clean = report[:3800]
                    summary = f"📋 Orden #{cmd_id} — Reporte de CLINE:\n\n{clean}"
                    await send_tg(tg, LINA_BOT, summary)
                    log(f"  ✅ #{cmd_id} → LINA notificada ({len(clean)} chars)")
                else:
                    log(f"  ⚠️ #{cmd_id} sin respuesta — timeout")
            else:
                log(f"  ⚠️ #{cmd_id} no enviada")
            await asyncio.sleep(2)
    finally:
        _pending = False


async def reset_orphaned_running(conn) -> int:
    """Resetea órdenes en 'running' que quedaron colgadas (daemon reiniciado).

    Busca órdenes con status='running' que tengan más de 120s sin completarse.
    Las resetea a 'pending' para que sean reprocesadas.
    """
    rows = await conn.fetch(
        """UPDATE cline_commands
           SET status = 'pending',
               started_at = NULL,
               response = COALESCE(response || '\n\n⏰ Timeout — daemon reiniciado', '⏰ Timeout — daemon reiniciado')
           WHERE status = 'running'
             AND (started_at IS NULL OR started_at < NOW() - INTERVAL '120 seconds')
           RETURNING id"""
    )
    count = len(rows)
    if count:
        ids = [r["id"] for r in rows]
        log(f"  ♻️ {count} orden(es) colgadas reseteadas: {ids}")
    return count


async def main_loop():
    log("Iniciando daemon bidireccional LINA ↔ CLINE")

    conn = await asyncpg.connect(DB_URL, timeout=10)
    log("✅ Conectado a PostgreSQL")

    # Reprocesar órdenes colgadas antes de arrancar
    colgadas = await reset_orphaned_running(conn)
    if colgadas:
        log(f"  ♻️ {colgadas} orden(es) serán reprocesadas")
    else:
        log("  ✅ No hay órdenes colgadas")

    tg = TelegramClient(SESSION_PATH, API_ID, API_HASH)
    await tg.start()
    me = await tg.get_me()
    log(f"✅ Telegram conectado como {me.first_name}")

    def on_new_command(connection, pid, channel, payload):
        log(f"🔔 Nueva orden #{payload}")
        asyncio.create_task(process_pending(conn, tg))

    await conn.add_listener("cline_new_command", on_new_command)
    log("📡 LISTEN en cline_new_command")

    await process_pending(conn, tg)

    while True:
        await asyncio.sleep(300)


def main():
    asyncio.run(main_loop())


if __name__ == "__main__":
    main()
