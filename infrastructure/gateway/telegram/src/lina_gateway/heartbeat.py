"""HeartbeatService — Pulso periódico de bots con detección de timeout.

Cada bot escribe su pulso cada N segundos en la tabla bot_heartbeat.
Si un bot deja de pulsear por > 60s, se marca como 'dead'.
Notifica en el grupo Comm cuando un bot se cae o se recupera.

Uso:
    hb = HeartbeatService(db_url, "lina", interval=30.0)
    await hb.start()  # background task
    # ...
    await hb.stop()
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import suppress

logger = logging.getLogger(__name__)


class HeartbeatService:
    """Servicio de heartbeat para un bot.

    Escribe un pulso en bot_heartbeat cada ``interval`` segundos.
    Si el bot deja de responder, se detecta vía TTL en la DB.
    """

    def __init__(
        self,
        db_url: str | None,
        bot_name: str,
        interval: float = 30.0,
    ) -> None:
        self._db_url = db_url
        self._bot_name = bot_name.lower()
        self._interval = interval
        self._task: asyncio.Task | None = None
        self._stopped = asyncio.Event()
        self._consecutive_failures = 0

    @property
    def is_running(self) -> bool:
        return self._task is not None and not self._task.done()

    async def start(self) -> None:
        """Inicia el loop de heartbeat en background."""
        if self._task is not None and not self._task.done():
            logger.warning("%s: heartbeat already running", self._bot_name)
            return
        self._stopped.clear()
        self._task = asyncio.create_task(
            self._run(),
            name=f"heartbeat-{self._bot_name}",
        )
        logger.info("%s: heartbeat started (interval=%ss)", self._bot_name, self._interval)

    async def stop(self) -> None:
        """Detiene el heartbeat."""
        self._stopped.set()
        if self._task is not None:
            self._task.cancel()
            with suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        logger.info("%s: heartbeat stopped", self._bot_name)

    async def _run(self) -> None:
        """Loop principal: pulsa cada N segundos."""
        if not self._db_url:
            logger.info("%s: no DB URL, heartbeat disabled", self._bot_name)
            return

        try:
            import asyncpg
        except ImportError:
            logger.warning("%s: asyncpg not installed, heartbeat disabled", self._bot_name)
            return

        while not self._stopped.is_set():
            try:
                conn = await asyncpg.connect(self._db_url, timeout=5)
                try:
                    await conn.execute(
                        """
                        INSERT INTO bot_heartbeat (bot_name, last_pulse, status, failures)
                        VALUES ($1, NOW(), 'alive', 0)
                        ON CONFLICT (bot_name)
                        DO UPDATE SET last_pulse = NOW(), status = 'alive', failures = 0
                        """,
                        self._bot_name,
                    )
                    # También resetear circuit breaker failures si hay mejora
                    await conn.execute(
                        """
                        UPDATE circuit_breaker
                        SET state = 'closed', failures = 0, opened_at = NULL, half_open_at = NULL
                        WHERE bot_name = $1 AND state IN ('half-open', 'closed')
                        """,
                        self._bot_name,
                    )
                    self._consecutive_failures = 0
                finally:
                    await conn.close()
            except Exception as exc:
                self._consecutive_failures += 1
                logger.warning(
                    "%s: heartbeat pulse failed (%d/%d): %s",
                    self._bot_name,
                    self._consecutive_failures,
                    3,
                    exc,
                )
                if self._consecutive_failures >= 3:
                    # Degradado: no se pudo pulsear 3 veces seguidas
                    logger.error(
                        "%s: heartbeat degraded after %d failures",
                        self._bot_name,
                        self._consecutive_failures,
                    )

            try:
                await asyncio.wait_for(
                    self._stopped.wait(),
                    timeout=self._interval,
                )
                break  # _stopped was set
            except TimeoutError:
                continue  # pulse again
