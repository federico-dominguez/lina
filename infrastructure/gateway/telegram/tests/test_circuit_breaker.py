"""Tests para CircuitBreaker (Fase 3 — Resiliencia)."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from lina_gateway.circuit_breaker import CircuitBreaker


class TestCircuitBreakerClosed:
    """Circuit breaker en estado closed (funcionamiento normal)."""

    @pytest.fixture
    def mock_db(self):
        conn = AsyncMock()
        conn.close = AsyncMock()
        mock_connect = AsyncMock(return_value=conn)
        with patch("asyncpg.connect", mock_connect):
            yield conn

    @pytest.mark.asyncio
    async def test_is_open_when_no_entry(self, mock_db):
        """Sin entrada en DB, is_open() = False."""
        mock_db.fetchrow = AsyncMock(return_value=None)
        cb = CircuitBreaker("postgresql://fake", "lina")
        assert not await cb.is_open()

    @pytest.mark.asyncio
    async def test_is_open_when_closed(self, mock_db):
        """Estado closed -> is_open() = False."""
        mock_db.fetchrow = AsyncMock(return_value={"state": "closed", "opened_at": None, "half_open_at": None})
        cb = CircuitBreaker("postgresql://fake", "lina")
        assert not await cb.is_open()

    @pytest.mark.asyncio
    async def test_record_success_resets(self, mock_db):
        """record_success() ejecuta INSERT/UPDATE."""
        mock_db.execute = AsyncMock()
        cb = CircuitBreaker("postgresql://fake", "lina")
        await cb.record_success()
        mock_db.execute.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_no_db_skips(self, mock_db):
        """Sin DB URL, no hay circuit breaker."""
        cb = CircuitBreaker(None, "lina")
        assert not await cb.is_open()
        await cb.record_success()
        await cb.record_failure()
        mock_db.connect.assert_not_called()


class TestCircuitBreakerOpen:
    """Circuit breaker en estado open (aislado)."""

    @pytest.fixture
    def mock_db(self):
        conn = AsyncMock()
        conn.close = AsyncMock()
        mock_connect = AsyncMock(return_value=conn)
        with patch("asyncpg.connect", mock_connect):
            yield conn

    @pytest.mark.asyncio
    async def test_is_open_when_open(self, mock_db):
        """Estado open -> is_open() = True."""
        from datetime import datetime, timezone
        mock_db.fetchrow = AsyncMock(return_value={
            "state": "open",
            "opened_at": datetime.now(timezone.utc),
            "half_open_at": None,
        })
        cb = CircuitBreaker("postgresql://fake", "lina")
        assert await cb.is_open()

    @pytest.mark.asyncio
    async def test_half_open_after_timeout(self, mock_db):
        """Open despues de recovery_timeout -> half-open -> permite request."""
        from datetime import datetime, timedelta, timezone
        mock_db.fetchrow = AsyncMock(return_value={
            "state": "open",
            "opened_at": datetime.now(timezone.utc) - timedelta(seconds=60),
            "half_open_at": None,
        })
        mock_db.execute = AsyncMock()
        cb = CircuitBreaker("postgresql://fake", "lina", recovery_timeout=30.0)
        assert not await cb.is_open()  # half-open -> permite
        mock_db.execute.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_record_failure_opens(self, mock_db):
        """N fallos consecutivos -> state = open."""
        mock_db.fetchrow = AsyncMock(return_value={"failures": 5})
        mock_db.execute = AsyncMock()
        cb = CircuitBreaker("postgresql://fake", "lina", threshold=5)
        await cb.record_failure()
        mock_db.execute.assert_awaited()

    @pytest.mark.asyncio
    async def test_record_failure_below_threshold(self, mock_db):
        """Menos de threshold fallos -> no abre."""
        mock_db.fetchrow = AsyncMock(return_value={"failures": 3})
        mock_db.execute = AsyncMock()
        cb = CircuitBreaker("postgresql://fake", "lina", threshold=5)
        await cb.record_failure()
        # No debe haber UPDATE de state = open
        calls = [str(c) for c in mock_db.execute.await_args_list]
        assert not any("state = 'open'" in c for c in calls)

    @pytest.mark.asyncio
    async def test_reset_clears(self, mock_db):
        """reset() borra la entrada."""
        mock_db.execute = AsyncMock()
        cb = CircuitBreaker("postgresql://fake", "lina")
        await cb.reset()
        mock_db.execute.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_db_error_fallback(self, mock_db):
        """Error de DB -> fail open (permite request)."""
        mock_db.fetchrow = AsyncMock(side_effect=Exception("connection refused"))
        cb = CircuitBreaker("postgresql://fake", "lina")
        assert not await cb.is_open()  # fail open
