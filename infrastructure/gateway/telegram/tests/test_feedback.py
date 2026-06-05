"""Tests para FeedbackManager (Fase 4 — Feedback entre bots)."""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, patch

from lina_gateway.feedback import FeedbackManager


class TestFeedbackManager:
    """Tests de FeedbackManager con asyncpg mockeado."""

    @pytest.fixture
    def mock_db(self):
        conn = AsyncMock()
        conn.close = AsyncMock()
        mock_connect = AsyncMock(return_value=conn)
        with patch("asyncpg.connect", mock_connect):
            yield conn

    @pytest.fixture
    def fb(self, mock_db):
        return FeedbackManager("postgresql://fake:5432/lina")

    @pytest.mark.asyncio
    async def test_submit_no_db(self):
        """Sin DB URL, submit() retorna False."""
        fb = FeedbackManager(None)
        assert not await fb.submit("lina", "cline", "conv-1", 5, "Bien!")

    @pytest.mark.asyncio
    async def test_submit_invalid_rating(self, mock_db):
        """Rating inválido (fuera de 1-5) retorna False."""
        fb = FeedbackManager("postgresql://fake:5432/lina")
        assert not await fb.submit("lina", "cline", "conv-1", 0, "mal")
        assert not await fb.submit("lina", "cline", "conv-1", 6, "mal")

    @pytest.mark.asyncio
    async def test_submit_success(self, fb, mock_db):
        """submit() ejecuta INSERT correctamente."""
        mock_db.execute = AsyncMock()
        result = await fb.submit("lina", "cline", "conv-1", 5, "Excelente!")
        assert result is True
        mock_db.execute.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_get_bot_average_no_data(self, fb, mock_db):
        """Sin datos, get_bot_average() retorna 0."""
        mock_db.fetch = AsyncMock(return_value=[])
        stats = await fb.get_bot_average("lina")
        assert stats["avg_rating"] == 0.0
        assert stats["total"] == 0

    @pytest.mark.asyncio
    async def test_get_bot_average_with_data(self, fb, mock_db):
        """Con datos, get_bot_average() calcula promedio."""
        import json
        from datetime import datetime, timezone

        mock_db.fetch = AsyncMock(return_value=[
            {"value": json.dumps({"from_bot": "lina", "to_bot": "cline", "rating": 5, "comment": "ok", "conversation_id": "c1"}), "created_at": datetime.now(timezone.utc)},
            {"value": json.dumps({"from_bot": "goose", "to_bot": "cline", "rating": 4, "comment": "bien", "conversation_id": "c2"}), "created_at": datetime.now(timezone.utc)},
            {"value": json.dumps({"from_bot": "lina", "to_bot": "cline", "rating": 3, "comment": "regular", "conversation_id": "c3"}), "created_at": datetime.now(timezone.utc)},
        ])
        stats = await fb.get_bot_average("cline")
        assert stats["total"] == 3
        assert stats["avg_rating"] == 4.0  # (5+4+3)/3

    @pytest.mark.asyncio
    async def test_get_team_summary(self, fb, mock_db):
        """get_team_summary() devuelve string formateado."""
        import json
        from datetime import datetime, timezone

        mock_db.fetch = AsyncMock(return_value=[
            {"value": json.dumps({"from_bot": "lina", "to_bot": "cline", "rating": 5, "comment": "ok", "conversation_id": "c1"}), "created_at": datetime.now(timezone.utc)},
        ])
        summary = await fb.get_team_summary()
        assert "Feedback del equipo" in summary
        assert "s_lina_bot" in summary
        assert "s_cline_bot" in summary
        assert "s_goose_bot" in summary
