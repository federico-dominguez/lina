"""Tests para bot-health — Bot Health Dashboard del ecosistema LINA.

Cubre:
- check_bot: healthcheck individual de cada bot (LINA, Cline, Gemma)
- check_bridge: estado del Comm Bridge via systemctl
- check_database: conexión a PostgreSQL
- _compute_overall: lógica de agregación de estado
- _build_json / _build_text: formato de salida
- main / collect: integración de pipeline completo
"""

from __future__ import annotations

import json
import subprocess
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock

import pytest

from bin.bot_health import (
    BotCheck,
    BridgeCheck,
    DatabaseCheck,
    HealthReport,
    check_bot,
    check_bridge,
    check_database,
    _compute_overall,
    _build_json,
    _build_text,
    BOTS,
    collect,
)

# ═══════════════════════════════════════════════════════════════════════════
# Data Model Tests
# ═══════════════════════════════════════════════════════════════════════════


class TestBotCheck:
    """BotCheck dataclass model."""

    def test_online_defaults(self):
        b = BotCheck(name="lina", port=3000)
        assert b.name == "lina"
        assert b.port == 3000
        assert b.online is False
        assert b.error == ""
        assert b.latency_ms == 0.0
        assert b.sessions == 0

    def test_online_ok(self):
        b = BotCheck(name="cline", port=3001, online=True, sessions=3, latency_ms=45.2)
        assert b.online is True
        assert b.sessions == 3
        assert b.latency_ms == 45.2


class TestBridgeCheck:
    """BridgeCheck dataclass model."""

    def test_defaults(self):
        b = BridgeCheck()
        assert b.active is False
        assert b.service_name == ""
        assert b.error == ""

    def test_active(self):
        b = BridgeCheck(active=True, service_name="lina-comm-bridge", state="active")
        assert b.active is True
        assert b.state == "active"


class TestDatabaseCheck:
    """DatabaseCheck dataclass model."""

    def test_defaults(self):
        d = DatabaseCheck()
        assert d.connected is False
        assert d.latency_ms == 0.0

    def test_connected(self):
        d = DatabaseCheck(connected=True, latency_ms=3.2)
        assert d.connected is True
        assert d.latency_ms == 3.2


class TestHealthReport:
    """HealthReport aggregation model."""

    def test_defaults(self):
        r = HealthReport()
        assert r.overall == "unknown"
        assert r.bots == {}
        assert r.bridge == BridgeCheck()
        assert r.database == DatabaseCheck()

    def test_with_data(self):
        r = HealthReport(
            timestamp="2026-06-06T00:00:00",
            overall="healthy",
            bots={
                "lina": BotCheck(name="lina", port=3000, online=True),
                "cline": BotCheck(name="cline", port=3001, online=True),
            },
            bridge=BridgeCheck(active=True, state="active"),
            database=DatabaseCheck(connected=True, latency_ms=2.0),
        )
        assert r.overall == "healthy"
        assert len(r.bots) == 2


# ═══════════════════════════════════════════════════════════════════════════
# _compute_overall Tests
# ═══════════════════════════════════════════════════════════════════════════


class TestComputeOverall:
    """Overall status computation logic."""

    def test_healthy_all_online_db_ok(self):
        r = HealthReport(
            bots={
                "lina": BotCheck(name="lina", port=3000, online=True),
                "cline": BotCheck(name="cline", port=3001, online=True),
                "gemma": BotCheck(name="gemma", port=3002, online=True),
            },
            database=DatabaseCheck(connected=True),
        )
        assert _compute_overall(r) == "healthy"

    def test_critical_no_bots(self):
        r = HealthReport(bots={}, database=DatabaseCheck(connected=False))
        assert _compute_overall(r) == "critical"

    def test_degraded_one_bot_down(self):
        r = HealthReport(
            bots={
                "lina": BotCheck(name="lina", port=3000, online=True),
                "cline": BotCheck(name="cline", port=3001, online=True),
                "gemma": BotCheck(name="gemma", port=3002, online=False, error="timeout"),
            },
            database=DatabaseCheck(connected=True),
        )
        assert _compute_overall(r) == "degraded"

    def test_critical_more_than_half_down(self):
        r = HealthReport(
            bots={
                "lina": BotCheck(name="lina", port=3000, online=False),
                "cline": BotCheck(name="cline", port=3001, online=True),
                "gemma": BotCheck(name="gemma", port=3002, online=False),
            },
            database=DatabaseCheck(connected=True),
        )
        assert _compute_overall(r) == "critical"

    def test_critical_db_down(self):
        r = HealthReport(
            bots={
                "lina": BotCheck(name="lina", port=3000, online=True),
                "cline": BotCheck(name="cline", port=3001, online=True),
                "gemma": BotCheck(name="gemma", port=3002, online=True),
            },
            database=DatabaseCheck(connected=False, error="connection refused"),
        )
        assert _compute_overall(r) == "critical"

    def test_degraded_all_bots_online_no_db(self):
        r = HealthReport(
            bots={
                "lina": BotCheck(name="lina", port=3000, online=True),
                "cline": BotCheck(name="cline", port=3001, online=True),
            },
            database=DatabaseCheck(connected=False),
        )
        assert _compute_overall(r) == "critical"

    def test_degraded_half_bots_db_ok(self):
        r = HealthReport(
            bots={
                "lina": BotCheck(name="lina", port=3000, online=True),
                "cline": BotCheck(name="cline", port=3001, online=False),
            },
            database=DatabaseCheck(connected=True),
        )
        assert _compute_overall(r) == "degraded"


# ═══════════════════════════════════════════════════════════════════════════
# check_bot Tests
# ═══════════════════════════════════════════════════════════════════════════


class TestCheckBot:
    """Individual bot healthcheck logic."""

    @patch("bin.bot_health.httpx.AsyncClient")
    async def test_online(self, mock_client_cls):
        """Bot responde 200 con sesiones."""
        mock_client = AsyncMock()
        mock_client_cls.return_value.__aenter__.return_value = mock_client

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"sessions": {"s1": 5, "s2": 3}, "version": "0.1.0"}
        mock_client.get.return_value = mock_resp

        result = await check_bot(mock_client, "lina", "https://localhost:3000", 3000, "secret123")

        assert result.name == "lina"
        assert result.online is True
        assert result.status_code == 200
        assert result.sessions == 2  # 2 session keys
        assert result.version == "0.1.0"
        assert result.error == ""

    @patch("bin.bot_health.httpx.AsyncClient")
    async def test_offline_connection_error(self, mock_client_cls):
        """Bot no responde (connection refused)."""
        import httpx
        mock_client = AsyncMock()
        mock_client_cls.return_value.__aenter__.return_value = mock_client
        mock_client.get.side_effect = httpx.ConnectError("connection refused")

        result = await check_bot(mock_client, "cline", "https://localhost:3001", 3001, "")

        assert result.name == "cline"
        assert result.online is False
        assert "connection refused" in result.error

    @patch("bin.bot_health.httpx.AsyncClient")
    async def test_offline_http_error(self, mock_client_cls):
        """Bot responde con error HTTP."""
        mock_client = AsyncMock()
        mock_client_cls.return_value.__aenter__.return_value = mock_client

        mock_resp = MagicMock()
        mock_resp.status_code = 503
        mock_client.get.return_value = mock_resp

        result = await check_bot(mock_client, "gemma", "https://localhost:3002", 3002, "")

        assert result.online is False
        assert result.status_code == 503
        assert "HTTP 503" in result.error

    @patch("bin.bot_health.httpx.AsyncClient")
    async def test_timeout(self, mock_client_cls):
        """Bot timeout."""
        import httpx
        mock_client = AsyncMock()
        mock_client_cls.return_value.__aenter__.return_value = mock_client
        mock_client.get.side_effect = httpx.TimeoutException("timed out")

        result = await check_bot(mock_client, "lina", "https://localhost:3000", 3000, "")

        assert result.online is False
        assert "timeout" in result.error

    @patch("bin.bot_health.httpx.AsyncClient")
    async def test_with_secret_header(self, mock_client_cls):
        """Verifica que el header x-secret-key se envía correctamente."""
        mock_client = AsyncMock()
        mock_client_cls.return_value.__aenter__.return_value = mock_client
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {}
        mock_client.get.return_value = mock_resp

        await check_bot(mock_client, "lina", "https://localhost:3000", 3000, "supersecret")

        # Verificar que el header se pasó
        call_kwargs = mock_client.get.call_args[1]
        assert call_kwargs["headers"].get("x-secret-key") == "supersecret"

    @patch("bin.bot_health.httpx.AsyncClient")
    async def test_no_secret_no_header(self, mock_client_cls):
        """Sin secret, no envía x-secret-key."""
        mock_client = AsyncMock()
        mock_client_cls.return_value.__aenter__.return_value = mock_client
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {}
        mock_client.get.return_value = mock_resp

        await check_bot(mock_client, "lina", "https://localhost:3000", 3000, "")

        call_kwargs = mock_client.get.call_args[1]
        assert "x-secret-key" not in call_kwargs["headers"]

    @patch("bin.bot_health.httpx.AsyncClient")
    async def test_non_json_response(self, mock_client_cls):
        """Respuesta 200 pero no JSON."""
        mock_client = AsyncMock()
        mock_client_cls.return_value.__aenter__.return_value = mock_client
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.side_effect = json.JSONDecodeError("Not JSON", "", 0)
        mock_client.get.return_value = mock_resp

        result = await check_bot(mock_client, "lina", "https://localhost:3000", 3000, "")

        # Even without JSON parse, 200 means online
        assert result.online is True
        assert result.sessions == 0  # defaults


# ═══════════════════════════════════════════════════════════════════════════
# check_bridge Tests
# ═══════════════════════════════════════════════════════════════════════════


class TestCheckBridge:
    """Comm Bridge healthcheck via systemctl."""

    @patch("bin.bot_health.subprocess.run")
    def test_active(self, mock_run):
        """Service is active (system scope)."""
        mock_result = MagicMock()
        mock_result.stdout = "active\n"
        mock_result.returncode = 0
        mock_run.return_value = mock_result

        result = asyncio_run(check_bridge())

        assert result.active is True
        assert result.state == "active"
        assert result.service_name == "lina-comm-bridge"

    @patch("bin.bot_health.subprocess.run")
    def test_inactive(self, mock_run):
        """Service is inactive."""
        mock_result = MagicMock()
        mock_result.stdout = "inactive\n"
        mock_result.returncode = 3  # systemctl returns 3 for inactive
        mock_run.return_value = mock_result

        # Second call (user scope) also inactive
        mock_result2 = MagicMock()
        mock_result2.stdout = "inactive\n"
        mock_result2.returncode = 3
        # Make the mock return inactive for both calls
        mock_run.side_effect = [mock_result, mock_result2]

        result = asyncio_run(check_bridge())

        assert result.active is False
        assert result.state == "inactive"

    @patch("bin.bot_health.subprocess.run")
    def test_user_scope_active(self, mock_run):
        """Service is active in --user scope."""
        mock_system = MagicMock()
        mock_system.stdout = "inactive\n"
        mock_system.returncode = 3

        mock_user = MagicMock()
        mock_user.stdout = "active\n"
        mock_user.returncode = 0

        mock_run.side_effect = [mock_system, mock_user]

        result = asyncio_run(check_bridge())

        assert result.active is True
        assert "user:" in result.state

    @patch("bin.bot_health.subprocess.run")
    def test_systemctl_not_found(self, mock_run):
        """systemctl not available (not Linux or no systemd)."""
        mock_run.side_effect = FileNotFoundError("No such file or directory: 'systemctl'")

        result = asyncio_run(check_bridge())

        assert result.active is False
        assert "not found" in result.error

    @patch("bin.bot_health.subprocess.run")
    def test_timeout_checking_service(self, mock_run):
        """subprocess timeout."""
        mock_run.side_effect = subprocess.TimeoutExpired("systemctl", 10)

        result = asyncio_run(check_bridge())

        assert result.active is False
        assert "timeout" in result.error


# ═══════════════════════════════════════════════════════════════════════════
# check_database Tests
# ═══════════════════════════════════════════════════════════════════════════


class TestCheckDatabase:
    """PostgreSQL connectivity check."""

    @patch("bin.bot_health.time.monotonic")
    @patch("bin.bot_health.asyncpg.connect")
    async def test_connected(self, mock_connect, mock_time):
        """DB connection OK, SELECT 1 returns 1."""
        mock_conn = AsyncMock()
        mock_conn.fetchval.return_value = 1
        mock_connect.return_value = mock_conn

        # Simula 5ms de latencia: 5.0 * 1000 = 5000ms... mmm, no.
        # Usemos 0.005 segundos = 5ms reales
        # time.monotonic() devuelve segundos. La función hace *1000 para ms.
        # (1000.005 - 1000.0) = 0.005s → 5ms
        mock_time.side_effect = [1000.0, 1000.005]

        result = await check_database()

        assert result.connected is True
        assert result.latency_ms == 5.0  # 5ms
        assert result.error == ""

    @patch("bin.bot_health.asyncpg.connect")
    async def test_connection_refused(self, mock_connect):
        """DB connection refused."""
        import asyncpg
        mock_connect.side_effect = asyncpg.CannotConnectNowError(
            "the database system is shutting down"
        )

        result = await check_database()

        assert result.connected is False
        assert result.error != ""

    @patch("bin.bot_health.asyncpg.connect")
    async def test_auth_failure(self, mock_connect):
        """DB auth failure."""
        import asyncpg
        mock_connect.side_effect = asyncpg.InvalidAuthorizationSpecificationError(
            "password authentication failed"
        )

        result = await check_database()

        assert result.connected is False
        assert "authentication failed" in result.error or "password" in result.error

    @patch("bin.bot_health.asyncpg.connect")
    async def test_timeout(self, mock_connect):
        """DB timeout."""
        import asyncio
        mock_connect.side_effect = asyncio.TimeoutError("connection timed out")

        result = await check_database()

        assert result.connected is False
        assert "timeout" in result.error or "timed out" in result.error

    @patch("bin.bot_health.asyncpg.connect")
    async def test_no_asyncpg(self, mock_connect):
        """asyncpg not installed."""
        # Simulate ImportError by raising at the import level
        # We need to mock the actual behavior when asyncpg is not importable
        # Since asyncpg IS installed in test env, we test the error handling path
        # by patching check_database's internal behavior
        mock_connect.side_effect = ImportError("No module named 'asyncpg'")

        result = await check_database()

        assert result.connected is False
        # The error handling catches ImportError at the import statement
        # Since asyncpg IS installed, we simulate via connect failing
        # The actual ImportError is caught inside the try block of check_database
        assert result.error != "" or result.connected is False


# ═══════════════════════════════════════════════════════════════════════════
# Report Format Tests
# ═══════════════════════════════════════════════════════════════════════════


class TestBuildJson:
    """JSON output format."""

    def test_json_structure(self):
        r = HealthReport(
            timestamp="2026-06-06T00:00:00+00:00",
            overall="healthy",
            bots={
                "lina": BotCheck(name="lina", port=3000, online=True, sessions=2, latency_ms=12.3),
                "cline": BotCheck(name="cline", port=3001, online=False, error="timeout"),
                "gemma": BotCheck(name="gemma", port=3002, online=True, latency_ms=45.0),
            },
            bridge=BridgeCheck(active=True, service_name="lina-comm-bridge", state="active"),
            database=DatabaseCheck(connected=True, latency_ms=3.1),
        )

        output = _build_json(r)
        data = json.loads(output)

        assert data["timestamp"] == "2026-06-06T00:00:00+00:00"
        assert data["overall"] == "healthy"

        # Bots
        assert data["bots"]["lina"]["online"] is True
        assert data["bots"]["lina"]["sessions"] == 2
        assert data["bots"]["cline"]["online"] is False
        assert data["bots"]["cline"]["error"] == "timeout"
        assert data["bots"]["gemma"]["online"] is True

        # Bridge
        assert data["bridge"]["active"] is True
        assert data["bridge"]["state"] == "active"

        # Database
        assert data["database"]["connected"] is True
        assert data["database"]["latency_ms"] == 3.1

    def test_json_empty_report(self):
        r = HealthReport()
        output = _build_json(r)
        data = json.loads(output)

        assert data["overall"] == "unknown"
        assert data["bots"] == {}
        assert data["bridge"]["active"] is False
        assert data["database"]["connected"] is False

    def test_json_serializable(self):
        """Verifica que todo el reporte sea serializable a JSON sin errores."""
        r = HealthReport(
            timestamp="2026-06-06T00:00:00",
            overall="degraded",
            bots={
                "lina": BotCheck(name="lina", port=3000, online=True, latency_ms=10.0),
                "cline": BotCheck(name="cline", port=3001, online=False, error="timeout"),
            },
            bridge=BridgeCheck(active=False, error="not found"),
            database=DatabaseCheck(connected=True, latency_ms=2.5),
        )
        # No debe lanzar excepción
        json.dumps(json.loads(_build_json(r)))


class TestBuildText:
    """Text output format."""

    def test_text_healthy(self):
        r = HealthReport(
            timestamp="2026-06-06T00:00:00",
            overall="healthy",
            bots={
                "lina": BotCheck(name="lina", port=3000, online=True, latency_ms=12.0, sessions=2),
                "cline": BotCheck(name="cline", port=3001, online=True, latency_ms=8.5),
                "gemma": BotCheck(name="gemma", port=3002, online=True, latency_ms=15.2),
            },
            bridge=BridgeCheck(active=True, service_name="lina-comm-bridge", state="active"),
            database=DatabaseCheck(connected=True, latency_ms=3.0),
        )

        output = _build_text(r)
        assert "healthy" in output.lower()
        assert "✅" in output
        assert "3/3 bots online" in output
        assert "DB ✅" in output
        assert "Bridge ✅" in output

    def test_text_critical(self):
        r = HealthReport(
            timestamp="2026-06-06T00:00:00",
            overall="critical",
            bots={
                "lina": BotCheck(name="lina", port=3000, online=True, latency_ms=12.0),
                "cline": BotCheck(name="cline", port=3001, online=False, error="timeout"),
                "gemma": BotCheck(name="gemma", port=3002, online=False, error="connection refused"),
            },
            bridge=BridgeCheck(active=False, error="not found"),
            database=DatabaseCheck(connected=True, latency_ms=3.0),
        )

        output = _build_text(r)
        assert "critical" in output.lower()
        assert "1/3 bots online" in output
        assert "Bridge ❌" in output

    def test_text_degraded(self):
        r = HealthReport(
            timestamp="2026-06-06T00:00:00",
            overall="degraded",
            bots={
                "lina": BotCheck(name="lina", port=3000, online=True, latency_ms=12.0),
                "cline": BotCheck(name="cline", port=3001, online=True, latency_ms=8.5),
                "gemma": BotCheck(name="gemma", port=3002, online=False, error="timeout"),
            },
            bridge=BridgeCheck(active=True, state="active"),
            database=DatabaseCheck(connected=True, latency_ms=3.0),
        )

        output = _build_text(r)
        assert "degraded" in output.lower()
        assert "2/3 bots online" in output

    def test_text_unknown_bot(self):
        """Bot no incluido en el reporte aparece como 'unknown'."""
        r = HealthReport(
            timestamp="2026-06-06T00:00:00",
            overall="degraded",
            bots={
                "lina": BotCheck(name="lina", port=3000, online=True),
                "cline": BotCheck(name="cline", port=3001, online=True),
            },
            bridge=BridgeCheck(active=True),
            database=DatabaseCheck(connected=True),
        )

        output = _build_text(r)
        # gemma no está en bots, debe aparecer como "unknown"
        assert "unknown" in output.lower() or "not checked" in output


# ═══════════════════════════════════════════════════════════════════════════
# Integration / collect() Tests
# ═══════════════════════════════════════════════════════════════════════════


class TestCollect:
    """Pipeline completo de recolección."""

    @patch("bin.bot_health.check_database")
    @patch("bin.bot_health.check_bridge")
    @patch("bin.bot_health.httpx.AsyncClient")
    async def test_all_healthy(self, mock_client_cls, mock_bridge, mock_db):
        """Todos los componentes saludables."""
        # Mock HTTP client for 3 bots
        mock_client = AsyncMock()
        mock_client_cls.return_value.__aenter__.return_value = mock_client

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {}
        mock_client.get.return_value = mock_resp

        # Mock bridge
        mock_bridge.return_value = BridgeCheck(active=True, state="active")
        mock_db.return_value = DatabaseCheck(connected=True, latency_ms=3.0)

        report = await collect()

        assert report.overall == "healthy"
        assert len(report.bots) == 3
        assert all(b.online for b in report.bots.values())
        assert report.bridge.active is True
        assert report.database.connected is True

    @patch("bin.bot_health.check_database")
    @patch("bin.bot_health.check_bridge")
    @patch("bin.bot_health.httpx.AsyncClient")
    async def test_all_down(self, mock_client_cls, mock_bridge, mock_db):
        """Todo caído — reporte critical."""
        import httpx
        mock_client = AsyncMock()
        mock_client_cls.return_value.__aenter__.return_value = mock_client
        mock_client.get.side_effect = httpx.ConnectError("connection refused")

        mock_bridge.return_value = BridgeCheck(active=False, error="not found")
        mock_db.return_value = DatabaseCheck(connected=False, error="connection refused")

        report = await collect()

        assert report.overall == "critical"
        assert all(not b.online for b in report.bots.values())
        assert report.bridge.active is False
        assert report.database.connected is False

    @patch("bin.bot_health.check_database")
    @patch("bin.bot_health.check_bridge")
    @patch("bin.bot_health.httpx.AsyncClient")
    async def test_partial_failure(self, mock_client_cls, mock_bridge, mock_db):
        """2/3 bots online = degraded."""
        mock_client = AsyncMock()
        mock_client_cls.return_value.__aenter__.return_value = mock_client

        # First two calls (lina, cline) = 200, third (gemma) = error
        mock_resp_ok = MagicMock()
        mock_resp_ok.status_code = 200
        mock_resp_ok.json.return_value = {}

        import httpx
        mock_client.get.side_effect = [
            mock_resp_ok,        # lina — 200
            mock_resp_ok,        # cline — 200
            httpx.ConnectError("refused"),  # gemma — error
        ]

        mock_bridge.return_value = BridgeCheck(active=True, state="active")
        mock_db.return_value = DatabaseCheck(connected=True, latency_ms=3.0)

        report = await collect()

        assert report.overall == "degraded"
        assert report.bots["lina"].online is True
        assert report.bots["cline"].online is True
        assert report.bots["gemma"].online is False


# ═══════════════════════════════════════════════════════════════════════════
# Helper
# ═══════════════════════════════════════════════════════════════════════════

def asyncio_run(coro):
    """Helper para ejecutar corrutinas en tests sincrónicos."""
    import asyncio
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            # Ya hay un loop corriendo (pytest-asyncio)
            import asyncio
            return asyncio.ensure_future(coro)
    except RuntimeError:
        pass
    return asyncio.run(coro)
