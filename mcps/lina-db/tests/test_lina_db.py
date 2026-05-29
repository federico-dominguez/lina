"""Tests unitarios para lina-db MCP.

Todas las queries SQL se mockean vía monkeypatch en _execute,
por lo que no se requiere una instancia PostgreSQL real.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


# ─── fixture base ─────────────────────────────────────────────────────────────


@pytest.fixture()
def mock_execute(monkeypatch):
    """Mockea lina_db.server._execute para evitar conexión real a PostgreSQL."""
    mock = MagicMock(return_value=None)
    monkeypatch.setattr("lina_db.server._execute", mock)
    return mock


@pytest.fixture(autouse=True)
def silence_audit(monkeypatch):
    """Evita que _audit dispare queries reales durante los tests."""
    monkeypatch.setattr("lina_db.server._audit", MagicMock())


# ─── store_memory ─────────────────────────────────────────────────────────────


class TestStoreMemory:
    def test_insert_new_key(self, mock_execute):
        from lina_db.server import store_memory

        # Primera llamada (check existing) → None (no existe)
        # Segunda llamada (INSERT) → None
        mock_execute.side_effect = [None, None]
        assert store_memory("k1", "v1") == "ok"

    def test_update_existing_key(self, mock_execute):
        from lina_db.server import store_memory

        mock_execute.side_effect = [{"id": 1}, None]
        assert store_memory("k1", "v1") == "updated"

    def test_insert_with_ttl(self, mock_execute):
        from lina_db.server import store_memory

        mock_execute.side_effect = [None, None]
        assert store_memory("k2", "v2", ttl_seconds=3600) == "ok"

    def test_empty_key_raises(self, mock_execute):
        from lina_db.server import store_memory

        with pytest.raises(ValueError, match="key"):
            store_memory("  ", "value")

    def test_empty_value_raises(self, mock_execute):
        from lina_db.server import store_memory

        with pytest.raises(ValueError, match="value"):
            store_memory("key", "  ")

    def test_negative_ttl_raises(self, mock_execute):
        from lina_db.server import store_memory

        with pytest.raises(ValueError, match="ttl_seconds"):
            store_memory("key", "val", ttl_seconds=-1)


# ─── get_memory ───────────────────────────────────────────────────────────────


class TestGetMemory:
    def test_returns_value(self, mock_execute):
        from lina_db.server import get_memory

        mock_execute.return_value = {"value": "hello world"}
        assert get_memory("mykey") == "hello world"

    def test_raises_if_missing(self, mock_execute):
        from lina_db.server import get_memory

        mock_execute.return_value = None
        with pytest.raises(KeyError, match="mykey"):
            get_memory("mykey")

    def test_empty_key_raises(self, mock_execute):
        from lina_db.server import get_memory

        with pytest.raises(ValueError, match="key"):
            get_memory("")


# ─── search_memory ────────────────────────────────────────────────────────────


class TestSearchMemory:
    def test_returns_list(self, mock_execute):
        from lina_db.server import search_memory

        mock_execute.return_value = [{"key": "k", "value": "val", "updated_at": "2026-01-01"}]
        result = search_memory("val")
        assert len(result) == 1
        assert result[0]["key"] == "k"

    def test_empty_query_raises(self, mock_execute):
        from lina_db.server import search_memory

        with pytest.raises(ValueError, match="query"):
            search_memory("  ")

    def test_clamps_limit(self, mock_execute):
        from lina_db.server import search_memory

        mock_execute.return_value = []
        search_memory("query", limit=9999)
        # El limit en el SQL debe ser 100 (máximo)
        call_args = mock_execute.call_args
        assert 100 in call_args[0][1]


# ─── summarize_session ────────────────────────────────────────────────────────


class TestSummarizeSession:
    def test_returns_ok(self, mock_execute):
        from lina_db.server import summarize_session

        mock_execute.return_value = None
        assert summarize_session("20260529_1", "Sesión de prueba") == "ok"

    def test_empty_session_id_raises(self, mock_execute):
        from lina_db.server import summarize_session

        with pytest.raises(ValueError, match="session_id"):
            summarize_session("", "summary")

    def test_empty_summary_raises(self, mock_execute):
        from lina_db.server import summarize_session

        with pytest.raises(ValueError, match="summary"):
            summarize_session("sid", "  ")


# ─── get_last_sessions ────────────────────────────────────────────────────────


class TestGetLastSessions:
    def test_returns_list(self, mock_execute):
        from lina_db.server import get_last_sessions

        mock_execute.return_value = [{"session_id": "s1", "summary": "ok", "created_at": "now"}]
        result = get_last_sessions(3)
        assert len(result) == 1

    def test_clamps_limit(self, mock_execute):
        from lina_db.server import get_last_sessions

        mock_execute.return_value = []
        get_last_sessions(999)
        call_args = mock_execute.call_args
        assert 50 in call_args[0][1]


# ─── store_preference ─────────────────────────────────────────────────────────


class TestStorePreference:
    def test_insert_new(self, mock_execute):
        from lina_db.server import store_preference

        mock_execute.side_effect = [None, None]
        assert store_preference("lang", "es") == "ok"

    def test_update_existing(self, mock_execute):
        from lina_db.server import store_preference

        mock_execute.side_effect = [{"id": 1}, None]
        assert store_preference("lang", "en") == "updated"

    def test_empty_key_raises(self, mock_execute):
        from lina_db.server import store_preference

        with pytest.raises(ValueError, match="key"):
            store_preference("", "value")


# ─── get_preferences ──────────────────────────────────────────────────────────


class TestGetPreferences:
    def test_all_preferences(self, mock_execute):
        from lina_db.server import get_preferences

        mock_execute.return_value = [{"key": "lang", "value": "es", "updated_at": "now"}]
        result = get_preferences()
        assert result[0]["key"] == "lang"

    def test_filter_by_key(self, mock_execute):
        from lina_db.server import get_preferences

        mock_execute.return_value = [{"key": "lang", "value": "es", "updated_at": "now"}]
        result = get_preferences(key="lang")
        assert len(result) == 1

    def test_returns_empty_list_when_none(self, mock_execute):
        from lina_db.server import get_preferences

        mock_execute.return_value = None
        assert get_preferences() == []


# ─── get_audit_logs ───────────────────────────────────────────────────────────


class TestGetAuditLogs:
    def test_returns_list(self, mock_execute):
        from lina_db.server import get_audit_logs

        mock_execute.return_value = [{"tool": "store_memory", "args_json": {}, "result_summary": "ok", "created_at": "now"}]
        result = get_audit_logs(10)
        assert len(result) == 1
        assert result[0]["tool"] == "store_memory"

    def test_clamps_limit(self, mock_execute):
        from lina_db.server import get_audit_logs

        mock_execute.return_value = []
        get_audit_logs(9999)
        call_args = mock_execute.call_args
        assert 500 in call_args[0][1]

    def test_returns_empty_list_when_none(self, mock_execute):
        from lina_db.server import get_audit_logs

        mock_execute.return_value = None
        assert get_audit_logs() == []


# ─── _conn ────────────────────────────────────────────────────────────────────


class TestConn:
    def test_uses_env_url(self, monkeypatch):
        monkeypatch.setenv("LINA_DB_URL", "postgresql://test:test@localhost:5432/test")
        import importlib
        import lina_db.server as m

        importlib.reload(m)
        assert "test" in m.LINA_DB_URL

    def test_conn_calls_psycopg2(self, monkeypatch):
        import psycopg2

        mock_connect = MagicMock()
        monkeypatch.setattr(psycopg2, "connect", mock_connect)
        from lina_db.server import _conn

        _conn()
        mock_connect.assert_called_once()
