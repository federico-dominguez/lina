"""Tests unitarios para lina-db MCP.

Todas las queries SQL se mockean vía monkeypatch en _execute,
por lo que no se requiere una instancia PostgreSQL real.
"""

from __future__ import annotations

from unittest.mock import MagicMock

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

        mock_execute.return_value = [
            {"tool": "store_memory", "args_json": {}, "result_summary": "ok", "created_at": "now"}
        ]
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


# ─── Token / cost metering (issue #61) ────────────────────────────────────────


class TestGetSessionCost:
    def test_returns_totals(self, mock_execute):
        from lina_db.server import get_session_cost

        mock_execute.return_value = {
            "turns": 3,
            "prompt_tokens_est": 150,
            "completion_tokens_est": 300,
            "total_tokens_est": 450,
            "cost_usd_est": 0.000126,
        }
        result = get_session_cost("telegram-123")
        assert result["session_id"] == "telegram-123"
        assert result["turns"] == 3
        assert result["cost_usd_est"] == 0.000126

    def test_no_data_returns_zeros(self, mock_execute):
        from lina_db.server import get_session_cost

        mock_execute.return_value = None
        result = get_session_cost("telegram-456")
        assert result["session_id"] == "telegram-456"
        # result dict is empty except session_id when no rows
        assert "turns" not in result or result["turns"] == 0


class TestGetDailyCost:
    def test_returns_list(self, mock_execute):
        from lina_db.server import get_daily_cost

        mock_execute.return_value = [
            {
                "day": "2026-05-31",
                "turns": 10,
                "prompt_tokens_est": 1000,
                "completion_tokens_est": 2000,
                "total_tokens_est": 3000,
                "cost_usd_est": 0.00084,
            }
        ]
        result = get_daily_cost(days=1)
        assert len(result) == 1
        assert result[0]["cost_usd_est"] == 0.00084

    def test_empty_returns_empty_list(self, mock_execute):
        from lina_db.server import get_daily_cost

        mock_execute.return_value = []
        assert get_daily_cost() == []

    def test_days_clamped_to_90(self, mock_execute):
        from lina_db.server import get_daily_cost

        mock_execute.return_value = []
        get_daily_cost(days=9999)  # Should not raise
        # Verify the SQL was called with n=90
        call_args = mock_execute.call_args[0]
        # Second positional arg is (n,)
        assert call_args[1] == (90,)


class TestGetDeepseekBalance:
    def test_no_api_key_returns_error(self, monkeypatch):
        from lina_db.server import get_deepseek_balance

        monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
        result = get_deepseek_balance()
        assert "error" in result
        assert "DEEPSEEK_API_KEY" in result["error"]

    def test_api_error_returns_error_dict(self, monkeypatch):
        import urllib.request

        from lina_db.server import get_deepseek_balance

        monkeypatch.setenv("DEEPSEEK_API_KEY", "fake-key")

        def _fail(*args, **kwargs):
            raise OSError("network error")

        monkeypatch.setattr(urllib.request, "urlopen", _fail)
        result = get_deepseek_balance()
        assert "error" in result


# ── Tests for platform.deepseek.com tools ─────────────────────────────────────

_PLATFORM_SUMMARY_RESPONSE = {
    "code": 0,
    "msg": "",
    "data": {
        "biz_code": 0,
        "biz_msg": "",
        "biz_data": {
            "normal_wallets": [{"currency": "USD", "balance": "9.9539719542", "token_estimation": "23699933"}],
            "bonus_wallets": [{"currency": "USD", "balance": "0", "token_estimation": "0"}],
            "monthly_costs": [{"currency": "USD", "amount": "5.0460280458"}],
            "monthly_token_usage": "222950661",
        },
    },
}

_PLATFORM_AMOUNT_RESPONSE = {
    "code": 0,
    "msg": "",
    "data": {
        "biz_data": {
            "total": [
                {
                    "model": "deepseek-v4-pro",
                    "usage": [
                        {"type": "PROMPT_CACHE_HIT_TOKEN", "amount": "23968256"},
                        {"type": "PROMPT_CACHE_MISS_TOKEN", "amount": "3684931"},
                        {"type": "RESPONSE_TOKEN", "amount": "407336"},
                        {"type": "REQUEST", "amount": "832"},
                    ],
                }
            ]
        }
    },
}

_PLATFORM_COST_RESPONSE = {
    "code": 0,
    "msg": "",
    "data": {
        "biz_data": [
            {
                "total": [
                    {
                        "model": "deepseek-v4-pro",
                        "usage": [
                            {"type": "PROMPT_CACHE_HIT_TOKEN", "amount": "0.0868849280000000"},
                            {"type": "PROMPT_CACHE_MISS_TOKEN", "amount": "1.6029449850000000"},
                            {"type": "RESPONSE_TOKEN", "amount": "0.3543823200000000"},
                        ],
                    }
                ]
            }
        ]
    },
}


def _make_fake_urlopen(response_data: dict):
    """Returns a fake urlopen that returns JSON response_data."""
    import io
    import json

    class _FakeResp:
        def read(self):
            return json.dumps(response_data).encode()

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

    def _fake_urlopen(req, timeout=None):
        return _FakeResp()

    return _fake_urlopen


class TestGetDeepseekUserSummary:
    def test_no_token_returns_error(self, monkeypatch):
        from lina_db.server import get_deepseek_user_summary

        monkeypatch.delenv("DEEPSEEK_PLATFORM_TOKEN", raising=False)
        result = get_deepseek_user_summary()
        assert "error" in result
        assert "DEEPSEEK_PLATFORM_TOKEN" in result["error"]

    def test_network_error_returns_error(self, monkeypatch):
        import urllib.request
        from lina_db.server import get_deepseek_user_summary

        monkeypatch.setenv("DEEPSEEK_PLATFORM_TOKEN", "fake-token")

        def _fail(*args, **kwargs):
            raise OSError("network error")

        monkeypatch.setattr(urllib.request, "urlopen", _fail)
        result = get_deepseek_user_summary()
        assert "error" in result

    def test_returns_parsed_balance(self, monkeypatch):
        import urllib.request
        from lina_db.server import get_deepseek_user_summary

        monkeypatch.setenv("DEEPSEEK_PLATFORM_TOKEN", "fake-token")
        monkeypatch.setattr(urllib.request, "urlopen", _make_fake_urlopen(_PLATFORM_SUMMARY_RESPONSE))

        result = get_deepseek_user_summary()
        assert "error" not in result
        assert result["balance_usd"] == "9.9539719542"
        assert result["monthly_cost_usd"] == "5.0460280458"
        assert result["monthly_token_usage"] == "222950661"


class TestGetDeepseekMonthlyUsage:
    def test_no_token_returns_error(self, monkeypatch):
        from lina_db.server import get_deepseek_monthly_usage

        monkeypatch.delenv("DEEPSEEK_PLATFORM_TOKEN", raising=False)
        result = get_deepseek_monthly_usage(year=2026, month=5)
        assert "error" in result

    def test_returns_token_breakdown(self, monkeypatch):
        import urllib.request
        from lina_db.server import get_deepseek_monthly_usage

        monkeypatch.setenv("DEEPSEEK_PLATFORM_TOKEN", "fake-token")
        monkeypatch.setattr(urllib.request, "urlopen", _make_fake_urlopen(_PLATFORM_AMOUNT_RESPONSE))

        result = get_deepseek_monthly_usage(year=2026, month=5)
        assert "error" not in result
        assert result["year"] == 2026
        assert result["month"] == 5
        pro = result["usage"]["deepseek-v4-pro"]
        assert pro["cache_hit_tokens"] == 23968256
        assert pro["cache_miss_tokens"] == 3684931
        assert pro["output_tokens"] == 407336
        assert pro["requests"] == 832


class TestGetDeepseekMonthlyCost:
    def test_no_token_returns_error(self, monkeypatch):
        from lina_db.server import get_deepseek_monthly_cost

        monkeypatch.delenv("DEEPSEEK_PLATFORM_TOKEN", raising=False)
        result = get_deepseek_monthly_cost(year=2026, month=5)
        assert "error" in result

    def test_returns_cost_breakdown(self, monkeypatch):
        import urllib.request
        from lina_db.server import get_deepseek_monthly_cost

        monkeypatch.setenv("DEEPSEEK_PLATFORM_TOKEN", "fake-token")
        monkeypatch.setattr(urllib.request, "urlopen", _make_fake_urlopen(_PLATFORM_COST_RESPONSE))

        result = get_deepseek_monthly_cost(year=2026, month=5)
        assert "error" not in result
        assert result["year"] == 2026
        pro = result["by_model"]["deepseek-v4-pro"]
        assert abs(pro["cache_hit_cost_usd"] - 0.086884928) < 1e-6
        assert abs(pro["cache_miss_cost_usd"] - 1.602944985) < 1e-6
        expected_total = round(0.086884928 + 1.602944985 + 0.354382320, 8)
        assert pro["total_usd"] == expected_total
        assert result["total_usd"] == expected_total


# ─── reasoning trace tools (issue #62) ───────────────────────────────────────


def _make_fake_conn(rows, col_names):
    """Return a fake psycopg2 context-manager connection with a preset cursor."""
    fake_cur = MagicMock()
    fake_cur.__enter__ = MagicMock(return_value=fake_cur)
    fake_cur.__exit__ = MagicMock(return_value=False)
    fake_cur.fetchall = MagicMock(return_value=rows)
    fake_cur.description = [(name,) for name in col_names]

    fake_conn = MagicMock()
    fake_conn.__enter__ = MagicMock(return_value=fake_conn)
    fake_conn.__exit__ = MagicMock(return_value=False)
    fake_conn.cursor = MagicMock(return_value=fake_cur)
    return fake_conn, fake_cur


class TestGetLastTraces:
    def test_returns_traces_for_session(self, monkeypatch):
        from lina_db.server import get_last_traces

        col_names = [
            "id", "session_id", "turn_number", "thinking_text",
            "prompt_hash", "model", "created_at",
        ]
        rows = [
            (1, "telegram-1", 2, "pensé en el problema", "abc123", "deepseek-v4-flash", "2026-05-31 10:00:00"),
            (2, "telegram-1", 1, "analicé las opciones", None, "deepseek-v4-flash", "2026-05-31 09:00:00"),
        ]
        fake_conn, _ = _make_fake_conn(rows, col_names)
        monkeypatch.setattr("lina_db.server._conn", lambda: fake_conn)

        result = get_last_traces("telegram-1", limit=5)
        assert len(result) == 2
        assert result[0]["session_id"] == "telegram-1"
        assert result[0]["thinking_text"] == "pensé en el problema"

    def test_clamps_limit_to_20(self, monkeypatch):
        from lina_db.server import get_last_traces

        col_names = ["id", "session_id", "turn_number", "thinking_text",
                     "prompt_hash", "model", "created_at"]
        fake_conn, fake_cur = _make_fake_conn([], col_names)
        monkeypatch.setattr("lina_db.server._conn", lambda: fake_conn)

        get_last_traces("telegram-1", limit=999)
        # The cursor.execute call should have used limit=20
        sql_call = fake_cur.execute.call_args[0]
        assert sql_call[1][1] == 20

    def test_truncates_long_thinking_text(self, monkeypatch):
        from lina_db.server import get_last_traces

        long_text = "x" * 2000
        col_names = ["id", "session_id", "turn_number", "thinking_text",
                     "prompt_hash", "model", "created_at"]
        rows = [(1, "telegram-1", 0, long_text, None, "deepseek-v4-flash", "2026-05-31")]
        fake_conn, _ = _make_fake_conn(rows, col_names)
        monkeypatch.setattr("lina_db.server._conn", lambda: fake_conn)

        result = get_last_traces("telegram-1")
        assert len(result[0]["thinking_text"]) <= 1015  # 1000 chars + "…[truncado]" suffix

    def test_db_error_returns_error_list(self, monkeypatch):
        from lina_db.server import get_last_traces

        monkeypatch.setattr("lina_db.server._conn", MagicMock(side_effect=Exception("no db")))
        result = get_last_traces("telegram-1")
        assert isinstance(result, list)
        assert "error" in result[0]


class TestSearchTraces:
    def test_returns_search_results(self, monkeypatch):
        from lina_db.server import search_traces

        col_names = ["id", "session_id", "turn_number", "thinking_text",
                     "model", "created_at", "rank"]
        rows = [(1, "telegram-1", 0, "Moodle attempt_id", "deepseek-v4-flash", "2026-05-31", 0.5)]
        fake_conn, _ = _make_fake_conn(rows, col_names)
        monkeypatch.setattr("lina_db.server._conn", lambda: fake_conn)

        result = search_traces("Moodle")
        assert len(result) == 1
        assert result[0]["rank"] == 0.5
        assert "Moodle" in result[0]["thinking_text"]

    def test_empty_query_returns_error(self, monkeypatch):
        from lina_db.server import search_traces

        result = search_traces("   ")
        assert isinstance(result, list)
        assert "error" in result[0]

    def test_db_error_returns_error_list(self, monkeypatch):
        from lina_db.server import search_traces

        monkeypatch.setattr("lina_db.server._conn", MagicMock(side_effect=Exception("no db")))
        result = search_traces("algo")
        assert isinstance(result, list)
        assert "error" in result[0]
