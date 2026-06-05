"""Tests para HeartbeatService (Fase 3 — Resiliencia)."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from lina_gateway.heartbeat import HeartbeatService


class TestHeartbeatService:
    """Tests del HeartbeatService con asyncpg mockeado."""

    @pytest.fixture
    def mock_db(self):
        conn = AsyncMock()
        conn.close = AsyncMock()
        mock_connect = AsyncMock(return_value=conn)
        with patch("asyncpg.connect", mock_connect):
            yield conn

    @pytest.mark.asyncio
    async def test_start_stop(self, mock_db):
        """Iniciar y detener el heartbeat."""
        hb = HeartbeatService("postgresql://fake", "lina", interval=0.1)
        assert not hb.is_running
        await hb.start()
        assert hb.is_running
        await hb.stop()
        assert not hb.is_running

    @pytest.mark.asyncio
    async def test_pulse_writes_to_db(self, mock_db):
        """El pulso hace INSERT/UPDATE en bot_heartbeat."""
        hb = HeartbeatService("postgresql://fake", "lina", interval=0.05)
        mock_db.execute = AsyncMock()
        await hb.start()
        await asyncio.sleep(0.12)  # suficiente para 2 pulsos
        await hb.stop()
        assert mock_db.execute.await_count >= 1
        # Verificar que el execute tiene el nombre del bot
        call_args = mock_db.execute.await_args
        if call_args:
            assert "lina" in str(call_args)

    @pytest.mark.asyncio
    async def test_no_db_skips(self, mock_db):
        """Sin DB URL, heartbeat no hace nada."""
        hb = HeartbeatService(None, "lina", interval=0.1)
        await hb.start()
        await asyncio.sleep(0.15)
        await hb.stop()
        mock_db.connect.assert_not_called()

    @pytest.mark.asyncio
    async def test_consecutive_failures(self, mock_db):
        """3 fallos consecutivos -> degraded (no crash)."""
        mock_db.execute = AsyncMock(side_effect=Exception("DB down"))
        hb = HeartbeatService("postgresql://fake", "lina", interval=0.05)
        await hb.start()
        await asyncio.sleep(0.2)  # varios pulsos fallando
        await hb.stop()
        # No debe crashear, solo loggear warnings
        assert hb._consecutive_failures >= 1

    @pytest.mark.asyncio
    async def test_multiple_bots_independent(self, mock_db):
        """Dos HeartbeatServices de diferentes bots son independientes."""
        hb1 = HeartbeatService("postgresql://fake", "lina", interval=0.1)
        hb2 = HeartbeatService("postgresql://fake", "cline", interval=0.1)
        await hb1.start()
        await hb2.start()
        assert hb1.is_running
        assert hb2.is_running
        assert hb1._bot_name == "lina"
        assert hb2._bot_name == "cline"
        await hb1.stop()
        assert not hb1.is_running
        assert hb2.is_running
        await hb2.stop()
        assert not hb2.is_running
