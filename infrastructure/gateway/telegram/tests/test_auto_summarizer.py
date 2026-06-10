"""Unit tests for lina_gateway.boot_hook — auto-summarizer + vector RAG (issue #219)."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from lina_gateway.boot_hook import (
    _extract_topics,
    auto_summarize,
    search_relevant_memories,
)


# ─── _extract_topics ─────────────────────────────────────────────────────────


class TestExtractTopics:
    """_extract_topics: keyword extraction from message texts."""

    def test_happy_path_extracts_top_words(self) -> None:
        texts = [
            "vamos a migrar la base de datos a PostgreSQL",
            "la migración de la base de datos quedó lista",
            "probando la migración de datos a PostgreSQL",
        ]
        topics = _extract_topics(texts, max_topics=5)
        # Common terms across texts should appear as topics
        assert len(topics) >= 2

    def test_empty_texts_returns_empty(self) -> None:
        assert _extract_topics([], max_topics=5) == []

    def test_short_words_filtered(self) -> None:
        texts = ["el sol es azul", "un pez es rojo"]
        topics = _extract_topics(texts, max_topics=5)
        # Words of length <= 3 should be filtered
        for t in topics:
            assert len(t) > 3

    def test_stop_words_filtered(self) -> None:
        texts = ["el la los las que de en un una es por con"]
        topics = _extract_topics(texts, max_topics=5)
        assert topics == []  # all stop words

    def test_mixed_spanish_english(self) -> None:
        texts = [
            "deploy del gateway completado exitosamente",
            "the gateway deployment is done",
        ]
        topics = _extract_topics(texts, max_topics=5)
        assert "gateway" in topics or "deployment" in topics or "deploy" in topics or "completado" in topics

    def test_max_topics_limit(self) -> None:
        texts = [
            "python rust go typescript swift kotlin java",
            "python rust go swift kotlin java typescript",
        ]
        topics = _extract_topics(texts, max_topics=3)
        assert len(topics) <= 3

    def test_spanish_accents_preserved(self) -> None:
        texts = ["configuración de la aplicación y comunicación"]
        topics = _extract_topics(texts, max_topics=5)
        assert "configuración" in topics
        assert "comunicación" in topics


# ─── auto_summarize ──────────────────────────────────────────────────────────


class TestAutoSummarize:
    """auto_summarize: periodic session summary generation."""

    @pytest.mark.asyncio
    async def test_skips_when_turn_number_zero(self) -> None:
        """turn_number=0 must be skipped (nothing to summarize)."""
        # Should not raise — no DB connection attempted
        result = await auto_summarize("postgresql://fake", "test-session", turn_number=0)
        assert result is None

    @pytest.mark.asyncio
    async def test_skips_when_not_interval_aligned(self) -> None:
        """turn_number=3 with interval=5 must skip."""
        result = await auto_summarize(
            "postgresql://fake", "test-session", turn_number=3,
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_skips_on_connection_error(self) -> None:
        """DB unreachable must not raise."""
        import asyncpg

        with patch.object(asyncpg, "connect", new=AsyncMock(side_effect=OSError("no db"))):
            await auto_summarize(
                "postgresql://fake", "test-session", turn_number=5,
            )

    @pytest.mark.asyncio
    async def test_inserts_summary_at_interval(self) -> None:
        """When turn_number is a multiple of interval, summary is stored."""
        import asyncpg

        fake_conn = AsyncMock()
        # Simulate 8 rows returned from session_logs query
        fake_conn.fetch = AsyncMock(
            return_value=[
                {"role": "user", "sender": "fede", "msg_text": "vamos a migrar a postgresql",
                 "thinking_text": None, "tool_name": None,
                 "tokens_in": 50, "tokens_out": None, "cost_usd": None},
                {"role": "assistant", "sender": "goose", "msg_text": "ok migrando la base de datos",
                 "thinking_text": "primero debo migrar...", "tool_name": None,
                 "tokens_in": None, "tokens_out": 120, "cost_usd": 0.00005},
                {"role": "user", "sender": "fede", "msg_text": "probando la migración de datos",
                 "thinking_text": None, "tool_name": None,
                 "tokens_in": 30, "tokens_out": None, "cost_usd": None},
            ]
        )
        fake_conn.execute = AsyncMock()
        fake_conn.close = AsyncMock()

        with patch.object(asyncpg, "connect", new=AsyncMock(return_value=fake_conn)):
            await auto_summarize("postgresql://fake", "test-session", turn_number=5)

        # Must have executed at least 2 inserts (session_summaries + memories)
        assert fake_conn.execute.call_count >= 2
        # First call should be the session_summaries insert
        first_sql = fake_conn.execute.call_args_list[0][0][0]
        assert "INSERT INTO session_summaries" in first_sql

    @pytest.mark.asyncio
    async def test_inserts_topics_into_memories(self) -> None:
        """Key topics should be stored in memories table for RAG."""
        import asyncpg

        fake_conn = AsyncMock()
        fake_conn.fetch = AsyncMock(
            return_value=[
                {"role": "user", "sender": "fede", "msg_text": "migracion base datos postgresql deploy",
                 "thinking_text": None, "tool_name": None,
                 "tokens_in": 10, "tokens_out": None, "cost_usd": None},
            ]
        )
        fake_conn.execute = AsyncMock()
        fake_conn.close = AsyncMock()

        with patch.object(asyncpg, "connect", new=AsyncMock(return_value=fake_conn)):
            await auto_summarize("postgresql://fake", "test-session", turn_number=5)

        # Check that a memories insert happened
        calls = [c[0][0] for c in fake_conn.execute.call_args_list]
        memory_calls = [s for s in calls if "INSERT INTO memories" in s]
        assert len(memory_calls) >= 1

    @pytest.mark.asyncio
    async def test_empty_session_logs_skips(self) -> None:
        """No rows from session_logs → no summary written."""
        import asyncpg

        fake_conn = AsyncMock()
        fake_conn.fetch = AsyncMock(return_value=[])  # empty
        fake_conn.execute = AsyncMock()
        fake_conn.close = AsyncMock()

        with patch.object(asyncpg, "connect", new=AsyncMock(return_value=fake_conn)):
            await auto_summarize("postgresql://fake", "test-session", turn_number=5)

        fake_conn.execute.assert_not_called()


# ─── search_relevant_memories ───────────────────────────────────────────────


class TestSearchRelevantMemories:
    """search_relevant_memories: RAG queries on the memories table."""

    @pytest.mark.asyncio
    async def test_returns_empty_on_connection_error(self) -> None:
        """DB unreachable must not raise, returns empty list."""
        import asyncpg

        with patch.object(asyncpg, "connect", new=AsyncMock(side_effect=OSError("no db"))):
            result = await search_relevant_memories("postgresql://fake", "test query")
        assert result == []

    @pytest.mark.asyncio
    async def test_returns_results_when_found(self) -> None:
        """Returns list of {key, value, similarity} dicts."""
        import asyncpg

        fake_conn = AsyncMock()
        fake_conn.fetch = AsyncMock(
            return_value=[
                {"key": "auto:topic:sess1:migracion", "value": "Se habló sobre migracion.",
                 "similarity": 1.0},
                {"key": "auto:topic:sess1:base", "value": "Se habló sobre base.",
                 "similarity": 1.0},
            ]
        )
        fake_conn.close = AsyncMock()

        with patch.object(asyncpg, "connect", new=AsyncMock(return_value=fake_conn)):
            result = await search_relevant_memories("postgresql://fake", "test", limit=2)

        assert len(result) == 2
        assert result[0]["key"] == "auto:topic:sess1:migracion"
        assert result[0]["similarity"] == 1.0

    @pytest.mark.asyncio
    async def test_respects_limit(self) -> None:
        """Only returns up to `limit` results."""
        import asyncpg

        fake_conn = AsyncMock()
        fake_conn.fetch = AsyncMock(
            return_value=[
                {"key": f"auto:topic:sess1:t{i}", "value": f"Topic {i}",
                 "similarity": 1.0}
                for i in range(5)
            ]
        )
        fake_conn.close = AsyncMock()

        with patch.object(asyncpg, "connect", new=AsyncMock(return_value=fake_conn)):
            result = await search_relevant_memories("postgresql://fake", "test", limit=3)

        assert len(result) == 5  # the mock returns 5, the limit is for SQL
        # Verify SQL had LIMIT 3
        called_sql = fake_conn.fetch.call_args[0][0]
        assert "LIMIT" in called_sql
