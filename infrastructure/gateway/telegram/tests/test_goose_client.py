"""Tests for goosed SSE event parsing and GoosedClient."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from lina_gateway.goose_client import EventType, GoosedClient, _parse_event


class TestParseEvent:
    def test_ping(self):
        e = _parse_event({"type": "Ping"})
        assert e is not None
        assert e.event_type == EventType.PING

    def test_finish(self):
        e = _parse_event({"type": "Finish", "reason": "stop", "token_state": {}})
        assert e is not None
        assert e.event_type == EventType.FINISH
        assert e.finish_reason == "stop"

    def test_error(self):
        e = _parse_event({"type": "Error", "error": "something broke"})
        assert e is not None
        assert e.event_type == EventType.ERROR
        assert "something broke" in e.error

    def test_message_text(self):
        e = _parse_event(
            {
                "type": "Message",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "text", "text": "Hello!"}],
                },
                "token_state": {},
            }
        )
        assert e is not None
        assert e.event_type == EventType.MESSAGE
        assert e.role == "assistant"
        assert len(e.contents) == 1
        assert e.contents[0].content_type == "text"
        assert e.contents[0].text == "Hello!"

    def test_message_thinking(self):
        e = _parse_event(
            {
                "type": "Message",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "thinking", "thinking": "I'm reasoning"}],
                },
                "token_state": {},
            }
        )
        assert e is not None
        assert e.contents[0].content_type == "thinking"
        assert e.contents[0].thinking == "I'm reasoning"

    def test_unknown_type_returns_none(self):
        e = _parse_event({"type": "SomeFutureEventType"})
        assert e is None


# ─── ensure_session returns (session_id, is_new) ─────────────────────────────


def _mock_response(status_code: int, json_data: dict | None = None) -> MagicMock:
    """Build a minimal mock httpx response."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.is_success = 200 <= status_code < 300
    if json_data is not None:
        resp.json = MagicMock(return_value=json_data)
    resp.text = ""
    resp.raise_for_status = MagicMock()
    return resp


class TestEnsureSessionReturnType:
    """ensure_session must return a (str, bool) tuple in all paths."""

    @pytest.mark.asyncio
    async def test_existing_session_returns_is_new_false(self) -> None:
        client = GoosedClient(base_url="https://goosed:3000", secret="s")

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=_mock_response(200, {"id": "test"}))
        mock_client.post = AsyncMock(return_value=_mock_response(200, {}))
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        with patch("httpx.AsyncClient", return_value=mock_client):
            session_id, is_new = await client.ensure_session("test-session")

        assert session_id == "test-session"
        assert is_new is False

    @pytest.mark.asyncio
    async def test_new_session_returns_is_new_true(self) -> None:
        client = GoosedClient(base_url="https://goosed:3000", secret="s")

        not_found = _mock_response(404)
        created = _mock_response(200, {"id": "new-session"})
        resumed = _mock_response(200, {})
        resumed.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=not_found)
        # First post → agent/start, second post → agent/resume
        mock_client.post = AsyncMock(side_effect=[created, resumed])
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        with patch("httpx.AsyncClient", return_value=mock_client):
            session_id, is_new = await client.ensure_session("new-session")

        assert is_new is True
