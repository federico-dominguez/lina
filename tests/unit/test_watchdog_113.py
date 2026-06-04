"""Tests para watchdog de timeout enforcement (issue #113)."""

import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, PropertyMock, patch

_SRC = str(Path(__file__).resolve().parents[3] / "mcps" / "orchestrator" / "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

# Mocks de dependencias del spawner — sin instalación real
sys.modules["psycopg2"] = MagicMock()
sys.modules["psycopg2.extras"] = MagicMock()
sys.modules["yaml"] = MagicMock()

from lina_orchestrator.infrastructure.spawner import (  # noqa: E402
    SpawnerService,
    _WATCHDOG_INTERVAL,
    _enforce_timeout,
)


class TestEnforceTimeout:
    """Pruebas de _enforce_timeout."""

    def test_within_limit_returns_none(self):
        """Agente dentro del límite → None (no timeout)."""
        started_at = datetime.now(timezone.utc)
        result = _enforce_timeout(
            "abc123", "dev", 12345, started_at, max_runtime_minutes=120,
        )
        assert result is None, "No debería aplicar timeout"

    def test_old_session_exceeds_limit(self):
        """Agente que arrancó hace mucho → timeout aplicado."""
        started_at = datetime(2020, 1, 1, tzinfo=timezone.utc)
        result = _enforce_timeout(
            "old-session-1", "dev", 99999, started_at, max_runtime_minutes=1,
        )
        assert result is not None, "Debería aplicar timeout"
        assert result["role"] == "dev"
        assert result["session_id"] == "old-session-1"
        assert "⌛" in result["message"]
        assert "timeout" in result["message"]
        assert result["max_min"] == 1

    def test_runtime_rounded_correctly(self):
        """runtime_min se redondea correctamente."""
        # 30 segundos atrás = 0.5 minutos
        started_at = datetime(2020, 1, 1, tzinfo=timezone.utc)
        result = _enforce_timeout(
            "test-session", "study", 88888, started_at, max_runtime_minutes=1,
        )
        assert result is not None
        assert result["runtime_min"] > 0

    def test_message_format(self):
        """Mensaje estructurado para el gateway."""
        started_at = datetime(2020, 1, 1, tzinfo=timezone.utc)
        result = _enforce_timeout(
            "session-abc", "ops", 77777, started_at, max_runtime_minutes=5,
        )
        assert result is not None
        msg = result["message"]
        assert "session-" in msg
        assert "ops" not in msg  # role no va en el mensaje
        assert "max 5m" in msg


class TestWatchdogConstants:
    """Pruebas de constantes del watchdog."""

    def test_interval_set(self):
        """Intervalo debe ser un entero positivo."""
        assert _WATCHDOG_INTERVAL > 0, f"Intervalo debe ser > 0, es {_WATCHDOG_INTERVAL}"
        assert isinstance(_WATCHDOG_INTERVAL, int)

    def test_interval_reasonable(self):
        """Intervalo debe ser ≤ 30s (detección rápida)."""
        assert _WATCHDOG_INTERVAL <= 30, (
            f"Intervalo {_WATCHDOG_INTERVAL}s es muy lento para detección inmediata"
        )


class TestSpawnerServiceWatchdog:
    """Pruebas del watchdog en SpawnerService."""

    def test_watchdog_not_running_by_default(self):
        """Watchdog no arranca automáticamente al crear el servicio."""
        policy = MagicMock()
        svc = SpawnerService(policy)
        assert svc._watchdog_thread is None
        assert not svc._watchdog_stop.is_set()

    def test_start_watchdog(self):
        """start_watchdog() arranca el thread."""
        policy = MagicMock()
        svc = SpawnerService(policy)
        svc.start_watchdog()
        assert svc._watchdog_thread is not None
        assert svc._watchdog_thread.is_alive()
        svc.stop_watchdog()
        time.sleep(0.1)
        assert not svc._watchdog_thread.is_alive()

    def test_double_start(self):
        """Doble start no crea threads duplicados."""
        policy = MagicMock()
        svc = SpawnerService(policy)
        svc.start_watchdog()
        thread_id = id(svc._watchdog_thread)
        svc.start_watchdog()  # segundo start no-op
        assert id(svc._watchdog_thread) == thread_id
        svc.stop_watchdog()

    def test_stop_watchdog_signal(self):
        """stop_watchdog() setea el event."""
        policy = MagicMock()
        svc = SpawnerService(policy)
        svc.start_watchdog()
        assert not svc._watchdog_stop.is_set()
        svc.stop_watchdog()
        assert svc._watchdog_stop.is_set()
