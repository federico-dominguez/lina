"""session_summarizer.py — Auto-summarización de sesiones al finalizar.

Cuando una sesión termina (Finish event del SSE), este módulo:
  1. Lee los últimos N mensajes de la sesión desde session_messages.
  2. Genera un resumen estructurado vía LinaDb.summarizeSessionSmart().
  3. Lo guarda en session_summaries para que get_smart_context() lo inyecte
     en el próximo arranque.
"""

from __future__ import annotations

import asyncio
import logging

logger = logging.getLogger(__name__)


async def auto_summarize_session(
    db_url: str,
    session_id: str,
    chat_id: int,
) -> None:
    """Genera y persiste un resumen de sesión automáticamente.

    Se ejecuta en background (fire-and-forget) cuando termina un turno.
    Lee los últimos mensajes de la sesión y usa summarizeSessionSmart
    para producir un resumen estructurado.

    Args:
        db_url:     PostgreSQL connection URL.
        session_id: ID de la sesión en goosed (ej: "20260603_52").
        chat_id:    Telegram chat ID (para logging).
    """
    try:
        import asyncpg

        conn = await asyncpg.connect(db_url, timeout=5)
        try:
            # 1. Leer los últimos 30 mensajes de la sesión
            rows = await conn.fetch(
                """SELECT role, content, turn_number
                   FROM (
                       SELECT role, content, turn_number
                       FROM session_messages
                       WHERE session_id = $1
                       ORDER BY turn_number DESC
                       LIMIT 30
                   ) sub
                   ORDER BY turn_number ASC""",
                session_id,
            )

            if not rows:
                logger.debug("summarizer: no messages for session %s, skipping", session_id)
                return

            messages = [
                {"role": r["role"], "content": r["content"]}
                for r in rows
            ]

            # 2. Construir el texto a resumir
            summary_text = "\n".join(
                f"{'👤' if m['role'] == 'user' else '🤖'} {m['content'][:2000]}"
                for m in messages[-20:]  # últimos 20 mensajes para resumen
            )

            # 3. Generar resumen inline (sin llamada externa, usamos la DB)
            # Extraemos palabras clave y construimos un resumen estructurado
            topics = _extract_topics(summary_text)
            facts = _extract_facts(summary_text)
            pending = _extract_pending(summary_text)
            raw_summary = _build_summary(summary_text, topics, facts, pending)

            # 4. Guardar en session_summaries (UPSERT)
            await conn.execute(
                """INSERT INTO session_summaries
                       (session_id, topics, facts, pending, raw_summary, updated_at)
                   VALUES ($1, $2, $3, $4, $5, NOW())
                   ON CONFLICT (session_id)
                   DO UPDATE SET
                       topics = $2,
                       facts = $3,
                       pending = $4,
                       raw_summary = $5,
                       updated_at = NOW()""",
                session_id,
                topics,
                facts,
                pending,
                raw_summary,
            )

            logger.info(
                "summarizer: session %s resumida (%d topics, %d facts, %d pending)",
                session_id,
                len(topics),
                len(facts),
                len(pending),
            )

        finally:
            await conn.close()

    except Exception as exc:
        logger.debug("summarizer: auto_summarize_session failed: %s", exc)


def _extract_topics(text: str) -> list[str]:
    """Extrae topics relevantes del texto de la conversación."""
    import re

    topics = set()

    # Detectar patrones comunes de temas
    patterns = [
        (r"ADR[- ]?\d+", lambda m: m.group(0)),  # ADR-0137
        (r"(?:feat|fix|chore|refactor)[/\w-]+", lambda m: m.group(0)),  # feat/agent-tagging
        (r"(?:issue|PR)\s*#?\d+", lambda m: m.group(0).replace(" ", "-")),  # issue #133
        (r"(?:docker|container)[a-z\s-]+", lambda m: "docker"),  # docker stuff
        (r"(?:gateway|bot|telegram)[a-z\s]+", lambda m: "gateway"),  # gateway
        (r"(?:postgres|sqlite|db)[a-z\s]+", lambda m: "database"),  # database
        (r"(?:mcp|tool)[a-z\s]+", lambda m: "MCP"),  # MCP tools
        (r"(?:deploy|build|ci)[a-z\s]+", lambda m: "CI/CD"),  # CI/CD
        (r"(?:cline|goose|lina)[a-z\s]+", lambda m: "agent-comms"),  # agent communication
        (r"(?:test|e2e)[a-z\s]+", lambda m: "testing"),  # testing
        (r"(?:github|git|branch|commit|merge|push)", lambda m: "git"),  # git
        (r"(?:moodle|utec|curso)[a-z\s]+", lambda m: "moodle"),  # moodle
        (r"(?:gns3|red|router)[a-z\s]+", lambda m: "GNS3"),  # GNS3
    ]

    for pattern, transform in patterns:
        for match in re.finditer(pattern, text, re.IGNORECASE):
            topic = transform(match).lower()
            if topic:
                topics.add(topic)

    return sorted(topics)[:8]  # máximo 8 topics


def _extract_facts(text: str) -> list[str]:
    """Extrae hechos/estados relevantes del texto."""
    import re

    facts = []

    # Detectar líneas con hechos: ✅, completado, instalado, etc.
    fact_patterns = [
        r"(?:✅|✔|✓)\s*(.+?)(?:\n|$)",
        r"(?:instalad[oa]|configurad[oa]|cread[oa]|implementad[oa])(?:\s+(exitosamente))?\s*(.+?)(?:\n|$)",
        r"(?:completad[oa]|finalizad[oa])\s*(.+?)(?:\n|$)",
    ]

    for pattern in fact_patterns:
        for match in re.finditer(pattern, text, re.IGNORECASE):
            fact = match.group(0).strip()[:200]
            if fact and fact not in facts:
                facts.append(fact)

    return facts[:10]  # máximo 10 hechos


def _extract_pending(text: str) -> list[str]:
    """Extrae tareas pendientes, TODOs, próximos pasos."""
    import re

    pending = []

    patterns = [
        r"(?:pendiente|PENDING|TODO|por hacer|falta|next|próximo|proximo)[:\s]+(.+?)(?:\n|$)",
        r"(?:falta|hace falta|necesit[oa]|requiere)\s+(.+?)(?:\n|$)",
    ]

    for pattern in patterns:
        for match in re.finditer(pattern, text, re.IGNORECASE):
            item = match.group(0).strip()[:200]
            if item and item not in pending:
                pending.append(item)

    return pending[:5]  # máximo 5 pendientes


def _build_summary(
    text: str,
    topics: list[str],
    facts: list[str],
    pending: list[str],
) -> str:
    """Construye un resumen estructurado en prosa."""
    # Extraer los últimos intercambios para contexto
    import re

    user_msgs = re.findall(r"👤 (.+?)(?=\n|$)", text)
    bot_msgs = re.findall(r"🤖 (.+?)(?=\n|$)", text)

    last_user = user_msgs[-1] if user_msgs else ""
    last_bot = bot_msgs[-1] if bot_msgs else ""

    summary_parts = [
        f"Topics: {', '.join(topics) if topics else 'general'}",
    ]

    if last_user:
        summary_parts.append(f"Último pedido del usuario: {last_user[:300]}")
    if last_bot:
        summary_parts.append(f"Última respuesta del bot: {last_bot[:300]}")

    if facts:
        summary_parts.append("Logros: " + " | ".join(f[:100] for f in facts[:3]))

    if pending:
        summary_parts.append("Pendientes: " + " | ".join(p[:100] for p in pending[:3]))

    return "\n".join(summary_parts)
