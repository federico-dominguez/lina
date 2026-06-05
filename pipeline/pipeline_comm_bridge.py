"""
pipeline_comm_bridge — Envía mensaje a bot vía comm_messages DB y espera respuesta COMPLETA.

Comunicación 100% por DB (comm_messages tabla). El Comm Bridge (lina-comm-bridge.service)
recoge los INSERTs (sender='goose', destination=bot_name), los rutea al goosed del bot
correspondiente via SSE, y el bot responde insertando en comm_messages:
  (sender=bot_name, destination='goose', message=respuesta, status='sent')

La finalización se detecta por silencio: SILENCE_TIMEOUT segundos sin mensajes nuevos
del bot = terminó.
"""

import asyncio, time
import asyncpg

DB_DSN = "postgresql://lina:lina_dev@localhost:5432/lina"

POLL_INTERVAL = 2       # segundos entre polls
SILENCE_TIMEOUT = 90    # segundos sin mensajes nuevos del bot = terminó
MAX_WAIT = 600          # timeout absoluto máximo

# Mapeo de nombres de bots a sus nombres en comm_messages
BOTS = {
    "lina":  "lina",
    "cline": "cline",
    "gemma": "gemma",
    "goose": "goose",
}

SENDER = "goose"  # El pipeline se ejecuta como goose


def send(bot_name: str, text: str, timeout: int = MAX_WAIT, last_n: int = 0) -> str:
    """
    Envía mensaje a un bot y espera sus respuestas completas.

    Args:
        bot_name: Nombre del bot ('lina', 'cline', 'gemma', 'goose')
        text: Mensaje a enviar
        timeout: Timeout máximo en segundos (default 600)
        last_n: Si > 0, devuelve solo los últimos N mensajes del bot

    Returns:
        Texto de todas las respuestas concatenadas, o cadena vacía si error/timeout
    """
    return asyncio.run(_send(bot_name, text, timeout, last_n))


async def _send(bot_name: str, text: str, timeout: int, last_n: int = 0) -> str:
    dest = BOTS.get(bot_name)
    if not dest:
        return f"ERROR: Bot '{bot_name}' desconocido"

    conn = await asyncpg.connect(DB_DSN)
    try:
        # ── Send message ────────────────────────────────────────────────
        # El Comm Bridge detecta INSERTs con destination=bot_name, status='sent'
        # y los rutea al goosed correspondiente via SSE.
        await conn.execute(
            "INSERT INTO comm_messages (sender, destination, message, status) "
            "VALUES ($1, $2, $3, 'sent')",
            SENDER, dest, text
        )
        sent_time = time.time()
        print(f"  📤 → {dest} ({len(text)} chars)")

        # ── Poll for responses ──────────────────────────────────────────
        # El bot responde con: sender=bot_name, destination='goose', status='sent'
        seen_ids = set()
        messages = []       # (id, text)
        last_new_time = time.time()
        deadline = time.time() + timeout

        while time.time() < deadline:
            await asyncio.sleep(POLL_INTERVAL)

            # Fetch new messages from bot addressed to goose
            rows = await conn.fetch(
                """SELECT id, message, created_at
                   FROM comm_messages
                   WHERE sender = $1
                     AND destination = $2
                     AND created_at > to_timestamp($3)
                   ORDER BY id ASC""",
                dest, SENDER, sent_time
            )

            got_new = False
            for row in rows:
                msg_id = row["id"]
                if msg_id in seen_ids:
                    continue
                seen_ids.add(msg_id)
                msg_text = (row["message"] or "").strip()
                if msg_text:
                    messages.append((msg_id, msg_text))
                    got_new = True
                    print(f"    📥 msg#{msg_id} ({len(msg_text)} chars)")

            if got_new:
                last_new_time = time.time()
            else:
                elapsed_since_last = time.time() - last_new_time
                if elapsed_since_last >= SILENCE_TIMEOUT and messages:
                    # Silence detected = bot finished
                    messages.sort(key=lambda x: x[0])
                    if last_n:
                        non_thinking = [m for m in messages if not m[1].startswith(("💭", "Razonando", "razonando"))]
                        filtered = non_thinking[-last_n:] if len(non_thinking) >= last_n else messages[-last_n:]
                    else:
                        filtered = messages
                    total = sum(len(m[1]) for m in messages)
                    print(f"  ✅ {dest} completado ({len(seen_ids)} msgs, {total} chars, last={len(filtered)})")
                    await conn.close()
                    return "\n".join(m[1] for m in filtered)

        # ── Timeout ─────────────────────────────────────────────────────
        filtered = messages[-last_n:] if last_n else messages
        total = sum(len(m[1]) for m in filtered)
        print(f"  ⚠️ Timeout ({timeout}s). {len(filtered)}/{len(messages)} msgs, {total} chars")
        await conn.close()
        return "\n".join(m[1] for m in filtered)

    except Exception as e:
        print(f"ERROR: {e}")
        try:
            await conn.close()
        except Exception:
            pass
        return ""


if __name__ == "__main__":
    import sys
    bot = sys.argv[1] if len(sys.argv) > 1 else "lina"
    msg = " ".join(sys.argv[2:]) or "Decime quién sos en una línea"
    print(f"\n🔬 → {bot}: {msg}\n")
    resp = send(bot, msg, timeout=120)
    print(f"\n📝 ({len(resp)} chars): {resp[:300]}")
