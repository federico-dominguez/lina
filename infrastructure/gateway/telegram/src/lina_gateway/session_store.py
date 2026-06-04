"""session_store.py — Persistencia y recuperación de session_id entre reinicios.

Resuelve el problema de que _session_id() siempre genera "lina-unified" fresco
en cada restart del gateway, impidiendo que goosed reanude la sesión anterior.

Estrategia (ADR-0137, fix permanente):
  1. Al crear una sesión en goosed, guardamos el session_id real en `preferences`
     con key = f"session_id:{chat_id}"  (chat_id del usuario).
  2. Al arrancar, _session_id() busca ese valor en la DB.
  3. ensure_session() usa ese ID persistido → goosed lo encuentra → lo reanuda.
  4. Mensajes se guardan bajo el mismo ID consistente.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_PREF_PREFIX = "session_id:"


async def load_session_id(db_url: str, chat_id: int) -> str | None:
    """Recupera el último session_id conocido para este chat desde PostgreSQL.

    Returns None si no hay session_id persistido (primera vez del usuario).
    """
    try:
        import asyncpg

        conn = await asyncpg.connect(db_url, timeout=5)
        try:
            row = await conn.fetchrow(
                "SELECT value FROM preferences WHERE key = $1",
                f"{_PREF_PREFIX}{chat_id}",
            )
            return row["value"] if row else None
        finally:
            await conn.close()
    except Exception as exc:
        logger.debug("session_store: load_session_id failed: %s", exc)
        return None


async def save_session_id(db_url: str, chat_id: int, session_id: str) -> None:
    """Persiste el session_id actual para este chat.

    Usa UPSERT (INSERT ON CONFLICT UPDATE) para ser idempotente.
    """
    try:
        import asyncpg

        conn = await asyncpg.connect(db_url, timeout=5)
        try:
            await conn.execute(
                """INSERT INTO preferences (key, value, updated_at)
                   VALUES ($1, $2, NOW())
                   ON CONFLICT (key) DO UPDATE SET value = $2, updated_at = NOW()""",
                f"{_PREF_PREFIX}{chat_id}",
                session_id,
            )
        finally:
            await conn.close()
    except Exception as exc:
        logger.debug("session_store: save_session_id failed: %s", exc)


async def load_last_session_id_any(db_url: str) -> str | None:
    """Recupera el session_id más reciente de cualquier chat.

    Útil para arrancar fresh: si no hay chat específico, al menos tenemos
    el último session_id conocido globalmente.
    """
    try:
        import asyncpg

        conn = await asyncpg.connect(db_url, timeout=5)
        try:
            row = await conn.fetchrow(
                """SELECT value FROM preferences
                   WHERE key LIKE 'session_id:%'
                   ORDER BY updated_at DESC
                   LIMIT 1"""
            )
            return row["value"] if row else None
        finally:
            await conn.close()
    except Exception as exc:
        logger.debug("session_store: load_last_session_id_any failed: %s", exc)
        return None
