"""FeedbackManager — Evaluación entre bots con ratings (Fase 4).

Los bots pueden evaluar las respuestas de otros bots, generando
un sistema de retroalimentación continua para mejora del equipo.

Usa la tabla knowledge_store con source='feedback' para persistencia.

Uso:
    fb = FeedbackManager(db_url)
    await fb.submit("lina", "cline", "conv-123", 5, "Excelente código")
    avg = await fb.get_bot_average("cline")
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime

logger = logging.getLogger(__name__)


class FeedbackManager:
    """Gestión de feedback entre bots.

    Almacena evaluaciones de un bot a otro después de una interacción.
    Proporciona estadísticas agregadas por bot.
    """

    def __init__(self, db_url: str | None) -> None:
        self._db_url = db_url

    async def submit(
        self,
        from_bot: str,
        to_bot: str,
        conversation_id: str,
        rating: int,
        comment: str = "",
    ) -> bool:
        """Registra una evaluación de un bot a otro.

        Args:
            from_bot: Bot que evalúa.
            to_bot: Bot evaluado.
            conversation_id: ID de la conversación.
            rating: Puntuación 1-5.
            comment: Comentario opcional.

        Returns:
            True si se guardó correctamente.
        """
        if not self._db_url:
            return False

        if not 1 <= rating <= 5:
            logger.warning("Invalid rating %d (must be 1-5)", rating)
            return False

        try:
            import asyncpg
        except ImportError:
            return False

        value = json.dumps(
            {
                "from_bot": from_bot,
                "to_bot": to_bot,
                "rating": rating,
                "comment": comment,
                "conversation_id": conversation_id,
            }
        )

        try:
            conn = await asyncpg.connect(self._db_url, timeout=5)
            try:
                await conn.execute(
                    """
                    INSERT INTO knowledge_store
                        (key, value, source, agent, tags)
                    VALUES ($1, $2, 'feedback', $3, $4)
                    ON CONFLICT (key)
                    DO UPDATE SET value = EXCLUDED.value, updated_at = NOW()
                    """,
                    f"feedback:{from_bot}:{to_bot}:{conversation_id}:{datetime.now(UTC).timestamp()}",
                    value,
                    to_bot,
                    [f"rating:{rating}", f"from:{from_bot}"],
                )
                logger.info(
                    "Feedback: %s → %s (rating=%d): %s",
                    from_bot,
                    to_bot,
                    rating,
                    comment[:50],
                )
                return True
            finally:
                await conn.close()
        except Exception as exc:
            logger.warning("Failed to submit feedback: %s", exc)
            return False

    async def get_bot_average(self, bot_name: str) -> dict:
        """Obtiene el rating promedio de un bot.

        Args:
            bot_name: Nombre del bot.

        Returns:
            Dict con avg_rating, total, last_5 ratings.
        """
        if not self._db_url:
            return {"avg_rating": 0.0, "total": 0, "last_ratings": []}

        try:
            import asyncpg
        except ImportError:
            return {"avg_rating": 0.0, "total": 0, "last_ratings": []}

        try:
            conn = await asyncpg.connect(self._db_url, timeout=5)
            try:
                # Get recent feedback for this bot
                rows = await conn.fetch(
                    """
                    SELECT value, created_at
                    FROM knowledge_store
                    WHERE source = 'feedback' AND agent = $1
                    ORDER BY created_at DESC
                    LIMIT 50
                    """,
                    bot_name,
                )

                if not rows:
                    return {"avg_rating": 0.0, "total": 0, "last_ratings": []}

                ratings = []
                for row in rows:
                    try:
                        data = json.loads(row["value"])
                        ratings.append(
                            {
                                "rating": data["rating"],
                                "from_bot": data.get("from_bot", "?"),
                                "comment": data.get("comment", ""),
                                "created_at": str(row["created_at"]),
                            }
                        )
                    except (json.JSONDecodeError, KeyError):
                        continue

                if not ratings:
                    return {"avg_rating": 0.0, "total": 0, "last_ratings": []}

                avg = sum(r["rating"] for r in ratings) / len(ratings)

                return {
                    "avg_rating": round(avg, 2),
                    "total": len(ratings),
                    "last_ratings": ratings[:5],
                }
            finally:
                await conn.close()
        except Exception as exc:
            logger.warning("Failed to get bot average: %s", exc)
            return {"avg_rating": 0.0, "total": 0, "last_ratings": []}

    async def get_team_summary(self) -> str:
        """Obtiene un resumen del equipo con ratings promedio.

        Returns:
            String formateado para mostrar en Telegram.
        """
        bots = ["lina", "cline", "goose"]
        parts = ["📊 **Feedback del equipo:**"]
        for bot in bots:
            stats = await self.get_bot_average(bot)
            if stats["total"] > 0:
                parts.append(
                    f"  • @s_{bot}_bot: ⭐ {stats['avg_rating']:.1f}/5 "
                    f"({stats['total']} evaluaciones)"
                )
            else:
                parts.append(f"  • @s_{bot}_bot: ⭐ sin evaluaciones aún")

        return "\n".join(parts)
