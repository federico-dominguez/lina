"""Tests for /health/status endpoint in observe.py."""

from __future__ import annotations

import json
import time
import pytest

from lina_gateway.observe import ObserveServer, EventStore


@pytest.fixture
def observer():
    """Create an ObserveServer with no DB for testing."""
    obs = ObserveServer(port=0, db_url=None, agent="test-bot")
    return obs


class TestHealthStatusEndpoint:
    """Test the /health/status endpoint."""

    @pytest.mark.asyncio
    async def test_returns_valid_json(self, observer):
        """A GET /health/status returns valid JSON."""
        from lina_gateway.observe import ObserveServer

        # Manually trigger start to set _start_ts
        await observer.start()

        # Simulate HTTP request handling - build what _serve_health_status returns
        # We call the method directly via a helper since we can test the response building
        writer = _MockWriter()
        await observer._serve_health_status(writer)

        # Parse the response body
        body_bytes = writer.body
        data = json.loads(body_bytes)

        # Assertions
        assert data["status"] == "ok"
        assert data["agent"] == "test-bot"
        assert isinstance(data["uptime_seconds"], (int, float))
        assert data["uptime_seconds"] >= 0
        assert data["port"] == 0

        # WebSocket section
        assert "websocket" in data
        assert "rooms" in data["websocket"]
        assert "clients" in data["websocket"]
        assert isinstance(data["websocket"]["rooms_detail"], dict)

        # Database section
        assert "database" in data
        assert data["database"]["connected"] is False  # No DB configured
        assert data["database"]["configured"] is False

        # Events section
        assert "events" in data
        assert "total" in data["events"]
        assert "per_session" in data["events"]
        assert data["events"]["total"] == 0

        # Sessions section
        assert "sessions" in data
        assert "active" in data["sessions"]
        assert "agent_mappings" in data["sessions"]

        # Timestamp
        assert "timestamp" in data
        assert isinstance(data["timestamp"], (int, float))

        await observer.stop()

    @pytest.mark.asyncio
    async def test_uptime_increases(self, observer):
        """Uptime should increase between calls."""
        await observer.start()
        writer1 = _MockWriter()
        await observer._serve_health_status(writer1)
        data1 = json.loads(writer1.body)

        await asyncio.sleep(0.01)

        writer2 = _MockWriter()
        await observer._serve_health_status(writer2)
        data2 = json.loads(writer2.body)

        # Uptime should have advanced or at least stayed the same
        # (0.1s rounding means 10ms may not show visibly)
        assert data2["uptime_seconds"] >= data1["uptime_seconds"]
        # No events were pushed, so event counts stay equal
        assert data2["events"]["total"] == data1["events"]["total"]
        # Timestamps should reflect real time passing
        assert data2["timestamp"] > data1["timestamp"]
        await observer.stop()

    @pytest.mark.asyncio
    async def test_status_degraded_with_db_url_but_no_conn(self, observer):
        """When DB is configured but disconnected, status should be 'degraded'."""
        # Create observer with a DB URL that won't connect
        obs = ObserveServer(port=0, db_url="postgresql://nope:5432/nonexistent", agent="test-bot")
        await obs.start()

        writer = _MockWriter()
        await obs._serve_health_status(writer)
        data = json.loads(writer.body)

        # DB is configured but not connected → degraded
        assert data["status"] == "degraded"
        assert data["database"]["configured"] is True
        assert data["database"]["connected"] is False

        await obs.stop()

    @pytest.mark.asyncio
    async def test_with_push_events(self, observer):
        """After pushing events, the event count should reflect them."""
        await observer.start()

        # Push some events
        observer.push_event("session-1", "user_message", "Hola")
        observer.push_event("session-1", "thinking", "Pensando...")
        observer.push_event("session-2", "user_message", "Test")

        writer = _MockWriter()
        await observer._serve_health_status(writer)
        data = json.loads(writer.body)

        assert data["events"]["total"] == 3
        assert "session-1" in data["events"]["per_session"]
        assert "session-2" in data["events"]["per_session"]
        assert data["events"]["per_session"]["session-1"] == 2
        assert data["events"]["per_session"]["session-2"] == 1

        await observer.stop()

    @pytest.mark.asyncio
    async def test_route_dispatch(self, observer):
        """Test that /health/status is routed correctly through _handle_http."""
        import asyncio

        await observer.start()

        # Build a raw HTTP request for /health/status
        raw_request = b"GET /health/status HTTP/1.1\r\nHost: localhost\r\n\r\n"

        # Use a mock reader/writer pair
        reader = asyncio.StreamReader()
        reader.feed_data(raw_request)
        reader.feed_eof()

        writer = _MockWriter()
        await observer._handle_connection(reader, writer)

        # Should get a 200 response
        assert writer.status_code == 200
        data = json.loads(writer.body)
        assert data["status"] == "ok"

        await observer.stop()

    @pytest.mark.asyncio
    async def test_unknown_route_returns_404(self, observer):
        """Test that unknown routes still return 404."""
        import asyncio

        await observer.start()

        raw_request = b"GET /nonexistent HTTP/1.1\r\nHost: localhost\r\n\r\n"
        reader = asyncio.StreamReader()
        reader.feed_data(raw_request)
        reader.feed_eof()

        writer = _MockWriter()
        await observer._handle_connection(reader, writer)

        assert writer.status_code == 404
        assert b"not found" in writer.body

        await observer.stop()


class _MockWriter:
    """Minimal mock for asyncio.StreamWriter to capture responses.

    Each response is written as a single chunk (headers + body),
    and drain() parses the latest one. `body` contains only the
    body bytes after the HTTP headers.
    """

    def __init__(self):
        self.status_code = 0
        self.body = b""
        self._closed = False

    def write(self, data):
        if isinstance(data, str):
            data = data.encode()
        # Each call is a full HTTP response, store directly
        self._raw = data

    async def drain(self):
        text = self._raw.decode("utf-8", errors="replace")
        # Parse status code
        if text.startswith("HTTP/1.1 200"):
            self.status_code = 200
        elif text.startswith("HTTP/1.1 404"):
            self.status_code = 404
        elif text.startswith("HTTP/1.1 400"):
            self.status_code = 400
        else:
            self.status_code = 0
        # Extract body after headers
        if "\r\n\r\n" in text:
            _, body_text = text.split("\r\n\r\n", 1)
            self.body = body_text.encode()

    async def wait_closed(self):
        pass

    def close(self):
        self._closed = True


# Need asyncio for uptime test
import asyncio
