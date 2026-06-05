"""CircuitBreaker — Aísla bots que fallan repetidamente.

Estados:
  - closed: funcionando normal (0 fallos)
  - open: aislado (>= threshold fallos consecutivos)
  - half-open: permitiendo 1 request de prueba

El breaker se abre después de N fallos consecutivos.
Se cierra automáticamente después de 30s en estado open (→ half-open).
Si el request de prueba falla, vuelve a open.

Uso:
    cb = CircuitBreaker(db_url, "lina", threshold=5)
    if await cb.is_open():
        logger.warning("Circuit open, skipping")
        return
    try:
        # ... hacer cosa riesgosa ...
        await cb.record_success()
    except Exception:
        await cb.record_failure()
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

logger = logging.getLogger(__name__)


class CircuitBreaker:
    """Circuit breaker por bot con persistencia en DB."""

    def __init__(
        self,
        db_url: str | None,
        bot_name: str,
        threshold: int = 5,
        recovery_timeout: float = 30.0,
    ) -> None:
        self._db_url = db_url
        self._bot_name = bot_name.lower()
        self._threshold = threshold
        self._recovery_timeout = recovery_timeout

    async def is_open(self) -> bool:
        """Check if circuit breaker is open for this bot.

        Returns True if the bot should NOT be used (circuit open).
        If half-open and timeout expired, allows one request.
        """
        if not self._db_url:
            return False  # No DB = no circuit breaker

        try:
            import asyncpg
        except ImportError:
            return False

        try:
            conn = await asyncpg.connect(self._db_url, timeout=5)
            try:
                row = await conn.fetchrow(
                    "SELECT state, opened_at, half_open_at FROM circuit_breaker WHERE bot_name = $1",
                    self._bot_name,
                )

                if row is None:
                    return False  # No entry = closed

                state = row["state"]

                if state == "closed":
                    return False

                if state == "open":
                    # Check if recovery timeout expired → half-open
                    opened_at = row["opened_at"]
                    if (
                        opened_at
                        and (datetime.now(UTC) - opened_at).total_seconds()
                        >= self._recovery_timeout
                    ):
                        await conn.execute(
                            """
                            UPDATE circuit_breaker
                            SET state = 'half-open', half_open_at = NOW()
                            WHERE bot_name = $1 AND state = 'open'
                            """,
                            self._bot_name,
                        )
                        logger.info("%s: circuit → half-open (recovery attempt)", self._bot_name)
                        return False  # Allow one request (half-open)
                    return True  # Still open

                if state == "half-open":
                    # Already in half-open, allow the request
                    return False

                return False
            finally:
                await conn.close()
        except Exception as exc:
            logger.warning("%s: circuit breaker check failed: %s", self._bot_name, exc)
            return False  # Fail open: allow the request

    async def record_success(self) -> None:
        """Registra un éxito: resetea el contador de fallos."""
        if not self._db_url:
            return

        try:
            import asyncpg
        except ImportError:
            return

        try:
            conn = await asyncpg.connect(self._db_url, timeout=5)
            try:
                await conn.execute(
                    """
                    INSERT INTO circuit_breaker (bot_name, state, failures)
                    VALUES ($1, 'closed', 0)
                    ON CONFLICT (bot_name)
                    DO UPDATE SET state = 'closed', failures = 0,
                                  opened_at = NULL, half_open_at = NULL
                    """,
                    self._bot_name,
                )
                logger.debug("%s: circuit breaker reset (success)", self._bot_name)
            finally:
                await conn.close()
        except Exception as exc:
            logger.warning("%s: failed to record success: %s", self._bot_name, exc)

    async def record_failure(self) -> None:
        """Registra un fallo: incrementa contador, abre si threshold."""
        if not self._db_url:
            return

        try:
            import asyncpg
        except ImportError:
            return

        try:
            conn = await asyncpg.connect(self._db_url, timeout=5)
            try:
                # Incrementar fallos
                row = await conn.fetchrow(
                    """
                    INSERT INTO circuit_breaker (bot_name, state, failures, last_failure)
                    VALUES ($1, 'closed', 1, NOW())
                    ON CONFLICT (bot_name)
                    DO UPDATE SET
                        failures = circuit_breaker.failures + 1,
                        last_failure = NOW()
                    RETURNING failures
                    """,
                    self._bot_name,
                )

                failures = row["failures"] if row else 1
                logger.warning(
                    "%s: circuit breaker failure (%d/%d)",
                    self._bot_name,
                    failures,
                    self._threshold,
                )

                if failures >= self._threshold:
                    await conn.execute(
                        """
                        UPDATE circuit_breaker
                        SET state = 'open', opened_at = NOW()
                        WHERE bot_name = $1 AND state != 'open'
                        """,
                        self._bot_name,
                    )
                    logger.error(
                        "%s: CIRCUIT BREAKER OPEN (threshold=%d)",
                        self._bot_name,
                        self._threshold,
                    )
            finally:
                await conn.close()
        except Exception as exc:
            logger.warning("%s: failed to record failure: %s", self._bot_name, exc)

    async def reset(self) -> None:
        """Resetea el circuit breaker (para uso manual)."""
        if not self._db_url:
            return
        try:
            import asyncpg
        except ImportError:
            return
        try:
            conn = await asyncpg.connect(self._db_url, timeout=5)
            try:
                await conn.execute(
                    "DELETE FROM circuit_breaker WHERE bot_name = $1",
                    self._bot_name,
                )
                logger.info("%s: circuit breaker reset manually", self._bot_name)
            finally:
                await conn.close()
        except Exception as exc:
            logger.warning("%s: failed to reset circuit breaker: %s", self._bot_name, exc)
