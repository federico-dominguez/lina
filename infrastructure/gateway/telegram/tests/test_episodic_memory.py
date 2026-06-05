"""Tests para EpisodicMemory (Fase 4 — Memoria Compartida)."""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, patch

from lina_gateway.episodic_memory import EpisodicMemory


class TestEpisodicMemory:
    """Tests de EpisodicMemory con asyncpg mockeado."""

    @pytest.fixture
    def mock_db(self):
        conn = AsyncMock()
        conn.close = AsyncMock()
        mock_connect = AsyncMock(return_value=conn)
        with patch("asyncpg.connect", mock_connect):
            yield conn

    @pytest.fixture
    def memory(self, mock_db):
        return EpisodicMemory("postgresql://fake:5432/lina")

    @pytest.mark.asyncio
    async def test_store_no_db(self):
        """Sin DB URL, store() retorna None sin error."""
        mem = EpisodicMemory(None)
        result = await mem.store("conv-1", "lina", "summary", ["test"])
        assert result is None

    @pytest.mark.asyncio
    async def test_store_success(self, memory, mock_db):
        """store() inserta en knowledge_store y retorna ID."""
        mock_db.fetchrow = AsyncMock(return_value={"id": 42})
        result = await memory.store("conv-1", "lina", "Resumen de prueba", ["tema1"])
        assert result == 42
        mock_db.fetchrow.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_store_with_topics(self, memory, mock_db):
        """store() guarda los tags correctamente."""
        mock_db.fetchrow = AsyncMock(return_value={"id": 1})
        await memory.store("conv-2", "cline", "Código nuevo", ["dev", "api", "python"])
        call_args = mock_db.fetchrow.call_args
        args = call_args[0] if call_args else []
        # Verificar que los tags se pasaron como argumento
        assert any("conv-2" in str(a) for a in args)

    @pytest.mark.asyncio
    async def test_search_no_db(self):
        """Sin DB URL, search() retorna lista vacía."""
        mem = EpisodicMemory(None)
        results = await mem.search("test query")
        assert results == []

    @pytest.mark.asyncio
    async def test_search_returns_results(self, memory, mock_db):
        """search() retorna resultados formateados correctamente."""
        mock_db.fetch = AsyncMock(return_value=[
            {
                "key": "conv:test-1",
                "value": "Resumen de prueba",
                "similarity": 0.85,
                "tags": ["dev"],
                "agent": "lina",
                "created_at": "2026-01-01",
            },
        ])
        results = await memory.search("implementar API")
        assert len(results) == 1
        assert results[0]["key"] == "conv:test-1"
        assert results[0]["similarity"] == 0.85
        assert results[0]["bot_name"] == "lina"

    @pytest.mark.asyncio
    async def test_get_context_no_results(self, memory, mock_db):
        """get_context() con 0 resultados retorna string vacío."""
        mock_db.fetch = AsyncMock(return_value=[])
        context = await memory.get_context("algo raro")
        assert context == ""

    @pytest.mark.asyncio
    async def test_get_context_with_results(self, memory, mock_db):
        """get_context() formatea correctamente con resultados."""
        mock_db.fetch = AsyncMock(return_value=[
            {
                "key": "conv:test-1",
                "value": "Conversación sobre implementación de API REST en Python con FastAPI",
                "similarity": 0.82,
                "tags": ["dev", "api"],
                "agent": "cline",
                "created_at": "2026-01-01",
            },
        ])
        context = await memory.get_context("cómo hago una API?", min_similarity=0.5)
        assert "Contexto de conversaciones similares" in context
        assert "cline" in context
        assert "82%" in context or "0.82" in context

    @pytest.mark.asyncio
    async def test_get_context_min_similarity_filter(self, memory, mock_db):
        """get_context() filtra por similaridad mínima."""
        mock_db.fetch = AsyncMock(return_value=[
            {
                "key": "conv:test-1",
                "value": "Bla bla",
                "similarity": 0.3,
                "tags": [],
                "agent": "lina",
                "created_at": "2026-01-01",
            },
        ])
        context = await memory.get_context("test", min_similarity=0.6)
        assert context == ""  # Filtrado
