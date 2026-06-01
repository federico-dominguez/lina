"""Unit tests — reload_mcp tool en lina-shell-policy.

Usa mocks para subprocess y urllib.request; no requiere Docker ni sudo.
"""

from __future__ import annotations

import subprocess
import urllib.error
import urllib.request
from unittest.mock import MagicMock, patch

import pytest

from lina_shell_policy.server import (
    _LINA_DEPLOY,
    _RELOADABLE_MCPS,
    _wait_for_health,
    reload_mcp,
)


# ─── _RELOADABLE_MCPS ─────────────────────────────────────────────────────────


class TestReloadableMcpsMapping:
    def test_has_all_expected_mcps(self):
        expected = {
            "lina-secrets",
            "lina-fs-safe",
            "lina-shell-policy",
            "lina-systemd-user",
            "lina-moodle",
            "lina-db",
        }
        assert expected.issubset(_RELOADABLE_MCPS.keys())

    def test_docker_service_names_are_prefixed(self):
        for mcp_name, (service, _url) in _RELOADABLE_MCPS.items():
            short = mcp_name[len("lina-"):]  # e.g. "fs-safe"
            assert service == f"lina-mcp-{short}", (
                f"{mcp_name} → service debe ser lina-mcp-{short}, got {service}"
            )

    def test_health_urls_are_localhost(self):
        for _name, (_service, url) in _RELOADABLE_MCPS.items():
            assert url.startswith("http://localhost:"), f"{_name} health_url debe ser localhost"

    def test_health_urls_distinct_ports(self):
        urls = [url for _, url in _RELOADABLE_MCPS.values()]
        assert len(urls) == len(set(urls)), "Cada MCP debe tener un puerto distinto"


# ─── _wait_for_health ─────────────────────────────────────────────────────────


class TestWaitForHealth:
    def test_returns_true_on_200(self):
        mock_resp = MagicMock()
        with patch("urllib.request.urlopen", return_value=mock_resp):
            assert _wait_for_health("http://localhost:8102/", timeout_s=5) is True

    def test_returns_true_on_http_error(self):
        """HTTPError (4xx/5xx) significa que el servidor está up → True."""
        with patch(
            "urllib.request.urlopen",
            side_effect=urllib.error.HTTPError("http://localhost:8102/", 404, "Not Found", {}, None),  # type: ignore[arg-type]
        ):
            assert _wait_for_health("http://localhost:8102/", timeout_s=5) is True

    def test_returns_false_on_connection_refused(self):
        """URLError (connection refused) → espera hasta timeout → False."""
        with patch(
            "urllib.request.urlopen",
            side_effect=urllib.error.URLError("connection refused"),
        ):
            # timeout=2 para que el test sea rápido
            assert _wait_for_health("http://localhost:9999/", timeout_s=2) is False

    def test_retries_until_success(self):
        """Dos fallas seguidas de un éxito → True."""
        call_count = 0
        original_urlopen = urllib.request.urlopen

        def side_effect(url, timeout=None):
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise urllib.error.URLError("not yet")
            return MagicMock()

        with patch("urllib.request.urlopen", side_effect=side_effect):
            with patch("time.sleep"):  # no esperar de verdad
                result = _wait_for_health("http://localhost:8102/", timeout_s=60)
        assert result is True
        assert call_count == 3


# ─── reload_mcp ───────────────────────────────────────────────────────────────


@pytest.fixture()
def allow_sudo(monkeypatch):
    """Activa LINA_SHELL_ALLOW_SUDO para los tests."""
    monkeypatch.setenv("LINA_SHELL_ALLOW_SUDO", "1")
    # Parchamos la variable del módulo directamente
    with patch("lina_shell_policy.server.ALLOW_SUDO_ENV", True):
        yield


class TestReloadMcpUnknown:
    def test_unknown_mcp_returns_failure(self):
        result = reload_mcp("lina-nonexistent")
        assert result["success"] is False
        assert "desconocido" in result["error"].lower()
        assert "lina-nonexistent" in result["error"]

    def test_error_lists_known_mcps(self):
        result = reload_mcp("lina-nonexistent")
        for known in _RELOADABLE_MCPS:
            assert known in result["error"]

    def test_empty_name_returns_failure(self):
        result = reload_mcp("")
        assert result["success"] is False


class TestReloadMcpNoSudo:
    def test_no_sudo_env_returns_failure(self):
        with patch("lina_shell_policy.server.ALLOW_SUDO_ENV", False):
            result = reload_mcp("lina-fs-safe")
        assert result["success"] is False
        assert "LINA_SHELL_ALLOW_SUDO" in result["error"]


class TestReloadMcpSuccess:
    def _make_proc(self, returncode=0, stdout="", stderr=""):
        proc = MagicMock()
        proc.returncode = returncode
        proc.stdout = stdout
        proc.stderr = stderr
        return proc

    def test_success_path(self, allow_sudo):
        """deploy restart OK + health OK → success=True."""
        proc = self._make_proc(returncode=0)
        with (
            patch("subprocess.run", return_value=proc) as mock_run,
            patch("lina_shell_policy.server._wait_for_health", return_value=True),
        ):
            result = reload_mcp("lina-fs-safe")

        assert result["success"] is True
        assert result["name"] == "lina-fs-safe"
        assert result["service"] == "lina-mcp-fs-safe"
        assert "health_url" in result
        assert "duration_seconds" in result
        assert "error" not in result

        # Verificar que se llamó correctamente a lina-deploy
        mock_run.assert_called_once()
        call_args = mock_run.call_args[0][0]
        assert call_args == ["sudo", _LINA_DEPLOY, "restart", "lina-mcp-fs-safe"]

    def test_deploy_nonzero_exit(self, allow_sudo):
        """lina-deploy exit != 0 → success=False con mensaje de error."""
        proc = self._make_proc(returncode=2, stderr="servicio no permitido")
        with patch("subprocess.run", return_value=proc):
            result = reload_mcp("lina-db")

        assert result["success"] is False
        assert "exit 2" in result["error"]
        assert "no permitido" in result["error"]

    def test_health_check_timeout(self, allow_sudo):
        """deploy OK pero container no levanta → success=False."""
        proc = self._make_proc(returncode=0)
        with (
            patch("subprocess.run", return_value=proc),
            patch("lina_shell_policy.server._wait_for_health", return_value=False),
        ):
            result = reload_mcp("lina-secrets", health_timeout=5)

        assert result["success"] is False
        assert "timeout" in result["error"].lower()

    def test_deploy_timeout_exception(self, allow_sudo):
        """subprocess.TimeoutExpired → success=False."""
        with patch(
            "subprocess.run",
            side_effect=subprocess.TimeoutExpired(["sudo", _LINA_DEPLOY], 60),
        ):
            result = reload_mcp("lina-moodle")

        assert result["success"] is False
        assert "timeout" in result["error"].lower()

    def test_lina_deploy_not_found(self, allow_sudo):
        """FileNotFoundError → success=False con mensaje descriptivo."""
        with patch("subprocess.run", side_effect=FileNotFoundError("no such file")):
            result = reload_mcp("lina-systemd-user")

        assert result["success"] is False
        assert "lina-deploy" in result["error"].lower()

    def test_health_timeout_capped_at_120(self, allow_sudo):
        """health_timeout > 120 se capa en 120 (no permite valores absurdos)."""
        proc = self._make_proc(returncode=0)
        captured_timeout = {}

        def mock_health(url, timeout_s):
            captured_timeout["value"] = timeout_s
            return True

        with (
            patch("subprocess.run", return_value=proc),
            patch("lina_shell_policy.server._wait_for_health", side_effect=mock_health),
        ):
            reload_mcp("lina-db", health_timeout=9999)

        assert captured_timeout["value"] == 120

    def test_all_reloadable_mcps_work(self, allow_sudo):
        """Todos los MCPs en el mapping se pueden recargar exitosamente."""
        proc = self._make_proc(returncode=0)
        with (
            patch("subprocess.run", return_value=proc),
            patch("lina_shell_policy.server._wait_for_health", return_value=True),
        ):
            for mcp_name in _RELOADABLE_MCPS:
                result = reload_mcp(mcp_name)
                assert result["success"] is True, f"reload_mcp({mcp_name!r}) failed: {result}"
