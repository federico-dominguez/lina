"""Tests for goosed SSE event parsing."""

import json

from lina_gateway.goose_client import EventType, _parse_event


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
        e = _parse_event({
            "type": "Message",
            "message": {
                "role": "assistant",
                "content": [{"type": "text", "text": "Hello!"}],
            },
            "token_state": {},
        })
        assert e is not None
        assert e.event_type == EventType.MESSAGE
        assert e.role == "assistant"
        assert len(e.contents) == 1
        assert e.contents[0].content_type == "text"
        assert e.contents[0].text == "Hello!"

    def test_message_thinking(self):
        e = _parse_event({
            "type": "Message",
            "message": {
                "role": "assistant",
                "content": [{"type": "thinking", "thinking": "I'm reasoning"}],
            },
            "token_state": {},
        })
        assert e is not None
        assert e.contents[0].content_type == "thinking"
        assert e.contents[0].thinking == "I'm reasoning"

    def test_unknown_type_returns_none(self):
        e = _parse_event({"type": "SomeFutureEventType"})
        assert e is None
