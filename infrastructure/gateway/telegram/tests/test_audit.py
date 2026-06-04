"""Unit tests for lina_gateway.commands.audit (issue #93).

Tests core logic (parsing, redaction, formatting) and the handle_audit
entry point with a mocked database.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from lina_gateway.commands.audit import (
    _elapsed,
    _format_audit_response,
    _format_row,
    _parse_filter,
    _redact_args,
    handle_audit,
)


# ─── _parse_filter ────────────────────────────────────────────────────────────


class TestParseFilter:
    def test_empty(self):
        assert _parse_filter("") == {}

    def test_mcp_only(self):
        result = _parse_filter("lina-db")
        assert result == {"mcp": "lina-db"}

    def test_since_hours(self):
        result = _parse_filter("since:2h")
        assert "since" in result
        assert result["since"].total_seconds() == 7200  # 2 hours

    def test_since_minutes(self):
        result = _parse_filter("since:30m")
        assert "since" in result
        assert result["since"].total_seconds() == 1800

    def test_since_days(self):
        result = _parse_filter("since:3d")
        assert "since" in result
        assert result["since"].total_seconds() == 259200

    def test_combined_mcp_and_since(self):
        result = _parse_filter("lina-github since:6h")
        assert result == {"mcp": "lina-github", "since": timedelta(hours=6)}

    def test_mcp_with_spaces(self):
        result = _parse_filter("lina secrets")
        assert result == {"mcp": "lina secrets"}

    def test_invalid_since_ignored(self):
        result = _parse_filter("since:abc")
        assert result == {"mcp": "since:abc"}  # parsed as mcp name


# ─── _redact_args ─────────────────────────────────────────────────────────────


class TestRedactArgs:
    def test_none_returns_empty(self):
        assert _redact_args(None) == {}

    def test_empty_dict(self):
        assert _redact_args({}) == {}

    def test_short_string_preserved(self):
        result = _redact_args({"key": "hello"})
        assert result == {"key": "hello"}

    def test_long_string_truncated(self):
        long_str = "a" * 100
        result = _redact_args({"key": long_str})
        assert result["key"].endswith("...")
        assert len(result["key"]) == 80

    def test_dict_value_truncated(self):
        result = _redact_args({"cfg": {"deep": "nested" * 20}})
        assert len(result["cfg"]) <= 80

    def test_none_value_preserved(self):
        result = _redact_args({"key": None})
        assert result == {"key": None}


# ─── _elapsed ─────────────────────────────────────────────────────────────────


class TestElapsed:
    def test_none(self):
        assert _elapsed(None) == ""

    def test_seconds_ago(self):
        ts = datetime.now(timezone.utc) - timedelta(seconds=30)
        result = _elapsed(ts)
        assert result == "hace 30s"

    def test_minutes_ago(self):
        ts = datetime.now(timezone.utc) - timedelta(minutes=5)
        result = _elapsed(ts)
        assert result == "hace 5m"

    def test_hours_ago(self):
        ts = datetime.now(timezone.utc) - timedelta(hours=3)
        result = _elapsed(ts)
        assert result == "hace 3h"

    def test_days_ago(self):
        ts = datetime.now(timezone.utc) - timedelta(days=2)
        result = _elapsed(ts)
        assert result == "hace 2d"

    def test_naive_datetime(self):
        ts = datetime.now() - timedelta(minutes=10)
        result = _elapsed(ts)
        assert result == "hace 10m"


# ─── _format_row ──────────────────────────────────────────────────────────────


class TestFormatRow:
    def test_basic_row(self):
        row = {
            "mcp": "lina-db",
            "tool": "store_memory",
            "args_json": {"key": "foo", "value": "bar"},
            "result_summary": "ok",
            "created_at": datetime.now(timezone.utc) - timedelta(minutes=2),
        }
        result = _format_row(1, row)
        assert "lina-db" in result
        assert "store_memory" in result
        assert "key=foo" in result
        assert "hace 2m" in result

    def test_secret_mcp_redacted(self):
        row = {
            "mcp": "lina-secrets",
            "tool": "get_secret",
            "args_json": {"secret_key": "super-secret-123", "path": "/etc/passwd"},
            "result_summary": "found",
            "created_at": datetime.now(timezone.utc),
        }
        result = _format_row(1, row)
        assert "<redacted>" in result
        assert "super-secret" not in result
        assert "/etc/passwd" not in result

    def test_no_args(self):
        row = {
            "mcp": "lina-db",
            "tool": "ping",
            "args_json": None,
            "result_summary": "pong",
            "created_at": datetime.now(timezone.utc),
        }
        result = _format_row(1, row)
        assert "ping" in result
        assert "pong" in result


# ─── _format_audit_response ───────────────────────────────────────────────────


class TestFormatAuditResponse:
    def test_empty_response(self):
        result = _format_audit_response([], page=1, total_pages=1, filters={})
        assert "No se encontraron acciones" in result

    def test_with_rows(self):
        rows = [
            {
                "mcp": "lina-db",
                "tool": "store_memory",
                "args_json": {},
                "result_summary": "ok",
                "created_at": datetime.now(timezone.utc) - timedelta(minutes=1),
            }
        ]
        result = _format_audit_response(rows, page=1, total_pages=1, filters={})
        assert "Auditoría" in result
        assert "lina-db" in result
        assert "1." in result  # numbered

    def test_shows_mcp_filter(self):
        result = _format_audit_response(
            [], page=1, total_pages=1, filters={"mcp": "lina-github"}
        )
        assert "lina-github" in result

    def test_shows_since_filter(self):
        result = _format_audit_response(
            [],
            page=1,
            total_pages=1,
            filters={"since": timedelta(hours=6)},
        )
        assert "6h" in result


# ─── handle_audit ─────────────────────────────────────────────────────────────


class FakeTg:
    """Minimal Telegram client stub that records sent messages."""

    def __init__(self) -> None:
        self.sent: list[tuple[int, str]] = []

    async def send_message(self, chat_id: int, html: str) -> int:
        self.sent.append((chat_id, html))
        return 42


class TestHandleAudit:
    @pytest.mark.asyncio
    async def test_no_db_url(self):
        tg = FakeTg()
        await handle_audit(chat_id=123, text="", tg=tg, db_url=None)
        assert len(tg.sent) == 1
        assert "no configurada" in tg.sent[0][1]

    @pytest.mark.asyncio
    async def test_db_query_error(self):
        tg = FakeTg()
        with patch("lina_gateway.commands.audit._query_audit", side_effect=Exception("DB down")):
            await handle_audit(chat_id=123, text="", tg=tg, db_url="postgresql://fake")
        assert len(tg.sent) == 1
        assert "Error" in tg.sent[0][1]

    @pytest.mark.asyncio
    async def test_successful_query(self):
        tg = FakeTg()
        mock_rows = [
            {
                "mcp": "lina-db",
                "tool": "store_memory",
                "args_json": "{}",
                "result_summary": "ok",
                "created_at": datetime.now(timezone.utc) - timedelta(minutes=5),
            },
            {
                "mcp": "lina-github",
                "tool": "search_issues",
                "args_json": '{"query": "bug"}',
                "result_summary": "3 results",
                "created_at": datetime.now(timezone.utc) - timedelta(hours=1),
            },
        ]
        with patch("lina_gateway.commands.audit._query_audit", AsyncMock(return_value=mock_rows)):
            await handle_audit(chat_id=123, text="lina-github", tg=tg, db_url="postgresql://fake")
        assert len(tg.sent) == 1
        msg = tg.sent[0][1]
        assert "Auditoría" in msg
        assert "lina-db" in msg
        assert "lina-github" in msg
        assert "store_memory" in msg
        assert "search_issues" in msg
