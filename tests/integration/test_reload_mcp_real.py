"""Integration test real para reload_mcp.

A diferencia de los tests unitarios en mcps/shell-policy/tests/test_reload_mcp.py
(todos mockeados), este test ejecuta un restart real de lina-mcp-fs-safe en el
docker-compose activo y verifica que:

1. El servicio responde por HTTP antes y después del restart.
2. El container tiene un StartedAt reciente post-restart (containers distintos).
3. fs-safe sigue siendo funcional (heartbeat HTTP).

Requisitos:
    - Docker + docker compose activos con el stack de LINA up.
    - sudo lina-deploy en PATH y con allowlist para lina-mcp-fs-safe.
    - Env var LINA_E2E_REAL_RELOAD=1 para activar el test (skip por defecto).

Ejecución:
    LINA_E2E_REAL_RELOAD=1 pytest -m integration tests/integration/test_reload_mcp_real.py -v
"""

from __future__ import annotations

import json
import os
import subprocess
import time
import urllib.error
import urllib.request

import pytest

pytestmark = pytest.mark.integration

_FS_SAFE_URL = "http://localhost:8102/"
_FS_SAFE_CONTAINER = "docker-lina-mcp-fs-safe-1"
_FS_SAFE_DEPLOY_NAME = "lina-mcp-fs-safe"

_ENABLED = os.environ.get("LINA_E2E_REAL_RELOAD") == "1"
_REASON_DISABLED = "Set LINA_E2E_REAL_RELOAD=1 to run real reload_mcp test"


def _docker_inspect(container: str) -> dict | None:
    """Devuelve el inspect JSON del container, o None si no existe."""
    try:
        out = subprocess.run(
            ["docker", "inspect", container],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    if out.returncode != 0:
        return None
    data = json.loads(out.stdout)
    return data[0] if data else None


def _http_alive(url: str, timeout_s: float = 2.0) -> bool:
    try:
        urllib.request.urlopen(url, timeout=timeout_s)  # noqa: S310
        return True
    except urllib.error.HTTPError:
        # Cualquier respuesta HTTP, incluso 4xx, indica server up.
        return True
    except (urllib.error.URLError, OSError):
        return False


def _wait_alive(url: str, deadline_s: float) -> bool:
    end = time.monotonic() + deadline_s
    while time.monotonic() < end:
        if _http_alive(url):
            return True
        time.sleep(0.5)
    return False


@pytest.mark.skipif(not _ENABLED, reason=_REASON_DISABLED)
def test_fs_safe_container_present() -> None:
    """Precondición: el container target debe estar corriendo."""
    info = _docker_inspect(_FS_SAFE_CONTAINER)
    assert info is not None, f"Container {_FS_SAFE_CONTAINER} no existe"
    assert info["State"]["Running"], f"Container {_FS_SAFE_CONTAINER} no está running"


@pytest.mark.skipif(not _ENABLED, reason=_REASON_DISABLED)
def test_fs_safe_alive_before_reload() -> None:
    """Smoke: fs-safe responde HTTP antes del reload."""
    assert _http_alive(_FS_SAFE_URL), f"{_FS_SAFE_URL} no responde antes del reload"


@pytest.mark.skipif(not _ENABLED, reason=_REASON_DISABLED)
def test_real_reload_changes_started_at_and_keeps_service_alive() -> None:
    """Restart real de fs-safe vía lina-deploy → StartedAt cambia + HTTP sigue arriba."""
    before = _docker_inspect(_FS_SAFE_CONTAINER)
    assert before is not None, "Container no encontrado pre-reload"
    started_before = before["State"]["StartedAt"]

    # Ejecuta reload real (requiere sudo + lina-deploy en PATH).
    result = subprocess.run(
        ["sudo", "-n", "lina-deploy", "restart", _FS_SAFE_DEPLOY_NAME],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, (
        f"lina-deploy restart falló (rc={result.returncode}): {result.stderr}"
    )

    # Health-poll hasta 30s.
    assert _wait_alive(_FS_SAFE_URL, deadline_s=30.0), (
        f"{_FS_SAFE_URL} no recuperó respuesta en 30s post-reload"
    )

    after = _docker_inspect(_FS_SAFE_CONTAINER)
    assert after is not None, "Container desapareció post-reload"
    started_after = after["State"]["StartedAt"]

    assert started_before != started_after, (
        f"StartedAt no cambió ({started_before}) — el container no se reinició realmente"
    )
    assert after["State"]["Running"], "Container quedó stopped post-reload"
