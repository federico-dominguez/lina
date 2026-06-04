"""Tests para lifecycle: watchdog, timeout, backoff, dead-letter (issue #89)."""

import sys
import time
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock, patch

_SRC = str(Path(__file__).resolve().parents[3] / "mcps" / "orchestrator" / "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

# Mocks de psycopg2
sys.modules["psycopg2"] = MagicMock()
sys.modules["psycopg2.extras"] = MagicMock()

from lina_orchestrator.application.lifecycle import (  # noqa: E402
    LifecycleService,
    _BACKOFF_INTERVALS,
    _MAX_RETRIES,
    _WATCHDOG_INTERVAL,
)


class TestBackoff:
    """Pruebas de la lógica de backoff."""

    def test_backoff_first_retry(self):
        """Primer reintento: 60s (1 minuto)."""
        retry_count = 0
        backoff = _BACKOFF_INTERVALS[min(retry_count, len(_BACKOFF_INTERVALS) - 1)]
        assert backoff == 60, f"Expected 60, got {backoff}"

    def test_backoff_second_retry(self):
        """Segundo reintento: 300s (5 minutos)."""
        retry_count = 1
        backoff = _BACKOFF_INTERVALS[min(retry_count, len(_BACKOFF_INTERVALS) - 1)]
        assert backoff == 300, f"Expected 300, got {backoff}"

    def test_backoff_third_retry(self):
        """Tercer reintento: 900s (15 minutos)."""
        retry_count = 2
        backoff = _BACKOFF_INTERVALS[min(retry_count, len(_BACKOFF_INTERVALS) - 1)]
        assert backoff == 900, f"Expected 900, got {backoff}"

    def test_backoff_capped(self):
        """Cuarto reintento: capped a 900s."""
        retry_count = 5  # más allá del array
        backoff = _BACKOFF_INTERVALS[min(retry_count, len(_BACKOFF_INTERVALS) - 1)]
        assert backoff == 900, f"Expected capped 900, got {backoff}"

    def test_max_retries(self):
        """Máximo de reintentos antes de dead-letter."""
        assert _MAX_RETRIES == 3, f"Expected 3, got {_MAX_RETRIES}"


class TestLifecycleService:
    """Pruebas del LifecycleService."""

    def test_init(self):
        """Creación del servicio."""
        policy = MagicMock()
        svc = LifecycleService(policy)
        assert svc is not None
        assert svc.is_running is False

    def test_start_stop(self):
        """Arranque y parada del watchdog."""
        policy = MagicMock()
        svc = LifecycleService(policy)
        svc.start()
        assert svc.is_running is True
        svc.stop()
        # Dar tiempo a que el thread termine
        time.sleep(0.1)
        assert svc.is_running is False

    def test_double_start(self):
        """Start dos veces no crea thread duplicado."""
        policy = MagicMock()
        svc = LifecycleService(policy)
        svc.start()
        thread_id = id(svc._thread)
        svc.start()  # segundo start no hace nada
        assert id(svc._thread) == thread_id
        svc.stop()

    @patch("lina_orchestrator.application.lifecycle._db_conn")
    def test_list_dead_letter_empty(self, mock_conn):
        """Listar dead-letter vacía."""
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = []
        mock_conn.return_value.cursor.return_value.__enter__.return_value = mock_cursor

        policy = MagicMock()
        svc = LifecycleService(policy)
        result = svc.list_dead_letter()
        assert result == []

    @patch("lina_orchestrator.application.lifecycle._db_conn")
    def test_get_failed_summary_empty(self, mock_conn):
        """Resumen de fallos vacío."""
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = []
        mock_conn.return_value.cursor.return_value.__enter__.return_value = mock_cursor

        policy = MagicMock()
        svc = LifecycleService(policy)
        result = svc.get_failed_summary()
        assert result == []

    def test_kill_process_group_nonexistent_pid(self):
        """killpg con PID inexistente no lanza excepción."""
        # No debe fallar aunque el PID no exista
        LifecycleService.kill_process_group(999999999)
        # Si llegamos acá, no falló
        assert True
