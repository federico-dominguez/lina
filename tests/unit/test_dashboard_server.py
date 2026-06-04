"""Unit tests for lina-dashboard HTTP server."""

from __future__ import annotations

import json
import pytest

from lina_dashboard.collectors import (
    BotStatus,
    ContainerStatus,
    MCPStatus,
    DashboardSnapshot,
    DashboardCollector,
)
from lina_dashboard.server import DashboardServer, HTML_TEMPLATE


class TestHTMLTemplate:
    """HTML template contains all required sections."""

    def test_contains_all_sections(self):
        assert "bot" in HTML_TEMPLATE.lower()
        assert "Contenedores" in HTML_TEMPLATE
        assert "MCPs" in HTML_TEMPLATE
        # Each bot column has a column per bot
        assert "bot-grid" in HTML_TEMPLATE
        assert "bot-col" in HTML_TEMPLATE

    def test_has_api_endpoint(self):
        assert "/api/json" in HTML_TEMPLATE

    def test_has_auto_refresh(self):
        assert "setInterval(fetchData,30000)" in HTML_TEMPLATE

    def test_has_alert_visual(self):
        assert "alerts" in HTML_TEMPLATE
        assert "Contenedores caídos" in HTML_TEMPLATE or "contenedores caídos" in HTML_TEMPLATE

    def test_has_fetch_function(self):
        assert "fetchData" in HTML_TEMPLATE


class TestSnapshotSerialization:
    """DashboardSnapshot serializes/deserializes correctly."""

    def test_snapshot_to_json_roundtrip(self):
        snapshot = DashboardSnapshot(
            bots=[BotStatus(name="LINA", port=9093, online=True, session_count=5)],
            containers=[ContainerStatus(name="lina-db", state="running", status="Up", ports="5432")],
            mcps=[MCPStatus(name="lina-db", port=8106, online=True)],
            all_sessions=[{"session_id": "s1", "bot": "LINA"}],
            collected_at=1000.0,
        )
        d = snapshot.to_dict()
        j = json.dumps(d, default=str)
        loaded = json.loads(j)
        assert loaded["bots"][0]["name"] == "LINA"
        assert loaded["containers"][0]["state"] == "running"
        assert loaded["mcps"][0]["online"] is True
        assert len(loaded["sessions"]) == 1
        assert loaded["collected_at"] == 1000.0


class TestDashboardServer:
    """Test server internal logic."""

    async def test_get_snapshot_empty_initially(self):
        collector = DashboardCollector(bot_ports=[], docker_url="http://nope:2375", mcp_host="nope")
        srv = DashboardServer(collector, host="127.0.0.1", port=0)
        try:
            snapshot = await srv._get_snapshot()
            assert snapshot.collected_at == 0.0
            assert snapshot.bots == []
            assert snapshot.containers == []
            assert snapshot.mcps == []
            assert snapshot.all_sessions == []
        finally:
            await srv.stop()

    async def test_get_snapshot_with_data(self):
        collector = DashboardCollector(bot_ports=[], docker_url="http://nope:2375", mcp_host="nope")
        srv = DashboardServer(collector, host="127.0.0.1", port=0)
        try:
            snapshot = DashboardSnapshot(
                bots=[BotStatus(name="LINA", port=9093, online=True, session_count=2)],
                containers=[ContainerStatus(name="lina-db", state="running", status="Up", ports="5432")],
                mcps=[MCPStatus(name="lina-db", port=8106, online=True)],
                all_sessions=[{"session_id": "s1", "bot": "LINA"}],
                collected_at=1000.0,
            )
            async with srv._snapshot_lock:
                srv._snapshot = snapshot

            got = await srv._get_snapshot()
            assert got.bots[0].name == "LINA"
            assert got.bots[0].session_count == 2
            assert len(got.all_sessions) == 1

            # Verify JSON
            d = got.to_dict()
            j = json.dumps(d, default=str)
            loaded = json.loads(j)
            assert loaded["bots"][0]["online"] is True
        finally:
            await srv.stop()

    async def test_refresh_snapshot_no_network(self):
        """Refresh gracefully handles network errors."""
        collector = DashboardCollector(bot_ports=[9090], docker_url="http://nope:2375", mcp_host="nope")
        srv = DashboardServer(collector, host="127.0.0.1", port=0)
        try:
            await srv._refresh_snapshot()
            snapshot = await srv._get_snapshot()
            # Should have attempted collection with some results
            assert snapshot is not None
            # Bots should be offline (connection refused/timeout)
            assert len(snapshot.bots) == 1
            assert snapshot.bots[0].online is False
        finally:
            await srv.stop()
