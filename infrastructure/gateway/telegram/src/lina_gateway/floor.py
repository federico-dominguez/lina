"""
FloorTokenManager — Token de turno conversacional.

Controla qué bot tiene la palabra en una conversación multi-bot.
Previene que los bots se pisen al hablar usando un sistema de
turnos con timeout automático y cola FIFO de mensajes.

Uso:
    floor = FloorTokenManager(db_url)
    async with floor.acquire("lina", conv_id, timeout=30.0) as token:
        if token.granted:
            # responder...
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, UTC

logger = logging.getLogger(__name__)

_DEFAULT_FLOOR_TIMEOUT = 30.0  # segundos
_CONTEXT_MESSAGE_LIMIT = 10    # últimos N mensajes para contexto acumulativo


@dataclass
class FloorToken:
    """Resultado de adquirir el token de turno."""

    granted: bool                     # True = este bot tiene la palabra
    conversation_id: str              # UUID de la conversación
    active_bot: str | None = None  # qué bot tiene el token (si no se concedió)
    reason: str = ""                  # 'ok' | 'busy' | 'error'
    token_id: int = 0                 # ID del registro en conversation_floor


@dataclass
class ContextMessage:
    """Un mensaje del contexto acumulativo."""

    from_bot: str
    to_bot: str
    message: str
    created_at: str


class FloorTokenManager:
    """Gestiona los turnos de conversación entre bots.

    Thread-safe por diseño: cada operación DB es una transacción atómica.
    """

    def __init__(self, db_url: str | None = None) -> None:
        self._db_url = db_url
        self._lock = asyncio.Lock()  # previene condiciones de carrera locales
        self._enabled = bool(db_url)  # sin DB = modo bypass (sin turnos)

    # ── Adquisición de token ──────────────────────────────────────────────────

    async def try_acquire(
        self,
        bot_name: str,
        conversation_id: str | None = None,
        *,
        timeout: float = _DEFAULT_FLOOR_TIMEOUT,
    ) -> FloorToken:
        """Intenta adquirir el token de turno para *bot_name*.

        Args:
            bot_name:        nombre del bot ('lina', 'cline', 'goose')
            conversation_id: UUID de la conversación (None = auto-generado)
            timeout:         segundos hasta que expire el turno

        Returns:
            FloorToken con granted=True si se obtuvo el turno.
        """
        if not self._enabled:
            return FloorToken(granted=True, conversation_id=conversation_id or "bypass")

        if conversation_id is None:
            conversation_id = str(uuid.uuid4())

        async with self._lock:
            try:
                import asyncpg

                conn = await asyncpg.connect(self._db_url, timeout=5)
                try:
                    # Verificar si hay un floor activo
                    active = await conn.fetchrow(
                        """SELECT id, active_bot, expires_at
                           FROM conversation_floor
                           WHERE conversation_id = $1
                             AND released_at IS NULL
                           FOR UPDATE""",  # lock fila para evitar race
                        conversation_id,
                    )

                    now = datetime.now(UTC)

                    if active:
                        # Hay un floor activo
                        if active["active_bot"] == bot_name:
                            # El mismo bot ya tiene el token — renovar
                            await conn.execute(
                                """UPDATE conversation_floor
                                   SET expires_at = $1
                                   WHERE id = $2""",
                                now + timedelta(seconds=timeout),
                                active["id"],
                            )
                            logger.debug(
                                "Floor renovado: bot=%s conv=%s expires_at=%s",
                                bot_name, conversation_id, now + timedelta(seconds=timeout),
                            )
                            return FloorToken(
                                granted=True,
                                conversation_id=conversation_id,
                                active_bot=bot_name,
                                reason="renewed",
                                token_id=active["id"],
                            )
                        else:
                            # Otro bot tiene el token — ver si expiró
                            expires = active["expires_at"]
                            if expires.tzinfo is None:
                                expires = expires.replace(tzinfo=UTC)
                            if now >= expires:
                                # Token expirado — reasignar
                                await conn.execute(
                                    """UPDATE conversation_floor
                                       SET released_at = $1, release_reason = 'timeout'
                                       WHERE id = $2""",
                                    now,
                                    active["id"],
                                )
                                # Crear nuevo floor
                                new_id = await conn.fetchval(
                                    """INSERT INTO conversation_floor
                                       (active_bot, conversation_id, expires_at)
                                       VALUES ($1, $2, $3)
                                       RETURNING id""",
                                    bot_name,
                                    conversation_id,
                                    now + timedelta(seconds=timeout),
                                )
                                logger.info(
                                    "Floor REASIGNADO por timeout: %s → %s conv=%s",
                                    active["active_bot"], bot_name, conversation_id,
                                )
                                return FloorToken(
                                    granted=True,
                                    conversation_id=conversation_id,
                                    active_bot=bot_name,
                                    reason="timeout_reassigned",
                                    token_id=new_id,
                                )
                            else:
                                # Token ocupado por otro bot
                                logger.debug(
                                    "Floor DENEGADO: %s tiene el turno (expira en %.1fs)",
                                    active["active_bot"],
                                    (expires - now).total_seconds(),
                                )
                                return FloorToken(
                                    granted=False,
                                    conversation_id=conversation_id,
                                    active_bot=active["active_bot"],
                                    reason="busy",
                                )

                    # No hay floor activo — crear uno nuevo
                    new_id = await conn.fetchval(
                        """INSERT INTO conversation_floor
                           (active_bot, conversation_id, expires_at)
                           VALUES ($1, $2, $3)
                           RETURNING id""",
                        bot_name,
                        conversation_id,
                        now + timedelta(seconds=timeout),
                    )
                    logger.debug(
                        "Floor CREADO: bot=%s conv=%s timeout=%.1fs",
                        bot_name, conversation_id, timeout,
                    )
                    return FloorToken(
                        granted=True,
                        conversation_id=conversation_id,
                        active_bot=bot_name,
                        reason="ok",
                        token_id=new_id,
                    )

                finally:
                    await conn.close()
            except Exception as exc:
                logger.error("Floor acquire error: %s", exc)
                # Fallback: conceder token para no bloquear la conversación
                return FloorToken(
                    granted=True,
                    conversation_id=conversation_id or "error-fallback",
                    reason=f"error_fallback:{exc}",
                )

    # ── Liberación de token ───────────────────────────────────────────────────

    async def release(
        self,
        token_id: int,
        conversation_id: str,
        bot_name: str,
        reason: str = "voluntary",
    ) -> None:
        """Libera el token de turno voluntariamente."""
        if not self._enabled:
            return

        try:
            import asyncpg

            conn = await asyncpg.connect(self._db_url, timeout=5)
            try:
                await conn.execute(
                    """UPDATE conversation_floor
                       SET released_at = NOW(), release_reason = $1
                       WHERE id = $2 AND released_at IS NULL""",
                    reason,
                    token_id,
                )
                logger.debug(
                    "Floor LIBERADO: bot=%s conv=%s reason=%s",
                    bot_name, conversation_id, reason,
                )
            finally:
                await conn.close()
        except Exception as exc:
            logger.error("Floor release error: %s", exc)

    # ── Cola de mensajes FIFO ─────────────────────────────────────────────────

    async def enqueue_message(
        self,
        conversation_id: str,
        from_bot: str,
        to_bot: str,
        message: str,
    ) -> int | None:
        """Mete un mensaje en la cola para procesamiento posterior."""
        if not self._enabled:
            return None

        try:
            import asyncpg

            conn = await asyncpg.connect(self._db_url, timeout=5)
            try:
                msg_id = await conn.fetchval(
                    """INSERT INTO conversation_messages
                       (conversation_id, from_bot, to_bot, message)
                       VALUES ($1, $2, $3, $4)
                       RETURNING id""",
                    conversation_id, from_bot, to_bot, message,
                )
                logger.debug(
                    "Mensaje ENCOLADO: %s → %s conv=%s id=%s",
                    from_bot, to_bot, conversation_id, msg_id,
                )
                return msg_id
            finally:
                await conn.close()
        except Exception as exc:
            logger.error("Enqueue error: %s", exc)
            return None

    async def ack_message(self, msg_id: int) -> None:
        """Marca un mensaje como procesado (ack)."""
        if not self._enabled:
            return

        try:
            import asyncpg

            conn = await asyncpg.connect(self._db_url, timeout=5)
            try:
                await conn.execute(
                    """UPDATE conversation_messages
                       SET processed_at = NOW(), ack_at = NOW()
                       WHERE id = $1""",
                    msg_id,
                )
            finally:
                await conn.close()
        except Exception as exc:
            logger.error("Ack error: %s", exc)

    async def get_pending_messages(
        self,
        conversation_id: str,
        to_bot: str,
        *,
        limit: int = 10,
    ) -> list[dict]:
        """Obtiene mensajes pendientes para *to_bot* en la conversación.

        Retorna los mensajes no procesados, ordenados por creación (FIFO).
        """
        if not self._enabled:
            return []

        try:
            import asyncpg

            conn = await asyncpg.connect(self._db_url, timeout=5)
            try:
                rows = await conn.fetch(
                    """SELECT id, from_bot, message, created_at
                       FROM conversation_messages
                       WHERE conversation_id = $1
                         AND to_bot = $2
                         AND processed_at IS NULL
                       ORDER BY created_at ASC
                       LIMIT $3""",
                    conversation_id, to_bot, limit,
                )
                return [dict(r) for r in rows]
            finally:
                await conn.close()
        except Exception as exc:
            logger.error("Get pending error: %s", exc)
            return []

    # ── Contexto acumulativo ──────────────────────────────────────────────────

    async def get_context_messages(
        self,
        conversation_id: str,
        *,
        limit: int = _CONTEXT_MESSAGE_LIMIT,
    ) -> list[ContextMessage]:
        """Recupera los últimos N mensajes de una conversación para contexto.

        Útil para inyectar en el prompt de los bots y mantener coherencia.
        """
        if not self._enabled:
            return []

        try:
            import asyncpg

            conn = await asyncpg.connect(self._db_url, timeout=5)
            try:
                rows = await conn.fetch(
                    """SELECT from_bot, to_bot, message, created_at
                       FROM conversation_messages
                       WHERE conversation_id = $1
                       ORDER BY created_at DESC
                       LIMIT $2""",
                    conversation_id, limit,
                )
                return [
                    ContextMessage(
                        from_bot=r["from_bot"],
                        to_bot=r["to_bot"],
                        message=r["message"],
                        created_at=r["created_at"].isoformat() if r["created_at"] else "",
                    )
                    for r in reversed(rows)  # oldest first
                ]
            finally:
                await conn.close()
        except Exception as exc:
            logger.error("Context error: %s", exc)
            return []

    # ── Utilidades ────────────────────────────────────────────────────────────

    async def get_active_floor(self, conversation_id: str) -> dict | None:
        """Devuelve información del floor activo, o None si no hay."""
        if not self._enabled:
            return None

        try:
            import asyncpg

            conn = await asyncpg.connect(self._db_url, timeout=5)
            try:
                row = await conn.fetchrow(
                    """SELECT id, active_bot, acquired_at, expires_at
                       FROM conversation_floor
                       WHERE conversation_id = $1
                         AND released_at IS NULL""",
                    conversation_id,
                )
                return dict(row) if row else None
            finally:
                await conn.close()
        except Exception as exc:
            logger.error("Get active floor error: %s", exc)
            return None

    @property
    def is_enabled(self) -> bool:
        return self._enabled
