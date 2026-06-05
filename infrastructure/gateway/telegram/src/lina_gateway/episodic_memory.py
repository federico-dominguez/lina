"""EpisodicMemory — Memoria episódica de conversaciones con pgvector (Fase 4).

Almacena resúmenes de conversaciones con embeddings vectoriales
para búsqueda semántica. Recupera contexto relevante de conversaciones
pasadas para inyectarlo en prompts activos.

Usa la tabla knowledge_store (migración 010) para persistencia,
con source='conversation_memory' y embedding vector(1536) via pgvector.

Uso:
    memory = EpisodicMemory(db_url)
    await memory.store("conv-123", "lina", "Resumen...", ["tema1", "tema2"])
    similares = await memory.search("implementación de API REST", limit=3)
    context = await memory.get_context("cómo hago un endpoint?")
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_EMBEDDING_DIMS = 1536


class EpisodicMemory:
    """Memoria episódica con búsqueda semántica.

    Almacena resúmenes de conversaciones en knowledge_store con embeddings
    pgvector. Busca por similitud coseno para recuperar contexto relevante.
    """

    def __init__(self, db_url: str | None) -> None:
        self._db_url = db_url

    async def store(
        self,
        conversation_id: str,
        bot_name: str,
        summary: str,
        topics: list[str],
        turn_count: int = 0,
    ) -> int | None:
        """Almacena un resumen de conversación con embedding.

        Args:
            conversation_id: ID único de la conversación.
            bot_name: Nombre del bot que participó.
            summary: Resumen textual de la conversación.
            topics: Lista de temas cubiertos.
            turn_count: Cantidad de turnos en la conversación.

        Returns:
            ID del registro insertado, o None si falló.
        """
        if not self._db_url:
            return None

        try:
            import asyncpg
        except ImportError:
            logger.warning("asyncpg not available")
            return None

        # Generar embedding del resumen
        embedding = await self._generate_embedding(summary)

        try:
            conn = await asyncpg.connect(self._db_url, timeout=5)
            try:
                row = await conn.fetchrow(
                    """
                    INSERT INTO knowledge_store
                        (key, value, embedding, source, agent, tags)
                    VALUES ($1, $2, $3::vector, 'conversation_memory', $4, $5)
                    ON CONFLICT (key)
                    DO UPDATE SET
                        value = EXCLUDED.value,
                        embedding = EXCLUDED.embedding,
                        tags = EXCLUDED.tags,
                        updated_at = NOW()
                    RETURNING id
                    """,
                    f"conv:{conversation_id}",
                    summary,
                    embedding,
                    bot_name,
                    topics,
                )
                inserted_id = row["id"] if row else None
                logger.debug(
                    "Stored conversation memory %s (%s): id=%s",
                    conversation_id,
                    bot_name,
                    inserted_id,
                )
                return inserted_id
            finally:
                await conn.close()
        except Exception as exc:
            logger.warning("Failed to store conversation memory: %s", exc)
            return None

    async def search(
        self,
        query: str,
        limit: int = 5,
        bot_name: str | None = None,
    ) -> list[dict]:
        """Busca conversaciones similares por similitud semántica.

        Args:
            query: Texto de búsqueda.
            limit: Cantidad máxima de resultados.
            bot_name: Filtrar por bot (opcional).

        Returns:
            Lista de dicts con key, value, similarity, tags, created_at.
        """
        if not self._db_url:
            return []

        try:
            import asyncpg
        except ImportError:
            return []

        # Generar embedding de la query
        query_embedding = await self._generate_embedding(query)
        if not query_embedding:
            return []

        try:
            conn = await asyncpg.connect(self._db_url, timeout=5)
            try:
                if bot_name:
                    rows = await conn.fetch(
                        """
                        SELECT key, value,
                               1 - (embedding <=> $1::vector) AS similarity,
                               tags, created_at
                        FROM knowledge_store
                        WHERE source = 'conversation_memory'
                          AND agent = $3
                          AND embedding IS NOT NULL
                        ORDER BY embedding <=> $1::vector
                        LIMIT $2
                        """,
                        query_embedding,
                        limit,
                        bot_name,
                    )
                else:
                    rows = await conn.fetch(
                        """
                        SELECT key, value,
                               1 - (embedding <=> $1::vector) AS similarity,
                               tags, created_at, agent
                        FROM knowledge_store
                        WHERE source = 'conversation_memory'
                          AND embedding IS NOT NULL
                        ORDER BY embedding <=> $1::vector
                        LIMIT $2
                        """,
                        query_embedding,
                        limit,
                    )

                results = []
                for row in rows:
                    results.append(
                        {
                            "key": row["key"],
                            "value": row["value"],
                            "similarity": round(float(row["similarity"]), 4)
                            if row["similarity"]
                            else 0.0,
                            "tags": row.get("tags", []),
                            "bot_name": row.get("agent", ""),
                            "created_at": str(row["created_at"]) if row.get("created_at") else "",
                        }
                    )
                return results
            finally:
                await conn.close()
        except Exception as exc:
            logger.warning("Failed to search conversation memory: %s", exc)
            return []

    async def get_context(
        self,
        query: str,
        limit: int = 3,
        min_similarity: float = 0.6,
    ) -> str:
        """Obtiene contexto relevante de conversaciones similares.

        Formatea los resultados como texto listo para inyectar en prompts.

        Args:
            query: Consulta actual del usuario.
            limit: Cantidad de conversaciones a recuperar.
            min_similarity: Similaridad mínima (0-1) para incluir.

        Returns:
            Texto formateado con el contexto relevante, o string vacío.
        """
        results = await self.search(query, limit=limit)
        if not results:
            return ""

        # Filtrar por similaridad mínima
        results = [r for r in results if r["similarity"] >= min_similarity]
        if not results:
            return ""

        parts = ["📚 Contexto de conversaciones similares:"]
        for i, r in enumerate(results, 1):
            bot = r.get("bot_name", "?")
            tags = ", ".join(r.get("tags", [])) if r.get("tags") else ""
            summary = r["value"][:300]
            parts.append(f"\n{i}. [{bot}] (sim: {r['similarity']:.0%})")
            if tags:
                parts.append(f"   Temas: {tags}")
            parts.append(f"   {summary}")

        return "\n".join(parts)

    async def _generate_embedding(self, text: str) -> list[float]:
        """Genera embedding vía DeepSeek API.

        Falls back a zero vector si la API no está disponible.
        """
        import os

        api_key = os.environ.get("DEEPSEEK_API_KEY")
        if not api_key:
            return [0.0] * _EMBEDDING_DIMS

        try:
            import httpx

            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(
                    "https://api.deepseek.com/beta/embeddings",
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": "text-embedding-3-small",
                        "input": text[:8191],  # max tokens
                    },
                )
                resp.raise_for_status()
                data = resp.json()
                return data["data"][0]["embedding"]
        except Exception as exc:
            logger.warning("Embedding generation failed: %s", exc)
            return [0.0] * _EMBEDDING_DIMS
